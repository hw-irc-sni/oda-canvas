"""Settings of the knowledge API, read from environment variables (and an optional .env file)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The ODA Component name (release-component); the API is served under /{component_name}/tmf-api/...
    component_name: str = "tmfc060-knowledgemanagement"
    api_path: str = "/tmf-api/knowledgeManagement/v5"

    # Stores
    store_path: str = "/data/store"
    database_url: str = "postgresql://tmfc060:tmfc060@localhost:5432/tmfc060"
    qdrant_url: str = "http://localhost:6333"

    # Identity (used from #23 on)
    keycloak_issuer: str = ""
    keycloak_jwks_url: str = ""

    # Timeout in seconds for each dependency check of /health and /ready
    health_check_timeout: float = 2.0

    @property
    def base_path(self) -> str:
        return f"/{self.component_name}{self.api_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
