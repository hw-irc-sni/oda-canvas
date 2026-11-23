"""The real knowledge API (a dev-only dependency) on a Postgres testcontainer, with its internal port open."""
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres:17.9-alpine"
TOKEN = "worker-test-token"


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer(POSTGRES_IMAGE, username="tmfc060", password="tmfc060", dbname="tmfc060") as pg:
        yield pg.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def qdrant_url():
    from docker.types import Ulimit
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import HttpWaitStrategy
    container = (DockerContainer("qdrant/qdrant:v1.13.6").with_exposed_ports(6333)
                 # Qdrant keeps files open per collection segment: the default limit runs out after a few collections
                 .with_kwargs(ulimits=[Ulimit(name="nofile", soft=65535, hard=65535)])
                 .waiting_for(HttpWaitStrategy(6333, "/readyz").for_status_code(200)))
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"


@asynccontextmanager
async def running_api(postgres_url: str, tmp_path, qdrant_url: str = "http://127.0.0.1:9", **overrides):
    from app.config import Settings
    from app.db.session import make_engine
    from app.main import create_app
    engine = make_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    await engine.dispose()
    token = tmp_path / "token"
    token.write_text(TOKEN)
    settings = Settings(component_name="km", store_path=str(tmp_path / "store"), database_url=postgres_url,
                        auth_enabled=False, keycloak_jwks_url="http://jwks.invalid/certs", internal_port=0,
                        internal_host="127.0.0.1", internal_token_file=str(token), federation_relay_port=0,
                        qdrant_url=qdrant_url, **overrides)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as client:
            client.app, client.base = app, settings.base_path
            client.internal_url = f"http://127.0.0.1:{app.state.internal.port}"
            client.token_file = str(token)
            yield client


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, tmp_path) as client:
        r = await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph"})
        assert r.status_code == 201, r.text
        yield client
