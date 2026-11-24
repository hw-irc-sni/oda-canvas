"""TMFOP012 settings, read from the environment (set by charts/dataresource-operator)."""

import os
from dataclasses import dataclass

# The custom resource this operator handles
DRC_GROUP = "oda.tmforum.org"
DRC_VERSION = "v1alpha1"
DRC_PLURAL = "dataresourceconfigs"

# The spec.provider value this operator owns (ADR-010 §7). DataResourceConfigs of any other provider are never
# written to, status included (ADR-010 §8).
OWNED_PROVIDER = "odaComponent"

# kopf keeps its handler progress and diff base in annotations with this prefix, and uses this finalizer, so it never
# collides with another kopf operator watching the same resources (the Databricks data operator).
KOPF_PREFIX = "dataresource.oda.tmforum.org"
FINALIZER = f"{KOPF_PREFIX}/finalizer"


@dataclass(frozen=True)
class Settings:
    """Operator configuration.

    Attributes:
        keycloak_url: Keycloak base URL, ending in /auth. It must be the host the provider components expect in the
            token issuer (Keycloak sets ``iss`` from the request host), by default the in-cluster headless Service.
        keycloak_realm: The Canvas realm.
        oidc_dir: Directory of the mounted ``dataresource-operator-oidc-secret`` (``client_id``, ``client_secret``,
            ``token_url``), written by the chart's setup Job. Read on every token fetch, so a Secret created after
            the operator started is picked up.
        poll_interval: Seconds between polls of a provider component (role provider).
        component_namespace: Default namespace of provider components (``providerRef.namespace`` overrides it).
        componentname_label: Label linking a resource to its ODA Component.
        listener_port: Port of the HTTP listener for provider hub events.
        callback_base: The listener's in-cluster base URL, registered on provider hubs; empty: polling only.
    """

    keycloak_url: str = "http://canvas-keycloak-headless.canvas.svc.cluster.local:8083/auth"
    keycloak_realm: str = "odari"
    oidc_dir: str = "/etc/dataresource-operator/oidc"
    poll_interval: float = 60.0
    component_namespace: str = "components"
    componentname_label: str = "oda.tmforum.org/componentName"
    http_timeout: float = 30.0
    # The HTTP listener for provider hub events, and its in-cluster base URL as registered on provider hubs (empty:
    # no hub registration, polling only)
    listener_port: int = 8081
    callback_base: str = ""

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        d = cls()
        return cls(
            keycloak_url=env.get("KEYCLOAK_URL", d.keycloak_url).rstrip("/"),
            keycloak_realm=env.get("KEYCLOAK_REALM", d.keycloak_realm),
            oidc_dir=env.get("OIDC_DIR", d.oidc_dir),
            poll_interval=float(env.get("POLL_INTERVAL_SECONDS", d.poll_interval)),
            component_namespace=env.get("COMPONENT_NAMESPACE", d.component_namespace),
            componentname_label=env.get("COMPONENTNAME_LABEL", d.componentname_label),
            http_timeout=float(env.get("HTTP_TIMEOUT_SECONDS", d.http_timeout)),
            listener_port=int(env.get("LISTENER_PORT", d.listener_port)),
            callback_base=env.get("CALLBACK_BASE", d.callback_base).rstrip("/"),
        )
