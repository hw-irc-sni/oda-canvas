"""Events and their delivery to /hub subscribers (TMF630).

Services publish ``Event``s (Create, AttributeValueChange, StateChange and Delete of Knowledge and
KnowledgeSpecification). ``HubEvents.publish`` writes one row per matching subscription into the ``event_delivery``
outbox; the ``Dispatcher`` delivers them to ``{callback}/listener/{eventName}`` (for example
``…/listener/knowledgeCreateEvent``) and retries after each delay of ``EVENT_RETRY_DELAYS`` before marking the row
``failed``. Finished rows are purged after ``EVENT_RETENTION_DAYS``.

Callbacks are guarded against SSRF: only http(s), and a host that resolves to a loopback, link-local, private or other
non-global address is refused unless it matches ``EVENT_CALLBACK_ALLOWED_HOSTS``. The check runs at registration and
again before every delivery.
"""
import asyncio
import fnmatch
import ipaddress
import logging
import secrets
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import delete, select, text

from .config import Settings
from .db.models import EventDelivery, HubSubscription
from .errors import ApiError
from .listing import absolutize

log = logging.getLogger(__name__)


@dataclass
class Event:
    event_type: str  # for example KnowledgeSpecificationCreateEvent
    resource_name: str  # payload key, for example knowledgeSpecification
    resource: dict
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def listener_name(event_type: str) -> str:
    return event_type[:1].lower() + event_type[1:]


# ---- callbacks -------------------------------------------------------------------------------------------------
class CallbackRefused(Exception):
    pass


async def check_callback(url: str, allowed_hosts: list[str]) -> None:
    """Raise CallbackRefused unless the URL is http(s) and its host is allowed or resolves to global addresses."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CallbackRefused("the callback must be an http or https URL with a host")
    host = parts.hostname.lower()
    if any(fnmatch.fnmatch(host, pattern.lower()) for pattern in allowed_hosts):
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                                             type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CallbackRefused(f"the callback host {host} cannot be resolved") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global or address.is_multicast:
            raise CallbackRefused(f"the callback host {host} resolves to the non-public address {address}")


def parse_query(query: str | None) -> set[str] | None:
    """The eventType filter of a subscription (None: every event)."""
    if not query:
        return None
    params = parse_qs(query, keep_blank_values=True)
    unknown = set(params) - {"eventType"}
    if unknown:
        raise ApiError(400, "400", "Bad Request", f"Only eventType can filter events, not {', '.join(sorted(unknown))}")
    types = {t.strip() for value in params["eventType"] for t in value.split(",") if t.strip()}
    if not types:
        raise ApiError(400, "400", "Bad Request", "eventType needs a value")
    return types


# ---- publishing ------------------------------------------------------------------------------------------------
class HubEvents:
    """Publishes events into the outbox (and keeps them in memory, for tests and logs)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions = None  # set by the lifespan; without it events are only recorded
        self.events: list[Event] = []

    def types(self) -> list[str]:
        return [e.event_type for e in self.events]

    def envelope(self, event: Event) -> dict:
        resource = absolutize(event.resource, self.settings.event_href_base.rstrip("/")) \
            if self.settings.event_href_base else event.resource
        return {"@type": event.event_type, "eventId": str(uuid.uuid4()), "eventTime": iso(datetime.now(timezone.utc)),
                "timeOccurred": iso(event.time), "eventType": event.event_type, "domain": "knowledgeManagement",
                "title": event.event_type, "event": {event.resource_name: resource}}

    async def publish(self, event: Event) -> None:
        log.debug("event %s %s", event.event_type, event.resource.get("id"))
        self.events.append(event)
        if self.sessions is None:
            return
        body = self.envelope(event)
        async with self.sessions() as session:
            for sub in (await session.scalars(select(HubSubscription))).all():
                wanted = parse_query(sub.query)
                if wanted is not None and event.event_type not in wanted:
                    continue
                session.add(EventDelivery(id=f"ev-{secrets.token_hex(8)}", subscription_id=sub.id,
                                          event_type=event.event_type, state="pending", attempts=0, body=body,
                                          url=f"{sub.callback.rstrip('/')}/listener/{listener_name(event.event_type)}"))
            await session.commit()


# ---- delivery --------------------------------------------------------------------------------------------------
DUE = text("""SELECT id FROM event_delivery WHERE state = 'pending' AND next_attempt_at <= now()
ORDER BY next_attempt_at LIMIT :limit FOR UPDATE SKIP LOCKED""")


class Dispatcher:
    def __init__(self, settings: Settings, sessions, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.sessions = sessions
        self.client = httpx.AsyncClient(transport=transport, timeout=settings.event_delivery_timeout)
        self.last_purge = 0.0

    async def deliver(self, row: EventDelivery) -> None:
        delays = self.settings.event_retry_delays
        row.attempts += 1
        try:
            await check_callback(row.url, self.settings.event_callback_allowed_hosts)
            response = await self.client.post(row.url, json=row.body)
            if response.is_success:
                row.state, row.delivered_at, row.last_error = "delivered", datetime.now(timezone.utc), None
                return
            error = f"HTTP {response.status_code}"
        except CallbackRefused as exc:
            row.state, row.last_error = "failed", str(exc)  # never retried
            log.warning("event %s to %s refused: %s", row.event_type, row.url, exc)
            return
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
        row.last_error = error
        if row.attempts > len(delays):
            row.state = "failed"
            log.warning("event %s to %s failed after %d tries: %s", row.event_type, row.url, row.attempts, error)
        else:
            row.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delays[row.attempts - 1])

    async def run_once(self, limit: int = 20) -> int:
        """Deliver the due rows (up to limit); returns how many were tried."""
        async with self.sessions() as session:
            ids = (await session.scalars(DUE, {"limit": limit})).all()
            rows = [await session.get(EventDelivery, i) for i in ids]
            await asyncio.gather(*(self.deliver(r) for r in rows))
            await session.commit()
        return len(rows)

    async def purge(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.settings.event_retention_days)
        async with self.sessions() as session:
            await session.execute(delete(EventDelivery).where(EventDelivery.state.in_(("delivered", "failed")),
                                                              EventDelivery.created < cutoff))
            await session.commit()

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                tried = await self.run_once()
                if loop.time() - self.last_purge > 3600:
                    await self.purge()
                    self.last_purge = loop.time()
            except Exception:  # noqa: BLE001 - keep delivering
                log.exception("event delivery failed")
                tried = 0
            if not tried:
                await asyncio.sleep(self.settings.event_poll_interval)

    async def aclose(self) -> None:
        await self.client.aclose()


# kept for callers that build an in-memory publisher (tests of single services)
EventRecorder = HubEvents
