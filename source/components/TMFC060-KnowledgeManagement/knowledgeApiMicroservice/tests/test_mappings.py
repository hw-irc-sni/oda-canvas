"""Bridge mappings: CRUD, the RDF shape of FTO's bridge files, edit reset, governance and synced read-only mappings."""
import json

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from .api import auth, running_api

pytestmark = pytest.mark.integration

NS = "https://example.org/bridge/"
B = Namespace(NS)
EDITOR, REVIEWER, APPROVER = "ontology-editor", "ontology-reviewer", "ontology-approver"
BRIDGE_TTL = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
<{NS}> a owl:Ontology .""".encode()


@pytest.fixture
async def api(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), mount_roots=[str(tmp_path)]) as client:
        yield client


async def setup(api):
    base = f"{api.base}/knowledgeSpecification"
    for mid, iri, prefix, kind in [("irc", "https://example.org/irc/", "irc", "ontology"),
                                   ("an", "https://example.org/an/", "an", "ontology"),
                                   ("bridge", NS, "bridge", "bridge")]:
        r = await api.post(base, json={"id": mid, "name": mid, "iri": iri, "prefix": prefix, "specKind": kind,
                                       "layer": "Implementation", "source": {"kind": "upload"}}, headers=auth(EDITOR))
        assert r.status_code == 201, r.text
    await api.put(f"{base}/bridge/content", content=BRIDGE_TTL, headers={**auth(EDITOR), "Content-Type": "text/turtle"})
    return f"{base}/bridge/mapping"


KPI = {"id": "m-kpi", "subject": "irc:KPI", "predicate": "rdfs:subClassOf", "object": "an:KPI", "confidence": "high",
       "rationale": "Same thing, same scope"}


async def export(api):
    content = await api.get(f"{api.base}/knowledgeSpecification/bridge/content", headers=auth())
    return Graph().parse(data=content.content, format="turtle")


async def test_crud_round_trip_and_bridge_file_shape(api):
    url = await setup(api)
    created = await api.post(url, json=KPI, headers=auth(EDITOR))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["lifecycleStatus"] == "draft" and body["status"] == "proposed" and body["subject"] == "irc:KPI"
    assert body["href"].endswith("/knowledgeSpecification/bridge/mapping/m-kpi")
    g = await export(api)
    node = B["m-kpi"]
    irc, an = URIRef("https://example.org/irc/KPI"), URIRef("https://example.org/an/KPI")
    assert (irc, RDFS.subClassOf, an) in g  # the asserted statement, as FTO writes it
    assert (node, RDF.type, B.mapping) in g
    assert {(p, o) for p, o in g.predicate_objects(node)} == {
        (RDF.type, B.mapping), (B.subject, irc), (B.predicate, RDFS.subClassOf), (B.object, an),
        (B.status, Literal("proposed")), (B.confidence, Literal("high")), (B.rationale, Literal("Same thing, same scope"))}
    assert (B.status, RDF.type, OWL.AnnotationProperty) in g and (B.mapping, RDF.type, OWL.Class) in g
    listed = await api.get(url, headers=auth("knowledge-viewer"))
    assert [m["id"] for m in listed.json()] == ["m-kpi"] and listed.headers["x-total-count"] == "1"
    got = await api.get(f"{url}/m-kpi", headers=auth())
    assert got.json() == body
    patched = await api.patch(f"{url}/m-kpi", json={"object": "an:Metric", "confidence": "medium"},
                              headers=auth(EDITOR))
    assert patched.status_code == 200 and patched.json()["object"] == "an:Metric"
    g = await export(api)
    assert (irc, RDFS.subClassOf, an) not in g and (irc, RDFS.subClassOf, URIRef("https://example.org/an/Metric")) in g
    assert (await api.delete(f"{url}/m-kpi", headers=auth(EDITOR))).status_code == 204
    g = await export(api)
    assert not list(g.predicate_objects(node))
    assert (await api.get(f"{url}/m-kpi", headers=auth())).status_code == 404


async def test_mappings_only_on_bridge_modules_and_validation(api):
    url = await setup(api)
    other = f"{api.base}/knowledgeSpecification/irc/mapping"
    assert (await api.post(other, json=KPI, headers=auth(EDITOR))).status_code == 409
    assert (await api.post(url, json={**KPI, "subject": "nope:KPI"}, headers=auth(EDITOR))).status_code == 400
    assert (await api.post(url, json={**KPI, "confidence": "huge"}, headers=auth(EDITOR))).status_code == 400
    assert (await api.post(url, json={k: v for k, v in KPI.items() if k != "object"},
                           headers=auth(EDITOR))).status_code == 400
    generated = await api.post(url, json={k: v for k, v in KPI.items() if k != "id"}, headers=auth(EDITOR))
    assert generated.json()["id"].startswith("m-")
    assert (await api.post(url, json=KPI, headers=auth("knowledge-viewer"))).status_code == 403
    assert (await api.post(url, json={**KPI, "subject": "https://full.example/iri"},
                           headers=auth(EDITOR))).status_code == 201


async def advance(api, url, *steps):
    for state, role in steps:
        r = await api.patch(url, json={"lifecycleStatus": state}, headers=auth(role))
        assert r.status_code == 200, r.text


async def test_editing_an_agreed_mapping_resets_it_to_draft(api):
    url = await setup(api)
    await api.post(url, json=KPI, headers=auth(EDITOR))
    m = f"{url}/m-kpi"
    await advance(api, m, ("proposed", EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER))
    assert (await api.get(m, headers=auth())).json()["status"] == "agreed"
    assert (B["m-kpi"], B.status, Literal("agreed")) in await export(api)
    edited = await api.patch(m, json={"rationale": "refined"}, headers=auth(EDITOR))
    assert edited.json()["lifecycleStatus"] == "draft" and edited.json()["status"] == "proposed"
    assert (B["m-kpi"], B.status, Literal("proposed")) in await export(api)


async def test_mapping_review_and_deprecation(api):
    url = await setup(api)
    await api.post(url, json=KPI, headers=auth(EDITOR))
    module = f"{api.base}/knowledgeSpecification/bridge"
    target = {"kind": "mapping", "id": "m-kpi"}
    assert (await api.post(f"{module}/review", json={"kind": "proposal", "target": target},
                           headers=auth(EDITOR))).status_code == 201
    assert (await api.post(f"{module}/review", json={"kind": "comment", "target": target, "text": "ok"},
                           headers=auth(REVIEWER))).status_code == 201
    await advance(api, f"{url}/m-kpi", ("inReview", REVIEWER))
    ruling = await api.post(f"{module}/review", json={"kind": "ruling", "target": target, "ruling": "agreed"},
                            headers=auth(APPROVER))
    assert ruling.status_code == 201, ruling.text
    m = f"{url}/m-kpi"
    await advance(api, m, ("published", APPROVER), ("deprecated", APPROVER))
    g = await export(api)
    assert (B["m-kpi"], B.status, Literal("agreed")) in g  # deprecated keeps the last bridge:status
    assert (B["m-kpi"], OWL.deprecated, Literal(True)) in g
    assert (await api.patch(m, json={"rationale": "x"}, headers=auth(EDITOR))).status_code == 409
    reviews = (await api.get(f"{module}/review", headers=auth())).json()
    assert [(r["kind"], r["target"]["kind"]) for r in reviews] == [("proposal", "mapping"), ("comment", "mapping"),
                                                                  ("ruling", "mapping")]
    assert (await api.patch(m, json={"lifecycleStatus": "draft"}, headers=auth(EDITOR))).status_code == 409
    assert (await api.delete(m, headers=auth(EDITOR))).status_code == 403
    assert (await api.delete(m, headers=auth(APPROVER))).status_code == 204


async def test_synced_bridge_mappings_are_read_only(api, tmp_path):
    from app.main import _registry_context
    from app.ontology import sync
    repo = tmp_path / "repo"
    (repo / "implementation").mkdir(parents=True)
    bridge_ns = "https://example.org/fed-bridge/"
    (repo / "implementation" / "irc-fto-bridge.ttl").write_text(f"""
