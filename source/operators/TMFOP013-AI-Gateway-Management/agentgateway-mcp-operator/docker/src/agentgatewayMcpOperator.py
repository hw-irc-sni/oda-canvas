"""TMFOP013 AI Gateway Management: registers the MCP ExposedAPIs of ODA Components with agentgateway.

Each ExposedAPI with ``apiType: mcp`` whose implementation is ready becomes an agentgateway MCP target named
``oda-<namespace>-<exposedapi>``, pointing at the ExposedAPI's implementation Service, with the caller's token
forwarded (backendAuth passthrough). Deleting the ExposedAPI (for example uninstalling the component) removes it.
The operator changes only the targets with that prefix; targets added by hand or in the Canvas Portal are kept.

Every ExposedAPI event (an event handler only: nothing is written to the ExposedAPIs) and a periodic resync (RESYNC_INTERVAL_SECONDS) reconcile all targets at once, so a reset of
the ConfigMap (``helm upgrade`` of agentgateway) is repaired.

Run: ``kopf run --all-namespaces --standalone agentgatewayMcpOperator.py`` (see the Dockerfile).
"""

import asyncio
import logging
import os

import kopf

from gateway import Gateway
from log_wrapper import LogWrapper
from settings import KOPF_PREFIX, Settings
from targets import is_mcp

logging_level = int(os.environ.get("LOGGING", logging.INFO))
logger = logging.getLogger("agentgateway-mcp-operator")
logger.setLevel(logging_level)
LogWrapper.set_defaultLogger(logger)

SETTINGS = Settings.from_env()
# Seconds between an ExposedAPI event and the reconciliation, so a component's install gives one write
DEBOUNCE_SECONDS = 2.0

_gateway: Gateway | None = None
_wake: asyncio.Event | None = None
_task: asyncio.Task | None = None


def gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        _gateway = Gateway(SETTINGS, client.CoreV1Api(), client.CustomObjectsApi())
    return _gateway


def request_reconcile() -> None:
    if _wake is not None:
        _wake.set()


async def reconcile_loop(gw_factory=gateway, interval: float | None = None) -> None:
    """Reconcile now, then on every request (debounced) or every resync interval."""
    interval = SETTINGS.resync_interval if interval is None else interval
    logw = LogWrapper(handler_name="reconcile_loop", function_name="reconcile_loop")
    while True:
        _wake.clear()
        try:
            await gw_factory().reconcile()
        except Exception as e:  # keep the loop alive; the next pass retries
            logw.warning("gateway not reconciled", f"{e.__class__.__name__}: {e}")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=interval)
            await asyncio.sleep(DEBOUNCE_SECONDS)
        except asyncio.TimeoutError:
            pass


@kopf.on.startup()
async def configure(settings: kopf.OperatorSettings, **_):
    global _wake, _task
    settings.peering.standalone = True
    settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(prefix=KOPF_PREFIX)
    settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
        prefix=KOPF_PREFIX, key="last-handled-configuration")
    settings.posting.level = logging.WARNING
    LogWrapper(handler_name="configure", function_name="configure").info(
        "TMFOP013 agentgateway-mcp-operator starting",
        f"gateway {SETTINGS.gateway_namespace}/{SETTINGS.config_map}; namespaces "
        f"{','.join(SETTINGS.namespaces) or 'all'}; resync {SETTINGS.resync_interval:.0f} s")
    _wake = asyncio.Event()
    _task = asyncio.create_task(reconcile_loop())


@kopf.on.cleanup()
async def cleanup(**_):
    if _task is not None:
        _task.cancel()


@kopf.on.event("oda.tmforum.org", "v1", "exposedapis", when=is_mcp)
async def exposed_api_event(type, name, namespace, **_):
    """An MCP ExposedAPI was added, changed (its status too) or deleted: reconcile the gateway.

    Only an event handler is used: kopf then writes nothing to the ExposedAPIs (no annotations, no finalizer), which
    belong to the component and the API operators, and a deleted component is never held back.
    """
    if type == "DELETED":
        LogWrapper(handler_name="exposed_api_event", function_name="exposed_api_event",
                   resource_name=f"ExposedAPI/{namespace}/{name}").info("MCP ExposedAPI deleted", "reconciling")
    request_reconcile()
