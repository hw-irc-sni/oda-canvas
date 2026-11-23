"""/health and /ready against real Postgres and Qdrant, and the API base path."""
import httpx
import pytest

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration


async def get(app, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_health_and_ready_with_all_dependencies_up(settings):
    app = create_app(settings)
    health = await get(app, "/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ready"
    assert {name: c["status"] for name, c in body["checks"].items()} == {
        "store": "up", "postgres": "up", "qdrant": "up", "modelRoute": "not-configured"}
    ready = await get(app, "/ready")
    assert ready.status_code == 200


async def test_ready_is_503_but_health_is_200_when_qdrant_is_down(settings):
    down = settings.model_copy(update={"qdrant_url": "http://127.0.0.1:9"})
    app = create_app(down)
    ready = await get(app, "/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["qdrant"]["status"] == "down"
    health = await get(app, "/health")
    assert health.status_code == 200
    assert health.json()["status"] == "degraded"


async def test_ready_is_503_when_postgres_is_down(settings):
    down = settings.model_copy(update={"database_url": "postgresql://x:y@127.0.0.1:9/none"})
    ready = await get(create_app(down), "/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["postgres"]["status"] == "down"


def test_base_path_uses_the_component_name():
    settings = Settings(component_name="km-knowledgemanagement")
    assert settings.base_path == "/km-knowledgemanagement/tmf-api/knowledgeManagement/v5"


async def test_405_allow_lists_the_documented_methods(settings):
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.request("PUT", f"{settings.base_path}/knowledge/kb")
        assert r.status_code == 405 and r.headers["allow"] == "DELETE, GET, PATCH"
        assert r.json()["code"] == "405"
        r = await client.request("DELETE", f"{settings.process_base_path}/task/t1")
        assert r.status_code == 405 and r.headers["allow"] == "GET"
        missing = await client.get(f"{settings.base_path}/nowhere")
        assert missing.status_code == 404 and missing.json()["code"] == "404"
