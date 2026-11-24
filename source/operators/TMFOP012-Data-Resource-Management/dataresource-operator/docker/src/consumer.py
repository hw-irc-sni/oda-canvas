"""Consumer role (hw-irc-sni/oda-canvas#64, ADR-010 §7): bind a consumer component to a knowledge-base served by a
provider component.

For each ``dependentDataResources[]`` item of a consumer DataResourceConfig (``provider: odaComponent``):

1. **ProviderResolved:** the provider component (``providerRef.componentName``, or the one Component whose
   ``componentMetadata.id`` is ``providerRef.componentId``) with its provider DataResourceConfig, and its REST and MCP
   ExposedAPIs (named by the provided item that matches the dependency's ``type``, else the first).
2. **KnowledgeBaseReady:** ``knowledgeId``, or the knowledge-base named ``create.name`` (reused if it exists, else
   created through the provider API).
3. **AccessGranted:** the provider client roles for the item's ``access`` are mapped to the consumer component's
   service account (``service-account-<consumer>``).
4. The binding Secret ``<drc>-<item>-binding`` and the DependentAPI ``<drc>-<item>`` (``apiType: mcp``,
   ``dependencyKind: data-resource``), both owned by the DataResourceConfig; the DependentAPI status is set here,
   because Dependency Management resolves only ``openapi`` dependencies.

The Secret holds URLs and ids only, never store credentials (Pattern A): the consumer calls the provider with its own
credentials, authorised by the granted roles. Deleting the DataResourceConfig removes the Secret and the DependentAPI,
revokes the roles no other binding of the same consumer still needs, and keeps the knowledge-base.
"""

import base64
from dataclasses import dataclass
from urllib.parse import quote

from common import changed, component_of, condition, role_of
from keycloak import CredentialsMissing, KeycloakAdmin, KeycloakError, TokenManager
from kube import Kube
from log_wrapper import LogWrapper
from provider import ProviderApi, ProviderClient, ProviderError, resolve_api
from settings import DRC_GROUP, DRC_VERSION, OWNED_PROVIDER, Settings

CONDITIONS = ("ProviderResolved", "KnowledgeBaseReady", "AccessGranted")
# Reasons a binding is still on its way (phase Pending) rather than refused (phase Error)
PENDING_REASONS = {"ProviderNotFound", "ExposedAPINotFound", "ExposedAPINotReady", "ProviderUnreachable",
                   "CredentialsMissing", "ConsumerClientNotFound"}


class BindingError(Exception):
    """A binding step failed: ``condition`` is the condition it sets False, ``reason`` its reason."""

    def __init__(self, condition: str, reason: str, message: str):
        super().__init__(message)
        self.condition = condition
        self.reason = reason


# --- pure --------------------------------------------------------------------------------------------------------


def secret_name(drc: str, item: str) -> str:
    return f"{drc}-{item}-binding"


def dependentapi_name(drc: str, item: str) -> str:
    return f"{drc}-{item}"


def owner_reference(drc: dict) -> dict:
    meta = drc["metadata"]
    return {"apiVersion": f"{DRC_GROUP}/{DRC_VERSION}", "kind": "DataResourceConfig", "name": meta["name"],
            "uid": meta["uid"], "controller": True}


def provider_drcs(drcs: list[dict], settings: Settings) -> dict[tuple[str, str], dict]:
    """The live provider DataResourceConfigs by (namespace, provider component)."""
    out = {}
    for d in drcs:
        spec, meta = d.get("spec") or {}, d.get("metadata") or {}
        if spec.get("provider") != OWNED_PROVIDER or role_of(spec) != "provider" or meta.get("deletionTimestamp"):
            continue
        comp = component_of(meta, settings)
        if comp:
            out[(meta.get("namespace"), comp)] = d
    return out


def resolve_provider(ref: dict, components: list[dict], providers: dict, namespace: str) -> tuple[str, dict]:
    """The provider component name and its provider DataResourceConfig.

    Raises:
        BindingError: ProviderResolved False (ProviderNotFound, ProviderAmbiguous).
    """
    if ref.get("componentName"):
        name = ref["componentName"]
        if (namespace, name) not in providers:
            raise BindingError("ProviderResolved", "ProviderNotFound",
                               f"component {namespace}/{name} has no provider DataResourceConfig")
        return name, providers[(namespace, name)]
    wanted = ref.get("componentId")
    names = sorted(c["metadata"]["name"] for c in components
                   if ((c.get("spec") or {}).get("componentMetadata") or {}).get("id") == wanted
                   and (namespace, c["metadata"]["name"]) in providers)
    if not names:
        raise BindingError("ProviderResolved", "ProviderNotFound",
                           f"no {wanted} component with a provider DataResourceConfig in {namespace}")
    if len(names) > 1:
        raise BindingError("ProviderResolved", "ProviderAmbiguous",
                           f"{len(names)} {wanted} components in {namespace} ({', '.join(names)}): "
                           "name one with providerRef.componentName")
    return names[0], providers[(namespace, names[0])]


