"""TMFC060 Knowledge Management instances, for the AI > Knowledge page.

Instances are discovered in TMF639 Resource Inventory (``KNOWLEDGE_DISCOVERY=ri``): every ODAComponent whose
``componentId`` is TMFC060, with the URLs of the ``API`` resources it exposes. With ``KNOWLEDGE_DISCOVERY=static``
they come from ``KNOWLEDGE_INSTANCES_JSON`` only.

The gateway URL Resource Inventory reports may not resolve inside the cluster (on kind it is the ingress
LoadBalancer address 127.0.0.1, spike S2), so by default (``KNOWLEDGE_URL_MODE=service``) each URL is built from
the API's implementation Service, port and path. ``utils/outbound`` can't be used: it blocks ``.svc`` addresses.

Resource Inventory content comes from Component and ExposedAPI resources that component charts write, so every
value used to build a URL is validated, and a malformed entry is skipped.
"""
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

import httpx

from config import get_settings
from utils.cache import knowledge_instances_cache
from utils.exceptions import CanvasPortalError

logger = logging.getLogger(__name__)

COMPONENT_ID = "TMFC060"
KNOWLEDGE_API_NAME = "knowledgemanagement"
PROCESS_API_NAME = "processflow"
TIMEOUT_SECONDS = 5.0
CACHE_KEY = "instances"

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
URL_PATH = re.compile(r"^(/[A-Za-z0-9._~-]+)+/?$")

_last_good_lock = threading.Lock()
_last_good: Optional[tuple[float, list["KnowledgeInstance"]]] = None


@dataclass(frozen=True)
class KnowledgeInstance:
    name: str  # the component name, which is also the instance's Keycloak client id
    label: str
    namespace: Optional[str]
    version: Optional[str]
    resourceStatus: Optional[str]
    deploymentStatus: Optional[str]
    available: bool
    apiUrl: Optional[str]
    processUrl: Optional[str]
    mcpUrl: Optional[str]
    source: str  # "ri" or "static"

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Discovery:
    instances: list[KnowledgeInstance]
    stale: bool = False


def reset() -> None:
    """Forget the cached and the last good list (tests, and a settings change)."""
    global _last_good
    knowledge_instances_cache.delete(CACHE_KEY)
    with _last_good_lock:
        _last_good = None


def list_instances() -> Discovery:
    settings = get_settings()
    if settings.KNOWLEDGE_DISCOVERY == "static":
        return Discovery(_static_instances(settings.KNOWLEDGE_INSTANCES_JSON))
    cached = knowledge_instances_cache.get(CACHE_KEY)
    if cached is not None:
        return Discovery(cached)
    return _discover(settings)


def get_instance(name: str) -> KnowledgeInstance:
    for instance in list_instances().instances:
        if instance.name == name:
            return instance
    raise CanvasPortalError(f"Knowledge instance '{name}' not found", 404)


def _discover(settings) -> Discovery:
    global _last_good
    try:
        resources = _fetch_resources(settings)
    except CanvasPortalError:
        now = time.time()
        with _last_good_lock:
            last_good = _last_good
        if last_good and now - last_good[0] <= settings.KNOWLEDGE_STALE_TTL_SECONDS:
            logger.warning("Serving the last good TMFC060 instance list; Resource Inventory is unavailable")
            return Discovery(last_good[1], stale=True)
        raise
    instances = instances_from_resources(resources, settings.KNOWLEDGE_URL_MODE)
    knowledge_instances_cache.set(CACHE_KEY, instances, ttl_seconds=settings.KNOWLEDGE_CACHE_TTL_SECONDS)
    with _last_good_lock:
        _last_good = (time.time(), instances)
    return Discovery(instances)


def _fetch_resources(settings) -> list:
    base_url = settings.RESOURCE_INVENTORY_URL.strip().rstrip("/")
    if not base_url:
        raise CanvasPortalError("Resource Inventory is not configured", 503)
    try:
        response = httpx.get(f"{base_url}/resource", params={"category": "ODAComponent,API"}, timeout=TIMEOUT_SECONDS)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        logger.warning("Resource Inventory request failed: %s", type(exc).__name__)
        raise CanvasPortalError("Resource Inventory is unreachable", 502) from None
    if not response.is_success:
        logger.warning("Resource Inventory returned HTTP %s", response.status_code)
        raise CanvasPortalError(f"Resource Inventory returned HTTP {response.status_code}", 502)
    try:
        body = response.json()
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, list):
        logger.warning("Resource Inventory did not return a list of resources")
        raise CanvasPortalError("Resource Inventory returned an invalid response", 502)
    return body


