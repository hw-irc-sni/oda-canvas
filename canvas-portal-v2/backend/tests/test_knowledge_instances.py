"""Discovery of TMFC060 Knowledge Management instances (k8s/resources/knowledge_instances.py).

ri_resources.json is a Resource Inventory payload produced by TMF639's own projection code from the TMFC060 chart
(tests/fixtures/knowledge/generate.sh), plus a ProductCatalog component that must not be listed.
"""
import copy
import json
import logging
from pathlib import Path

import httpx
import pytest

from config import get_settings
from k8s.resources import knowledge_instances as ki
from utils.exceptions import CanvasPortalError

RI_URL = "http://ri.test/tmf-api/resourceInventoryManagement/v5"
RESOURCES = json.loads((Path(__file__).parent / "fixtures" / "knowledge" / "ri_resources.json").read_text())
BASE = "/kmk-knowledgemanagement"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("RESOURCE_INVENTORY_URL", RI_URL + "/")
    ki.reset()
    yield monkeypatch
    ki.reset()
    get_settings.cache_clear()


@pytest.fixture
def ri(monkeypatch):
    """Serve RESOURCES (or a status, a body or an exception) in place of Resource Inventory; returns the calls."""
    class Calls(list):
        pass

    state = {"result": (200, RESOURCES)}
    calls = Calls()

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        result = state["result"]
        if isinstance(result, Exception):
            raise result
        status, body = result
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    calls.state = state
    return calls


def chars(resource):
    return {c["name"]: c["value"] for c in resource["resourceCharacteristic"]}


def without_port_and_path(resources):
    resources = copy.deepcopy(resources)
    for resource in resources:
        resource["resourceCharacteristic"] = [
            c for c in resource["resourceCharacteristic"] if c["name"] not in ("port", "path")]
    return resources


def test_ri_mode_lists_tmfc060_with_service_urls(ri):
    discovery = ki.list_instances()

    assert discovery.stale is False
    assert [i.name for i in discovery.instances] == ["kmk-knowledgemanagement"]
    instance = discovery.instances[0]
    assert instance.public() == {
        "name": "kmk-knowledgemanagement",
        "label": "kmk-knowledgemanagement",
        "namespace": "components",
        "version": "0.1.0",
        "resourceStatus": "available",
        "deploymentStatus": "Complete",
        "available": True,
        "apiUrl": f"http://kmk-kmapi.components.svc.cluster.local:8080{BASE}/tmf-api/knowledgeManagement/v5",
        "processUrl": f"http://kmk-kmapi.components.svc.cluster.local:8080{BASE}/tmf-api/process/v5",
        "mcpUrl": f"http://kmk-kmmcp.components.svc.cluster.local:8080{BASE}/mcp",
        "source": "ri",
    }
    assert ri[0]["url"] == f"{RI_URL}/resource"
    assert ri[0]["params"] == {"category": "ODAComponent,API"}
    assert ri[0]["timeout"]


def test_gateway_mode_uses_the_reported_urls(ri, settings):
    settings.setenv("KNOWLEDGE_URL_MODE", "gateway")

    instance = ki.list_instances().instances[0]

    assert instance.apiUrl == f"https://127.0.0.1{BASE}/tmf-api/knowledgeManagement/v5"
    assert instance.processUrl == f"https://127.0.0.1{BASE}/tmf-api/process/v5"
    assert instance.mcpUrl == f"https://127.0.0.1{BASE}/mcp"


def test_service_mode_falls_back_to_the_gateway_url_without_port_and_path(ri, caplog):
    ri.state["result"] = (200, without_port_and_path(RESOURCES))

    with caplog.at_level(logging.WARNING):
        instance = ki.list_instances().instances[0]

    assert instance.apiUrl == f"https://127.0.0.1{BASE}/tmf-api/knowledgeManagement/v5"
    assert "older than 1.2.1" in caplog.text


def test_older_inventory_without_component_id_matches_the_id_characteristic(ri):
    resources = copy.deepcopy(RESOURCES)
    for resource in resources:
        resource["resourceCharacteristic"] = [c for c in resource["resourceCharacteristic"] if c["name"] != "componentId"]
    ri.state["result"] = (200, resources)

    assert [i.name for i in ki.list_instances().instances] == ["kmk-knowledgemanagement"]


