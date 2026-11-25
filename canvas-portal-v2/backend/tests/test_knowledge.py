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


# --- Federated Ontology routes (#74) ----------------------------------------------------------------------------

MODULE = {"id": "mod", "name": "mod", "iri": "https://example.org/mod", "layer": "Telecom", "specKind": "ontology",
          "source": {"kind": "upload"}}
TURTLE = b"@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<https://example.org/mod> a owl:Ontology .\n"
JSONLD = b'{"@id": "https://example.org/x", "@type": "https://example.org/T"}'
MERGE = "application/merge-patch+json"
SPEC = "/knowledgeSpecification"

# operationId, role, method, portal path (after the instance), body, content type, upstream path (after the API base),
# upstream status, upstream response body, upstream response content type
ONTOLOGY_ROUTES = [
    ("listKnowledgeSpecification", "knowledge-viewer", "GET", "knowledgeSpecification?limit=10&offset=0&sort=name&fields=id,name",
     None, None, f"{SPEC}?limit=10&offset=0&sort=name&fields=id%2Cname", 200, b"[]", "application/json"),
    ("createKnowledgeSpecification", "ontology-editor", "POST", "knowledgeSpecification", json.dumps(MODULE).encode(),
     "application/json", SPEC, 201, json.dumps({**MODULE, "lifecycleStatus": "draft"}).encode(), "application/json"),
    ("retrieveKnowledgeSpecification", "knowledge-viewer", "GET", "knowledgeSpecification/mod?fields=lifecycleStatus",
     None, None, f"{SPEC}/mod?fields=lifecycleStatus", 200, b'{"id": "mod"}', "application/json"),
    ("patchKnowledgeSpecification", "ontology-editor", "PATCH", "knowledgeSpecification/mod",
     b'{"lifecycleStatus": "proposed"}', MERGE, f"{SPEC}/mod", 200, b'{"lifecycleStatus": "proposed"}', "application/json"),
    ("deleteKnowledgeSpecification", "ontology-approver", "DELETE", "knowledgeSpecification/mod", None, None,
     f"{SPEC}/mod", 204, b"", None),
    ("retrieveKnowledgeSpecificationContent", "knowledge-viewer", "GET", "knowledgeSpecification/mod/content", None, None,
     f"{SPEC}/mod/content", 200, TURTLE, "text/turtle; charset=utf-8"),
    ("replaceKnowledgeSpecificationContent", "ontology-editor", "PUT", "knowledgeSpecification/mod/content", TURTLE,
     "text/turtle", f"{SPEC}/mod/content", 204, b"", None),
    ("listKnowledgeSpecificationMapping", "knowledge-viewer", "GET", "knowledgeSpecification/mod/mapping?limit=5", None, None,
     f"{SPEC}/mod/mapping?limit=5", 200, b"[]", "application/json"),
    ("createKnowledgeSpecificationMapping", "ontology-editor", "POST", "knowledgeSpecification/mod/mapping",
     b'{"subject": "tio:Region", "predicate": "owl:equivalentClass", "object": "fto:Area"}', "application/json",
     f"{SPEC}/mod/mapping", 201, b'{"id": "m1"}', "application/json"),
    ("retrieveKnowledgeSpecificationMapping", "knowledge-viewer", "GET", "knowledgeSpecification/mod/mapping/m1", None, None,
     f"{SPEC}/mod/mapping/m1", 200, b'{"id": "m1"}', "application/json"),
    ("patchKnowledgeSpecificationMapping", "ontology-reviewer", "PATCH", "knowledgeSpecification/mod/mapping/m1",
     b'{"confidence": 0.9}', MERGE, f"{SPEC}/mod/mapping/m1", 200, b'{"id": "m1"}', "application/json"),
    ("deleteKnowledgeSpecificationMapping", "ontology-editor", "DELETE", "knowledgeSpecification/mod/mapping/m1", None, None,
     f"{SPEC}/mod/mapping/m1", 204, b"", None),
    ("listKnowledgeSpecificationReview", "knowledge-viewer", "GET", "knowledgeSpecification/mod/review", None, None,
     f"{SPEC}/mod/review", 200, b"[]", "application/json"),
    ("createKnowledgeSpecificationReview", "ontology-reviewer", "POST", "knowledgeSpecification/mod/review",
     b'{"kind": "comment", "target": {"kind": "module", "id": "mod"}, "text": "ok"}', "application/json",
     f"{SPEC}/mod/review", 201, b'{"id": "r1"}', "application/json"),
    ("listKnowledgeSpecificationTransition", "knowledge-viewer", "GET", "knowledgeSpecification/mod/transition?limit=5", None,
     None, f"{SPEC}/mod/transition?limit=5", 200, b"[]", "application/json"),
    ("retrieveKnowledgeSpecificationValidation", "knowledge-viewer", "GET", "knowledgeSpecification/mod/validation", None,
     None, f"{SPEC}/mod/validation", 200, b'{"conforms": true}', "application/json"),
    ("runKnowledgeSpecificationValidation", "ontology-editor", "POST", "knowledgeSpecification/mod/validation", None, None,
     f"{SPEC}/mod/validation", 200, b'{"conforms": true}', "application/json"),
    ("validateDocument", "knowledge-viewer", "POST", "knowledgeSpecification/mod/validation/document", TURTLE,
     "text/turtle", f"{SPEC}/mod/validation/document", 200, b'{"conforms": false}', "application/json"),
    ("validateDocument", "knowledge-viewer", "POST", "knowledgeSpecification/mod/validation/document", JSONLD,
     "application/ld+json", f"{SPEC}/mod/validation/document", 200, b'{"conforms": true}', "application/json"),
    ("retrieveKnowledgeTerm", "knowledge-viewer", "GET", "knowledgeTerm?q=region&limit=20&includeUnpublished=true", None,
     None, "/knowledgeTerm?q=region&limit=20&includeUnpublished=true", 200, b"[]", "application/json"),
    ("describeKnowledgeTerm", "ontology-reviewer", "GET",
     "knowledgeTerm/describe?iri=https%3A%2F%2Fexample.org%2Fmod%23Region&layerChain=true", None, None,
     "/knowledgeTerm/describe?iri=https%3A%2F%2Fexample.org%2Fmod%23Region&layerChain=true", 200, b'{"iri": "x"}',
     "application/json"),
]
ONTOLOGY_IDS = [f"{r[2]} {r[3].split('?')[0]} {r[5] or ''}".strip() for r in ONTOLOGY_ROUTES]


