"""The MCP server app: FastMCP over streamable HTTP at /mcp, which needs a bearer token (401 otherwise), and /health."""
import contextlib

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from . import tools
from .api import KnowledgeApi
from .config import Settings

INSTRUCTIONS = ("TMFC060 Knowledge Management: the federated TM Forum ontology (OFO, TIO, AN and extensions) and the "
                "knowledge-bases typed by it. Use search or describe to ground a term before acting on it.")


class RequireBearer:
    """Every MCP request must carry a bearer token; it is forwarded to the knowledge API, which checks it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if not headers.get(b"authorization", b"").lower().startswith(b"bearer "):
                response = JSONResponse({"error": "A bearer token is required"}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, api: KnowledgeApi | None = None) -> Starlette:
    settings = settings or Settings()
    api = api or KnowledgeApi(settings.knowledge_api_url, settings.request_timeout)
    # In the cluster the Host header is the Service name and every call needs a bearer token, so FastMCP's
    # DNS-rebinding protection (meant for servers on localhost) is off.
    mcp = FastMCP("tmfc060-knowledge", instructions=INSTRUCTIONS, stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    tools.register(mcp, api)
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await api.aclose()

    async def health(request):
        return JSONResponse({"status": "up"})

    app = Starlette(routes=[Route("/health", health), Mount("/", app=RequireBearer(mcp_app))], lifespan=lifespan)
    app.state.mcp = mcp
    return app
