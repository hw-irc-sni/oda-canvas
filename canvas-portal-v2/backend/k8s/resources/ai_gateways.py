"""Read the active agentgateway configuration for the AI Gateways page.

The source is agentgateway's /config_dump, a debug API with no stability
guarantee, so parsing is defensive. Only whitelisted fields leave this module.
"""

import base64
import binascii
import ipaddress
import json
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, NamedTuple
from urllib.parse import urljoin, urlsplit

import httpx
from cachetools import TTLCache

from config import get_settings

MCP_ACCEPT = "application/json, text/event-stream"
MAX_MCP_RESPONSE_BYTES = 1024 * 1024
MCP_DISCOVERY_DEADLINE_SECONDS = 8.0
MAX_MCP_TEXT_LENGTH = 2048
MAX_MCP_SCHEMA_BYTES = 64 * 1024
MAX_MCP_SCHEMA_DEPTH = 16
MAX_MCP_ARGUMENTS_BYTES = 64 * 1024
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_CONNECT_TIMEOUT_SECONDS = 10.0
MCP_CALL_DEADLINE_SECONDS = 60.0
MCP_CLOSE_TIMEOUT_SECONDS = 2.0
MAX_CONCURRENT_MCP_CALLS = 8
# Server icons (serverInfo.icons) are new in this protocol version.
MCP_ICON_PROTOCOL_VERSION = "2025-11-25"
MCP_ICON_DEADLINE_SECONDS = 6.0
MAX_MCP_ICON_BYTES = 64 * 1024
MCP_ICON_CANDIDATES = 3
MCP_ICON_CACHE_SECONDS = 300

LLM_TEST_PROMPT = "PING"
# Models that reason first can use most of this before replying: 64 nearly cut off a PING.
LLM_TEST_MAX_TOKENS = 256
LLM_TEST_TIMEOUT = httpx.Timeout(10.0, read=60.0)
LLM_TEST_DEADLINE_SECONDS = 120.0
MAX_LLM_TEST_RESPONSE_BYTES = 1024 * 1024

# Case-sensitive `[Tt]oken$`, so rate-limit fields such as maxTokens stay visible.
_SECRET_KEY = re.compile(
    r"[Ss]ecret|[Pp]assword|[Aa]pi[-_]?[Kk]ey|[Aa]uthorization|[Cc]ookie|[Cc]redential|[Tt]oken$|^key$"
)
# user:password@host at the start of a word or after a slash. It needs a colon before
# the last @, so plain email addresses stay readable.
_USERINFO_WITH_PASSWORD = r"(?<![^\s/])[^/\s:]+:[^/\s]+(?=@)"
_SECRET_VALUE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+\S+"  # Authorization values
    r"|\bsk-[A-Za-z0-9_-]{8,}"  # Provider API keys
    r"|(?<=://)[^/\s]*(?=@)"  # URL userinfo, up to the last @ in the authority
    r"|^[^/\s]*(?=@)"  # Userinfo on a host with no scheme
    rf"|{_USERINFO_WITH_PASSWORD}"
)
# For MCP descriptions only: an auth scheme counts when followed by a credential-length
# token, so that prose such as "Basic calculator" stays readable.
_SECRET_IN_PROSE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/=._~-]{16,}"
    r"|\bsk-[A-Za-z0-9_-]{8,}"
    r"|(?<=://)[^/\s]*(?=@)"
    rf"|{_USERINFO_WITH_PASSWORD}"
)
_PARAM_START = re.compile(r"[?&;#][\w-]*=")
_PARAM_VALUE_END = re.compile(r"[&;#\s]")
_SECRET_PARAM_NAME = re.compile(r"(?i)key|token|secret|password|credential|auth|sig")

# Policies shown elsewhere on the page, or with nothing to show.
_NON_TRAFFIC_POLICIES = {"a2a", "budget"}
_POLICY_FIELDS = {
    "timeout": ("requestTimeout", "backendRequestTimeout"),
    "retry": ("attempts", "backoff", "codes"),
    "localRateLimit": ("type", "maxTokens", "tokensPerFill", "fillInterval"),
}

_discovery_cache: TTLCache = TTLCache(maxsize=1, ttl=30)
_discovery_lock = threading.Lock()
# Keyed by the targets probed, so a config change probes again.
_icon_cache: TTLCache = TTLCache(maxsize=1, ttl=MCP_ICON_CACHE_SECONDS)
_icon_lock = threading.Lock()
# The page's Content-Security-Policy only allows images from data: URIs, so icons are sent as one of these.
_ICON_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"})
# Calls run on the shared worker threads, so slow upstreams mustn't be able to take them all.
_mcp_call_slots = threading.BoundedSemaphore(MAX_CONCURRENT_MCP_CALLS)


class _Backend(NamedTuple):
    kind: str
    body: dict[str, Any]
    policies: list[Any]


_MISSING_BACKEND = _Backend("", {}, [])

