from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["normal", "slow", "down", "flaky"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "mock-bank-gateway"
    host: str = "0.0.0.0"
    port: int
    log_level: str = "INFO"

    # Behaviour at start-up. Can be changed at runtime with PUT /gateway/v1/admin/mode.
    gateway_mode: Mode = "normal"
    gateway_latency_ms: int = 80
    gateway_slow_latency_ms: int = 4000
    gateway_failure_rate: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
