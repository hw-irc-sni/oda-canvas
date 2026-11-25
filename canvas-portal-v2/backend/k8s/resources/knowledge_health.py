"""The active model route of a TMFC060 instance, read from its ``/health``.

``/health`` is served at the root of the instance's API Service and isn't an operation of its OAS, so the proxy never
forwards it; the portal reads it itself and returns only the name and the status of the model route. The rest of the
answer (URLs, model names, store details) stays here.

An instance reached through a gateway doesn't expose ``/health``; its route is then unknown, like on any failure.
"""
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from k8s.resources.knowledge_instances import KnowledgeInstance
from utils.cache import knowledge_health_cache

logger = logging.getLogger(__name__)

MAX_BYTES = 64 * 1024
TTL_SECONDS = 30
TIMEOUT_SECONDS = 3.0
STATUSES = {"up", "down", "unknown", "not-configured"}
ROUTE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,39}$")

# Tests replace the transport; None is httpx's default.
transport: Optional[httpx.BaseTransport] = None


def health_url(instance: KnowledgeInstance) -> Optional[str]:
    parts = urlsplit(instance.apiUrl or "")
    return f"{parts.scheme}://{parts.netloc}/health" if parts.scheme and parts.netloc else None


def _fetch(url: str) -> Any:
    with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client, \
            client.stream("GET", url, headers={"Accept": "application/json"}) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError("too large")
    return json.loads(bytes(body))


def parse(document: Any) -> Optional[dict]:
    """{"status", "route"} of the modelRoute check, or None when the document isn't a TMFC060 health answer."""
    checks = document.get("checks") if isinstance(document, dict) else None
    check = checks.get("modelRoute") if isinstance(checks, dict) else None
    status = check.get("status") if isinstance(check, dict) else None
    if not isinstance(status, str) or status not in STATUSES:
        return None
    route = check.get("route")
    return {"status": status, "route": route if isinstance(route, str) and ROUTE_NAME.match(route) else None}


def model_route(instance: KnowledgeInstance) -> Optional[dict]:
    """The instance's active model route, cached (failures too); None when it can't be read."""
    url = health_url(instance)
    if url is None:
        return None
    cached = knowledge_health_cache.get(url)
    if cached is not None:
        return cached["modelRoute"]
    try:
        route = parse(_fetch(url))
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, RecursionError) as exc:
        logger.info("Health of knowledge instance %s unavailable: %s", instance.name, type(exc).__name__)
        route = None
    knowledge_health_cache.set(url, {"modelRoute": route}, ttl_seconds=TTL_SECONDS)
    return route
