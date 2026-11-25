"""Knowledge-bases: CRUD with the Qdrant collection, the graph (Graph Store Protocol), read-only queries, RBAC."""
import json

import pytest
from qdrant_client import QdrantClient

from app.db.models import StateTransition

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, VIEWER = "knowledge-editor", "knowledge-viewer"
ONT_EDITOR, REVIEWER, APPROVER = "ontology-editor", "ontology-reviewer", "ontology-approver"
ONTOLOGY = b"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix as: <https://example.org/assurance#> .
<https://example.org/assurance> a owl:Ontology . as:Incident a owl:Class ; rdfs:label "Incident" ."""
DATA = b"""@prefix as: <https://example.org/assurance#> .
<https://example.org/inc/1> a as:Incident ; as:affects <https://example.org/cell/4711> .
<https://example.org/inc/2> a as:Incident ; as:affects <https://example.org/cell/4712> ."""
MORE = b"""<https://example.org/inc/3> a <https://example.org/assurance#Incident> ."""
JSONLD = json.dumps({"@id": "https://example.org/inc/9", "@type": "https://example.org/assurance#Incident"}).encode()
SELECT = """PREFIX as: <https://example.org/assurance#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?i ?label WHERE { ?i a ?c . ?c rdfs:label ?label } ORDER BY ?i"""


@pytest.fixture
async def api(postgres_url, qdrant_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url,
                           governance_require_validation=False) as client:
        client.qdrant = QdrantClient(url=qdrant_url)
        yield client


async def published_module(api, mid="assurance"):
    base = f"{api.base}/knowledgeSpecification"
    r = await api.post(base, json={"id": mid, "name": mid, "iri": "https://example.org/assurance", "layer": "Telecom",
                                   "specKind": "ontology", "source": {"kind": "upload"}}, headers=auth(ONT_EDITOR))
    assert r.status_code == 201, r.text
    await api.put(f"{base}/{mid}/content", content=ONTOLOGY, headers={**auth(ONT_EDITOR), "Content-Type": "text/turtle"})
    for state, role in [("proposed", ONT_EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER),
                        ("published", APPROVER)]:
        assert (await api.patch(f"{base}/{mid}", json={"lifecycleStatus": state}, headers=auth(role))).status_code == 200


async def new_kb(api, role=EDITOR, **fields):
    payload = {"id": "incidents", "name": "Incidents", **fields}
    r = await api.post(f"{api.base}/knowledge", json=payload, headers=auth(role))
    assert r.status_code == 201, r.text
    return r.json()


def url(api, kb="incidents", sub=""):
    return f"{api.base}/knowledge/{kb}{sub}"


async def put_graph(api, data=DATA, media="text/turtle", role=EDITOR, kb="incidents"):
    return await api.put(url(api, kb, "/graph"), content=data, headers={**auth(role), "Content-Type": media})


async def query(api, text, role=VIEWER, kb="incidents", accept=None, form=False, **params):
    headers = auth(role)
    if accept:
        headers["Accept"] = accept
    if form:
        return await api.post(url(api, kb, "/query"), data={"query": text}, params=params, headers=headers)
    return await api.post(url(api, kb, "/query"), content=text.encode(), params=params,
                          headers={**headers, "Content-Type": "application/sparql-query"})


async def test_crud_with_the_vector_collection(api):
    await published_module(api)
    body = await new_kb(api, conformsTo=[{"iri": "https://example.org/assurance"}], knowledgeCategory=["know-what"])
    assert body["lifecycleStatus"] == "draft" and body["storeKind"] == "hybrid" and body["@type"] == "Knowledge"
    assert body["href"].endswith("/knowledge/incidents")
    assert body["conformsTo"][0]["id"] == "assurance" and body["conformsTo"][0]["href"].startswith("https://")
    assert body["statistics"] == {"triples": 0, "documents": 0, "chunks": 0}
    collection = api.qdrant.get_collection("tmfc060-kb-incidents")
    assert collection.config.params.vectors["dense"].size == 1024
    assert "sparse" in collection.config.params.sparse_vectors
    listed = await api.get(f"{api.base}/knowledge", headers=auth(VIEWER))
    assert [k["id"] for k in listed.json()] == ["incidents"] and listed.headers["x-total-count"] == "1"
    patched = await api.patch(url(api), json={"description": "All incidents", "retrieval": {"topK": 5}},
                              headers=auth(EDITOR))
    assert patched.status_code == 200 and patched.json()["description"] == "All incidents"
    assert (await api.patch(url(api), json={"storeKind": "graph"}, headers=auth(EDITOR))).status_code == 400
    await put_graph(api)
    assert (await api.get(url(api), headers=auth())).json()["statistics"]["triples"] == 4
    in_use = await api.delete(f"{api.base}/knowledgeSpecification/assurance", headers=auth(APPROVER))
    assert in_use.status_code == 409 and "incidents" in in_use.json()["message"]
    assert (await api.delete(url(api), headers=auth(EDITOR))).status_code == 204
    assert not api.qdrant.collection_exists("tmfc060-kb-incidents")
    assert (await api.get(url(api), headers=auth())).status_code == 404
    assert api.app.state.graphs.count("urn:tmfc060:kb:incidents") == 0
    assert [e for e in api.app.state.events.types() if e.startswith("Knowledge") and "Specification" not in e] == [
        "KnowledgeCreateEvent", "KnowledgeAttributeValueChangeEvent", "KnowledgeAttributeValueChangeEvent",
        "KnowledgeDeleteEvent"]


