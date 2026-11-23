"""Knowledge recommendation: candidates, deterministic ranking, feedback signals, topK, checks."""
import pytest
from qdrant_client import QdrantClient
from sqlalchemy import func, select

from app.db.models import RecommendFeedback
from app.kb.vectors import Chunk, Vectors

from .api import auth, running_api

pytestmark = pytest.mark.integration

EDITOR, VIEWER = "knowledge-editor", "knowledge-viewer"
AS = "https://example.org/as#"
GRAPH = f"""@prefix as: <{AS}> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:inc:1> a as:Incident ; as:on <urn:cell:4711> .
<urn:fix:a> a as:Runbook ; rdfs:label "Reset the rectifier" ; as:resolves <urn:inc:1> .
<urn:fix:b> a as:Runbook ; rdfs:label "Swap the battery" ; as:resolves <urn:inc:1> .
<urn:cell:4711> a as:Cell ."""


@pytest.fixture
async def api(postgres_url, qdrant_url, tmp_path):
    qdrant = QdrantClient(url=qdrant_url)
    if qdrant.collection_exists("tmfc060-kb-kb"):
        qdrant.delete_collection("tmfc060-kb-kb")
    async with running_api(postgres_url, str(tmp_path / "store"), qdrant_url=qdrant_url,
                           embedding_dimension=3) as client:
        await client.post(f"{client.base}/knowledge", json={"id": "kb", "name": "KB"}, headers=auth(EDITOR))
        await client.put(f"{client.base}/knowledge/kb/graph", content=GRAPH.encode(),
                         headers={**auth(EDITOR), "Content-Type": "text/turtle"})
        vectors = Vectors(client.app.state.qdrant, 3)
        await vectors.upsert_chunks("kb", [
            Chunk("d:0", "Reset the rectifier after a mains power alarm", "d", entity_iri=["urn:fix:a"]),
            Chunk("d:1", "Monthly invoice", "d")], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        yield client


async def recommend(api, role=VIEWER, **body):
    r = await api.post(f"{api.base}/knowledge/kb/recommend", json=body, headers=auth(role))
    assert r.status_code == 200, r.text
    return r.json()


async def feedback(api, rec, item, role=VIEWER, **extra):
    return await api.post(f"{api.base}/knowledge/kb/recommend/feedback",
                          json={"recommendationId": rec, "itemId": item, **extra}, headers=auth(role))


def order(result):
    return [i["itemId"] for i in result["items"]]


async def test_iri_context_ranking_is_deterministic_and_feedback_changes_it(api):
    first = await recommend(api, iri=["urn:inc:1"])
    # the incident's neighbours, all relevance 1; ties broken by item id
    assert order(first) == ["urn:cell:4711", "urn:fix:a", "urn:fix:b"]
    assert first["items"][1]["label"] == "Reset the rectifier" and first["items"][1]["kind"] == "entity"
    assert order(await recommend(api, iri=["urn:inc:1"])) == order(first)  # deterministic
    # success on fix:b moves it up
    assert (await feedback(api, first["recommendationId"], "urn:fix:b", outcome="success")).status_code == 204
    second = await recommend(api, iri=["urn:inc:1"])
    assert order(second) == ["urn:fix:b", "urn:cell:4711", "urn:fix:a"]
    b = second["items"][0]["signals"]
    assert b == {"successRate": round(2 / 3, 6), "applicability": b["applicability"], "usage": 1}
    # failure on the cell moves it down
    await feedback(api, second["recommendationId"], "urn:cell:4711", outcome="failure")
    assert order(await recommend(api, iri=["urn:inc:1"])) == ["urn:fix:b", "urn:fix:a", "urn:cell:4711"]


async def test_top_k_and_mixed_candidates(api):
    result = await recommend(api, context="mains power alarm", topK=2)
    assert len(result["items"]) == 2
    kinds = {i["kind"] for i in (await recommend(api, context="mains power alarm", topK=10))["items"]}
    assert kinds == {"chunk", "entity"}  # the chunk and the entity it mentions
    assert (await recommend(api, iri=["urn:inc:1"], topK=1))["items"][0]["itemId"] == "urn:cell:4711"


async def test_applicability_from_context_iris(api):
    result = await recommend(api, context="mains power alarm", iri=["urn:inc:1"], topK=10)
    by_id = {i["itemId"]: i for i in result["items"]}
    # fix:a is connected to the context incident; the invoice chunk is not
    assert by_id["urn:fix:a"]["signals"]["applicability"] > 0
    assert by_id.get("chunk:d:1", {"signals": {"applicability": 0}})["signals"]["applicability"] == 0


async def test_feedback_checks_and_repeats(api):
    rec = (await recommend(api, iri=["urn:inc:1"]))["recommendationId"]
    assert (await feedback(api, "rec-nope", "urn:fix:a")).status_code == 404
    assert (await feedback(api, rec, "urn:not:recommended")).status_code == 400
    assert (await feedback(api, rec, "urn:fix:a", outcome="great")).status_code == 400
    assert (await feedback(api, rec, "urn:fix:a", outcome="success")).status_code == 204
    assert (await feedback(api, rec, "urn:fix:a", outcome="failure")).status_code == 204  # replaces
    async with api.app.state.sessions() as s:
        rows = (await s.scalars(select(RecommendFeedback))).all()
    assert [(r.item_id, r.outcome, r.used) for r in rows] == [("urn:fix:a", "failure", True)]
    for body in [{}, {"iri": ["not an iri"]}, {"context": "x", "topK": 0}]:
        r = await api.post(f"{api.base}/knowledge/kb/recommend", json=body, headers=auth())
        assert r.status_code == 400, body
    assert (await api.post(f"{api.base}/knowledge/kb/recommend", json={"iri": ["urn:inc:1"]})).status_code == 401


async def test_graph_knowledge_base_uses_iris_only(postgres_url, tmp_path):
    async with running_api(postgres_url, str(tmp_path / "store")) as api:
        await api.post(f"{api.base}/knowledge", json={"id": "kb", "name": "G", "storeKind": "graph"},
                       headers=auth(EDITOR))
        await api.put(f"{api.base}/knowledge/kb/graph", content=GRAPH.encode(),
                      headers={**auth(EDITOR), "Content-Type": "text/turtle"})
        result = await recommend(api, context="anything", iri=["urn:inc:1"])
        assert {i["kind"] for i in result["items"]} == {"entity"} and len(result["items"]) == 3
        assert (await recommend(api, context="anything only"))["items"] == []