def provided_item(provider_drc: dict, wanted_type: str | None) -> dict:
    """The provided store matching the dependency's type, else the first one."""
    items = (provider_drc.get("spec") or {}).get("providedDataResources") or []
    if not items:
        raise BindingError("ProviderResolved", "ProviderNotFound", "the provider declares no providedDataResources")
    return next((i for i in items if wanted_type and i.get("type") == wanted_type), items[0])


def knowledge_body(create: dict) -> dict:
    """The POST /knowledge body for ``providerRef.create``."""
    body = {"name": create["name"]}
    for key in ("description", "storeKind"):
        if create.get(key):
            body[key] = create[key]
    if create.get("conformsTo"):
        body["conformsTo"] = [{"iri": c} if ("://" in c or c.startswith("urn:")) else {"id": c}
                              for c in create["conformsTo"]]
    if create.get("source"):
        body["source"] = [{"kind": s} for s in create["source"]]
    return body


def binding_data(rest: ProviderApi, mcp: ProviderApi, kb_id: str, provider: str) -> dict:
    api_url = f"{rest.public_url}/knowledge/{quote(kb_id, safe='')}"
    return {"apiUrl": api_url, "queryUrl": f"{api_url}/query", "mcpUrl": mcp.public_url, "kbId": kb_id,
            "providerComponent": provider, "qdrantCollection": f"tmfc060-kb-{kb_id}"}


def secret_body(drc: dict, item: str, consumer: str | None, data: dict, settings: Settings) -> dict:
    meta = drc["metadata"]
    labels = {"oda.tmforum.org/dataResourceConfig": meta["name"]}
    if consumer:
        labels[settings.componentname_label] = consumer
    return {"apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": secret_name(meta["name"], item), "namespace": meta["namespace"], "labels": labels,
                         "ownerReferences": [owner_reference(drc)]},
            "type": "Opaque",
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}}


def dependentapi_body(drc: dict, item: dict, consumer: str | None, mcp_spec_url: str | None,
                      settings: Settings) -> dict:
    meta = drc["metadata"]
    labels = {"oda.tmforum.org/dataResourceConfig": meta["name"]}
    if consumer:
        labels[settings.componentname_label] = consumer
    spec = {"name": item["name"], "apiType": "mcp", "dependencyKind": "data-resource",
            "required": item.get("required", True)}
    if mcp_spec_url:
        spec["specification"] = {"url": mcp_spec_url}
    return {"apiVersion": "oda.tmforum.org/v1", "kind": "DependentAPI",
            "metadata": {"name": dependentapi_name(meta["name"], item["name"]), "namespace": meta["namespace"],
                         "labels": labels, "ownerReferences": [owner_reference(drc)]},
            "spec": spec}


@dataclass
class ItemResult:
    name: str
    required: bool
    binding: dict | None = None          # the status.bindings entry
    error: BindingError | None = None


def consumer_status(previous: dict, generation, results: list[ItemResult]) -> dict:
    """The status patch: bindings, the three step conditions, Ready (every required item bound), phase."""
    previous = previous or {}
    prev_conditions = previous.get("conditions")
    prev_bindings = {b.get("name"): b for b in previous.get("bindings") or []}
    bindings = []
    for r in results:
        if r.binding is not None:
            bindings.append(r.binding)
        elif r.name in prev_bindings:            # keep what was bound, marked not ready
            bindings.append({**prev_bindings[r.name], "ready": False})
    conditions = []
    for ctype in CONDITIONS:
        failed = [r for r in results if r.error is not None and r.error.condition == ctype]
        if failed:
            e = failed[0].error
            conditions.append(condition(ctype, "False", e.reason, f"{failed[0].name}: {e}", generation,
                                        prev_conditions))
        else:
            blocked = [r for r in results if r.error is not None]   # an earlier step failed for these
            done = len(results) - len(blocked)
            conditions.append(condition(ctype, "True", "Done", f"{done} of {len(results)} item(s)", generation,
                                        prev_conditions))
    missing = [r for r in results if r.required and r.binding is None]
    bound = sum(r.binding is not None for r in results)
    if not missing:
        message = f"{bound} of {len(results)} data resource(s) bound"
        conditions.append(condition("Ready", "True", "Bound", message, generation, prev_conditions))
        phase = "Ready"
    else:
        e = missing[0].error
        message = f"{missing[0].name}: {e}" if e else f"{missing[0].name} not bound"
        reason = e.reason if e else "NotBound"
        conditions.append(condition("Ready", "False", reason, message, generation, prev_conditions))
        phase = "Pending" if reason in PENDING_REASONS else "Error"
    return {"phase": phase, "message": message, "observedGeneration": generation, "resourceCount": bound,
            "bindings": bindings, "conditions": conditions}


