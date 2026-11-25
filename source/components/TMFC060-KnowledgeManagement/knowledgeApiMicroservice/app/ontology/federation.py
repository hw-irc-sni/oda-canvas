"""SPARQL federation to allowlisted peers through a loopback relay.

The allowlist is the endpoints (``source.url``) of the published ``specKind: remote`` modules. A query is
pre-checked against it (S3); each allowlisted ``SERVICE <url>`` is then rewritten to the relay,
``http://127.0.0.1:{FEDERATION_RELAY_PORT}/federation/{moduleId}?deadline=…``, and the rewritten query must name
the relay only (fail closed). The relay listens on the loopback interface only, so it can't be used as a proxy from
outside the pod. It forwards the SPARQL-protocol request to the module's endpoint with:

- the module's OAuth2 client-credentials token, when ``FEDERATION_SECRETS_DIR/<moduleId>/`` holds ``tokenUrl``,
  ``clientId`` and ``clientSecret`` (cached until it expires);
- a timeout of ``FEDERATION_CALL_TIMEOUT``, never past the query's deadline (504 on expiry, 502 on other failures);
- a cache of the answers for ``FEDERATION_CACHE_TTL`` seconds (LRU, 256 entries; 0 disables it).
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from sqlalchemy import select

from ..config import Settings
from ..db.models import KnowledgeSpecification as SpecRow
from ..errors import ApiError
from ..query import service_targets

log = logging.getLogger(__name__)
RELAY_HOST = "127.0.0.1"
CACHE_SIZE = 256
FORWARDED = ("content-type", "accept")


async def allowlist(session) -> dict[str, str]:
    """Endpoint URL -> module id, for the published remote modules."""
    rows = (await session.scalars(select(SpecRow).where(SpecRow.spec_kind == "remote",
                                                        SpecRow.lifecycle_status == "published"))).all()
    return {r.body["source"]["url"]: r.id for r in rows if (r.body.get("source") or {}).get("url")}


def relay_url(port: int, module_id: str, deadline: float) -> str:
    return f"http://{RELAY_HOST}:{port}/federation/{module_id}?deadline={deadline:.3f}"


def rewrite(query: str, endpoints: dict[str, str], port: int, deadline: float) -> tuple[str, frozenset[str]]:
    """Point the SERVICE clauses of a pre-checked query at the relay; returns the query and its relay targets."""
    relays = {}
    for url, module_id in endpoints.items():
        target = relay_url(port, module_id, deadline)
        pattern = re.compile(r"(SERVICE\s+(?:SILENT\s+)?)<" + re.escape(url) + ">", re.IGNORECASE)
        query, n = pattern.subn(lambda m: f"{m.group(1)}<{target}>", query)
        if n:
            relays[target] = module_id
    leftover = [str(t) for t in service_targets(query) if str(t) not in relays]
    if leftover:
        raise ApiError(403, "SERVICE_NOT_ALLOWED", "SERVICE endpoint not allowed",
                       f"Write SERVICE endpoints as full IRIs <…>: {', '.join(leftover)}")
    return query, frozenset(relays)


class TokenCache:
    """OAuth2 client-credentials tokens per remote module, read from FEDERATION_SECRETS_DIR/<moduleId>/."""

    def __init__(self, secrets_dir: str | None, client: httpx.AsyncClient):
        self.secrets_dir = Path(secrets_dir) if secrets_dir else None
        self.client = client
        self.tokens: dict[str, tuple[str, float]] = {}

    def credentials(self, module_id: str) -> dict | None:
        if not self.secrets_dir:
            return None
        folder = self.secrets_dir / module_id
        files = {k: folder / k for k in ("tokenUrl", "clientId", "clientSecret")}
        if not all(f.is_file() for f in files.values()):
            return None
        return {k: f.read_text().strip() for k, f in files.items()}

    async def header(self, module_id: str, timeout: float) -> dict:
        cached = self.tokens.get(module_id)
        if cached and cached[1] > time.time():
            return {"Authorization": f"Bearer {cached[0]}"}
        creds = self.credentials(module_id)
        if creds is None:
            return {}
        response = await self.client.post(creds["tokenUrl"], timeout=timeout, data={
            "grant_type": "client_credentials", "client_id": creds["clientId"], "client_secret": creds["clientSecret"]})
        response.raise_for_status()
        grant = response.json()
        self.tokens[module_id] = (grant["access_token"], time.time() + max(0, int(grant.get("expires_in", 60)) - 30))
        return {"Authorization": f"Bearer {grant['access_token']}"}


class Relay:
    def __init__(self, settings: Settings, sessions):
        self.settings = settings
        self.sessions = sessions
        self.client = httpx.AsyncClient()
        self.tokens = TokenCache(settings.federation_secrets_dir, self.client)
        self.cache: OrderedDict[tuple, tuple[float, int, str, bytes]] = OrderedDict()
        self.calls = 0  # requests forwarded to peers (for tests and logs)
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task | None = None
        self.port = settings.federation_relay_port
        self.app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        self.app.add_api_route("/federation/{module_id}", self.handle, methods=["GET", "POST"])

    async def endpoint(self, module_id: str) -> str | None:
        async with self.sessions() as session:
            return {v: k for k, v in (await allowlist(session)).items()}.get(module_id)

    async def handle(self, module_id: str, request: Request) -> Response:
        url = await self.endpoint(module_id)
        if url is None:
            return Response(f"'{module_id}' is not a published remote module", status_code=403)
        try:
            deadline = float(request.query_params.get("deadline", "0")) or time.time() + self.settings.federation_call_timeout
        except ValueError:
            return Response("bad deadline", status_code=400)
        timeout = min(self.settings.federation_call_timeout, deadline - time.time())
        if timeout <= 0:
            return Response("the query deadline has passed", status_code=504)
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED}
        key = (module_id, request.method, str(request.query_params.get("query", "")), body,
               headers.get("accept", ""))
        ttl = self.settings.federation_cache_ttl
        if ttl > 0 and key in self.cache and self.cache[key][0] > time.time():
            self.cache.move_to_end(key)
            _, status, media_type, content = self.cache[key]
            return Response(content, status_code=status, media_type=media_type)
        try:
            auth = await self.tokens.header(module_id, timeout)
            self.calls += 1
            params = {k: v for k, v in request.query_params.items() if k != "deadline"}
            response = await self.client.request(request.method, url, content=body or None, params=params or None,
                                                 headers={**headers, **auth}, timeout=timeout)
        except httpx.TimeoutException:
            log.warning("federation: %s did not answer within %.1f s", module_id, timeout)
            return Response(f"{module_id} did not answer in time", status_code=504)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("federation: %s failed: %s", module_id, exc)
            return Response(f"{module_id} failed: {type(exc).__name__}", status_code=502)
        if response.status_code >= 400:
            log.warning("federation: %s answered %s", module_id, response.status_code)
            return Response(f"{module_id} answered {response.status_code}", status_code=502)
        media_type = response.headers.get("content-type", "application/sparql-results+json")
        if ttl > 0:
            self.cache[key] = (time.time() + ttl, response.status_code, media_type, response.content)
            self.cache.move_to_end(key)
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
        return Response(response.content, status_code=response.status_code, media_type=media_type)

    async def start(self) -> None:
        config = uvicorn.Config(self.app, host=RELAY_HOST, port=self.port, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.server.install_signal_handlers = lambda: None  # the main server owns the signals
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            if self.task.done():
                self.task.result()  # raises the startup error
            await asyncio.sleep(0.02)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        log.info("federation relay listening on %s:%s", RELAY_HOST, self.port)

    async def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
            await self.task
        await self.client.aclose()