def answer(status, body, content_type):
    return httpx.Response(status, content=body, headers={"content-type": content_type} if content_type else {})


@pytest.mark.parametrize("operation_id, role, method, path, body, content_type, upstream_path, status, response_body, "
                         "response_type", ONTOLOGY_ROUTES, ids=ONTOLOGY_IDS)
def test_ontology_route_is_forwarded(upstream, operation_id, role, method, path, body, content_type, upstream_path,
                                     status, response_body, response_type):
    act_as(claims(role))
    upstream.default = answer(status, response_body, response_type)
    headers = {"content-type": content_type} if content_type else {}

    response = client.request(method, f"/api/knowledge/{NAME}/{path}", content=body, headers=headers)

    assert response.status_code == status
    assert response.content == response_body
    if response_type:
        assert response.headers["content-type"] == response_type
    sent = upstream.requests[0]
    assert sent.method == method
    assert str(sent.url) == API + upstream_path
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.content == (body or b"")
    if content_type:
        assert sent.headers["content-type"] == content_type
    if body is None:
        assert "content-length" not in sent.headers or sent.headers["content-length"] == "0"
        assert "transfer-encoding" not in sent.headers


@pytest.mark.parametrize("operation_id, role, method, path, body, content_type", [r[:6] for r in ONTOLOGY_ROUTES],
                         ids=ONTOLOGY_IDS)
def test_ontology_route_capability_matches_the_oas_roles(operation_id, role, method, path, body, content_type):
    act_as(claims(role))

    ops = instance_view()["capabilities"]["operations"]

    assert ops[operation_id] is True


@pytest.mark.parametrize("answer_, expected", [
    (httpx.Response(403, json={"message": "needs ontology-approver"}), 403),
    (httpx.Response(409, json={"message": "not allowed in draft"}), 409),
    (httpx.Response(401, json={"message": "bad token"}), 502),
    (httpx.Response(500, json={"message": "boom"}), 502),
], ids=["403", "409", "401", "500"])
@pytest.mark.parametrize("operation_id, role, method, path, body, content_type", [r[:6] for r in ONTOLOGY_ROUTES],
                         ids=ONTOLOGY_IDS)
