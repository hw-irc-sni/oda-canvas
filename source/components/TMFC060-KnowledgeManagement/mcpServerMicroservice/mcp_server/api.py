"""The knowledge API, called with the MCP caller's own bearer token."""
import httpx


class ToolError(Exception):
    """An error the tool reports to the MCP client (the REST API's status and message)."""


class KnowledgeApi:
    def __init__(self, base_url: str, timeout: float = 60.0, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    async def call(self, token: str, method: str, path: str, **kwargs):
        headers = {"Authorization": token, **kwargs.pop("headers", {})}
        response = await self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.text
            except ValueError:
                detail = response.text
            raise ToolError(f"{method} {path} answered {response.status_code}: {detail}")
        if response.headers.get("content-type", "").startswith(("application/json", "application/sparql-results")):
            return response.json()
        return response.text

    async def aclose(self) -> None:
        await self.http.aclose()
