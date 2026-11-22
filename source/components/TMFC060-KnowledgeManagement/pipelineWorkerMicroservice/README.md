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

## KM-FX-01 Data Processing

[`worker/steps/km_fx_01.py`](worker/steps/km_fx_01.py) reads the document through the internal API and:

- **PDF** (`pypdf`), **Markdown**, **HTML** (Python's `html.parser`; `script`, `style`, `nav`, `header`, `footer` and
  `aside` dropped) and plain **JSON** (`path: value` lines) become structural blocks (headings, paragraphs, list
  items, table rows, code), cleaned of control characters and extra whitespace. PDF lines found on more than half
  of the pages (running headers, footers, page numbers) are dropped.
- Blocks are chunked by section (a heading starts one) up to `CHUNK_MAX_TOKENS` (400), overlapping by about
  `CHUNK_OVERLAP_TOKENS` (60); tokens are estimated as characters / 4. A long block is split on sentences, then words.
  Each chunk has its `start`/`end` in the extracted text.
- **Turtle and JSON-LD** are not chunked: the content goes on to KM-FX-02 to be lifted directly.
- **TMF batches** (TMF639/638/642/628/921: `params.resources`, or a TMF event in `params.event`) give one record per
  resource for lifting and one `key: value` text chunk per resource for search.

The result is handed to KM-FX-02 as its `input` (at most `TASK_RESULT_MAX_BYTES` on the API; a larger result fails
the step).

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
| `CHUNK_MAX_TOKENS` / `CHUNK_OVERLAP_TOKENS` | `400` / `60` | KM-FX-01 chunk size and overlap (tokens ≈ characters / 4) |