async def test_validation(api):
    base = f"{api.base}/knowledge"
    for payload in [{}, {"name": "x", "storeKind": "tree"}, {"name": "x", "knowledgeCategory": ["know-when"]},
                    {"name": "x", "source": [{"kind": "ftp"}]}, {"name": "x", "retrieval": {"topK": 0}},
                    {"name": "x", "conformsTo": [{"id": "nope"}]}, {"name": "x", "id": "Bad Id"}]:
        assert (await api.post(base, json=payload, headers=auth(EDITOR))).status_code == 400, payload
    graph_only = await api.post(base, json={"name": "Graph Only KB", "storeKind": "graph"}, headers=auth(EDITOR))
    assert graph_only.json()["id"] == "graph-only-kb" and "chunks" not in graph_only.json()["statistics"]
    assert not api.qdrant.collection_exists("tmfc060-kb-graph-only-kb")
    assert (await api.post(base, json={"name": "x", "id": "graph-only-kb"}, headers=auth(EDITOR))).status_code == 409


@pytest.mark.parametrize("media", ["text/turtle", "application/ld+json"])
async def test_graph_round_trips(api, media):
    from rdflib import Graph
    await new_kb(api, storeKind="graph")
    data = DATA if media == "text/turtle" else JSONLD
    expected = Graph().parse(data=data, format="turtle" if media == "text/turtle" else "json-ld")
    assert (await put_graph(api, data, media)).status_code == 204
    got = await api.get(url(api, sub="/graph"), headers={**auth(), "Accept": media})
    assert got.headers["content-type"].startswith(media)
    assert Graph().parse(data=got.content, format="turtle" if media == "text/turtle" else "json-ld").isomorphic(expected)
    merged = await api.post(url(api, sub="/graph"), content=MORE, headers={**auth(EDITOR), "Content-Type": "text/turtle"})
    assert merged.status_code == 204
    assert api.app.state.graphs.count("urn:tmfc060:kb:incidents") == len(expected) + 1
    assert (await put_graph(api, b"not turtle <")).status_code == 400
    assert api.app.state.graphs.count("urn:tmfc060:kb:incidents") == len(expected) + 1  # unchanged
    assert (await put_graph(api, DATA, "application/rdf+xml")).status_code == 415
    assert (await api.delete(url(api, sub="/graph"), headers=auth(EDITOR))).status_code == 204
    assert api.app.state.graphs.count("urn:tmfc060:kb:incidents") == 0
    assert (await api.get(url(api), headers=auth())).status_code == 200  # the KB is kept


async def test_query(api):
    await published_module(api)
    await new_kb(api, storeKind="graph", conformsTo=[{"id": "assurance"}])
    await put_graph(api)
    r = await query(api, SELECT)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/sparql-results+json")
    rows = r.json()["results"]["bindings"]
    assert [(b["i"]["value"], b["label"]["value"]) for b in rows] == [
        ("https://example.org/inc/1", "Incident"), ("https://example.org/inc/2", "Incident")]  # KB joined with ontology
    ask = await query(api, "ASK { <https://example.org/inc/1> ?p ?o }", form=True)
    assert ask.json()["boolean"] is True
    construct = await query(api, "CONSTRUCT { ?i a ?c } WHERE { ?i a ?c }", accept="text/turtle")
    assert construct.headers["content-type"].startswith("text/turtle") and b"inc/1" in construct.content
    update = await query(api, "INSERT DATA { <urn:a> <urn:b> <urn:c> }")
    assert update.status_code == 400
    service = await query(api, "SELECT * WHERE { SERVICE <https://elsewhere.example/sparql> { ?s ?p ?o } }")
    assert service.status_code == 403 and service.json()["code"] == "SERVICE_NOT_ALLOWED"
    assert (await query(api, "SELEKT nonsense")).status_code == 400
    assert (await query(api, SELECT, timeout="0")).status_code == 400


async def test_query_limits(postgres_url, qdrant_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url, query_row_cap=1,
                           query_timeout_max=1) as api:
        await new_kb(api, storeKind="graph")
        await put_graph(api)
        capped = await query(api, "SELECT ?s WHERE { ?s ?p ?o }")
        assert len(capped.json()["results"]["bindings"]) == 1 and capped.headers["x-result-truncated"] == "true"
        slow = """SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l . ?m ?n2 ?o .
                  ?p ?q ?r . ?s ?t ?u . ?v ?w ?x . ?y ?z ?aa . ?bb ?cc ?dd . ?ee ?ff ?gg . ?hh ?ii ?jj }"""
        timed_out = await query(api, slow, timeout="60")  # capped to the 1 s maximum
        assert timed_out.status_code == 504, timed_out.text


