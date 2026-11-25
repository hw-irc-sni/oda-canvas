"""A fake TMFC060 TMFxxx API (the calls TMFOP012 makes), a fake Kubernetes API, and a resolver that sends the
in-cluster Service host names to the local test servers."""

import socket

from aiohttp import web
from aiohttp.abc import AbstractResolver

from fake_keycloak import FakeKeycloak

PROVIDER = "kmk-knowledgemanagement"
BASE_PATH = "/kmk-knowledgemanagement/tmf-api/knowledgeManagement/v5"


class FakeProviderApi:
    """``/knowledge``, ``/knowledgeSpecification`` (offset/limit, X-Total-Count) and ``/hub``.

    A token is accepted only when, in the fake Keycloak, its client's service account holds ``canvasRole`` on the
    provider client: the same rule as TMFC060 (role in resource_access, client in aud).
    """

    def __init__(self, kc: FakeKeycloak, client_id: str = PROVIDER, system_role: str = "canvasRole"):
        self.kc, self.client_id, self.system_role = kc, client_id, system_role
        self.knowledge, self.modules, self.hubs = [], [], {}
        self.requests = []
        self.created = []
        self.down = False
        self._hub_n = 0

    def _authorised(self, request) -> bool:
        info = self.kc.tokens.get(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if not info or info["kind"] != "client":
            return False
        caller = self.kc.by_client_id(info["client_id"])
        provider = self.kc.by_client_id(self.client_id)
        if caller is None or provider is None:
            return False
        sa = self.kc.sa_of(caller["id"])
        return self.system_role in sa["mappings"].get(provider["id"], set())

    @web.middleware
    async def guard(self, request, handler):
        self.requests.append((request.method, request.path_qs))
        if self.down:
            raise web.HTTPServiceUnavailable()
        if not self._authorised(request):
            return web.json_response({"code": "401", "reason": "Unauthorized"}, status=401)
        return await handler(request)

    def _list(self, items):
        async def handler(request):
            offset = int(request.query.get("offset", 0))
            limit = int(request.query["limit"]) if "limit" in request.query else None
            selected = [i for i in items() if "name" not in request.query or i.get("name") == request.query["name"]]
            page = selected[offset: offset + limit if limit else None]
            return web.json_response(page, headers={"X-Total-Count": str(len(selected)),
                                                    "X-Result-Count": str(len(page))})
        return handler

    async def kb_get(self, request):
        kb = next((k for k in self.knowledge if k["id"] == request.match_info["id"]), None)
        return web.json_response(kb) if kb else web.json_response({"reason": "Not Found"}, status=404)

    async def kb_post(self, request):
        body = await request.json()
        if not body.get("name"):
            return web.json_response({"reason": "name is required"}, status=400)
        kb = {"id": body["name"], "lifecycleStatus": "draft", **body}
        self.knowledge.append(kb)
        self.created.append(body)
        return web.json_response(kb, status=201)

    async def hub_post(self, request):
        body = await request.json()
        if not body.get("callback", "").startswith("http"):
            return web.json_response({"reason": "callback is required"}, status=400)
        self._hub_n += 1
        hub_id = f"hub-{self._hub_n}"
        self.hubs[hub_id] = {"id": hub_id, "callback": body["callback"], "query": body.get("query")}
        return web.json_response(self.hubs[hub_id], status=201)

    async def hub_get(self, request):
        hub = self.hubs.get(request.match_info["id"])
        return web.json_response(hub) if hub else web.json_response({"reason": "Not Found"}, status=404)

    async def hub_delete(self, request):
        if self.hubs.pop(request.match_info["id"], None) is None:
            return web.json_response({"reason": "Not Found"}, status=404)
        return web.Response(status=204)

    def app(self):
        app = web.Application(middlewares=[self.guard])
        app.router.add_get(BASE_PATH + "/knowledge", self._list(lambda: self.knowledge))
        app.router.add_post(BASE_PATH + "/knowledge", self.kb_post)
        app.router.add_get(BASE_PATH + "/knowledge/{id}", self.kb_get)
        app.router.add_get(BASE_PATH + "/knowledgeSpecification", self._list(lambda: self.modules))
        app.router.add_post(BASE_PATH + "/hub", self.hub_post)
        app.router.add_get(BASE_PATH + "/hub/{id}", self.hub_get)
        app.router.add_delete(BASE_PATH + "/hub/{id}", self.hub_delete)
        return app


class LocalResolver(AbstractResolver):
    """Resolves every host name to 127.0.0.1 (the test servers)."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET, "proto": 0,
                 "flags": socket.AI_NUMERICHOST}]

    async def close(self):
        pass


def exposed_api(port: int, ready: bool = True, name: str = "knowledgemanagement", component: str = PROVIDER):
    return {
        "metadata": {"name": f"{component}-{name}-v5", "namespace": "components",
                     "labels": {"oda.tmforum.org/componentName": component}},
        "spec": {"name": name, "implementation": "kmk-kmapi", "port": port, "path": BASE_PATH, "apiType": "openapi",
                 "specification": {"url": f"http://kmk-kmapi.components.svc.cluster.local:{port}{BASE_PATH}/openapi.yaml"}},
        "status": {"implementation": {"ready": ready},
                   "apiStatus": {"url": f"https://gateway.example{BASE_PATH}"}},
    }


def mcp_api(port: int, component: str = PROVIDER):
    path = f"/{component}/mcp"
    return {
        "metadata": {"name": f"{component}-knowledgemcp", "namespace": "components",
                     "labels": {"oda.tmforum.org/componentName": component}},
        "spec": {"name": "knowledgemcp", "implementation": "kmk-kmmcp", "port": port, "path": path, "apiType": "mcp",
                 "specification": {"url": "https://modelcontextprotocol.io/specification/2025-06-18"}},
        "status": {"implementation": {"ready": True}, "apiStatus": {"url": f"https://gateway.example{path}"}},
    }


def component(name: str, component_id: str = "TMFC060", system_role: str = "canvasRole"):
    return {"metadata": {"name": name, "namespace": "components"},
            "spec": {"componentMetadata": {"id": component_id},
                     "securityFunction": {"canvasSystemRole": system_role}}}


def provider_drc(component_name: str = PROVIDER):
    return {"metadata": {"name": component_name, "namespace": "components", "uid": "uid-provider",
                         "labels": {"oda.tmforum.org/componentName": component_name}},
            "spec": {"provider": "odaComponent", "role": "provider", "providedDataResources": [
                {"name": "knowledge-vectors", "type": "vectorStore", "exposedAPI": "knowledgemanagement",
                 "mcpAPI": "knowledgemcp"},
                {"name": "knowledge-graph", "type": "knowledgeGraph", "exposedAPI": "knowledgemanagement",
                 "mcpAPI": "knowledgemcp"}]}}


class FakeKube:
    """The Kube calls of the mirror and the binder, in memory."""

    def __init__(self):
        self.components = {}
        self.exposed = []
        self.status_patches = []
        self.annotations = {}
        self.drcs = []
        self.objects = {}              # (kind, namespace, name) -> body
        self.depapi_status = {}
        self.fail_apply = False

    async def list_components(self, namespace):
        return [c for (ns, _), c in self.components.items() if ns == namespace]

    async def list_drcs(self):
        return list(self.drcs)

    async def apply(self, body):
        if self.fail_apply:
            raise RuntimeError("apply refused")
        meta = body["metadata"]
        self.objects[(body["kind"], meta["namespace"], meta["name"])] = body

    async def patch_dependentapi_status(self, namespace, name, status):
        self.depapi_status[(namespace, name)] = status

    async def delete(self, kind, namespace, name):
        return self.objects.pop((kind, namespace, name), None) is not None

    async def get_component(self, namespace, name):
        return self.components.get((namespace, name))

    async def list_exposed_apis(self, namespace, component):
        return [a for a in self.exposed
                if a["metadata"]["labels"].get("oda.tmforum.org/componentName") == component]

    async def patch_status(self, namespace, name, status):
        self.status_patches.append(status)

    async def set_hub(self, namespace, name, hub):
        self.annotations[(namespace, name)] = hub
