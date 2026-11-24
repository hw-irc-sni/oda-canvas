"""The real knowledge API (dev-only dependency) on Postgres, with token validation, behind the MCP server."""
import os

# Every test app picks a free port for its metrics listener instead of 4000
os.environ.setdefault("METRICS_PORT", "0")
import asyncio
import json
import socket
import time
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import text
from testcontainers.community.postgres import PostgresContainer

CLIENT, ISSUER = "km", "https://keycloak.example.org/realms/odari"
PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(*roles) -> str:
    now = int(time.time())
    return jwt.encode({"iss": ISSUER, "sub": "user-1", "preferred_username": "alice", "iat": now, "exp": now + 600,
                       "aud": [CLIENT], "azp": "agent", "resource_access": {CLIENT: {"roles": list(roles)}}},
                      PRIVATE, algorithm="RS256", headers={"kid": "k1"})


class StaticJWKS(jwt.PyJWKClient):
    def __init__(self):
        super().__init__("http://jwks.invalid", cache_keys=True)

    def fetch_data(self):
        key = json.loads(RSAAlgorithm.to_jwk(PRIVATE.public_key()))
        return {"keys": [{**key, "kid": "k1", "use": "sig", "alg": "RS256"}]}


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:17.9-alpine", username="t", password="t", dbname="t") as pg:
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


async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    server.install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.02)
    return server, task, f"http://127.0.0.1:{sock.getsockname()[1]}"


@asynccontextmanager
async def running(postgres_url, tmp_path, qdrant_url="http://127.0.0.1:9", **overrides):
    from app.auth import TokenValidator
    from app.config import Settings
    from app.db.session import make_engine
    from app.main import create_app as create_api

    from mcp_server.api import KnowledgeApi
    from mcp_server.app import create_app as create_mcp
    from mcp_server.config import Settings as McpSettings
    engine = make_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    await engine.dispose()
    settings = Settings(component_name=CLIENT, store_path=str(tmp_path / "store"), database_url=postgres_url,
                        keycloak_jwks_url="http://jwks.invalid", keycloak_issuers=[ISSUER], federation_relay_port=0,
                        internal_port=0, qdrant_url=qdrant_url, governance_require_validation=False, **overrides)
    api_app = create_api(settings, TokenValidator(settings, StaticJWKS()))
    async with api_app.router.lifespan_context(api_app):
        rest = httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://api")
        base = settings.base_path
        mcp_api = KnowledgeApi(f"http://api{base}", transport=httpx.ASGITransport(app=api_app))
        server, task, url = await serve(create_mcp(McpSettings(), mcp_api))
        try:
            rest.app = api_app
            yield rest, base, f"{url}/mcp"
        finally:
            server.should_exit = True
            await task
            await rest.aclose()
