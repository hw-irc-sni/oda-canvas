"""TMFOP012 Data Resource Management operator.

Handles the DataResourceConfig resources (``oda.tmforum.org/v1alpha1``) whose ``spec.provider`` is ``odaComponent``
(ADR-010 §7): data resources served by an ODA Component in the Canvas, such as the TMFC060 Knowledge Management
knowledge-bases.

- **Provider role** (``role: provider``, rendered by the provider component's chart): mirror the knowledge-bases and
  ontology modules the component serves into ``status.providedResources`` (hw-irc-sni/oda-canvas#63).
- **Consumer role** (default): ensure the knowledge-base on the provider, grant the consumer component the provider's
  client roles in Keycloak, and write a binding Secret and a DependentAPI (hw-irc-sni/oda-canvas#64).

DataResourceConfigs of any other provider (for example ``databricks``, handled by its own operator) are never
touched: no status, finalizer or annotation is written to them (ADR-010 §8). Every handler is filtered on
``spec.provider``, and kopf keeps its state under its own annotation prefix and finalizer.

The handlers in this version are scaffold stubs: they log the resource and set ``status.phase: Pending`` with a
``Ready`` condition ``Unknown`` (reason ``NotImplemented``).

Run: ``kopf run --all-namespaces --standalone dataResourceOperator.py`` (see the Dockerfile).
"""

import datetime
import logging
import os

import aiohttp
import kopf

from keycloak import CredentialsMissing, KeycloakError, TokenManager
from log_wrapper import LogWrapper
from settings import DRC_GROUP, DRC_PLURAL, DRC_VERSION, FINALIZER, KOPF_PREFIX, OWNED_PROVIDER, Settings

logging_level = int(os.environ.get("LOGGING", logging.INFO))
logger = logging.getLogger("dataresource-operator")
logger.setLevel(logging_level)
LogWrapper.set_defaultLogger(logger)

SETTINGS = Settings.from_env()

CICD_BUILD_TIME = os.getenv("CICD_BUILD_TIME")
GIT_COMMIT_SHA = os.getenv("GIT_COMMIT_SHA")

_session: aiohttp.ClientSession | None = None


