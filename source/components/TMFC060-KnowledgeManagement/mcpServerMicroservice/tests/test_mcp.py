"""The MCP server with an MCP client: tools listed, each tool's data equals its REST endpoint's, tokens enforced."""
import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .conftest import running, token

pytestmark = pytest.mark.integration

AS = "https://example.org/as#"
VIEWER = ("knowledge-viewer",)
ONTOLOGY = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix fto: <https://models.tmforum.org/ontology/fto/> . @prefix as: <{AS}> .
<https://example.org/as> a owl:Ontology ; fto:layer fto:Telecom .
as:Site a owl:Class ; rdfs:label "Site" ; fto:layer fto:Foundational .
as:Cell a owl:Class ; rdfs:subClassOf as:Site ; rdfs:label "Cell" ; rdfs:comment "A radio cell." ; fto:layer fto:Telecom .
as:Alarm a owl:Class ; rdfs:label "Alarm" . as:affects a owl:ObjectProperty ; rdfs:label "affects" .
as:severity a owl:DatatypeProperty ; rdfs:label "severity" ."""
SHAPES = f"""@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix as: <{AS}> .
as:AlarmShape a sh:NodeShape ; sh:targetClass as:Alarm ; sh:property [ sh:path as:severity ; sh:minCount 1 ] ."""
DATA = f"<urn:a1> a <{AS}Alarm> ; <{AS}affects> <urn:c1> . <urn:c1> a <{AS}Cell> ."


@pytest.fixture
async def env(postgres_url, tmp_path):
    async with running(postgres_url, tmp_path) as (rest, base, mcp_url):
        ed = {"Authorization": f"Bearer {token('ontology-editor', 'ontology-reviewer', 'ontology-approver', 'knowledge-editor')}"}
        for mid, kind, ttl in [("as", "ontology", ONTOLOGY), ("as-shapes", "shapes", SHAPES)]:
            await rest.post(f"{base}/knowledgeSpecification", json={
                "id": mid, "name": mid, "iri": f"https://example.org/{mid}", "prefix": mid.replace("-", ""),
                "specKind": kind, "layer": "Telecom", "source": {"kind": "upload"}}, headers=ed)
            await rest.put(f"{base}/knowledgeSpecification/{mid}/content", content=ttl.encode(),
                           headers={**ed, "Content-Type": "text/turtle"})
            for s in ("proposed", "inReview", "agreed", "published"):
                r = await rest.patch(f"{base}/knowledgeSpecification/{mid}", json={"lifecycleStatus": s}, headers=ed)
                assert r.status_code == 200, r.text
        await rest.post(f"{base}/knowledge", json={"id": "kb", "name": "KB", "storeKind": "graph",
                                                   "conformsTo": [{"id": "as"}]}, headers=ed)
        await rest.put(f"{base}/knowledge/kb/graph", content=DATA.encode(),
                       headers={**ed, "Content-Type": "text/turtle"})
        yield rest, base, mcp_url


async def call(mcp_url, tool, args, roles=VIEWER):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token(*roles)}"}) as http, \
            streamable_http_client(mcp_url, http_client=http) as (r, w, _), ClientSession(r, w) as session:
        await session.initialize()
        if tool is None:
            return await session.list_tools()
        return await session.call_tool(tool, args)


def data(result):
    assert not result.isError, result.content
    structured = result.structuredContent
    return structured["result"] if structured and set(structured) == {"result"} else structured


async def rest_get(rest, base, path, **params):
    r = await rest.get(f"{base}{path}", params=params, headers={"Authorization": f"Bearer {token(*VIEWER)}"})
    return r.json()


async def test_tools_listed(env):
    _, _, mcp_url = env
    tools = await call(mcp_url, None, None)
    assert sorted(t.name for t in tools.tools) == [  # the FTO set (#47) and the knowledge-base tools (#48)
        "describe", "get_entity", "get_knowledge", "get_path", "get_subgraph", "hybrid_search", "layer_of",
        "list_knowledge", "modules", "propose_mapping", "recommend", "recommend_feedback", "resolve_intent", "search",
        "sparql", "validate"]


async def test_each_tool_returns_the_rest_data(env):
    rest, base, mcp_url = env
    assert data(await call(mcp_url, "modules", {})) == await rest_get(
        rest, base, "/knowledgeSpecification", fields="id,name,layer,iri,specKind,lifecycleStatus,prefix,linkStatus")
    assert data(await call(mcp_url, "describe", {"term": "as:Cell"})) == (
        await rest_get(rest, base, "/knowledgeTerm/describe", iri="as:Cell"))["description"]
    assert data(await call(mcp_url, "search", {"text": "cell", "limit": 5})) == (
        await rest_get(rest, base, "/knowledgeTerm", q="cell", limit=5))["hits"]
    chain = data(await call(mcp_url, "layer_of", {"term": "as:Cell"}))
    rest_chain = await rest_get(rest, base, "/knowledgeTerm/describe", iri="as:Cell", layerChain="true")
    assert chain["chain"] == rest_chain["layerChain"]["chain"] and chain["layer"] == "Telecom"
    assert chain["endsAtFoundational"] == rest_chain["layerChain"]["endsAtFoundational"]
    query = f"SELECT ?c WHERE {{ <urn:a1> <{AS}affects> ?c }}"
    via_rest = (await rest.post(f"{base}/knowledge/kb/query", content=query.encode(), headers={
        "Authorization": f"Bearer {token(*VIEWER)}", "Content-Type": "application/sparql-query"})).json()
    assert data(await call(mcp_url, "sparql", {"query": query, "knowledge": "kb"})) == via_rest
    document = f"<urn:a2> a <{AS}Alarm> ."
    via_rest = (await rest.post(f"{base}/knowledgeSpecification/as-shapes/validation/document", content=document.encode(),
                                headers={"Authorization": f"Bearer {token(*VIEWER)}", "Content-Type": "text/turtle"})).json()
    validated = data(await call(mcp_url, "validate", {"document": document, "shapes": "as-shapes"}))
    def stable(results):  # an anonymous property shape's id is a blank node, new on every parse
        return [{k: v for k, v in r.items() if not (k == "sourceShape" and "://" not in v)} for r in results]
    assert validated["conforms"] is False and stable(validated["results"]) == stable(via_rest["results"])


async def test_resolve_intent(env):
    _, _, mcp_url = env
    intent = {"id": "I1", "@type": "Intent", "expression": {"@type": "TurtleExpression", "expressionValue":
              f"<urn:i1> a <{AS}Alarm> ; <{AS}affects> <urn:c1> ; <{AS}unknownProperty> \"x\" ."}}
    result = data(await call(mcp_url, "resolve_intent", {"intent": json.dumps(intent)}))
    assert {r["term"] for r in result["resolved"]} == {AS + "Alarm", AS + "affects"}
    alarm = next(r for r in result["resolved"] if r["term"] == AS + "Alarm")
    assert alarm["definedIn"] == ["as"] and alarm["label"] == "Alarm"
    assert result["unresolved"] == [AS + "unknownProperty"] and len(result["questions"]) == 1
    bad = await call(mcp_url, "resolve_intent", {"intent": "not rdf <"})
    assert bad.isError


async def test_missing_or_insufficient_token(env):
    rest, base, mcp_url = env
    async with httpx.AsyncClient() as client:
        r = await client.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                              headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    # a token the API refuses: the tool reports the REST error
    refused = await call(mcp_url, "modules", {}, roles=())
    assert refused.isError and "403" in refused.content[0].text
    async with httpx.AsyncClient() as client:
        assert (await client.get(mcp_url.replace("/mcp", "/health"))).json() == {"status": "up"}