def test_ontology_route_error_mapping(upstream, operation_id, role, method, path, body, content_type, answer_, expected):
    act_as(claims(role))
    upstream.default = answer_
    headers = {"content-type": content_type} if content_type else {}

    response = client.request(method, f"/api/knowledge/{NAME}/{path}", content=body, headers=headers)

    assert response.status_code == expected
    if expected == 502:
        assert "bad token" not in response.text and "boom" not in response.text
    else:
        assert response.json() == json.loads(answer_.content)


def test_every_ontology_operation_of_the_oas_has_a_route_test():
    tested = {r[0] for r in ONTOLOGY_ROUTES}
    ops = instance_view()["capabilities"]["operations"]
    ontology = {op for op in ops if "KnowledgeSpecification" in op or "KnowledgeTerm" in op or op == "validateDocument"}

    assert ontology == tested


def test_a_streamed_turtle_upload_is_forwarded_chunked(upstream):
    act_as(claims("ontology-editor"))
    upstream.default = httpx.Response(204)

    def chunks():
        yield TURTLE[:20]
        yield TURTLE[20:]

    response = client.put(f"/api/knowledge/{NAME}/knowledgeSpecification/mod/content", content=chunks(),
                          headers={"content-type": "text/turtle"})

    assert response.status_code == 204
    assert upstream.requests[0].content == TURTLE
    assert upstream.requests[0].headers["content-type"] == "text/turtle"


def test_curie_ids_keep_their_colon_and_other_characters_are_encoded(upstream):
    act_as(claims("knowledge-viewer"))

    client.get(f"/api/knowledge/{NAME}/knowledgeSpecification/tio:core%2Fx/mapping/a%20b")

    assert upstream.requests == []  # %2F decodes to a slash: not a single segment, so not an OAS route
    client.get(f"/api/knowledge/{NAME}/knowledgeSpecification/tio:core/mapping/a%20b%3F")
    assert upstream.requests[0].url.raw_path.decode().endswith("/knowledgeSpecification/tio:core/mapping/a%20b%3F")


# --- Knowledge-Base and TMF701 routes (#75) ---------------------------------------------------------------------

KB_TURTLE = b"@prefix ex: <https://example.org/> .\nex:site1 a ex:Site .\n"
KB_JSONLD = b'{"@id": "https://example.org/site1", "@type": "https://example.org/Site"}'
SPARQL = b"SELECT ?s WHERE { ?s ?p ?o } LIMIT 5"
BOUNDARY = "p5boundary"
MULTIPART = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"runbook.md\"\r\n"
             f"Content-Type: text/markdown\r\n\r\n# Runbook\n\nRestart the site.\r\n--{BOUNDARY}--\r\n").encode()
MULTIPART_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
KB = "/knowledge"
SEARCH = json.dumps({"query": "restart site", "topK": 5}).encode()

