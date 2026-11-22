# TMFC060 knowledge API microservice

Serves the TMFxxx Knowledge Management v5 API ([`api/TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml`](api/TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml))
of the TMFC060 Knowledge Management reference component. It is the only process that writes the pyoxigraph
store (see [the design](../../../../Knowledge-Management-design.md)).

## Run locally

```bash
docker compose -f ../dev/docker-compose.yaml up -d --wait   # Postgres and Qdrant
uv sync
STORE_PATH=/tmp/tmfc060-store AUTH_ENABLED=false uv run uvicorn --factory app.main:create_app --port 8080
curl localhost:8080/health   # liveness: always 200, with the status of each dependency
curl localhost:8080/ready    # readiness: 503 unless the store, Postgres and Qdrant are up
```

The API is mounted under `/{COMPONENT_NAME}/tmf-api/knowledgeManagement/v5`.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `COMPONENT_NAME` | `tmfc060-knowledgemanagement` | ODA Component name, first segment of the API path |
| `STORE_PATH` | `/data/store` | pyoxigraph RocksDB directory |
| `DATABASE_URL` | `postgresql://tmfc060:tmfc060@localhost:5432/tmfc060` | PostgreSQL |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant |
| `KEYCLOAK_JWKS_URL` | empty | In-cluster JWKS of the realm, for example `http://canvas-keycloak.canvas:8083/auth/realms/odari/protocol/openid-connect/certs` |
| `KEYCLOAK_ISSUERS` | empty | Comma-separated accepted `iss` values: the public and the in-cluster realm URL |
| `CANVAS_SYSTEM_ROLE` | `canvasRole` | Name of the Canvas system role on this component's client (the chart's `security.canvasSystemRole`) |
| `JWT_LEEWAY_SECONDS` | `30` | Clock skew allowed on `exp`, `nbf` and `iat` |
| `AUTH_ENABLED` | `true` | `false` skips token validation and grants every role. Local development only: refused in Kubernetes |
| `OAS_PATH` | `api/TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml` | The OAS whose `x-required-roles` drive the guards |
| `HEALTH_CHECK_TIMEOUT` | `2.0` | Seconds per dependency check |
| `QUERY_TIMEOUT_DEFAULT`, `QUERY_TIMEOUT_MAX` | `30`, `120` | Read-only SPARQL timeout in seconds: the default, and the cap on the client's `timeout` |
| `QUERY_ROW_CAP` | `10000` | Maximum SELECT rows (or CONSTRUCT triples); beyond it the result is flagged truncated |
| `ONTOLOGY_SOURCES` | `[]` | Federation repositories to sync, JSON: `[{"name": "fto", "git": {"url": "https://…", "ref": "main", "secretRef": "tfo-git"}}]` or `{"name": "x", "mount": {"path": "/etc/tmfc060/ontologies/x"}}` |
| `ONTOLOGY_SYNC_INTERVAL` | `900` | Seconds between syncs (also at startup) |
| `GIT_ALLOWED_SCHEMES` | `https` | Comma-separated URL schemes allowed for git sources |
| `GIT_SECRETS_DIR` | `/etc/tmfc060/git` | Git credentials: `<dir>/<secretRef>/username` and `password` (token), used through `GIT_ASKPASS` |
| `GIT_CACHE_DIR`, `GIT_TIMEOUT` | `/tmp/tmfc060-git`, `120` | Clone cache and timeout |
| `MOUNT_ROOTS` | `/etc/tmfc060/ontologies` | Comma-separated directories that `mount` sources must be under |
| `LOG_LEVEL` | `INFO` | Level of the application loggers |
| `FEDERATION_RELAY_PORT` | `8099` | Loopback port of the federation relay |
| `FEDERATION_SECRETS_DIR` | | Folder of per-module OAuth2 client Secrets (`<moduleId>/tokenUrl`, `clientId`, `clientSecret`) |
| `FEDERATION_CALL_TIMEOUT` | `10` | Seconds a federated SERVICE call may take |
| `FEDERATION_CACHE_TTL` | `60` | Seconds federated answers are cached (0 = off) |
| `TASK_LEASE_SECONDS` / `TASK_MAX_ATTEMPTS` / `TASK_RETRY_DELAY` | `300` / `3` / `30` | Ingestion task lease, attempts, and retry delay per attempt |
| `DOCUMENT_MAX_BYTES` | `52428800` | Largest document a knowledge-base accepts (50 MiB) |
| `MODEL_GATEWAY_SERVICE`, `MGS_CHAT_MODEL`, `MGS_EMBEDDING_MODEL` | | Route 1: the ModelGatewayService CR and its model names |
| `AGENTGATEWAY_URL`, `AGENTGATEWAY_CHAT_MODEL`, `AGENTGATEWAY_EMBEDDING_MODEL` | | Route 2: the agentgateway `/v1` URL and model names |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY_FILE`, `OPENAI_CHAT_MODEL`, `OPENAI_EMBEDDING_MODEL` | | Route 3: an OpenAI-compatible URL, the key file and model names |
| `MODEL_ROUTE_INTERVAL` | `300` | Seconds between route evaluations |
| `MODEL_PROBE_TIMEOUT` / `MODEL_CALL_TIMEOUT` | `5` / `120` | Seconds for the `/models` probe and for a model call |
| `EMBEDDING_DIMENSION` | `1024` | Dense vector size of a knowledge-base's Qdrant collection |
| `GOVERNANCE_REQUIRE_VALIDATION` | `true` | A ruling `agreed` needs the module's validation to conform |
| `DB_STARTUP_TIMEOUT` | `120` | Seconds the API retries Postgres at startup (backoff 1–10 s) before it exits |

## Ontology modules

[`app/ontology/registry.py`](app/ontology/registry.py) and [`app/routers/specifications.py`](app/routers/specifications.py)
serve `/knowledgeSpecification` (list, create, get, patch fields, delete) and its `/content`.

- **Ids:** a client slug `^[a-z0-9][a-z0-9-]{0,62}$`, or derived from `prefix`/`name`.
- **Content:** `upload` modules get it through `PUT /content` (`ontology-editor`); `componentContribution` modules
  from the contributing component (its token's `azp`, with the Canvas system role); `git` and `mount` modules from
  their source; `remoteEndpoint` modules have none.
- **Imports:** `imports[]` and the content's `owl:imports` resolve to registered modules by IRI; unresolved ones are
  logged and skipped.
- **Federation sync** ([`app/ontology/sync.py`](app/ontology/sync.py)): each `ONTOLOGY_SOURCES` repository in FTO
  layout (`modules.json`, `catalog.json`) is synced at startup and every `ONTOLOGY_SYNC_INTERVAL`. Its modules
  become `FederatedKnowledgeSpecification` (`published`, with the repository status as `sourceStatus`, and
  `@schemaLocation` served at `…/schema/FederatedKnowledgeSpecification.schema.json`). The layer is the module's
  `fto:layer`, else its top folder. A module without a file is metadata-only, without an IRI it gets
  `urn:tmfc060:federation:<source>:<key>`. A module gone from the repository becomes `deprecated`.
- **Shapes:** an `ONTOLOGY_SOURCES` entry may list `"shapes": [{"id": "tio-shacl-ext", "path": "implementation/tio-shacl-ext/rdf"}]`;
  each path (file or folder of `.ttl`) becomes a `specKind: shapes` module.

## Knowledge-bases

[`app/kb/`](app/kb/) serves `/knowledge` and `/knowledge/{id}`, with metadata in the `knowledge` table:

- `storeKind` `vector` or `hybrid` creates the Qdrant collection `tmfc060-kb-{id}` (dense vector `dense`, cosine,
  `EMBEDDING_DIMENSION`; sparse vector `sparse` with IDF) at creation, and 503 if Qdrant is down. Deleting the KB
  drops the collection, the graph and the documents. `storeKind` cannot change.
- `conformsTo` refs must name registered modules (by id or IRI), in any state; only published and deprecated ones
  reach the query dataset. A module named by a KB cannot be deleted (409).
- `statistics` (`triples`, `documents`, `chunks`) are computed on every read; `chunks` is left out when Qdrant is
  unreachable.
- A KB can be changed in every lifecycle state except deprecated (409). `lifecycleStatus` follows the `Knowledge`
  `x-lifecycle` (PATCH it alone). A `canvasSystemRole` caller is recorded as the owner of the KBs it creates and may
  change only those; a `knowledge-editor` may change any.
- `/knowledge/{id}/graph` is the Graph Store Protocol (GET, PUT, POST, DELETE) in Turtle or JSON-LD.
- `/knowledge/{id}/query` runs a read-only SPARQL query (`application/sparql-query` or the form field `query`) over
  the KB graph plus its conformsTo closure, in a killable worker: `timeout` capped by `QUERY_TIMEOUT_MAX` (504),
  the row cap (`X-Result-Truncated`), updates refused (400), and SERVICE only to allowlisted peers (below).

## Ingestion processes

`POST /knowledge/{id}/ingest` ([`app/processes.py`](app/processes.py)) starts a TMF701 v5 **process** and answers 201
with its `ProcessRef`. Each document (for `upload`: the named ones, or every one not yet ingested) or source batch
(`tmf…`, `remote`: a source of the KB, with `params`) gets a chain of six **tasks**, KM-FX-01 … KM-FX-06. 409 when
the KB is deprecated, the source is not one of its `source[]`, nothing is left to ingest, or a named document is
already being ingested.

- Workers `claim` tasks with `SELECT … FOR UPDATE SKIP LOCKED`: a task is due when the previous step of its chain
  has completed. A claim holds a lease of `TASK_LEASE_SECONDS` (300), renewed by `heartbeat`; an expired lease makes
  the task claimable again.
- `fail` reschedules a task `TASK_RETRY_DELAY` × attempt seconds later (30 s) until `TASK_MAX_ATTEMPTS` (3); then
  the task, the rest of its chain and its document fail. The process ends `completed`, or `failed` with the first
  error once no chain is still running.
- The component's **processflow** API (TMF701 v5 subset,
  [`api/TMF701-ProcessManagement-v5.0.0-subset.oas.yaml`](api/TMF701-ProcessManagement-v5.0.0-subset.oas.yaml)) reads
  them at `/{component}/tmf-api/process/v5/process` and `/task` (filter tasks with `processId=` or `documentId=`).

## Search

`POST /knowledge/{id}/search` ([`app/kb/vectors.py`](app/kb/vectors.py)) searches the chunks of a vector or hybrid
knowledge-base (409 for a graph one). `topK`, `scoreThreshold` and `hybrid` default to the KB's `retrieval` block
(8, 0, true).

- **Hybrid**: a dense prefetch (the query embedded by the active model route) and a sparse one
  ([`app/kb/sparse.py`](app/kb/sparse.py): hashed word tokens with BM25 term-frequency weights; Qdrant adds IDF),
  fused with Qdrant's RRF and normalised to [0, 1] (first in both lists: 1.0). **`hybrid: false`**: dense only,
  scored by cosine similarity. `scoreThreshold` applies to these scores.
- With no model route, a hybrid search falls back to the sparse side alone (rank-normalised); a dense-only one
  answers 503. The response header `X-Search-Mode` says which ran: `hybrid`, `dense` or `sparse`.
- `iriFilter` keeps chunks whose `entityIri` or `classIri` payload holds one of the IRIs or a subclass of one
  (`rdfs:subClassOf*` over the KB's query dataset).
- Chunks are written (by the pipeline, #44) with `Vectors.upsert_chunks`, which computes the sparse vector with the
  same encoder.

## Documents

[`app/kb/documents.py`](app/kb/documents.py) serves `/knowledge/{id}/document`: a `multipart/form-data` upload
(`file`, optional `name` and `description`) is streamed into 1 MiB `document_blob` rows, so every worker replica
can read it from Postgres.

- `DOCUMENT_MAX_BYTES` (50 MiB) is enforced while streaming, and from `Content-Length` before reading: 413, with
  nothing stored.
- The same bytes (sha256) twice in one knowledge-base: 409 naming the existing document.
- The media type is PDF, Markdown, HTML, Turtle, JSON-LD or JSON (415 otherwise): `%PDF-` content is a PDF, parseable
  JSON is JSON-LD when it has `@context` and JSON otherwise; then the part's Content-Type, then the file extension.
  A declared type the content contradicts gets 415.
- `…/{documentId}/content` streams the original bytes back. Delete removes the metadata, the blobs and the
  document's chunks in Qdrant (`documentId` payload); triples extracted from it and their provenance stay.
- Uploading does not ingest; ingestion starts with `…/ingest`.

## Model routes

[`app/models.py`](app/models.py) finds the LLM and embedding endpoints (OpenAI-compatible, `…/v1`) through a chain,
re-evaluated every `MODEL_ROUTE_INTERVAL` seconds:

1. **modelGatewayService**: the ModelGatewayService CR `MODEL_GATEWAY_SERVICE` (`name` or `namespace/name`), when its
   CRD exists, read with the pod's service account; its endpoint comes from the CR status or spec, else its proxy
   Service (as in the portal). Skipped if the CR lists models and not both `MGS_CHAT_MODEL`/`MGS_EMBEDDING_MODEL`.
2. **agentgateway**: `AGENTGATEWAY_URL`, with `AGENTGATEWAY_CHAT_MODEL`/`AGENTGATEWAY_EMBEDDING_MODEL`.
3. **openaiCompatible**: `OPENAI_BASE_URL`, the key from the file `OPENAI_API_KEY_FILE`, and
   `OPENAI_CHAT_MODEL`/`OPENAI_EMBEDDING_MODEL`.

A route needs both of its model names and is selected when `GET {base}/models` answers 2xx; a probe embedding then
detects the dimension, and `/health` flags a mismatch with `EMBEDDING_DIMENSION`. A call failing with a connection
error, a timeout or a 5xx fails over to the next route and is retried once; a 4xx is returned as is. `/health`
shows the active route, its models and the reason each other route was skipped (never the key); `/ready` does not
depend on it.

## Federation

[`app/ontology/federation.py`](app/ontology/federation.py): a `SERVICE` clause may only name the endpoint
(`source.url`) of a **published** `specKind: remote` module; anything else, a variable or a prefixed endpoint is
refused with 403 `SERVICE_NOT_ALLOWED` before any remote call. Allowed endpoints are rewritten to a relay that the
API runs on `127.0.0.1:FEDERATION_RELAY_PORT` only (never exposed by the Service). The relay forwards the SPARQL
request with:

- an OAuth2 client-credentials token when `FEDERATION_SECRETS_DIR/<moduleId>/` holds `tokenUrl`, `clientId` and
  `clientSecret` (for a peer TMFC060, whose Keycloak grants that client `knowledge-viewer`), cached until it expires;
- `FEDERATION_CALL_TIMEOUT` (10 s, never past the query deadline): a peer that doesn't answer gives 504
  `FEDERATION_TIMEOUT`, a failing one 502 `FEDERATION_ERROR` (`SERVICE SILENT` continues without it);
- an answer cache of `FEDERATION_CACHE_TTL` seconds (60; 0 turns it off).

## Governance

[`app/ontology/governance.py`](app/ontology/governance.py) runs the governed lifecycle
`draft → proposed → inReview → agreed | rejected → published → deprecated` (`rejected → draft` to revise). The
transitions and their roles are read from the OAS `x-lifecycle` of `KnowledgeSpecification` (and `Knowledge`, for
knowledge-bases); the review rules from `x-review-kinds` of `ReviewItem`.

- A transition is a `PATCH` of `lifecycleStatus` alone, or a review item: a `proposal` (editor, draft → proposed),
  a `comment` (reviewer, only while proposed or inReview) or a `ruling` (approver, inReview → agreed or rejected,
  setting `linkStatus`). Wrong role: 403. Transition not in the lifecycle: 409.
- A ruling `agreed` runs validation first and is refused (409) unless it conforms
  (`GOVERNANCE_REQUIRE_VALIDATION`, default true).
- Modules imported by the federation sync are governed by their repository: 409.
- Every transition is recorded in `state_transition` and published as a StateChange event, and written back to RDF:
  `fto:linkStatus` and `owl:deprecated` on the module's `owl:Ontology` node; `adms:status` and, per ruling, a
  `prov:Activity` in the governance graph `urn:tmfc060:governance`.

## Mappings

[`app/ontology/mappings.py`](app/ontology/mappings.py) serves `/knowledgeSpecification/{id}/mapping` on
`specKind: bridge` modules (409 otherwise). A mapping (`subject`, `predicate`, `object` as IRIs or CURIEs of
registered prefixes, `confidence` high|medium|low, `rationale`) is written to the bridge module graph as FTO's
`irc-fto-bridge.ttl` does: the asserted statement plus a reified record `<moduleIRI><id> a <moduleIRI>mapping`, in
the module's own namespace. `status` is `agreed` for agreed and published mappings and `proposed` otherwise.

- Mappings follow the module lifecycle, via `PATCH lifecycleStatus` or review items with
  `target: {kind: mapping, id}`.
- Editing is allowed in any state except deprecated; editing an agreed or published mapping puts it back to draft.
- The records of synced bridge modules are imported read-only (FTO `agreed` → published, `proposed` → proposed).

## Term lookups

`GET /knowledgeTerm` ([`app/ontology/terms.py`](app/ontology/terms.py)) ports the FTO MCP tools `fto_describe`,
`fto_search` and `fto_layer_of` to the module graphs of the registry: published and deprecated modules, or every
module with `includeUnpublished=true`. Give exactly one of `iri` and `q`.

- `iri=` (an IRI or a CURIE): labels, definitions, types, parents, SKOS matches, the defining modules, and the
  term's own `fto:layer` and `fto:linkStatus`; with `layerChain=true` also the chain of first parents (up to 12)
  with `endsAtFoundational`. A plain word or unknown prefix gives 400 (use `q=`); a term no module mentions, 404.
- `q=`: ranked hits (exact label, label prefix, label substring, IRI local name, then definition or comment), each
  with its module and that module's layer, up to `limit` (25).
- CURIEs use the prefixes the modules list (`prefix`), bound to the namespaces their content declares, so TIO's
  `icm`, `imo`, `met`, … each mean their own sub-namespace; a module listing a single prefix its content doesn't
  declare binds it to the module IRI.

## Validation

`POST /knowledgeSpecification/{id}/validation` runs, and `GET` returns, the latest report
([`app/ontology/checks.py`](app/ontology/checks.py)):

- The five structural rules of FTO's `scripts/check.py`, per module: `parse`, `layerAnnotation` (the module's
  `fto:layer`; missing is a warning), `layerMonotonicity` (deeper-layer inheritance; a warning in bridge-like
  modules: `specKind: bridge` or content using `fto:linkStatus`), `parentsResolve` (parents in the namespaces of
  registered modules with content must be declared, as `owl:Class` or `rdfs:Class`), and `catalogIntegrity` (the
  content declares the module IRI; unresolved imports are warnings; placeholder IRIs are not checked). Class and
  layer lookups use the union of all registered modules, as check.py merges its modules.
- SHACL (pyshacl, inference none, as FTO's `scripts/validate.py`): the module's content against the `shapes`
  modules it imports, with the rest of its import closure as the ontology graph.
- The sync also runs check.py's repository-level part (every `.ttl` outside `_sources/` parses, `catalog.json` is
  intact) and logs it.

## RDF store and queries

- [`app/graphstore.py`](app/graphstore.py): the single-writer pyoxigraph store, with one named graph per module
  (`urn:tmfc060:module:{id}`), per knowledge-base (`urn:tmfc060:kb:{id}`) and a governance graph. Graph Store
  Protocol operations in Turtle or JSON-LD.
- [`app/dataset.py`](app/dataset.py): a knowledge-base query sees its own graph, plus the modules it conforms to and
  their transitive imports, **published (or deprecated) modules only**.
- [`app/query.py`](app/query.py): read-only SPARQL (spike S3). An rdflib pre-check rejects updates, parse failures and
  non-allowlisted or variable `SERVICE` targets. Each query then runs in **its own spawned process** on
  `Store.read_only`, which is killed at the timeout (504).

## PostgreSQL schema

SQLAlchemy 2 async models in [`app/db/models.py`](app/db/models.py), migrated by Alembic
([`app/db/migrations/`](app/db/migrations)) **at API startup**, under a Postgres advisory lock. The worker never
migrates.

| Table | Holds |
|---|---|
| `knowledge`, `knowledge_specification`, `mapping` | Resource metadata: indexed columns plus the served body (JSONB) |
| `document`, `document_blob` | Document metadata, and its content in 1 MiB `bytea` rows (deleted with the document) |
| `process`, `task` | TMF701 v5 processes and their KM-FX tasks; workers claim tasks with `FOR UPDATE SKIP LOCKED` |
| `review_item`, `state_transition` | Proposals, comments and rulings, and the audit trail of lifecycle transitions |
| `hub_subscription` | Event subscriptions registered through `/hub` |
| `recommend_feedback` | Feedback on recommendations (KM-FX-06) |

To add a migration, change the models and autogenerate a revision against a database at head (see
`app/db/migrate.py` for running Alembic on a connection), then review it.

## Authentication and roles

Every API call needs a bearer JWT from the Canvas Keycloak (realm `odari`). Following spike S1
([#70](https://github.com/hw-irc-sni/oda-canvas/issues/70)), the token must verify against the JWKS, have an
accepted issuer and contain `COMPONENT_NAME` in `aud`. The caller's roles are
`resource_access[COMPONENT_NAME].roles`. Each route declares its OAS `operationId`
(`Depends(require_operation("listKnowledge"))`), and the roles allowed come from that operation's
`x-required-roles` in the OAS. An invalid token gets 401; a valid token without a required role gets 403.

| Operations | Roles (any one) |
|---|---|
| Read knowledge-bases (list, get, graph GET, query, search, recommend and feedback, documents, `/hub`) | `knowledge-viewer`, `knowledge-editor`, `canvasSystemRole` |
| Change knowledge-bases (create, graph PUT/POST/DELETE, document upload/delete, ingest) | `knowledge-editor`, `canvasSystemRole` |
| Patch a knowledge-base (lifecycle transitions per `x-lifecycle` of `Knowledge`) | `knowledge-editor`, `ontology-reviewer`, `ontology-approver`, `canvasSystemRole` |
| Read the ontology (modules, content, mappings, reviews, validation, `/knowledgeTerm`) | all five component roles, `canvasSystemRole` |
| Author modules, content and mappings | `ontology-editor` |
| Review: comment, run validation | `ontology-reviewer` (editor and approver may also run validation) |
| Rule | `ontology-approver` |
| Lifecycle of modules and mappings | per transition, `x-lifecycle` of `KnowledgeSpecification` |
| Delete a module or mapping | `ontology-editor` (draft or rejected), `ontology-approver` |

## Test and build

```bash
uv run pytest -q          # starts Postgres and Qdrant with testcontainers (needs Docker)
docker build -t tmforumodacanvas/tmfc060-knowledge-api:dev .
```
