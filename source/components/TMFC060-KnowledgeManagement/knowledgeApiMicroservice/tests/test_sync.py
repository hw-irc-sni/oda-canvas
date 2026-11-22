"""The federation repository sync (FTO layout), against a fixture shaped like the tfo repository."""
import json

import pytest

from app.ontology import sync

from .api import auth, running_api

pytestmark = pytest.mark.integration

FTO = "@prefix fto: <https://models.tmforum.org/ontology/fto/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .\n"


def make_repo(root, with_extra=True):
    (root / "foundational" / "ofo").mkdir(parents=True, exist_ok=True)
    (root / "telecom" / "an").mkdir(parents=True, exist_ok=True)
    (root / "foundational" / "ofo" / "ofo.ttl").write_text(
        FTO + "<https://example.org/ofo/> a owl:Ontology ; fto:layer fto:Foundational .")
    (root / "telecom" / "an" / "an.ttl").write_text(
        FTO + "<https://example.org/an/> a owl:Ontology ; owl:imports <https://example.org/ofo/> .")  # no fto:layer
    (root / "fto.ttl").write_text(FTO + "<https://example.org/fto/> a owl:Ontology .")
    modules = {
        "ofo": {"label": "OFO", "status": "draft", "prefix": "ofo", "iri": "https://example.org/ofo/",
                "file": "foundational/ofo/ofo.ttl", "imports": ["fto"], "owner": "Tiger Team"},
        "an-core": {"label": "AN core", "status": "in preparation", "prefix": "an", "iri": "https://example.org/an/",
                    "file": "telecom/an/an.ttl", "imports": ["ofo"]},
        "fto": {"label": "FTO meta", "status": "draft", "prefix": "fto", "iri": "https://example.org/fto/",
                "file": "fto.ttl"},
    }
    if with_extra:
        modules["etom"] = {"label": "eTOM", "status": "not lifted", "file": None}  # no iri, no file
    (root / "modules.json").write_text(json.dumps({"groups": [], "modules": modules}))
    (root / "catalog.json").write_text(json.dumps({"ontologies": {}}))


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), mount_roots=[str(tmp_path)]) as client:
        yield client


def registry(api):
    from app.main import _registry_context
    return lambda: _registry_context(api.app)


async def test_sync_registers_the_repository(api, tmp_path):
    repo = tmp_path / "tfo"
    make_repo(repo)
    source = {"name": "fto", "mount": {"path": str(repo)}}
    result = await sync.sync_all(registry(api), [source])
    assert result["fto"]["created"] == 4 and result["fto"]["metadataOnly"] == 1
    listed = (await api.get(f"{api.base}/knowledgeSpecification", headers=auth())).json()
    by_id = {m["id"]: m for m in listed}
    assert {k: by_id[k]["layer"] for k in by_id} == {"ofo": "Foundational", "an-core": "Telecom",
                                                     "fto": "Foundational", "etom": "Foundational"}
    an = by_id["an-core"]
    assert an["@type"] == "FederatedKnowledgeSpecification" and an["@baseType"] == "KnowledgeSpecification"
    assert an["@schemaLocation"].endswith("/schema/FederatedKnowledgeSpecification.schema.json")
    assert an["lifecycleStatus"] == "published" and an["sourceStatus"] == "in preparation"
    assert [r["id"] for r in an["imports"]] == ["ofo"]
    assert [r["id"] for r in by_id["ofo"]["imports"]] == ["fto"]
    assert by_id["ofo"]["owner"]["partyOrPartyRole"]["name"] == "Tiger Team"
    assert by_id["etom"]["iri"] == "urn:tmfc060:federation:fto:etom"
    etom_content = await api.get(f"{api.base}/knowledgeSpecification/etom/content", headers=auth())
    assert etom_content.status_code == 200 and not etom_content.content.strip()

    # a second sync with no changes updates nothing; a removed module is deprecated, not deleted
    again = await sync.sync_all(registry(api), [source])
    assert again["fto"]["updated"] == 0 and again["fto"]["created"] == 0
    make_repo(repo, with_extra=False)
    third = await sync.sync_all(registry(api), [source])
    assert third["fto"]["deprecated"] == 1
    etom = (await api.get(f"{api.base}/knowledgeSpecification/etom", headers=auth())).json()
    assert etom["lifecycleStatus"] == "deprecated"


async def test_a_failing_source_does_not_stop_the_others(api, tmp_path):
    good = tmp_path / "good"
    make_repo(good)
    result = await sync.sync_all(registry(api), [{"name": "bad", "mount": {"path": "/etc"}},
                                                 {"name": "good", "mount": {"path": str(good)}}])
    assert "error" in result["bad"] and result["good"]["created"] == 4


def test_parse_sources():
    assert sync.parse_sources('[{"name": "a", "git": {"url": "https://h/x.git"}}]')[0]["name"] == "a"
    with pytest.raises(ValueError):
        sync.parse_sources('[{"name": "a"}]')
