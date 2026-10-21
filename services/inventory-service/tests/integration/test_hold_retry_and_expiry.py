"""Hold placement is idempotent per booking even when two requests for the same
booking overlap, and the expiry safety net only ever releases stale HELD holds."""
import threading
import time
import uuid

import pytest
from sqlalchemy import text

from app.repository import inventory_repo
from app.services.catalog import CatalogService
from app.services.expiry import HoldExpiry
from app.services.faults import FaultInjector
from app.services.holds import HoldService

pytestmark = pytest.mark.integration


def available(engine, run_date):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT available_seats FROM seat_inventory WHERE train_no='12627' AND journey_date=:d "
            "AND travel_class='SL'"), {"d": run_date}).scalar_one()


def test_overlapping_retry_for_the_same_booking_gets_the_same_hold(engine, fresh_run_date, monkeypatch):
    """The booking-service client timed out and retried while the first attempt was
    still inside its transaction. The retry must return the first attempt's hold,
    not 409 SEATS_UNAVAILABLE for seats that this same booking already took."""
    CatalogService(engine).open_quota("12627", fresh_run_date, "SL", 2, 90_500)
    holds = HoldService(engine, FaultInjector())
    booking_id = uuid.uuid4()

    first_inside = threading.Event()
    original = inventory_repo.decrement_if_available

    def slow_first_decrement(conn, inventory_id, seat_count):
        result = original(conn, inventory_id, seat_count)
        if not first_inside.is_set():
            first_inside.set()
            time.sleep(0.5)  # attempt 1 holds its locks, uncommitted, while the retry arrives
        return result

    monkeypatch.setattr(inventory_repo, "decrement_if_available", slow_first_decrement)
    results = {}

    def attempt(name):
        results[name] = holds.place_hold(booking_id, "12627", fresh_run_date, "SL", 2)

    first = threading.Thread(target=attempt, args=("first",))
    first.start()
    first_inside.wait(5)
    retry = threading.Thread(target=attempt, args=("retry",))
    retry.start()
    first.join()
    retry.join()

    (hold_1, created_1), (hold_2, created_2) = results["first"], results["retry"]
    assert hold_1["hold_id"] == hold_2["hold_id"]
    assert (created_1, created_2) == (True, False)
    assert available(engine, fresh_run_date) == 0


def test_expiry_releases_only_stale_held_holds_and_is_safe_to_run_twice(engine, fresh_run_date):
    CatalogService(engine).open_quota("12627", fresh_run_date, "SL", 5, 90_500)
    holds = HoldService(engine, FaultInjector())
    stale, _ = holds.place_hold(uuid.uuid4(), "12627", fresh_run_date, "SL", 2)
    paid, _ = holds.place_hold(uuid.uuid4(), "12627", fresh_run_date, "SL", 1)
    fresh, _ = holds.place_hold(uuid.uuid4(), "12627", fresh_run_date, "SL", 1)
    holds.confirm(paid["hold_id"])
    with engine.begin() as conn:  # make the first two look 20 minutes old
        conn.execute(text("UPDATE seat_holds SET created_at = now() - interval '20 minutes' "
                          "WHERE hold_id = ANY(:ids)"), {"ids": [stale["hold_id"], paid["hold_id"]]})
    assert available(engine, fresh_run_date) == 1

    expiry = HoldExpiry(engine, ttl_seconds=600, interval_seconds=30)
    assert expiry.run_once() == 1
    assert expiry.run_once() == 0  # second run finds nothing more to do

    assert holds.get(stale["hold_id"])["status"] == "RELEASED"
    assert holds.get(stale["hold_id"])["release_reason"] == "HOLD_EXPIRED"
    assert holds.get(paid["hold_id"])["status"] == "CONFIRMED"   # paid seats are never released
    assert holds.get(fresh["hold_id"])["status"] == "HELD"       # younger than the TTL
    assert available(engine, fresh_run_date) == 3                # 1 + the 2 stale seats, exactly once
