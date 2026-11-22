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
