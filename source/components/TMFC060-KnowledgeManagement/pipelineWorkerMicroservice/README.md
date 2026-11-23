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

## KM-FX-02 Information Extraction

[`worker/steps/km_fx_02.py`](worker/steps/km_fx_02.py) turns KM-FX-01's output into triples that use the KB's
ontology (its conformsTo closure, read with the internal SPARQL call and cached for 5 minutes):

- **Text**: each chunk goes to the model (internal `models/chat`, JSON output) with the ontology's classes and
  properties, all of them or the `EXTRACTION_MAX_TERMS` (150) matching the chunk best plus their superclasses. The
  answer's entities get deterministic IRIs, `urn:tmfc060:kb:{kb}:entity:{class}:{label slug}`, so one entity in two
  chunks or documents is one node. Invalid JSON is asked again once.
- **RDF documents** (Turtle, JSON-LD without remote contexts) and **TMF921 intent expressions** are lifted directly;
  every **TMF record** also gets a generic mapping into FTO's Open API namespace
  (`https://models.tmforum.org/ontology/open-api/{api}/`).
- **The ontology filter**: a predicate must be declared by the ontology modules (with any type), or be `rdf:type`,
  `rdfs:label`/`comment`, SKOS labels or RDF list structure; an `rdf:type` object must be a class there. Schema a
  document declares itself (classes, properties, `rdfs:subClassOf`/`domain`/`range`) is kept with it, and its own
  terms are valid in it. Everything else is dropped and listed in `proposedTerms` for KM-FX-04.

## KM-FX-03 Knowledge Generation

[`worker/steps/km_fx_03.py`](worker/steps/km_fx_03.py) links extracted entities to existing ones: an entity whose
normalised label (rdfs:label, skos:prefLabel or altLabel; case, accents and punctuation ignored) and class (or a sub-
or superclass) match exactly one KB entity takes that entity's IRI; several matches are left unlinked and reported
as `linkCandidates`. It adds PROV-O, which travels with the facts to the document's source graph: entity
`prov:wasDerivedFrom` chunk, chunk `prov:wasDerivedFrom` document (TMF: `prov:hadPrimarySource` the resource),
entity `prov:wasGeneratedBy` the ingestion process (a `prov:Activity`) and `prov:generatedAtTime`. Re-ingesting a
document yields the same IRIs and replaces its source graph, so nothing is duplicated.

## KM-FX-04 Knowledge Fusion

[`worker/steps/km_fx_04.py`](worker/steps/km_fx_04.py) checks the delta before anything is stored:

- **SHACL** (pyshacl) against the shapes modules of the KB's conformsTo closure: a violation's triple (or, without a
  value, the focus node's new triples) is withheld.
- **Functional properties**: a new value that differs from the KB's existing one (outside this document's source
  graph), or several new values, are withheld.
- Withheld triples become `dataConflict` **proposals** (`/knowledge/{id}/proposal`), with a model's suggested
  resolution when a model route is up; nothing in conflict is written until a person accepts it. KM-FX-02's unknown
  terms and KM-FX-03's ambiguous matches become `ontologyTerm` and `entityLink` proposals. A re-ingest replaces the
  document's undecided proposals.
- **OWL-RL** (owlrl) over the ontology and the kept delta adds the inferred triples about the delta's subjects
  (no axiomatic noise); skipped above `REASONING_MAX_TRIPLES`.

## KM-FX-05 Knowledge Storage

[`worker/steps/km_fx_05.py`](worker/steps/km_fx_05.py) stores what KM-FX-04 kept, as the KB's `storeKind` says:
`graph` gets the triples (in the document's source graph, replacing its previous ones), `vector` gets the chunks
(with their entity and class IRIs as payload), `hybrid` both. Chunks are embedded through the API's model route in
batches of `EMBEDDING_BATCH` and indexed by the API (which adds the sparse BM25 vector); a re-ingest replaces the
document's chunks, so nothing stale stays searchable.

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
| `EXTRACTION_MAX_TERMS` | `150` | KM-FX-02: ontology terms sent to the model with a chunk |
| `REASONING_MAX_TRIPLES` | `200000` | KM-FX-04: no OWL-RL reasoning above this many triples (ontology + delta) |
| `EMBEDDING_BATCH` | `32` | KM-FX-05: chunk texts per embedding call |