# operationId, role, method, portal path, body, content type, accept, api ("knowledge" or "process"),
# upstream path, upstream status, upstream response body, upstream response content type
KB_ROUTES = [
    ("listKnowledge", "knowledge-viewer", "GET", "knowledge?limit=20&offset=0&sort=-lastUpdate&fields=id", None, None, None,
     "knowledge", f"{KB}?limit=20&offset=0&sort=-lastUpdate&fields=id", 200, b"[]", "application/json"),
    ("createKnowledge", "knowledge-editor", "POST", "knowledge?fields=id", b'{"name": "kb1", "storeKind": "hybrid"}',
     "application/json", None, "knowledge", f"{KB}?fields=id", 201, b'{"id": "kb1"}', "application/json"),
    ("retrieveKnowledge", "knowledge-viewer", "GET", "knowledge/kb1", None, None, None, "knowledge", f"{KB}/kb1", 200,
     b'{"id": "kb1"}', "application/json"),
    ("patchKnowledge", "knowledge-editor", "PATCH", "knowledge/kb1", b'{"description": "x"}', MERGE, None, "knowledge",
     f"{KB}/kb1", 200, b'{"id": "kb1"}', "application/json"),
    ("deleteKnowledge", "knowledge-editor", "DELETE", "knowledge/kb1", None, None, None, "knowledge", f"{KB}/kb1", 204, b"", None),
    ("retrieveKnowledgeGraph", "knowledge-viewer", "GET", "knowledge/kb1/graph", None, None, "text/turtle", "knowledge",
     f"{KB}/kb1/graph", 200, KB_TURTLE, "text/turtle; charset=utf-8"),
    ("retrieveKnowledgeGraph", "knowledge-viewer", "GET", "knowledge/kb1/graph", None, None, "application/ld+json",
     "knowledge", f"{KB}/kb1/graph", 200, KB_JSONLD, "application/ld+json"),
    ("replaceKnowledgeGraph", "knowledge-editor", "PUT", "knowledge/kb1/graph", KB_TURTLE, "text/turtle", None, "knowledge",
     f"{KB}/kb1/graph", 204, b"", None),
    ("mergeKnowledgeGraph", "knowledge-editor", "POST", "knowledge/kb1/graph", KB_JSONLD, "application/ld+json", None,
     "knowledge", f"{KB}/kb1/graph", 204, b"", None),
    ("clearKnowledgeGraph", "knowledge-editor", "DELETE", "knowledge/kb1/graph", None, None, None, "knowledge",
     f"{KB}/kb1/graph", 204, b"", None),
    ("queryKnowledge", "knowledge-viewer", "POST", "knowledge/kb1/query?timeout=10", SPARQL, "application/sparql-query",
     "application/sparql-results+json", "knowledge", f"{KB}/kb1/query?timeout=10", 200, b'{"results": {"bindings": []}}',
     "application/sparql-results+json"),
    ("queryKnowledge", "knowledge-viewer", "POST", "knowledge/kb1/query", b"query=CONSTRUCT+%7B%7D+WHERE+%7B%7D",
     "application/x-www-form-urlencoded", "text/turtle", "knowledge", f"{KB}/kb1/query", 200, b"", "text/turtle"),
    ("searchKnowledge", "knowledge-viewer", "POST", "knowledge/kb1/search", SEARCH, "application/json", None, "knowledge",
     f"{KB}/kb1/search", 200, b'{"hits": []}', "application/json"),
    ("recommendFromKnowledge", "knowledge-viewer", "POST", "knowledge/kb1/recommend", SEARCH, "application/json", None,
     "knowledge", f"{KB}/kb1/recommend", 200, b'{"items": []}', "application/json"),
    ("createRecommendationFeedback", "knowledge-viewer", "POST", "knowledge/kb1/recommend/feedback",
     b'{"recommendationId": "r1", "useful": true}', "application/json", None, "knowledge", f"{KB}/kb1/recommend/feedback",
     204, b"", None),
    ("listKnowledgeDocument", "knowledge-viewer", "GET", "knowledge/kb1/document?limit=10", None, None, None, "knowledge",
     f"{KB}/kb1/document?limit=10", 200, b"[]", "application/json"),
    ("createKnowledgeDocument", "knowledge-editor", "POST", "knowledge/kb1/document", MULTIPART, MULTIPART_TYPE, None,
     "knowledge", f"{KB}/kb1/document", 201, b'{"id": "d1"}', "application/json"),
    ("retrieveKnowledgeDocument", "knowledge-viewer", "GET", "knowledge/kb1/document/d1", None, None, None, "knowledge",
     f"{KB}/kb1/document/d1", 200, b'{"id": "d1"}', "application/json"),
    ("deleteKnowledgeDocument", "knowledge-editor", "DELETE", "knowledge/kb1/document/d1", None, None, None, "knowledge",
     f"{KB}/kb1/document/d1", 204, b"", None),
    ("retrieveKnowledgeDocumentContent", "knowledge-viewer", "GET", "knowledge/kb1/document/d1/content", None, None, None,
     "knowledge", f"{KB}/kb1/document/d1/content", 200, b"# Runbook\n", "text/markdown"),
    ("createKnowledgeIngestion", "knowledge-editor", "POST", "knowledge/kb1/ingest", b'{"source": {"kind": "document", "id": "d1"}}',
     "application/json", None, "knowledge", f"{KB}/kb1/ingest", 201, b'{"id": "p1"}', "application/json"),
    ("listKnowledgeProposal", "knowledge-viewer", "GET", "knowledge/kb1/proposal?limit=5", None, None, None, "knowledge",
     f"{KB}/kb1/proposal?limit=5", 200, b"[]", "application/json"),
    ("retrieveKnowledgeProposal", "knowledge-viewer", "GET", "knowledge/kb1/proposal/pr1", None, None, None, "knowledge",
     f"{KB}/kb1/proposal/pr1", 200, b'{"id": "pr1"}', "application/json"),
    ("patchKnowledgeProposal", "knowledge-editor", "PATCH", "knowledge/kb1/proposal/pr1", b'{"status": "accepted"}',
     "application/json", None, "knowledge", f"{KB}/kb1/proposal/pr1", 200, b'{"id": "pr1"}', "application/json"),
    ("listProcess", "knowledge-viewer", "GET", "process?limit=5", None, None, None, "process", "/process?limit=5", 200, b"[]",
     "application/json"),
    ("retrieveProcess", "knowledge-viewer", "GET", "process/p1", None, None, None, "process", "/process/p1", 200,
     b'{"id": "p1", "state": "completed"}', "application/json"),
    ("listTask", "knowledge-viewer", "GET", "task?limit=5", None, None, None, "process", "/task?limit=5", 200, b"[]",
     "application/json"),
    ("retrieveTask", "knowledge-viewer", "GET", "task/t1", None, None, None, "process", "/task/t1", 200, b'{"id": "t1"}',
     "application/json"),
]
KB_IDS = [f"{r[2]} {r[3].split('?')[0]} {r[5] or r[6] or ''}".strip() for r in KB_ROUTES]


