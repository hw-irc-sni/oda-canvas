"""The scaffold handlers: filters (only spec.provider odaComponent is owned) and the status they write."""

import asyncio

import kopf

import dataResourceOperator as op
import common
from settings import Settings


def test_only_oda_component_is_owned():
    assert common.is_owned({"provider": "odaComponent"})
    assert not common.is_owned({"provider": "databricks"})
    assert not common.is_owned({})
    assert not common.is_owned(None)


def test_provider_filter():
    assert common.is_owned_provider({"provider": "odaComponent", "role": "provider"})
    assert not common.is_owned_provider({"provider": "odaComponent"})                 # role defaults to consumer
    assert not common.is_owned_provider({"provider": "odaComponent", "role": "consumer"})
    assert not common.is_owned_provider({"provider": "databricks", "role": "provider"})


def test_component_of():
    s = Settings()
    assert common.component_of({"labels": {"oda.tmforum.org/componentName": "km-x"}}, s) == "km-x"
    assert common.component_of({"ownerReferences": [{"kind": "Deployment", "name": "d"},
                                                    {"kind": "Component", "name": "agent"}]}, s) == "agent"
    assert common.component_of({}, s) is None


def test_reconcile_leaves_a_provider_status_to_its_daemon():
    patch = kopf.Patch()
    spec = {"provider": "odaComponent", "role": "provider",
            "providedDataResources": [{"name": "knowledge-vectors", "type": "vectorStore"}]}
    trigger = asyncio.Event()
    op.TRIGGERS[("components", "km")] = trigger
    try:
        asyncio.run(op.reconcile(spec=spec, meta={"generation": 3, "labels": {}}, status={}, name="km",
                                 namespace="components", patch=patch, reason="update"))
    finally:
        del op.TRIGGERS[("components", "km")]
    assert "status" not in patch and trigger.is_set()


def test_reconcile_writes_the_scaffold_status_of_a_consumer():
    patch = kopf.Patch()
    spec = {"provider": "odaComponent", "dependentDataResources": [{"name": "kb", "providerRef": {}}]}
    asyncio.run(op.reconcile(spec=spec, meta={"generation": 3, "labels": {}}, status={}, name="km",
                             namespace="components", patch=patch, reason="create"))
    st = patch["status"]
    assert st["phase"] == "Pending" and st["observedGeneration"] == 3
    assert "consumer role is not implemented" in st["message"]
    (ready,) = st["conditions"]
    assert ready["type"] == "Ready" and ready["status"] == "Unknown" and ready["reason"] == "NotImplemented"
    assert ready["observedGeneration"] == 3 and ready["lastTransitionTime"].endswith("Z")


def test_condition_keeps_transition_time_when_unchanged():
    old = [{"type": "Ready", "status": "Unknown", "lastTransitionTime": "2026-01-01T00:00:00Z"}]
    assert common.condition("Ready", "Unknown", "r", "m", 2, old)["lastTransitionTime"] == "2026-01-01T00:00:00Z"
    assert common.condition("Ready", "True", "r", "m", 2, old)["lastTransitionTime"] != "2026-01-01T00:00:00Z"


def test_handlers_are_registered_with_the_owner_filter():
    registry = kopf.get_default_registry()
    handlers = [h for h in registry._spawning.get_all_handlers()] + \
               [h for h in registry._changing.get_all_handlers()]
    names = {h.id.split("/")[0] for h in handlers}
    assert {"reconcile", "delete", "provider_daemon"} <= names
    for h in handlers:
        if h.id.split("/")[0] in ("reconcile", "delete"):
            assert h.when is common.is_owned
        if h.id.split("/")[0] == "provider_daemon":
            assert h.when is common.is_owned_provider


def test_listener_wakes_the_right_daemon():
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        trigger = asyncio.Event()
        op.TRIGGERS[("components", "km")] = trigger
        client = TestClient(TestServer(op.listener_app()))
        await client.start_server()
        try:
            ok = await client.post("/providers/components/km/listener/knowledgeCreateEvent", json={"any": "thing"})
            unknown = await client.post("/providers/components/other/listener/knowledgeCreateEvent")
            other = await client.get("/providers/components/km/listener/knowledgeCreateEvent")
            return ok.status, unknown.status, other.status, trigger.is_set()
        finally:
            await client.close()
            del op.TRIGGERS[("components", "km")]

    assert asyncio.run(go()) == (204, 404, 405, True)


def test_wait_for_trigger_returns_on_trigger_or_stop(monkeypatch):
    monkeypatch.setattr(op, "EVENT_COALESCE_SECONDS", 0)

    class Stopped:
        def __init__(self):
            self.flag = False

        def __bool__(self):
            return self.flag

    async def go():
        loop = asyncio.get_running_loop()
        trigger, stopped = asyncio.Event(), Stopped()
        loop.call_later(0.1, trigger.set)
        t0 = loop.time()
        await op.wait_for_trigger(trigger, stopped, 30)
        woken = loop.time() - t0
        trigger.clear()
        t0 = loop.time()
        await op.wait_for_trigger(trigger, stopped, 0.2)
        timed_out = loop.time() - t0
        return woken, timed_out

    woken, timed_out = asyncio.run(go())
    assert woken < 1.5 and 0.15 < timed_out < 1.5
