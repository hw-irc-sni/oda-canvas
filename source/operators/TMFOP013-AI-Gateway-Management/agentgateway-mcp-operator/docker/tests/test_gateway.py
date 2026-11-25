"""Reconciliation against fake Kubernetes clients: write only on a change, retry on 409, nudge the pods."""

import asyncio
from types import SimpleNamespace

import pytest
import yaml
from kubernetes.client.exceptions import ApiException

import agentgatewayMcpOperator as op
from gateway import ConfigConflict, Gateway
from settings import Settings
from test_targets import MANUAL, mcp_api


class FakeCore:
    def __init__(self, config):
        self.text, self.version, self.writes, self.nudged = yaml.safe_dump(config), 1, 0, []
        self.conflicts = 0          # how many writes to refuse with 409 (a concurrent change)

    def read_namespaced_config_map(self, name, namespace):
        assert (name, namespace) == ("agentgateway-config", "canvas")
        return SimpleNamespace(data={"config.yaml": self.text}, metadata=SimpleNamespace(resource_version=str(self.version)))

    def patch_namespaced_config_map(self, name, namespace, body):
        if body["metadata"]["resourceVersion"] != str(self.version) or self.conflicts:
            if self.conflicts:
                self.conflicts -= 1
                self.version += 1        # someone else wrote meanwhile
            raise ApiException(status=409)
        self.text, self.version, self.writes = body["data"]["config.yaml"], self.version + 1, self.writes + 1

    def list_namespaced_pod(self, namespace, label_selector):
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="agentgateway-0"))])

    def patch_namespaced_pod(self, name, namespace, patch):
        self.nudged.append(name)

    def targets(self):
        return yaml.safe_load(self.text)["mcp"]["targets"]


class FakeCustom:
    def __init__(self, apis):
        self.apis = apis

    def list_cluster_custom_object(self, group, version, plural):
        return {"items": list(self.apis)}


def make(apis, config=None):
    core = FakeCore(config or {"mcp": {"gateways": "default", "targets": [MANUAL]}})
    return Gateway(Settings(), core, FakeCustom(apis)), core


def test_registers_and_removes_and_writes_only_on_change():
    gw, core = make([mcp_api()])
    assert asyncio.run(gw.reconcile()) is True
    assert [t["name"] for t in core.targets()] == ["test-mcp", "oda-components-ctk-knowledgemanagement-knowledgemcp"]
    assert core.nudged == ["agentgateway-0"]
    assert asyncio.run(gw.reconcile()) is False and core.writes == 1        # unchanged: no write
    gw.custom.apis = []                                                     # the component was uninstalled
    assert asyncio.run(gw.reconcile()) is True
    assert core.targets() == [MANUAL]


def test_retries_on_a_concurrent_change():
    gw, core = make([mcp_api()])
    core.conflicts = 2
    assert asyncio.run(gw.reconcile()) is True and core.writes == 1


def test_gives_up_when_the_config_keeps_changing():
    gw, core = make([mcp_api()])
    core.conflicts = 10
    with pytest.raises(ConfigConflict):
        asyncio.run(gw.reconcile(attempts=3))


def test_manual_targets_added_meanwhile_are_kept():
    gw, core = make([mcp_api()])
    asyncio.run(gw.reconcile())
    doc = yaml.safe_load(core.text)
    doc["mcp"]["targets"].insert(0, {"name": "added-in-portal", "mcp": {"host": "http://x/mcp"}})
    core.text = yaml.safe_dump(doc)
    gw.custom.apis = []
    asyncio.run(gw.reconcile())
    assert [t["name"] for t in core.targets()] == ["added-in-portal", "test-mcp"]


def test_event_handler_requests_a_reconcile():
    async def go():
        op._wake = asyncio.Event()
        await op.exposed_api_event(type="DELETED", name="x", namespace="components")
        return op._wake.is_set()

    assert asyncio.run(go())


def test_reconcile_loop_survives_errors(monkeypatch):
    calls = []

    class Broken:
        async def reconcile(self):
            calls.append(1)
            raise RuntimeError("API down")

    async def go():
        op._wake = asyncio.Event()
        task = asyncio.create_task(op.reconcile_loop(lambda: Broken(), interval=0.05))
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(go())
    assert len(calls) >= 2


def test_only_an_event_handler_is_registered_on_exposedapis():
    import kopf

    registry = kopf.get_default_registry()
    assert list(registry._changing.get_all_handlers()) == []
    (handler,) = list(registry._watching.get_all_handlers())
    assert handler.when.__name__ == "is_mcp"
