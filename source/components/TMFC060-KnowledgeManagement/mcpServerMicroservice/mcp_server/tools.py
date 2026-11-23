"""The FTO tool set (semantics of tfo's mcp/server.py), each wrapping a knowledge API operation:

modules -> GET /knowledgeSpecification; sparql -> POST /knowledge/{id}/query; describe, layer_of ->
GET /knowledgeTerm/describe; search -> GET /knowledgeTerm; resolve_intent -> the terms an intent uses, each looked
up with /knowledgeTerm/describe; validate ->
POST /knowledgeSpecification/{id}/validation/document. The caller's bearer token is forwarded on every call.
"""
import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from rdflib import Graph, URIRef

from .api import KnowledgeApi, ToolError

MODULE_FIELDS = "id,name,layer,iri,specKind,lifecycleStatus,prefix,linkStatus"
STRUCTURAL = ("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "http://www.w3.org/2000/01/rdf-schema#",
              "http://www.w3.org/2002/07/owl#", "http://www.w3.org/2001/XMLSchema#")


def token_of(ctx: Context) -> str:
    request = ctx.request_context.request
    header = request.headers.get("authorization", "") if request is not None else ""
    if not header.lower().startswith("bearer "):
        raise ToolError("A bearer token is required")
    return header


def intent_graph(intent: str) -> Graph:
    """An intent as Turtle, JSON-LD, or a TMF921 intent JSON with expression.expressionValue."""
    text = intent.strip()
    if text.startswith(("{", "[")):
        data = json.loads(text)
        expression = data.get("expression") if isinstance(data, dict) else None
        if isinstance(expression, dict) and expression.get("expressionValue") is not None:
            value = expression["expressionValue"]
            if isinstance(value, (dict, list)):
                return Graph().parse(data=json.dumps(value), format="json-ld")
            return Graph().parse(data=str(value), format="json-ld" if "json" in str(expression.get("@type", "")).lower()
                                 else "turtle")
        return Graph().parse(data=text, format="json-ld")
    return Graph().parse(data=text, format="turtle")


def intent_terms(graph: Graph) -> list[str]:
    """The vocabulary an intent uses: its predicates and its types (not its own individuals)."""
    terms = {str(p) for p in graph.predicates()} | {str(o) for o in graph.objects(None, URIRef(
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type")) if isinstance(o, URIRef)}
    return sorted(t for t in terms if not t.startswith(STRUCTURAL))


def register(mcp: FastMCP, api: KnowledgeApi) -> None:
    @mcp.tool()
    async def modules(ctx: Context) -> list[dict[str, Any]]:
        """List the ontology modules of the federation with their FTO layer, IRI and lifecycle state."""
        return await api.call(token_of(ctx), "GET", "/knowledgeSpecification", params={"fields": MODULE_FIELDS})

    @mcp.tool()
    async def sparql(query: str, knowledge: str, ctx: Context) -> dict[str, Any]:
        """Run a read-only SPARQL SELECT or ASK over a knowledge-base: its data plus the ontology modules it conforms
        to. `knowledge` is the knowledge-base id."""
        return await api.call(token_of(ctx), "POST", f"/knowledge/{knowledge}/query", content=query.encode(),
                              headers={"Content-Type": "application/sparql-query",
                                       "Accept": "application/sparql-results+json"})

    @mcp.tool()
    async def describe(term: str, ctx: Context) -> dict[str, Any]:
        """Describe an ontology term (an IRI or a CURIE): labels, definitions, FTO layer, defining modules, parents
        and SKOS matches."""
        return (await api.call(token_of(ctx), "GET", "/knowledgeTerm/describe", params={"iri": term}))["description"]

    @mcp.tool()
    async def search(text: str, ctx: Context, limit: int = 25) -> list[dict[str, Any]]:
        """Find ontology terms whose label, local name or definition matches the text, with module and layer."""
        return (await api.call(token_of(ctx), "GET", "/knowledgeTerm", params={"q": text, "limit": limit}))["hits"]

    @mcp.tool()
    async def layer_of(term: str, ctx: Context) -> dict[str, Any]:
        """Which FTO layer owns a term, and its chain of parents down to the Foundational layer."""
        result = await api.call(token_of(ctx), "GET", "/knowledgeTerm/describe", params={"iri": term, "layerChain": "true"})
        return {"term": result["description"].get("curie", term), "layer": result["description"].get("layer"),
                **result["layerChain"]}

    @mcp.tool()
    async def resolve_intent(intent: str, ctx: Context) -> dict[str, Any]:
        """Resolve the terms an intent uses (Turtle, JSON-LD, or a TMF921 intent JSON): which module defines each,
        its layer and label. Terms no published module defines are returned as clarification questions."""
        token = token_of(ctx)
        try:
            graph = intent_graph(intent)
        except Exception as exc:  # noqa: BLE001 - the caller's document
            raise ToolError(f"The intent could not be parsed: {exc}") from exc
        resolved, unresolved = [], []
        for term in intent_terms(graph):
            try:
                d = (await api.call(token, "GET", "/knowledgeTerm/describe", params={"iri": term}))["description"]
                resolved.append({"term": term, "curie": d.get("curie"), "definedIn": d.get("definedIn"),
                                 "layer": d.get("layer"), "label": (d.get("labels") or [None])[0]})
            except ToolError as exc:
                if " 404:" not in str(exc):
                    raise
                unresolved.append(term)
        return {"triples": len(graph), "resolved": resolved, "unresolved": unresolved,
                "questions": [f"What does <{t}> mean? No published ontology module defines it." for t in unresolved]}

    @mcp.tool()
    async def validate(document: str, shapes: str, ctx: Context) -> dict[str, Any]:
        """SHACL-validate an RDF document (Turtle, or JSON-LD) against a shapes module (its id)."""
        media = "application/ld+json" if document.lstrip().startswith(("{", "[")) else "text/turtle"
        return await api.call(token_of(ctx), "POST", f"/knowledgeSpecification/{shapes}/validation/document",
                              content=document.encode(), headers={"Content-Type": media})
