import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.clients.base import ServiceClient
from app.clients.inventory import InventoryClient
from app.clients.notification import NotificationClient
from app.clients.payment import PaymentClient
from app.config import get_settings
from app.core.db import build_engine, create_tables, wait_for_database
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import CorrelationIdMiddleware
from app.core.openapi import install_problem_openapi
from app.repository.tables import metadata
from app.services.idempotency import IdempotencyStore
from app.services.outbox import OutboxDispatcher
from app.services.recovery import SagaRecovery
from app.services.saga import BookingSaga

log = logging.getLogger(__name__)

DESCRIPTION = """
Owns bookings and runs the booking saga:

1. hold seats (inventory-service), undone by a release
2. take payment (payment-service), undone by a refund
3. confirm seats (inventory-service), the pivot step
4. notify (notification-service), async through the outbox

`POST /bookings` needs an `Idempotency-Key`. Business rules here: BR2 (passengers),
BR5 (state machine), BR6 (compensation) and BR7 (Tatkal cancellation).
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)
    engine = build_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)

    inventory_http = ServiceClient("inventory-service", settings.inventory_service_url,
                                   settings.inventory_timeout_seconds, settings.downstream_retries)
    payment_http = ServiceClient("payment-service", settings.payment_service_url,
                                 settings.payment_timeout_seconds, settings.downstream_retries)
    notification_http = ServiceClient("notification-service", settings.notification_service_url,
                                      settings.notification_timeout_seconds, retries=0)

    saga = BookingSaga(engine, InventoryClient(inventory_http), PaymentClient(payment_http))
    dispatcher = OutboxDispatcher(engine, NotificationClient(notification_http), settings.outbox_poll_seconds)
    recovery = SagaRecovery(engine, saga, settings.recovery_interval_seconds, settings.compensation_retry_seconds,
                            settings.saga_stuck_after_seconds, settings.idempotency_ttl_hours)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        wait_for_database(engine)
        create_tables(engine, metadata)
        if settings.background_jobs_enabled:
            dispatcher.start()
            recovery.start()
        log.info("service ready", extra={
            "port": settings.port, "inventory": settings.inventory_service_url,
            "payment": settings.payment_service_url, "notification": settings.notification_service_url,
            "background_jobs": settings.background_jobs_enabled})
        yield
        dispatcher.stop()
        recovery.stop()
        for client in (inventory_http, payment_http, notification_http):
            client.close()
        engine.dispose()

    app = FastAPI(title="Booking Service", version="1.0.0", description=DESCRIPTION.strip(), lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.saga = saga
    app.state.dispatcher = dispatcher
    app.state.recovery = recovery
    app.state.idempotency = IdempotencyStore(engine, settings.idempotency_lease_seconds,
                                             settings.idempotency_wait_seconds)

    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    install_problem_openapi(app)
    return app


app = create_app()
