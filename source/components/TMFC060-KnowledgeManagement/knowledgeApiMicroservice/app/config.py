"""Settings of the knowledge API, read from environment variables (and an optional .env file)."""
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The ODA Component name (release-component); the API is served under /{component_name}/tmf-api/...
    component_name: str = "tmfc060-knowledgemanagement"
    api_path: str = "/tmf-api/knowledgeManagement/v5"

    # Stores
    store_path: str = "/data/store"
    database_url: str = "postgresql://tmfc060:tmfc060@localhost:5432/tmfc060"
    qdrant_url: str = "http://localhost:6333"

    # Identity (spike S1, #70): tokens are validated against the in-cluster JWKS, their issuer must be one of
    # keycloak_issuers (comma-separated in the environment), and their audience must contain component_name.
    keycloak_jwks_url: str = ""
    keycloak_issuers: Annotated[list[str], NoDecode] = []
    # The real name of the Canvas system role on this component's client (the chart's security.canvasSystemRole).
    canvas_system_role: str = "canvasRole"
    jwt_leeway_seconds: int = 30
    # Local development only: skip token validation and grant every role. Refused inside Kubernetes.
    auth_enabled: bool = True
    # The TMFxxx OAS; its x-required-roles drive the endpoint guards.
    oas_path: str = str(Path(__file__).resolve().parent.parent / "api" / "TMFxxx-KnowledgeManagement-v5.0.0.oas.yaml")

    @field_validator("keycloak_issuers", mode="before")
    @classmethod
    def _split_issuers(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # How long the API keeps retrying Postgres at startup before it exits (migrations need it)
    db_startup_timeout: float = 120.0

    # Timeout in seconds for each dependency check of /health and /ready
    health_check_timeout: float = 2.0

    @property
    def base_path(self) -> str:
        return f"/{self.component_name}{self.api_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
