"""Reading and writing the agentgateway config ConfigMap, and the full reconciliation of the operator's targets.

The ConfigMap is also changed by the Canvas Portal (its MCP editor) and reset by `helm upgrade` of agentgateway.
Writes carry the resourceVersion read, so a concurrent change gives a 409 and the merge is redone on the new
content. After a write the gateway pods are nudged (an annotation), so the kubelet refreshes the mounted file now
rather than within a minute, as the portal does.
"""

import asyncio
import time

import yaml

from log_wrapper import LogWrapper
from settings import Settings
from targets import desired_targets, merge

NUDGE_ANNOTATION = "agentgateway-mcp.oda.tmforum.org/config-changed"


class ConfigConflict(Exception):
    pass


def render(document: dict) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)


class Gateway:
    """The Kubernetes calls (sync clients, run in threads); ``core`` is a CoreV1Api, ``custom`` a CustomObjectsApi."""

    def __init__(self, settings: Settings, core, custom):
        self.settings, self.core, self.custom = settings, core, custom
        self.logw = LogWrapper(handler_name="reconcile", function_name="Gateway")

    async def list_exposed_apis(self) -> list[dict]:
        found = await asyncio.to_thread(self.custom.list_cluster_custom_object, "oda.tmforum.org", "v1", "exposedapis")
        return found.get("items", [])

    async def read(self):
        cm = await asyncio.to_thread(self.core.read_namespaced_config_map, self.settings.config_map,
                                     self.settings.gateway_namespace)
        text = (cm.data or {}).get(self.settings.config_key, "")
        return (yaml.safe_load(text) or {}), text, cm.metadata.resource_version

    async def write(self, text: str, resource_version: str) -> None:
        from kubernetes.client.exceptions import ApiException

        body = {"metadata": {"resourceVersion": resource_version}, "data": {self.settings.config_key: text}}
        try:
            await asyncio.to_thread(self.core.patch_namespaced_config_map, self.settings.config_map,
                                    self.settings.gateway_namespace, body)
        except ApiException as e:
            if e.status == 409:
                raise ConfigConflict() from None
            raise

    async def nudge(self) -> int:
        """Annotate the gateway pods so their mounted config is refreshed now; returns how many were nudged."""
        from kubernetes.client.exceptions import ApiException

        patch = {"metadata": {"annotations": {NUDGE_ANNOTATION: str(time.time_ns())}}}
        try:
            pods = await asyncio.to_thread(self.core.list_namespaced_pod, self.settings.gateway_namespace,
                                           label_selector=self.settings.pod_selector)
            for pod in pods.items:
                await asyncio.to_thread(self.core.patch_namespaced_pod, pod.metadata.name,
                                        self.settings.gateway_namespace, patch)
            return len(pods.items)
        except ApiException as e:
            self.logw.warning("gateway pods not nudged", f"HTTP {e.status}")
            return 0

    async def reconcile(self, attempts: int = 5) -> bool:
        """Make the gateway's oda-* targets match the ready MCP ExposedAPIs; True if the config was changed."""
        desired = desired_targets(await self.list_exposed_apis(), self.settings.namespaces)
        for attempt in range(1, attempts + 1):
            document, text, version = await self.read()
            new_text = render(merge(document, desired, self.settings.mcp_gateway))
            if yaml.safe_load(new_text) == (yaml.safe_load(text) or {}):
                return False
            try:
                await self.write(new_text, version)
            except ConfigConflict:
                self.logw.info("gateway config changed concurrently", f"retrying ({attempt}/{attempts})")
                continue
            names = ", ".join(t["name"] for t in desired) or "none"
            self.logw.info("gateway MCP targets updated", f"operator targets: {names}")
            await self.nudge()
            return True
        raise ConfigConflict(f"the gateway config kept changing ({attempts} attempts)")
