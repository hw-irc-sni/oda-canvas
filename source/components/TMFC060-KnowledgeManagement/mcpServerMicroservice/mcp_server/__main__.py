"""python -m mcp_server: serve the MCP server on MCP_HOST:MCP_PORT."""
import logging

import uvicorn

from .app import create_app
from .config import Settings

if __name__ == "__main__":
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(settings), host=settings.mcp_host, port=settings.mcp_port, log_level="warning")
