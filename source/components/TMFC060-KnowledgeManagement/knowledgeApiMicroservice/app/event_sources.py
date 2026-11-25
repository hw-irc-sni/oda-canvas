"""Event-driven ingestion from TMF providers (TMF639, TMF638, TMF642, TMF628, TMF921), by direct hub registration
(spike S5: TMFOP006 is not implemented).

- ``Registrar`` (every ``EVENT_SOURCE_INTERVAL``): the sources the knowledge-bases use (``source[].kind`` and
  ``dependentApiName``) are resolved to provider URLs, from canvas-info (where TMFOP005 records the resolved
  dependentAPIs) or ``EVENT_SOURCE_URLS``. Each gets a hub registration on its provider, asking for the union of
  the KBs' ``eventTypes``, with a callback ``{EVENT_CALLBACK_BASE}/eventSource/{kind}/{token}``: a secret token per
  registration, stored hashed. A changed URL or event-type set re-registers; an unused source is deregistered.
- ``receive`` (the callback): an unknown token gets 404. Each KB with that source takes the event if its own
  ``eventTypes`` and ``query`` (``field=value&…`` on the resource) match; anything else is ignored. Events wait in
  ``event_buffer``.
- ``Batcher``: per KB and kind, one ingest process for the buffered resources at most every
  ``EVENT_BATCH_SECONDS``, or at once at ``EVENT_BATCH_MAX``.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import httpx
from sqlalchemy import delete, func, select

from . import processes
from .config import Settings
from .db.models import EventBuffer, EventSource
from .db.models import Knowledge as KnowledgeRow
from .errors import ApiError

log = logging.getLogger(__name__)
KINDS = ("tmf639", "tmf638", "tmf642", "tmf628", "tmf921")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def source_dependency(source: dict) -> str:
    return source.get("dependentApiName") or source["kind"]


def wants(source: dict, event_type: str, resource: dict) -> bool:
    types = source.get("eventTypes") or []
    if types and event_type not in types:
        return False
    for field, value in parse_qsl(source.get("query") or ""):
        if str(resource.get(field)) != value:
            return False
    return True


async def wanted_sources(session) -> dict[tuple[str, str], set[str] | None]:
    """(kind, dependency) -> the event types its KBs want (None: every event)."""
    out: dict[tuple[str, str], set[str] | None] = {}
    for kb in (await session.scalars(select(KnowledgeRow).where(KnowledgeRow.lifecycle_status != "deprecated"))).all():
        for source in kb.body.get("source") or []:
            if source.get("kind") not in KINDS:
                continue
            key = (source["kind"], source_dependency(source))
            types = set(source.get("eventTypes") or [])
            current = out.get(key, set())
            out[key] = None if current is None or not types else current | types
    return out


class Registrar:
    def __init__(self, settings: Settings, sessions, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.sessions = sessions
        self.http = httpx.AsyncClient(transport=transport, timeout=10)
        try:
            self.overrides = json.loads(settings.event_source_urls or "{}")
        except ValueError:
            log.error("EVENT_SOURCE_URLS is not a JSON object; ignored")
            self.overrides = {}

    async def provider_url(self, kind: str, dependency: str) -> str | None:
        for key in (dependency, kind):
            if self.overrides.get(key):
                return self.overrides[key].rstrip("/")
        try:
            response = await self.http.get(f"{self.settings.canvas_info_url.rstrip('/')}/service", params={
                "serviceCharacteristic.value": self.settings.component_name, "state": "active"})
            response.raise_for_status()
            services = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("canvas-info not available: %s", exc)
            return None
        for service in services if isinstance(services, list) else []:
            chars = {c.get("name"): c.get("value") for c in service.get("serviceCharacteristic") or []}
            if chars.get("componentName") == self.settings.component_name and chars.get("dependencyName") == dependency:
                return (chars.get("url") or "").rstrip("/") or None
        return None

    async def register(self, session, kind: str, dependency: str, url: str, types: set[str] | None) -> None:
        token = secrets.token_urlsafe(32)
        callback = f"{self.settings.event_callback_base.rstrip('/')}/eventSource/{kind}/{token}"
        query = f"eventType={','.join(sorted(types))}" if types else None
        response = await self.http.post(f"{url}/hub", json={"callback": callback, **({"query": query} if query else {})})
        response.raise_for_status()
        hub_id = (response.json() or {}).get("id") if response.content else None
        session.add(EventSource(id=f"es-{secrets.token_hex(6)}", kind=kind, dependency=dependency, provider_url=url,
                                hub_id=hub_id, token_hash=token_hash(token), query=query))
        await session.commit()
        log.info("registered for %s events on %s (hub %s)", kind, url, hub_id)

    async def deregister(self, session, source: EventSource) -> None:
        if source.hub_id:
            try:
                await self.http.delete(f"{source.provider_url}/hub/{source.hub_id}")
            except httpx.HTTPError as exc:
                log.warning("could not delete hub %s on %s: %s", source.hub_id, source.provider_url, exc)
        await session.delete(source)
        await session.commit()

    async def reconcile(self) -> None:
        if not self.settings.event_callback_base:
            log.warning("EVENT_CALLBACK_BASE is not set: no hub registrations on TMF providers")
            return
        async with self.sessions() as session:
            wanted = await wanted_sources(session)
            existing = {(s.kind, s.dependency): s for s in (await session.scalars(select(EventSource))).all()}
            for key, source in existing.items():
                if key not in wanted:
                    await self.deregister(session, source)
            for (kind, dependency), types in wanted.items():
                url = await self.provider_url(kind, dependency)
                current = existing.get((kind, dependency))
                query = f"eventType={','.join(sorted(types))}" if types else None
                if current and url == current.provider_url and current.query == query:
                    continue
                if url is None:
                    log.warning("no provider URL for %s (%s) yet", kind, dependency)
                    continue
                try:
                    if current:
                        await self.deregister(session, current)
                    await self.register(session, kind, dependency, url, types)
                except httpx.HTTPError as exc:
                    log.warning("hub registration on %s failed: %s; retried next round", url, exc)

    async def run_periodically(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:  # noqa: BLE001 - keep reconciling
                log.exception("event source reconciliation failed")
            await asyncio.sleep(self.settings.event_source_interval)

    async def aclose(self) -> None:
        await self.http.aclose()


# ---- receiving -------------------------------------------------------------------------------------------------
def resource_of(event: dict) -> dict | None:
    payload = event.get("event")
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, dict):
                return value
    return None


async def receive(session, settings: Settings, kind: str, token: str, event: dict, batcher) -> int:
    """Buffer the event for the KBs that want it; returns how many took it."""
    sources = (await session.scalars(select(EventSource).where(EventSource.kind == kind))).all()
    digest = token_hash(token)
    source = next((s for s in sources if hmac.compare_digest(s.token_hash, digest)), None)
    if source is None:
        raise ApiError(404, "404", "Not Found", "No such event source")
    event_type, resource = event.get("eventType"), resource_of(event)
    if not isinstance(event_type, str) or resource is None:
        log.info("ignored a %s notification without eventType or resource", kind)
        return 0
    taken = []
    for kb in (await session.scalars(select(KnowledgeRow).where(KnowledgeRow.lifecycle_status != "deprecated"))).all():
        for s in kb.body.get("source") or []:
            if s.get("kind") == kind and source_dependency(s) == source.dependency and wants(s, event_type, resource):
                session.add(EventBuffer(knowledge_id=kb.id, kind=kind, event_type=event_type, resource=resource))
                taken.append(kb.id)
                break
    await session.commit()
    for kb_id in taken:
        waiting = await session.scalar(select(func.count()).select_from(EventBuffer).where(
            EventBuffer.knowledge_id == kb_id, EventBuffer.kind == kind))
        if waiting >= settings.event_batch_max:
            await batcher.flush(kb_id, kind)
    return len(taken)


class Batcher:
    def __init__(self, settings: Settings, sessions):
        self.settings = settings
        self.sessions = sessions
        self.lock = asyncio.Lock()

    async def flush(self, kb_id: str, kind: str) -> str | None:
        """Start one ingest process for the buffered events of a KB and kind; returns its id."""
        async with self.lock, self.sessions() as session:
            rows = (await session.scalars(select(EventBuffer).where(
                EventBuffer.knowledge_id == kb_id, EventBuffer.kind == kind).order_by(EventBuffer.id)
                .with_for_update(skip_locked=True))).all()
            if not rows:
                return None
            kb = await session.get(KnowledgeRow, kb_id)
            params = {"resources": [r.resource for r in rows], "eventTypes": sorted({r.event_type for r in rows})}
            await session.execute(delete(EventBuffer).where(EventBuffer.id.in_([r.id for r in rows])))
            try:
                ref = await processes.start_ingestion(session, self.settings, kb, {"source": kind, "params": params})
            except ApiError as exc:  # the KB was deprecated or lost the source meanwhile
                log.info("dropped %d %s events for %s: %s", len(rows), kind, kb_id, exc.message)
                await session.commit()
                return None
            return ref["id"]

    async def flush_due(self, force: bool = False) -> list[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=0 if force else self.settings.event_batch_seconds)
        async with self.sessions() as session:
            due = (await session.execute(select(EventBuffer.knowledge_id, EventBuffer.kind)
                                         .group_by(EventBuffer.knowledge_id, EventBuffer.kind)
                                         .having(func.min(EventBuffer.received) <= cutoff))).all()
        started = []
        for kb_id, kind in due:
            if pid := await self.flush(kb_id, kind):
                started.append(pid)
        return started

    async def run_periodically(self) -> None:
        while True:
            try:
                await self.flush_due()
            except Exception:  # noqa: BLE001
                log.exception("event batch flush failed")
            await asyncio.sleep(1.0)
