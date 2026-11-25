"""LLM and embedding endpoints: the resolver chain and an OpenAI-compatible client with failover.

Routes, tried in order (each gives an OpenAI-compatible base URL ``…/v1``):

1. **modelGatewayService**: the ModelGatewayService CR named by ``MODEL_GATEWAY_SERVICE`` (``name`` in the pod's
   namespace, or ``namespace/name``), when its CRD exists, read through the in-cluster Kubernetes API. Its endpoint is
   found as in the portal (``canvas-portal-v2/backend/k8s/resources/model_gateway_services.py``): status or spec
   endpoint fields, else the proxy Service. Skipped when the CR lists models and not both of ``MGS_*_MODEL``.
2. **agentgateway**: ``AGENTGATEWAY_URL``.
3. **openaiCompatible**: ``OPENAI_BASE_URL`` with the key read from ``OPENAI_API_KEY_FILE``.

A route needs its chat and embedding model names (``MGS_``, ``AGENTGATEWAY_``, ``OPENAI_`` + ``CHAT_MODEL`` /
``EMBEDDING_MODEL``) and is selected when ``GET {base}/models`` answers 2xx. One probe embedding then detects the
embedding dimension (compared with ``EMBEDDING_DIMENSION``). The chain is re-evaluated every ``MODEL_ROUTE_INTERVAL``
seconds; a call failing with a connection error, a timeout or a 5xx marks the route failed, re-resolves without it
and retries once. 4xx answers are returned to the caller.
"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
MGS_GROUP, MGS_VERSION, MGS_PLURAL = "oda.tmforum.org", "v1alpha1", "modelgatewayservices"
MGS_CRD = f"{MGS_PLURAL}.{MGS_GROUP}"
ROUTE_NAMES = ("modelGatewayService", "agentgateway", "openaiCompatible")


class Skip(Exception):
    """A route that cannot be used, with the reason (shown in /health)."""

    def __init__(self, reason: str, configured: bool = True):
        super().__init__(reason)
        self.reason = reason
        self.configured = configured


class ModelError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Route:
    name: str
    base_url: str
    chat_model: str
    embedding_model: str
    api_key: str | None = None

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


@dataclass
class Outcome:
    route: str
    status: str  # selected, standby (not evaluated), unavailable, skipped, not-configured, failed
    reason: str = ""

    def as_dict(self) -> dict:
        return {"route": self.route, "status": self.status, **({"reason": self.reason} if self.reason else {})}


# ---- Kubernetes ------------------------------------------------------------------------------------------------
class K8s:
    """A minimal in-cluster Kubernetes API client (GET only)."""

    def __init__(self, base_url: str, token: str, verify: Any = True, transport: httpx.AsyncBaseTransport | None = None,
                 namespace: str = "default"):
        self.client = httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {token}"},
                                        verify=verify, transport=transport, timeout=5)
        self.namespace = namespace

    @classmethod
    def in_cluster(cls) -> "K8s | None":
        host, port = os.environ.get("KUBERNETES_SERVICE_HOST"), os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        token = SA_DIR / "token"
        if not host or not token.is_file():
            return None
        namespace = (SA_DIR / "namespace").read_text().strip() if (SA_DIR / "namespace").is_file() else "default"
        ca = SA_DIR / "ca.crt"
        return cls(f"https://{host}:{port}", token.read_text().strip(), str(ca) if ca.is_file() else True,
                   namespace=namespace)

    async def get(self, path: str, **params) -> tuple[int, dict]:
        response = await self.client.get(path, params=params or None)
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body if isinstance(body, dict) else {}

    async def aclose(self) -> None:
        await self.client.aclose()


def _s(*values) -> str:
    return next((v.strip() for v in values if isinstance(v, str) and v.strip()), "")


def _d(value) -> dict:
    return value if isinstance(value, dict) else {}


def _l(value) -> list:
    return value if isinstance(value, list) else []


def endpoint_from_status_or_spec(spec: dict, status: dict) -> str:
    """The portal's _endpoint_from_status_or_spec."""
    gateway = _d(spec.get("gateway"))
    endpoint = _s(status.get("gatewayEndpoint"), status.get("endpointUrl"), status.get("endpointURL"),
                  status.get("endpoint"), status.get("url"), status.get("serviceEndpoint"), status.get("proxyUrl"),
                  status.get("proxyURL"), gateway.get("endpointUrl"), gateway.get("endpointURL"),
                  gateway.get("endpoint"), gateway.get("baseUrl"), gateway.get("baseURL"), gateway.get("url"),
                  gateway.get("externalUrl"), gateway.get("externalURL"))
    if endpoint:
        return endpoint
    endpoints = status.get("endpoints")
    if isinstance(endpoints, dict):
        return _s(endpoints.get("url"), endpoints.get("endpointUrl"), endpoints.get("endpoint"),
                  endpoints.get("address"))
    for item in _l(endpoints):
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            found = _s(item.get("url"), item.get("endpointUrl"), item.get("endpoint"), item.get("address"))
            if found:
                return found
    return ""


