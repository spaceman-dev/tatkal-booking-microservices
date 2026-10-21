"""Duplicate / retry cases for publish(), against the real Postgres."""
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.core.errors import AppError
from app.repository.tables import metadata
from app.services.notifications import NotificationService
from app.services.provider import ProviderUnavailable, SimulatedProvider

pytestmark = pytest.mark.integration

DATA = {"pnr": "4521876390", "train_no": "12951", "journey_date": "2026-11-20", "travel_class": "3A",
        "passenger_count": 2, "amount_paise": 579000}


@pytest.fixture(scope="module")
def engine():
    """Real Postgres, but in a throwaway schema so we don't touch the running service's tables."""
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


class RecordingProvider(SimulatedProvider):
    """Counts what really got sent. Set fail[channel] = n to make the next n sends of it fail."""

    def __init__(self, latency_ms=0):
        super().__init__(failure_rate=0, latency_ms=latency_ms)
        self.sent = Counter()
        self.fail = Counter()
        self._lock = threading.Lock()

    def send(self, channel, recipient, message):
        with self._lock:
            if self.fail[channel] > 0:
                self.fail[channel] -= 1
                raise ProviderUnavailable(f"{channel} provider did not accept the message")
        super().send(channel, recipient, message)
        with self._lock:
            self.sent[channel] += 1


def claim_row(engine, event_id, booking_id, channel, lease_ends_in_seconds):
    """Fake a PENDING claim from another delivery. A lease already over means that delivery died."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO notifications (notification_id, event_id, event_type, booking_id, channel, recipient, "
            "message, status, claim_token, locked_until) VALUES (:n, :e, 'BOOKING_CONFIRMED', :b, :c, "
            "'9876543210', 'msg', 'PENDING', 'someone-else', now() + make_interval(secs => :s))"),
            {"n": uuid.uuid4(), "e": event_id, "b": booking_id, "c": channel, "s": lease_ends_in_seconds})


def count(engine, event_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM notifications WHERE event_id = :e"),
                            {"e": event_id}).scalar_one()


def test_concurrent_duplicates_send_once(engine):
    provider = RecordingProvider(latency_ms=50)
    service = NotificationService(engine, provider)
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()
    start_line = Barrier(8)

    def deliver(_):
        start_line.wait()
        return service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", "asha@example.com",
                               DATA)[0]

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(deliver, range(8)))

    assert statuses.count(201) == 1
    assert statuses.count(200) == 7  # the rest waited for the first delivery and got "duplicate"
    assert count(engine, event_id) == 2  # one SMS + one e-mail
    assert provider.sent == {"SMS": 1, "EMAIL": 1}


def test_provider_failure_then_retry(engine):
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


def test_partial_failure_retry_sends_only_missing(engine):
    provider = RecordingProvider()
    provider.fail["EMAIL"] = 1  # SMS goes out, then e-mail fails
    service = NotificationService(engine, provider)
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()

    with pytest.raises(AppError) as err:
        service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", "asha@example.com", DATA)
    status_code, body = service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210",
                                        "asha@example.com", DATA)

    assert err.value.code == "PROVIDER_UNAVAILABLE"
    assert status_code == 201 and not body["duplicate"]
    assert provider.sent == {"SMS": 1, "EMAIL": 1}  # SMS not sent twice
    assert [d["status"] for d in body["deliveries"]] == ["SENT", "SENT"]


def test_in_progress_elsewhere_returns_503(engine):
    provider = RecordingProvider()
    service = NotificationService(engine, provider, wait_seconds=0.3, poll_seconds=0.05)
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()
    claim_row(engine, event_id, booking_id, "SMS", lease_ends_in_seconds=30)

    with pytest.raises(AppError) as err:
        service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", None, DATA)

    # this used to come back as 200 duplicate, and then the outbox stopped retrying
    assert (err.value.status, err.value.code) == (503, "NOTIFICATION_IN_PROGRESS")
    assert provider.sent == {}


def test_dead_claim_is_taken_over(engine):
    provider = RecordingProvider()
    service = NotificationService(engine, provider)
    event_id, booking_id = str(uuid.uuid4()), uuid.uuid4()
    claim_row(engine, event_id, booking_id, "SMS", lease_ends_in_seconds=-1)

    status_code, body = service.publish(event_id, "BOOKING_CONFIRMED", booking_id, "9876543210", None, DATA)

    assert status_code == 201
    assert provider.sent == {"SMS": 1}
    assert [d["status"] for d in body["deliveries"]] == ["SENT"]
