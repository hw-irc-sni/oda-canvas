# TMFC060 MCP server

The MCP server of the TMFC060 Knowledge Management component: the federated TM Forum ontology (FTO) tool set and the
knowledge-base tools, for AI agents. It uses the official `mcp` SDK (FastMCP, 1.30.0) over **streamable
HTTP at `/mcp`**, like the reference ProductCatalog MCP server, and wraps the knowledge API: every call forwards the
caller's bearer token, so the API's roles apply. A request without `Authorization: Bearer …` gets 401.

## Tools

| Tool | Wraps | Semantics (tfo `mcp/server.py`) |
|---|---|---|
| `modules` | `GET /knowledgeSpecification` | the modules with layer, IRI, state |
| `sparql(query, knowledge)` | `POST /knowledge/{id}/query` | read-only SPARQL over a knowledge-base and its ontology |
| `describe(term)` | `GET /knowledgeTerm/describe?iri=` | labels, definitions, layer, defining modules, parents, matches |
| `search(text, limit)` | `GET /knowledgeTerm?q=` | terms by label, local name or definition |
| `layer_of(term)` | `GET /knowledgeTerm/describe?iri=&layerChain=true` | the term's layer and its chain of parents |
| `resolve_intent(intent)` | `/knowledgeTerm/describe` per term | the terms an intent (Turtle, JSON-LD or TMF921 JSON) uses, where each is defined, and questions for the undefined ones |
| `validate(document, shapes)` | `POST /knowledgeSpecification/{id}/validation/document` | SHACL validation against a shapes module |

Knowledge-base tools ([`mcp_server/kb_tools.py`](mcp_server/kb_tools.py); the graph tools follow c-kg's designs, run
as SPARQL through the KB's `/query`, and are bounded):

| Tool | Wraps | What it does |
|---|---|---|
| `list_knowledge`, `get_knowledge(knowledge)` | `GET /knowledge[/{id}]` | the knowledge-bases, their ontology and statistics |
| `hybrid_search(knowledge, query, …)` | `POST /knowledge/{id}/search` | chunks with the IRIs of the entities they mention |
| `recommend(knowledge, context?, iri?, topK?)` | `POST /knowledge/{id}/recommend` | ranked entities and chunks for a situation |
| `recommend_feedback(knowledge, recommendationId, itemId, …)` | `POST …/recommend/feedback` | report what was used and how it went |
| `get_entity(knowledge, entity)` | `/query` | every triple with the IRI as subject or object |
| `get_subgraph(knowledge, root, hops)` | `/query` | outgoing IRI edges for `hops` (≤ 4) steps, ≤ 500 triples |
| `get_path(knowledge, start, end, maxHops)` | `/query` | the shortest outgoing path (≤ 6 hops), or `[]` |
| `propose_mapping(module, subject, predicate, object, …)` | `POST …/mapping` + `POST …/review` | a bridge mapping submitted for review: proposed, never agreed (ontology-editor) |

Provenance nodes and `rdf:type` edges are not followed; IRIs are checked before they reach SPARQL.

## Run

```bash
uv sync
KNOWLEDGE_API_URL=http://localhost:8080/tmfc060-knowledgemanagement/tmf-api/knowledgeManagement/v5 uv run python -m mcp_server
uv run pytest            # the real API (dev-only path dependency) on a Postgres testcontainer, and an MCP client
docker build -t tmforumodacanvas/tmfc060-mcp-server:dev .
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_API_URL` | `http://localhost:8080/tmfc060-knowledgemanagement/tmf-api/knowledgeManagement/v5` | The knowledge API's TMFxxx base URL |
| `REQUEST_TIMEOUT` | `60` | Seconds per API call |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8080` | Listener (`/mcp`, `/health`) |
| `LOG_LEVEL` | `INFO` | Log level |
