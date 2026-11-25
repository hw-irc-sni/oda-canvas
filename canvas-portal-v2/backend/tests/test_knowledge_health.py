"""The active model route of an instance in /api/knowledge/instances, read from the component's /health."""
import json

import httpx
import pytest

from k8s.resources import knowledge_health
from tests.test_knowledge import act_as, claims, client, setup, upstream  # noqa: F401 - fixtures
from utils.cache import knowledge_health_cache

URL = "http://km-kmapi.components.svc.cluster.local:8080/health"
HEALTHY = {"status": "ready", "checks": {
    "store": {"status": "up"}, "postgres": {"status": "up", "detail": "host=secret-db"},
    "modelRoute": {"status": "up", "route": "openaiCompatible", "baseUrl": "http://llm.internal/v1",
                   "chatModel": "chat", "routes": [{"route": "modelGatewayService", "reason": "http://mgs.internal"}]}}}


@pytest.fixture
def health(monkeypatch):
    calls = []

    def serve(answer):
        def handler(request):
            calls.append(request)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(knowledge_health, "transport", httpx.MockTransport(handler))
        knowledge_health_cache.clear()
        return calls
    return serve


def model_route():
    response = client.get("/api/knowledge/instances")
    assert response.status_code == 200
    return response.json()["instances"][0]["modelRoute"]


def test_only_the_route_and_its_status_are_returned(upstream, health):  # noqa: F811
    act_as(claims("knowledge-viewer"))
    calls = health(httpx.Response(200, json=HEALTHY))

    body = client.get("/api/knowledge/instances")

    assert body.json()["instances"][0]["modelRoute"] == {"status": "up", "route": "openaiCompatible"}
    assert "internal" not in body.text and "secret-db" not in body.text
    assert [str(c.url) for c in calls] == [URL]
    assert "authorization" not in calls[0].headers


@pytest.mark.parametrize("check, expected", [
    ({"status": "not-configured"}, {"status": "not-configured", "route": None}),
    ({"status": "down", "routes": []}, {"status": "down", "route": None}),
    ({"status": "up", "route": "<script>"}, {"status": "up", "route": None}),
    ({"status": "up", "route": ["x"]}, {"status": "up", "route": None}),
    ({"status": "odd"}, None),
    ({"status": ["up"]}, None),
    ("up", None),
], ids=["not-configured", "down", "bad name", "wrong type", "unknown status", "status list", "not an object"])
def test_the_check_is_parsed_strictly(upstream, health, check, expected):  # noqa: F811
    act_as(claims("knowledge-viewer"))
    health(httpx.Response(200, json={"checks": {"modelRoute": check}}))

    assert model_route() == expected


@pytest.mark.parametrize("answer", [
    httpx.Response(404), httpx.Response(302, headers={"location": "http://elsewhere/"}),
    httpx.Response(200, content=b"<html>"), httpx.Response(200, json=["x"]), httpx.Response(200, json={"checks": []}),
    httpx.Response(200, content=json.dumps({"checks": {"pad": "x" * 70000}}).encode()),
    httpx.ConnectError("refused"), httpx.ReadTimeout("slow"),
], ids=["404", "redirect", "html", "list", "no checks", "too large", "refused", "timeout"])
def test_an_unreadable_health_leaves_the_route_unknown(upstream, health, answer):  # noqa: F811
    act_as(claims("knowledge-viewer"))
    calls = health(answer)

    assert model_route() is None
    assert model_route() is None
    assert len(calls) == 1  # failures are cached too


def test_the_route_is_cached(upstream, health):  # noqa: F811
    act_as(claims("knowledge-viewer"))
    calls = health(httpx.Response(200, json=HEALTHY))

    assert model_route() == model_route()
    assert len(calls) == 1


def test_a_user_without_a_role_learns_nothing(upstream, health):  # noqa: F811
    act_as(claims())
    calls = health(httpx.Response(200, json=HEALTHY))

    assert model_route() is None
    assert calls == []
