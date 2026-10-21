from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything comes from environment variables (or a local .env file).
    PORT and DATABASE_URL have no defaults on purpose: a missing value should
    stop the service at start-up instead of silently using something else."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "inventory-service"
    host: str = "0.0.0.0"
    port: int
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    log_level: str = "INFO"

    # Inserts the demo trains and quota for the next few days on start-up.
    seed_demo_data: bool = True
    seed_days: int = 7

    # /api/v1/admin/* (fault injection, hold strategy switch). Never enable in production.
    admin_api_enabled: bool = False

    # "atomic" is the real implementation. "naive" is the check-then-update
    # version we keep only to demonstrate the race in Challenge 1.
    hold_strategy: Literal["atomic", "naive"] = "atomic"
    naive_think_time_ms: int = 150

    # Safety net: release holds still HELD after this long (see services/expiry.py)
    hold_expiry_enabled: bool = True
    hold_ttl_seconds: int = 600
    hold_sweep_interval_seconds: float = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