def _characteristics(resource: dict) -> dict[str, Any]:
    values = {}
    for item in resource.get("resourceCharacteristic") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            values.setdefault(item["name"], item.get("value"))
    return values


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _scalar_text(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return _text(value)


def _http_url(value: Any) -> Optional[str]:
    """An absolute http(s) URL without credentials, query or fragment; anything else is None."""
    text = _text(value)
    if not text:
        return None
    try:
        url = httpx.URL(text)
    except (httpx.InvalidURL, TypeError):
        return None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo or url.query or url.fragment:
        return None
    return str(url).rstrip("/")


def _service_url(api: dict[str, Any], namespace: Optional[str]) -> Optional[str]:
    implementation, path, port = _text(api.get("implementation")), _text(api.get("path")), api.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if not (namespace and implementation and path and isinstance(port, int) and not isinstance(port, bool)):
        return None
    if not (DNS_LABEL.match(namespace) and DNS_LABEL.match(implementation) and 0 < port < 65536):
        return None
    if not URL_PATH.match(path) or any(segment in (".", "..") for segment in path.split("/")):
        return None
    return f"http://{implementation}.{namespace}.svc.cluster.local:{port}{path.rstrip('/')}"


def _api_url(api: Optional[dict[str, Any]], namespace: Optional[str], url_mode: str, component: str) -> Optional[str]:
    if api is None:
        return None
    if url_mode == "service":
        url = _service_url(api, namespace)
        if url:
            return url
        logger.warning(
            "API %s of %s has no usable implementation, port and path (Resource Inventory older than 1.2.1?); "
            "using its gateway url", api.get("apiName"), component)
    return _http_url(api.get("url"))


def instances_from_resources(resources: list, url_mode: str = "service") -> list[KnowledgeInstance]:
    """Build the TMFC060 instances from a Resource Inventory list of ODAComponent and API resources."""
    apis = {}
    for resource in resources:
        if isinstance(resource, dict) and resource.get("category") == "API" and isinstance(resource.get("id"), str):
            apis[resource["id"]] = _characteristics(resource)
    instances = {}
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("category") != "ODAComponent":
            continue
        values = _characteristics(resource)
        if (values.get("componentId") or values.get("id")) != COMPONENT_ID:
            continue
        name = _text(resource.get("name")) or _text(resource.get("id"))
        if not name or not DNS_LABEL.match(name):
            logger.warning("Skipping a TMFC060 component with an invalid name")
            continue
        if name in instances:
            logger.warning("Skipping a second TMFC060 component named %s", name)
            continue
        namespace = _text(values.get("namespace"))
        if namespace and not DNS_LABEL.match(namespace):
            logger.warning("Skipping TMFC060 component %s: invalid namespace", name)
            continue
        exposed = [
            apis[rel["resource"]["id"]]
            for rel in resource.get("resourceRelationship") or []
            if isinstance(rel, dict) and rel.get("relationshipType") == "exposes"
            and isinstance(rel.get("resource"), dict) and rel["resource"].get("id") in apis
        ]
        knowledge = next((a for a in exposed if a.get("apiType") == "openapi" and a.get("apiName") == KNOWLEDGE_API_NAME), None)
        process = next((a for a in exposed if a.get("apiType") == "openapi" and a.get("apiName") == PROCESS_API_NAME), None)
        mcp = next((a for a in exposed if a.get("apiType") == "mcp"), None)
        api_url = _api_url(knowledge, namespace, url_mode, name)
        resource_status = _text(resource.get("resourceStatus"))
        instances[name] = KnowledgeInstance(
            name=name,
            label=name,
            namespace=namespace,
            version=_scalar_text(values.get("version")),
            resourceStatus=resource_status,
            deploymentStatus=_text(values.get("deploymentStatus")),
            available=resource_status == "available" and api_url is not None,
            apiUrl=api_url,
            processUrl=_api_url(process, namespace, url_mode, name),
            mcpUrl=_api_url(mcp, namespace, url_mode, name),
            source="ri",
        )
    return sorted(instances.values(), key=lambda instance: instance.name)


def _static_instances(raw: str) -> list[KnowledgeInstance]:
    try:
        entries = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError, RecursionError):
        logger.warning("KNOWLEDGE_INSTANCES_JSON is not valid JSON; no static instances")
        return []
    if not isinstance(entries, list):
        logger.warning("KNOWLEDGE_INSTANCES_JSON is not a JSON list; no static instances")
        return []
    instances = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            logger.warning("KNOWLEDGE_INSTANCES_JSON entry %d is not an object; skipped", index)
            continue
        name, namespace = _text(entry.get("name")), _text(entry.get("namespace"))
        api_url = _http_url(entry.get("apiUrl"))
        if not name or not DNS_LABEL.match(name) or (namespace and not DNS_LABEL.match(namespace)) or not api_url:
            logger.warning("KNOWLEDGE_INSTANCES_JSON entry %d needs a valid name and an http(s) apiUrl; skipped", index)
            continue
        if name in instances:
            logger.warning("KNOWLEDGE_INSTANCES_JSON has a second entry named %s; skipped", name)
            continue
        optional = {}
        for key in ("processUrl", "mcpUrl"):
            optional[key] = _http_url(entry.get(key))
            if entry.get(key) is not None and optional[key] is None:
                logger.warning("KNOWLEDGE_INSTANCES_JSON entry %s has an invalid %s; ignored", name, key)
        instances[name] = KnowledgeInstance(
            name=name,
            label=_text(entry.get("label")) or name,
            namespace=namespace,
            version=None,
            resourceStatus=None,
            deploymentStatus=None,
            available=True,
            apiUrl=api_url,
            source="static",
            **optional,
        )
    return list(instances.values())
