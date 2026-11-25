"""TMFOP013 settings, read from the environment (set by charts/agentgateway-mcp-operator)."""

import os
from dataclasses import dataclass

# Prefix of the MCP targets this operator owns: oda-<namespace>-<exposedapi>. Targets without it (added by hand or
# through the Canvas Portal) are never changed.
TARGET_PREFIX = "oda-"
# kopf keeps its state in annotations with this prefix, so it doesn't collide with the API operators on ExposedAPIs
KOPF_PREFIX = "agentgateway-mcp.oda.tmforum.org"


@dataclass(frozen=True)
class Settings:
    """Operator configuration.

    Attributes:
        gateway_namespace: Namespace of the agentgateway release.
        config_map: The agentgateway config ConfigMap and its key.
        pod_selector: Label selector of the gateway pods, nudged after a change so they reload now.
        mcp_gateway: The agentgateway gateway an mcp section is attached to when the operator creates one.
        namespaces: Namespaces whose ExposedAPIs are registered (empty: all).
        resync_interval: Seconds between full reconciliations (restores the targets after the ConfigMap is reset).
    """

    gateway_namespace: str = "canvas"
    config_map: str = "agentgateway-config"
    config_key: str = "config.yaml"
    pod_selector: str = "app.kubernetes.io/name=agentgateway-standalone"
    mcp_gateway: str = "default"
    namespaces: tuple = ()
    resync_interval: float = 60.0

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        d = cls()
        return cls(
            gateway_namespace=env.get("GATEWAY_NAMESPACE", d.gateway_namespace),
            config_map=env.get("GATEWAY_CONFIG_MAP", d.config_map),
            config_key=env.get("GATEWAY_CONFIG_KEY", d.config_key),
            pod_selector=env.get("GATEWAY_POD_SELECTOR", d.pod_selector),
            mcp_gateway=env.get("GATEWAY_MCP_GATEWAY", d.mcp_gateway),
            namespaces=tuple(n.strip() for n in env.get("WATCHED_NAMESPACES", "").split(",") if n.strip()),
            resync_interval=float(env.get("RESYNC_INTERVAL_SECONDS", d.resync_interval)),
        )
