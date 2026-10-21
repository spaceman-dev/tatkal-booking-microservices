"""BR1: 20 threads race for 5 seats through HoldService and real Postgres (no HTTP)."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.services.catalog import CatalogService
from app.services.faults import FaultInjector
from app.services.holds import HoldService

pytestmark = pytest.mark.integration

CAPACITY = 5
REQUESTS = 20


def _race(engine, run_date, strategy):
    CatalogService(engine).open_quota("12627", run_date, "SL", CAPACITY, 90_500)
    holds = HoldService(engine, FaultInjector(), strategy=strategy, naive_think_time_ms=100)
    start_line = Barrier(REQUESTS)

    def attempt(_):
        start_line.wait()  # all threads go at once
        try:
            holds.place_hold(uuid.uuid4(), "12627", run_date, "SL", 1)
            return "ok"
        except AppError as err:
            return err.code

    with ThreadPoolExecutor(max_workers=REQUESTS) as pool:
        outcomes = list(pool.map(attempt, range(REQUESTS)))

    with engine.connect() as conn:
        available = conn.execute(text(
            "SELECT available_seats FROM seat_inventory WHERE train_no='12627' "
            "AND journey_date=:d AND travel_class='SL'"), {"d": run_date}).scalar_one()
        held = conn.execute(text(
            "SELECT count(*) FROM seat_holds h JOIN seat_inventory i USING (inventory_id) "
            "WHERE i.train_no='12627' AND i.journey_date=:d AND i.travel_class='SL' "
            "AND h.status='HELD'"), {"d": run_date}).scalar_one()
    return outcomes, available, held


def test_br1_atomic_hold_never_oversells(engine, fresh_run_date):
    outcomes, available, held = _race(engine, fresh_run_date, "atomic")

    assert outcomes.count("ok") == CAPACITY
    assert outcomes.count("SEATS_UNAVAILABLE") == REQUESTS - CAPACITY
    assert held == CAPACITY
    assert available == 0
    assert held + available == CAPACITY  # counter and hold rows agree


def test_naive_check_then_update_oversells(engine, fresh_run_date):
    # the bug the atomic version fixes. If this starts failing, the naive demo no longer shows the race.
    outcomes, available, held = _race(engine, fresh_run_date, "naive")

    assert outcomes.count("ok") > CAPACITY
    assert held + available != CAPACITY  # lost updates: counter no longer matches rows
