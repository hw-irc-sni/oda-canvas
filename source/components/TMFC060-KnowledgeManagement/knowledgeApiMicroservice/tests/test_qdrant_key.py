"""A Qdrant that requires an API key (as the chart deploys it): the API reads the key from QDRANT_API_KEY_FILE."""
import pytest
from docker.types import Ulimit
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from app.config import Settings

from .api import auth, running_api
from .conftest import QDRANT_IMAGE

KEY = "test-qdrant-key-0123456789"


@pytest.fixture(scope="module")
def keyed_qdrant_url():
    container = (DockerContainer(QDRANT_IMAGE).with_exposed_ports(6333).with_env("QDRANT__SERVICE__API_KEY", KEY)
                 .with_kwargs(ulimits=[Ulimit(name="nofile", soft=65535, hard=65535)])
                 .waiting_for(HttpWaitStrategy(6333, "/readyz").for_status_code(200)))
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"


async def test_the_api_uses_the_key_from_its_file(postgres_url, keyed_qdrant_url, tmp_path):
    key_file = tmp_path / "qdrant-api-key"
    key_file.write_text(KEY + "\n")
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=keyed_qdrant_url,
                           qdrant_api_key_file=str(key_file)) as api:
        ready = await api.get("/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["checks"]["qdrant"]["status"] == "up"
        # Creating a knowledge-base creates its Qdrant collection, through the keyed client
        r = await api.post(f"{api.base}/knowledge", headers=auth("knowledge-editor"),
                           json={"name": "keyed", "storeKind": "hybrid"})
        assert r.status_code == 201, r.text


async def test_without_the_key_qdrant_is_reported_down(postgres_url, keyed_qdrant_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=keyed_qdrant_url) as api:
        ready = await api.get("/ready")
        assert ready.status_code == 503
        check = ready.json()["checks"]["qdrant"]
        assert check["status"] == "down" and "API key" in check["detail"], check


def test_a_missing_key_file_is_a_configuration_error(tmp_path):
    with pytest.raises(ValueError, match="missing or empty"):
        Settings(qdrant_api_key_file=str(tmp_path / "absent")).qdrant_api_key()
    (tmp_path / "empty").write_text("  \n")
    with pytest.raises(ValueError, match="missing or empty"):
        Settings(qdrant_api_key_file=str(tmp_path / "empty")).qdrant_api_key()