def test_an_instance_that_is_not_deployed_is_listed_but_not_available(ri):
    resources = copy.deepcopy(RESOURCES)
    resources[0]["resourceStatus"] = "installing"
    ri.state["result"] = (200, resources)

    instance = ki.list_instances().instances[0]

    assert instance.resourceStatus == "installing"
    assert instance.available is False


def test_an_instance_without_its_knowledge_api_is_not_available(ri):
    resources = [r for r in RESOURCES if r["id"] != "kmk-knowledgemanagement-knowledgemanagement"]
    ri.state["result"] = (200, resources)

    instance = ki.list_instances().instances[0]

    assert instance.apiUrl is None
    assert instance.processUrl is not None
    assert instance.available is False


@pytest.mark.parametrize("name, value", [
    ("implementation", "evil.example.com/x"),
    ("implementation", "kmk-kmapi.other"),
    ("port", 0),
    ("port", "8080abc"),
    ("port", True),
    ("path", "/a/../admin"),
    ("path", "relative"),
    ("path", "/a?b=c"),
    ("path", "/a#frag"),
])
def test_untrusted_implementation_values_are_not_used_to_build_a_url(ri, name, value):
    resources = copy.deepcopy(RESOURCES)
    api = next(r for r in resources if r["id"] == "kmk-knowledgemanagement-knowledgemanagement")
    for c in api["resourceCharacteristic"]:
        if c["name"] == name:
            c["value"] = value
    ri.state["result"] = (200, resources)

    instance = ki.list_instances().instances[0]

    assert instance.apiUrl == f"https://127.0.0.1{BASE}/tmf-api/knowledgeManagement/v5"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:pw@gw/x", "https://gw/x?token=1", "ftp://gw/x"])
def test_an_unsafe_gateway_url_is_dropped(ri, settings, url):
    settings.setenv("KNOWLEDGE_URL_MODE", "gateway")
    resources = copy.deepcopy(RESOURCES)
    api = next(r for r in resources if r["id"] == "kmk-knowledgemanagement-knowledgemanagement")
    for c in api["resourceCharacteristic"]:
        if c["name"] == "url":
            c["value"] = url
    ri.state["result"] = (200, resources)

    instance = ki.list_instances().instances[0]

    assert instance.apiUrl is None
    assert instance.available is False


def test_invalid_component_names_and_namespaces_are_skipped(ri):
    resources = copy.deepcopy(RESOURCES)
    resources[0]["name"] = resources[0]["id"] = "Bad_Name"
    second = copy.deepcopy(RESOURCES[0])
    second["name"] = second["id"] = "km2-knowledgemanagement"
    for c in second["resourceCharacteristic"]:
        if c["name"] == "namespace":
            c["value"] = "bad.namespace"
    ri.state["result"] = (200, resources + [second])

    assert ki.list_instances().instances == []


def test_cache_ttl_is_honoured(ri, settings):
    clock = {"now": 1000.0}
    settings.setattr(ki.time, "time", lambda: clock["now"])
    settings.setattr("utils.cache.time.time", lambda: clock["now"])

    ki.list_instances()
    clock["now"] += 29
    ki.list_instances()
    assert len(ri) == 1

    clock["now"] += 2
    ki.list_instances()
    assert len(ri) == 2


def test_the_cache_ttl_is_configurable(ri, settings):
    settings.setenv("KNOWLEDGE_CACHE_TTL_SECONDS", "0")
    clock = {"now": 1000.0}
    settings.setattr("utils.cache.time.time", lambda: clock["now"])

    ki.list_instances()
    clock["now"] += 1
    ki.list_instances()

    assert len(ri) == 2


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), (500, {}), (200, {"not": "a list"})])
def test_the_last_good_list_is_served_stale_while_inventory_fails(ri, settings, failure):
    clock = {"now": 1000.0}
    settings.setattr(ki.time, "time", lambda: clock["now"])
    settings.setattr("utils.cache.time.time", lambda: clock["now"])
    ki.list_instances()

    ri.state["result"] = failure
    clock["now"] += 31
    discovery = ki.list_instances()

    assert discovery.stale is True
    assert [i.name for i in discovery.instances] == ["kmk-knowledgemanagement"]

    clock["now"] += 600
    with pytest.raises(CanvasPortalError) as error:
        ki.list_instances()
    assert error.value.status_code == 502


