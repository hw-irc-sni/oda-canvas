"""The AI > Knowledge page's backend: TMFC060 Knowledge Management instances and a proxy to their APIs.

``GET /api/knowledge/instances`` lists the instances (``k8s/resources/knowledge_instances.py``) with the user's
``capabilities`` on each, computed from the roles in the user's token and the ``x-required-roles``,
``x-lifecycle`` and ``x-review-kinds`` extensions of the OAS the instance serves (``knowledge_spec.py``), and its active
model route (``knowledge_health.py``).

``/api/knowledge/{instance}/{path}`` forwards a call to the instance's TMFxxx or TMF701 API with the **user's own
bearer token**; the component validates it and checks the role, and its 403 is passed through. Only operations of
the instance's OAS are forwarded. Upstream failures never reach the browser as they are: the frontend treats a 401
as an expired portal session, so a 401, a redirect, a 5xx, a timeout or an unreachable instance becomes a 502. Other
4xx answers (400, 404, 409, 413, 422…) pass through with the component's RFC 7807 body.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from auth.jwt_validator import validate_token
from auth.middleware import bearer_scheme
from config import get_settings
from k8s.resources import knowledge_health, knowledge_instances, knowledge_spec
from k8s.resources.knowledge_instances import DNS_LABEL, KnowledgeInstance
from utils.exceptions import CanvasPortalError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
BODY_METHODS = {"POST", "PUT", "PATCH"}
FORWARDED_REQUEST_HEADERS = ("accept", "content-type", "if-match", "if-none-match")
FORWARDED_RESPONSE_HEADERS = (
    "content-type", "content-disposition", "etag", "last-modified", "x-total-count", "x-result-count",
    # How a search ran (hybrid, dense, or sparse without embeddings), and whether a query's rows were cut
    "x-search-mode", "x-result-truncated")
# Operations whose body may use KNOWLEDGE_MAX_UPLOAD_BYTES instead of KNOWLEDGE_MAX_BODY_BYTES.
UPLOAD_OPERATIONS = {"createKnowledgeDocument"}
# Document content is whatever a knowledge-editor uploaded, HTML included: served from the portal's origin it must
# never render, so it is always a sandboxed download.
DOWNLOAD_OPERATIONS = {"retrieveKnowledgeDocumentContent"}
# Characters kept as they are in a forwarded path segment (RFC 3986 pchar), so CURIEs such as tio:Region stay readable.
SEGMENT_SAFE = "-._~:@!$&'()*+,;="

# Tests replace the transport; None is httpx's default.
transport: Optional[httpx.AsyncBaseTransport] = None


@dataclass(frozen=True)
class Caller:
    token: str
    claims: dict[str, Any]

    @property
    def username(self) -> str:
        return self.claims.get("preferred_username") or self.claims.get("sub", "")


async def get_caller(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Caller:
    """The validated portal token: its claims for capabilities, and the token itself to forward."""
    claims = await validate_token(credentials.credentials)
    return Caller(token=credentials.credentials, claims=claims)


class BodyTooLarge(Exception):
    pass


def _too_large() -> CanvasPortalError:
    return CanvasPortalError("The request body is larger than the portal allows", 413)


async def _instance_view(instance: KnowledgeInstance, caller: Caller) -> dict:
    capabilities, error, model_route = None, None, None
    if not instance.available:
        error = "The knowledge instance is not available"
    else:
        try:
            spec = await run_in_threadpool(knowledge_spec.load_spec, instance)
            roles = knowledge_spec.user_roles(caller.claims, instance)
            capabilities = knowledge_spec.capabilities(spec, roles)
            if roles:
                model_route = await run_in_threadpool(knowledge_health.model_route, instance)
        except knowledge_spec.SpecUnavailable as exc:
            error = exc.message
    return {**instance.public(), "capabilities": capabilities, "capabilitiesError": error, "modelRoute": model_route}


@router.get("/instances")
async def get_instances(caller: Caller = Depends(get_caller)):
    discovery = await run_in_threadpool(knowledge_instances.list_instances)
    views = await asyncio.gather(*(_instance_view(instance, caller) for instance in discovery.instances))
    return {"discovery": get_settings().KNOWLEDGE_DISCOVERY, "stale": discovery.stale, "instances": list(views)}


def _upstream_path(path: str) -> Optional[str]:
    """The request path relative to the API base, or None when it isn't a plain sequence of segments."""
    segments = path.split("/")
    if not path or any(segment in ("", ".", "..") for segment in segments):
        return None
    return "/" + "/".join(segments)