# What the form reads. Anything else in a schema is dropped.
_SCHEMA_SCALAR_FIELDS = ("title", "minimum", "maximum")
_TOOL_ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
_PROTOCOL_VERSION_FORMAT = re.compile(r"[\w.-]{1,32}")
_SSE_LINE_END = re.compile(r"\r\n|\r|\n")
_MCP_CALL_METHODS = {"tools": "tools/call", "resources": "resources/read"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def sanitize(value: Any) -> Any:
    """Mask secret-like keys and values. Upstream redaction is only per field."""
    if isinstance(value, dict):
        return {k: "<redacted>" if _SECRET_KEY.search(str(k)) else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _mask_params(_SECRET_VALUE.sub("<redacted>", value))
    return value


def _mask_params(text: str) -> str:
    """Mask secret-like parameter values, including parameters nested in another's value.

    A secret value runs to the next delimiter, even past a `?`. This is one scan,
    so its time stays linear on hostile input.
    """
    parts = []
    position = 0
    for match in _PARAM_START.finditer(text):
        if match.start() < position or not _SECRET_PARAM_NAME.search(match.group()):
            continue
        end = _PARAM_VALUE_END.search(text, match.end())
        parts += [text[position:match.end()], "<redacted>"]
        position = end.start() if end else len(text)
    parts.append(text[position:])
    return "".join(parts)


def _mcp_text(value: Any) -> str:
    """Upstream servers control this text, so limit its length. Mask first, so a cut can't hide a secret."""
    return sanitize(str(value or ""))[:MAX_MCP_TEXT_LENGTH]


def _mask_prose(text: str) -> str:
    return _mask_params(_SECRET_IN_PROSE.sub("<redacted>", text))


def _mcp_description(value: Any) -> str:
    return _mask_prose(str(value or ""))[:MAX_MCP_TEXT_LENGTH]


def _mask_prose_values(value: Any) -> Any:
    """Mask string values but keep every key, so a masked MCP message keeps its shape."""
    if isinstance(value, dict):
        return {k: _mask_prose_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_prose_values(item) for item in value]
    return _mask_prose(value) if isinstance(value, str) else value


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _filter_schema(schema: Any, depth: int = 0) -> dict[str, Any]:
    """Keep the parts of an MCP schema that the form reads.

    This doesn't use sanitize(): it would replace the schema of a field named `password`
    with a string, and the form for that field would be lost.
    """
    if not isinstance(schema, dict) or depth > MAX_MCP_SCHEMA_DEPTH:
        return {}
    result: dict[str, Any] = {}
    kind = schema.get("type")
    if isinstance(kind, str) or (isinstance(kind, list) and all(isinstance(k, str) for k in kind)):
        result["type"] = kind
    for field in _SCHEMA_SCALAR_FIELDS:
        if field in schema and _is_json_scalar(schema[field]):
            result[field] = _mask_prose_values(schema[field])
    if isinstance(schema.get("description"), str):
        result["description"] = _mcp_description(schema["description"])
    required = _as_list(schema.get("required"))
    if required and all(isinstance(name, str) for name in required):
        result["required"] = required
    if "default" in schema and _is_json_scalar(schema["default"]):
        result["default"] = _mask_prose_values(schema["default"])
    choices = _as_list(schema.get("enum"))
    if choices and all(_is_json_scalar(choice) for choice in choices):
        result["enum"] = _mask_prose_values(choices)
    for field in ("anyOf", "oneOf"):
        if field in schema:
            result[field] = [_filter_schema(item, depth + 1) for item in _as_list(schema[field])]
    if "properties" in schema:
        result["properties"] = {
            str(name): _filter_schema(item, depth + 1)
            for name, item in _as_dict(schema["properties"]).items()
        }
    return result


def _without_schema_values(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop defaults and choices at every level. The result is bounded by _filter_schema's depth limit."""
    result = {key: value for key, value in schema.items() if key not in ("default", "enum")}
    for field in ("anyOf", "oneOf"):
        if field in result:
            result[field] = [_without_schema_values(item) for item in result[field]]
    if "properties" in result:
        result["properties"] = {name: _without_schema_values(item) for name, item in result["properties"].items()}
    return result


def without_schema_values(summary: dict[str, Any]) -> dict[str, Any]:
    """The summary for someone who can't call tools, who has no use for a schema's values.

    Defaults and choices can hold real values such as keys, and no list of field names
    can reliably find them. This copies what it changes, because discovery is cached.
    """
    discovered = summary.get("discovered")
    if not discovered:
        return summary
    tools = [
        {**tool, "inputSchema": tool["inputSchema"] and _without_schema_values(tool["inputSchema"])}
        for tool in discovered["tools"]
    ]
    return {**summary, "discovered": {**discovered, "tools": tools}}


def _input_schema(schema: Any) -> dict[str, Any] | None:
    """None tells the page to offer raw JSON input instead of a form."""
    if not isinstance(schema, dict):
        return None
    filtered = _filter_schema(schema)
    return filtered if len(json.dumps(filtered)) <= MAX_MCP_SCHEMA_BYTES else None


def _tool_annotations(value: Any) -> dict[str, Any]:
    annotations = _as_dict(value)
    result: dict[str, Any] = {
        hint: annotations[hint] for hint in _TOOL_ANNOTATION_HINTS if isinstance(annotations.get(hint), bool)
    }
    if annotations.get("title"):
        result["title"] = _mcp_description(annotations["title"])
    return result


def _format_match(match: Any) -> str:
    match = _as_dict(match)
    path = _as_dict(match.get("path"))
    if "exact" in path:
        text = str(path["exact"])
    elif "pathPrefix" in path:
        text = f"{str(path['pathPrefix']).rstrip('/')}/*"
    elif "regex" in path:
        text = f"regex {path['regex']}"
    else:
        text = "*"
    method = match.get("method")
    return f"{method} {text}" if isinstance(method, str) else text


def _backend_index(dump: dict[str, Any]) -> dict[str, _Backend]:
    index = {}
    for entry in _as_list(dump.get("backends")):
        entry = _as_dict(entry)
        for kind, body in _as_dict(entry.get("backend")).items():
            body = _as_dict(body)
            name = body.get("name")
            if isinstance(name, str):
                index[name] = _Backend(kind, body, _as_list(entry.get("inlinePolicies")))
    return index


def _backend_ref(ref: Any) -> str:
    return ref.lstrip("/") if isinstance(ref, str) else ""


def _routes(dump: dict[str, Any], backends: dict[str, _Backend]) -> list[dict[str, Any]]:
    """Flatten routes, leaving out the gateway's own read path (internal backends only)."""
    routes = []
    for bind in _as_list(dump.get("binds")):
        for listener in _as_dict(_as_dict(bind).get("listeners")).values():
            for route in _as_dict(_as_dict(listener).get("routes")).values():
                route = _as_dict(route)
                refs = [_as_dict(b) for b in _as_list(route.get("backends"))]
                names = [_backend_ref(b.get("backend")) for b in refs]
                kinds = [backends.get(n, _MISSING_BACKEND).kind for n in names]
                if kinds and all(kind == "internal" for kind in kinds):
                    continue
                routes.append({
                    "name": str(route.get("name") or route.get("key") or ""),
                    "matches": [_format_match(m) for m in _as_list(route.get("matches"))],
                    "refs": refs,
                    "backendNames": names,
                    "policies": _as_list(route.get("inlinePolicies")),
                })
    return routes


def _routes_to(routes: list[dict[str, Any]], names: set[str]) -> list[str]:
    matches: list[str] = []
    for route in routes:
        if names.intersection(route["backendNames"]):
            matches.extend(m for m in route["matches"] if m not in matches)
    return matches


def _parse_models(backends: dict[str, _Backend], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routers = [name for name, backend in backends.items() if backend.kind == "llmRouter"]
    models = []
    for name, backend in backends.items():
        if backend.kind != "ai":
            continue
        match = _routes_to(routes, {name, *routers})
        for group in _as_list(_as_dict(backend.body.get("target")).get("providers")):
            group = _as_dict(group)
            for status in ("active", "rejected"):
                for model_name, item in _as_dict(group.get(status)).items():
                    endpoint = _as_dict(_as_dict(item).get("endpoint"))
                    provider = next(iter(_as_dict(endpoint.get("provider"))), "")
                    models.append({
                        "name": str(model_name),
                        "provider": provider,
                        "upstream": f"{endpoint.get('hostOverride') or ''}{endpoint.get('pathPrefix') or ''}",
                        "status": status,
                        "matches": match,
                    })
    return models


def _parse_tools(
    backends: dict[str, _Backend], routes: list[dict[str, Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    tools = []
    for name, backend in backends.items():
        if backend.kind != "mcp":
            continue
        target_config = _as_dict(backend.body.get("target"))
        match = _routes_to(routes, {name})
        for target in _as_list(target_config.get("targets")):
            target = _as_dict(target)
            transport, spec = next(
                ((k, v) for k, v in target.items() if isinstance(v, dict)), ("", {})
            )
            ref = _backend_ref(_as_dict(spec.get("backend")).get("backend"))
            resolved = backends.get(ref, _MISSING_BACKEND)
            host = resolved.body.get("target") if resolved.kind == "host" else None
            if not isinstance(host, str):
                host = ""
                warnings.append(f"MCP target {target.get('name')} has no resolvable host")
            tools.append({
                "name": str(target.get("name") or ""),
                "transport": transport,
                "upstream": f"{host}{spec.get('path') or ''}",
                "stateful": bool(target_config.get("stateful")),
                "failureMode": str(target_config.get("failureMode") or ""),
                "matches": match,
            })
    return tools


def _has_a2a(policies: list[Any]) -> bool:
    return any("a2a" in _as_dict(policy) for policy in policies)


def _parse_agents(backends: dict[str, _Backend], routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agents = []
    for route in routes:
        for ref, name in zip(route["refs"], route["backendNames"]):
            resolved = backends.get(name, _MISSING_BACKEND)
            if not (
                _has_a2a(_as_list(ref.get("inlinePolicies")))
                or _has_a2a(route["policies"])
                or _has_a2a(resolved.policies)
            ):
                continue
            target = resolved.body.get("target")
            agents.append({
                "name": route["name"],
                "matches": route["matches"],
                "backend": target if isinstance(target, str) else name,
            })
    return agents


def _policy_settings(policy_type: str, body: Any) -> list[dict[str, Any]]:
    if policy_type == "urlRewrite":
        path = _as_dict(_as_dict(body).get("path"))
        return [{field: path[field] for field in ("prefix", "full") if field in path}]
    fields = _POLICY_FIELDS.get(policy_type, ())
    items = body if isinstance(body, list) else [body]
    return [
        {field: item[field] for field in fields if field in item}
        for item in map(_as_dict, items)
    ]


def _parse_traffic(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for route in routes:
        for policy in route["policies"]:
            for policy_type, body in _as_dict(policy).items():
                if policy_type in _NON_TRAFFIC_POLICIES:
                    continue
                rows.extend(
                    {
                        "route": route["name"],
                        "matches": route["matches"],
                        "policy": policy_type,
                        "settings": settings,
                    }
                    for settings in _policy_settings(policy_type, body)
                )
    return rows


def parse_config_dump(dump: Any) -> dict[str, Any]:
    """Turn a /config_dump document into the four page sections."""
    dump = _as_dict(dump)
    warnings: list[str] = []
    backends = _backend_index(dump)
    routes = _routes(dump, backends)
    version = _as_dict(dump.get("version"))
    return sanitize({
        "version": str(version.get("version") or ""),
        "models": _parse_models(backends, routes),
        "tools": _parse_tools(backends, routes, warnings),
        "agents": _parse_agents(backends, routes),
        "traffic": _parse_traffic(routes),
        "warnings": warnings,
    })


def _sse_data_events(body: str) -> Iterator[str]:
    lines: list[str] = []
    # Not str.splitlines: it also breaks on U+2028 and others, which JSON text can contain.
    for line in [*_SSE_LINE_END.split(body), ""]:
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif not line:
            if any(lines):
                yield "\n".join(lines)
            lines = []


def _load_mcp_json(text: str) -> dict[str, Any]:
    """Parse upstream JSON, refusing what the portal couldn't send on to the browser.

    json.loads accepts NaN, 1e999 and lone surrogates, and very deep nesting can overflow
    later. Checking with the response's own settings raises ValueError or RecursionError
    now, instead of an HTTP 500 for the whole page. UnicodeEncodeError is a ValueError.
    """
    document = json.loads(text)
    json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return _as_dict(document)


def _parse_mcp_message(body: str, content_type: str, request_id: int) -> dict[str, Any]:
    """Find the JSON-RPC response to a request. A stream can send notifications before it."""
    if "text/event-stream" not in content_type:
        return _load_mcp_json(body)
    for data in _sse_data_events(body):
        message = _load_mcp_json(data)
        if message.get("id") == request_id:
            return message
    return {}


def _read_raw(response: httpx.Response, deadline: float, limit: int) -> bytes:
    """Limit what's read from upstream MCP servers. Count raw bytes and refuse compression,
    so the limit can't be passed by decompressing."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("MCP response is compressed")
    body = bytearray()
    for chunk in response.iter_raw():
        body += chunk
        if len(body) > limit:
            raise ValueError("MCP response is too large")
        if time.monotonic() > deadline:
            raise ValueError("MCP response took too long")
    return bytes(body)


def _read_limited(response: httpx.Response, deadline: float) -> str:
    return _read_raw(response, deadline, MAX_MCP_RESPONSE_BYTES).decode("utf-8", "replace")


def _mcp_list(client: httpx.Client, url: str, method: str, deadline: float) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    headers = {"Accept": MCP_ACCEPT, "Accept-Encoding": "identity"}
    with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        body = _read_limited(response, deadline)
        message = _parse_mcp_message(body, response.headers.get("content-type", ""), 1)
    return _as_dict(message.get("result"))


def _discover_mcp(client: httpx.Client, url: str) -> dict[str, Any]:
    """List what the gateway's MCP endpoint serves now. This is live state, not config."""
    if not url:
        return {"discovered": None, "discoveryError": "MCP URL isn't configured"}
    try:
        deadline = time.monotonic() + MCP_DISCOVERY_DEADLINE_SECONDS
        tools = _mcp_list(client, url, "tools/list", deadline)
        resources = _mcp_list(client, url, "resources/list", deadline)
    # A deeply nested document raises RecursionError from json.loads.
    except (httpx.HTTPError, ValueError, RecursionError) as exc:
        return {"discovered": None, "discoveryError": "ValueError" if isinstance(exc, RecursionError) else type(exc).__name__}
    return {
        "discovered": {
            "tools": [
                {
                    "name": _mcp_text(t.get("name")),
                    "title": _mcp_description(t.get("title")),
                    "description": _mcp_description(t.get("description")),
                    "inputSchema": _input_schema(t.get("inputSchema")),
                    "annotations": _tool_annotations(t.get("annotations")),
                    "icon": _tool_icon(t.get("icons")),
                }
                for t in map(_as_dict, _as_list(tools.get("tools")))
            ],
            "resources": [
                {
                    "name": _mcp_text(r.get("name")),
                    "uri": _mcp_text(r.get("uri")),
                    "description": _mcp_description(r.get("description")),
                    "mimeType": _mcp_text(r.get("mimeType")),
                }
                for r in map(_as_dict, _as_list(resources.get("resources")))
            ],
            "partial": bool(tools.get("nextCursor") or resources.get("nextCursor")),
        },
        "discoveryError": None,
    }


def _cached_discovery(client: httpx.Client, url: str) -> dict[str, Any]:
    with _discovery_lock:
        cached = _discovery_cache.get("result")
    if cached is not None:
        return cached
    # Fetch without the lock, so a slow upstream can't block other requests.
    # Concurrent misses may each fetch, which is harmless for two read-only calls.
    result = _discover_mcp(client, url)
    with _discovery_lock:
        _discovery_cache["result"] = result
    return result


def _has_policy(policies: list[Any], name: str) -> bool:
    return any(name in _as_dict(policy) for policy in policies)


def _icon_probes(dump: dict[str, Any]) -> tuple[tuple[str, str, bool], ...]:
    """The MCP targets whose own initialize response the portal can read, as (name, URL, needs key).

    The gateway's MCP endpoint answers with agentgateway's serverInfo, not the targets', so
    each target is asked directly. Only streamable HTTP targets. A target the gateway sends a
    key to is asked with the same key, read from the gateway's key Secret when it's needed.
    """
    backends = _backend_index(dump)
    probes = []
    for backend in backends.values():
        if backend.kind != "mcp":
            continue
        for target in map(_as_dict, _as_list(_as_dict(backend.body.get("target")).get("targets"))):
            spec = _as_dict(target.get("mcp"))
            resolved = backends.get(_backend_ref(_as_dict(spec.get("backend")).get("backend")), _MISSING_BACKEND)
            name, host, path = target.get("name"), resolved.body.get("target"), spec.get("path") or ""
            if (
                resolved.kind != "host"
                or not all(isinstance(value, str) for value in (name, host, path))
            ):
                continue
            scheme = "https" if _has_policy(resolved.policies, "backendTLS") else "http"
            needs_key = _has_policy(resolved.policies, "backendAuth")
            probes.append((name, f"{scheme}://{_in_gateway_namespace(host)}{path}", needs_key))
    return tuple(probes)


def _in_gateway_namespace(host: str) -> str:
    """A single-label service name resolves in the gateway's namespace, not the portal's."""
    namespace = get_settings().AGENTGATEWAY_CONFIG_NAMESPACE
    hostname, colon, port = host.partition(":")
    if not namespace or "." in hostname or hostname.startswith("["):
        return host
    return f"{hostname}.{namespace}.svc{colon}{port}"


def _server_icons(client: httpx.Client, url: str, deadline: float, auth: dict[str, str]) -> list[Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_ICON_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "canvas-portal", "version": "1"},
        },
    }
    headers = {"Accept": MCP_ACCEPT, "Accept-Encoding": "identity", **auth}
    with client.stream("POST", url, json=payload, headers=headers) as response:
        _refuse_local_peer(response)
        response.raise_for_status()
        body = _read_limited(response, deadline)
        session = response.headers.get("mcp-session-id", "")
        message = _parse_mcp_message(body, response.headers.get("content-type", ""), 1)
    if session and len(session) <= 256:
        # A stateful server keeps a session for each initialize. Ending it is best effort, and its
        # response body is never read, so it can't be made to send more than the limits allow.
        try:
            with client.stream("DELETE", url, headers={"Mcp-Session-Id": session, "Accept-Encoding": "identity", **auth}):
                pass
        except httpx.HTTPError:
            pass
    return _as_list(_as_dict(_as_dict(message.get("result")).get("serverInfo")).get("icons"))


def _data_uri(mime: str, data: bytes) -> str | None:
    if not data or len(data) > MAX_MCP_ICON_BYTES:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# Addresses that carry an IPv4 address inside, which is_global doesn't look at: NAT64, IPv4-compatible,
# 6to4 and Teredo. They're refused rather than unwrapped.
_TRANSLATED_IPV6 = tuple(
    ipaddress.ip_network(network) for network in ("64:ff9b::/96", "64:ff9b:1::/48", "::/96", "2002::/16", "2001::/32")
)
_IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _is_public_address(address: _IpAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif any(address in network for network in _TRANSLATED_IPV6):
            return False
    return address.is_global


def _is_public_https(url: str) -> bool:
    """An https URL whose host only resolves to public internet addresses."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    return bool(addresses) and all(_is_public_address(address) for address in addresses)


def _peer_address(response: httpx.Response) -> _IpAddress | None:
    """The address the connection really went to. A name can resolve differently when it's
    checked and when it's connected to, so the address is checked again here."""
    stream = response.extensions.get("network_stream")
    try:
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        return ipaddress.ip_address(str(peer[0]).split("%")[0]) if peer else None
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _refuse_local_peer(response: httpx.Response) -> None:
    from k8s.resources.ai_gateway_config import _is_gateway_local

    peer = _peer_address(response)
    if peer is None or _is_gateway_local(peer):
        raise ValueError("MCP server is on a local address")


def _checked_data_uri(src: str) -> str | None:
    """A data: URI icon, rebuilt from its decoded bytes, when it's a base64 image of a type the page shows."""
    header, comma, payload = src[5:].partition(",")
    mime, _, encoding = header.partition(";")
    if src[:5].lower() != "data:" or not comma or encoding.lower() != "base64" or mime.lower() not in _ICON_TYPES:
        return None
    try:
        return _data_uri(mime.lower(), base64.b64decode(payload, validate=True))
    except binascii.Error:
        return None


def _preferred_icons(icons: Any) -> list[dict[str, Any]]:
    """The icons with a src, best first: the page has a light background, so one made for dark ones goes last."""
    candidates = [icon for icon in map(_as_dict, _as_list(icons)) if isinstance(icon.get("src"), str)]
    return sorted(candidates, key=lambda icon: icon.get("theme") == "dark")


def _tool_icon(icons: Any) -> str | None:
    """A tool's icon, from data: URIs only. Tools are listed every 30 seconds, so their icons
    aren't downloaded."""
    for icon in _preferred_icons(icons)[:MCP_ICON_CANDIDATES]:
        uri = _checked_data_uri(icon["src"])
        if uri:
            return uri
    return None


def _icon_data_uri(client: httpx.Client, src: str, server_url: str, deadline: float) -> str | None:
    """An icon as a data: URI, or None. Fetches from the server's own origin, or over https from
    a public address: a server can't make the portal request other addresses in the cluster."""
    if src[:5].lower() == "data:":
        return _checked_data_uri(src)
    url = urljoin(server_url, src)
    icon, server = urlsplit(url), urlsplit(server_url)
    same_origin = (icon.scheme, icon.netloc) == (server.scheme, server.netloc)
    if not same_origin and not _is_public_https(url):
        return None
    headers = {"Accept": ", ".join(sorted(_ICON_TYPES)), "Accept-Encoding": "identity"}
    with client.stream("GET", url, headers=headers) as response:
        if same_origin:
            _refuse_local_peer(response)
        else:
            peer = _peer_address(response)
            if peer is None or not _is_public_address(peer):
                return None
        response.raise_for_status()
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if mime not in _ICON_TYPES:
            return None
        return _data_uri(mime, _read_raw(response, deadline, MAX_MCP_ICON_BYTES))


def _upstream(url: str) -> tuple[str, str, int, str] | None:
    """An address in a form that compares equal however it's written: scheme, host in the
    gateway's namespace, port and path."""
    try:
        parts = urlsplit(url)
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:
        return None
    if not parts.hostname or port is None:
        return None
    hostname = urlsplit(f"//{_in_gateway_namespace(parts.hostname)}").hostname or ""
    return parts.scheme, hostname.rstrip(".").lower(), port, parts.path or "/"


def _target_keys() -> dict[str, tuple[str, str]]:
    """The keys the gateway sends to MCP targets, or none when the portal can't read them."""
    # Imported here: that module imports this one.
    from kubernetes.client.exceptions import ApiException

    from k8s.resources.ai_gateway_mcp import target_keys

    try:
        return target_keys()
    except (GatewayRequestRefused, ApiException, OSError, ValueError):
        return {}


def _discover_icons(client: httpx.Client, probes: tuple[tuple[str, str, bool], ...]) -> dict[str, str]:
    """Icons are decoration: any failure leaves that server without one."""
    # Imported here: that module imports this one.
    from k8s.resources.ai_gateway_config import _points_at_gateway

    deadline = time.monotonic() + MCP_ICON_DEADLINE_SECONDS
    icons = {}
    keys: dict[str, tuple[str, str]] | None = None
    names = [name for name, _, _ in probes]
    for name, url, needs_key in probes:
        if time.monotonic() > deadline:
            break
        # The portal makes these requests from its own pod. A target the gateway reaches on its
        # loopback would reach the portal's instead, and a link-local one the cloud metadata service.
        # Icons are only fetched from this same origin, so this check covers them too.
        if _points_at_gateway(url):
            continue
        auth = {}
        if needs_key:
            # Only read when a target needs one. The key goes to that target's MCP endpoint only,
            # as the gateway sends it, and never with an icon request. The names in the config file
            # and in the running config can differ, during a reload for example, so the key is only
            # sent to the address configured with it, and never for a name used twice.
            keys = _target_keys() if keys is None else keys
            key, configured = keys.get(name, ("", ""))
            if not key or names.count(name) != 1 or _upstream(configured) != _upstream(url):
                continue
            auth = {"Authorization": f"Bearer {key}"}
        try:
            for icon in _preferred_icons(_server_icons(client, url, deadline, auth))[:MCP_ICON_CANDIDATES]:
                uri = _icon_data_uri(client, icon["src"], url, deadline)
                if uri:
                    icons[name] = uri
                    break
        except (httpx.HTTPError, ValueError, RecursionError):
            continue
    return icons


def _cached_icons(client: httpx.Client, probes: tuple[tuple[str, str, bool], ...]) -> dict[str, str]:
    with _icon_lock:
        cached = _icon_cache.get(probes)
    if cached is not None:
        return cached
    icons = _discover_icons(client, probes) if probes else {}
    with _icon_lock:
        _icon_cache[probes] = icons
    return icons


def _read_runtime(client: httpx.Client, config_url: str, headers: dict[str, str]) -> dict[str, Any] | None:
    """Reload state is a nice-to-have: the page still works without it."""
    try:
        response = client.get(urljoin(config_url, "/api/runtime"), headers=headers)
        response.raise_for_status()
        reload_state = _as_dict(_as_dict(response.json()).get("configReload"))
    except (httpx.HTTPError, ValueError):
        return None
    # Leave out lastError: it's free text that can quote the config, and any logged-in
    # user can read this response. The detail is in the gateway's logs.
    return {"synchronized": bool(reload_state.get("synchronized"))}


def _host_resolves(url: str) -> bool:
    try:
        socket.getaddrinfo(urlsplit(url).hostname, None)
    except (socket.gaierror, UnicodeError):
        return False
    return True


def _summary(client: httpx.Client) -> dict[str, Any]:
    settings = get_settings()
    url = settings.AGENTGATEWAY_CONFIG_URL
    if not url:
        return {"state": "not_deployed", "message": "No agentgateway URL is configured."}

    headers = {"Authorization": f"Bearer {settings.AGENTGATEWAY_API_KEY}"}
    try:
        response = client.get(url, headers=headers)
    except httpx.ConnectError:
        if _host_resolves(url):
            return {"state": "unreachable", "message": "Couldn't connect to agentgateway."}
        return {"state": "not_deployed", "message": "The agentgateway service wasn't found."}
    except httpx.HTTPError as exc:
        return {"state": "unreachable", "message": f"Request failed: {type(exc).__name__}."}

    if response.status_code in (401, 403):
        return {"state": "unauthorized", "message": "agentgateway rejected the portal's API key."}
    if response.status_code != 200:
        return {"state": "unreachable", "message": f"agentgateway returned HTTP {response.status_code}."}
    try:
        dump = response.json()
    except ValueError:
        return {"state": "unreachable", "message": "agentgateway returned invalid JSON."}
    if not isinstance(dump, dict):
        return {"state": "unreachable", "message": "agentgateway returned an unexpected document."}

    parsed = parse_config_dump(dump)
    icons = _cached_icons(client, _icon_probes(dump))
    parsed["tools"] = [{**tool, "icon": icons.get(tool["name"])} for tool in parsed["tools"]]
    return {
        "state": "ok",
        "message": "",
        **parsed,
        "runtime": _read_runtime(client, url, headers),
        **_cached_discovery(client, settings.AGENTGATEWAY_MCP_URL),
    }


def get_ai_gateways_summary(client: httpx.Client | None = None) -> dict[str, Any]:
    if client is not None:
        return _summary(client)
    # trust_env=False: cluster addresses must never go through a proxy from the environment.
    with httpx.Client(timeout=4.0, trust_env=False, follow_redirects=False) as owned:
        return _summary(owned)


class GatewayRequestRefused(Exception):
    """The request can't start. The message is safe to show to the user."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _llm_client(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    # trust_env=False: cluster addresses must never go through a proxy from the environment.
    return httpx.AsyncClient(
        transport=transport, timeout=LLM_TEST_TIMEOUT, trust_env=False, follow_redirects=False
    )


async def ensure_model_is_active(model: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Only models the gateway has active can be tested, so this can't reach anything else."""
    settings = get_settings()
    if not settings.AGENTGATEWAY_CONFIG_URL or not settings.AGENTGATEWAY_LLM_URL:
        raise GatewayRequestRefused(503, "Model testing isn't configured.")
    try:
        async with _llm_client(transport) as client:
            response = await client.get(
                settings.AGENTGATEWAY_CONFIG_URL,
                headers={"Authorization": f"Bearer {settings.AGENTGATEWAY_API_KEY}"},
            )
        response.raise_for_status()
        models = parse_config_dump(response.json())["models"]
    except (httpx.HTTPError, ValueError) as exc:
        raise GatewayRequestRefused(502, "Couldn't read the agentgateway configuration.") from exc
    if not any(m["name"] == model and m["status"] == "active" for m in models):
        raise GatewayRequestRefused(404, "This model isn't active on the gateway.")


def _event(**fields: Any) -> bytes:
    return (json.dumps(fields) + "\n").encode()


def _llm_line_events(line: bytes) -> Iterator[dict[str, Any]]:
    """Read one server-sent event line, keeping only the text and how it finished."""
    text = line.decode("utf-8", "replace").strip()
    if not text.startswith("data:"):
        return
    data = text[5:].strip()
    if data == "[DONE]":
        yield {"type": "end"}
        return
    try:
        chunk = _as_dict(json.loads(data))
    except ValueError:
        yield {"type": "error", "message": "The model sent an invalid stream."}
        return
    if "error" in chunk:
        # Fixed text: the upstream's error can quote its address or request details.
        yield {"type": "error", "message": "The model reported an error."}
        return
    for choice in map(_as_dict, _as_list(chunk.get("choices"))):
        delta = _as_dict(choice.get("delta"))
        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if isinstance(reasoning, str):
            yield {"type": "reasoning", "text": reasoning}
        if isinstance(delta.get("content"), str) and delta["content"]:
            yield {"type": "content", "text": delta["content"]}
        if isinstance(choice.get("finish_reason"), str):
            yield {"type": "finish", "reason": choice["finish_reason"]}


def _llm_status_message(status_code: int) -> str:
    if status_code == 429:
        return "The gateway's rate limit was reached. Try again in a minute."
    return f"agentgateway returned HTTP {status_code}."


async def stream_model_test(
    model: str, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[bytes]:
    """Send PING to a model through the gateway, and yield its reply as NDJSON events.

    Events are `reasoning` and `content` with a `text`, then one `done` or `error`.
    Closing this generator closes the upstream request, which stops the generation.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": LLM_TEST_PROMPT}],
        "stream": True,
        "max_tokens": LLM_TEST_MAX_TOKENS,
    }
    # Count raw bytes and refuse compression, so the limit can't be passed by decompressing.
    headers = {"Accept": "text/event-stream", "Accept-Encoding": "identity"}
    deadline = time.monotonic() + LLM_TEST_DEADLINE_SECONDS
    try:
        async with _llm_client(transport) as client, client.stream(
            "POST", get_settings().AGENTGATEWAY_LLM_URL, json=payload, headers=headers
        ) as response:
            if response.status_code != 200:
                yield _event(type="error", message=_llm_status_message(response.status_code))
                return
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                yield _event(type="error", message="The model sent a compressed response.")
                return
            finish_reason = None
            pending = b""
            received = 0
            async for chunk in response.aiter_raw():
                received += len(chunk)
                if received > MAX_LLM_TEST_RESPONSE_BYTES or time.monotonic() > deadline:
                    yield _event(type="error", message="The reply was too long, so the test stopped.")
                    return
                *lines, pending = (pending + chunk).split(b"\n")
                for line in lines:
                    for event in _llm_line_events(line):
                        if event["type"] == "finish":
                            finish_reason = event["reason"]
                        elif event["type"] == "end":
                            yield _event(type="done", finishReason=finish_reason)
                            return
                        else:
                            yield _event(**event)
                            if event["type"] == "error":
                                return
    except httpx.HTTPError as exc:
        yield _event(type="error", message=f"Request failed: {type(exc).__name__}.")
        return
    if finish_reason:
        yield _event(type="done", finishReason=finish_reason)
    else:
        yield _event(type="error", message="The stream ended before the model finished.")


class _McpFailure(Exception):
    """The call failed before a JSON-RPC response. The message is safe to show to the user."""


class _McpSession:
    """One MCP session for one call, recording each JSON-RPC exchange exactly as it happened."""

    def __init__(self, client: httpx.Client, url: str):
        self.client = client
        self.url = url
        self.deadline = time.monotonic() + MCP_CALL_DEADLINE_SECONDS
        self.headers = {"Accept": MCP_ACCEPT, "Accept-Encoding": "identity"}
        self.exchanges: list[dict[str, Any]] = []
        self.next_id = 1
        self.session_id = ""

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        exchange: dict[str, Any] = {"method": payload["method"], "request": payload, "status": None, "response": None}
        self.exchanges.append(exchange)
        # The budget covers the whole session, however many requests it takes.
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _McpFailure("The MCP endpoint took too long to respond.")
        timeout = httpx.Timeout(min(remaining, MCP_CONNECT_TIMEOUT_SECONDS), read=remaining)
        with self.client.stream("POST", self.url, json=payload, headers=self.headers, timeout=timeout) as response:
            exchange["status"] = response.status_code
            if not response.is_success:
                # Fixed text only: an error page can quote internal addresses.
                raise _McpFailure(f"The MCP endpoint returned HTTP {response.status_code}.")
            body = _read_limited(response, self.deadline)
            self.session_id = self.session_id or response.headers.get("mcp-session-id", "")
            if "id" not in payload:
                return {}
            message = _parse_mcp_message(body, response.headers.get("content-type", ""), payload["id"])
        exchange["response"] = message
        return message

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}
        self.next_id += 1
        return self._post(payload)

    def open(self) -> None:
        """Stateless servers don't need this handshake, but stateful ones refuse calls without it."""
        message = self.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "canvas-portal", "version": "1.0"},
        })
        if "error" in message:
            raise _McpFailure("The MCP endpoint refused to start a session.")
        version = _as_dict(message.get("result")).get("protocolVersion")
        valid = isinstance(version, str) and _PROTOCOL_VERSION_FORMAT.fullmatch(version)
        self.headers["MCP-Protocol-Version"] = version if valid else MCP_PROTOCOL_VERSION
        if self.session_id:
            self.headers["Mcp-Session-Id"] = self.session_id
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            # Don't read the body: nothing in it is needed, and the server controls its size.
            with self.client.stream("DELETE", self.url, headers=self.headers, timeout=MCP_CLOSE_TIMEOUT_SECONDS):
                pass
        except httpx.HTTPError:
            pass  # The server expires sessions it doesn't hear from.


def _find_listed(items: Any, field: str, displayed: str) -> str | None:
    """Return the upstream value for one the page displayed, which may be masked or cut short."""
    for item in map(_as_dict, _as_list(items)):
        raw = item.get(field)
        if isinstance(raw, str) and _mcp_text(raw) == displayed:
            return raw
    return None


def _mcp_call(
    kind: str, field: str, target: str, extra_params: dict[str, Any], client: httpx.Client | None
) -> dict[str, Any]:
    url = get_settings().AGENTGATEWAY_MCP_URL
    if not url:
        raise GatewayRequestRefused(503, "MCP calls aren't configured.")
    if not _mcp_call_slots.acquire(blocking=False):
        raise GatewayRequestRefused(429, "Too many MCP calls are running. Try again shortly.")
    owned = None
    if client is None:
        # trust_env=False: cluster addresses must never go through a proxy from the environment.
        client = owned = httpx.Client(trust_env=False, follow_redirects=False)
    session = _McpSession(client, url)
    error = None
    try:
        session.open()
        listed = session.request(f"{kind}/list", {})
        raw = _find_listed(_as_dict(listed.get("result")).get(kind), field, target)
        if raw is None:
            raise GatewayRequestRefused(404, "The gateway doesn't list this item now.")
        session.request(_MCP_CALL_METHODS[kind], {field: raw, **extra_params})
    except _McpFailure as exc:
        error = str(exc)
    except httpx.TimeoutException:
        error = "The MCP endpoint took too long to respond."
    except httpx.HTTPError as exc:
        error = f"Request failed: {type(exc).__name__}."
    except (ValueError, RecursionError):
        error = "The MCP endpoint sent a response that can't be used."
    finally:
        try:
            session.close()
            if owned is not None:
                owned.close()
        finally:
            _mcp_call_slots.release()
    return {"error": error, "exchanges": session.exchanges}


def call_mcp_tool(name: str, arguments: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Call a tool the gateway lists now, and return the exchanges unmasked for the admin who asked."""
    return _mcp_call("tools", "name", name, {"arguments": arguments}, client)


def read_mcp_resource(uri: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Read a resource the gateway lists now, and return the exchanges unmasked for the admin who asked."""
    return _mcp_call("resources", "uri", uri, {}, client)
