from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "payment-service"
    host: str = "0.0.0.0"
    port: int
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    log_level: str = "INFO"

    # bank gateway (our mock in dev)
    gateway_url: str
    gateway_timeout_seconds: float = 2.0
    gateway_max_attempts: int = 3
    gateway_backoff_base_seconds: float = 0.2
    gateway_backoff_cap_seconds: float = 2.0

    # Idempotency-Key handling
    idempotency_lease_seconds: int = 60   # after this an IN_PROGRESS key is considered abandoned
    idempotency_wait_seconds: float = 15  # how long a duplicate waits for the first request to finish


@lru_cache
def get_settings() -> Settings:
    return Settings()
