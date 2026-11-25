"""KM-FX-05 Knowledge Storage, integration: the full chain into the real API with Qdrant, then /query and /search."""
import json
import math

import httpx
import pytest
from qdrant_client import QdrantClient

from worker.steps import default_handlers

from .conftest import running_api
from .test_worker import make_worker, wait_done

pytestmark = pytest.mark.integration

AS = "https://example.org/as#"
ONTOLOGY = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix as: <{AS}> . <https://example.org/as> a owl:Ontology .
as:Site a owl:Class . as:Cell a owl:Class ; rdfs:subClassOf as:Site . as:Alarm a owl:Class .
as:affects a owl:ObjectProperty ."""
CONCEPTS = {"outage": 0, "down": 0, "blackout": 0, "invoice": 1, "billing": 1}


def embedding(text):
    v = [0.0, 0.0, 0.05, 0.05]
    for word in text.lower().replace(".", " ").split():
        if word in CONCEPTS:
            v[CONCEPTS[word]] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def fake_model(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    body = json.loads(request.content)
    if request.url.path.endswith("/embeddings"):
        return httpx.Response(200, json={"data": [{"index": i, "embedding": embedding(t)}
                                                  for i, t in enumerate(body["input"])]})
    text = body["messages"][-1]["content"]
    entities, relations = [], []
    if "4711" in text:
        entities.append({"id": "c", "type": AS + "Cell", "label": "Cell 4711"})
    if "alarm" in text.lower():
        entities.append({"id": "a", "type": AS + "Alarm", "label": "Mains alarm"})
        if "4711" in text:
            relations.append({"subject": "a", "predicate": AS + "affects", "object": "c"})
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
        {"entities": entities, "relations": relations, "attributes": []})}}]})


@pytest.fixture
async def store_api(postgres_url, qdrant_url, tmp_path):
    from app.models import ModelResolver
    qdrant = QdrantClient(url=qdrant_url)
    for c in qdrant.get_collections().collections:
        qdrant.delete_collection(c.name)
    async with running_api(postgres_url, tmp_path, qdrant_url, embedding_dimension=4) as api:
        state = api.app.state
        state.settings.governance_require_validation = False
        state.models = ModelResolver(state.settings.model_copy(update={
            "openai_base_url": "http://m.test/v1", "openai_chat_model": "c", "openai_embedding_model": "e"}),
            transport=httpx.MockTransport(fake_model), use_in_cluster=False)
        await state.models.resolve()
        base = f"{api.base}/knowledgeSpecification"
        await api.post(base, json={"id": "as", "name": "as", "iri": "https://example.org/as", "layer": "Telecom",
                                   "specKind": "ontology", "source": {"kind": "upload"}})
        await api.put(f"{base}/as/content", content=ONTOLOGY.encode(), headers={"Content-Type": "text/turtle"})
        for s in ("proposed", "inReview", "agreed", "published"):
            await api.patch(f"{base}/as", json={"lifecycleStatus": s})
        api.qdrant = qdrant
        yield api


async def kb(api, kb_id, kind):
    r = await api.post(f"{api.base}/knowledge", json={"id": kb_id, "name": kb_id, "storeKind": kind,
                                                      "conformsTo": [{"id": "as"}]})
    assert r.status_code == 201, r.text


async def ingest(api, kb_id, text, doc=None, **worker_settings):
    if doc is None:
        doc = (await api.post(f"{api.base}/knowledge/{kb_id}/document",
                              files={"file": ("d.md", text.encode(), "text/markdown")})).json()["id"]
    pid = (await api.post(f"{api.base}/knowledge/{kb_id}/ingest", json={"documentId": [doc]})).json()["id"]
    worker = make_worker(api, "w1", default_handlers(), **worker_settings)
    worker.handlers = default_handlers(worker.settings)
    worker.start()
    try:
        p = await wait_done(api, pid, timeout=60)
    finally:
        await worker.stop()
    assert p["state"] == "completed", p
    return doc


async def query(api, kb_id, sparql):
    r = await api.post(f"{api.base}/knowledge/{kb_id}/query", content=sparql.encode(),
                       headers={"Content-Type": "application/sparql-query"})
    return r.json()


async def search(api, kb_id, **body):
    r = await api.post(f"{api.base}/knowledge/{kb_id}/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()["hits"]


TEXT = "# Outage\n\nThe mains alarm took cell 4711 down.\n\n## Billing\n\nThe invoice for March is late."
CELL = "urn:tmfc060:kb:hyb:entity:Cell:cell-4711"
ALARM = "urn:tmfc060:kb:hyb:entity:Alarm:mains-alarm"


async def test_triples_queryable_and_chunks_searchable_with_entity_iris(store_api):
    api = store_api
    await kb(api, "hyb", "hybrid")
    doc = await ingest(api, "hyb", TEXT)
    rows = (await query(api, "hyb", f"SELECT ?c WHERE {{ <{ALARM}> <{AS}affects> ?c }}"))["results"]["bindings"]
    assert [r["c"]["value"] for r in rows] == [CELL]
    assert (await query(api, "hyb", f"ASK {{ <{CELL}> a <{AS}Site> }}"))["boolean"]  # with KM-FX-04's inference
    hits = await search(api, "hyb", query="outage")
    assert hits[0]["documentId"] == doc and set(hits[0]["entityIri"]) == {CELL, ALARM}
    assert "mains alarm" in hits[0]["text"]
    filtered = await search(api, "hyb", query="outage invoice", iriFilter=[AS + "Site"])  # Cell is a Site
    assert [h["entityIri"] for h in filtered] == [hits[0]["entityIri"]]
    assert len(await search(api, "hyb", query="invoice")) == 2  # both chunks, billing first
    before = api.qdrant.count("tmfc060-kb-hyb").count
    assert before == 2
    # re-ingesting with other chunk sizes replaces the document's chunks: nothing stale
    await ingest(api, "hyb", TEXT, doc=doc, chunk_max_tokens=8, chunk_overlap_tokens=0)
    points = api.qdrant.scroll("tmfc060-kb-hyb", limit=100, with_payload=True)[0]
    assert len(points) > before and all(p.payload["source"] == doc for p in points)
    texts = [p.payload["text"] for p in points]
    assert "The mains alarm took cell 4711 down." not in texts  # the old whole-paragraph chunk is gone
    stats = (await api.get(f"{api.base}/knowledge/hyb")).json()["statistics"]
    assert stats["chunks"] == len(points) and stats["triples"] > 0
    # KM-FX-06 stored the entities' applicability profiles, which recommend uses
    from sqlalchemy import select
    from app.db.models import RecommendProfile
    async with api.app.state.sessions() as s:
        profiles = {p.entity_iri: p.iris for p in (await s.scalars(select(RecommendProfile))).all()}
    assert ALARM in profiles[CELL] and AS + "Site" in profiles[CELL] and CELL in profiles[ALARM]
    r = await api.post(f"{api.base}/knowledge/hyb/recommend", json={"context": "outage", "iri": [CELL], "topK": 3})
    assert r.status_code == 200 and r.json()["items"]
    assert any(i["itemId"] == ALARM and i["signals"]["applicability"] > 0 for i in r.json()["items"])


async def test_graph_and_vector_knowledge_bases(store_api):
    api = store_api
    await kb(api, "gra", "graph")
    await ingest(api, "gra", TEXT)
    assert (await query(api, "gra", f"ASK {{ ?a <{AS}affects> ?c }}"))["boolean"]
    assert not api.qdrant.collection_exists("tmfc060-kb-gra")
    await kb(api, "vec", "vector")
    await ingest(api, "vec", TEXT)
    # no triples in a vector KB (its query dataset still has the ontology, so ask for instance data)
    assert not (await query(api, "vec", f"ASK {{ ?a <{AS}affects> ?c }}"))["boolean"]
    assert (await api.get(f"{api.base}/knowledge/vec")).json()["statistics"]["triples"] == 0
    hits = await search(api, "vec", query="outage")
    assert hits and set(hits[0]["entityIri"]) == {"urn:tmfc060:kb:vec:entity:Cell:cell-4711",
                                                   "urn:tmfc060:kb:vec:entity:Alarm:mains-alarm"}
