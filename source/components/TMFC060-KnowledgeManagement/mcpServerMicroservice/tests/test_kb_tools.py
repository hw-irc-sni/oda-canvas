"""The knowledge-base MCP tools, round trips against the real API (with Qdrant), and propose_mapping's guarantee."""
import pytest
from qdrant_client import QdrantClient

from .conftest import running, token
from .test_mcp import call, data

pytestmark = pytest.mark.integration

AS = "https://example.org/as#"
EDITOR = ("knowledge-editor", "knowledge-viewer")
ONT = ("ontology-editor", "ontology-reviewer", "ontology-approver")
GRAPH = f"""@prefix as: <{AS}> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<urn:a> as:next <urn:b> ; rdfs:label "A" ; a as:Node . <urn:b> as:next <urn:c> . <urn:c> as:next <urn:d> .
<urn:d> as:next <urn:e> . <urn:x> as:points <urn:a> .
<urn:a> <http://www.w3.org/ns/prov#wasDerivedFrom> <urn:tmfc060:kb:kb:chunk:d:0> .
<urn:inc> as:on <urn:a> ; as:on <urn:b> ."""


@pytest.fixture
async def env(postgres_url, qdrant_url, tmp_path):
    q = QdrantClient(url=qdrant_url)
    if q.collection_exists("tmfc060-kb-kb"):
        q.delete_collection("tmfc060-kb-kb")
    async with running(postgres_url, tmp_path, qdrant_url, embedding_dimension=2) as (rest, base, mcp_url):
        auth = {"Authorization": f"Bearer {token(*EDITOR, *ONT)}"}
        r = await rest.post(f"{base}/knowledge", json={"id": "kb", "name": "KB"}, headers=auth)
        assert r.status_code == 201, r.text
        await rest.put(f"{base}/knowledge/kb/graph", content=GRAPH.encode(), headers={**auth, "Content-Type": "text/turtle"})
        from app.kb.vectors import Chunk, Vectors
        await Vectors(rest.app.state.qdrant, 2).upsert_chunks("kb", [
            Chunk("d:0", "Node A starts the chain of next links", "d", entity_iri=["urn:a"]),
            Chunk("d:1", "The invoice is late", "d")], [[1.0, 0.0], [0.0, 1.0]])
        await rest.post(f"{base}/knowledgeSpecification", json={
            "id": "br", "name": "bridge", "iri": "https://example.org/br/", "specKind": "bridge", "prefix": "br",
            "source": {"kind": "upload"}}, headers=auth)
        yield rest, base, mcp_url, auth


async def test_knowledge_tools_round_trip(env):
    rest, base, mcp_url, auth = env
    listed = data(await call(mcp_url, "list_knowledge", {}))
    assert [k["id"] for k in listed] == ["kb"] and "statistics" in listed[0]
    got = data(await call(mcp_url, "get_knowledge", {"knowledge": "kb"}))
    assert got == (await rest.get(f"{base}/knowledge/kb", headers=auth)).json()
    hits = data(await call(mcp_url, "hybrid_search", {"knowledge": "kb", "query": "chain of next links", "topK": 1}))
    via_rest = (await rest.post(f"{base}/knowledge/kb/search", json={"query": "chain of next links", "topK": 1},
                                headers=auth)).json()["hits"]
    assert hits == via_rest and hits[0]["entityIri"] == ["urn:a"]
    rec = data(await call(mcp_url, "recommend", {"knowledge": "kb", "iri": ["urn:inc"], "topK": 2}))
    assert [i["itemId"] for i in rec["items"]] == ["urn:a", "urn:b"]
    assert data(await call(mcp_url, "recommend_feedback", {"knowledge": "kb", "recommendationId": rec["recommendationId"],
                                                           "itemId": "urn:b", "outcome": "success"})) == {"recorded": True}
    again = data(await call(mcp_url, "recommend", {"knowledge": "kb", "iri": ["urn:inc"], "topK": 2}))
    assert [i["itemId"] for i in again["items"]] == ["urn:b", "urn:a"]  # the feedback changed the ranking


async def test_graph_tools(env):
    _, _, mcp_url, _ = env
    entity = data(await call(mcp_url, "get_entity", {"knowledge": "kb", "entity": "urn:a"}))
    triples = {(t["subject"], t["predicate"].rsplit("#", 1)[-1], t["object"]) for t in entity}
    assert ("urn:a", "next", "urn:b") in triples and ("urn:x", "points", "urn:a") in triples
    two = data(await call(mcp_url, "get_subgraph", {"knowledge": "kb", "root": "urn:a", "hops": 2}))
    subjects = {t["subject"] for t in two["triples"]}
    assert subjects == {"urn:a", "urn:b"} and not two["truncated"]
    assert not any("chunk" in t["subject"] for t in two["triples"])  # provenance nodes are not followed
    four = data(await call(mcp_url, "get_subgraph", {"knowledge": "kb", "root": "urn:a", "hops": 4}))
    assert {t["subject"] for t in four["triples"]} == {"urn:a", "urn:b", "urn:c", "urn:d"}
    assert (await call(mcp_url, "get_subgraph", {"knowledge": "kb", "root": "urn:a", "hops": 9})).isError
    path = data(await call(mcp_url, "get_path", {"knowledge": "kb", "start": "urn:a", "end": "urn:e"}))
    assert [t["object"] for t in path] == ["urn:b", "urn:c", "urn:d", "urn:e"]
    assert data(await call(mcp_url, "get_path", {"knowledge": "kb", "start": "urn:e", "end": "urn:a"})) == []
    assert data(await call(mcp_url, "get_path", {"knowledge": "kb", "start": "urn:a", "end": "urn:e",
                                                 "maxHops": 3})) == []
    injected = await call(mcp_url, "get_entity", {"knowledge": "kb", "entity": "urn:a> } ; DROP ALL ; {"})
    assert injected.isError  # IRIs are checked before they go into SPARQL


async def test_propose_mapping_never_agrees(env):
    rest, base, mcp_url, auth = env
    result = data(await call(mcp_url, "propose_mapping", {
        "module": "br", "subject": "urn:x:Cell", "predicate": "http://www.w3.org/2004/02/skos/core#exactMatch",
        "object": "urn:y:Cell", "confidence": "high", "rationale": "same thing"}, roles=ONT))
    mapping = result["mapping"]
    assert mapping["lifecycleStatus"] == "proposed" and mapping["status"] == "proposed"
    assert result["review"]["kind"] == "proposal" and result["review"]["target"] == {"kind": "mapping",
                                                                                     "id": mapping["id"]}
    listed = (await rest.get(f"{base}/knowledgeSpecification/br/mapping", headers=auth)).json()
    assert all(m["lifecycleStatus"] not in ("agreed", "published") for m in listed)
    denied = await call(mcp_url, "propose_mapping", {"module": "br", "subject": "urn:x:A", "predicate": "urn:p",
                                                     "object": "urn:y:A"}, roles=EDITOR)
    assert denied.isError and "403" in denied.content[0].text
    assert len((await rest.get(f"{base}/knowledgeSpecification/br/mapping", headers=auth)).json()) == 1
