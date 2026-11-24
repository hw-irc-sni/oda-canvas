"""The consumer role against a fake Keycloak, a fake TMFC060 API and a fake Kubernetes API."""

import asyncio
import base64
import copy

import aiohttp
from aiohttp.test_utils import TestServer

from consumer import ConsumerBinder, consumer_status, ItemResult, BindingError, knowledge_body, needed_roles
from fake_keycloak import FakeKeycloak
from fake_provider import (PROVIDER, FakeKube, FakeProviderApi, LocalResolver, component, exposed_api, mcp_api,
                           provider_drc)
from keycloak import KeycloakAdmin, TokenManager
from settings import Settings

CONSUMER = "kmc-productcatalogmanagement"


def item(name="incident-kb", access="read", required=True, **ref):
    ref = ref or {"componentId": "TMFC060", "create": {"name": name, "storeKind": "hybrid"}}
    return {"name": name, "type": "vectorStore", "access": access, "required": required, "providerRef": ref}


def consumer_drc(*items, name="kmc-knowledge"):
    return {"apiVersion": "oda.tmforum.org/v1alpha1", "kind": "DataResourceConfig",
            "metadata": {"name": name, "namespace": "components", "uid": f"uid-{name}", "generation": 1,
                         "labels": {"oda.tmforum.org/componentName": CONSUMER}},
            "spec": {"provider": "odaComponent", "role": "consumer", "dependentDataResources": list(items)},
            "status": {}}


class Harness:
    def __init__(self, tmp_path):
        self.kc = FakeKeycloak()
        op = self.kc.add_client("dataresource-operator", secret="op-secret")
        rm = self.kc.by_client_id("realm-management")["id"]
        self.kc.sa_of(op)["mappings"][rm] = {"view-clients", "manage-users"}
        prov = self.kc.add_client(PROVIDER)
        for r in ("canvasRole", "knowledge-viewer", "knowledge-editor"):
            self.kc.add_role(prov, r)
        self.kc.add_client(CONSUMER)
        self.api = FakeProviderApi(self.kc)
        self.kube = FakeKube()
        self.kube.components[("components", PROVIDER)] = component(PROVIDER)
        self.kube.drcs = [provider_drc()]
        self.tmp_path = tmp_path

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
        self.kube.exposed = [exposed_api(self.api_server.port), mcp_api(self.api_server.port)]
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=LocalResolver()))
        self.settings = Settings(oidc_dir=str(oidc))
        self.tokens = TokenManager(str(oidc), lambda: self.session)
        self.admin = KeycloakAdmin(kc_base, "odari", self.tokens, lambda: self.session)
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.api_server.close()
        await self.kc_server.close()

    def binder(self, name="kmc-knowledge"):
        return ConsumerBinder("components", name, self.settings, self.kube, self.tokens, self.admin,
                              lambda: self.session)

    async def reconcile(self, drc):
        """One pass; the status is merged into drc and into the fake cluster's copy, as the API server would."""
        if all(d["metadata"]["name"] != drc["metadata"]["name"] for d in self.kube.drcs):
            self.kube.drcs.append(drc)
        patch = await self.binder(drc["metadata"]["name"]).reconcile(copy.deepcopy(drc))
        if patch:
            drc["status"] = {**drc.get("status", {}), **patch}
        return patch

    def secret(self, name):
        s = self.kube.objects.get(("Secret", "components", name))
        return None if s is None else {k: base64.b64decode(v).decode() for k, v in s["data"].items()}


def run(coro):
    return asyncio.run(coro)


