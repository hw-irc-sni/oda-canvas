"""The model resolver chain (ModelGatewayService -> agentgateway -> OpenAI-compatible), failover and /health,
with the Kubernetes API and the model endpoints mocked."""
import json

import httpx
import pytest

from app.config import Settings
from app.models import K8s, ModelError, ModelResolver

MGS = "/apis/oda.tmforum.org/v1alpha1/namespaces/canvas/modelgatewayservices/llm"
CRD = "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/modelgatewayservices.oda.tmforum.org"
BASES = {"modelGatewayService": "http://mgs.example:8080/v1", "agentgateway": "http://agw.example/v1",
         "openaiCompatible": "https://llm.example/v1"}


class World:
    """The mocked cluster and model servers; tests switch pieces off."""

    def __init__(self):
        self.crd = 200
        self.cr = {"spec": {"dependentAIModels": [{"name": "chat-m"}, {"name": "embed-m"}]},
                   "status": {"endpointUrl": "http://mgs.example:8080"}}
        self.services = {}
        self.down = set()  # base URLs whose /models probe fails
        self.failing = set()  # base URLs whose calls answer 503
        self.refusing = set()  # base URLs whose calls answer 400
        self.dimension = 4
        self.calls = []

    def k8s(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == CRD:
            return httpx.Response(self.crd, json={})
        if path == "/apis/oda.tmforum.org/v1alpha1/modelgatewayservices":
            return httpx.Response(200, json={"items": []})
        if path == MGS:
            return httpx.Response(200, json=self.cr) if self.cr is not None else httpx.Response(404, json={})
        if path.startswith("/api/v1/namespaces/canvas/services/"):
            svc = self.services.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=svc) if svc else httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def models(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        base = next(b for b in BASES.values() if url.startswith(b)) if any(url.startswith(b) for b in BASES.values()) \
            else url.rsplit("/", 1)[0]
        self.calls.append((request.method, url, request.headers.get("authorization")))
        if url.endswith("/models"):
            if base in self.down:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json={"data": []})
        if base in self.failing:
            return httpx.Response(503, json={})
        if base in self.refusing:
            return httpx.Response(400, json={"error": "bad"})
        body = json.loads(request.content)
        if url.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.1] * self.dimension}
                                                      for i, _ in enumerate(body["input"])]})
        return httpx.Response(200, json={"choices": [{"message": {"content": f"answer from {base}"}}]})


@pytest.fixture
def world():
    return World()


def resolver(world, tmp_path, **overrides):
    key = tmp_path / "key"
    key.write_text("sk-secret\n")
    values = dict(model_gateway_service="canvas/llm", mgs_chat_model="chat-m", mgs_embedding_model="embed-m",
                  agentgateway_url=BASES["agentgateway"], agentgateway_chat_model="c2", agentgateway_embedding_model="e2",
                  openai_base_url=BASES["openaiCompatible"], openai_api_key_file=str(key), openai_chat_model="c3",
                  openai_embedding_model="e3", embedding_dimension=4)
    values.update(overrides)
    settings = Settings(component_name="km", keycloak_jwks_url="http://x", **values)
    k8s = K8s("https://k8s.example", "sa-token", transport=httpx.MockTransport(world.k8s), namespace="canvas")
    return ModelResolver(settings, k8s=k8s, transport=httpx.MockTransport(world.models))


async def route_of(r):
    route = await r.resolve()
    return route.name if route else None


async def test_each_route_in_turn(world, tmp_path):
    r = resolver(world, tmp_path)
    assert await route_of(r) == "modelGatewayService" and r.active.base_url == BASES["modelGatewayService"]
    world.cr["status"] = {}  # no endpoint in status/spec: the proxy Service is used
    world.services["llm-proxy"] = {"spec": {"clusterIP": "10.0.0.7", "ports": [{"port": 8080}]}}
    assert await route_of(r) == "modelGatewayService" and r.active.base_url == "http://10.0.0.7:8080/v1"
    world.cr["spec"]["dependentAIModels"] = [{"name": "chat-m"}]  # the embedding model is not offered
    assert await route_of(r) == "agentgateway"
    assert r.status()["routes"][0] == {"route": "modelGatewayService", "status": "skipped",
                                       "reason": "model not offered by the gateway: embed-m"}
    world.cr = None
    assert await route_of(r) == "agentgateway"
    assert "not found" in r.status()["routes"][0]["reason"]
    world.crd = 404
    assert await route_of(r) == "agentgateway"
    assert r.status()["routes"][0]["reason"] == "the ModelGatewayService CRD is not installed"
    world.down.add(BASES["agentgateway"])
    assert await route_of(r) == "openaiCompatible"
    assert r.status()["routes"][1]["status"] == "unavailable"
    world.down.add(BASES["openaiCompatible"])
    assert await route_of(r) is None and r.status()["status"] == "down"