@pytest.mark.parametrize("operation_id, role, method, path, body, content_type, accept, api, upstream_path, status, "
                         "response_body, response_type", KB_ROUTES, ids=KB_IDS)
def test_kb_route_is_forwarded(upstream, operation_id, role, method, path, body, content_type, accept, api,
                               upstream_path, status, response_body, response_type):
    act_as(claims(role))
    upstream.default = answer(status, response_body, response_type)
    headers = {**({"content-type": content_type} if content_type else {}), **({"accept": accept} if accept else {})}

    response = client.request(method, f"/api/knowledge/{NAME}/{path}", content=body, headers=headers)

    assert response.status_code == status
    assert response.content == response_body
    assert response.headers["x-content-type-options"] == "nosniff"
    if response_type:
        assert response.headers["content-type"] == response_type
    sent = upstream.requests[0]
    assert sent.method == method
    assert str(sent.url) == (API if api == "knowledge" else PROCESS) + upstream_path
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.content == (body or b"")
    if content_type:
        assert sent.headers["content-type"] == content_type
    if accept:
        assert sent.headers["accept"] == accept


@pytest.mark.parametrize("operation_id, role", [r[:2] for r in KB_ROUTES], ids=KB_IDS)
def test_kb_route_capability_matches_the_oas_roles(operation_id, role):
    act_as(claims(role))

    assert instance_view()["capabilities"]["operations"][operation_id] is True


@pytest.mark.parametrize("answer_, expected", [
    (httpx.Response(403, json={"message": "needs knowledge-editor"}), 403),
    (httpx.Response(409, json={"message": "deprecated"}), 409),
    (httpx.Response(401, json={"message": "bad token"}), 502),
    (httpx.Response(500, json={"message": "boom"}), 502),
], ids=["403", "409", "401", "500"])
@pytest.mark.parametrize("operation_id, role, method, path, body, content_type", [r[:6] for r in KB_ROUTES], ids=KB_IDS)
def test_kb_route_error_mapping(upstream, operation_id, role, method, path, body, content_type, answer_, expected):
    act_as(claims(role))
    upstream.default = answer_
    headers = {"content-type": content_type} if content_type else {}

    response = client.request(method, f"/api/knowledge/{NAME}/{path}", content=body, headers=headers)

    assert response.status_code == expected
    if expected == 502:
        assert "bad token" not in response.text and "boom" not in response.text
    else:
        assert response.json() == json.loads(answer_.content)


def test_every_kb_and_process_operation_of_the_oas_has_a_route_test():
    tested = {r[0] for r in KB_ROUTES}
    ops = instance_view()["capabilities"]["operations"]
    ontology = {r[0] for r in ONTOLOGY_ROUTES}

    assert set(ops) - ontology == tested


def test_document_content_is_a_sandboxed_download(upstream):
    upstream.default = httpx.Response(200, content=b"<script>alert(1)</script>", headers={
        "content-type": "text/html", "content-disposition": 'inline; filename="page.html"; evil=1'})

    response = client.get(f"/api/knowledge/{NAME}/knowledge/kb1/document/d1/content")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="page.html"'
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "text/html"


def test_document_content_without_a_filename_is_still_an_attachment(upstream):
    upstream.default = httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    response = client.get(f"/api/knowledge/{NAME}/knowledge/kb1/document/d1/content")

    assert response.headers["content-disposition"] == "attachment"


def test_other_responses_are_not_downloads(upstream):
    upstream.default = httpx.Response(200, json=[], headers={"content-disposition": "inline"})

    response = client.get(f"/api/knowledge/{NAME}/knowledge/kb1/document")

    assert response.headers["content-disposition"] == "inline"
    assert "content-security-policy" not in response.headers


