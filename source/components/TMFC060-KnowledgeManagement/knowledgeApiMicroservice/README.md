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
| `DB_STARTUP_TIMEOUT` | `120` | Seconds the API retries Postgres at startup (backoff 1–10 s) before it exits |

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
