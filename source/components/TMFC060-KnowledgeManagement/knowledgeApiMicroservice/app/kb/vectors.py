"""The Qdrant collection of a vector or hybrid knowledge-base: a named dense vector and a sparse (BM25) vector.

Each point is a chunk, with the payload ``kbId``, ``documentId``, ``chunkId``, ``text``, ``start``, ``end``,
``entityIri`` (graph entities it mentions) and ``classIri`` (their classes); ``documentId``, ``entityIri`` and
``classIri`` are indexed. Hybrid search fuses a dense and a sparse prefetch with Qdrant's RRF; Qdrant 1.13 scores
each list 1/(2 + rank) with rank from 0, so the fused score is normalised by 0.5 per list (first in every list: 1.0).
"""
import uuid
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient, models

from ..errors import ApiError
from . import sparse

DENSE, SPARSE = "dense", "sparse"
INDEXED = ("documentId", "entityIri", "classIri", "source")
RRF_TOP = 0.5  # Qdrant's RRF contribution of a list's first hit
PREFETCH_FACTOR, PREFETCH_MIN = 4, 50
CHUNK_NAMESPACE = uuid.UUID("6f1d2b1e-3c55-4d0a-9a43-6b1c7e0a2f60")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    document_id: str | None = None
    start: int | None = None
    end: int | None = None
    entity_iri: list[str] = field(default_factory=list)
    class_iri: list[str] = field(default_factory=list)
    source: str | None = None  # the document or batch it came from (replace-by-source)


def point_id(chunk: Chunk) -> str:
    """Content-addressed: the same chunk of the same document always gets the same point."""
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{chunk.document_id or chunk.source}|{chunk.start}|{chunk.end}|"
                                           f"{chunk.text}"))


def collection_name(kb_id: str) -> str:
    return f"tmfc060-kb-{kb_id}"


def unavailable(exc: Exception) -> ApiError:
    return ApiError(503, "503", "Service Unavailable", f"The vector store is not available: {type(exc).__name__}")


class Vectors:
    def __init__(self, client: AsyncQdrantClient, dimension: int):
        self.client = client
        self.dimension = dimension

    async def create(self, kb_id: str) -> None:
        try:
            await self.client.create_collection(
                collection_name(kb_id),
                vectors_config={DENSE: models.VectorParams(size=self.dimension, distance=models.Distance.COSINE)},
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)})
            for key in INDEXED:
                await self.client.create_payload_index(collection_name(kb_id), key,
                                                       field_schema=models.PayloadSchemaType.KEYWORD)
        except Exception as exc:  # noqa: BLE001 - any transport or server error
            raise unavailable(exc) from exc

    async def drop(self, kb_id: str) -> None:
        try:
            if await self.client.collection_exists(collection_name(kb_id)):
                await self.client.delete_collection(collection_name(kb_id))
        except Exception as exc:  # noqa: BLE001
            raise unavailable(exc) from exc

    async def count(self, kb_id: str) -> int | None:
        """Indexed chunks, or None when Qdrant can't be reached."""
        try:
            return (await self.client.count(collection_name(kb_id), exact=True)).count
        except Exception:  # noqa: BLE001 - statistics are best effort
            return None

    async def upsert_chunks(self, kb_id: str, chunks: list[Chunk], dense: list[list[float]]) -> None:
        """Index chunks with their dense vectors; the sparse vector is computed here (sparse.encode_document)."""
        points = []
        for chunk, vector in zip(chunks, dense, strict=True):
            indices, values = sparse.encode_document(chunk.text)
            payload = {"kbId": kb_id, "chunkId": chunk.chunk_id, "text": chunk.text, "documentId": chunk.document_id,
                       "start": chunk.start, "end": chunk.end, "entityIri": chunk.entity_iri,
                       "classIri": chunk.class_iri, "source": chunk.source}
            points.append(models.PointStruct(id=point_id(chunk), payload=payload, vector={
                DENSE: vector, SPARSE: models.SparseVector(indices=indices, values=values)}))
        try:
            await self.client.upsert(collection_name(kb_id), points=points, wait=True)
        except Exception as exc:  # noqa: BLE001
            raise unavailable(exc) from exc

    async def replace_source(self, kb_id: str, source: str, chunks: list[Chunk], dense: list[list[float]]) -> None:
        """Replace what one source (a document or batch) indexed: delete its points, then upsert the new chunks."""
        for chunk in chunks:
            chunk.source = source
        bad = [len(v) for v in dense if len(v) != self.dimension]
        if bad:
            raise ApiError(409, "DIMENSION_MISMATCH", "Conflict",
                           f"The embeddings have {bad[0]} dimensions; the collection has {self.dimension}")
        try:
            try:  # collections created before the source index existed
                await self.client.create_payload_index(collection_name(kb_id), "source",
                                                       field_schema=models.PayloadSchemaType.KEYWORD)
            except Exception:  # noqa: BLE001 - already there
                pass
            await self.client.delete(collection_name(kb_id), points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="source",
                                                                 match=models.MatchValue(value=source))])), wait=True)
        except Exception as exc:  # noqa: BLE001
            raise unavailable(exc) from exc
        if chunks:
            await self.upsert_chunks(kb_id, chunks, dense)

    async def search(self, kb_id: str, text: str, dense: list[float] | None, *, top_k: int, threshold: float,
                     mode: str, iris: list[str] | None) -> list[dict]:
        """mode: hybrid (dense + sparse, RRF), dense (cosine) or sparse (rank-normalised). Scores are in [0, 1]."""
        flt = None
        if iris is not None:
            flt = models.Filter(should=[models.FieldCondition(key=key, match=models.MatchAny(any=iris))
                                        for key in ("entityIri", "classIri")])
        limit = max(PREFETCH_MIN, top_k * PREFETCH_FACTOR)
        indices, values = sparse.encode_query(text)
        sparse_query = models.SparseVector(indices=indices, values=values)
        try:
            if mode == "dense":
                result = await self.client.query_points(collection_name(kb_id), query=dense, using=DENSE,
                                                        query_filter=flt, limit=top_k, with_payload=True,
                                                        score_threshold=threshold or None)
                scored = [(p, p.score) for p in result.points]
            else:
                prefetch = [models.Prefetch(query=sparse_query, using=SPARSE, filter=flt, limit=limit)]
                if mode == "hybrid":
                    prefetch.insert(0, models.Prefetch(query=dense, using=DENSE, filter=flt, limit=limit))
                result = await self.client.query_points(collection_name(kb_id), prefetch=prefetch,
                                                        query=models.FusionQuery(fusion=models.Fusion.RRF),
                                                        limit=top_k, with_payload=True)
                scale = RRF_TOP * len(prefetch)
                scored = [(p, min(1.0, p.score / scale)) for p in result.points]
        except Exception as exc:  # noqa: BLE001
            raise unavailable(exc) from exc
        hits = []
        for point, score in scored:
            if score < threshold:
                continue
            payload = point.payload or {}
            hit = {"chunkId": payload.get("chunkId", str(point.id)), "text": payload.get("text", ""),
                   "score": round(score, 6)}
            if payload.get("documentId"):
                hit["documentId"] = payload["documentId"]
            if payload.get("entityIri"):
                hit["entityIri"] = payload["entityIri"]
            hits.append(hit)
        return hits
