"""The agentgateway MCP targets of ODA Component MCP APIs (pure functions, tested without a cluster).

agentgateway (standalone) reads one config file. Its mcp section lists targets, each {name, mcp: {host}}; through
the gateway's MCP endpoint a target's tools are named <target>_<tool>. This operator owns only the targets named
oda-<namespace>-<exposedapi>; every other target (added by hand or in the Canvas Portal) is kept as it is, in place.
"""

import fnmatch

from settings import TARGET_PREFIX


def target_name(namespace: str, exposed_api: str) -> str:
    return f"{TARGET_PREFIX}{namespace}-{exposed_api}"


def is_owned(entry) -> bool:
    return isinstance(entry, dict) and str(entry.get("name", "")).startswith(TARGET_PREFIX)


def is_mcp(spec, **_) -> bool:
    return (spec or {}).get("apiType") == "mcp"


def in_namespaces(namespace: str, patterns) -> bool:
    return not patterns or any(fnmatch.fnmatch(namespace, p) for p in patterns)


def target_for(exposed_api: dict) -> dict | None:
    """The target of a ready MCP ExposedAPI, or None (not mcp, not ready, or no implementation Service).

    The host is the ExposedAPI's implementation Service, reachable from the gateway on any cluster. backendAuth
    passthrough forwards the caller's own token: agents call the component with their own credentials and roles.
    """
    meta, spec, status = exposed_api.get("metadata") or {}, exposed_api.get("spec") or {}, exposed_api.get("status") or {}
    if not is_mcp(spec) or not (status.get("implementation") or {}).get("ready"):
        return None
    if not spec.get("implementation") or not spec.get("port") or not meta.get("name") or not meta.get("namespace"):
        return None
    path = "/" + (spec.get("path") or "").strip("/")
    host = f"http://{spec['implementation']}.{meta['namespace']}.svc.cluster.local:{spec['port']}{path.rstrip('/')}"
    return {"name": target_name(meta["namespace"], meta["name"]), "mcp": {"host": host},
            "policies": {"backendAuth": {"passthrough": {}}}}


def desired_targets(exposed_apis: list[dict], namespaces=()) -> list[dict]:
    """The targets for these ExposedAPIs, sorted by name."""
    out = [t for t in (target_for(a) for a in exposed_apis
                       if in_namespaces((a.get("metadata") or {}).get("namespace", ""), namespaces)) if t]
    return sorted(out, key=lambda t: t["name"])


def merge(document: dict, desired: list[dict], mcp_gateway: str = "default") -> dict:
    """The config with the operator's targets set to ``desired``. Other targets are untouched and keep their
    positions; the operator's own come after them. An mcp section is created only when there is a target to add."""
    document = dict(document or {})
    mcp = document.get("mcp")
    if not isinstance(mcp, dict):
        if not desired:
            return document
        mcp = {"gateways": mcp_gateway, "statefulMode": "stateless", "failureMode": "failOpen", "targets": []}
    else:
        mcp = dict(mcp)
    others = [t for t in (mcp.get("targets") or []) if not is_owned(t)]
    mcp["targets"] = others + list(desired)
    document["mcp"] = mcp
    return document

