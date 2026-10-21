from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "inventory-service"
    host: str = "0.0.0.0"
    # no defaults for these two, start-up should fail if they're missing
    port: int
    database_url: str
    db_pool_size: int = 10
    db_max_overflow: int = 20
    log_level: str = "INFO"

    # demo trains + quota for the next seed_days days, inserted on start-up
    seed_demo_data: bool = True
    seed_days: int = 7

    # /api/v1/admin/* (fault injection, strategy switch), dev only
    admin_api_enabled: bool = False

    # naive = check-then-update, only for the race demo
    hold_strategy: Literal["atomic", "naive"] = "atomic"
    naive_think_time_ms: int = 150

    # release holds still HELD after hold_ttl_seconds (services/expiry.py)
    hold_expiry_enabled: bool = True
    hold_ttl_seconds: int = 600
    hold_sweep_interval_seconds: float = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