@prefix bridge: <{bridge_ns}> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix ex: <https://example.org/x/> .
<{bridge_ns}> a owl:Ontology .
ex:A rdfs:subClassOf ex:B .
bridge:m-a a bridge:mapping ; bridge:subject ex:A ; bridge:predicate rdfs:subClassOf ; bridge:object ex:B ;
  bridge:status "agreed" ; bridge:confidence "high" ; bridge:rationale "ruled" .
bridge:m-b a bridge:mapping ; bridge:subject ex:C ; bridge:predicate rdfs:subClassOf ; bridge:object ex:D ;
  bridge:status "proposed" ; bridge:confidence "low" ; bridge:rationale "awaits review" .
""")
    (repo / "modules.json").write_text(json.dumps({"modules": {"fed-bridge": {
        "label": "Bridge", "iri": bridge_ns, "status": "proposed", "file": "implementation/irc-fto-bridge.ttl"}}}))
    result = await sync.sync_all(lambda: _registry_context(api.app), [{"name": "s", "mount": {"path": str(repo)}}])
    assert result["s"]["mappings"] == 2
    url = f"{api.base}/knowledgeSpecification/fed-bridge/mapping"
    listed = {m["id"]: m for m in (await api.get(url, headers=auth())).json()}
    assert {k: (v["lifecycleStatus"], v["status"]) for k, v in listed.items()} == {
        "m-a": ("published", "agreed"), "m-b": ("proposed", "proposed")}
    assert (await api.patch(f"{url}/m-b", json={"rationale": "x"}, headers=auth(EDITOR))).status_code == 409
    assert (await api.post(url, json=KPI, headers=auth(EDITOR))).status_code == 409
