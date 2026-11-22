# TMFC060 knowledge API microservice

Serves the TMFxxx Knowledge Management v5 API ([`api/TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml`](api/TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml))
of the TMFC060 Knowledge Management reference component. It is the only process that writes the pyoxigraph
store (see [the design](../../../../Knowledge-Management-design.md)).

## Run locally

```bash
docker compose -f ../dev/docker-compose.yaml up -d --wait   # Postgres and Qdrant
uv sync
STORE_PATH=/tmp/tmfc060-store uv run uvicorn app.main:app --port 8080
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
| `KEYCLOAK_ISSUER`, `KEYCLOAK_JWKS_URL` | empty | Token validation (#23) |
| `HEALTH_CHECK_TIMEOUT` | `2.0` | Seconds per dependency check |

## Test and build

```bash
uv run pytest -q          # starts Postgres and Qdrant with testcontainers (needs Docker)
docker build -t tmforumodacanvas/tmfc060-knowledge-api:dev .
```
