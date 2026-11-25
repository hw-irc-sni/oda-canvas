"""Hybrid dense + sparse search with IRI filters, against Qdrant, with a fake embedding model."""
import json
import math

import httpx
import pytest
from qdrant_client import QdrantClient

from app.kb.vectors import Chunk, Vectors
from app.models import ModelResolver

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, ONT_EDITOR, REVIEWER, APPROVER = "knowledge-editor", "ontology-editor", "ontology-reviewer", "ontology-approver"
AS = "https://example.org/as#"
ONTOLOGY = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix as: <{AS}> . <https://example.org/as> a owl:Ontology .
as:Alarm a owl:Class . as:PowerAlarm a owl:Class ; rdfs:subClassOf as:Alarm .
as:LinkAlarm a owl:Class ; rdfs:subClassOf as:Alarm . as:Billing a owl:Class .""".encode()
CONCEPTS = {"outage": 0, "blackout": 0, "failure": 0, "down": 0, "cut": 0, "invoice": 1, "billing": 1, "payment": 1}


def embed(text: str) -> list[float]:
    """A fake 'semantic' embedding: words of one concept point the same way."""
    v = [0.0, 0.0, 0.05]
    for word in text.lower().replace("-", " ").split():
        if word in CONCEPTS:
            v[CONCEPTS[word]] += 1.0
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def fake_models(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/models"):
        return httpx.Response(200, json={"data": []})
    body = json.loads(request.content)
    return httpx.Response(200, json={"data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(body["input"])]})


CHUNKS = [
    # c1 has one outage word, c4 two: their embeddings differ, so dense ranks never tie
    (Chunk("c1", "Site power blackout took the cell offline", "doc-1", 0, 41, [f"{AS}inc1"], [f"{AS}PowerAlarm"]), None),
    (Chunk("c2", "Monthly invoice for the transport contract", "doc-2", class_iri=[f"{AS}Billing"]), None),
    (Chunk("c3", "Firmware zx-9000 rollout notes", "doc-3"), [0.0, 1.0, 0.0]),  # dense points elsewhere
    (Chunk("c4", "Fibre cut caused a failure in the access ring", "doc-1", class_iri=[f"{AS}LinkAlarm"]), None),
]


@pytest.fixture
async def api(postgres_url, qdrant_url, tmp_path):
    qdrant = QdrantClient(url=qdrant_url)
    for name in ("tmfc060-kb-kb", "tmfc060-kb-small"):
        if qdrant.collection_exists(name):
            qdrant.delete_collection(name)
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url, embedding_dimension=3,
                           governance_require_validation=False, openai_base_url="http://models.test/v1",
                           openai_chat_model="c", openai_embedding_model="e") as client:
        state = client.app.state
        state.models = ModelResolver(state.settings, transport=httpx.MockTransport(fake_models), use_in_cluster=False)
        assert (await state.models.resolve()).name == "openaiCompatible"
        base = f"{client.base}/knowledgeSpecification"
        await client.post(base, json={"id": "as", "name": "as", "iri": "https://example.org/as", "layer": "Telecom",
                                      "specKind": "ontology", "source": {"kind": "upload"}}, headers=auth(ONT_EDITOR))
        await client.put(f"{base}/as/content", content=ONTOLOGY,
                         headers={**auth(ONT_EDITOR), "Content-Type": "text/turtle"})
        for st, role in [("proposed", ONT_EDITOR), ("inReview", REVIEWER), ("agreed", APPROVER),
                         ("published", APPROVER)]:
            await client.patch(f"{base}/as", json={"lifecycleStatus": st}, headers=auth(role))
        r = await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB", "conformsTo": [{"id": "as"}]},
                              headers=auth(EDITOR))
        assert r.status_code == 201, r.text
        vectors = Vectors(state.qdrant, 3)
        await vectors.upsert_chunks("kb", [c for c, _ in CHUNKS], [v or embed(c.text) for c, v in CHUNKS])
        yield client


async def search(api, role="knowledge-viewer", kb="kb", **body):
    return await api.post(f"{api.base}/knowledge/{kb}/search", json=body, headers=auth(role))


def ids(response):
    return [h["chunkId"] for h in response.json()["hits"]]


async def test_keyword_only_and_semantic_only_queries_are_found(api):
    keyword = await search(api, query="zx-9000")
    assert keyword.status_code == 200 and keyword.headers["x-search-mode"] == "hybrid"
    assert ids(keyword)[0] == "c3"  # found by the sparse side only: its dense vector points elsewhere
    semantic = await search(api, query="outage")  # no chunk contains the word
    assert set(ids(semantic)[:2]) == {"c1", "c4"}
    hit = semantic.json()["hits"][ids(semantic).index("c1")]
    assert hit["documentId"] == "doc-1" and hit["entityIri"] == [f"{AS}inc1"]
    assert "Site power blackout" in hit["text"]


async def test_first_in_both_lists_scores_one(api):
    hits = (await search(api, query="site power blackout")).json()["hits"]
    assert hits[0]["chunkId"] == "c1" and hits[0]["score"] == 1.0
    assert all(0 <= h["score"] <= 1 for h in hits)


async def test_iri_filter_narrows_with_subclasses(api):
    narrowed = await search(api, query="outage invoice", iriFilter=[f"{AS}Alarm"])  # via PowerAlarm, LinkAlarm
    assert sorted(ids(narrowed)) == ["c1", "c4"]
    by_entity = await search(api, query="outage", iriFilter=[f"{AS}inc1"])
    assert ids(by_entity) == ["c1"]
    assert ids(await search(api, query="outage", iriFilter=[])) != []  # an empty filter filters nothing


async def test_threshold_and_top_k(api):
    everything = (await search(api, query="outage failure")).json()["hits"]
    strict = (await search(api, query="outage failure", scoreThreshold=0.9)).json()["hits"]
    assert [h["chunkId"] for h in strict] == ["c4"] and strict[0]["score"] == 1.0  # first in both lists
    assert len(everything) > 1 and all(h["score"] < 0.9 for h in everything[1:])
    assert len(ids(await search(api, query="outage", topK=1))) == 1


async def test_knowledge_base_retrieval_defaults(api):
    await api.patch(f"{api.base}/knowledge/kb", json={"retrieval": {"topK": 2, "hybrid": False}}, headers=auth(EDITOR))
    r = await search(api, query="outage")
    assert r.headers["x-search-mode"] == "dense" and len(ids(r)) == 2
    assert ids(r)[0] in ("c1", "c4") and r.json()["hits"][0]["score"] > 0.9  # cosine similarity


async def test_sparse_fallback_when_no_model_route(api):
    state = api.app.state
    state.models = ModelResolver(state.settings.model_copy(update={"openai_base_url": None}), use_in_cluster=False)
    await state.models.resolve()
    r = await search(api, query="zx-9000")
    assert r.status_code == 200 and r.headers["x-search-mode"] == "sparse" and ids(r)[0] == "c3"
    assert r.json()["hits"][0]["score"] == 1.0
    assert (await search(api, query="outage", hybrid=False)).status_code == 503


async def test_validation_and_access(api):
    for body in [{}, {"query": " "}, {"query": "x", "topK": 0}, {"query": "x", "scoreThreshold": 2},
                 {"query": "x", "hybrid": "yes"}, {"query": "x", "iriFilter": ["not an iri"]},
                 {"query": "x", "iriFilter": "urn:a"}]:
        assert (await search(api, **body)).status_code == 400, body
    await api.post(f"{api.base}/knowledge", json={"id": "graph", "name": "G", "storeKind": "graph"},
                   headers=auth(EDITOR))
    assert (await search(api, kb="graph", query="x")).status_code == 409
    assert (await search(api, kb="nope", query="x")).status_code == 404
    unauthenticated = await api.post(f"{api.base}/knowledge/kb/search", json={"query": "x"})
    assert unauthenticated.status_code == 401
