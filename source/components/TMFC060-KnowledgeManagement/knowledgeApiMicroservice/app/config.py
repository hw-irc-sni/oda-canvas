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
    # A mounted Secret file with the Qdrant API key (the chart generates one); unset means no key
    qdrant_api_key_file: str | None = None

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
    # The TMF701 v5 subset served as the component's processflow API (ingestion processes and their tasks)
    process_oas_path: str = str(Path(__file__).resolve().parent.parent / "api" /
                                "TMF701-ProcessManagement-v5.0.0-subset.oas.yaml")

    @field_validator("keycloak_issuers", "git_allowed_schemes", "mount_roots", "event_callback_allowed_hosts",
                     "event_retry_delays", mode="before")
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

    # Model routes (see app/models.py): 1. a ModelGatewayService CR, 2. agentgateway, 3. an OpenAI-compatible URL
    model_gateway_service: str | None = None  # "name" in the pod's namespace, or "namespace/name"
    mgs_chat_model: str | None = None
    mgs_embedding_model: str | None = None
    agentgateway_url: str | None = None  # for example http://agentgateway.<ns>.svc:8080/v1
    agentgateway_chat_model: str | None = None
    agentgateway_embedding_model: str | None = None
    openai_base_url: str | None = None
    openai_api_key_file: str | None = None  # a mounted Secret file holding the key
    openai_chat_model: str | None = None
    openai_embedding_model: str | None = None
    model_route_interval: int = 300
    model_probe_timeout: float = 5.0
    model_call_timeout: float = 120.0

    # Event delivery to /hub subscribers: callback hosts allowed even when they resolve to private addresses (glob
    # patterns, for example *.svc.cluster.local), the delays before each retry, the call timeout, the outbox poll
    # interval, how long finished deliveries are kept, and an optional base URL to make event hrefs absolute
    event_callback_allowed_hosts: Annotated[list[str], NoDecode] = []
    event_retry_delays: Annotated[list[float], NoDecode] = [10, 60, 300, 900, 1800]
    event_delivery_timeout: float = 10.0
    event_poll_interval: float = 1.0
    event_retention_days: int = 7
    event_href_base: str = ""

    # The internal API of the pipeline workers: its own listener, guarded by the token in INTERNAL_TOKEN_FILE
    internal_host: str = "0.0.0.0"
    internal_port: int = 8081
    internal_token_file: str | None = None

    # Prometheus metrics (the component's metrics management API): their own listener, serving /{component_name}/metrics
    # (0 picks a free port, a negative port turns the listener off)
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 4000

    # Event-driven ingestion from TMF providers (#46): canvas-info (TMFOP005's record of resolved dependentAPIs), an
    # override map (kind or dependency name -> provider API URL, JSON), the externally reachable base of this API for
    # callbacks (the ExposedAPI URL; no registration without it), the re-check interval and the coalescing window
    canvas_info_url: str = "http://info.canvas.svc.cluster.local"
    event_source_urls: str = "{}"
    event_callback_base: str = ""
    event_source_interval: float = 300.0
    event_batch_seconds: float = 5.0
    event_batch_max: int = 100

    # Ingestion tasks: the lease a worker holds (renewed by heartbeats), attempts, and the retry delay per attempt
    task_lease_seconds: int = 300
    task_max_attempts: int = 3
    task_retry_delay: int = 30
    # The largest result a step may hand to the next one (the internal complete call)
    task_result_max_bytes: int = 32 * 1024 * 1024

    # Largest document a knowledge-base accepts, in bytes (50 MiB)
    document_max_bytes: int = 50 * 1024 * 1024

    # Size of the dense vectors of a knowledge-base's Qdrant collection (the embedding model's dimension)
    embedding_dimension: int = 1024

    # Governance: a ruling 'agreed' runs validation and needs it to conform
    governance_require_validation: bool = True

    # Log level of the application loggers (uvicorn configures its own)
    log_level: str = "INFO"

    # Timeout in seconds for each dependency check of /health and /ready
    health_check_timeout: float = 2.0

    def qdrant_api_key(self) -> str | None:
        """The key in QDRANT_API_KEY_FILE; a set but missing or empty file is a configuration error."""
        if not self.qdrant_api_key_file:
            return None
        path = Path(self.qdrant_api_key_file)
        key = path.read_text().strip() if path.is_file() else ""
        if not key:
            raise ValueError(f"QDRANT_API_KEY_FILE {self.qdrant_api_key_file} is missing or empty")
        return key

    @property
    def base_path(self) -> str:
        return f"/{self.component_name}{self.api_path}"

    @property
    def process_base_path(self) -> str:
        """The TMF701 processflow API of the component."""
        return f"/{self.component_name}/tmf-api/process/v5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
