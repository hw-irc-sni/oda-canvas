"""The Knowledge router: instances with capabilities, and the proxy to TMFC060 instances.

The instances come from static discovery; the OAS each one serves is the real TMFxxx and TMF701 OAS from the
repository, and the component is an httpx MockTransport.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import get_settings
from k8s.resources import knowledge_instances, knowledge_spec
from routers import knowledge
from utils.cache import knowledge_spec_cache
from utils.exceptions import register_exception_handlers

API_DIR = Path(__file__).resolve().parents[3] / "source/components/TMFC060-KnowledgeManagement/knowledgeApiMicroservice/api"
KNOWLEDGE_OAS = (API_DIR / "TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml").read_bytes()
PROCESS_OAS = (API_DIR / "TMF701-ProcessManagement-v5.0.0-subset.oas.yaml").read_bytes()

NAME = "km-knowledgemanagement"
API = f"http://km-kmapi.components.svc.cluster.local:8080/{NAME}/tmf-api/knowledgeManagement/v5"
PROCESS = f"http://km-kmapi.components.svc.cluster.local:8080/{NAME}/tmf-api/process/v5"
TOKEN = "user-token-abc.def.ghi"
INSTANCES = [{"name": NAME, "label": "Knowledge", "namespace": "components", "apiUrl": API, "processUrl": PROCESS,
              "mcpUrl": f"http://km-kmmcp.components.svc.cluster.local:8080/{NAME}/mcp"}]

app = FastAPI()
register_exception_handlers(app)
app.include_router(knowledge.router)
client = TestClient(app, raise_server_exceptions=False)


def claims(*roles, client_id=NAME):
    return {"sub": "u1", "preferred_username": "user", "resource_access": {client_id: {"roles": list(roles)}} if roles else {}}


def act_as(token_claims):
    app.dependency_overrides[knowledge.get_caller] = lambda: knowledge.Caller(token=TOKEN, claims=token_claims)


class Upstream:
    """The component: records each request and answers from a {(method, path): response} table."""

    def __init__(self):
        self.requests = []
        self.routes = {}
        self.default = httpx.Response(200, json={"ok": True})

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        result = self.routes.get((request.method, request.url.path), self.default)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("KNOWLEDGE_DISCOVERY", "static")
    monkeypatch.setenv("KNOWLEDGE_INSTANCES_JSON", json.dumps(INSTANCES))
    knowledge_instances.reset()
    knowledge_spec_cache.clear()
    spec_calls = []

    def serve_oas(request):
        spec_calls.append(str(request.url))
        if str(request.url) == f"{API}/openapi.yaml":
            return httpx.Response(200, content=KNOWLEDGE_OAS, headers={"content-type": "application/yaml"})
        if str(request.url) == f"{PROCESS}/openapi.yaml":
            return httpx.Response(200, content=PROCESS_OAS, headers={"content-type": "application/yaml"})
        return httpx.Response(404)

    monkeypatch.setattr(knowledge_spec, "transport", httpx.MockTransport(serve_oas))
    upstream = Upstream()
    monkeypatch.setattr(knowledge, "transport", httpx.MockTransport(upstream.handler))
    act_as(claims("knowledge-viewer"))
    yield {"upstream": upstream, "spec_calls": spec_calls}
    app.dependency_overrides.clear()
    knowledge_spec_cache.clear()
    get_settings.cache_clear()


@pytest.fixture
def upstream(setup):
    return setup["upstream"]


def instance_view():
    response = client.get("/api/knowledge/instances")
    assert response.status_code == 200
    return response.json()["instances"][0]


# --- instances and capabilities ---------------------------------------------------------------------------------

def test_instances_list_the_instance_with_its_urls(setup):
    body = client.get("/api/knowledge/instances").json()

    assert body["discovery"] == "static"
    assert body["stale"] is False
    view = body["instances"][0]
    assert {k: view[k] for k in ("name", "label", "apiUrl", "processUrl", "available")} == {
        "name": NAME, "label": "Knowledge", "apiUrl": API, "processUrl": PROCESS, "available": True}
    assert view["capabilitiesError"] is None
    assert setup["spec_calls"] == [f"{API}/openapi.yaml", f"{PROCESS}/openapi.yaml"]


def test_the_oas_is_cached(setup):
    client.get("/api/knowledge/instances")
    client.get("/api/knowledge/instances")

    assert len(setup["spec_calls"]) == 2


def test_viewer_capabilities():
    caps = instance_view()["capabilities"]

    assert caps["roles"] == ["knowledge-viewer"]
    ops = caps["operations"]
    assert ops["listKnowledge"] and ops["retrieveKnowledge"] and ops["searchKnowledge"] and ops["listProcess"]
    assert ops["listKnowledgeSpecification"] and ops["retrieveKnowledgeTerm"]
    assert not ops["createKnowledge"] and not ops["createKnowledgeDocument"] and not ops["createKnowledgeSpecification"]
    assert not ops["patchKnowledgeSpecification"] and not ops["createKnowledgeSpecificationReview"]
    assert caps["transitions"] == {"knowledge": [], "knowledgeSpecification": []}
    assert caps["reviewKinds"] == []


def test_excluded_operations_are_not_capabilities():
    ops = instance_view()["capabilities"]["operations"]

    for excluded in ("createHub", "retrieveHub", "deleteHub", "retrieveSchema", "receiveSourceEvent", "knowledgeCreateEvent"):
        assert excluded not in ops


def test_knowledge_editor_capabilities():
    act_as(claims("knowledge-editor"))

    caps = instance_view()["capabilities"]

    ops = caps["operations"]
    assert ops["createKnowledge"] and ops["createKnowledgeDocument"] and ops["createKnowledgeIngestion"]
    assert ops["replaceKnowledgeGraph"] and ops["deleteKnowledge"] and ops["patchKnowledgeProposal"]
    assert not ops["createKnowledgeSpecification"]
    assert caps["transitions"]["knowledge"] == [{"from": "draft", "to": "proposed"}, {"from": "rejected", "to": "draft"}]
    assert caps["transitions"]["knowledgeSpecification"] == []


def test_ontology_editor_capabilities():
    act_as(claims("ontology-editor"))

    caps = instance_view()["capabilities"]

    ops = caps["operations"]
    assert ops["createKnowledgeSpecification"] and ops["patchKnowledgeSpecification"]
    assert ops["createKnowledgeSpecificationMapping"] and ops["replaceKnowledgeSpecificationContent"]
    assert not ops["listKnowledge"] and not ops["createKnowledge"]
    assert caps["transitions"]["knowledgeSpecification"] == [
        {"from": "draft", "to": "proposed"}, {"from": "rejected", "to": "draft"}]
    assert caps["reviewKinds"] == ["proposal"]


def test_reviewer_capabilities():
    act_as(claims("ontology-reviewer"))

    caps = instance_view()["capabilities"]

    assert caps["operations"]["createKnowledgeSpecificationReview"]
    assert not caps["operations"]["createKnowledgeSpecification"]
    assert caps["transitions"]["knowledgeSpecification"] == [{"from": "proposed", "to": "inReview"}]
    assert caps["transitions"]["knowledge"] == [{"from": "proposed", "to": "inReview"}]
    assert caps["reviewKinds"] == ["comment"]


def test_approver_capabilities():
    act_as(claims("ontology-approver"))

    caps = instance_view()["capabilities"]

    assert caps["operations"]["deleteKnowledgeSpecification"] and caps["operations"]["patchKnowledgeSpecification"]
    assert {"from": "inReview", "to": "agreed"} in caps["transitions"]["knowledgeSpecification"]
    assert {"from": "published", "to": "deprecated"} in caps["transitions"]["knowledgeSpecification"]
    assert {"from": "draft", "to": "proposed"} not in caps["transitions"]["knowledgeSpecification"]
    assert caps["reviewKinds"] == ["ruling"]


def test_several_roles_combine():
    act_as(claims("knowledge-viewer", "ontology-editor", "ontology-reviewer"))

    caps = instance_view()["capabilities"]

    assert caps["roles"] == ["knowledge-viewer", "ontology-editor", "ontology-reviewer"]
    assert caps["operations"]["listKnowledge"] and caps["operations"]["createKnowledgeSpecification"]
    assert caps["reviewKinds"] == ["comment", "proposal"]


def test_the_canvas_system_role_counts_as_canvas_system_role(setup, monkeypatch):
    act_as(claims("canvasRole"))
    caps = instance_view()["capabilities"]
    assert caps["operations"]["createKnowledge"] and caps["operations"]["listProcess"]
    assert not caps["operations"]["createKnowledgeSpecification"]

    act_as(claims("canvasSystemRole"))
    assert not any(instance_view()["capabilities"]["operations"].values())

    monkeypatch.setenv("KNOWLEDGE_CANVAS_SYSTEM_ROLE", "system")
    get_settings.cache_clear()
    act_as(claims("system"))
    assert instance_view()["capabilities"]["operations"]["createKnowledge"]


def test_no_roles_and_other_clients_roles_give_no_capabilities():
    for token_claims in (claims(), claims("knowledge-editor", client_id="other-km"), {"sub": "u", "resource_access": "x"}):
        act_as(token_claims)
        caps = instance_view()["capabilities"]
        assert caps["roles"] == []
        assert not any(caps["operations"].values())


@pytest.mark.parametrize("oas", [httpx.Response(500), httpx.Response(200, content=b"- just\n- a list\n"),
                                 httpx.Response(200, content=b"{not: [yaml"), httpx.Response(302, headers={"location": "http://x"}),
                                 httpx.Response(200, content=b"paths: {}\n")],
                         ids=["5xx", "list", "invalid", "redirect", "no-operations"])
def test_an_unusable_oas_lists_the_instance_without_capabilities(monkeypatch, upstream, oas):
    monkeypatch.setattr(knowledge_spec, "transport", httpx.MockTransport(lambda request: oas))

    view = instance_view()

    assert view["capabilities"] is None
    assert "unavailable" in view["capabilitiesError"]
    response = client.get(f"/api/knowledge/{NAME}/knowledge")
    assert response.status_code == 502
    assert upstream.requests == []


def test_an_oversized_oas_is_refused(monkeypatch):
    big = KNOWLEDGE_OAS + b"\n# " + b"x" * (knowledge_spec.MAX_SPEC_BYTES + 1) + b"\n"
    monkeypatch.setattr(knowledge_spec, "transport", httpx.MockTransport(lambda request: httpx.Response(200, content=big)))

    assert instance_view()["capabilities"] is None


def test_a_missing_tmf701_oas_keeps_the_tmfxxx_operations(monkeypatch):
    def serve(request):
        if str(request.url).startswith(API):
            return httpx.Response(200, content=KNOWLEDGE_OAS)
        return httpx.Response(404)

    monkeypatch.setattr(knowledge_spec, "transport", httpx.MockTransport(serve))

    ops = instance_view()["capabilities"]["operations"]
    assert ops["listKnowledge"] and "listProcess" not in ops


def test_discovery_failures_keep_their_status(monkeypatch):
    def unavailable():
        raise knowledge_instances.CanvasPortalError("Resource Inventory is unreachable", 502)

    monkeypatch.setattr(knowledge_instances, "list_instances", unavailable)

    assert client.get("/api/knowledge/instances").status_code == 502


def test_stale_discovery_is_reported(monkeypatch):
    instance = knowledge_instances._static_instances(json.dumps(INSTANCES))
    monkeypatch.setattr(knowledge_instances, "list_instances",
                        lambda: knowledge_instances.Discovery(instance, stale=True))

    assert client.get("/api/knowledge/instances").json()["stale"] is True


def test_instances_need_a_token():
    app.dependency_overrides.clear()

    assert client.get("/api/knowledge/instances").status_code == 403  # HTTPBearer without credentials


# --- proxy ------------------------------------------------------------------------------------------------------

def test_the_users_token_is_forwarded_with_the_query(upstream):
    response = client.get(f"/api/knowledge/{NAME}/knowledge?limit=5&offset=10&fields=id,name",
                          headers={"accept": "application/json", "cookie": "canvas_portal_refresh=secret",
                                   "x-forwarded-for": "1.2.3.4"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    sent = upstream.requests[0]
    assert str(sent.url) == f"{API}/knowledge?limit=5&offset=10&fields=id%2Cname"
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.headers["accept"] == "application/json"
    assert "cookie" not in sent.headers and "x-forwarded-for" not in sent.headers


def test_tmf701_calls_go_to_the_process_api(upstream):
    assert client.get(f"/api/knowledge/{NAME}/process/p-1").status_code == 200

    assert str(upstream.requests[0].url) == f"{PROCESS}/process/p-1"


def test_a_json_body_is_forwarded(upstream):
    act_as(claims("knowledge-editor"))
    upstream.default = httpx.Response(201, json={"id": "kb1"}, headers={"location": f"{API}/knowledge/kb1"})

    response = client.post(f"/api/knowledge/{NAME}/knowledge", json={"name": "kb1"})

    assert response.status_code == 201
    assert response.json() == {"id": "kb1"}
    assert "location" not in response.headers
    sent = upstream.requests[0]
    assert json.loads(sent.content) == {"name": "kb1"}
    assert sent.headers["content-type"] == "application/json"


def test_path_parameters_are_encoded(upstream):
    assert client.get(f"/api/knowledge/{NAME}/knowledgeSpecification/tio:Region%20x").status_code == 200

    assert upstream.requests[0].url.raw_path.decode() == f"/{NAME}/tmf-api/knowledgeManagement/v5/knowledgeSpecification/tio:Region%20x"


def test_response_headers_are_filtered(upstream):
    upstream.default = httpx.Response(200, json=[], headers={
        "x-total-count": "7", "x-result-count": "0", "etag": "W/1", "set-cookie": "a=b", "server": "uvicorn",
        "x-internal": "1"})

    response = client.get(f"/api/knowledge/{NAME}/knowledge")

    assert response.headers["x-total-count"] == "7" and response.headers["etag"] == "W/1"
    assert "set-cookie" not in response.headers and "x-internal" not in response.headers


@pytest.mark.parametrize("status", [400, 403, 404, 409, 413, 422])
def test_client_errors_pass_through_with_their_body(upstream, status):
    problem = {"code": str(status), "reason": "Reason", "message": "Detail from the component"}
    upstream.default = httpx.Response(status, json=problem, headers={"content-type": "application/problem+json"})

    response = client.get(f"/api/knowledge/{NAME}/knowledge/kb1")

    assert response.status_code == status
    assert response.json() == problem
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("answer", [
    httpx.Response(401, json={"message": "Invalid token: expired"}),
    httpx.Response(500, json={"message": "Traceback secret"}),
    httpx.Response(503),
    httpx.Response(302, headers={"location": "http://evil/"}),
    httpx.ReadTimeout("slow"),
    httpx.ConnectError("refused by km-kmapi"),
], ids=["401", "500", "503", "302", "timeout", "unreachable"])
def test_upstream_failures_become_502_without_detail(upstream, answer, caplog):
    upstream.default = answer

    with caplog.at_level(logging.DEBUG):
        response = client.get(f"/api/knowledge/{NAME}/knowledge")

    assert response.status_code == 502
    for leaked in ("expired", "Traceback", "evil", "refused", TOKEN):
        assert leaked not in response.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("method, path", [
    ("GET", "unknown"), ("GET", "knowledge/kb1/unknown"), ("PUT", "knowledge"), ("GET", "hub/h1"),
    ("POST", "hub"), ("POST", "listener/knowledgeCreateEvent"), ("GET", "schema/Knowledge"),
    ("POST", "eventSource/TMF639/t0k3n"), ("GET", "knowledge/%2E%2E/admin"), ("GET", "knowledge/%2E/kb1"),
    ("GET", "knowledge/kb1/%2E%2E"),
    ("GET", "knowledge//graph"), ("GET", "openapi.yaml"),
])
def test_only_oas_operations_are_forwarded(upstream, method, path):
    response = client.request(method, f"/api/knowledge/{NAME}/{path}")

    assert response.status_code == 404
    assert upstream.requests == []


def test_an_unknown_instance_is_404(upstream):
    for name in ("other-km", "Bad_Name"):
        assert client.get(f"/api/knowledge/{name}/knowledge").status_code == 404
    assert upstream.requests == []


def test_a_user_without_a_role_on_the_instance_is_refused_without_a_call(upstream):
    act_as(claims("knowledge-editor", client_id="other-km"))

    response = client.get(f"/api/knowledge/{NAME}/knowledge")

    assert response.status_code == 403
    assert upstream.requests == []


def test_a_role_the_operation_does_not_list_is_left_to_the_component(upstream):
    upstream.default = httpx.Response(403, json={"message": "Requires knowledge-editor"})

    response = client.post(f"/api/knowledge/{NAME}/knowledge", json={"name": "kb"})

    assert response.status_code == 403
    assert len(upstream.requests) == 1


def test_a_declared_body_over_the_cap_is_413_without_a_call(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_BODY_BYTES", "100")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))

    response = client.post(f"/api/knowledge/{NAME}/knowledge", content=b"x" * 101, headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert upstream.requests == []


def test_a_streamed_body_over_the_cap_is_413(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_BODY_BYTES", "100")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))

    def chunks():
        for _ in range(5):
            yield b"x" * 40

    response = client.post(f"/api/knowledge/{NAME}/knowledge", content=chunks(), headers={"content-type": "text/turtle"})

    assert response.status_code == 413


def test_a_body_at_the_cap_is_forwarded(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_BODY_BYTES", "100")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))

    response = client.put(f"/api/knowledge/{NAME}/knowledge/kb1/graph", content=b"x" * 100, headers={"content-type": "text/turtle"})

    assert response.status_code == 200
    assert upstream.requests[0].content == b"x" * 100
    assert upstream.requests[0].headers["content-type"] == "text/turtle"


def test_a_malformed_content_length_is_400(upstream):
    act_as(claims("knowledge-editor"))

    response = client.post(f"/api/knowledge/{NAME}/knowledge", content=b"{}", headers={"content-length": "-1"})

    assert response.status_code in (400, 413)
    assert upstream.requests == []
