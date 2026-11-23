"""KM-FX-05 Knowledge Storage: the triples to the document's source graph, the chunks to the vector store.

What is written follows the KB's ``storeKind``: ``graph`` (triples only), ``vector`` (chunks only, with their entity
and class IRIs as payload) or ``hybrid`` (both). Both writes replace what the same source wrote before, so
re-ingesting a document leaves nothing stale. Chunks are embedded through the API's model route in batches of
``EMBEDDING_BATCH``; the API adds the sparse (BM25) vector and upserts them.
"""
from ..client import IndexRefused, ModelRefused, ModelUnavailable
from . import StepError
from .rdf_out import ntriples


def handler(settings=None):
    batch = getattr(settings, "embedding_batch", 32)

    async def km_fx_05(task: dict, client) -> dict:
        previous = task.get("input") or {}
        if "triples" not in previous:
            raise StepError("KM-FX-05 got no KM-FX-04 result", retryable=False)
        kb_id = task["payload"]["kbId"]
        source = previous.get("documentId") or task.get("chain")
        kind = (await client.knowledge(kb_id))["storeKind"]
        written = indexed = 0
        if kind in ("graph", "hybrid"):
            await client.write_source_graph(kb_id, source, ntriples(previous["triples"]), task.get("id"))
            written = len(previous["triples"])
        if kind in ("vector", "hybrid"):
            chunks = [{"chunkId": c["chunkId"], "text": c["text"], "start": c.get("start"), "end": c.get("end"),
                       "documentId": previous.get("documentId"), "entityIri": c.get("entityIri") or [],
                       "classIri": c.get("classIri") or []} for c in previous.get("chunks") or [] if c.get("text")]
            dense: list[list[float]] = []
            try:
                for i in range(0, len(chunks), batch):
                    dense += await client.embed([c["text"] for c in chunks[i:i + batch]])
            except ModelUnavailable as exc:
                raise StepError(f"embedding model unavailable: {exc}") from exc
            except ModelRefused as exc:
                raise StepError(f"embedding refused: {exc}", retryable=False) from exc
            try:
                indexed = await client.index_chunks(kb_id, source, chunks, dense)
            except IndexRefused as exc:
                raise StepError(str(exc), retryable=False) from exc
        out = {k: v for k, v in previous.items() if k != "triples"}
        out.update({"storeKind": kind, "written": written, "indexed": indexed})
        return out

    return km_fx_05