def needed_roles(drcs: list[dict], consumer: str, provider: str, settings: Settings,
                 exclude: tuple[str, str] | None = None) -> set[str]:
    """The provider client roles that live consumer bindings of ``consumer`` to ``provider`` still need."""
    roles = set()
    for d in drcs:
        spec, meta = d.get("spec") or {}, d.get("metadata") or {}
        if spec.get("provider") != OWNED_PROVIDER or role_of(spec) != "consumer" or meta.get("deletionTimestamp"):
            continue
        if exclude and (meta.get("namespace"), meta.get("name")) == exclude:
            continue
        if component_of(meta, settings) != consumer:
            continue
        for b in (d.get("status") or {}).get("bindings") or []:
            if b.get("providerComponent") == provider:
                roles.update(settings.roles_for(b.get("access")))
    return roles


def system_role_needed(drcs: list[dict], provider: str, settings: Settings,
                       exclude: tuple[str, str] | None = None) -> bool:
    """True while a provider DataResourceConfig of ``provider`` or a consumer binding to it remains: the operator then
    keeps the provider's canvasSystemRole."""
    for d in drcs:
        spec, meta = d.get("spec") or {}, d.get("metadata") or {}
        if spec.get("provider") != OWNED_PROVIDER or meta.get("deletionTimestamp"):
            continue
        if exclude and (meta.get("namespace"), meta.get("name")) == exclude:
            continue
        if role_of(spec) == "provider" and component_of(meta, settings) == provider:
            return True
        if role_of(spec) == "consumer" and any(b.get("providerComponent") == provider
                                               for b in (d.get("status") or {}).get("bindings") or []):
            return True
    return False


# --- binding -----------------------------------------------------------------------------------------------------


