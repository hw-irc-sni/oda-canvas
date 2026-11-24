"""The provider mirror against a fake Keycloak, a fake TMFC060 API and a fake Kubernetes API."""

import asyncio
import json

import aiohttp
from aiohttp.test_utils import TestServer

from fake_keycloak import FakeKeycloak
from fake_provider import PROVIDER, FakeKube, FakeProviderApi, LocalResolver, exposed_api
from keycloak import KeycloakAdmin, TokenManager
from kube import HUB_ANNOTATION
from mirror import ProviderMirror, changed, provider_status
from provider import ProviderError, HUB_EVENTS
from settings import Settings

SPEC = {"provider": "odaComponent", "role": "provider", "providedDataResources": [
    {"name": "knowledge-vectors", "type": "vectorStore", "exposedAPI": "knowledgemanagement"},
    {"name": "knowledge-graph", "type": "knowledgeGraph", "exposedAPI": "knowledgemanagement"}]}
CALLBACK_BASE = "http://dro-dataresource-op.canvas.svc.cluster.local:8081"


def keycloak():
    kc = FakeKeycloak()
    op = kc.add_client("dataresource-operator", secret="op-secret")
    rm = kc.by_client_id("realm-management")["id"]
    kc.sa_of(op)["mappings"][rm] = {"view-clients", "manage-users"}
    prov = kc.add_client(PROVIDER)
    for r in ("canvasRole", "knowledge-viewer", "knowledge-editor"):
        kc.add_role(prov, r)
    return kc


class Harness:
    def __init__(self, tmp_path, callback_base=CALLBACK_BASE):
        self.kc = keycloak()
        self.api = FakeProviderApi(self.kc)
        self.kube = FakeKube()
        self.kube.components[("components", PROVIDER)] = {"spec": {"securityFunction": {"canvasSystemRole": "canvasRole"}}}
        self.tmp_path, self.callback_base = tmp_path, callback_base
        self.meta = {"generation": 1, "labels": {"oda.tmforum.org/componentName": PROVIDER}, "annotations": {}}
        self.status = {}

    async def __aenter__(self):
        self.kc_server, self.api_server = TestServer(self.kc.app()), TestServer(self.api.app())
        await self.kc_server.start_server()
        await self.api_server.start_server()
        kc_base = str(self.kc_server.make_url("/auth"))
        oidc = self.tmp_path / "oidc"
        oidc.mkdir(exist_ok=True)
        (oidc / "client_id").write_text("dataresource-operator")
        (oidc / "client_secret").write_text("op-secret")
        (oidc / "token_url").write_text(f"{kc_base}/realms/odari/protocol/openid-connect/token")
        self.kube.exposed = [exposed_api(self.api_server.port)]
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=LocalResolver()))
        settings = Settings(oidc_dir=str(oidc), callback_base=self.callback_base)
        self.tokens = TokenManager(str(oidc), lambda: self.session)
        admin = KeycloakAdmin(kc_base, "odari", self.tokens, lambda: self.session)
        self.mirror = ProviderMirror("components", "kmk-knowledgemanagement", settings, self.kube, self.tokens, admin,
                                     lambda: self.session)
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.api_server.close()
        await self.kc_server.close()

    async def poll(self):
        patch = await self.mirror.poll(SPEC, self.meta, self.status)
        if patch:
            self.status = {**self.status, **patch}
        hub = self.kube.annotations.get(("components", "kmk-knowledgemanagement"))
        self.meta["annotations"] = {HUB_ANNOTATION: json.dumps(hub)} if hub else {}
        return patch


def run(coro):
    return asyncio.run(coro)


def kb(i, store="hybrid"):
    return {"id": f"kb-{i}", "name": f"KB {i}", "storeKind": store, "lifecycleStatus": "draft"}


