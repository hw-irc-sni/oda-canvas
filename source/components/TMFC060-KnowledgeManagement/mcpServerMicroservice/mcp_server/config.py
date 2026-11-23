"""MCP server settings (environment variables)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The knowledge API's TMFxxx base URL (in the cluster: its Service), and its TMF701 processflow base
    knowledge_api_url: str = "http://localhost:8080/tmfc060-knowledgemanagement/tmf-api/knowledgeManagement/v5"
    request_timeout: float = 60.0
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8080
    log_level: str = "INFO"
