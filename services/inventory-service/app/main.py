import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.core.db import build_engine, wait_for_database
from app.core.errors import install_error_handlers
from app.core.health import router as health_router
from app.core.logging import setup_logging
from app.core.middleware import CorrelationIdMiddleware
from app.core.openapi import install_problem_openapi
from app.repository.tables import metadata
from app.services.catalog import CatalogService
from app.services.faults import FaultInjector
from app.services.holds import HoldService
from app.services.seed import seed_demo_data

log = logging.getLogger(__name__)

DESCRIPTION = """
Owns the train catalogue, fares and the **Tatkal seat quota**, and is the only
service that can hold, confirm or release seats.

* Contended resource: `seat_inventory.available_seats` for a train/date/class.
* BR1 (no over-allocation) is enforced by a single conditional `UPDATE`.
* BR3 (server-side Tatkal fare) is computed in `app/domain/fares.py`.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)
    engine = build_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        wait_for_database(engine)
        metadata.create_all(engine)
        if settings.seed_demo_data:
            seed_demo_data(engine, settings.seed_days)
        log.info("service ready", extra={"port": settings.port, "hold_strategy": settings.hold_strategy,
                                         "admin_api": settings.admin_api_enabled})
        yield
        engine.dispose()

    app = FastAPI(
        title="Inventory Service",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "catalogue", "description": "Trains, availability and fares"},
            {"name": "inventory", "description": "Tatkal quota per train/date/class"},
            {"name": "holds", "description": "Seat holds used by the booking saga"},
            {"name": "health"},
        ],
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.faults = FaultInjector()
    app.state.holds = HoldService(engine, app.state.faults, settings.hold_strategy, settings.naive_think_time_ms)
    app.state.catalog = CatalogService(engine)

    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router)
    if settings.admin_api_enabled:
        app.include_router(admin_router)
    install_problem_openapi(app)
    return app


app = create_app()
