"""The operations of a TMFC060 instance, read from the OAS it serves, and the user's capabilities on them.

Each instance serves its TMFxxx and TMF701 OAS next to the API (``<apiUrl>/openapi.yaml``, the URL its ExposedAPI
``specification`` points at). Each operation lists the roles allowed to call it in ``x-required-roles``; the
``x-lifecycle`` extensions of the ``Knowledge`` and ``KnowledgeSpecification`` schemas say which role may make each
lifecycle transition, and ``x-review-kinds`` of ``ReviewItem`` which role may post each kind of review item.

The table serves two purposes: the portal's ``capabilities`` (what the page shows), and the proxy allowlist (only
operations of the OAS are forwarded). The component itself stays the authority on every call.

An OAS comes from a component, so it is read with a size cap and ``yaml.safe_load``, and every extension is
type-checked; anything unusable makes the spec unavailable rather than partly trusted.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import yaml

from config import get_settings
from k8s.resources.knowledge_instances import KnowledgeInstance
from utils.cache import knowledge_spec_cache
from utils.exceptions import CanvasPortalError

logger = logging.getLogger(__name__)

MAX_SPEC_BYTES = 2 * 1024 * 1024
SPEC_TTL_SECONDS = 300
FAILURE_TTL_SECONDS = 30
TIMEOUT_SECONDS = 5.0
METHODS = ("get", "post", "put", "patch", "delete")
SYSTEM_ROLE = "canvasSystemRole"
# Operations the page never calls: event listeners, hub registration, event sources and JSON Schemas.
EXCLUDED_PREFIXES = ("/listener/", "/hub", "/eventSource/", "/schema/")
LIFECYCLE_SCHEMAS = {"knowledge": "Knowledge", "knowledgeSpecification": "KnowledgeSpecification"}
TEMPLATE_SEGMENT = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_-]*|\{[A-Za-z][A-Za-z0-9_]*\})$")


class SpecUnavailable(CanvasPortalError):
    def __init__(self):
        super().__init__("The knowledge instance's API description is unavailable", 502)


@dataclass(frozen=True)
class Operation:
    api: str  # "knowledge" (TMFxxx) or "process" (TMF701)
    method: str
    template: str
    operation_id: str
    roles: frozenset[str]
    matcher: re.Pattern = field(compare=False, repr=False)


@dataclass(frozen=True)
class InstanceSpec:
    operations: tuple[Operation, ...]
    transitions: dict[str, tuple[tuple[str, str, str], ...]]  # kind -> (from, to, role)
    review_kinds: dict[str, str]  # review kind -> role

    def match(self, method: str, path: str) -> Optional[tuple[Operation, dict[str, str]]]:
        """The operation for a request path (relative to the API base), with its path parameters."""
        method = method.lower()
        for api in ("knowledge", "process"):
            for operation in self.operations:
                if operation.api != api or operation.method != method:
                    continue
                found = operation.matcher.fullmatch(path)
                if found:
                    return operation, found.groupdict()
        return None


def _compile(template: str) -> Optional[re.Pattern]:
    segments = template.strip("/").split("/")
    if not template.startswith("/") or not all(TEMPLATE_SEGMENT.match(s) for s in segments):
        return None
    parts = []
    for segment in segments:
        if segment.startswith("{"):
            parts.append(f"(?P<{segment[1:-1]}>[^/]+)")
        else:
            parts.append(re.escape(segment))
    try:
        return re.compile("/" + "/".join(parts))
    except re.error:
        return None


def _string_list(value: Any) -> Optional[list[str]]:
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return value
    return None


def parse_spec(documents: dict[str, Any]) -> InstanceSpec:
    """Build the table from {"knowledge": <TMFxxx OAS>, "process": <TMF701 OAS or None>}; raises ValueError."""
    operations = []
    transitions: dict[str, tuple] = {}
    review_kinds: dict[str, str] = {}
    for api, document in documents.items():
        if document is None:
            continue
        if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
            raise ValueError(f"{api} OAS has no paths")
        for template, item in document["paths"].items():
            if not isinstance(template, str) or not isinstance(item, dict):
                continue
            if template.startswith(EXCLUDED_PREFIXES):
                continue
            matcher = _compile(template)
            if matcher is None:
                continue
            for method in METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                operation_id, roles = op.get("operationId"), _string_list(op.get("x-required-roles"))
                if not isinstance(operation_id, str) or not operation_id or not roles:
                    continue
                operations.append(Operation(api, method, template, operation_id, frozenset(roles), matcher))
        if api != "knowledge":
            continue
        schemas = (document.get("components") or {}).get("schemas") or {}
        if not isinstance(schemas, dict):
            continue
        for kind, schema_name in LIFECYCLE_SCHEMAS.items():
            lifecycle = (schemas.get(schema_name) or {}).get("x-lifecycle") if isinstance(schemas.get(schema_name), dict) else None
            items = lifecycle.get("transitions") if isinstance(lifecycle, dict) else None
            transitions[kind] = tuple(
                (t["from"], t["to"], t["role"])
                for t in items or []
                if isinstance(t, dict) and all(isinstance(t.get(k), str) and t.get(k) for k in ("from", "to", "role"))
            )
        review = schemas.get("ReviewItem") if isinstance(schemas.get("ReviewItem"), dict) else {}
        kinds = review.get("x-review-kinds")
        if isinstance(kinds, dict):
            review_kinds = {
                kind: spec["role"]
                for kind, spec in kinds.items()
                if isinstance(kind, str) and isinstance(spec, dict) and isinstance(spec.get("role"), str)
            }
    if not any(op.api == "knowledge" for op in operations):
        raise ValueError("TMFxxx OAS has no usable operations")
    return InstanceSpec(tuple(operations), transitions, review_kinds)


# Tests replace the transport; None is httpx's default.
transport: Optional[httpx.BaseTransport] = None


def _fetch_document(url: str) -> Any:
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client, \
                client.stream("GET", url, headers={"Accept": "application/yaml, application/json"}) as response:
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_SPEC_BYTES:
                    raise ValueError("too large")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise ValueError(type(exc).__name__) from None
    try:
        # The C loader when PyYAML has it: the same safe constructors, much faster on a large OAS.
        return yaml.load(bytes(body), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except (yaml.YAMLError, RecursionError, UnicodeDecodeError):
        raise ValueError("not YAML") from None


def load_spec(instance: KnowledgeInstance) -> InstanceSpec:
    """The instance's operations table, cached; raises SpecUnavailable."""
    key = f"{instance.name}|{instance.apiUrl}|{instance.processUrl}"
    cached = knowledge_spec_cache.get(key)
    if isinstance(cached, InstanceSpec):
        return cached
    if cached is not None:
        raise SpecUnavailable()
    if not instance.apiUrl:
        raise SpecUnavailable()
    try:
        documents = {"knowledge": _fetch_document(f"{instance.apiUrl}/openapi.yaml")}
        documents["process"] = None
        if instance.processUrl:
            try:
                documents["process"] = _fetch_document(f"{instance.processUrl}/openapi.yaml")
            except ValueError as exc:
                logger.warning("TMF701 OAS of knowledge instance %s unavailable: %s", instance.name, exc)
        spec = parse_spec(documents)
    except ValueError as exc:
        logger.warning("OAS of knowledge instance %s unavailable: %s", instance.name, exc)
        knowledge_spec_cache.set(key, "unavailable", ttl_seconds=FAILURE_TTL_SECONDS)
        raise SpecUnavailable() from None
    knowledge_spec_cache.set(key, spec, ttl_seconds=SPEC_TTL_SECONDS)
    return spec


