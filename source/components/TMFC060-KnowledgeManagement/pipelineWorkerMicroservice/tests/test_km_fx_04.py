"""KM-FX-04 Knowledge Fusion: SHACL and functional conflicts withheld as proposals, OWL-RL inferences, end to end."""
import json

import httpx
import pytest

from worker.client import ModelUnavailable
from worker.steps import default_handlers, noop
from worker.steps.km_fx_04 import handler, inferred, graph_of

from .test_km_fx_03 import store
from .test_worker import make_worker, wait_done

AS = "https://example.org/as#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
ONTOLOGY = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <{RDFS}> . @prefix as: <{AS}> .
<https://example.org/as> a owl:Ontology .
as:Site a owl:Class . as:Cell a owl:Class ; rdfs:subClassOf as:Site . as:Alarm a owl:Class .
as:operator a owl:DatatypeProperty, owl:FunctionalProperty . as:severity a owl:DatatypeProperty .
as:affects a owl:ObjectProperty . as:affectedBy a owl:ObjectProperty ; owl:inverseOf as:affects ."""
SHAPES = f"""@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix as: <{AS}> .
as:AlarmShape a sh:NodeShape ; sh:targetClass as:Alarm ;
  sh:property [ sh:path as:severity ; sh:in ( "minor" "major" "critical" ) ] ."""


def extraction(text):
    entities = [{"id": "e2", "type": AS + "Cell", "label": "Cell 4711"}]
    relations, attributes = [], []
    if "alarm" in text.lower():
        entities.append({"id": "e1", "type": AS + "Alarm", "label": "Mains alarm"})
        relations.append({"subject": "e1", "predicate": AS + "affects", "object": "e2"})
        attributes.append({"subject": "e1", "predicate": AS + "severity",
                           "value": "apocalyptic" if "apocalyptic" in text else "major"})
    if "ACME" in text:
        attributes.append({"subject": "e2", "predicate": AS + "operator", "value": "ACME"})
    return {"entities": entities, "relations": relations, "attributes": attributes}


def fake_model(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    messages = json.loads(request.content)["messages"]
    if "Suggest how a reviewer" in messages[0]["content"]:
        content = {"action": "keepExisting", "rationale": "the curated value wins"}
    else:
        content = extraction(messages[-1]["content"])
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


async def publish(api, mid, kind, ttl):
    base = f"{api.base}/knowledgeSpecification"
    await api.post(base, json={"id": mid, "name": mid, "iri": f"https://example.org/{mid}", "specKind": kind,
                               "layer": "Telecom", "source": {"kind": "upload"}})
    await api.put(f"{base}/{mid}/content", content=ttl.encode(), headers={"Content-Type": "text/turtle"})
    for s in ("proposed", "inReview", "agreed", "published"):
        assert (await api.patch(f"{base}/{mid}", json={"lifecycleStatus": s})).status_code == 200


@pytest.fixture
async def fused(api):
    from app.models import ModelResolver
    state = api.app.state
    state.settings.governance_require_validation = False
    state.models = ModelResolver(state.settings.model_copy(update={
        "openai_base_url": "http://m.test/v1", "openai_chat_model": "c", "openai_embedding_model": "e"}),
        transport=httpx.MockTransport(fake_model), use_in_cluster=False)
    await state.models.resolve()
    await publish(api, "as", "ontology", ONTOLOGY)
    await publish(api, "as-shapes", "shapes", SHAPES)
    await api.patch(f"{api.base}/knowledge/kb", json={"conformsTo": [{"id": "as"}, {"id": "as-shapes"}]})
    # a curated cell with its operator
    await api.post(f"{api.base}/knowledge/kb/graph", content=f"""<urn:x:cell> a <{AS}Cell> ;
        <{RDFS}label> "Cell 4711" ; <{AS}operator> "Globex" .""".encode(), headers={"Content-Type": "text/turtle"})
    yield api


async def ingest(api, text):
    doc = (await api.post(f"{api.base}/knowledge/kb/document",
                          files={"file": ("d.md", text.encode(), "text/markdown")})).json()["id"]
    pid = (await api.post(f"{api.base}/knowledge/kb/ingest", json={"documentId": [doc]})).json()["id"]
    worker = make_worker(api, "w1", {**default_handlers(), "KM-FX-05": store, "KM-FX-06": noop})
    worker.start()
    try:
        assert (await wait_done(api, pid))["state"] == "completed"
    finally:
        await worker.stop()
    return doc


async def ask(api, query):
    r = await api.post(f"{api.base}/knowledge/kb/query", content=query.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    return r.json()["boolean"]


ALARM = "urn:tmfc060:kb:kb:entity:Alarm:mains-alarm"


async def test_conflicts_become_proposals_and_are_not_written(fused):
    api = fused
    doc = await ingest(api, "# Outage\n\nThe mains alarm (severity apocalyptic) affects Cell 4711, run by ACME.")
    # withheld: the shape violation and the functional clash
    assert not await ask(api, f'ASK {{ ?a <{AS}severity> "apocalyptic" }}')
    assert not await ask(api, f'ASK {{ <urn:x:cell> <{AS}operator> "ACME" }}')
    # the rest went through, linked to the curated cell, with OWL-RL inferences
    assert await ask(api, f"ASK {{ <{ALARM}> <{AS}affects> <urn:x:cell> }}")
    assert await ask(api, f"ASK {{ <urn:x:cell> <{AS}affectedBy> <{ALARM}> }}")  # owl:inverseOf
    assert await ask(api, f"ASK {{ <urn:x:cell> a <{AS}Site> }}")  # rdfs:subClassOf
    proposals = (await api.get(f"{api.base}/knowledge/kb/proposal")).json()
    conflicts = [p for p in proposals if p["kind"] == "dataConflict"]
    assert len(conflicts) == 2 and all(p["source"] == doc and p["state"] == "proposed" for p in conflicts)
    assert all(p["suggestion"] == {"action": "keepExisting", "rationale": "the curated value wins"} for p in conflicts)
    clash = next(p for p in conflicts if "clash" in p["details"])
    assert clash["details"]["clash"]["existing"] == ["Globex"] and clash["details"]["clash"]["new"] == ["ACME"]
    shape = next(p for p in conflicts if "violation" in p["details"])
    assert shape["details"]["violation"]["value"] == "apocalyptic"
    # a person accepts the clash: now it is written
    r = await api.patch(f"{api.base}/knowledge/kb/proposal/{clash['id']}", json={"state": "accepted"})
    assert r.status_code == 200
    assert await ask(api, f'ASK {{ <urn:x:cell> <{AS}operator> "ACME" }}')


async def test_conformant_data_passes_with_inferences(fused):
    api = fused
    await ingest(api, "# Outage\n\nThe mains alarm (severity major) affects Cell 4711.")
    assert await ask(api, f'ASK {{ <{ALARM}> <{AS}severity> "major" }}')
    assert await ask(api, f"ASK {{ <urn:x:cell> <{AS}affectedBy> <{ALARM}> }}")
    proposals = (await api.get(f"{api.base}/knowledge/kb/proposal")).json()
    assert [p for p in proposals if p["kind"] == "dataConflict"] == []


class FakeClient:
    def __init__(self, ontology="", shapes=""):
        self.texts = {"ontology": ontology, "shapes": shapes}
        self.proposed = None

    async def ontology(self, kb, part):
        return self.texts[part]

    async def query(self, kb, sparql):
        return {"results": {"bindings": []}}

    async def chat(self, *a, **k):
        raise ModelUnavailable("no route")

    async def propose(self, kb, source, process, items):
        self.proposed = (source, items)
        return []


async def test_no_model_no_suggestion_and_other_proposals():
    triples = [["urn:a", AS + "operator", "X", "literal", None], ["urn:a", AS + "operator", "Y", "literal", None]]
    client = FakeClient(ontology=ONTOLOGY)
    task = {"payload": {"kbId": "kb", "documentId": "d"}, "processId": "pf", "chain": "d", "input": {
        "kind": "text", "documentId": "d", "triples": triples, "chunks": [],
        "proposedTerms": [{"term": AS + "Battery", "role": "class", "count": 2, "example": "x"}],
        "linkCandidates": [{"iri": "urn:n", "label": "N", "type": AS + "Cell", "candidates": ["urn:a", "urn:b"]}]}}
    result = await handler()(task, client)
    assert result["withheld"] == 2  # two values of a functional property: both withheld
    assert not any(t[1] == AS + "operator" for t in result["triples"])
    source, items = client.proposed
    assert source == "d" and [i["kind"] for i in items] == ["dataConflict", "ontologyTerm", "entityLink"]
    assert "suggestion" not in items[0]  # no model: no suggestion, no failure


def test_reasoning_limit():
    from rdflib import Graph
    extra, warning = inferred(Graph(), graph_of([["urn:a", AS + "x", "urn:b", "iri", None]]), limit=0)
    assert extra == [] and "reasoning skipped" in warning
