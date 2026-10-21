"""Seat holds: step 1 (hold), step 3 (confirm) and the compensation (release) of
the booking saga. This is where the contended resource lives."""
import logging
import time
import uuid
from datetime import date
from uuid import UUID

from sqlalchemy.engine import Engine

from app.core.errors import AppError
from app.domain import allocation, fares
from app.domain.allocation import HoldStatus
from app.repository import inventory_repo as repo
from app.services.faults import FaultInjector

log = logging.getLogger(__name__)


class HoldService:
    def __init__(self, engine: Engine, faults: FaultInjector, strategy: str = "atomic",
                 naive_think_time_ms: int = 150):
        self.engine = engine
        self.faults = faults
        self.strategy = strategy
        self.naive_think_time_ms = naive_think_time_ms

    # ---- step 1: hold ---------------------------------------------------------

    def place_hold(self, booking_id: UUID, train_no: str, journey_date: date, travel_class: str,
                   seat_count: int) -> tuple[dict, bool]:
        """Returns (hold, created). A repeat call for the same booking returns the
        existing hold with created=False instead of taking seats again."""
        allocation.check_seat_request(seat_count)
        self.faults.maybe_fail("place_hold")

        existing = self.find_by_booking(booking_id)
        if existing:
            log.info("hold already exists for booking, returning it",
                     extra={"booking_id": str(booking_id), "hold_id": str(existing["hold_id"])})
            return existing, False

        hold_id = uuid.uuid4()
        with self.engine.begin() as conn:
            inv = repo.get_inventory(conn, train_no, journey_date, travel_class)
            if inv is None:
                raise AppError(404, "INVENTORY_NOT_FOUND", "No Tatkal quota for this train, date and class",
                               f"No Tatkal quota found for train {train_no} on {journey_date} in {travel_class}")
            quote = fares.quote(travel_class, inv["base_fare_paise"], seat_count)

            # 1) one hold per booking - a concurrent retry for the same booking waits here
            created = repo.claim_hold(conn, hold_id, booking_id, inv["inventory_id"], seat_count,
                                      quote.fare_per_seat_paise, quote.total_amount_paise)
            # 2) then take the seats; if there are not enough, the exception rolls back the hold row too
            if created:
                if self.strategy == "naive":
                    seats_left = self._take_seats_naive(conn, inv, seat_count)
                else:
                    seats_left = self._take_seats_atomic(conn, inv, seat_count)

        if not created:
            existing = self.find_by_booking(booking_id)
            log.info("hold already exists for booking, returning it",
                     extra={"booking_id": str(booking_id), "hold_id": str(existing["hold_id"])})
            return existing, False

        log.info("seats held", extra={"booking_id": str(booking_id), "hold_id": str(hold_id),
                                      "seat_count": seat_count, "seats_left": seats_left,
                                      "strategy": self.strategy})
        return self.get(hold_id), True

    def _take_seats_atomic(self, conn, inv: dict, seat_count: int) -> int:
        seats_left = repo.decrement_if_available(conn, inv["inventory_id"], seat_count)
        if seats_left is None:
            raise self._seats_unavailable(inv, seat_count, repo.read_available(conn, inv["inventory_id"]))
        return seats_left

    def _take_seats_naive(self, conn, inv: dict, seat_count: int) -> int:
        """DEMO ONLY - kept to show *why* the atomic version exists (Challenge 1).

        Read the counter, check it in Python, then write back `value - n`. Two
        requests that read the same value both pass the check and both write; the
        later write overwrites the earlier one (lost update) and the train is oversold.
        The sleep stands in for the work a real handler does between read and write.
        """
        available = repo.read_available(conn, inv["inventory_id"])
        if not allocation.can_allocate(available, seat_count):
            raise self._seats_unavailable(inv, seat_count, available)
        time.sleep(self.naive_think_time_ms / 1000)
        repo.overwrite_available(conn, inv["inventory_id"], available - seat_count)
        return available - seat_count

    @staticmethod
    def _seats_unavailable(inv: dict, requested: int, available: int) -> AppError:
        return AppError(
            409, "SEATS_UNAVAILABLE", "Not enough seats",
            f"Requested {requested} seat(s) in {inv['travel_class']} on train {inv['train_no']} "
            f"for {inv['journey_date']}, only {available} left",
            requested=requested, available=available,
        )

    # ---- step 3: confirm (the saga's pivot) ----------------------------------

    def confirm(self, hold_id: UUID) -> dict:
        self.faults.maybe_fail("confirm_hold")
        with self.engine.begin() as conn:
            changed = repo.set_hold_status(conn, hold_id, allocation.statuses_that_can_move_to(HoldStatus.CONFIRMED),
                                           HoldStatus.CONFIRMED)
        hold = self._get_or_404(hold_id)
        if changed:
            log.info("hold confirmed", extra={"hold_id": str(hold_id), "booking_id": str(hold["booking_id"])})
            return hold
        if hold["status"] == HoldStatus.CONFIRMED:
            return hold  # repeat of a confirm that already happened
        raise AppError(409, "HOLD_NOT_ACTIVE", "Hold is no longer active",
                       f"Hold {hold_id} is {hold['status']} and cannot be confirmed",
                       hold_status=hold["status"])

    # ---- compensation / cancellation: release --------------------------------

    def release(self, hold_id: UUID, reason: str) -> dict:
        """Gives the seats back exactly once, however many times it is called."""
        self.faults.maybe_fail("release_hold")
        with self.engine.begin() as conn:
            changed = repo.set_hold_status(conn, hold_id, allocation.statuses_that_can_move_to(HoldStatus.RELEASED),
                                           HoldStatus.RELEASED, release_reason=reason)
            if changed:
                repo.increment_available(conn, changed["inventory_id"], changed["seat_count"])
        hold = self._get_or_404(hold_id)
        if changed:
            log.info("hold released", extra={"hold_id": str(hold_id), "booking_id": str(hold["booking_id"]),
                                             "seat_count": hold["seat_count"], "reason": reason})
        return hold

    # ---- reads ----------------------------------------------------------------

    def get(self, hold_id: UUID) -> dict | None:
        with self.engine.connect() as conn:
            return repo.get_hold(conn, hold_id)

    def find_by_booking(self, booking_id: UUID) -> dict | None:
        with self.engine.connect() as conn:
            return repo.get_hold_by_booking(conn, booking_id)

    def _get_or_404(self, hold_id: UUID) -> dict:
        hold = self.get(hold_id)
        if hold is None:
            raise AppError(404, "HOLD_NOT_FOUND", "Hold not found", f"No hold with id {hold_id}")
        return hold
