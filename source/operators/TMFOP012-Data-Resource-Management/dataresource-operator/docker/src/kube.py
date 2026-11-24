"""The Kubernetes reads and writes TMFOP012 makes outside kopf's own patching (the provider daemon runs in a loop,
where kopf's ``patch`` is not applied). The kubernetes client is synchronous: every call runs in a thread."""

import asyncio
import json

from settings import DRC_GROUP, DRC_PLURAL, DRC_VERSION

ODA_GROUP = "oda.tmforum.org"
ODA_VERSION = "v1"
HUB_ANNOTATION = "dataresource.oda.tmforum.org/hub"


class Kube:
    """Wraps ``kubernetes.client.CustomObjectsApi`` (injected, so tests can pass a fake)."""

    def __init__(self, custom_api, componentname_label: str = "oda.tmforum.org/componentName"):
        self.api = custom_api
        self.label = componentname_label

    @classmethod
    def from_cluster(cls, componentname_label: str) -> "Kube":
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        return cls(client.CustomObjectsApi(), componentname_label)

    async def get_component(self, namespace: str, name: str) -> dict | None:
        from kubernetes.client.exceptions import ApiException

        try:
            return await asyncio.to_thread(self.api.get_namespaced_custom_object, ODA_GROUP, ODA_VERSION, namespace,
                                           "components", name)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    async def list_exposed_apis(self, namespace: str, component: str) -> list[dict]:
        found = await asyncio.to_thread(self.api.list_namespaced_custom_object, ODA_GROUP, ODA_VERSION, namespace,
                                        "exposedapis", label_selector=f"{self.label}={component}")
        return found.get("items", [])

    async def patch_status(self, namespace: str, name: str, status: dict) -> None:
        """Merge-patch ``status`` (lists such as providedResources are replaced as a whole)."""
        await asyncio.to_thread(self.api.patch_namespaced_custom_object_status, DRC_GROUP, DRC_VERSION, namespace,
                                DRC_PLURAL, name, {"status": status})

    async def set_hub(self, namespace: str, name: str, hub: dict | None) -> None:
        """Record (or with None remove) the provider hub subscription in the DataResourceConfig annotation."""
        value = json.dumps(hub, sort_keys=True) if hub else None
        await asyncio.to_thread(self.api.patch_namespaced_custom_object, DRC_GROUP, DRC_VERSION, namespace,
                                DRC_PLURAL, name, {"metadata": {"annotations": {HUB_ANNOTATION: value}}})


def hub_of(meta) -> dict | None:
    """The hub subscription recorded on the DataResourceConfig, or None."""
    raw = ((meta or {}).get("annotations") or {}).get(HUB_ANNOTATION)
    try:
        hub = json.loads(raw) if raw else None
    except ValueError:
        return None
    return hub if isinstance(hub, dict) and hub.get("id") else None
