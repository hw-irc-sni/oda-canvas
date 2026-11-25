"""The RDF store: named-graph operations, the knowledge-base query dataset and the read-only query runner."""
import json
import multiprocessing

import pytest
from pyoxigraph import Store

from app.dataset import ModuleInfo, query_dataset, visible_modules
from app.errors import ApiError
from app.graphstore import GraphStore, kb_graph, module_graph
from app.query import QueryTimeout, effective_timeout, precheck, run_query

TTL = b"""@prefix ex: <https://example.org/> .
ex:INC-1 a ex:Incident ; ex:affects ex:cell-1 .
ex:INC-2 a ex:Incident ; ex:affects ex:cell-2 .
"""
JSONLD = json.dumps({"@id": "https://example.org/INC-3", "@type": "https://example.org/Incident"}).encode()
ONTOLOGY = b"""@prefix ex: <https://example.org/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Incident rdfs:subClassOf ex:Event ."""
FOUNDATION = b"""@prefix ex: <https://example.org/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Event rdfs:label "Event" ."""
DRAFT = b"""@prefix ex: <https://example.org/> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:Incident rdfs:label "draft label" ."""


@pytest.fixture
def store_path(tmp_path):
    return str(tmp_path / "store")


@pytest.fixture
def graphs(store_path):
    return GraphStore(Store(store_path))


def test_merge_replace_clear_export_and_count(graphs):
    g = kb_graph("kb1")
    graphs.merge(g, TTL, "text/turtle")
    assert graphs.count(g) == 4
    graphs.merge(g, JSONLD, "application/ld+json")
    assert graphs.count(g) == 5
    graphs.replace(g, JSONLD, "application/ld+json")
    assert graphs.count(g) == 1
    assert b"INC-3" in graphs.export(g, "text/turtle")
    assert json.loads(graphs.export(g, "application/ld+json"))
    graphs.clear(g)
    assert graphs.count(g) == 0


def test_invalid_rdf_leaves_the_graph_unchanged(graphs):
    g = kb_graph("kb1")
    graphs.merge(g, TTL, "text/turtle")
    with pytest.raises(ApiError) as err:
        graphs.replace(g, b"this is not turtle", "text/turtle")
    assert err.value.status_code == 400
    assert graphs.count(g) == 4


def test_unsupported_media_type(graphs):
    with pytest.raises(ApiError) as err:
        graphs.merge(kb_graph("kb1"), b"<x/>", "application/rdf+xml")
    assert err.value.status_code == 415


MODULES = {
    "assurance": ModuleInfo("published", ("core",)),
    "core": ModuleInfo("published", ("assurance",)),  # import cycle
    "draft-mod": ModuleInfo("draft"),
    "old": ModuleInfo("deprecated"),
    "uses-draft": ModuleInfo("published", ("draft-mod",)),
}


def test_visible_modules_follow_imports_and_skip_unpublished():
    assert visible_modules(["assurance"], MODULES) == ["assurance", "core"]
    assert visible_modules(["draft-mod", "old", "missing"], MODULES) == ["old"]
    assert visible_modules(["uses-draft"], MODULES) == ["uses-draft"]
    assert query_dataset("kb1", ["assurance"], MODULES) == [kb_graph("kb1"), module_graph("assurance"),
                                                             module_graph("core")]


async def test_query_unions_the_kb_with_published_modules_only(graphs, store_path):
    graphs.merge(kb_graph("kb1"), TTL, "text/turtle")
    graphs.merge(module_graph("assurance"), ONTOLOGY, "text/turtle")
    graphs.merge(module_graph("core"), FOUNDATION, "text/turtle")
    graphs.merge(module_graph("draft-mod"), DRAFT, "text/turtle")
    dataset = query_dataset("kb1", ["assurance", "draft-mod"], MODULES)
    q = """PREFIX ex: <https://example.org/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
           SELECT ?i ?label WHERE { ?i a ?c . ?c rdfs:subClassOf ?super . ?super rdfs:label ?label }"""
    result = await run_query(store_path, q, dataset, timeout=30, row_cap=100)
    assert result.kind == "select" and not result.truncated
    rows = result.body["results"]["bindings"]
    assert {r["i"]["value"] for r in rows} == {"https://example.org/INC-1", "https://example.org/INC-2"}
    assert {r["label"]["value"] for r in rows} == {"Event"}
    labels = await run_query(store_path, "SELECT ?l WHERE { ?s <http://www.w3.org/2000/01/rdf-schema#label> ?l }",
                             dataset, timeout=30, row_cap=100)
    assert "draft label" not in {r["l"]["value"] for r in labels.body["results"]["bindings"]}


