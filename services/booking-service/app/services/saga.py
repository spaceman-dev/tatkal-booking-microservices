"""The booking saga - orchestrated by booking-service.

    step 1  HOLD_SEATS      inventory-service  POST /holds               undo: RELEASE_SEATS
    step 2  CHARGE_PAYMENT  payment-service    POST /payments            undo: REFUND_PAYMENT
    step 3  CONFIRM_SEATS   inventory-service  POST /holds/{id}/confirm  pivot - no undo needed after it
    step 4  NOTIFY          outbox -> notification-service (async, retried, never undone)

Every state change is committed before the next remote call, so if this process
dies half way the database says exactly how far the booking got, and the recovery
job (services/recovery.py) can finish the compensation.
"""
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.clients.base import DownstreamError, DownstreamRejected
from app.clients.inventory import InventoryClient
from app.clients.payment import PaymentClient
from app.core.context import get_correlation_id
from app.core.db import is_unique_violation
from app.core.errors import AppError
from app.domain.booking_rules import BookingStatus as S
from app.domain.booking_rules import Step, compensation_plan, tatkal_cancellation
from app.domain.errors import RuleViolation
from app.repository import bookings_repo as repo
from app.repository.bookings_repo import ConcurrentUpdate

log = logging.getLogger(__name__)

EXECUTE, COMPENSATE = "EXECUTE", "COMPENSATE"
OK, FAILED = "OK", "FAILED"
NOTHING_TAKEN = {"DECLINED", "FAILED", "VOIDED"}


@dataclass
class BookingRequest:
    train_no: str
    journey_date: date
    travel_class: str
    passengers: list[dict]
    mobile: str
    email: str | None
    payment_method: str
    payer_ref: str
    idempotency_key: str | None = None


def new_pnr() -> str:
    return f"{random.randint(1, 9)}{random.randint(0, 999_999_999):09d}"


def rupees(paise: int | None) -> str:
    return f"{(paise or 0) / 100:,.2f}"


