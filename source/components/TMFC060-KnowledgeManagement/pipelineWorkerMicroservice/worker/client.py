"""The API's internal port: the only way the worker reaches the component."""
from pathlib import Path

import httpx


class LeaseLost(Exception):
    """The API says this worker no longer holds the task."""


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
        await self._post(f"/internal/task/{task_id}/complete", {"result": result})

    async def fail(self, task_id: str, error: str, retryable: bool) -> None:
        await self._post(f"/internal/task/{task_id}/fail", {"error": error, "retryable": retryable})

    async def ready(self) -> bool:
        """The internal API answers and accepts the token (a claim without a worker: 400 means authenticated)."""
        try:
            response = await self.http.post("/internal/task/claim", json={}, headers=self.headers())
        except httpx.HTTPError:
            return False
        return response.status_code == 400  # authenticated, and refused only for the missing worker field

    async def aclose(self) -> None:
        await self.http.aclose()
