"""DataResource mirroring (ADR-011): the DataResourceConfig sync, the relationships ODA components keep, the
relationship rows an update replaces, and the web UI's per-component list."""

import asyncio
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import crud, models  # noqa: E402
from app.database import Base  # noqa: E402
import app.main as main  # noqa: E402


def rel(kind, target):
    return {"@type": "ResourceRelationship", "relationshipType": kind,
            "resource": {"@type": "ResourceRef", "id": target, "href": f"/x/{target}"}}


def data_resource(rid, rels, **chars):
    return {"id": rid, "name": rid.split(".")[-1], "category": "DataResource", "resourceVersion": "5",
            "resourceCharacteristic": [{"name": k, "value": v} for k, v in chars.items()],
            "resourceRelationship": rels}


class FakeSyncer(main.ResourceSyncer):
    """ResourceSyncer with the HTTP and Kubernetes calls in memory."""

    def __init__(self):
        super().__init__(downstream_url="http://ri", upstream_url="http://up", upstream_repo_name="self")
        self.downstream = []            # DataResource entries
        self.components = {}            # name -> ODAComponent resource
        self.drcs = {}                  # name -> DataResourceConfig
        self.upstream = {}              # up id -> data
        self.deleted, self.resynced = [], []

    async def fetch_data_resources(self, namespace):
        return [dict(e) for e in self.downstream]

    async def components_of_drc(self, namespace, drc_name):
        return main.components_named_by(self.drcs.get(drc_name))

    async def fetch_from_downstream(self, resource_id, namespace=None):
        if resource_id not in self.components:
            raise RuntimeError("404")
        self.resynced.append(resource_id)
        return dict(self.components[resource_id])

    async def send_to_upstream(self, resource_id, resource_data):
        self.upstream[resource_id] = resource_data

    async def delete_upstream(self, resource_id):
        self.deleted.append(resource_id)
        self.upstream.pop(resource_id, None)


def provider_entries(with_kb=True):
    entries = [
        data_resource("km.knowledge-vectors", [rel("providedBy", "km")], type="vectorStore"),
        data_resource("km.knowledge-graph", [rel("providedBy", "km")], type="knowledgeGraph"),
    ]
    if with_kb:
        entries.append(data_resource("km.knowledge.kb-1", [rel("providedBy", "km"), rel("isPartOf", "km.knowledge-vectors")],
                                     kind="knowledge", storeKind="hybrid"))
    return entries


def test_components_named_by():
    drc = {"metadata": {"labels": {"oda.tmforum.org/componentName": "agent"}},
           "status": {"bindings": [{"providerComponent": "km"}, {"name": "x"}]}}
    assert main.components_named_by(drc) == {"agent", "km"}
    assert main.components_named_by({"metadata": {"ownerReferences": [{"kind": "Component", "name": "c"}]}}) == {"c"}
    assert main.components_named_by(None) == set()


def test_component_keeps_its_data_relationships_only():
    s = FakeSyncer()
    data = s.patch_resource_data({"id": "km", "category": "ODAComponent", "resourceRelationship": [
        rel("exposes", "km-api"), rel("provides", "km.knowledge-vectors"), rel("dependsOn", "other.knowledge.kb")]},
        "self")
    assert data["id"] == "self:km"
    assert [(r["relationshipType"], r["resource"]["id"]) for r in data["resourceRelationship"]] == [
        ("provides", "self:km.knowledge-vectors"), ("dependsOn", "self:other.knowledge.kb")]


def test_sync_creates_updates_removes_and_resyncs_components():
    s = FakeSyncer()
    s.downstream = provider_entries() + [data_resource("other.store", [], type="cache")]
    s.components = {"km": {"id": "km", "category": "ODAComponent",
                           "resourceRelationship": [rel("provides", "km.knowledge-vectors")]}}
    s.drcs = {"km": {"metadata": {"labels": {"oda.tmforum.org/componentName": "km"}}}}
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "km", "10", None))
    assert sorted(k for k in s.upstream if k.startswith("self:km.")) == [
        "self:km.knowledge-graph", "self:km.knowledge-vectors", "self:km.knowledge.kb-1"]
    assert "self:other.store" not in s.upstream                    # another DataResourceConfig's entry
    kb = s.upstream["self:km.knowledge.kb-1"]
    assert [r["resource"]["id"] for r in kb["resourceRelationship"]] == ["self:km", "self:km.knowledge-vectors"]
    assert s.upstream["self:km"]["resourceRelationship"][0]["resource"]["id"] == "self:km.knowledge-vectors"
    assert s.resynced == ["km"]
    # the knowledge-base is deleted on the provider: its entry goes, the component is re-synced again
    s.downstream = provider_entries(with_kb=False)
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "km", "11", "10"))
    assert s.deleted == ["self:km.knowledge.kb-1"] and s.resynced == ["km", "km"]
    # the DataResourceConfig is deleted: all its entries go
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "km", None, "11"))
    assert sorted(s.deleted) == ["self:km.knowledge-graph", "self:km.knowledge-vectors", "self:km.knowledge.kb-1"]
    assert not any(k.startswith("self:km.") for k in s.upstream)