class BookingSaga:
    def __init__(self, engine: Engine, inventory: InventoryClient, payment: PaymentClient):
        self.engine = engine
        self.inventory = inventory
        self.payment = payment

    # ==== place a booking =========================================================

    def place_booking(self, req: BookingRequest) -> tuple[int, dict]:
        booking_id = uuid.uuid4()
        seats = len(req.passengers)
        with self.engine.begin() as conn:
            repo.insert_booking(conn, {
                "booking_id": booking_id, "status": S.PENDING, "train_no": req.train_no,
                "journey_date": req.journey_date, "travel_class": req.travel_class, "passenger_count": seats,
                "contact_mobile": req.mobile, "contact_email": req.email, "payment_method": req.payment_method,
                "payer_ref": req.payer_ref, "idempotency_key": req.idempotency_key,
                "correlation_id": get_correlation_id(),
            }, req.passengers)
        log.info("booking created", extra={"booking_id": str(booking_id), "train_no": req.train_no,
                                           "journey_date": str(req.journey_date), "seats": seats})

        # ---- step 1: hold seats -------------------------------------------------
        try:
            hold = self.inventory.hold(booking_id, req.train_no, req.journey_date, req.travel_class, seats)
        except DownstreamRejected as rej:
            self._record(booking_id, Step.HOLD_SEATS, EXECUTE, FAILED, f"{rej.status} {rej.code}")
            with self.engine.begin() as conn:
                repo.transition(conn, booking_id, S.PENDING, S.FAILED, failure_reason=rej.code)
            status = rej.status if rej.status in (404, 409, 422) else 502
            raise self._error(booking_id, status, rej.code, rej.body.get("title", "Seats could not be held"),
                              rej.body.get("detail"), S.FAILED)
        except DownstreamError as err:
            self._record(booking_id, Step.HOLD_SEATS, EXECUTE, FAILED, err.detail)
            final = self._compensate(booking_id, S.PENDING, "INVENTORY_UNAVAILABLE",
                                     compensation_plan(S.PENDING, hold_outcome_unknown=err.uncertain))
            raise self._error(booking_id, 503, "INVENTORY_UNAVAILABLE", "Seat inventory unavailable",
                              "Could not reach the seat inventory. Nothing was charged; try again with a new "
                              "Idempotency-Key.", final)

        amount = hold["total_amount_paise"]
        with self.engine.begin() as conn:
            repo.transition(conn, booking_id, S.PENDING, S.SEATS_HELD, hold_id=hold["hold_id"], amount_paise=amount)
            repo.log_step(conn, booking_id, Step.HOLD_SEATS, EXECUTE, OK,
                          f"hold {hold['hold_id']}: {seats} seat(s), Rs {rupees(amount)}")

        # ---- step 2: take the payment ----------------------------------------------
        try:
            payment = self.payment.charge(booking_id, amount, req.payment_method, req.payer_ref)
        except DownstreamRejected as rej:
            self._record(booking_id, Step.CHARGE_PAYMENT, EXECUTE, FAILED, f"{rej.status} {rej.code}")
            # A second payment for this booking id should be impossible; if it happens, refund to be safe.
            unknown = rej.code == "PAYMENT_ALREADY_EXISTS"
            final = self._compensate(booking_id, S.SEATS_HELD, rej.code,
                                     compensation_plan(S.SEATS_HELD, payment_outcome_unknown=unknown))
            if rej.status == 402:
                raise self._error(booking_id, 402, "PAYMENT_DECLINED", "Payment declined",
                                  f"{rej.body.get('detail', 'Declined by the bank')}. The seats were released.", final)
            raise self._error(booking_id, 502, rej.code, "Payment rejected", rej.body.get("detail"), final)
        except DownstreamError as err:
            self._record(booking_id, Step.CHARGE_PAYMENT, EXECUTE, FAILED, err.detail)
            # 503 GATEWAY_UNAVAILABLE or "could not connect" = definitely nothing taken.
            nothing_taken = err.code == "GATEWAY_UNAVAILABLE" or (err.status is None and not err.uncertain)
            reason = "PAYMENT_UNAVAILABLE" if nothing_taken else "PAYMENT_OUTCOME_UNKNOWN"
            final = self._compensate(booking_id, S.SEATS_HELD, reason,
                                     compensation_plan(S.SEATS_HELD, payment_outcome_unknown=not nothing_taken))
            if nothing_taken:
                raise self._error(booking_id, 503, "PAYMENT_UNAVAILABLE", "Payment could not be taken",
                                  "The bank could not be reached. Nothing was charged and the seats were released.",
                                  final)
            raise self._error(booking_id, 504, "PAYMENT_OUTCOME_UNKNOWN", "Payment did not complete in time",
                              "The payment could not be confirmed, so the booking was not completed. Any amount "
                              "taken is refunded automatically.", final)

        with self.engine.begin() as conn:
            repo.transition(conn, booking_id, S.SEATS_HELD, S.PAYMENT_DONE, payment_id=payment["payment_id"])
            repo.log_step(conn, booking_id, Step.CHARGE_PAYMENT, EXECUTE, OK,
                          f"payment {payment['payment_id']} captured, Rs {rupees(amount)}")

        # ---- step 3: confirm the seats (pivot) ------------------------------------
        try:
            self.inventory.confirm(hold["hold_id"])
        except (DownstreamRejected, DownstreamError) as exc:
            self._record(booking_id, Step.CONFIRM_SEATS, EXECUTE, FAILED, str(exc))
            final = self._compensate(booking_id, S.PAYMENT_DONE, "SEAT_CONFIRMATION_FAILED",
                                     compensation_plan(S.PAYMENT_DONE))
            detail = ("The seats could not be confirmed. Your payment was refunded in full and the seats released."
                      if final == S.FAILED else
                      "The seats could not be confirmed. The refund will be completed automatically.")
            raise self._error(booking_id, 502, "SEAT_CONFIRMATION_FAILED", "Booking failed after payment",
                              detail, final)

        pnr = self._mark_confirmed(booking_id)
        log.info("booking confirmed", extra={"booking_id": str(booking_id), "pnr": pnr})
        return 201, self.get(booking_id)

    def _mark_confirmed(self, booking_id: UUID) -> str:
        for _ in range(5):
            pnr = new_pnr()
            try:
                with self.engine.begin() as conn:
                    repo.transition(conn, booking_id, S.PAYMENT_DONE, S.CONFIRMED, pnr=pnr)
                    repo.log_step(conn, booking_id, Step.CONFIRM_SEATS, EXECUTE, OK, f"seats confirmed, PNR {pnr}")
                    self._add_event(conn, repo.get_booking(conn, booking_id), "BOOKING_CONFIRMED")
                return pnr
            except IntegrityError as exc:
                if not is_unique_violation(exc, "uq_bookings_pnr"):
                    raise
        raise RuntimeError("could not allocate a unique PNR")

    # ==== compensation (BR6) =======================================================

    def _compensate(self, booking_id: UUID, from_status: str, reason: str, steps: list[str]) -> str:
        with self.engine.begin() as conn:
            repo.transition(conn, booking_id, from_status, S.COMPENSATING, failure_reason=reason,
                            compensation_steps=",".join(steps) or None)
        log.warning("saga step failed, compensating", extra={"booking_id": str(booking_id), "reason": reason,
                                                             "from_status": from_status, "steps": steps})
        return self._run_compensations(booking_id, steps)

    def _run_compensations(self, booking_id: UUID, steps: list[str]) -> str:
        """Runs every step (newest first) even if an earlier one fails, so seats are not
        kept hostage by a payment outage. Failed steps are left for the recovery job."""
        booking = self.get(booking_id)
        remaining = []
        for step in steps:
            try:
                detail = self._refund(booking) if step == Step.REFUND_PAYMENT else self._release(booking)
                self._record(booking_id, step, COMPENSATE, OK, detail)
            except (DownstreamError, DownstreamRejected) as exc:
                self._record(booking_id, step, COMPENSATE, FAILED, str(exc))
                remaining.append(step)

        with self.engine.begin() as conn:
            if remaining:
                repo.set_fields(conn, booking_id, compensation_steps=",".join(remaining))
                log.error("compensation incomplete, will be retried",
                          extra={"booking_id": str(booking_id), "remaining": remaining})
                return S.COMPENSATING
            try:
                repo.transition(conn, booking_id, S.COMPENSATING, S.FAILED, compensation_steps=None)
            except ConcurrentUpdate:
                # The recovery job (or the request) finished it first; all steps are idempotent.
                return repo.get_booking(conn, booking_id)["status"]
            current = repo.get_booking(conn, booking_id)
            if current["refund_paise"]:
                self._add_event(conn, current, "BOOKING_FAILED")
        log.info("compensation finished, booking failed cleanly", extra={"booking_id": str(booking_id)})
        return S.FAILED

    def _refund(self, booking: dict) -> str:
        payment = self.payment.find_payment(booking["booking_id"])
        if payment is None:
            return "no payment was made"
        if payment["status"] in NOTHING_TAKEN:
            return f"nothing to refund (payment {payment['status']})"
        if payment["status"] != "REFUNDED":
            try:
                payment = self.payment.refund(payment["payment_id"], booking["failure_reason"] or "BOOKING_FAILED")
            except DownstreamRejected as rej:
                if rej.code == "PAYMENT_NOT_REFUNDABLE":
                    return "nothing to refund"
                raise
        if payment["status"] != "REFUNDED":
            return f"nothing to refund (payment {payment['status']})"
        with self.engine.begin() as conn:
            repo.set_fields(conn, booking["booking_id"], refund_paise=payment["amount_paise"],
                            payment_id=payment["payment_id"])
        return f"refunded Rs {rupees(payment['amount_paise'])} on payment {payment['payment_id']}"

    def _release(self, booking: dict) -> str:
        hold_id = booking["hold_id"]
        if hold_id is None:
            hold = self.inventory.find_hold(booking["booking_id"])
            if hold is None:
                return "no seats were held"
            hold_id = hold["hold_id"]
        released = self.inventory.release(hold_id, booking["failure_reason"] or "BOOKING_FAILED")
        return f"released {released['seat_count']} seat(s) of hold {hold_id}"

    # ==== recovery (called by the background job) ==================================

    def recover(self, booking_id: UUID) -> str | None:
        booking = self.get(booking_id)
        if booking is None:
            return None
        status = booking["status"]
        if status == S.COMPENSATING:
            steps = booking["compensation_steps"].split(",") if booking["compensation_steps"] else []
            log.info("retrying unfinished compensation", extra={"booking_id": str(booking_id), "steps": steps})
            return self._run_compensations(booking_id, steps)
        if status in (S.PENDING, S.SEATS_HELD, S.PAYMENT_DONE):
            # The request that was driving this saga died. Undo whatever may have happened.
            plan = compensation_plan(status, payment_outcome_unknown=status == S.SEATS_HELD,
                                     hold_outcome_unknown=status == S.PENDING)
            log.warning("saga stopped half way, compensating", extra={"booking_id": str(booking_id),
                                                                      "stuck_in": status})
            return self._compensate(booking_id, status, "SAGA_TIMEOUT", plan)
        return status

    # ==== cancellation (BR7) =======================================================

    def cancel(self, booking_id: UUID) -> dict:
        booking = self.get(booking_id)
        if booking is None:
            raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found", f"No booking with id {booking_id}")
        if booking["status"] == S.CANCELLED:
            return booking  # cancelling twice is harmless
        try:
            outcome = tatkal_cancellation(booking["status"], booking["amount_paise"] or 0)
        except RuleViolation as rv:
            raise AppError(409, rv.code, "Booking cannot be cancelled", rv.message, rule=rv.rule,
                           booking_status=booking["status"]) from rv

        if outcome.release_seats:
            try:
                self.inventory.release(booking["hold_id"], "BOOKING_CANCELLED")
            except (DownstreamError, DownstreamRejected) as exc:
                raise AppError(503, "INVENTORY_UNAVAILABLE", "Cancellation not completed",
                               "The seats could not be released, so the booking is still CONFIRMED. "
                               "Cancelling again is safe.") from exc

        with self.engine.begin() as conn:
            try:
                repo.transition(conn, booking_id, S.CONFIRMED, S.CANCELLED, refund_paise=outcome.refund_paise)
            except ConcurrentUpdate:
                pass  # a parallel cancel got there first
            else:
                repo.log_step(conn, booking_id, Step.CANCEL, EXECUTE, OK,
                              f"seats released, refund Rs {rupees(outcome.refund_paise)} (Tatkal rule)")
                self._add_event(conn, repo.get_booking(conn, booking_id), "BOOKING_CANCELLED")
        log.info("booking cancelled", extra={"booking_id": str(booking_id)})
        return self.get(booking_id)

    # ==== helpers ===================================================================

    def get(self, booking_id: UUID) -> dict | None:
        with self.engine.connect() as conn:
            return repo.get_booking(conn, booking_id)

    def list_bookings(self, status: str | None, train_no: str | None, journey_date: date | None,
                      limit: int) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.list_bookings(conn, status, train_no, journey_date, limit)

    def saga_log(self, booking_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.saga_steps(conn, booking_id)

    def _record(self, booking_id: UUID, step: str, action: str, outcome: str, detail: str | None) -> None:
        with self.engine.begin() as conn:
            repo.log_step(conn, booking_id, step, action, outcome, detail)

    @staticmethod
    def _add_event(conn, booking: dict, event_type: str) -> None:
        amount = booking["refund_paise"] if event_type == "BOOKING_FAILED" else booking["amount_paise"]
        event_id = uuid.uuid4()
        payload = {
            "event_id": str(event_id),
            "event_type": event_type,
            "booking_id": str(booking["booking_id"]),
            "recipient": {"mobile": booking["contact_mobile"], "email": booking["contact_email"]},
            "data": {
                "pnr": booking["pnr"], "train_no": booking["train_no"],
                "journey_date": booking["journey_date"].isoformat(), "travel_class": booking["travel_class"],
                "passenger_count": booking["passenger_count"], "amount_paise": amount,
                "booking_ref": str(booking["booking_id"])[:8].upper(),
            },
        }
        repo.add_outbox_event(conn, event_id, booking["booking_id"], event_type, payload, get_correlation_id())

    @staticmethod
    def _error(booking_id: UUID, status: int, code: str, title: str, detail: str | None,
               booking_status: str) -> AppError:
        return AppError(status, code, title, detail, booking_id=booking_id, booking_status=booking_status)
