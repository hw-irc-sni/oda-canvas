# Spike S3: pyoxigraph SPARQL SERVICE and endpoint allowlisting

Issue: [hw-irc-sni/oda-canvas#20](https://github.com/hw-irc-sni/oda-canvas/issues/20). Run on 2026-11-22 with
pyoxigraph 0.5.13 and rdflib 7.1.1. Re-run when either version changes.

## Run

```bash
python -m venv .venv && .venv/bin/pip install pyoxigraph==0.5.13 rdflib==7.1.1
docker run -d --rm --name s3peer -p 17878:7878 ghcr.io/oxigraph/oxigraph:0.5.3 serve --location /data --bind 0.0.0.0:7878
curl -X POST -H 'Content-Type: text/turtle' 'http://localhost:17878/store?default' --data-binary \
  '<https://partner.example.net/cell/4711> <https://partner.example.net/ont#vendor> "VendorA" .
   <https://partner.example.net/cell/4712> <https://partner.example.net/ont#vendor> "VendorB" .'

# one case at a time: a1 a2 b1 b2 b3 b4 (b1 and b2 hang; run them with `timeout 40`)
.venv/bin/python -u native_service.py http://localhost:17878/query a1
.venv/bin/python allowlist_precheck.py
.venv/bin/python readonly_worker.py
docker stop s3peer
```

## Results

| Case | Result |
|---|---|
| a1 plain `SERVICE <peer>` | Works: both remote rows returned |
| a2 local triple joined with `SERVICE <peer>` | Works: `INC-1042` joined with `VendorA` |
| b1 unreachable peer | Hangs: no answer after 40 s (no query timeout in pyoxigraph) |
| b2 unreachable peer with `SERVICE SILENT` | Hangs the same way |
| b3 `SERVICE ?e` | `RuntimeError: The variable encoding the service name is unbound` |
| b4 `default_graph`/`named_graphs` restricted | The remote call still runs: these options only restrict the local dataset |
| `Store.query` options | No service handler, allowlist, HTTP or timeout option. There is no query parser in the Python API |
| rdflib algebra pre-check | Finds SERVICE in plain, OPTIONAL, UNION, subquery, ASK and CONSTRUCT queries, and variable targets. SERVICE nested in SERVICE fails rdflib's parser (RecursionError), so it is rejected, fail closed |
| `Store.read_only(path)` from another process | Works while the writer holds the store: about 22 ms to open 200k triples, and it sees the latest flushed write. `Store.secondary` is gone in 0.5 |

## Decision (for #31 and the `/query` timeout)

1. Before running a query, parse it with rdflib and collect every `SERVICE` target. Reject the query
   (`403 SERVICE_NOT_ALLOWED`) when a target is not the endpoint of a published `remote` module, is a
   variable, or the query cannot be parsed.
2. Run each `/query` in a short-lived worker process that opens the store with `Store.read_only` and
   uses pyoxigraph's native SERVICE. Kill it at the timeout and answer `504`. The API process stays the
   only writer.
3. As defence in depth, the Phase 3 chart can add a NetworkPolicy that limits egress to the peer
   endpoints.
