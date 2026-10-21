import httpx
import pytest
from sqlalchemy import text

from app.clients.base import ServiceClient
from app.clients.inventory import InventoryClient
from app.clients.payment import PaymentClient
from app.config import get_settings
from app.core.db import build_engine
from app.repository.tables import metadata
from app.services.saga import BookingSaga
from tests.integration.fakes import FakeInventory, FakePayment


@pytest.fixture(scope="session")
def engine():
    eng = build_engine(get_settings().database_url, pool_size=10, max_overflow=5)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL for booking-service is not reachable: {exc}")
    metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def fakes():
    return FakeInventory(), FakePayment()


@pytest.fixture
def saga(engine, fakes):
    inventory, payment = fakes
    inventory_client = ServiceClient("inventory-service", "http://inventory", 2, retries=1,
                                     transport=httpx.MockTransport(inventory.handler))
    payment_client = ServiceClient("payment-service", "http://payment", 2, retries=1,
                                   transport=httpx.MockTransport(payment.handler))
    return BookingSaga(engine, InventoryClient(inventory_client), PaymentClient(payment_client))