async def test_row_cap_truncates(graphs, store_path):
    graphs.merge(kb_graph("kb1"), TTL, "text/turtle")
    result = await run_query(store_path, "SELECT * WHERE { ?s ?p ?o }", [kb_graph("kb1")], timeout=30, row_cap=3)
    assert len(result.body["results"]["bindings"]) == 3 and result.truncated


async def test_ask_and_construct(graphs, store_path):
    graphs.merge(kb_graph("kb1"), TTL, "text/turtle")
    ask = await run_query(store_path, "ASK { ?s ?p ?o }", [kb_graph("kb1")], timeout=30, row_cap=10)
    assert ask.kind == "ask" and ask.body["boolean"] is True
    graph = await run_query(store_path, "CONSTRUCT WHERE { ?s ?p ?o }", [kb_graph("kb1")], timeout=30, row_cap=10)
    assert graph.kind == "graph" and b"INC-1" in graph.body


async def test_a_write_is_visible_to_the_next_query(graphs, store_path):
    q = "ASK { <https://example.org/INC-1> ?p ?o }"
    assert (await run_query(store_path, q, [kb_graph("kb1")], timeout=30, row_cap=1)).body["boolean"] is False
    graphs.merge(kb_graph("kb1"), TTL, "text/turtle")  # no flush
    assert (await run_query(store_path, q, [kb_graph("kb1")], timeout=30, row_cap=1)).body["boolean"] is True


@pytest.mark.parametrize("query, status, code", [
    ("INSERT DATA { <urn:a> <urn:b> <urn:c> }", 400, "400"),
    ("SELECT nonsense", 400, "400"),
    ("SELECT * WHERE { SERVICE <http://other.example/sparql> { ?s ?p ?o } }", 403, "SERVICE_NOT_ALLOWED"),
    ("SELECT * WHERE { SERVICE ?e { ?s ?p ?o } }", 403, "SERVICE_NOT_ALLOWED"),
    ("SELECT * WHERE { ?a ?b ?c OPTIONAL { SERVICE <http://evil.example/sparql> { ?s ?p ?o } } }", 403,
     "SERVICE_NOT_ALLOWED"),
    ("SELECT * WHERE { SERVICE <http://peer.example/sparql> { SERVICE <http://evil.example/sparql> "
     "{ ?s ?p ?o } } }", 403, "SERVICE_NOT_ALLOWED"),
])
def test_precheck_rejects(query, status, code):
    with pytest.raises(ApiError) as err:
        precheck(query, frozenset({"http://peer.example/sparql"}))
    assert (err.value.status_code, err.value.code) == (status, code)


def test_precheck_allows_an_allowlisted_service():
    precheck("SELECT * WHERE { SERVICE <http://peer.example/sparql> { ?s ?p ?o } }",
             frozenset({"http://peer.example/sparql"}))


async def test_a_long_query_is_killed_at_the_timeout(graphs, store_path):
    # ~1.4M-row cross product over 1200 triples: far longer than 1 s
    data = "\n".join(f"<urn:s{i}> <urn:p> {i} ." for i in range(1200)).encode()
    graphs.merge(kb_graph("kb1"), data, "text/turtle")
    q = "SELECT (COUNT(*) AS ?n) WHERE { ?a ?p ?x . ?b ?p ?y . ?c ?p ?z }"
    with pytest.raises(QueryTimeout) as err:
        await run_query(store_path, q, [kb_graph("kb1")], timeout=1, row_cap=10)
    assert err.value.status_code == 504
    assert not [p for p in multiprocessing.active_children() if p.is_alive()]


def test_effective_timeout():
    assert effective_timeout(None, 30, 120) == 30
    assert effective_timeout(500, 30, 120) == 120
    assert effective_timeout(5, 30, 120) == 5
