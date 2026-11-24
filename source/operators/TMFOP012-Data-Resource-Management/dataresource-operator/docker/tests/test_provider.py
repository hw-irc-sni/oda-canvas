"""The pure provider functions: ExposedAPI resolution and the providedResources list."""

import pytest

from fake_provider import BASE_PATH, PROVIDER, exposed_api
from provider import ProviderApi, ProviderError, exposed_api_names, provided_resources, resolve_api, summary

SPEC = {"provider": "odaComponent", "role": "provider", "providedDataResources": [
    {"name": "knowledge-vectors", "type": "vectorStore", "exposedAPI": "knowledgemanagement", "mcpAPI": "knowledgemcp"},
    {"name": "knowledge-graph", "type": "knowledgeGraph", "exposedAPI": "knowledgemanagement"},
    {"name": "cache", "type": "cache"}]}


def test_exposed_api_names_are_distinct():
    assert exposed_api_names(SPEC) == ["knowledgemanagement"]
    assert exposed_api_names({}) == []


def test_resolve_api_builds_the_service_and_public_urls():
    api = resolve_api("knowledgemanagement", PROVIDER, "components", [exposed_api(8080)])
    assert api.internal_url == f"http://kmk-kmapi.components.svc.cluster.local:8080{BASE_PATH}"
    assert api.public_url == f"https://gateway.example{BASE_PATH}"


def test_resolve_api_errors():
    with pytest.raises(ProviderError) as e:
        resolve_api("knowledgemanagement", PROVIDER, "components", [])
    assert e.value.reason == "ExposedAPINotFound"
    with pytest.raises(ProviderError) as e:   # another component's API of the same name
        resolve_api("knowledgemanagement", PROVIDER, "components", [exposed_api(8080, component="other")])
    assert e.value.reason == "ExposedAPINotFound"
    with pytest.raises(ProviderError) as e:
        resolve_api("knowledgemanagement", PROVIDER, "components", [exposed_api(8080, ready=False)])
    assert e.value.reason == "ExposedAPINotReady"


def test_provided_resources():
    api = ProviderApi("knowledgemanagement", "http://x", "https://gw/km/v5")
    kbs = [{"id": "kb-b", "name": "B", "storeKind": "hybrid", "lifecycleStatus": "draft"},
           {"id": "kb-a", "name": "A", "storeKind": "graph", "lifecycleStatus": "published"},
           {"id": "kb c", "storeKind": "vector"}]
    mods = [{"id": "tio-core", "name": "TIO core", "layer": "Telecom", "lifecycleStatus": "published"}]
    out = provided_resources(api, kbs, mods)
    assert [r["id"] for r in out] == ["kb c", "kb-a", "kb-b", "tio-core"]
    assert out[0] == {"kind": "knowledge", "id": "kb c", "name": "kb c", "type": "vectorStore",
                      "storeKind": "vector", "href": "https://gw/km/v5/knowledge/kb%20c"}
    assert out[1]["type"] == "knowledgeGraph" and out[1]["lifecycleStatus"] == "published"
    assert "type" not in out[2] and out[2]["storeKind"] == "hybrid"            # hybrid: both stores, no type
    assert out[3] == {"kind": "knowledgeSpecification", "id": "tio-core", "name": "TIO core",
                      "type": "knowledgeGraph", "layer": "Telecom", "lifecycleStatus": "published",
                      "href": "https://gw/km/v5/knowledgeSpecification/tio-core"}
    assert summary(out) == "3 knowledge-bases and 1 ontology module"
    assert summary([]) == "0 knowledge-bases and 0 ontology modules"