def test_a_document_error_is_not_turned_into_a_download(upstream):
    upstream.default = httpx.Response(404, json={"message": "no document"})

    response = client.get(f"/api/knowledge/{NAME}/knowledge/kb1/document/d1/content")

    assert response.status_code == 404
    assert "content-disposition" not in response.headers


def multipart(size):
    head = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode()
    tail = f"\r\n--{BOUNDARY}--\r\n".encode()
    return head + b"x" * (size - len(head) - len(tail)) + tail


def test_a_document_above_the_body_cap_but_within_the_upload_cap_is_forwarded(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_BODY_BYTES", "1000")
    monkeypatch.setenv("KNOWLEDGE_MAX_UPLOAD_BYTES", "5000")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))
    upstream.default = httpx.Response(201, json={"id": "d1"})

    response = client.post(f"/api/knowledge/{NAME}/knowledge/kb1/document", content=multipart(4000),
                           headers={"content-type": MULTIPART_TYPE})

    assert response.status_code == 201
    assert len(upstream.requests[0].content) == 4000


def test_the_upload_cap_does_not_apply_to_other_bodies(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_BODY_BYTES", "1000")
    monkeypatch.setenv("KNOWLEDGE_MAX_UPLOAD_BYTES", "5000")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))

    response = client.put(f"/api/knowledge/{NAME}/knowledge/kb1/graph", content=b"x" * 4000, headers={"content-type": "text/turtle"})

    assert response.status_code == 413
    assert upstream.requests == []


def test_a_declared_document_over_the_upload_cap_is_413_without_a_call(upstream, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_MAX_UPLOAD_BYTES", "5000")
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))

    response = client.post(f"/api/knowledge/{NAME}/knowledge/kb1/document", content=multipart(6000),
                           headers={"content-type": MULTIPART_TYPE})

    assert response.status_code == 413
    assert upstream.requests == []


def test_the_default_upload_cap_allows_a_50_mib_document_with_its_envelope():
    get_settings.cache_clear()

    assert get_settings().KNOWLEDGE_MAX_UPLOAD_BYTES == 51 * 1024 * 1024
    assert get_settings().KNOWLEDGE_MAX_BODY_BYTES == 10 * 1024 * 1024


async def _asgi_upload(chunks, chunk_size, headers):
    """Run one upload through the ASGI app; returns (status, how many body chunks the app pulled)."""
    pulled = {"count": 0}
    sent = []

    async def receive():
        if pulled["count"] < chunks:
            pulled["count"] += 1
            return {"type": "http.request", "body": b"x" * chunk_size, "more_body": pulled["count"] < chunks}
        # The body is complete: what a server reports next is the client going away (StreamingResponse waits for it).
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": f"/api/knowledge/{NAME}/knowledge/kb1/document", "raw_path": f"/api/knowledge/{NAME}/knowledge/kb1/document".encode(),
             "query_string": b"", "root_path": "", "server": ("test", 80), "client": ("127.0.0.1", 1234),
             "headers": [(k.encode(), v.encode()) for k, v in headers.items()]}
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], pulled["count"]


def test_an_oversized_streamed_upload_is_refused_without_reading_the_whole_body(upstream, monkeypatch):
    import asyncio

    monkeypatch.setenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(64 * 1024))
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))
    upstream.default = httpx.Response(201, json={"id": "d1"})

    # 1000 chunks of 16 KiB (16 MiB) without a Content-Length; the cap is 4 chunks.
    status, pulled = asyncio.run(_asgi_upload(1000, 16 * 1024, {"content-type": MULTIPART_TYPE, "transfer-encoding": "chunked"}))

    assert status == 413
    assert pulled <= 6


def test_a_streamed_upload_within_the_cap_is_read_to_the_end(upstream, monkeypatch):
    import asyncio

    monkeypatch.setenv("KNOWLEDGE_MAX_UPLOAD_BYTES", str(64 * 1024))
    get_settings.cache_clear()
    act_as(claims("knowledge-editor"))
    upstream.default = httpx.Response(201, json={"id": "d1"})

    status, pulled = asyncio.run(_asgi_upload(3, 16 * 1024, {"content-type": MULTIPART_TYPE, "transfer-encoding": "chunked"}))

    assert status == 201
    assert pulled == 3
    assert len(upstream.requests[0].content) == 3 * 16 * 1024
