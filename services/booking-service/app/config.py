from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "booking-service"
    host: str = "0.0.0.0"
    port: int
    database_url: str
    db_pool_size: int = 15
    db_max_overflow: int = 25
    log_level: str = "INFO"

    # other services, HTTP only
    inventory_service_url: str
    payment_service_url: str
    notification_service_url: str
    inventory_timeout_seconds: float = 3.0
    payment_timeout_seconds: float = 10.0   # payment-service retries the bank itself (~7 s worst case)
    notification_timeout_seconds: float = 3.0
    downstream_retries: int = 1             # extra attempts for idempotent calls (hold/confirm/release/refund)

    # Idempotency-Key on POST /bookings. The lease has to outlast the slowest saga.
    idempotency_lease_seconds: int = 60
    idempotency_wait_seconds: float = 20
    idempotency_ttl_hours: int = 24

    # Background jobs
    background_jobs_enabled: bool = True
    outbox_poll_seconds: float = 2.0
    recovery_interval_seconds: float = 10.0
    compensation_retry_seconds: int = 20   # COMPENSATING bookings untouched this long are retried
    saga_stuck_after_seconds: int = 60     # in-flight bookings untouched this long are compensated


@lru_cache
def get_settings() -> Settings:
    return Settings()