class ConsumerBinder:
    """Binds one consumer DataResourceConfig; driven by ``dataResourceOperator.consumer_daemon``."""

    def __init__(self, namespace: str, name: str, settings: Settings, kube: Kube, tokens: TokenManager,
                 admin: KeycloakAdmin, session_factory):
        self.namespace, self.name = namespace, name
        self.settings, self.kube, self.tokens, self.admin = settings, kube, tokens, admin
        self.session = session_factory
        self.logw = LogWrapper(handler_name="consumer_daemon", function_name="ConsumerBinder",
                               resource_name=f"DataResourceConfig/{namespace}/{name}")

    def client_for(self, api: ProviderApi, provider: str, system_role: str) -> ProviderClient:
        return ProviderClient(api, provider, system_role, self.tokens, self.admin, self.session,
                              self.settings.oidc_dir)

    async def reconcile(self, drc: dict) -> dict | None:
        """Bind every item and write the status if it changed; returns the patch written (or None)."""
        spec, meta, status = drc.get("spec") or {}, drc.get("metadata") or {}, drc.get("status") or {}
        consumer = component_of(meta, self.settings)
        self.logw.set(component_name=consumer)
        try:
            drcs = await self.kube.list_drcs()
            providers = provider_drcs(drcs, self.settings)
            components = {}
            results = []
            for item in spec.get("dependentDataResources") or []:
                results.append(await self.bind(drc, item, consumer, providers, components))
        except (ProviderError, KeycloakError) as e:       # not specific to an item
            reason = getattr(e, "reason", "CredentialsMissing" if isinstance(e, CredentialsMissing) else
                             "AccessDenied")
            results = [ItemResult(i["name"], i.get("required", True),
                                  error=BindingError("ProviderResolved", reason, str(e)))
                       for i in spec.get("dependentDataResources") or []]
        await self.release_removed(drc, consumer, results)
        patch = consumer_status(status, meta.get("generation"), results)
        if not changed(status, patch):
            return None
        await self.kube.patch_status(self.namespace, self.name, patch)
        self.logw.info(f"status written ({patch['phase']})", patch["message"])
        return patch

    async def bind(self, drc: dict, item: dict, consumer: str | None, providers: dict, components: dict) -> ItemResult:
        name, required = item["name"], item.get("required", True)
        ref = item.get("providerRef") or {}
        ns = ref.get("namespace") or self.settings.component_namespace
        try:
            if ns not in components:
                components[ns] = await self.kube.list_components(ns)
            provider, provider_drc = resolve_provider(ref, components[ns], providers, ns)
            store = provided_item(provider_drc, item.get("type"))
            comp = next((c for c in components[ns] if c["metadata"]["name"] == provider), None) or {}
            system_role = ((comp.get("spec") or {}).get("securityFunction") or {}).get("canvasSystemRole")
            if not system_role:
                raise BindingError("ProviderResolved", "ProviderNotFound",
                                   f"component {provider} declares no securityFunction.canvasSystemRole")
            exposed = await self.kube.list_exposed_apis(ns, provider)
            try:
                rest = resolve_api(store.get("exposedAPI"), provider, ns, exposed, self.settings.componentname_label)
                mcp = resolve_api(store.get("mcpAPI"), provider, ns, exposed, self.settings.componentname_label)
            except ProviderError as e:
                raise BindingError("ProviderResolved", e.reason, str(e)) from None
            mcp_spec = next((((a.get("spec") or {}).get("specification") or {}).get("url") for a in exposed
                             if (a.get("spec") or {}).get("name") == store.get("mcpAPI")), None)
            kb_id = await self.ensure_knowledge(self.client_for(rest, provider, system_role), ref)
            await self.grant(consumer, provider, self.settings.roles_for(item.get("access")))
            data = binding_data(rest, mcp, kb_id, provider)
            await self.kube.apply(secret_body(drc, name, consumer, data, self.settings))
            await self.kube.apply(dependentapi_body(drc, item, consumer, mcp_spec, self.settings))
            await self.kube.patch_dependentapi_status(self.namespace, dependentapi_name(self.name, name),
                                                      {"implementation": {"ready": True},
                                                       "depapiStatus": {"url": data["mcpUrl"]}})
            binding = {"name": name, "providerComponent": provider, "kbId": kb_id,
                       "secretName": secret_name(self.name, name),
                       "dependentApiName": dependentapi_name(self.name, name),
                       "access": item.get("access") or "read", "ready": True}
            return ItemResult(name, required, binding=binding)
        except BindingError as e:
            return ItemResult(name, required, error=e)
        except ProviderError as e:   # calling the provider API, including the operator's own canvasSystemRole grant
            cond = "ProviderResolved" if e.reason == "ComponentNotFound" else "KnowledgeBaseReady"
            return ItemResult(name, required, error=BindingError(cond, e.reason, str(e)))
        except KeycloakError as e:   # granting the consumer
            return ItemResult(name, required, error=BindingError("AccessGranted", "AccessDenied", str(e)))

    async def ensure_knowledge(self, client: ProviderClient, ref: dict) -> str:
        """The bound knowledge-base id: ``knowledgeId``, or the one named ``create.name`` (created if missing)."""
        await client.grant()
        if ref.get("knowledgeId"):
            kb = await client.get_knowledge(ref["knowledgeId"])
            if kb is None:
                raise BindingError("KnowledgeBaseReady", "KnowledgeBaseNotFound",
                                   f"the provider has no knowledge-base {ref['knowledgeId']}")
            return str(kb["id"])
        create = ref.get("create") or {}
        if not create.get("name"):
            raise BindingError("KnowledgeBaseReady", "KnowledgeBaseNotFound", "providerRef has no knowledgeId or create")
        kb = await client.find_knowledge(create["name"])
        if kb is None:
            kb = await client.create_knowledge(knowledge_body(create))
            self.logw.info("knowledge-base created", f"{kb['id']} on {client.provider_client}")
        return str(kb["id"])

    async def grant(self, consumer: str | None, provider: str, roles: tuple) -> None:
        """Map the provider client roles to the consumer component's service account."""
        if not consumer:
            raise BindingError("AccessGranted", "ConsumerClientNotFound",
                               f"no {self.settings.componentname_label} label names the consumer component")
        consumer_client = await self.admin.find_client(consumer)
        provider_client = await self.admin.find_client(provider)
        if consumer_client is None:
            raise BindingError("AccessGranted", "ConsumerClientNotFound", f"no Keycloak client {consumer}")
        if provider_client is None:
            raise BindingError("AccessGranted", "AccessDenied", f"no Keycloak client {provider}")
        sa = await self.admin.service_account_user(consumer_client["id"])
        if sa is None:
            raise BindingError("AccessGranted", "ConsumerClientNotFound", f"client {consumer} has no service account")
        mapped = {r.get("name") for r in await self.admin.mapped_client_roles(sa["id"], provider_client["id"])}
        missing = []
        for role_name in roles:
            if role_name in mapped:
                continue
            role = await self.admin.client_role(provider_client["id"], role_name)
            if role is None:
                raise BindingError("AccessGranted", "AccessDenied", f"client {provider} has no role {role_name}")
            missing.append(role)
        if missing:
            await self.admin.map_client_roles(sa["id"], provider_client["id"], missing)
            self.logw.info("access granted", f"{', '.join(r['name'] for r in missing)} of {provider} to {consumer}")

    async def revoke(self, consumer: str | None, provider: str, roles: set) -> None:
        if not consumer or not roles:
            return
        consumer_client = await self.admin.find_client(consumer)
        provider_client = await self.admin.find_client(provider)
        if consumer_client is None or provider_client is None:
            return
        sa = await self.admin.service_account_user(consumer_client["id"])
        if sa is None:
            return
        mapped = await self.admin.mapped_client_roles(sa["id"], provider_client["id"])
        drop = [r for r in mapped if r.get("name") in roles]
        if drop:
            await self.admin.unmap_client_roles(sa["id"], provider_client["id"], drop)
            self.logw.info("access revoked", f"{', '.join(r['name'] for r in drop)} of {provider} from {consumer}")

    async def release(self, consumer: str | None, bindings: list[dict], exclude: tuple[str, str] | None,
                      also_keep: list[dict] = ()) -> list[str]:
        """Remove the Secrets and DependentAPIs of ``bindings`` and revoke the roles that neither another consumer
        DataResourceConfig (``exclude`` aside) nor ``also_keep`` still needs. Best effort: problems are returned,
        never raised."""
        problems = []
        try:
            drcs = await self.kube.list_drcs()
        except Exception as e:
            return [f"DataResourceConfigs not listed, nothing released: {e.__class__.__name__}"]
        for b in bindings:
            for kind, obj in (("Secret", b.get("secretName")), ("DependentAPI", b.get("dependentApiName"))):
                if obj:
                    try:
                        await self.kube.delete(kind, self.namespace, obj)
                    except Exception as e:
                        problems.append(f"{kind} {obj} not deleted: {e.__class__.__name__}")
        for provider in sorted({b.get("providerComponent") for b in bindings if b.get("providerComponent")}):
            gone = {r for b in bindings if b.get("providerComponent") == provider
                    for r in self.settings.roles_for(b.get("access"))}
            keep = needed_roles(drcs, consumer, provider, self.settings, exclude)
            keep |= {r for b in also_keep if b.get("providerComponent") == provider
                     for r in self.settings.roles_for(b.get("access"))}
            try:
                await self.revoke(consumer, provider, gone - keep)
            except (KeycloakError, ProviderError) as e:
                problems.append(f"access to {provider} not revoked: {e}")
        return problems

    async def release_removed(self, drc: dict, consumer: str | None, results: list[ItemResult]) -> None:
        """Release the bindings of items no longer in the spec (this resource's other bindings keep their roles)."""
        names = {r.name for r in results}
        removed = [b for b in (drc.get("status") or {}).get("bindings") or [] if b.get("name") not in names]
        if removed:
            live = [r.binding for r in results if r.binding]
            for p in await self.release(consumer, removed, exclude=(self.namespace, self.name), also_keep=live):
                self.logw.warning("released binding incompletely", p)

    async def cleanup(self, drc: dict) -> list[str]:
        """On delete: release every binding, then drop the operator's canvasSystemRole on providers nothing else
        needs. The knowledge-bases are kept."""
        spec, meta = drc.get("spec") or {}, drc.get("metadata") or {}
        bindings = (drc.get("status") or {}).get("bindings") or []
        me = (self.namespace, self.name)
        problems = await self.release(component_of(meta, self.settings), bindings, exclude=me)
        namespaces = {i.get("name"): (i.get("providerRef") or {}).get("namespace") or self.settings.component_namespace
                      for i in spec.get("dependentDataResources") or []}
        try:
            drcs = await self.kube.list_drcs()
            for b in bindings:
                provider = b.get("providerComponent")
                if not provider or system_role_needed(drcs, provider, self.settings, exclude=me):
                    continue
                comp = await self.kube.get_component(namespaces.get(b.get("name"), self.settings.component_namespace),
                                                     provider)
                role = (((comp or {}).get("spec") or {}).get("securityFunction") or {}).get("canvasSystemRole")
                if role and await self.client_for(None, provider, role).revoke():
                    self.logw.info("revoked", f"{role} on {provider}")
        except Exception as e:
            problems.append(f"canvasSystemRole not revoked: {e.__class__.__name__}: {e}")
        return problems