def user_roles(claims: dict[str, Any], instance: KnowledgeInstance) -> list[str]:
    """The user's client roles on the instance's Keycloak client (its component name)."""
    access = claims.get("resource_access")
    client = access.get(instance.name) if isinstance(access, dict) else None
    roles = client.get("roles") if isinstance(client, dict) else None
    return sorted({role for role in roles if isinstance(role, str) and role}) if isinstance(roles, list) else []


def effective_roles(roles: list[str]) -> set[str]:
    """The OAS roles the user holds, as the component computes them: a client role named
    KNOWLEDGE_CANVAS_SYSTEM_ROLE counts as the symbolic canvasSystemRole, and a role literally named
    canvasSystemRole grants nothing."""
    effective = {role for role in roles if role != SYSTEM_ROLE}
    if get_settings().KNOWLEDGE_CANVAS_SYSTEM_ROLE in roles:
        effective.add(SYSTEM_ROLE)
    return effective


def capabilities(spec: InstanceSpec, roles: list[str]) -> dict:
    effective = effective_roles(roles)
    return {
        "roles": roles,
        "operations": {op.operation_id: not op.roles.isdisjoint(effective) for op in spec.operations},
        "transitions": {
            kind: [{"from": src, "to": dst} for src, dst, role in items if role in effective]
            for kind, items in spec.transitions.items()
        },
        "reviewKinds": sorted(kind for kind, role in spec.review_kinds.items() if role in effective),
    }
