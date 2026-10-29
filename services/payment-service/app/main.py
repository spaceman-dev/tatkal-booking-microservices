import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.clients.gateway import GatewayClient
from app.config import get_settings
from app.core.db import build_engine, create_tables, wait_for_database
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import CorrelationIdMiddleware
from app.core.openapi import install_problem_openapi
from app.repository.tables import metadata
from app.services.idempotency import IdempotencyStore
from app.services.payments import PaymentService

log = logging.getLogger(__name__)

DESCRIPTION = """
Payments and refunds for bookings. The only service that talks to the bank gateway.
A booking is charged at most once (BR4) because of:

* `Idempotency-Key` on `POST /payments` (stored with a request fingerprint and the response)
* `UNIQUE(booking_id)` on the payments table
* the same idempotency key on every retry to the gateway
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)
    engine = build_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    gateway = GatewayClient(settings.gateway_url, settings.gateway_timeout_seconds, settings.gateway_max_attempts,
                            settings.gateway_backoff_base_seconds, settings.gateway_backoff_cap_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        wait_for_database(engine)
        create_tables(engine, metadata)
        log.info("service ready", extra={"port": settings.port, "gateway_url": settings.gateway_url})
        yield
        gateway.close()
        engine.dispose()

    app = FastAPI(title="Payment Service", version="1.0.0", description=DESCRIPTION.strip(), lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    # how long a charge can keep retrying: each attempt's timeout + max backoff, plus 10 s slack
    charge_window = settings.gateway_max_attempts * (settings.gateway_timeout_seconds
                                                     + settings.gateway_backoff_cap_seconds) + 10
    app.state.payments = PaymentService(engine, gateway, charge_window)
    app.state.idempotency = IdempotencyStore(engine, settings.idempotency_lease_seconds,
                                             settings.idempotency_wait_seconds)

    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    install_problem_openapi(app)
    return app


app = create_app()