def test_failure_without_a_good_list_is_a_502_that_logs_no_detail(ri, caplog):
    ri.state["result"] = httpx.ConnectError("refused by http://secret-host/")

    with caplog.at_level(logging.DEBUG), pytest.raises(CanvasPortalError) as error:
        ki.list_instances()

    assert error.value.status_code == 502
    assert "secret-host" not in caplog.text
    assert "secret-host" not in error.value.message


def test_unconfigured_inventory_is_a_503(ri, settings):
    settings.setenv("RESOURCE_INVENTORY_URL", "")

    with pytest.raises(CanvasPortalError) as error:
        ki.list_instances()

    assert error.value.status_code == 503
    assert ri == []


def test_get_instance(ri):
    assert ki.get_instance("kmk-knowledgemanagement").name == "kmk-knowledgemanagement"
    with pytest.raises(CanvasPortalError) as error:
        ki.get_instance("missing")
    assert error.value.status_code == 404


STATIC = [
    {"name": "kmk-knowledgemanagement", "label": "Knowledge (lab)", "namespace": "components",
     "apiUrl": "http://kmk-kmapi.components.svc.cluster.local:8080/kmk-knowledgemanagement/tmf-api/knowledgeManagement/v5/",
     "processUrl": "http://kmk-kmapi.components.svc.cluster.local:8080/kmk-knowledgemanagement/tmf-api/process/v5",
     "mcpUrl": "http://kmk-kmmcp.components.svc.cluster.local:8080/kmk-knowledgemanagement/mcp"},
    {"name": "remote-km", "apiUrl": "https://km.example.org/tmf-api/knowledgeManagement/v5"},
]


def test_static_mode_reads_the_json_and_never_calls_inventory(ri, settings):
    settings.setenv("KNOWLEDGE_DISCOVERY", "static")
    settings.setenv("KNOWLEDGE_INSTANCES_JSON", json.dumps(STATIC))

    instances = ki.list_instances().instances

    assert ri == []
    assert [i.public() for i in instances] == [
        {"name": "kmk-knowledgemanagement", "label": "Knowledge (lab)", "namespace": "components", "version": None,
         "resourceStatus": None, "deploymentStatus": None, "available": True,
         "apiUrl": "http://kmk-kmapi.components.svc.cluster.local:8080/kmk-knowledgemanagement/tmf-api/knowledgeManagement/v5",
         "processUrl": STATIC[0]["processUrl"], "mcpUrl": STATIC[0]["mcpUrl"], "source": "static"},
        {"name": "remote-km", "label": "remote-km", "namespace": None, "version": None, "resourceStatus": None,
         "deploymentStatus": None, "available": True, "apiUrl": STATIC[1]["apiUrl"], "processUrl": None,
         "mcpUrl": None, "source": "static"},
    ]


@pytest.mark.parametrize("raw", ["not json", '{"name": "x"}', "null", "[[" * 10000], ids=["text", "object", "null", "deep"])
def test_static_mode_with_invalid_json_has_no_instances(ri, settings, raw):
    settings.setenv("KNOWLEDGE_DISCOVERY", "static")
    settings.setenv("KNOWLEDGE_INSTANCES_JSON", raw)

    assert ki.list_instances().instances == []


def test_static_mode_skips_invalid_entries(ri, settings, caplog):
    settings.setenv("KNOWLEDGE_DISCOVERY", "static")
    settings.setenv("KNOWLEDGE_INSTANCES_JSON", json.dumps([
        "not an object",
        {"name": "no-url"},
        {"name": "Bad_Name", "apiUrl": "http://a/b"},
        {"name": "creds", "apiUrl": "https://u:p@a/b"},
        {"name": "js", "apiUrl": "javascript:alert(1)"},
        {"name": "ok", "apiUrl": "http://a/b", "mcpUrl": "ftp://a/mcp"},
        {"name": "ok", "apiUrl": "http://c/d"},
    ]))

    with caplog.at_level(logging.WARNING):
        instances = ki.list_instances().instances

    assert [(i.name, i.apiUrl, i.mcpUrl) for i in instances] == [("ok", "http://a/b", None)]
    assert "u:p" not in caplog.text
