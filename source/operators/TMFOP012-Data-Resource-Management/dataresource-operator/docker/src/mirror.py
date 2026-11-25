"""One provider DataResourceConfig's mirror: a poll (resolve, grant, list, hub, status) and its cleanup.

``ProviderMirror`` holds the per-resource state of the daemon (the grant done, the hub checked) and is driven by
``dataResourceOperator.provider_daemon``. Everything it needs is injected, so the tests run it against fakes.
"""

from common import changed, component_of, condition
from keycloak import KeycloakAdmin, KeycloakError, TokenManager
from kube import Kube, hub_of
from log_wrapper import LogWrapper
from provider import (ProviderClient, ProviderError, exposed_api_names, provided_resources, resolve_api, summary)
from settings import Settings


def provider_status(previous: dict, generation, resources: list[dict] | None = None,
                    error: ProviderError | None = None, note: str = "") -> dict:
    """The status patch of a poll. On an error the last good ``providedResources`` is kept (not in the patch)."""
    previous = previous or {}
    conditions = previous.get("conditions")
    if error is None:
        message = f"Mirroring {summary(resources or [])}" + (f"; {note}" if note else "")
        return {"phase": "Ready", "message": message, "observedGeneration": generation,
                "providedResources": resources or [], "resourceCount": len(resources or []),
                "conditions": [condition("Ready", "True", "Mirrored", message, generation, conditions)]}
    message = str(error)
    return {"phase": "Error", "message": message, "observedGeneration": generation,
            "conditions": [condition("Ready", "False", error.reason, message, generation, conditions)]}


class ProviderMirror:
    def __init__(self, namespace: str, name: str, settings: Settings, kube: Kube, tokens: TokenManager,
                 admin: KeycloakAdmin, session_factory):
        self.namespace, self.name = namespace, name
        self.settings, self.kube, self.tokens, self.admin = settings, kube, tokens, admin
        self.session = session_factory
        self.granted = False
        self.hub_checked = False
        self.logw = LogWrapper(handler_name="provider_daemon", function_name="ProviderMirror",
                               resource_name=f"DataResourceConfig/{namespace}/{name}")

    @property
    def callback(self) -> str:
        return f"{self.settings.callback_base.rstrip('/')}/providers/{self.namespace}/{self.name}"

    async def clients(self, spec: dict, meta: dict) -> list[ProviderClient]:
        """One client per distinct REST ExposedAPI the provided stores name."""
        component = component_of(meta, self.settings)
        if not component:
            raise ProviderError("ComponentNotFound",
                                f"no {self.settings.componentname_label} label or Component ownerReference")
        self.logw.set(component_name=component)
        comp = await self.kube.get_component(self.namespace, component)
        if comp is None:
            raise ProviderError("ComponentNotFound", f"Component {self.namespace}/{component} not found")
        system_role = ((comp.get("spec") or {}).get("securityFunction") or {}).get("canvasSystemRole")
        if not system_role:
            raise ProviderError("AccessDenied", f"Component {component} declares no securityFunction.canvasSystemRole")
        names = exposed_api_names(spec)
        if not names:
            raise ProviderError("ExposedAPINotFound", "no providedDataResources item names an exposedAPI")
        exposed = await self.kube.list_exposed_apis(self.namespace, component)
        return [ProviderClient(resolve_api(n, component, self.namespace, exposed, self.settings.componentname_label),
                               component, system_role, self.tokens, self.admin, self.session,
                               self.settings.oidc_dir) for n in names]

    async def poll(self, spec: dict, meta: dict, status: dict) -> dict | None:
        """Poll the provider and write the status if it changed; returns the patch written (or None)."""
        note = ""
        try:
            clients = await self.clients(spec, meta)
            if not self.granted:
                if await clients[0].grant():
                    self.logw.info("granted", f"{clients[0].system_role} on {clients[0].provider_client}")
                self.granted = True
            resources, seen = [], set()
            for client in clients:
                for item in provided_resources(client.api, await client.list("knowledge"),
                                               await client.list("knowledgeSpecification")):
                    if (item["kind"], item["id"]) not in seen:
                        seen.add((item["kind"], item["id"]))
                        resources.append(item)
            note = await self.ensure_hub(clients[0], meta)
            patch = provider_status(status, meta.get("generation"), resources, note=note)
        except ProviderError as e:
            self.granted = self.granted and e.reason not in ("AccessDenied", "ComponentNotFound")
            patch = provider_status(status, meta.get("generation"), error=e)
            if changed(status, patch):
                self.logw.warning(f"provider not mirrored ({e.reason})", str(e))
        if not changed(status, patch):
            return None
        await self.kube.patch_status(self.namespace, self.name, patch)
        if patch["phase"] == "Ready":
            self.logw.info("status written", patch["message"])
        return patch

    async def ensure_hub(self, client: ProviderClient, meta: dict) -> str:
        """Keep one hub subscription for this resource; returns a note for the status message if there is none."""
        if not self.settings.callback_base:
            return "no change events (no callback base configured)"
        if self.hub_checked:
            return ""
        hub = hub_of(meta)
        try:
            if hub and hub.get("callback") == self.callback and await client.hub_get(hub["id"]):
                self.hub_checked = True
                return ""
            if hub:
                await client.hub_delete(hub["id"])
            created = await client.hub_register(self.callback)
            await self.kube.set_hub(self.namespace, self.name, {"id": created["id"], "callback": self.callback})
            self.logw.info("registered on the provider hub", f"{created['id']} -> {self.callback}")
            self.hub_checked = True
            return ""
        except ProviderError as e:
            self.logw.warning("no hub subscription, polling only", str(e))
            return f"no change events ({e})"

    async def cleanup(self, spec: dict, meta: dict) -> list[str]:
        """On delete: remove the hub subscription and the canvasSystemRole grant. Best effort: failures are logged
        and returned, never raised, so the finalizer never pins the resource."""
        problems = []
        try:
            clients = await self.clients(spec, meta)
        except (ProviderError, KeycloakError) as e:
            return [f"provider not resolved, nothing to clean up: {e}"]
        except Exception as e:  # the Kubernetes API, for example
            return [f"provider not resolved: {e.__class__.__name__}: {e}"]
        hub = hub_of(meta)
        if hub:
            try:
                await clients[0].hub_delete(hub["id"])
            except (ProviderError, KeycloakError) as e:
                problems.append(f"hub {hub['id']} not deleted: {e}")
        try:
            from consumer import system_role_needed

            drcs = await self.kube.list_drcs()
            if system_role_needed(drcs, clients[0].provider_client, self.settings, exclude=(self.namespace, self.name)):
                self.logw.info("canvasSystemRole kept", "consumer bindings to this provider remain")
            elif await clients[0].revoke():
                self.logw.info("revoked", f"{clients[0].system_role} on {clients[0].provider_client}")
        except (ProviderError, KeycloakError) as e:
            problems.append(f"grant not revoked: {e}")
        except Exception as e:  # the Kubernetes API, for example
            problems.append(f"grant not revoked: {e.__class__.__name__}: {e}")
        return problems
