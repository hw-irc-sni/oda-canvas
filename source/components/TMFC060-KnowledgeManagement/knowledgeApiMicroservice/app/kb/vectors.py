"""The Qdrant collection of a vector or hybrid knowledge-base: a named dense vector and a sparse (BM25) vector."""
from qdrant_client import AsyncQdrantClient, models

from ..errors import ApiError

DENSE, SPARSE = "dense", "sparse"


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
