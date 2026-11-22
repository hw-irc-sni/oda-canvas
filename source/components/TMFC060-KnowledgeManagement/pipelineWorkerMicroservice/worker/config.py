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