async def test_crd_access_denied_falls_back_to_listing(world, tmp_path):
    world.crd = 403
    r = resolver(world, tmp_path)
    assert await route_of(r) == "modelGatewayService"


async def test_routes_need_their_model_names(world, tmp_path):
    r = resolver(world, tmp_path, mgs_embedding_model=None, agentgateway_chat_model=None)
    assert await route_of(r) == "openaiCompatible"
    reasons = [o["reason"] for o in r.status()["routes"][:2]]
    assert reasons == ["MGS_CHAT_MODEL and MGS_EMBEDDING_MODEL must both be set",
                       "AGENTGATEWAY_CHAT_MODEL and AGENTGATEWAY_EMBEDDING_MODEL must both be set"]
    none = resolver(world, tmp_path, model_gateway_service=None, agentgateway_url=None, openai_base_url=None)
    assert await route_of(none) is None and none.status()["status"] == "not-configured"


async def test_outside_a_cluster_route_1_is_skipped(world, tmp_path):
    r = resolver(world, tmp_path)
    r.k8s = None
    assert await route_of(r) == "agentgateway"
    assert r.status()["routes"][0]["reason"] == "not running in a Kubernetes cluster"


async def test_calls_and_failover(world, tmp_path):
    r = resolver(world, tmp_path)
    await r.resolve()
    assert await r.chat([{"role": "user", "content": "hi"}]) == f"answer from {BASES['modelGatewayService']}"
    assert len((await r.embed(["a", "b"]))) == 2
    world.failing.add(BASES["modelGatewayService"])  # 5xx: fail over and retry once
    assert await r.chat([{"role": "user", "content": "hi"}]) == f"answer from {BASES['agentgateway']}"
    assert r.active.name == "agentgateway"
    assert r.status()["routes"][0]["status"] == "failed"
    world.failing.discard(BASES["modelGatewayService"])
    assert await route_of(r) == "modelGatewayService"  # the timer evaluation picks route 1 back up
    world.refusing.add(BASES["modelGatewayService"])  # 4xx: no failover
    with pytest.raises(ModelError) as exc:
        await r.chat([{"role": "user", "content": "hi"}])
    assert exc.value.status == 400 and r.active.name == "modelGatewayService"


async def test_connection_errors_fail_over(world, tmp_path):
    r = resolver(world, tmp_path, model_gateway_service=None)
    await r.resolve()

    original = world.models

    def broken(request):
        if str(request.url).startswith(BASES["agentgateway"]) and not str(request.url).endswith("/models"):
            raise httpx.ConnectError("reset")
        return original(request)
    r.http = httpx.AsyncClient(transport=httpx.MockTransport(broken))
    assert await r.embed(["x"]) == [[0.1] * 4]
    assert r.active.name == "openaiCompatible"


async def test_the_key_is_sent_but_never_shown(world, tmp_path):
    r = resolver(world, tmp_path, model_gateway_service=None, agentgateway_url=None)
    await r.resolve()
    await r.chat([{"role": "user", "content": "hi"}])
    assert all(auth == "Bearer sk-secret" for _, url, auth in world.calls if url.startswith(BASES["openaiCompatible"]))
    assert "sk-secret" not in json.dumps(r.status())


async def test_status_and_dimension_mismatch(world, tmp_path):
    r = resolver(world, tmp_path, embedding_dimension=1024)
    await r.resolve()
    status = r.status()
    assert status["status"] == "up" and status["route"] == "modelGatewayService"
    assert status["chatModel"] == "chat-m" and status["embeddingModel"] == "embed-m"
    assert status["embeddingDimension"] == 4 and "EMBEDDING_DIMENSION=1024" in status["dimensionMismatch"]
    assert [o["status"] for o in status["routes"]] == ["selected", "standby", "standby"]
    assert "evaluatedAt" in status


async def test_health_shows_the_route(world, tmp_path):
    from app.main import create_app
    r = resolver(world, tmp_path)
    await r.resolve()
    app = create_app(Settings(component_name="km", keycloak_jwks_url="http://x", auth_enabled=False,
                              qdrant_url="http://127.0.0.1:9", database_url="postgresql://x:y@127.0.0.1:9/none"))
    app.state.models = r
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        health = (await client.get("/health")).json()
    model = health["checks"]["modelRoute"]
    assert model["status"] == "up" and model["route"] == "modelGatewayService"
    assert model["baseUrl"] == BASES["modelGatewayService"]
