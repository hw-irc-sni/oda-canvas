"""Provider role (hw-irc-sni/oda-canvas#63): mirror what an ODA Component serves into its DataResourceConfig status.

The pure functions here turn Kubernetes objects and API lists into the status (tested without kopf); the
``ProviderClient`` calls the provider's TMFxxx API with the operator's token.

- The provider API is called at its ExposedAPI implementation Service,
  ``http://<spec.implementation>.<namespace>.svc.cluster.local:<spec.port><spec.path>``, which pods can reach on any
  cluster. The published gateway URL (``status.apiStatus.url``) is only used for the ``href`` of each item.
- The operator's token carries the provider's ``canvasSystemRole`` because the operator maps that client role to its
  own service account (spike S4). The mapping is lost when the provider is reinstalled (its Keycloak client is
  recreated), so a 401 or 403 re-runs the grant once.
"""

from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from keycloak import CredentialsMissing, KeycloakAdmin, KeycloakError, TokenManager, read_credentials

# The events of the TMFxxx Knowledge Management hub this operator subscribes to
HUB_EVENTS = (
    "KnowledgeCreateEvent", "KnowledgeAttributeValueChangeEvent", "KnowledgeStateChangeEvent", "KnowledgeDeleteEvent",
    "KnowledgeSpecificationCreateEvent", "KnowledgeSpecificationAttributeValueChangeEvent",
    "KnowledgeSpecificationStateChangeEvent", "KnowledgeSpecificationDeleteEvent",
)

# storeKind of a knowledge-base -> the store type it lives in; hybrid lives in both, so it gets no type (ADR-011:
# isPartOf both stores, derived from storeKind)
STORE_TYPE = {"graph": "knowledgeGraph", "vector": "vectorStore"}


