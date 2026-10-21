from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "notification-service"
    host: str = "0.0.0.0"
    port: int
    database_url: str
    db_pool_size: int = 5
    db_max_overflow: int = 10
    log_level: str = "INFO"

    # Simulated SMS/e-mail provider (we never call a real one)
    provider_failure_rate: float = 0.0
    provider_latency_ms: int = 30

    # Duplicate deliveries: how long a claimed message stays reserved for the delivery
    # sending it, and how long a duplicate waits for that delivery to finish
    claim_lease_seconds: float = 30
    duplicate_wait_seconds: float = 2.0

    admin_api_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
