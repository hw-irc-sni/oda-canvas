"""TMFOP012 Data Resource Management operator.

Handles the DataResourceConfig resources (``oda.tmforum.org/v1alpha1``) whose ``spec.provider`` is ``odaComponent``
(ADR-010 §7): data resources served by an ODA Component in the Canvas, such as the TMFC060 Knowledge Management
knowledge-bases.

- **Provider role** (``role: provider``, rendered by the provider component's chart): a daemon per resource mirrors
  the knowledge-bases and ontology modules the component serves into ``status.providedResources`` (#63). It polls
  every ``POLL_INTERVAL_SECONDS`` and is woken early by the provider's hub events, received on the HTTP listener.
- **Consumer role** (default): a daemon per resource ensures the knowledge-base on the provider, grants the consumer
  component the provider's client roles in Keycloak, and writes a binding Secret and a DependentAPI (#64). On delete
  it removes them and revokes the roles; the knowledge-base is kept.

DataResourceConfigs of any other provider (for example ``databricks``, handled by its own operator) are never
touched: no status, finalizer or annotation is written to them (ADR-010 §8). Every handler is filtered on
``spec.provider``, and kopf keeps its state under its own annotation prefix and finalizer.

Run: ``kopf run --all-namespaces --standalone dataResourceOperator.py`` (see the Dockerfile).
"""

import asyncio
import logging
import os

import aiohttp
import kopf
from aiohttp import web

from common import component_of, is_owned, is_owned_consumer, is_owned_provider, role_of
from consumer import ConsumerBinder
from keycloak import CredentialsMissing, KeycloakAdmin, KeycloakError, TokenManager
from kube import Kube
from log_wrapper import LogWrapper
from mirror import ProviderMirror
from settings import DRC_GROUP, DRC_PLURAL, DRC_VERSION, FINALIZER, KOPF_PREFIX, Settings

logging_level = int(os.environ.get("LOGGING", logging.INFO))
logger = logging.getLogger("dataresource-operator")
logger.setLevel(logging_level)
LogWrapper.set_defaultLogger(logger)

SETTINGS = Settings.from_env()

CICD_BUILD_TIME = os.getenv("CICD_BUILD_TIME")
GIT_COMMIT_SHA = os.getenv("GIT_COMMIT_SHA")

# Seconds a provider daemon waits after a hub event before polling, so a burst of events gives one poll
EVENT_COALESCE_SECONDS = 2.0

_session: aiohttp.ClientSession | None = None
_listener: web.AppRunner | None = None
_kube: Kube | None = None

# Per provider DataResourceConfig (namespace, name): set by the listener to wake its daemon
TRIGGERS: dict[tuple[str, str], asyncio.Event] = {}


