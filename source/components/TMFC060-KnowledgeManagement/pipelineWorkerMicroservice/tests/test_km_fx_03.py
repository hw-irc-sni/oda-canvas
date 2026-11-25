"""KM-FX-03 Knowledge Generation: entity linking and PROV-O, unit and end to end with the real API."""
import asyncio
import json

import httpx
import pytest

from worker.steps import default_handlers, noop
from worker.steps.km_fx_03 import PROV, handler, normalise
from worker.steps.rdf_out import ntriples
from worker.steps.vocabulary import RDF, RDFS

from .test_worker import make_worker, wait_done

AS = "https://example.org/as#"


def rows(*entries):
    return {"results": {"bindings": [{"e": {"type": "uri", "value": e}, "label": {"type": "literal", "value": label},
                                      "c0": {"type": "uri", "value": c0}} for e, label, c0 in entries]}}


class FakeClient:
    worker = "w1"

    def __init__(self, result):
        self.result = result

    async def query(self, kb, sparql):
        return self.result


def task_with(entities, triples, chunks, kind="text"):
    return {"id": "tk-3", "processId": "pf-1", "payload": {"kbId": "kb", "documentId": "doc-1"},
            "input": {"kind": kind, "documentId": "doc-1", "triples": triples, "entities": entities,
                      "chunks": chunks, "proposedTerms": [], "dropped": 0}}


NEW = "urn:tmfc060:kb:kb:entity:Cell:cell-4711"
ALARM = "urn:tmfc060:kb:kb:entity:Alarm:mains"


def base_task():
    return task_with(
        [{"iri": NEW, "type": AS + "Cell", "label": "Cell 4711", "chunkIds": ["doc-1:0"]},
         {"iri": ALARM, "type": AS + "Alarm", "label": "Mains", "chunkIds": ["doc-1:0"]}],
        [[NEW, RDF + "type", AS + "Cell", "iri", None], [ALARM, AS + "affects", NEW, "iri", None]],
        [{"chunkId": "doc-1:0", "text": "x", "entityIri": [NEW, ALARM], "classIri": [AS + "Alarm", AS + "Cell"]}])


def test_normalise():
    assert normalise("  Céll-4711! ") == normalise("cell 4711") == "cell 4711"


async def test_a_single_match_is_linked():
    existing = "urn:x:cell-4711"
    result = await handler()(base_task(), FakeClient(rows((existing, "CÉLL 4711", AS + "Cell"),
                                                          (NEW, "Cell 4711", AS + "Cell"))))  # itself is ignored
    assert result["links"] == {NEW: existing}
    triples = {tuple(t[:3]) for t in result["triples"]}
    assert (existing, RDF + "type", AS + "Cell") in triples and (ALARM, AS + "affects", existing) in triples
    assert not any(NEW in t[:3] for t in triples)
    assert existing in result["chunks"][0]["entityIri"]


async def test_several_matches_link_nothing():
    result = await handler()(base_task(), FakeClient(rows(("urn:x:a", "Cell 4711", AS + "Cell"),
                                                          ("urn:x:b", "cell-4711", AS + "Cell"))))
    assert result["links"] == {}
    assert result["linkCandidates"] == [{"iri": NEW, "label": "Cell 4711", "type": AS + "Cell",
                                         "candidates": ["urn:x:a", "urn:x:b"]}]


async def test_provenance_triples():
    result = await handler()(base_task(), FakeClient(rows()))
    triples = {tuple(t[:3]) for t in result["triples"]}
    chunk, doc, process = "urn:tmfc060:kb:kb:chunk:doc-1:0", "urn:tmfc060:kb:kb:document:doc-1", "urn:tmfc060:process:pf-1"
    for expected in [(NEW, PROV + "wasDerivedFrom", chunk), (chunk, PROV + "wasDerivedFrom", doc),
                     (NEW, PROV + "wasGeneratedBy", process), (process, RDF + "type", PROV + "Activity"),
                     (process, PROV + "wasInformedBy", "urn:tmfc060:task:tk-3"),
                     (process, PROV + "wasAssociatedWith", "urn:tmfc060:worker:w1")]:
        assert expected in triples
    assert any(t[1] == PROV + "generatedAtTime" and t[0] == NEW for t in result["triples"])
    assert result["provenance"] == 2


async def test_rdf_and_tmf_provenance():
    rdf = task_with([], [["urn:a", RDF + "type", AS + "Cell", "iri", None]], [], kind="rdf")
    triples = {tuple(t[:3]) for t in (await handler()(rdf, FakeClient(rows())))["triples"]}
    assert ("urn:a", PROV + "wasDerivedFrom", "urn:tmfc060:kb:kb:document:doc-1") in triples
    href = "https://inv.example/tmf-api/resourceInventoryManagement/v5/resource/r-1"
    tmf = task_with([], [[href, RDF + "type", AS + "Cell", "iri", None]],
                    [{"chunkId": "tmf639:r-1", "text": "x", "entityIri": [href]}], kind="tmf")
    tmf["input"].pop("documentId")
    triples = {tuple(t[:3]) for t in (await handler()(tmf, FakeClient(rows())))["triples"]}
    assert ("urn:tmfc060:kb:kb:chunk:tmf639:r-1", PROV + "hadPrimarySource", href) in triples


