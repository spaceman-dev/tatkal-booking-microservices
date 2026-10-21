import uuid

import httpx
import pytest
from sqlalchemy import create_engine, text

from app.clients.base import ServiceClient
from app.clients.inventory import InventoryClient
from app.clients.payment import PaymentClient
from app.config import get_settings
from app.repository.tables import metadata
from app.services.saga import BookingSaga
from tests.integration.fakes import FakeInventory, FakePayment


@pytest.fixture(scope="session")
def engine():
    """Real PostgreSQL, but a throwaway schema per test run, so tests never touch the
    tables the running service uses (and its background jobs never see test data)."""
    url = get_settings().database_url
    schema = f"test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL for booking-service is not reachable: {exc}")
    eng = create_engine(url, pool_size=10, max_overflow=5, connect_args={"options": f"-csearch_path={schema}"})
    metadata.create_all(eng)
    yield eng
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


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
