import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import admin_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.core.db import build_engine, wait_for_database
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import CorrelationIdMiddleware
from app.core.openapi import install_problem_openapi
from app.repository.tables import metadata
from app.services.notifications import NotificationService
from app.services.provider import SimulatedProvider

log = logging.getLogger(__name__)

DESCRIPTION = """
Sends SMS / e-mail for booking events (simulated provider) and keeps a delivery log.
A repeated event (same `event_id`) is not sent again, so booking-service's outbox can retry safely.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)
    engine = build_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    provider = SimulatedProvider(settings.provider_failure_rate, settings.provider_latency_ms)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        wait_for_database(engine)
        metadata.create_all(engine)
        log.info("service ready", extra={"port": settings.port,
                                         "provider_failure_rate": settings.provider_failure_rate})
        yield
        engine.dispose()

    app = FastAPI(title="Notification Service", version="1.0.0", description=DESCRIPTION.strip(), lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.provider = provider
    app.state.notifications = NotificationService(engine, provider, settings.claim_lease_seconds,
                                                   settings.duplicate_wait_seconds)

    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    if settings.admin_api_enabled:
        app.include_router(admin_router)
    install_problem_openapi(app)
    return app


app = create_app()
