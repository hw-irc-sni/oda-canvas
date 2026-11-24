"""The Kubernetes reads and writes TMFOP012 makes outside kopf's own patching (the provider daemon runs in a loop,
where kopf's ``patch`` is not applied). The kubernetes client is synchronous: every call runs in a thread."""

import asyncio
import json

from settings import DRC_GROUP, DRC_PLURAL, DRC_VERSION

ODA_GROUP = "oda.tmforum.org"
ODA_VERSION = "v1"
HUB_ANNOTATION = "dataresource.oda.tmforum.org/hub"
FIELD_MANAGER = "dataresource-operator"


class Kube:
    """Wraps ``kubernetes.client.CustomObjectsApi`` (injected, so tests can pass a fake)."""

    def __init__(self, custom_api, componentname_label: str = "oda.tmforum.org/componentName", core_api=None):
        self.api = custom_api
        self.label = componentname_label
        self._core = core_api

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

    async def list_components(self, namespace: str) -> list[dict]:
        found = await asyncio.to_thread(self.api.list_namespaced_custom_object, ODA_GROUP, ODA_VERSION, namespace,
                                        "components")
        return found.get("items", [])

    async def list_drcs(self) -> list[dict]:
        """Every DataResourceConfig in the cluster."""
        found = await asyncio.to_thread(self.api.list_cluster_custom_object, DRC_GROUP, DRC_VERSION, DRC_PLURAL)
        return found.get("items", [])

    async def apply(self, body: dict) -> None:
        """Server-side apply of a binding Secret or DependentAPI (create or update, without reading it first)."""
        meta = body["metadata"]
        if body["kind"] == "Secret":
            await asyncio.to_thread(self.core().patch_namespaced_secret, meta["name"], meta["namespace"], body,
                                    field_manager=FIELD_MANAGER, force=True,
                                    _content_type="application/apply-patch+yaml")
        else:
            group, version = body["apiVersion"].split("/")
            await asyncio.to_thread(self.api.patch_namespaced_custom_object, group, version, meta["namespace"],
                                    "dependentapis", meta["name"], body, field_manager=FIELD_MANAGER, force=True,
                                    _content_type="application/apply-patch+yaml")

    async def patch_dependentapi_status(self, namespace: str, name: str, status: dict) -> None:
        """Merge-patch the DependentAPI status. The DependentAPI CRD has no status subresource, so the status is
        part of the object (as TMFOP005 writes it)."""
        await asyncio.to_thread(self.api.patch_namespaced_custom_object, ODA_GROUP, ODA_VERSION, namespace,
                                "dependentapis", name, {"status": status})

    async def delete(self, kind: str, namespace: str, name: str) -> bool:
        """Delete a binding Secret or DependentAPI; False if it was already gone."""
        from kubernetes.client.exceptions import ApiException

        try:
            if kind == "Secret":
                await asyncio.to_thread(self.core().delete_namespaced_secret, name, namespace)
            else:
                await asyncio.to_thread(self.api.delete_namespaced_custom_object, ODA_GROUP, ODA_VERSION, namespace,
                                        "dependentapis", name)
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    def core(self):
        if self._core is None:
            from kubernetes import client

            self._core = client.CoreV1Api(self.api.api_client)
        return self._core

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
