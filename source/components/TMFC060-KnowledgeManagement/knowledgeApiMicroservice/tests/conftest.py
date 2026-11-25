"""Fixtures: real Postgres and Qdrant in containers (testcontainers), and a temporary pyoxigraph store."""
import os

# Every test app picks a free port for its metrics listener instead of 4000
os.environ.setdefault("METRICS_PORT", "0")
import pytest
from docker.types import Ulimit
from testcontainers.core.container import DockerContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from app.config import Settings

POSTGRES_IMAGE = "postgres:17.12-alpine"
QDRANT_IMAGE = "qdrant/qdrant:v1.20.2"


@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer(POSTGRES_IMAGE, username="tmfc060", password="tmfc060", dbname="tmfc060") as pg:
        yield pg.get_connection_url(driver=None)


@pytest.fixture(scope="session")
def qdrant_container():
    container = (DockerContainer(QDRANT_IMAGE).with_exposed_ports(6333)
                 # Qdrant keeps files open per collection segment: the default limit runs out after a few collections
                 .with_kwargs(ulimits=[Ulimit(name="nofile", soft=65535, hard=65535)])
                 .waiting_for(HttpWaitStrategy(6333, "/readyz").for_status_code(200)))
    with container:
        yield container


@pytest.fixture(scope="session")
def qdrant_url(qdrant_container):
    return f"http://{qdrant_container.get_container_host_ip()}:{qdrant_container.get_exposed_port(6333)}"


@pytest.fixture
def settings(tmp_path, postgres_url, qdrant_url):
    return Settings(component_name="km-knowledgemanagement", store_path=str(tmp_path / "store"),
                    database_url=postgres_url, qdrant_url=qdrant_url,
                    keycloak_jwks_url="http://jwks.invalid/certs",
                    keycloak_issuers=["https://keycloak.example.org/auth/realms/odari"], federation_relay_port=0)