async def test_rbac_and_ownership(api):
    await new_kb(api, storeKind="graph")
    assert (await api.post(f"{api.base}/knowledge", json={"name": "x"}, headers=auth(VIEWER))).status_code == 403
    assert (await put_graph(api, role=VIEWER)).status_code == 403
    assert (await api.patch(url(api), json={"description": "x"}, headers=auth(VIEWER))).status_code == 403
    assert (await query(api, SELECT, role=VIEWER)).status_code == 200
    # a component owns the KBs it creates; other components cannot change them; editors can
    own = await new_kb(api, role="canvasRole", id="mine", storeKind="graph")
    assert own["relatedParty"] == [{"role": "owner", "@type": "RelatedPartyRefOrPartyRoleRef",
                                    "partyOrPartyRole": {"id": "canvas-portal", "name": "canvas-portal",
                                                         "@type": "PartyRef"}}]
    assert (await put_graph(api, role="canvasRole", kb="mine")).status_code == 204
    other = auth("canvasRole", extra={"azp": "other-component"})
    r = await api.put(url(api, "mine", "/graph"), content=DATA, headers={**other, "Content-Type": "text/turtle"})
    assert r.status_code == 403
    assert (await api.delete(url(api, "incidents"), headers=auth("canvasRole"))).status_code == 403
    assert (await api.patch(url(api, "mine"), json={"relatedParty": []}, headers=auth(EDITOR))).json()[
        "relatedParty"][0]["role"] == "owner"  # the owner is kept
    assert (await api.delete(url(api, "mine"), headers=auth(EDITOR))).status_code == 204


@pytest.mark.parametrize("role", [REVIEWER, APPROVER])
async def test_reviewers_and_approvers_read_what_they_govern_but_not_its_content(api, role):
    await new_kb(api, storeKind="graph")
    listed = await api.get(f"{api.base}/knowledge", headers=auth(role))
    assert listed.status_code == 200 and [kb["id"] for kb in listed.json()] == ["incidents"]
    assert (await api.get(url(api), headers=auth(role))).status_code == 200
    for sub in ("/graph", "/document", "/proposal"):
        assert (await api.get(url(api, sub=sub), headers=auth(role))).status_code == 403, sub
    assert (await query(api, SELECT, role=role)).status_code == 403
    assert (await api.post(url(api, sub="/search"), json={"query": "x"}, headers=auth(role))).status_code == 403
    assert (await api.patch(url(api), json={"description": "x"}, headers=auth(role))).status_code == 403
    assert (await api.get(f"{api.base}/knowledge", headers=auth(ONT_EDITOR))).status_code == 403


async def test_lifecycle(api):
    await new_kb(api, storeKind="graph")
    steps = [("proposed", EDITOR), ("inReview", REVIEWER), ("rejected", APPROVER), ("draft", EDITOR),
             ("proposed", EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER), ("published", APPROVER)]
    for state, role in steps:
        wrong = VIEWER if role != VIEWER else EDITOR
        assert (await api.patch(url(api), json={"lifecycleStatus": state}, headers=auth(wrong))).status_code == 403
        r = await api.patch(url(api), json={"lifecycleStatus": state}, headers=auth(role))
        assert r.status_code == 200 and r.json()["lifecycleStatus"] == state, r.text
    assert (await api.patch(url(api), json={"lifecycleStatus": "draft"}, headers=auth(EDITOR))).status_code == 409
    assert (await put_graph(api)).status_code == 204  # published is still writable
    assert (await api.patch(url(api), json={"lifecycleStatus": "deprecated"},
                            headers=auth(APPROVER))).status_code == 200
    assert (await put_graph(api)).status_code == 409
    assert (await api.patch(url(api), json={"description": "x"}, headers=auth(EDITOR))).status_code == 409
    assert (await api.get(url(api, sub="/graph"), headers=auth())).status_code == 200  # still readable
    assert (await query(api, "ASK { ?s ?p ?o }")).json()["boolean"] is True
    async with api.app.state.sessions() as s:
        from sqlalchemy import select
        audit = (await s.scalars(select(StateTransition).where(StateTransition.target_kind == "knowledge"))).all()
    assert len(audit) == 9
    assert api.app.state.events.types().count("KnowledgeStateChangeEvent") == 9


async def test_statistics_without_qdrant(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url="http://127.0.0.1:9") as api:
        r = await api.post(f"{api.base}/knowledge", json={"name": "Vectors"}, headers=auth(EDITOR))
        assert r.status_code == 503  # the collection cannot be created
        r = await api.post(f"{api.base}/knowledge", json={"name": "Graph", "storeKind": "graph"}, headers=auth(EDITOR))
        assert r.status_code == 201
        async with api.app.state.sessions() as s:
            from app.db.models import Knowledge
            row = await s.get(Knowledge, "graph")
            row.store_kind = "hybrid"  # as if Qdrant went down after creation
            await s.commit()
        stats = (await api.get(f"{api.base}/knowledge/graph", headers=auth())).json()["statistics"]
        assert stats == {"triples": 0, "documents": 0}
