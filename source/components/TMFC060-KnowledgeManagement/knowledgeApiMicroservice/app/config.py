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

    @field_validator("keycloak_issuers", "git_allowed_schemes", "mount_roots", mode="before")
    @classmethod
    def _split_issuers(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # Read-only SPARQL (/query): each query runs in its own process, killed at the timeout
    query_timeout_default: int = 30
    query_timeout_max: int = 120
    query_row_cap: int = 10000

    # Ontology sources (federation repositories) synced at startup and every ontology_sync_interval seconds.
    # JSON list: [{"name": "fto", "git": {"url": "...", "ref": "main", "secretRef": "tfo-git"}}, {"name": "x", "mount": {"path": "..."}}]
    ontology_sources: str = "[]"
    ontology_sync_interval: int = 900
    # Git: allowed URL schemes, credential Secrets mounted as <dir>/<secretRef>/{username,password}, clone cache
    git_allowed_schemes: Annotated[list[str], NoDecode] = ["https"]
    git_secrets_dir: str = "/etc/tmfc060/git"
    git_cache_dir: str = "/tmp/tmfc060-git"
    git_timeout: int = 120
    # Module content from a mounted volume must be under one of these directories
    mount_roots: Annotated[list[str], NoDecode] = ["/etc/tmfc060/ontologies"]

    # How long the API keeps retrying Postgres at startup before it exits (migrations need it)
    db_startup_timeout: float = 120.0

    # Federation: the loopback relay port (0 picks a free one), the OAuth2 client Secrets of remote modules
    # (<dir>/<moduleId>/{tokenUrl,clientId,clientSecret}), the per-call timeout and the answer cache TTL (0 = off)
    federation_relay_port: int = 8099
    federation_secrets_dir: str | None = None
    federation_call_timeout: float = 10.0
    federation_cache_ttl: int = 60

    # Size of the dense vectors of a knowledge-base's Qdrant collection (the embedding model's dimension)
    embedding_dimension: int = 1024

    # Governance: a ruling 'agreed' runs validation and needs it to conform
    governance_require_validation: bool = True

    # Log level of the application loggers (uvicorn configures its own)
    log_level: str = "INFO"

    # Timeout in seconds for each dependency check of /health and /ready
    health_check_timeout: float = 2.0

    @property
    def base_path(self) -> str:
        return f"/{self.component_name}{self.api_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
