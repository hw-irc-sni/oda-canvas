# TMFC060 pipeline worker

The ingestion worker of the TMFC060 Knowledge Management component. It claims the KM-FX tasks of ingestion processes
and runs them: KM-FX-01 data processing to KM-FX-06 knowledge recommendation (the step handlers arrive with issues
#40–#45; until then every step completes as a no-op).

The worker reaches the component **only through the knowledge API's internal port** (`INTERNAL_PORT`, see the API
README): it has no database, Qdrant or model access, and shares no code with the API. Run several replicas: the API
hands out each task once (`SELECT … FOR UPDATE SKIP LOCKED`).

## Run

```bash
uv sync
INTERNAL_API_URL=http://localhost:8081 INTERNAL_TOKEN_FILE=./token uv run python -m worker
uv run pytest            # starts the real API (dev-only path dependency) on a Postgres testcontainer
docker build -t tmforumodacanvas/tmfc060-pipeline-worker:dev .
```

## How it works

- `WORKER_CONCURRENCY` slots each claim a task (`POST /internal/task/claim`), renew its lease every lease/3, run the
  step handler ([`worker/steps.py`](worker/steps.py)) and report `complete` or `fail`. A handler raises
  `StepError(retryable=…)` to fail its task; any other error is retried. A lost lease (409) abandons the task.
- On SIGTERM the worker stops claiming, gives in-flight tasks `WORKER_SHUTDOWN_GRACE` seconds, then fails the rest as
  retryable so another replica takes them at once.
- `WORKER_HEALTH_PORT` serves `/health` (the loop is alive; in-flight tasks, last claim) and `/ready` (the internal
  API accepts the token).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `INTERNAL_API_URL` | `http://localhost:8081` | The knowledge API's internal port |
| `INTERNAL_TOKEN_FILE` | `/etc/tmfc060/internal/token` | The shared service token (a Secret key; re-read when it changes) |
| `WORKER_ID` | the host name | The worker's name in task leases |
| `WORKER_CONCURRENCY` | `2` | Tasks in flight per replica |
| `WORKER_POLL_INTERVAL` | `2` | Seconds between claims when nothing is due |
| `WORKER_SHUTDOWN_GRACE` | `30` | Seconds in-flight tasks get on SIGTERM |
| `WORKER_REQUEST_TIMEOUT` | `30` | Seconds per internal API call |
| `WORKER_HEALTH_HOST` / `WORKER_HEALTH_PORT` | `0.0.0.0` / `8082` | The probe server |
| `LOG_LEVEL` | `INFO` | Log level |
