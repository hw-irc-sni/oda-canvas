"""Tests for the read-only Orchestration Center solution package route, with the upstream stubbed."""
import base64
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.exceptions import ApiException

from auth.middleware import get_current_user
from auth.models import AuthUser
from config import get_settings
from routers import solution_packages as sp
from utils.exceptions import register_exception_handlers

PASSWORD = "test-oc-password"
# Two seeded PSOPs from the Orchestration Center, trimmed to three steps each.
PSOPS = json.loads((Path(__file__).parent / "fixtures" / "orchestration_psops.json").read_text())
OTHER = {"workflow_id": "not-a-package", "name": "Ad hoc diagnosis", "tags": ["graph-editor"]}

app = FastAPI()
register_exception_handlers(app)
app.include_router(sp.router)
app.dependency_overrides[get_current_user] = lambda: AuthUser(username="viewer", email="", groups=[])
client = TestClient(app, raise_server_exceptions=False)


class FakeOrchestrationCenter:
    """Serves the token, workflow list and workflow detail endpoints."""

    def __init__(self):
        self.logins = 0
        self.valid_tokens: set[str] = set()
        self.requests: list[str] = []
        self.login_status = 200
        self.list_status = 200
        self.summaries = [
            {"workflow_id": p["id"], "name": p["name"], "tags": p["tags"], "created_at": p["created_at"]} for p in PSOPS
        ] + [OTHER]
        self.details = {p["id"]: p for p in PSOPS}

    def restart(self):
        """Sessions live in memory, so a restart drops every token."""
        self.valid_tokens.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == sp.TOKEN_PATH:
            if self.login_status != 200:
                return httpx.Response(self.login_status, json={"detail": "upstream detail"})
            if json.loads(request.content) != {"username": "admin", "password": PASSWORD}:
                return httpx.Response(401, json={"detail": "Incorrect username or password"})
            self.logins += 1
            token = f"token-{self.logins}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": 43200})

        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"code": 401, "message": "Valid login token required"})
        if request.url.path == sp.WORKFLOWS_PATH:
            if self.list_status != 200:
                return httpx.Response(self.list_status, text="<html>upstream page</html>")
            limit, offset = int(request.url.params["limit"]), int(request.url.params["offset"])
            page = self.summaries[offset : offset + limit]
            more = offset + limit < len(self.summaries)
            return httpx.Response(
                200,
                json={"code": 200, "status": "success", "data": page, "pagination": {"offset": offset, "limit": limit, "has_more": more}},
            )
        workflow_id = request.url.path.rsplit("/", 1)[-1]
        if workflow_id in self.details:
            return httpx.Response(200, json={"code": 200, "status": "success", "data": self.details[workflow_id]})
        return httpx.Response(404, json={"code": 404, "status": "error"})

    def list_calls(self) -> int:
        return self.requests.count(sp.WORKFLOWS_PATH)


@pytest.fixture
def upstream(monkeypatch):
    fake = FakeOrchestrationCenter()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(sp.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(fake.handle), **kw))
    # One package per page, so the list is paged.
    monkeypatch.setattr(sp, "PAGE_LIMIT", 1)
    monkeypatch.setattr(sp, "_state", sp._State())

    settings = get_settings()
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_URL", "http://orchestration-center:5001")
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PAGE_URL", "https://openan.example/orchestration")
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PASSWORD_SECRET_NAME", "")
    return fake


def expire_cache():
    sp._state.result_expires_at = 0


def test_not_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "ORCHESTRATION_CENTER_URL", "")
    response = client.get("/api/solution-packages")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["packages"] == []


def test_lists_only_tagged_packages_with_steps(upstream):
    response = client.get("/api/solution-packages")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["pageUrl"] == "https://openan.example/orchestration"
    assert [p["name"] for p in body["packages"]] == sorted(p["name"] for p in PSOPS)

    assurance = next(p for p in body["packages"] if p["name"].startswith("IG1503A"))
    assert assurance["id"] == PSOPS[0]["id"]
    assert assurance["tags"] == ["spg", "solution-package"]
    assert assurance["agents"] == ["Live Streaming Agent", "Service Assurance Agent"]
    assert "KQI Monitoring" in assurance["skills"]
    first = assurance["steps"][0]
    assert first["name"] == "step1"
    assert first["next"] == ["step2"]
    assert first["tasks"][0] == {
        "agent": "Live Streaming Agent",
        "skill": "KQI Monitoring",
        "description": "Report performance KPIs and QoE indicators to the Service Assurance Agent",
    }
    # The summary is the chapter's first paragraph, without its heading.
    assert assurance["summary"].startswith("The interaction flow for Individual Service Assurance")
    assert "#" not in assurance["summary"]
    assert "/rest/v1/orchestrate/workflows/not-a-package" not in upstream.requests


