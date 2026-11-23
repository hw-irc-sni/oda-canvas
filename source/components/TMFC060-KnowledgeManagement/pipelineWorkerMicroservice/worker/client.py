"""The API's internal port: the only way the worker reaches the component."""
from pathlib import Path

import httpx


class LeaseLost(Exception):
    """The API says this worker no longer holds the task."""


class ModelUnavailable(Exception):
    """No model route, or the model failed: worth retrying later."""


class ModelRefused(Exception):
    """The model rejected the request (4xx): retrying the same request won't help."""


class IndexRefused(Exception):
    """The API refused to index the chunks (for example an embedding dimension mismatch): retrying won't help."""


class ResultTooLarge(Exception):
    """The API refused a task result over TASK_RESULT_MAX_BYTES."""


class Token:
    def __init__(self, path: str):
        self.path = Path(path)
        self.mtime: int | None = None
        self.value = ""

    def current(self) -> str:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            return ""
        if mtime != self.mtime:
            self.mtime, self.value = mtime, self.path.read_text().strip()
        return self.value


class InternalClient:
    def __init__(self, base_url: str, token_file: str, worker: str, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.worker = worker
        self.token = Token(token_file)
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token.current()}"}

    async def _post(self, path: str, body: dict) -> httpx.Response:
        response = await self.http.post(path, json={"worker": self.worker, **body}, headers=self.headers())
        if response.status_code == 409:
            raise LeaseLost(path)
        response.raise_for_status()
        return response

    async def claim(self) -> dict | None:
        response = await self._post("/internal/task/claim", {})
        return None if response.status_code == 204 else response.json()

    async def heartbeat(self, task_id: str) -> None:
        await self._post(f"/internal/task/{task_id}/heartbeat", {})

    async def complete(self, task_id: str, result: dict) -> None:
        try:
            await self._post(f"/internal/task/{task_id}/complete", {"result": result})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 413:
                raise ResultTooLarge(exc.response.json().get("message", "the result is too large")) from exc
            raise

    async def fail(self, task_id: str, error: str, retryable: bool) -> None:
        await self._post(f"/internal/task/{task_id}/fail", {"error": error, "retryable": retryable})

    async def document(self, kb_id: str, doc_id: str) -> tuple[dict, bytes]:
        """A document's metadata and content (KM-FX-01)."""
        meta = await self.http.get(f"/internal/knowledge/{kb_id}/document/{doc_id}", headers=self.headers())
        meta.raise_for_status()
        content = await self.http.get(f"/internal/knowledge/{kb_id}/document/{doc_id}/content", headers=self.headers())
        content.raise_for_status()
        return meta.json(), content.content

    async def chat(self, messages: list[dict], **options) -> str:
        """A chat completion through the API's model route (KM-FX-02). Raises ModelUnavailable or ModelRefused."""
        response = await self.http.post("/internal/models/chat", json={"messages": messages, **options},
                                        headers=self.headers())
        if response.status_code == 400:
            raise ModelRefused(response.json().get("message", "refused"))
        if response.status_code in (502, 503):
            raise ModelUnavailable(response.json().get("message", "unavailable"))
        response.raise_for_status()
        return response.json()["content"]

    async def query(self, kb_id: str, sparql: str) -> dict:
        """A read-only SPARQL SELECT over the knowledge-base's dataset (SPARQL JSON results)."""
        response = await self.http.post(f"/internal/knowledge/{kb_id}/query", content=sparql.encode(),
                                        headers=self.headers())
        response.raise_for_status()
        return response.json()

    async def ontology(self, kb_id: str, part: str = "ontology") -> str:
        """The Turtle of the KB's conformsTo closure: its ontology modules, or its shapes modules (KM-FX-04)."""
        response = await self.http.get(f"/internal/knowledge/{kb_id}/ontology", params={"part": part},
                                       headers=self.headers())
        response.raise_for_status()
        return response.text

    async def propose(self, kb_id: str, source: str | None, process_id: str | None, proposals: list[dict]) -> list:
        """Record proposed changes, replacing this source's undecided ones (KM-FX-04)."""
        response = await self.http.post(f"/internal/knowledge/{kb_id}/proposal", headers=self.headers(),
                                        json={"source": source, "processId": process_id, "proposals": proposals})
        response.raise_for_status()
        return response.json()["ids"]

    async def knowledge(self, kb_id: str) -> dict:
        response = await self.http.get(f"/internal/knowledge/{kb_id}", headers=self.headers())
        response.raise_for_status()
        return response.json()

    async def write_source_graph(self, kb_id: str, source: str, data: bytes, task_id: str | None = None) -> None:
        """Replace what this source contributed to the KB graph (N-Triples, sent as Turtle)."""
        params = {"source": source, **({"task": task_id} if task_id else {})}
        response = await self.http.put(f"/internal/knowledge/{kb_id}/graph", params=params, content=data,
                                       headers={**self.headers(), "Content-Type": "text/turtle"})
        response.raise_for_status()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.http.post("/internal/models/embeddings", json={"input": texts}, headers=self.headers())
        if response.status_code == 400:
            raise ModelRefused(response.json().get("message", "refused"))
        if response.status_code in (502, 503):
            raise ModelUnavailable(response.json().get("message", "unavailable"))
        response.raise_for_status()
        return response.json()["embeddings"]

    async def index_chunks(self, kb_id: str, source: str, chunks: list[dict], dense: list[list[float]]) -> int:
        """Replace this source's chunks in the KB's vector store (the API adds the sparse vectors)."""
        response = await self.http.put(f"/internal/knowledge/{kb_id}/chunks", params={"source": source},
                                       json={"chunks": chunks, "dense": dense}, headers=self.headers())
        if response.status_code == 409:
            raise IndexRefused(response.json().get("message", "refused"))
        response.raise_for_status()
        return response.json()["indexed"]

    async def store_profiles(self, kb_id: str, profiles: list[dict]) -> int:
        """Store entities' applicability profiles for recommendation (KM-FX-06)."""
        response = await self.http.put(f"/internal/knowledge/{kb_id}/profiles", json={"profiles": profiles},
                                       headers=self.headers())
        response.raise_for_status()
        return response.json()["stored"]

    async def ready(self) -> bool:
        """The internal API answers and accepts the token (a claim without a worker: 400 means authenticated)."""
        try:
            response = await self.http.post("/internal/task/claim", json={}, headers=self.headers())
        except httpx.HTTPError:
            return False
        return response.status_code == 400  # authenticated, and refused only for the missing worker field

    async def aclose(self) -> None:
        await self.http.aclose()