def service_endpoint_url(service: dict) -> str:
    """The portal's _service_endpoint_url: load-balancer ingress, external IPs, then the cluster IP."""
    spec, status = _d(service.get("spec")), _d(service.get("status"))
    ports = _l(spec.get("ports"))
    first = _d(ports[0]) if ports else {}
    try:
        port = int(first.get("port", 80))
    except (TypeError, ValueError):
        port = 80
    scheme = "https" if port == 443 or "https" in str(first.get("name", "")).lower() else "http"
    suffix = "" if (scheme, port) in (("http", 80), ("https", 443)) else f":{port}"
    for ingress in _l(_d(status.get("loadBalancer")).get("ingress")):
        host = _s(_d(ingress).get("ip"), _d(ingress).get("hostname"))
        if host:
            return f"{scheme}://{host}{suffix}"
    for host in _l(spec.get("externalIPs")):
        if isinstance(host, str) and host.strip():
            return f"{scheme}://{host.strip()}{suffix}"
    cluster_ip = _s(spec.get("clusterIP"))
    if cluster_ip and cluster_ip.lower() != "none":
        return f"{scheme}://{cluster_ip}{suffix}"
    return ""


def offered_models(spec: dict, status: dict) -> set[str]:
    names = {_s(_d(m).get("name")) for m in _l(spec.get("dependentAIModels"))}
    listed = status.get("modelNames")
    if isinstance(listed, str):
        names |= {n for n in re.split(r"[\s,]+", listed) if n}
    elif isinstance(listed, list):
        names |= {_s(n) for n in listed}
    return {n for n in names if n}