async def _limited(request: Request, limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise BodyTooLarge()
        if chunk:
            yield chunk


def _attachment(disposition: Optional[str]) -> str:
    """Content-Disposition: attachment, keeping the component's filename parameters (and nothing else)."""
    params = []
    for part in (disposition or "").split(";")[1:]:
        name = part.split("=", 1)[0].strip().lower()
        if name in ("filename", "filename*") and "\r" not in part and "\n" not in part:
            params.append(part.strip())
    return "; ".join(["attachment", *params])


def _failure(instance: KnowledgeInstance, operation_id: str, reason: str, message: str) -> CanvasPortalError:
    logger.warning("Knowledge instance %s %s: %s", instance.name, operation_id, reason)
    return CanvasPortalError(message, 502)


@router.api_route("/{instance_name}/{path:path}", methods=PROXY_METHODS)
async def proxy(instance_name: str, path: str, request: Request, caller: Caller = Depends(get_caller)):
    settings = get_settings()
    if not DNS_LABEL.match(instance_name):
        raise CanvasPortalError(f"Knowledge instance '{instance_name}' not found", 404)
    instance = await run_in_threadpool(knowledge_instances.get_instance, instance_name)
    relative = _upstream_path(path)
    if relative is None:
        raise CanvasPortalError("Not found", 404)
    spec = await run_in_threadpool(knowledge_spec.load_spec, instance)
    matched = spec.match(request.method, relative)
    if matched is None:
        raise CanvasPortalError("Not found", 404)
    operation, _ = matched
    # Without any role on the instance the token doesn't name it in aud, and the component would answer 401.
    if not knowledge_spec.user_roles(caller.claims, instance):
        raise CanvasPortalError("You have no role on this knowledge instance", 403)
    base = instance.apiUrl if operation.api == "knowledge" else instance.processUrl
    if not base:
        raise knowledge_spec.SpecUnavailable()
    url = base + "/" + "/".join(quote(segment, safe=SEGMENT_SAFE) for segment in relative.strip("/").split("/"))

    headers = {name: request.headers[name] for name in FORWARDED_REQUEST_HEADERS if name in request.headers}
    headers["authorization"] = f"Bearer {caller.token}"
    limit = settings.KNOWLEDGE_MAX_UPLOAD_BYTES if operation.operation_id in UPLOAD_OPERATIONS else settings.KNOWLEDGE_MAX_BODY_BYTES
    content = None
    # A request without Content-Length or Transfer-Encoding has no body (a validation run, for example); it is
    # forwarded without one rather than as an empty chunked stream.
    has_body = "content-length" in request.headers or "transfer-encoding" in request.headers
    if request.method in BODY_METHODS and has_body:
        declared = request.headers.get("content-length")
        if declared is not None:
            if not declared.isdigit():
                raise CanvasPortalError("Invalid Content-Length", 400)
            if int(declared) > limit:
                raise _too_large()
            headers["content-length"] = declared
        content = _limited(request, limit)

    client = httpx.AsyncClient(transport=transport, timeout=settings.KNOWLEDGE_TIMEOUT_SECONDS, follow_redirects=False)
    upstream = client.build_request(
        request.method, url, params=list(request.query_params.multi_items()), headers=headers, content=content)
    try:
        response = await client.send(upstream, stream=True)
    except BodyTooLarge:
        await client.aclose()
        raise _too_large() from None
    except httpx.TimeoutException:
        await client.aclose()
        raise _failure(instance, operation.operation_id, "timeout", "The knowledge instance did not respond in time") from None
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        await client.aclose()
        raise _failure(instance, operation.operation_id, type(exc).__name__, "The knowledge instance is unreachable") from None

    status = response.status_code
    if status == 401 or status < 200 or 300 <= status < 400 or status >= 500:
        await response.aclose()
        await client.aclose()
        raise _failure(instance, operation.operation_id, f"HTTP {status}", "The knowledge instance returned an error")

    async def close():
        await response.aclose()
        await client.aclose()

    passed = {name: response.headers[name] for name in FORWARDED_RESPONSE_HEADERS if name in response.headers}
    passed["x-content-type-options"] = "nosniff"
    if operation.operation_id in DOWNLOAD_OPERATIONS and 200 <= status < 300:
        passed["content-disposition"] = _attachment(response.headers.get("content-disposition"))
        passed["content-security-policy"] = "sandbox"
    return StreamingResponse(response.aiter_bytes(), status_code=status, headers=passed, background=BackgroundTask(close))