def test_bind_with_create(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            drc = consumer_drc(item(componentId="TMFC060", create={
                "name": "incident-kb", "storeKind": "hybrid", "source": ["tmf642", "tmf628"],
                "conformsTo": ["tio-core", "https://example.org/onto#"]}))
            patch = await h.reconcile(drc)
            again = await h.reconcile(drc)
            return h, patch, again

    h, patch, again = run(go())
    assert h.api.created == [{"name": "incident-kb", "storeKind": "hybrid",
                              "conformsTo": [{"id": "tio-core"}, {"iri": "https://example.org/onto#"}],
                              "source": [{"kind": "tmf642"}, {"kind": "tmf628"}]}]
    gw = "https://gateway.example/kmk-knowledgemanagement/tmf-api/knowledgeManagement/v5"
    assert h.secret("kmc-knowledge-incident-kb-binding") == {
        "apiUrl": f"{gw}/knowledge/incident-kb", "queryUrl": f"{gw}/knowledge/incident-kb/query",
        "mcpUrl": "https://gateway.example/kmk-knowledgemanagement/mcp", "kbId": "incident-kb",
        "providerComponent": PROVIDER, "qdrantCollection": "tmfc060-kb-incident-kb"}
    secret = h.kube.objects[("Secret", "components", "kmc-knowledge-incident-kb-binding")]
    assert secret["metadata"]["ownerReferences"] == [{"apiVersion": "oda.tmforum.org/v1alpha1",
                                                      "kind": "DataResourceConfig", "name": "kmc-knowledge",
                                                      "uid": "uid-kmc-knowledge", "controller": True}]
    dep = h.kube.objects[("DependentAPI", "components", "kmc-knowledge-incident-kb")]
    assert dep["spec"] == {"name": "incident-kb", "apiType": "mcp", "dependencyKind": "data-resource",
                           "required": True,
                           "specification": {"url": "https://modelcontextprotocol.io/specification/2025-06-18"}}
    assert dep["metadata"]["labels"]["oda.tmforum.org/componentName"] == CONSUMER
    assert h.kube.depapi_status[("components", "kmc-knowledge-incident-kb")] == {
        "implementation": {"ready": True}, "depapiStatus": {"url": "https://gateway.example/kmk-knowledgemanagement/mcp"}}
    assert h.kc.mapped(CONSUMER, PROVIDER) == ["knowledge-viewer"]
    assert h.kc.mapped("dataresource-operator", PROVIDER) == ["canvasRole"]
    assert patch["phase"] == "Ready" and patch["resourceCount"] == 1
    assert {c["type"]: c["status"] for c in patch["conditions"]} == {
        "ProviderResolved": "True", "KnowledgeBaseReady": "True", "AccessGranted": "True", "Ready": "True"}
    assert patch["bindings"] == [{"name": "incident-kb", "providerComponent": PROVIDER, "kbId": "incident-kb",
                                  "secretName": "kmc-knowledge-incident-kb-binding",
                                  "dependentApiName": "kmc-knowledge-incident-kb", "access": "read", "ready": True}]
    assert again is None


def test_existing_kb_is_reused_and_knowledge_id(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.api.knowledge = [{"id": "kb-9", "name": "incident-kb", "storeKind": "hybrid"}]
            by_name = await h.reconcile(consumer_drc(item()))
            by_id = await h.reconcile(consumer_drc(item(componentName=PROVIDER, knowledgeId="kb-9"), name="by-id"))
            missing = await h.reconcile(consumer_drc(item(componentName=PROVIDER, knowledgeId="nope"), name="missing"))
            return h, by_name, by_id, missing

    h, by_name, by_id, missing = run(go())
    assert h.api.created == []
    assert by_name["bindings"][0]["kbId"] == "kb-9" and by_id["bindings"][0]["kbId"] == "kb-9"
    kb = {c["type"]: c for c in missing["conditions"]}["KnowledgeBaseReady"]
    assert (kb["status"], kb["reason"]) == ("False", "KnowledgeBaseNotFound")
    assert missing["phase"] == "Error" and missing["bindings"] == []


def test_read_write_grants_both_roles(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            await h.reconcile(consumer_drc(item(access="readWrite")))
            return h

    assert run(go()).kc.mapped(CONSUMER, PROVIDER) == ["knowledge-editor", "knowledge-viewer"]


def test_provider_not_found_or_ambiguous(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            h.kube.drcs = []
            nf = await h.reconcile(consumer_drc(item()))
            h.kube.components[("components", "km2-knowledgemanagement")] = component("km2-knowledgemanagement")
            h.kube.drcs = [provider_drc(), provider_drc("km2-knowledgemanagement")]
            amb = await h.reconcile(consumer_drc(item(), name="amb"))
            return nf, amb

    nf, amb = run(go())
    pr = {c["type"]: c for c in nf["conditions"]}["ProviderResolved"]
    assert (pr["status"], pr["reason"], nf["phase"]) == ("False", "ProviderNotFound", "Pending")
    pr = {c["type"]: c for c in amb["conditions"]}["ProviderResolved"]
    assert pr["reason"] == "ProviderAmbiguous" and "providerRef.componentName" in pr["message"]
    assert amb["phase"] == "Error"


def test_an_unbound_optional_item_does_not_block_ready(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            return await h.reconcile(consumer_drc(item(), item("extra", required=False, componentName="nobody",
                                                               create={"name": "x"})))

    patch = run(go())
    conds = {c["type"]: c for c in patch["conditions"]}
    assert conds["Ready"]["status"] == "True" and conds["ProviderResolved"]["status"] == "False"
    assert patch["resourceCount"] == 1 and patch["message"] == "1 of 2 data resource(s) bound"


def test_consumer_client_missing(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            del h.kc.clients[h.kc.by_client_id(CONSUMER)["id"]]
            return await h.reconcile(consumer_drc(item()))

    patch = run(go())
    ag = {c["type"]: c for c in patch["conditions"]}["AccessGranted"]
    assert (ag["status"], ag["reason"], patch["phase"]) == ("False", "ConsumerClientNotFound", "Pending")


def test_delete_revokes_unshared_roles_and_keeps_the_kb(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            a = consumer_drc(item(access="readWrite"), name="a")
            b = consumer_drc(item("other-kb"), name="b")
            await h.reconcile(a)
            await h.reconcile(b)
            problems = await h.binder("a").cleanup(copy.deepcopy(a))
            after_a = (h.kc.mapped(CONSUMER, PROVIDER), sorted(k[2] for k in h.kube.objects))
            h.kube.drcs = [d for d in h.kube.drcs if d["metadata"]["name"] != "a"]
            h.kube.drcs = [d for d in h.kube.drcs if d["spec"]["role"] != "provider"]      # provider CR gone too
            problems_b = await h.binder("b").cleanup(copy.deepcopy(b))
            after_b = (h.kc.mapped(CONSUMER, PROVIDER), h.kube.objects, h.kc.mapped("dataresource-operator", PROVIDER))
            return h, problems, after_a, problems_b, after_b

    h, problems, after_a, problems_b, after_b = run(go())
    assert problems == [] and problems_b == []
    # a's editor role is gone, the viewer role b still needs is kept; a's Secret and DependentAPI are gone
    assert after_a == (["knowledge-viewer"], ["b-other-kb", "b-other-kb-binding"])
    # b was the last binding and the provider CR is gone: everything revoked, including the operator's own role
    assert after_b == ([], {}, [])
    assert {k["id"] for k in h.api.knowledge} == {"incident-kb", "other-kb"}       # knowledge-bases kept


def test_system_role_kept_while_the_provider_cr_exists(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            a = consumer_drc(item(), name="a")
            await h.reconcile(a)
            await h.binder("a").cleanup(copy.deepcopy(a))
            return h

    h = run(go())
    assert h.kc.mapped("dataresource-operator", PROVIDER) == ["canvasRole"]      # provider CR still there
    assert h.kc.mapped(CONSUMER, PROVIDER) == []


def test_removed_item_is_released(tmp_path):
    async def go():
        async with Harness(tmp_path) as h:
            drc = consumer_drc(item(access="readWrite"), item("second"))
            await h.reconcile(drc)
            drc["spec"]["dependentDataResources"] = [item("second")]
            for d in h.kube.drcs:
                if d["metadata"]["name"] == drc["metadata"]["name"]:
                    d.update(copy.deepcopy(drc))
            patch = await h.reconcile(drc)
            return h, patch

    h, patch = run(go())
    assert [b["name"] for b in patch["bindings"]] == ["second"]
    assert ("Secret", "components", "kmc-knowledge-incident-kb-binding") not in h.kube.objects
    assert ("Secret", "components", "kmc-knowledge-second-binding") in h.kube.objects
    assert h.kc.mapped(CONSUMER, PROVIDER) == ["knowledge-viewer"]                # editor revoked, viewer kept


def test_pure_helpers():
    assert knowledge_body({"name": "k"}) == {"name": "k"}
    assert knowledge_body({"name": "k", "conformsTo": ["urn:x:y"]})["conformsTo"] == [{"iri": "urn:x:y"}]
    s = Settings()
    drcs = [consumer_drc(item(access="readWrite"), name="a"), {"metadata": {"name": "gone", "namespace": "components",
                                                                             "deletionTimestamp": "x"}, "spec": {}}]
    drcs[0]["status"] = {"bindings": [{"name": "incident-kb", "providerComponent": PROVIDER, "access": "readWrite"}]}
    assert needed_roles(drcs, CONSUMER, PROVIDER, s) == {"knowledge-viewer", "knowledge-editor"}
    assert needed_roles(drcs, CONSUMER, PROVIDER, s, exclude=("components", "a")) == set()
    assert needed_roles(drcs, "someone-else", PROVIDER, s) == set()
    st = consumer_status({"bindings": [{"name": "x", "kbId": "1", "ready": True}]}, 2,
                         [ItemResult("x", True, error=BindingError("KnowledgeBaseReady", "ProviderUnreachable", "down"))])
    assert st["bindings"] == [{"name": "x", "kbId": "1", "ready": False}] and st["phase"] == "Pending"