def test_entries_left_upstream_by_an_earlier_run_are_removed():
    s = FakeSyncer()
    s._resource_versions = {"self:km.stale": "1", "self:kmx.other": "1"}
    s.downstream = provider_entries(with_kb=False)
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "km", "3", None))
    assert s.deleted == ["self:km.stale"]


def test_a_consumer_drc_resyncs_its_component_and_provider():
    s = FakeSyncer()
    s.components = {"agent": {"id": "agent", "category": "ODAComponent",
                              "resourceRelationship": [rel("dependsOn", "km.knowledge.kb-1")]},
                    "km": {"id": "km", "category": "ODAComponent"}}
    s.drcs = {"agent-kb": {"metadata": {"labels": {"oda.tmforum.org/componentName": "agent"}},
                           "status": {"bindings": [{"providerComponent": "km", "kbId": "kb-1"}]}}}
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "agent-kb", "4", None))
    assert sorted(s.resynced) == ["agent", "km"]
    assert s.upstream["self:agent"]["resourceRelationship"][0]["relationshipType"] == "dependsOn"
    # deleted: the components it named are re-synced from memory (the object is gone)
    s.drcs = {}
    s.components["agent"]["resourceRelationship"] = []
    asyncio.run(s.k8s_resource_changed_event("DataResourceConfig", "components", "agent-kb", None, "4"))
    assert sorted(s.resynced) == ["agent", "agent", "km", "km"]
    assert s.upstream["self:agent"]["resourceRelationship"] == []


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_update_replaces_the_relationship_rows(db):
    crud.create_resource(db, {"id": "c", "category": "ODAComponent", "resourceRelationship": [rel("provides", "a")]}, "")
    crud.update_resource(db, "c", {"category": "ODAComponent", "resourceRelationship": [rel("provides", "b"),
                                                                                       rel("dependsOn", "d")]})
    got = crud.get_resource(db, "c")
    assert sorted((r["relationshipType"], r["resource"]["id"]) for r in got.data["resourceRelationship"]) == [
        ("dependsOn", "d"), ("provides", "b")]
    assert "resourceRelationship" not in db.query(models.Resource).filter(models.Resource.id == "c").first().data
    crud.update_resource(db, "c", {"category": "ODAComponent", "resourceRelationship": []})
    assert "resourceRelationship" not in crud.get_resource(db, "c").data
    crud.update_resource(db, "c", {"category": "ODAComponent", "name": "no relationship key"})   # rows untouched
    assert crud.get_resource(db, "c").data["name"] == "no relationship key"


def test_ui_lists_data_resources_per_component():
    def res(rid, data):
        return models.Resource(id=rid, data=data)

    resources = [
        res("self:km", {"category": "ODAComponent"}),
        res("self:agent", {"category": "ODAComponent", "resourceRelationship": [rel("dependsOn", "self:km.knowledge.kb-1")]}),
        res("self:km.knowledge.kb-1", {"category": "DataResource", "name": "kb-1", "resourceStatus": "standby",
                                       "resourceCharacteristic": [{"name": "kind", "value": "knowledge"},
                                                                  {"name": "storeKind", "value": "hybrid"},
                                                                  {"name": "lifecycleStatus", "value": "draft"}],
                                       "resourceRelationship": [rel("providedBy", "self:km")]}),
        res("self:km.knowledge-vectors", {"category": "DataResource", "name": "knowledge-vectors",
                                          "resourceStatus": "available",
                                          "resourceCharacteristic": [{"name": "type", "value": "vectorStore"},
                                                                     {"name": "engine", "value": "qdrant"}],
                                          "resourceRelationship": [rel("providedBy", "self:km")]}),
    ]
    out = main.data_resources_by_component(resources)
    assert [(e["name"], e["label"], e["detail"], e["store"]) for e in out["self:km"]["provides"]] == [
        ("knowledge-vectors", "vectorStore", "qdrant", True), ("kb-1", "knowledge", "hybrid", False)]
    assert out["self:agent"]["dependsOn"] == [{"id": "self:km.knowledge.kb-1", "name": "kb-1", "status": "standby"}]
