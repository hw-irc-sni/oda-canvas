"""The scaffold handlers: filters (only spec.provider odaComponent is owned) and the status they write."""

import asyncio

import kopf

import dataResourceOperator as op
from settings import Settings


def test_only_oda_component_is_owned():
    assert op.is_owned({"provider": "odaComponent"})
    assert not op.is_owned({"provider": "databricks"})
    assert not op.is_owned({})
    assert not op.is_owned(None)


def test_provider_filter():
    assert op.is_owned_provider({"provider": "odaComponent", "role": "provider"})
    assert not op.is_owned_provider({"provider": "odaComponent"})                 # role defaults to consumer
    assert not op.is_owned_provider({"provider": "odaComponent", "role": "consumer"})
    assert not op.is_owned_provider({"provider": "databricks", "role": "provider"})


def test_component_of():
    s = Settings()
    assert op.component_of({"labels": {"oda.tmforum.org/componentName": "km-x"}}, s) == "km-x"
    assert op.component_of({"ownerReferences": [{"kind": "Deployment", "name": "d"},
                                                {"kind": "Component", "name": "agent"}]}, s) == "agent"
    assert op.component_of({}, s) is None


def test_reconcile_writes_the_scaffold_status():
    patch = kopf.Patch()
    spec = {"provider": "odaComponent", "role": "provider",
            "providedDataResources": [{"name": "knowledge-vectors", "type": "vectorStore"}]}
    asyncio.run(op.reconcile(spec=spec, meta={"generation": 3, "labels": {}}, status={}, name="km",
                             namespace="components", patch=patch, reason="create"))
    st = patch["status"]
    assert st["phase"] == "Pending" and st["observedGeneration"] == 3
    assert "provider role is not implemented" in st["message"]
    (ready,) = st["conditions"]
    assert ready["type"] == "Ready" and ready["status"] == "Unknown" and ready["reason"] == "NotImplemented"
    assert ready["observedGeneration"] == 3 and ready["lastTransitionTime"].endswith("Z")


def test_condition_keeps_transition_time_when_unchanged():
    old = [{"type": "Ready", "status": "Unknown", "lastTransitionTime": "2026-01-01T00:00:00Z"}]
    assert op.condition("Ready", "Unknown", "r", "m", 2, old)["lastTransitionTime"] == "2026-01-01T00:00:00Z"
    assert op.condition("Ready", "True", "r", "m", 2, old)["lastTransitionTime"] != "2026-01-01T00:00:00Z"


def test_handlers_are_registered_with_the_owner_filter():
    registry = kopf.get_default_registry()
    handlers = [h for h in registry._spawning.get_all_handlers()] + \
               [h for h in registry._changing.get_all_handlers()]
    names = {h.id.split("/")[0] for h in handlers}
    assert {"reconcile", "delete", "poll_provider"} <= names
    for h in handlers:
        if h.id.split("/")[0] in ("reconcile", "delete"):
            assert h.when is op.is_owned
        if h.id.split("/")[0] == "poll_provider":
            assert h.when is op.is_owned_provider