def test_pages_through_the_list(upstream):
    client.get("/api/solution-packages")
    assert upstream.list_calls() == len(upstream.summaries)


def test_caches_results_and_reuses_the_token(upstream):
    client.get("/api/solution-packages")
    requests = len(upstream.requests)
    client.get("/api/solution-packages")
    assert len(upstream.requests) == requests

    expire_cache()
    client.get("/api/solution-packages")
    assert len(upstream.requests) > requests
    assert upstream.logins == 1


def test_logs_in_again_after_the_upstream_restarts(upstream):
    client.get("/api/solution-packages")
    upstream.restart()
    expire_cache()
    response = client.get("/api/solution-packages")
    assert response.status_code == 200
    assert len(response.json()["packages"]) == len(PSOPS)
    assert upstream.logins == 2


@pytest.mark.parametrize("status", [401, 403, 429])
def test_rejected_login_is_a_502_and_backs_off(upstream, status):
    upstream.login_status = status
    response = client.get("/api/solution-packages")
    # Never 401, which would log the portal user out.
    assert response.status_code == 502
    assert "upstream detail" not in response.text

    requests = len(upstream.requests)
    assert client.get("/api/solution-packages").status_code == 502
    assert len(upstream.requests) == requests


def test_wrong_password_is_a_502(upstream, monkeypatch):
    monkeypatch.setattr(get_settings(), "ORCHESTRATION_CENTER_PASSWORD", "wrong")
    assert client.get("/api/solution-packages").status_code == 502
    assert upstream.logins == 0


def test_upstream_error_is_a_502_without_upstream_content(upstream):
    upstream.list_status = 500
    response = client.get("/api/solution-packages")
    assert response.status_code == 502
    assert "upstream page" not in response.text


def test_unreachable_upstream_is_a_502(upstream, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(upstream, "handle", refuse)
    response = client.get("/api/solution-packages")
    assert response.status_code == 502
    assert response.json()["detail"] == "The Orchestration Center could not be reached."


def test_reads_the_password_from_the_secret(upstream, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PASSWORD", "")
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PASSWORD_SECRET_NAMESPACE", "components")
    monkeypatch.setattr(settings, "ORCHESTRATION_CENTER_PASSWORD_SECRET_NAME", "openan-orchestration-runtime")
    reads = []

    class FakeCoreV1Api:
        def __init__(self, _client):
            pass

        def read_namespaced_secret(self, name, namespace):
            reads.append((namespace, name))
            data = {"OC_ADMIN_INITIAL_PASSWORD": base64.b64encode(PASSWORD.encode()).decode()}
            return type("Secret", (), {"data": data})()

    monkeypatch.setattr(sp.k8s_client, "CoreV1Api", FakeCoreV1Api)
    monkeypatch.setattr(sp, "get_base_api_client", lambda: None)
    assert client.get("/api/solution-packages").status_code == 200
    assert reads == [("components", "openan-orchestration-runtime")]


def test_unreadable_secret_is_a_502(upstream, monkeypatch):
    monkeypatch.setattr(get_settings(), "ORCHESTRATION_CENTER_PASSWORD_SECRET_NAME", "openan-orchestration-runtime")

    class ForbiddenCoreV1Api:
        def __init__(self, _client):
            pass

        def read_namespaced_secret(self, name, namespace):
            raise ApiException(status=403)

    monkeypatch.setattr(sp.k8s_client, "CoreV1Api", ForbiddenCoreV1Api)
    monkeypatch.setattr(sp, "get_base_api_client", lambda: None)
    response = client.get("/api/solution-packages")
    assert response.status_code == 502
    assert response.json()["detail"] == "The portal cannot read the Orchestration Center credentials."
    assert upstream.logins == 0


@pytest.mark.parametrize(
    ("intent", "summary"),
    [
        ("# 5. Flow\n\n[Figure 1]\n\nFirst paragraph\nwraps here.\n\nSecond.", "First paragraph wraps here."),
        ("## Only a heading", ""),
        (None, ""),
    ],
)
def test_summary(intent, summary):
    assert sp._summary(intent) == summary


def test_summary_is_capped():
    summary = sp._summary("word " * 500)
    assert len(summary) == sp.SUMMARY_MAX_CHARS
    assert summary.endswith("…")
