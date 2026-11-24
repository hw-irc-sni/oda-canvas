"""Target names and entries, and the merge into mcp.targets."""

from targets import desired_targets, merge, target_for, target_name


def mcp_api(name="ctk-knowledgemanagement-knowledgemcp", ns="components", ready=True, api_type="mcp"):
    return {"metadata": {"name": name, "namespace": ns},
            "spec": {"apiType": api_type, "implementation": "ctk-kmmcp", "port": 8080, "path": "/ctk-knowledgemanagement/mcp"},
            "status": {"implementation": {"ready": ready}}}


MANUAL = {"name": "test-mcp", "mcp": {"host": "http://agentgateway-test-mcp-server:8000/mcp"}}


def test_name_and_entry():
    assert target_name("components", "x-knowledgemcp") == "oda-components-x-knowledgemcp"
    assert target_for(mcp_api()) == {
        "name": "oda-components-ctk-knowledgemanagement-knowledgemcp",
        "mcp": {"host": "http://ctk-kmmcp.components.svc.cluster.local:8080/ctk-knowledgemanagement/mcp"},
        "policies": {"backendAuth": {"passthrough": {}}}}


def test_only_ready_mcp_apis_with_a_service():
    assert target_for(mcp_api(ready=False)) is None
    assert target_for(mcp_api(api_type="openapi")) is None
    broken = mcp_api(); broken["spec"].pop("implementation")
    assert target_for(broken) is None


def test_desired_targets_sorted_and_namespace_filtered():
    apis = [mcp_api("b-mcp"), mcp_api("a-mcp"), mcp_api("c-mcp", ns="odacompns-1"), mcp_api("d-mcp", ns="other")]
    assert [t["name"] for t in desired_targets(apis)] == [
        "oda-components-a-mcp", "oda-components-b-mcp", "oda-odacompns-1-c-mcp", "oda-other-d-mcp"]
    assert [t["name"] for t in desired_targets(apis, ("components", "odacompns-*"))] == [
        "oda-components-a-mcp", "oda-components-b-mcp", "oda-odacompns-1-c-mcp"]


def test_merge_keeps_foreign_targets_in_place():
    doc = {"binds": [1], "mcp": {"gateways": "default", "statefulMode": "stateless",
                                 "targets": [MANUAL, {"name": "oda-components-gone"}, {"name": "manual-2"}]}}
    new = target_for(mcp_api())
    out = merge(doc, [new])
    assert out["mcp"]["targets"] == [MANUAL, {"name": "manual-2"}, new]
    assert out["binds"] == [1] and out["mcp"]["gateways"] == "default"
    assert doc["mcp"]["targets"][1] == {"name": "oda-components-gone"}      # the input is not changed
    assert merge(out, [])["mcp"]["targets"] == [MANUAL, {"name": "manual-2"}]


def test_merge_creates_an_mcp_section_only_when_needed():
    assert merge({"binds": []}, []) == {"binds": []}
    out = merge({"binds": []}, [target_for(mcp_api())], "default")
    assert out["mcp"]["gateways"] == "default" and out["mcp"]["failureMode"] == "failOpen"
    assert [t["name"] for t in out["mcp"]["targets"]] == ["oda-components-ctk-knowledgemanagement-knowledgemcp"]
