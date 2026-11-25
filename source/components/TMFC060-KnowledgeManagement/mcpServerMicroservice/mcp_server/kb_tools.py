"""Knowledge-base tools: list/get, hybrid_search, recommend (+ feedback), the graph tools get_entity, get_subgraph and
get_path (c-kg's designs, bounded, run as SPARQL through POST /knowledge/{id}/query), and propose_mapping (a mapping
plus a proposal review item: never agreed). Every call carries the caller's bearer token."""
import re
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from .api import KnowledgeApi, ToolError
from .tools import token_of

IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s<>\"{}|^`\\]+$")
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SKIP = ("urn:tmfc060:process:", "urn:tmfc060:task:", "urn:tmfc060:worker:")
MAX_TRIPLES = 500


def check_iri(value: str, name: str) -> str:
    if not isinstance(value, str) or not IRI.match(value):
        raise ToolError(f"{name} must be an absolute IRI")
    return value


def followed(node: str) -> bool:
    return not node.startswith(SKIP) and ":chunk:" not in node and ":document:" not in node


def values(nodes) -> str:
    return " ".join(f"<{n}>" for n in sorted(nodes))


def rows(result: dict) -> list[dict]:
    return result["results"]["bindings"]


def register(mcp: FastMCP, api: KnowledgeApi) -> None:
    async def run_sparql(ctx: Context, knowledge: str, sparql: str) -> list[dict]:
        return rows(await api.call(token_of(ctx), "POST", f"/knowledge/{knowledge}/query", content=sparql.encode(),
                                   headers={"Content-Type": "application/sparql-query",
                                            "Accept": "application/sparql-results+json"}))

    async def outgoing(ctx, knowledge: str, nodes: set[str]) -> list[dict]:
        found = await run_sparql(ctx, knowledge, f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values(nodes)} }} ?s ?p ?o }}")
        return [{"subject": r["s"]["value"], "predicate": r["p"]["value"], "object": r["o"]["value"],
                 "objectIsIri": r["o"]["type"] == "uri"} for r in found]

    @mcp.tool()
    async def list_knowledge(ctx: Context) -> list[dict[str, Any]]:
        """List the knowledge-bases: id, name, store kind, lifecycle state, the ontology modules they conform to and
        their statistics."""
        return await api.call(token_of(ctx), "GET", "/knowledge", params={
            "fields": "id,name,description,storeKind,lifecycleStatus,conformsTo,statistics"})

    @mcp.tool()
    async def get_knowledge(knowledge: str, ctx: Context) -> dict[str, Any]:
        """Get one knowledge-base by id."""
        return await api.call(token_of(ctx), "GET", f"/knowledge/{knowledge}")

    @mcp.tool()
    async def hybrid_search(knowledge: str, query: str, ctx: Context, topK: int | None = None,
                            scoreThreshold: float | None = None, hybrid: bool | None = None,
                            iriFilter: list[str] | None = None) -> list[dict[str, Any]]:
        """Hybrid (dense + keyword) search over a knowledge-base's chunks; each hit has its text, score and the IRIs
        of the entities it mentions. `iriFilter` keeps chunks about those IRIs (or subclasses)."""
        body = {"query": query, "topK": topK, "scoreThreshold": scoreThreshold, "hybrid": hybrid,
                "iriFilter": iriFilter}
        return (await api.call(token_of(ctx), "POST", f"/knowledge/{knowledge}/search",
                               json={k: v for k, v in body.items() if v is not None}))["hits"]

    @mcp.tool()
    async def recommend(knowledge: str, ctx: Context, context: str | None = None, iri: list[str] | None = None,
                        topK: int | None = None) -> dict[str, Any]:
        """Recommend entities and chunks for a situation (text `context` and/or entity IRIs), ranked by relevance,
        applicability and what worked before. Report the outcome with recommend_feedback."""
        body = {"context": context, "iri": iri, "topK": topK}
        return await api.call(token_of(ctx), "POST", f"/knowledge/{knowledge}/recommend",
                              json={k: v for k, v in body.items() if v is not None})

    @mcp.tool()
    async def recommend_feedback(knowledge: str, recommendationId: str, itemId: str, ctx: Context,
                                 used: bool | None = None, outcome: str | None = None) -> dict[str, Any]:
        """Report whether a recommended item was used and its outcome (success, failure, ignored)."""
        body = {"recommendationId": recommendationId, "itemId": itemId, "used": used, "outcome": outcome}
        await api.call(token_of(ctx), "POST", f"/knowledge/{knowledge}/recommend/feedback",
                       json={k: v for k, v in body.items() if v is not None})
        return {"recorded": True}

    @mcp.tool()
    async def get_entity(knowledge: str, entity: str, ctx: Context) -> list[dict[str, str]]:
        """Every triple where the IRI is the subject or the object."""
        node = check_iri(entity, "entity")
        out = [{k: t[k] for k in ("subject", "predicate", "object")} for t in await outgoing(ctx, knowledge, {node})]
        incoming = await run_sparql(ctx, knowledge, f"SELECT ?s ?p WHERE {{ ?s ?p <{node}> }}")
        out += [{"subject": r["s"]["value"], "predicate": r["p"]["value"], "object": node} for r in incoming]
        return out

    @mcp.tool()
    async def get_subgraph(knowledge: str, root: str, ctx: Context, hops: int = 2) -> dict[str, Any]:
        """The triples reachable from a node by following outgoing IRI edges for `hops` steps (default 2, max 4),
        at most 500 triples. rdf:type edges and provenance nodes are listed but not followed."""
        if not 1 <= hops <= 4:
            raise ToolError("hops must be between 1 and 4")
        frontier, visited, triples = {check_iri(root, "root")}, set(), []
        for _ in range(hops):
            visited |= frontier
            nxt = set()
            for t in await outgoing(ctx, knowledge, frontier):
                triples.append({k: t[k] for k in ("subject", "predicate", "object")})
                if len(triples) >= MAX_TRIPLES:
                    return {"root": root, "hops": hops, "triples": triples, "truncated": True}
                if t["objectIsIri"] and t["predicate"] != RDF_TYPE and followed(t["object"]) \
                        and t["object"] not in visited:
                    nxt.add(t["object"])
            if not nxt:
                break
            frontier = nxt
        return {"root": root, "hops": hops, "triples": triples, "truncated": False}

    @mcp.tool()
    async def get_path(knowledge: str, start: str, end: str, ctx: Context, maxHops: int = 4) -> list[dict[str, str]]:
        """The shortest path of outgoing IRI edges from `start` to `end` (at most `maxHops`, default 4, max 6), as
        ordered triples; [] when there is none."""
        if not 1 <= maxHops <= 6:
            raise ToolError("maxHops must be between 1 and 6")
        start, end = check_iri(start, "start"), check_iri(end, "end")
        if start == end:
            return []
        came_from: dict[str, dict] = {}
        frontier, visited = {start}, {start}
        for _ in range(maxHops):
            nxt = set()
            for t in await outgoing(ctx, knowledge, frontier):
                o = t["object"]
                if not t["objectIsIri"] or t["predicate"] == RDF_TYPE or o in visited or not followed(o):
                    continue
                visited.add(o)
                came_from[o] = {k: t[k] for k in ("subject", "predicate", "object")}
                if o == end:
                    path, node = [], end
                    while node != start:
                        path.append(came_from[node])
                        node = came_from[node]["subject"]
                    return list(reversed(path))
                nxt.add(o)
            if not nxt:
                return []
            frontier = nxt
        return []

    @mcp.tool()
    async def propose_mapping(module: str, subject: str, predicate: str, object: str, ctx: Context,
                              confidence: str | None = None, rationale: str | None = None) -> dict[str, Any]:
        """Propose a bridge mapping on a bridge module: it is created and submitted for review (state proposed),
        never agreed. Needs the ontology-editor role."""
        token = token_of(ctx)
        body = {"subject": subject, "predicate": predicate, "object": object, "confidence": confidence,
                "rationale": rationale}
        mapping = await api.call(token, "POST", f"/knowledgeSpecification/{module}/mapping",
                                 json={k: v for k, v in body.items() if v is not None})
        try:
            review = await api.call(token, "POST", f"/knowledgeSpecification/{module}/review", json={
                "kind": "proposal", "target": {"kind": "mapping", "id": mapping["id"]},
                "text": rationale or "Proposed by an agent through the MCP server"})
        except ToolError:
            await api.call(token, "DELETE", f"/knowledgeSpecification/{module}/mapping/{mapping['id']}")
            raise
        mapping = await api.call(token, "GET", f"/knowledgeSpecification/{module}/mapping/{mapping['id']}")
        return {"mapping": mapping, "review": review}
