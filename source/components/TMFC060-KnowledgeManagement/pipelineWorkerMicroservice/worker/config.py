"""Worker settings (environment variables)."""
import socket

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The API's internal port and the shared service token (a mounted Secret key, re-read when it changes)
    internal_api_url: str = "http://localhost:8081"
    internal_token_file: str = "/etc/tmfc060/internal/token"
    # This worker's name in task leases (the pod name by default)
    worker_id: str = socket.gethostname()
    worker_concurrency: int = 2
    worker_poll_interval: float = 2.0
    worker_shutdown_grace: float = 30.0
    worker_request_timeout: float = 30.0
    worker_health_host: str = "0.0.0.0"
    worker_health_port: int = 8082
    log_level: str = "INFO"
    # KM-FX-01 chunking (tokens are estimated as characters / 4)
    chunk_max_tokens: int = 400
    chunk_overlap_tokens: int = 60
    # KM-FX-02: the most ontology terms sent to the model with a chunk (the best-matching ones, plus superclasses)
    extraction_max_terms: int = 150
    # KM-FX-04: skip OWL-RL reasoning above this many triples (ontology + delta)
    reasoning_max_triples: int = 200_000