class ProviderError(Exception):
    """A provider poll failed. ``reason`` is the Ready condition reason."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# --- resolution (pure) -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderApi:
    """One ExposedAPI of the provider: where the operator calls it, and its published URL."""

    name: str              # ExposedAPI spec.name, e.g. knowledgemanagement
    internal_url: str      # http://<implementation>.<ns>.svc.cluster.local:<port><path>
    public_url: str        # status.apiStatus.url (falls back to internal_url)


def exposed_api_names(spec: dict) -> list[str]:
    """The distinct REST ExposedAPI names the provided stores name, in order."""
    names = []
    for item in spec.get("providedDataResources") or []:
        name = item.get("exposedAPI")
        if name and name not in names:
            names.append(name)
    return names


def resolve_api(name: str, component: str, namespace: str, exposed_apis: list[dict],
                componentname_label: str = "oda.tmforum.org/componentName") -> ProviderApi:
    """Find the component's ExposedAPI with ``spec.name == name`` and build its URLs.

    Raises:
        ProviderError: ExposedAPINotFound, or ExposedAPINotReady while its implementation isn't ready.
    """
    for api in exposed_apis:
        meta, spec = api.get("metadata") or {}, api.get("spec") or {}
        if spec.get("name") != name or (meta.get("labels") or {}).get(componentname_label) != component:
            continue
        if not spec.get("implementation") or not spec.get("port"):
            raise ProviderError("ExposedAPINotFound", f"ExposedAPI {meta.get('name')} has no implementation or port")
        ns = meta.get("namespace") or namespace
        path = "/" + (spec.get("path") or "").strip("/")
        internal = f"http://{spec['implementation']}.{ns}.svc.cluster.local:{spec['port']}{path.rstrip('/')}"
        status = api.get("status") or {}
        if not (status.get("implementation") or {}).get("ready"):
            raise ProviderError("ExposedAPINotReady", f"ExposedAPI {meta.get('name')} is not ready yet")
        public = ((status.get("apiStatus") or {}).get("url") or internal).rstrip("/")
        return ProviderApi(name=name, internal_url=internal, public_url=public)
    raise ProviderError("ExposedAPINotFound", f"component {component} has no ExposedAPI named {name}")


def provided_resources(api: ProviderApi, knowledge: list[dict], specifications: list[dict]) -> list[dict]:
    """``status.providedResources[]`` (ADR-010 §6) from the provider's /knowledge and /knowledgeSpecification lists.

    Knowledge-bases first, then modules, each sorted by id, so an unchanged provider gives an identical list.
    """
    out = []
    for kb in sorted(knowledge, key=lambda k: str(k.get("id"))):
        entry = {"kind": "knowledge", "id": str(kb.get("id")), "name": kb.get("name") or str(kb.get("id"))}
        store_kind = kb.get("storeKind")
        if store_kind in STORE_TYPE:
            entry["type"] = STORE_TYPE[store_kind]
        if store_kind:
            entry["storeKind"] = store_kind
        if kb.get("lifecycleStatus"):
            entry["lifecycleStatus"] = kb["lifecycleStatus"]
        entry["href"] = f"{api.public_url}/knowledge/{quote(str(kb.get('id')), safe='')}"
        out.append(entry)
    for mod in sorted(specifications, key=lambda m: str(m.get("id"))):
        entry = {"kind": "knowledgeSpecification", "id": str(mod.get("id")),
                 "name": mod.get("name") or str(mod.get("id")), "type": "knowledgeGraph"}
        if mod.get("layer"):
            entry["layer"] = mod["layer"]
        if mod.get("lifecycleStatus"):
            entry["lifecycleStatus"] = mod["lifecycleStatus"]
        entry["href"] = f"{api.public_url}/knowledgeSpecification/{quote(str(mod.get('id')), safe='')}"
        out.append(entry)
    return out


def summary(resources: list[dict]) -> str:
    kbs = sum(r["kind"] == "knowledge" for r in resources)
    mods = len(resources) - kbs
    return f"{kbs} knowledge-base{'s' if kbs != 1 else ''} and {mods} ontology module{'s' if mods != 1 else ''}"


# --- calls -------------------------------------------------------------------------------------------------------


class ProviderClient:
    """The provider's TMFxxx API, called with the operator token; re-grants the canvasSystemRole on 401/403.

    Args:
        api: Where to call the provider.
        provider_client: The provider's Keycloak client id (the component name).
        system_role: The provider's canvasSystemRole (``securityFunction.canvasSystemRole``).
    """

    def __init__(self, api: ProviderApi, provider_client: str, system_role: str, tokens: TokenManager,
                 admin: KeycloakAdmin, session_factory, oidc_dir: str):
        self.api = api
        self.provider_client = provider_client
        self.system_role = system_role
        self.tokens = tokens
        self.admin = admin
        self.session = session_factory
        self.oidc_dir = oidc_dir

    async def grant(self) -> bool:
        """Map the provider's canvasSystemRole to the operator's service account; True if it was added."""
        try:
            own_id = read_credentials(self.oidc_dir)[0]
            own = await self.admin.find_client(own_id)
            provider = await self.admin.find_client(self.provider_client)
            if own is None:
                raise ProviderError("AccessDenied", f"the operator client {own_id} is missing in Keycloak")
            if provider is None:
                raise ProviderError("ComponentNotFound", f"no Keycloak client {self.provider_client}")
            role = await self.admin.client_role(provider["id"], self.system_role)
            if role is None:
                raise ProviderError("AccessDenied", f"client {self.provider_client} has no role {self.system_role}")
            me = await self.admin.service_account_user(own["id"])
            if me is None:
                raise ProviderError("AccessDenied", f"the operator client {own_id} has no service account")
            mapped = {r.get("name") for r in await self.admin.mapped_client_roles(me["id"], provider["id"])}
            if self.system_role in mapped:
                return False
            await self.admin.map_client_roles(me["id"], provider["id"], [role])
        except CredentialsMissing as e:
            raise ProviderError("CredentialsMissing", str(e)) from None
        except KeycloakError as e:
            raise ProviderError("AccessDenied", f"granting {self.system_role}: {e}") from None
        self.tokens.invalidate()
        return True

    async def revoke(self) -> bool:
        """Remove the canvasSystemRole mapping; True if it was there. Missing clients count as done."""
        own = await self.admin.find_client(read_credentials(self.oidc_dir)[0])
        provider = await self.admin.find_client(self.provider_client)
        if own is None or provider is None:
            return False
        me = await self.admin.service_account_user(own["id"])
        role = await self.admin.client_role(provider["id"], self.system_role)
        if me is None or role is None:
            return False
        mapped = {r.get("name") for r in await self.admin.mapped_client_roles(me["id"], provider["id"])}
        if self.system_role not in mapped:
            return False
        await self.admin.unmap_client_roles(me["id"], provider["id"], [role])
        self.tokens.invalidate()
        return True

    async def _request(self, method: str, path: str, body=None, retry=True):
        """One call; on 401/403 the grant is re-run and the call retried once. Returns (status, json, headers)."""
        try:
            token = await self.tokens.token()
        except KeycloakError as e:
            raise ProviderError("CredentialsMissing", str(e)) from None
        try:
            async with self.session().request(method, self.api.internal_url + path, json=body,
                                              headers={"Authorization": f"Bearer {token}"}) as r:
                denied = r.status in (401, 403)
                if not (denied and retry):
                    data = None if r.status == 204 else await r.json(content_type=None)
                    return r.status, data, r.headers
        except aiohttp.ClientError as e:
            raise ProviderError("ProviderUnreachable", f"{method} {self.api.name}{path}: {e.__class__.__name__}") \
                from None
        except ValueError:
            raise ProviderError("ProviderUnreachable", f"{method} {self.api.name}{path}: not JSON") from None
        await self.grant()
        self.tokens.invalidate()
        return await self._request(method, path, body, retry=False)

    async def list(self, resource: str, page: int = 200) -> list[dict]:
        """Every item of /knowledge or /knowledgeSpecification, following X-Total-Count."""
        items, offset = [], 0
        while True:
            status, data, headers = await self._request("GET", f"/{resource}?offset={offset}&limit={page}")
            if status in (401, 403):
                raise ProviderError("AccessDenied", f"GET /{resource} refused with HTTP {status}")
            if status != 200 or not isinstance(data, list):
                raise ProviderError("ProviderUnreachable", f"GET /{resource} answered HTTP {status}")
            items.extend(data)
            total = int(headers.get("X-Total-Count", len(items)))
            if not data or len(items) >= total:
                return items
            offset += len(data)

    async def hub_get(self, hub_id: str) -> dict | None:
        status, data, _ = await self._request("GET", f"/hub/{quote(hub_id, safe='')}")
        return data if status == 200 else None

    async def hub_register(self, callback: str) -> dict:
        body = {"callback": callback, "query": "eventType=" + ",".join(HUB_EVENTS)}
        status, data, _ = await self._request("POST", "/hub", body)
        if status != 201 or not isinstance(data, dict) or not data.get("id"):
            detail = (data.get("reason") or data.get("message") or "") if isinstance(data, dict) else ""
            raise ProviderError("HubRefused", f"POST /hub answered HTTP {status} {detail}".strip())
        return data

    async def hub_delete(self, hub_id: str) -> bool:
        status, _, _ = await self._request("DELETE", f"/hub/{quote(hub_id, safe='')}")
        return status in (204, 404)