def http_session() -> aiohttp.ClientSession:
    """The operator's shared HTTP session, created on first use (inside the kopf event loop)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=SETTINGS.http_timeout))
    return _session


TOKENS = TokenManager(SETTINGS.oidc_dir, http_session)


# --- filters -----------------------------------------------------------------------------------------------------


def is_owned(spec, **_) -> bool:
    """True for the DataResourceConfigs this operator owns (``spec.provider: odaComponent``)."""
    return (spec or {}).get("provider") == OWNED_PROVIDER


def role_of(spec) -> str:
    return (spec or {}).get("role") or "consumer"


def is_owned_provider(spec, **_) -> bool:
    """True for an owned DataResourceConfig with ``role: provider``."""
    return is_owned(spec) and role_of(spec) == "provider"


def component_of(meta, settings: Settings = SETTINGS) -> str | None:
    """The owning ODA Component: the componentName label, else a Component ownerReference."""
    labels = (meta or {}).get("labels") or {}
    if labels.get(settings.componentname_label):
        return labels[settings.componentname_label]
    for ref in (meta or {}).get("ownerReferences") or []:
        if ref.get("kind") == "Component":
            return ref.get("name")
    return None


# --- status ------------------------------------------------------------------------------------------------------


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def condition(ctype: str, status: str, reason: str, message: str, generation, previous=None) -> dict:
    """A Kubernetes condition; ``lastTransitionTime`` is kept when the status did not change."""
    for c in previous or []:
        if c.get("type") == ctype and c.get("status") == status and c.get("lastTransitionTime"):
            transition = c["lastTransitionTime"]
            break
    else:
        transition = now()
    return {"type": ctype, "status": status, "reason": reason, "message": message,
            "lastTransitionTime": transition, "observedGeneration": generation}


def scaffold_status(spec, meta, status) -> dict:
    """The status the scaffold handlers write: Pending, Ready Unknown (NotImplemented)."""
    generation = (meta or {}).get("generation")
    role = role_of(spec)
    message = f"TMFOP012 received this {role} DataResourceConfig; the {role} role is not implemented yet"
    return {
        "phase": "Pending",
        "message": message,
        "observedGeneration": generation,
        "conditions": [condition("Ready", "Unknown", "NotImplemented", message, generation,
                                 (status or {}).get("conditions"))],
    }


# --- kopf --------------------------------------------------------------------------------------------------------


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    """Own annotation prefix and finalizer, standalone, and a check that the Keycloak client credentials work.

    A missing credentials Secret is only a warning: the chart's setup Job may still be creating it, and the Secret
    is read again on every token fetch.
    """
    settings.peering.standalone = True
    settings.persistence.finalizer = FINALIZER
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(prefix=KOPF_PREFIX)
    settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
        prefix=KOPF_PREFIX, key="last-handled-configuration")
    settings.posting.level = logging.WARNING
    settings.watching.server_timeout = 1 * 60
    settings.watching.connect_timeout = 1 * 60

    logw = LogWrapper(handler_name="configure", function_name="configure")
    logw.info("TMFOP012 dataresource-operator starting",
              f"build {CICD_BUILD_TIME or '-'} {GIT_COMMIT_SHA or '-'}; keycloak {SETTINGS.keycloak_url} "
              f"realm {SETTINGS.keycloak_realm}; poll {SETTINGS.poll_interval:.0f} s")
    try:
        await TOKENS.token()
        logw.info("Keycloak client credentials", "token obtained")
    except CredentialsMissing as e:
        logw.warning("Keycloak client credentials not mounted yet", str(e))
    except KeycloakError as e:
        logw.warning("Keycloak client credentials not usable yet", str(e))


@kopf.on.cleanup()
async def cleanup(**_):
    if _session is not None and not _session.closed:
        await _session.close()


@kopf.on.probe(id="ready")
async def ready_probe(**_):
    return True


@kopf.on.resume(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
@kopf.on.create(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
@kopf.on.update(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
async def reconcile(spec, meta, status, name, namespace, patch, reason=None, **_):
    """Create, update or resume of an owned DataResourceConfig (scaffold: log and set the Pending status).

    Args:
        spec: The DataResourceConfig spec.
        meta: Its metadata.
        status: Its current status.
        name: Its name.
        namespace: Its namespace.
        patch: The kopf patch the status is written to.
        reason: The kopf cause (create, update, resume).
    """
    logw = LogWrapper(handler_name="reconcile", function_name="reconcile",
                      resource_name=f"DataResourceConfig/{namespace}/{name}", component_name=component_of(meta))
    items = spec.get("providedDataResources") if role_of(spec) == "provider" else spec.get("dependentDataResources")
    logw.info(f"handling {reason or 'event'} of a {role_of(spec)} DataResourceConfig",
              f"{len(items or [])} item(s): {', '.join(i.get('name', '?') for i in items or [])}")
    for key, value in scaffold_status(spec, meta, status).items():
        patch.status[key] = value


@kopf.on.delete(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
async def delete(spec, meta, name, namespace, **_):
    """Deletion of an owned DataResourceConfig (scaffold: log; #64 revokes the granted roles here)."""
    logw = LogWrapper(handler_name="delete", function_name="delete",
                      resource_name=f"DataResourceConfig/{namespace}/{name}", component_name=component_of(meta))
    logw.info(f"handling delete of a {role_of(spec)} DataResourceConfig", "nothing to revoke yet")


@kopf.timer(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned_provider, interval=SETTINGS.poll_interval,
            initial_delay=SETTINGS.poll_interval)
async def poll_provider(spec, meta, name, namespace, **_):
    """Periodic poll of a provider component (scaffold: log; #63 mirrors its knowledge-bases and modules)."""
    logw = LogWrapper(handler_name="poll_provider", function_name="poll_provider",
                      resource_name=f"DataResourceConfig/{namespace}/{name}", component_name=component_of(meta))
    logw.debug("provider poll", "not implemented yet")