def test_first_poll_grants_mirrors_and_registers(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.api.knowledge = [kb(1)]
            h.api.modules = [{"id": "tio-core", "name": "TIO core", "layer": "Telecom", "lifecycleStatus": "published"}]
            patch = await h.poll()
            return h, patch

    h, patch = run(go())
    assert h.kc.mapped("dataresource-operator", PROVIDER) == ["canvasRole"]
    assert patch["phase"] == "Ready" and patch["resourceCount"] == 2
    assert patch["message"] == "Mirroring 1 knowledge-base and 1 ontology module"
    assert [r["id"] for r in patch["providedResources"]] == ["kb-1", "tio-core"]
    assert patch["providedResources"][0]["href"].startswith("https://gateway.example/")
    (ready,) = patch["conditions"]
    assert (ready["type"], ready["status"], ready["reason"]) == ("Ready", "True", "Mirrored")
    (hub,) = h.api.hubs.values()
    assert hub["callback"] == f"{CALLBACK_BASE}/providers/components/kmk-knowledgemanagement"
    assert hub["query"] == "eventType=" + ",".join(HUB_EVENTS)
    assert h.kube.annotations[("components", "kmk-knowledgemanagement")] == {"id": hub["id"],
                                                                             "callback": hub["callback"]}


def test_unchanged_provider_writes_nothing_and_a_change_is_mirrored(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            await h.poll()
            second = await h.poll()
            h.api.knowledge.append(kb(7, "graph"))
            third = await h.poll()
            h.api.knowledge.clear()
            fourth = await h.poll()
            return h, second, third, fourth

    h, second, third, fourth = run(go())
    assert second is None and len(h.kube.status_patches) == 3
    assert third["providedResources"] == [{"kind": "knowledge", "id": "kb-7", "name": "KB 7", "type": "knowledgeGraph",
                                           "storeKind": "graph", "lifecycleStatus": "draft",
                                           "href": "https://gateway.example/kmk-knowledgemanagement/tmf-api/"
                                                   "knowledgeManagement/v5/knowledge/kb-7"}]
    assert fourth["providedResources"] == [] and fourth["resourceCount"] == 0
    assert len(h.api.hubs) == 1         # registered once


def test_errors_keep_the_last_good_list(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.api.knowledge = [kb(1)]
            await h.poll()
            h.api.down = True
            err = await h.poll()
            again = await h.poll()          # same error: nothing rewritten
            h.api.down = False
            back = await h.poll()
            return h, err, again, back

    h, err, again, back = run(go())
    assert err["phase"] == "Error" and "providedResources" not in err
    assert err["conditions"][0]["status"] == "False" and err["conditions"][0]["reason"] == "ProviderUnreachable"
    assert h.status["providedResources"][0]["id"] == "kb-1"
    assert again is None
    assert back["phase"] == "Ready"


def test_lost_grant_is_restored_on_401(tmp_path):
    """The provider was reinstalled: its Keycloak client (and the operator's role mapping) is new."""
    async def go():
        async with Harness(tmp_path) as h:
            await h.poll()
            prov = h.kc.by_client_id(PROVIDER)["id"]
            h.kc.sa_of(h.kc.by_client_id("dataresource-operator")["id"])["mappings"][prov] = set()
            h.tokens.invalidate()
            h.api.knowledge = [kb(2)]
            patch = await h.poll()
            return h, patch

    h, patch = run(go())
    assert patch["phase"] == "Ready" and patch["providedResources"][0]["id"] == "kb-2"
    assert h.kc.mapped("dataresource-operator", PROVIDER) == ["canvasRole"]


def test_paging(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.api.modules = [{"id": f"m-{i:03d}", "layer": "Implementation"} for i in range(450)]
            patch = await h.poll()
            return h, patch

    h, patch = run(go())
    assert patch["resourceCount"] == 450
    pages = [q for m, q in h.api.requests if q.split("?")[0].endswith("/knowledgeSpecification")]
    assert len(pages) == 3


def test_hub_reuse_stale_and_moved_callback(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            await h.poll()
            first = dict(h.api.hubs)
            # a restarted operator: same annotation, hub still there -> reused
            h.mirror.hub_checked = False
            await h.mirror.ensure_hub((await h.mirror.clients(SPEC, h.meta))[0], h.meta)
            reused = dict(h.api.hubs)
            # the provider lost it -> registered again
            h.api.hubs.clear()
            h.mirror.hub_checked = False
            await h.mirror.ensure_hub((await h.mirror.clients(SPEC, h.meta))[0], h.meta)
            recreated = dict(h.api.hubs)
            h.meta["annotations"] = {HUB_ANNOTATION: json.dumps(
                h.kube.annotations[("components", "kmk-knowledgemanagement")])}
            # the operator's Service moved (another release name) -> old one deleted, new one registered
            h.mirror.settings = Settings(oidc_dir=h.mirror.settings.oidc_dir,
                                         callback_base="http://other-dataresource-op.canvas.svc.cluster.local:8081")
            h.mirror.hub_checked = False
            await h.mirror.ensure_hub((await h.mirror.clients(SPEC, h.meta))[0], h.meta)
            moved = dict(h.api.hubs)
            return first, reused, recreated, moved

    first, reused, recreated, moved = run(go())
    assert reused == first and len(recreated) == 1 and recreated.keys() != first.keys()
    (hub,) = moved.values()
    assert hub["callback"].startswith("http://other-dataresource-op.") and moved.keys() != recreated.keys()


def test_no_callback_base_means_polling_only(tmp_path):
    async def go():
        async with Harness(tmp_path, callback_base="") as h:
            return h, await h.poll()

    h, patch = run(go())
    assert patch["phase"] == "Ready" and h.api.hubs == {}
    assert patch["message"].endswith("; no change events (no callback base configured)")


def test_component_and_api_errors(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.kube.components.clear()
            missing = await h.poll()
            h.kube.components[("components", PROVIDER)] = {"spec": {"securityFunction": {"canvasSystemRole": "canvasRole"}}}
            h.kube.exposed = [exposed_api(h.api_server.port, ready=False)]
            not_ready = await h.poll()
            return missing, not_ready

    missing, not_ready = run(go())
    assert missing["conditions"][0]["reason"] == "ComponentNotFound"
    assert not_ready["conditions"][0]["reason"] == "ExposedAPINotReady"


def test_cleanup_is_best_effort(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            await h.poll()
            problems = await h.mirror.cleanup(SPEC, h.meta)
            clean = (dict(h.api.hubs), h.kc.mapped("dataresource-operator", PROVIDER))
            # provider and its client gone at the same time (helm uninstall): nothing raised
            h.api.down = True
            del h.kc.clients[h.kc.by_client_id(PROVIDER)["id"]]
            late = await h.mirror.cleanup(SPEC, h.meta)
            h.kube.components.clear()
            gone = await h.mirror.cleanup(SPEC, h.meta)
            return problems, clean, late, gone

    problems, clean, late, gone = run(go())
    assert problems == [] and clean == ({}, [])
    assert any("hub" in p for p in late)
    assert gone and "nothing to clean up" in gone[0]


def test_status_helpers():
    ok = provider_status({}, 2, [])
    assert ok["phase"] == "Ready" and ok["providedResources"] == [] and ok["observedGeneration"] == 2
    err = provider_status(ok, 2, error=ProviderError("AccessDenied", "nope"))
    assert err["conditions"][0]["lastTransitionTime"]
    assert not changed({**ok, **err}, provider_status({**ok, **err}, 2, error=ProviderError("AccessDenied", "nope")))
    assert changed(ok, err)