def test_ntriples():
    out = ntriples([["urn:a", RDFS + "label", 'say "hi"\n', "literal", "@en"], ["urn:a", "urn:p", "urn:b", "iri"],
                    ["urn:a", "urn:n", "5", "literal", "http://www.w3.org/2001/XMLSchema#integer"]])
    assert out.decode().splitlines() == [
        f'<urn:a> <{RDFS}label> "say \\"hi\\"\\n"@en .', "<urn:a> <urn:p> <urn:b> .",
        '<urn:a> <urn:n> "5"^^<http://www.w3.org/2001/XMLSchema#integer> .']


# ---- end to end ----------------------------------------------------------------------------------------------
ONTOLOGY = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <{RDFS}> . @prefix as: <{AS}> .
<https://example.org/as> a owl:Ontology . as:Site a owl:Class . as:Cell a owl:Class ; rdfs:subClassOf as:Site .
as:Alarm a owl:Class . as:affects a owl:ObjectProperty .""".encode()


def fake_model(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    text = json.loads(request.content)["messages"][-1]["content"]
    entities = [{"id": "e1", "type": AS + "Cell", "label": "Cell 4711"}] if "4711" in text else []
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
        {"entities": entities, "relations": [], "attributes": []})}}]})


async def store(task, client):
    """Stands in for KM-FX-05 (#44): write the KM-FX-03 triples to the document's source graph."""
    result = task["input"].get("passed") or task["input"]
    r = await client.http.put(f"/internal/knowledge/{task['payload']['kbId']}/graph",
                              params={"source": task["payload"]["documentId"]}, content=ntriples(result["triples"]),
                              headers={**client.headers(), "Content-Type": "text/turtle"})  # N-Triples is Turtle
    r.raise_for_status()
    return {}


async def count_cells(api):
    q = f"SELECT DISTINCT ?e WHERE {{ ?e a <{AS}Cell> }}"
    r = await api.post(f"{api.base}/knowledge/kb/query", content=q.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    return sorted(b["e"]["value"] for b in r.json()["results"]["bindings"])


@pytest.fixture
async def linked_api(api):
    from app.models import ModelResolver
    state = api.app.state
    state.settings.governance_require_validation = False
    state.models = ModelResolver(state.settings.model_copy(update={
        "openai_base_url": "http://m.test/v1", "openai_chat_model": "c", "openai_embedding_model": "e"}),
        transport=httpx.MockTransport(fake_model), use_in_cluster=False)
    await state.models.resolve()
    base = f"{api.base}/knowledgeSpecification"
    await api.post(base, json={"id": "as", "name": "as", "iri": "https://example.org/as", "layer": "Telecom",
                               "specKind": "ontology", "source": {"kind": "upload"}})
    await api.put(f"{base}/as/content", content=ONTOLOGY, headers={"Content-Type": "text/turtle"})
    for s in ("proposed", "inReview", "agreed", "published"):
        assert (await api.patch(f"{base}/as", json={"lifecycleStatus": s})).status_code == 200
    r = await api.patch(f"{api.base}/knowledge/kb", json={"conformsTo": [{"id": "as"}]})
    assert r.status_code == 200, r.text
    yield api


async def run(api, body):
    pid = (await api.post(f"{api.base}/knowledge/kb/ingest", json=body)).json()["id"]
    worker = make_worker(api, "w1", {**default_handlers(), "KM-FX-04": noop, "KM-FX-05": store, "KM-FX-06": noop})
    worker.start()
    try:
        assert (await wait_done(api, pid))["state"] == "completed"
    finally:
        await worker.stop()
    return pid


async def upload(api, name, text):
    r = await api.post(f"{api.base}/knowledge/kb/document", files={"file": (name, text, "text/markdown")})
    return r.json()["id"]


async def test_reingest_creates_no_duplicates_and_links_across_sources(linked_api):
    api = linked_api
    doc = await upload(api, "a.md", b"# Outage\n\nCell 4711 is down.")
    await run(api, {"documentId": [doc]})
    first = await count_cells(api)
    assert first == ["urn:tmfc060:kb:kb:entity:Cell:cell-4711"]
    await run(api, {"documentId": [doc]})  # re-ingest
    assert await count_cells(api) == first
    # provenance is there
    q = f"""ASK {{ <{first[0]}> <{PROV}wasDerivedFrom> ?chunk ; <{PROV}wasGeneratedBy> ?p .
                  ?chunk <{PROV}wasDerivedFrom> <urn:tmfc060:kb:kb:document:{doc}> . ?p a <{PROV}Activity> }}"""
    r = await api.post(f"{api.base}/knowledge/kb/query", content=q.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    assert r.json()["boolean"] is True
    # a manually curated entity with an altLabel: a new document's mention links to it, not to a new node
    await api.post(f"{api.base}/knowledge/kb/graph", content=f"""<urn:x:site-cell> a <{AS}Cell> ;
        <http://www.w3.org/2004/02/skos/core#altLabel> "CELL-4711" .""".encode(), headers={"Content-Type": "text/turtle"})
    other = await upload(api, "b.md", b"# Report\n\nCell 4711 was restored.")
    await run(api, {"documentId": [other]})
    q = f"SELECT ?e WHERE {{ ?e <{PROV}wasDerivedFrom> <urn:tmfc060:kb:kb:chunk:{other}:0> }}"
    r = await api.post(f"{api.base}/knowledge/kb/query", content=q.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    linked = [b["e"]["value"] for b in r.json()["results"]["bindings"]]
    assert linked == ["urn:x:site-cell"]
