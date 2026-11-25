"""KM-FX-01 Data Processing: parse, clean and chunk a document, or turn a TMF batch into records and chunks.

Result (the next step's ``input``):
- documents: ``{"kind": "text", "documentId", "mediaType", "text", "chunks": [{chunkId, text, start, end, section}]}``
- RDF (Turtle, JSON-LD): ``{"kind": "rdf", "documentId", "mediaType", "content"}``, not chunked (lifted by KM-FX-02)
- TMF batches: ``{"kind": "tmf", "source", "records": [...], "chunks": [...]}``
"""
from . import StepError
from .chunk import chunk
from .parse import PARSERS, RDF_TYPES, layout
from .tmf import SOURCES, record, render, resources_of


def handler(settings=None):
    max_tokens = getattr(settings, "chunk_max_tokens", 400)
    overlap = getattr(settings, "chunk_overlap_tokens", 60)

    async def km_fx_01(task: dict, client) -> dict:
        payload = task["payload"]
        if payload.get("documentId"):
            return await document(task, client, payload["kbId"], payload["documentId"])
        source = payload.get("source")
        if source in SOURCES:
            return tmf_batch(source, payload.get("params") or {})
        raise StepError(f"KM-FX-01 cannot process the source {source!r} yet", retryable=False)

    async def document(task, client, kb_id, doc_id) -> dict:
        meta, data = await client.document(kb_id, doc_id)
        media_type = meta["mediaType"]
        if media_type in RDF_TYPES:
            return {"kind": "rdf", "documentId": doc_id, "mediaType": media_type,
                    "content": data.decode("utf-8", errors="replace")}
        parser = PARSERS.get(media_type)
        if parser is None:
            raise StepError(f"no parser for {media_type}", retryable=False)
        try:
            blocks = parser(data)
        except Exception as exc:  # noqa: BLE001 - a broken document will not parse on a retry either
            raise StepError(f"{media_type} could not be parsed: {type(exc).__name__}: {exc}", retryable=False) from exc
        text, laid_out = layout(blocks)
        chunks = [{"chunkId": f"{doc_id}:{c['seq']}", "text": c["text"], "start": c["start"], "end": c["end"],
                   "section": c["section"]} for c in chunk(text, laid_out, max_tokens, overlap)]
        return {"kind": "text", "documentId": doc_id, "mediaType": media_type, "text": text, "chunks": chunks}

    def tmf_batch(source: str, params: dict) -> dict:
        resources = resources_of(params)
        records = [record(r, source) for r in resources]
        chunks = [{"chunkId": f"{source}:{r.get('id') or i}", "text": render(r, source), "start": None, "end": None,
                   "section": r.get("@type")} for i, r in enumerate(resources)]
        return {"kind": "tmf", "source": source, "records": records, "chunks": chunks}

    return km_fx_01
