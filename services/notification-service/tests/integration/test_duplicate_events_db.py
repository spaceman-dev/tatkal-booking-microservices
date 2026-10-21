"""The idempotent-consumer guarantee against the real database: the same event
delivered many times (even at once) produces exactly one message per channel."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.core.errors import AppError
from app.repository.tables import metadata
from app.services.notifications import NotificationService
from app.services.provider import SimulatedProvider

pytestmark = pytest.mark.integration

DATA = {"pnr": "4521876390", "train_no": "12951", "journey_date": "2026-11-20", "travel_class": "3A",
        "passenger_count": 2, "amount_paise": 579000}


@pytest.fixture(scope="module")
def engine():
    """Real PostgreSQL, but a throwaway schema per test run, so tests never touch the
    tables the running service uses."""
    url = get_settings().database_url
    schema = f"test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL for notification-service is not reachable: {exc}")
    eng = create_engine(url, pool_size=15, max_overflow=5, connect_args={"options": f"-csearch_path={schema}"})
    metadata.create_all(eng)
    yield eng
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


def count(engine, event_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM notifications WHERE event_id = :e"),
                            {"e": event_id}).scalar_one()


def test_duplicate_events_send_one_message_per_channel(engine):
    service = NotificationService(engine, SimulatedProvider(failure_rate=0, latency_ms=50))
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()
    start_line = Barrier(8)

    def deliver(_):
        start_line.wait()
        return service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", "asha@example.com",
                               DATA)[0]

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(deliver, range(8)))

    assert statuses.count(201) == 1
    assert statuses.count(200) == 7
    assert count(engine, event_id) == 2  # one SMS + one e-mail


def test_provider_failure_records_nothing_so_a_retry_can_send(engine):
    provider = SimulatedProvider(failure_rate=1.0, latency_ms=0)
    service = NotificationService(engine, provider)
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()

    with pytest.raises(AppError) as err:
        service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", None, DATA)
    assert err.value.status == 503
    assert count(engine, event_id) == 0

    provider.failure_rate = 0.0
    assert service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", None, DATA)[0] == 201
    assert count(engine, event_id) == 1