def http_session() -> aiohttp.ClientSession:
    """The operator's shared HTTP session, created on first use (inside the kopf event loop)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=SETTINGS.http_timeout))
    return _session


def kube() -> Kube:
    global _kube
    if _kube is None:
        _kube = Kube.from_cluster(SETTINGS.componentname_label)
    return _kube


TOKENS = TokenManager(SETTINGS.oidc_dir, http_session)
ADMIN = KeycloakAdmin(SETTINGS.keycloak_url, SETTINGS.keycloak_realm, TOKENS, http_session)


def mirror_for(namespace: str, name: str) -> ProviderMirror:
    return ProviderMirror(namespace, name, SETTINGS, kube(), TOKENS, ADMIN, http_session)


def binder_for(namespace: str, name: str) -> ConsumerBinder:
    return ConsumerBinder(namespace, name, SETTINGS, kube(), TOKENS, ADMIN, http_session)


# --- hub event listener ------------------------------------------------------------------------------------------


async def on_event(request: web.Request) -> web.Response:
    """``POST /providers/{namespace}/{name}/listener/{event}``: wake that provider's daemon.

    The payload is never read: an event only makes the daemon poll the provider API earlier, so the unauthenticated
    endpoint can at worst cause an extra poll.
    """
    trigger = TRIGGERS.get((request.match_info["namespace"], request.match_info["name"]))
    if trigger is None:
        return web.Response(status=404)
    trigger.set()
    return web.Response(status=204)


def listener_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_post("/providers/{namespace}/{name}/listener/{event}", on_event)
    return app


# --- kopf --------------------------------------------------------------------------------------------------------


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    """Own annotation prefix and finalizer, standalone, the hub event listener, and a check that the Keycloak
    client credentials work (a missing Secret is only a warning: the setup Job may still be creating it)."""
    global _listener
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
              f"realm {SETTINGS.keycloak_realm}; poll {SETTINGS.poll_interval:.0f} s; "
              f"callback base {SETTINGS.callback_base or '- (polling only)'}")
    _listener = web.AppRunner(listener_app(), access_log=None)
    await _listener.setup()
    await web.TCPSite(_listener, "0.0.0.0", SETTINGS.listener_port).start()
    logw.info("hub event listener", f"port {SETTINGS.listener_port}")
    try:
        await TOKENS.token()
        logw.info("Keycloak client credentials", "token obtained")
    except CredentialsMissing as e:
        logw.warning("Keycloak client credentials not mounted yet", str(e))
    except KeycloakError as e:
        logw.warning("Keycloak client credentials not usable yet", str(e))


@kopf.on.cleanup()
async def cleanup(**_):
    if _listener is not None:
        await _listener.cleanup()
    if _session is not None and not _session.closed:
        await _session.close()


@kopf.on.probe(id="ready")
async def ready_probe(**_):
    return True


@kopf.on.resume(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
@kopf.on.create(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
@kopf.on.update(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, field="spec", retries=5)
async def reconcile(spec, meta, name, namespace, reason=None, **_):
    """Create, update (of the spec) or resume of an owned DataResourceConfig: log it and wake its daemon, which owns
    the status (``provider_daemon`` or ``consumer_daemon``).

    Args:
        spec: The DataResourceConfig spec.
        meta: Its metadata.
        name: Its name.
        namespace: Its namespace.
        reason: The kopf cause (create, update, resume).
    """
    logw = LogWrapper(handler_name="reconcile", function_name="reconcile",
                      resource_name=f"DataResourceConfig/{namespace}/{name}",
                      component_name=component_of(meta, SETTINGS))
    role = role_of(spec)
    items = spec.get("providedDataResources") if role == "provider" else spec.get("dependentDataResources")
    logw.info(f"handling {reason or 'event'} of a {role} DataResourceConfig",
              f"{len(items or [])} item(s): {', '.join(i.get('name', '?') for i in items or [])}")
    trigger = TRIGGERS.get((namespace, name))
    if trigger is not None:
        trigger.set()          # a changed spec: act now


@kopf.on.delete(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned, retries=5)
async def delete(spec, meta, body, name, namespace, **_):
    """Deletion of an owned DataResourceConfig, best effort (a provider uninstalled at the same time must not pin the
    finalizer). Provider: remove the hub subscription and the canvasSystemRole grant. Consumer: remove the binding
    Secrets and DependentAPIs and revoke the granted roles; the knowledge-bases are kept."""
    logw = LogWrapper(handler_name="delete", function_name="delete",
                      resource_name=f"DataResourceConfig/{namespace}/{name}",
                      component_name=component_of(meta, SETTINGS))
    role = role_of(spec)
    if role == "provider":
        problems = await mirror_for(namespace, name).cleanup(dict(spec), dict(meta))
    else:
        problems = await binder_for(namespace, name).cleanup(dict(body))
    for p in problems:
        logw.warning(f"{role} cleanup incomplete", p)
    logw.info(f"{role} DataResourceConfig deleted", "cleaned up" if not problems else f"{len(problems)} issue(s)")


@kopf.daemon(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned_provider, cancellation_timeout=10)
async def provider_daemon(stopped, name, namespace, body, **_):
    """Mirror one provider: poll, then wait for a hub event, a spec change or the poll interval."""
    key = (namespace, name)
    trigger = TRIGGERS.setdefault(key, asyncio.Event())
    mirror = mirror_for(namespace, name)
    try:
        while not stopped:
            trigger.clear()
            try:
                await mirror.poll(dict(body.get("spec") or {}), dict(body.get("metadata") or {}),
                                  dict(body.get("status") or {}))
            except Exception as e:  # never let one bad poll kill the daemon
                mirror.logw.exception("provider poll failed", e)
            await wait_for_trigger(trigger, stopped, SETTINGS.poll_interval)
    finally:
        if TRIGGERS.get(key) is trigger:
            del TRIGGERS[key]


@kopf.daemon(DRC_GROUP, DRC_VERSION, DRC_PLURAL, when=is_owned_consumer, cancellation_timeout=10)
async def consumer_daemon(stopped, name, namespace, body, **_):
    """Bind one consumer: reconcile, then wait for a spec change, or the poll interval (RETRY_INTERVAL_SECONDS while
    a required item is not bound), so a reinstalled provider or a lost role is repaired."""
    key = (namespace, name)
    trigger = TRIGGERS.setdefault(key, asyncio.Event())
    binder = binder_for(namespace, name)
    try:
        while not stopped:
            trigger.clear()
            ready = False
            try:
                drc = {k: body.get(k) for k in ("apiVersion", "kind", "metadata", "spec", "status")}
                patch = await binder.reconcile(drc)
                st = patch or (body.get("status") or {})
                ready = st.get("phase") == "Ready"
            except Exception as e:  # never let one bad pass kill the daemon
                binder.logw.exception("consumer reconcile failed", e)
            await wait_for_trigger(trigger, stopped, SETTINGS.poll_interval if ready else SETTINGS.retry_interval)
    finally:
        if TRIGGERS.get(key) is trigger:
            del TRIGGERS[key]


async def wait_for_trigger(trigger: asyncio.Event, stopped, interval: float) -> None:
    """Return after ``interval`` seconds, or soon after the trigger is set or the daemon is stopped."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + interval
    while not stopped and loop.time() < deadline:
        try:
            await asyncio.wait_for(trigger.wait(), timeout=min(1.0, deadline - loop.time()))
        except asyncio.TimeoutError:
            continue
        await asyncio.sleep(EVENT_COALESCE_SECONDS)
        return