def with_v1(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


# ---- resolver --------------------------------------------------------------------------------------------------
class ModelResolver:
    def __init__(self, settings: Settings, *, k8s: K8s | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 use_in_cluster: bool = True):
        self.settings = settings
        self.k8s = k8s if k8s is not None else (K8s.in_cluster() if use_in_cluster else None)
        self.http = httpx.AsyncClient(transport=transport, timeout=settings.model_call_timeout)
        self.active: Route | None = None
        self.dimension: int | None = None
        self.dimension_detail = ""
        self.outcomes: list[Outcome] = []
        self.evaluated_at: str | None = None
        self.lock = asyncio.Lock()

    # routes
    async def route_mgs(self) -> Route:
        s = self.settings
        if not s.model_gateway_service:
            raise Skip("MODEL_GATEWAY_SERVICE is not set", configured=False)
        if not (s.mgs_chat_model and s.mgs_embedding_model):
            raise Skip("MGS_CHAT_MODEL and MGS_EMBEDDING_MODEL must both be set")
        if self.k8s is None:
            raise Skip("not running in a Kubernetes cluster")
        namespace, _, name = s.model_gateway_service.rpartition("/")
        namespace = namespace or self.k8s.namespace
        status, _ = await self.k8s.get(f"/apis/apiextensions.k8s.io/v1/customresourcedefinitions/{MGS_CRD}")
        if status == 403:  # like the portal: without CRD read access, try listing the resources
            status, _ = await self.k8s.get(f"/apis/{MGS_GROUP}/{MGS_VERSION}/{MGS_PLURAL}", limit=1)
        if status == 404:
            raise Skip("the ModelGatewayService CRD is not installed")
        if status >= 400:
            raise Skip(f"the ModelGatewayService CRD cannot be read (HTTP {status})")
        status, cr = await self.k8s.get(f"/apis/{MGS_GROUP}/{MGS_VERSION}/namespaces/{namespace}/{MGS_PLURAL}/{name}")
        if status == 404:
            raise Skip(f"ModelGatewayService {namespace}/{name} not found")
        if status >= 400:
            raise Skip(f"ModelGatewayService {namespace}/{name} cannot be read (HTTP {status})")
        spec, cr_status = _d(cr.get("spec")), _d(cr.get("status"))
        offered = offered_models(spec, cr_status)
        missing = [m for m in (s.mgs_chat_model, s.mgs_embedding_model) if offered and m not in offered]
        if missing:
            raise Skip(f"model not offered by the gateway: {', '.join(missing)}")
        endpoint = endpoint_from_status_or_spec(spec, cr_status)
        if not endpoint:
            gateway = _d(spec.get("gateway"))
            for service in dict.fromkeys(filter(None, [
                    _s(cr_status.get("proxyServiceName")), _s(cr_status.get("gatewayServiceName")),
                    _s(cr_status.get("serviceName")), _s(gateway.get("proxyServiceName")),
                    _s(gateway.get("serviceName")), f"{name}-proxy"])):
                svc_status, svc = await self.k8s.get(f"/api/v1/namespaces/{namespace}/services/{service}")
                if svc_status == 200 and (endpoint := service_endpoint_url(svc)):
                    break
        if not endpoint:
            raise Skip(f"ModelGatewayService {namespace}/{name} has no endpoint")
        return Route("modelGatewayService", with_v1(endpoint), s.mgs_chat_model, s.mgs_embedding_model)

    async def route_agentgateway(self) -> Route:
        s = self.settings
        if not s.agentgateway_url:
            raise Skip("AGENTGATEWAY_URL is not set", configured=False)
        if not (s.agentgateway_chat_model and s.agentgateway_embedding_model):
            raise Skip("AGENTGATEWAY_CHAT_MODEL and AGENTGATEWAY_EMBEDDING_MODEL must both be set")
        return Route("agentgateway", s.agentgateway_url.rstrip("/"), s.agentgateway_chat_model,
                     s.agentgateway_embedding_model)

    async def route_openai(self) -> Route:
        s = self.settings
        if not s.openai_base_url:
            raise Skip("OPENAI_BASE_URL is not set", configured=False)
        if not (s.openai_chat_model and s.openai_embedding_model):
            raise Skip("OPENAI_CHAT_MODEL and OPENAI_EMBEDDING_MODEL must both be set")
        key = None
        if s.openai_api_key_file:
            path = Path(s.openai_api_key_file)
            if not path.is_file():
                raise Skip("OPENAI_API_KEY_FILE does not exist")
            key = path.read_text().strip()
        return Route("openaiCompatible", s.openai_base_url.rstrip("/"), s.openai_chat_model, s.openai_embedding_model,
                     key)

    async def probe(self, route: Route) -> str:
        """'' when GET {base}/models answers 2xx, else the reason."""
        try:
            response = await self.http.get(f"{route.base_url}/models", headers=route.headers(),
                                           timeout=self.settings.model_probe_timeout)
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__} on GET /models"
        return "" if response.is_success else f"GET /models answered {response.status_code}"

    async def resolve(self, skip: frozenset[str] = frozenset()) -> Route | None:
        async with self.lock:
            outcomes, selected = [], None
            for name, build in zip(ROUTE_NAMES, (self.route_mgs, self.route_agentgateway, self.route_openai)):
                if selected is not None:
                    outcomes.append(Outcome(name, "standby" if name not in skip else "failed"))
                    continue
                if name in skip:
                    outcomes.append(Outcome(name, "failed", "a call failed; skipped until the next evaluation"))
                    continue
                try:
                    route = await build()
                except Skip as exc:
                    outcomes.append(Outcome(name, "skipped" if exc.configured else "not-configured", exc.reason))
                    continue
                except httpx.HTTPError as exc:
                    outcomes.append(Outcome(name, "unavailable", f"Kubernetes API: {type(exc).__name__}"))
                    continue
                reason = await self.probe(route)
                if reason:
                    outcomes.append(Outcome(name, "unavailable", reason))
                    continue
                selected = route
                outcomes.append(Outcome(name, "selected"))
            changed = (selected and selected.base_url) != (self.active and self.active.base_url)
            self.active, self.outcomes = selected, outcomes
            self.evaluated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if changed:
                log.info("model route: %s", selected.name if selected else "none")
        if selected is not None and changed:
            await self.detect_dimension(selected)
        elif selected is None:
            self.dimension, self.dimension_detail = None, ""
        return selected

    async def detect_dimension(self, route: Route) -> None:
        try:
            vectors = await self._embed(route, ["dimension probe"])
            self.dimension, self.dimension_detail = len(vectors[0]), ""
        except (ModelError, httpx.HTTPError, LookupError, ValueError) as exc:
            self.dimension, self.dimension_detail = None, f"probe embedding failed: {exc}"

    async def run_periodically(self) -> None:
        while True:
            try:
                await self.resolve()
            except Exception:  # noqa: BLE001 - keep evaluating
                log.exception("model route evaluation failed")
            await asyncio.sleep(self.settings.model_route_interval)

    # calls
    async def _post(self, route: Route, path: str, payload: dict) -> dict:
        response = await self.http.post(f"{route.base_url}{path}", json=payload, headers=route.headers())
        if response.status_code >= 500:
            raise ModelError(f"{route.name} answered {response.status_code}", response.status_code)
        if response.status_code >= 400:
            raise ModelError(f"{route.name} refused the request ({response.status_code}): {response.text[:200]}",
                             response.status_code)
        return response.json()

    async def _embed(self, route: Route, texts: list[str]) -> list[list[float]]:
        body = await self._post(route, "/embeddings", {"model": route.embedding_model, "input": texts})
        return [item["embedding"] for item in sorted(body["data"], key=lambda d: d.get("index", 0))]

    async def _chat(self, route: Route, messages: list[dict], options: dict) -> str:
        body = await self._post(route, "/chat/completions", {"model": route.chat_model, "messages": messages,
                                                             **options})
        return body["choices"][0]["message"]["content"]

    async def call(self, operation, *args):
        """Run an operation on the active route; on a connection error, timeout or 5xx, fail over once."""
        route = self.active or await self.resolve()
        if route is None:
            raise ModelError("No model route is available")
        try:
            return await operation(route, *args)
        except (httpx.TransportError, ModelError) as exc:
            if isinstance(exc, ModelError) and (exc.status is None or exc.status < 500):
                raise
            log.warning("model route %s failed (%s); failing over", route.name, exc)
            fallback = await self.resolve(skip=frozenset({route.name}))
            if fallback is None:
                raise ModelError(f"{route.name} failed and no other model route is available") from exc
            return await operation(fallback, *args)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self.call(self._embed, texts)

    async def chat(self, messages: list[dict], **options) -> str:
        return await self.call(self._chat, messages, options)

    # status
    def status(self) -> dict:
        configured = any(o.status != "not-configured" for o in self.outcomes)
        if self.active is not None:
            state = "up"
        elif not self.outcomes:
            state = "unknown"
        else:
            state = "down" if configured else "not-configured"
        out: dict[str, Any] = {"status": state, "routes": [o.as_dict() for o in self.outcomes]}
        if self.evaluated_at:
            out["evaluatedAt"] = self.evaluated_at
        if self.active is not None:
            out.update({"route": self.active.name, "baseUrl": self.active.base_url,
                        "chatModel": self.active.chat_model, "embeddingModel": self.active.embedding_model})
            if self.dimension is not None:
                out["embeddingDimension"] = self.dimension
                if self.dimension != self.settings.embedding_dimension:
                    out["dimensionMismatch"] = (f"the model gives {self.dimension}, collections are created with "
                                                f"EMBEDDING_DIMENSION={self.settings.embedding_dimension}")
            elif self.dimension_detail:
                out["dimensionDetail"] = self.dimension_detail
        return out

    async def aclose(self) -> None:
        await self.http.aclose()
        if self.k8s is not None:
            await self.k8s.aclose()
