import logging
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.clients.gateway import GatewayClient, GatewayOutcome
from app.core.db import is_unique_violation
from app.core.errors import AppError
from app.domain import payment_rules as rules
from app.domain.payment_rules import PaymentStatus
from app.repository import payments_repo as repo

log = logging.getLogger(__name__)


# for logs: asha@okmock -> as***@okmock
def mask_payer(payer_ref: str) -> str:
    name, _, bank = payer_ref.partition("@")
    return f"{name[:2]}***@{bank}" if bank else f"{payer_ref[:2]}***"


# one gateway key per payment, reused on every retry
def gateway_key(payment_id: UUID) -> str:
    return f"pay-{payment_id}"


class PaymentService:
    def __init__(self, engine: Engine, gateway: GatewayClient, charge_window_seconds: float = 30):
        self.engine = engine
        self.gateway = gateway
        # how long a charge() call may still be sending attempts to the bank
        self.charge_window = timedelta(seconds=charge_window_seconds)

    # ---- charge (saga step 2) -------------------------------------------------

    def charge(self, booking_id: UUID, amount_paise: int, currency: str, method: str,
               payer_ref: str) -> tuple[int, dict]:
        rules.check_charge_amount(amount_paise)
        payment_id = uuid.uuid4()
        try:
            with self.engine.begin() as conn:
                repo.insert_payment(conn, payment_id, booking_id, amount_paise, currency, method, payer_ref)
        except IntegrityError as exc:
            if not is_unique_violation(exc, "uq_payments_booking"):
                raise
            # BR4: booking already has a payment (maybe a VOIDED one from void_for_booking)
            existing = self.find_by_booking(booking_id)
            log.warning("second payment attempt for the same booking refused",
                        extra={"booking_id": str(booking_id), "payment_id": str(existing["payment_id"]),
                               "existing_status": existing["status"]})
            raise AppError(409, "PAYMENT_ALREADY_EXISTS", "Booking already has a payment",
                           f"Booking {booking_id} already has payment {existing['payment_id']} "
                           f"({existing['status']}); it will not be charged again",
                           rule="BR4", payment_id=existing["payment_id"],
                           payment_status=existing["status"]) from exc

        log.info("charging payer", extra={"payment_id": str(payment_id), "booking_id": str(booking_id),
                                          "amount_paise": amount_paise, "payer": mask_payer(payer_ref)})
        # still_wanted is checked before every attempt, so retries stop if the payment gets closed
        outcome = self.gateway.charge(gateway_key(payment_id), {
            "amount_paise": amount_paise, "currency": currency, "method": method,
            "payer_ref": payer_ref, "merchant_reference": str(booking_id),
        }, still_wanted=lambda: self._still_pending(payment_id))
        return self._apply_charge_outcome(payment_id, outcome)

    def _still_pending(self, payment_id: UUID) -> bool:
        with self.engine.connect() as conn:
            return repo.get_status(conn, payment_id) == PaymentStatus.PENDING

    def _apply_charge_outcome(self, payment_id: UUID, outcome: GatewayOutcome) -> tuple[int, dict]:
        pending = rules.statuses_that_can_move_to(PaymentStatus.CAPTURED)
        late_capture = False
        with self.engine.begin() as conn:
            repo.record_attempts(conn, payment_id, outcome.attempts)
            if outcome.kind == "captured":
                moved = repo.set_status(conn, payment_id, pending, PaymentStatus.CAPTURED,
                                        gateway_charge_id=outcome.body["charge_id"])
                if not moved:
                    # payment got voided while this attempt was at the bank (late capture).
                    # keep the charge id, the money is refunded below
                    late_capture = True
                    repo.set_fields(conn, payment_id, gateway_charge_id=outcome.body["charge_id"])
            elif outcome.kind == "declined":
                repo.set_status(conn, payment_id, pending, PaymentStatus.DECLINED,
                                gateway_charge_id=outcome.body["charge_id"],
                                failure_reason=outcome.body.get("decline_reason"))
            elif outcome.kind in ("unavailable", "rejected"):
                repo.set_status(conn, payment_id, pending, PaymentStatus.FAILED,
                                failure_reason="GATEWAY_UNAVAILABLE" if outcome.kind == "unavailable"
                                else "GATEWAY_REJECTED")
            elif outcome.kind == "timeout":  # never heard back, stays PENDING until reconciled
                repo.set_status(conn, payment_id, (PaymentStatus.PENDING,), PaymentStatus.PENDING,
                                failure_reason="GATEWAY_TIMEOUT")
            # "cancelled": payment was closed before the next attempt, nothing to record

        if late_capture:
            self._refund_late_capture(payment_id)

        payment = self.get(payment_id)
        status = payment["status"]
        log.info("payment %s", status.lower(), extra={"payment_id": str(payment_id), "status": status,
                                                      "gateway_attempts": outcome.attempts})
        if status == PaymentStatus.CAPTURED:
            return 201, payment
        if status == PaymentStatus.DECLINED:
            raise AppError(402, "PAYMENT_DECLINED", "Payment declined by the bank",
                           f"Declined: {payment['failure_reason']}", payment_id=payment_id,
                           payment_status=status)
        if status == PaymentStatus.FAILED:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "Bank gateway unavailable",
                           f"The gateway could not be reached after {outcome.attempts} attempt(s); "
                           "no money was taken", payment_id=payment_id, payment_status=status)
        if status in (PaymentStatus.VOIDED, PaymentStatus.REFUNDED):
            raise AppError(409, "PAYMENT_CANCELLED", "Payment was cancelled",
                           "The payment was cancelled while it was being processed; no money is kept.",
                           payment_id=payment_id, payment_status=status)
        raise AppError(504, "PAYMENT_OUTCOME_UNKNOWN", "Payment outcome unknown",
                       "The gateway did not answer in time. The payment stays PENDING until it is "
                       "reconciled; refunding it is safe.", payment_id=payment_id, payment_status=status)

    def _refund_late_capture(self, payment_id: UUID) -> None:
        """Money came in for a payment we had already voided, so give it back."""
        payment = self.get(payment_id)
        log.error("charge captured after the payment was voided - refunding it now",
                  extra={"payment_id": str(payment_id), "booking_id": str(payment["booking_id"]),
                         "gateway_charge_id": payment["gateway_charge_id"]})
        # the refund row is what tells refund() this VOIDED payment still owes money back
        self._get_or_create_refund(payment_id, payment["amount_paise"], "LATE_CAPTURE")
        try:
            self.refund(payment_id, "LATE_CAPTURE")
        except AppError as err:
            log.error("late capture not refunded yet - POST /api/v1/payments/{id}/refund retries it",
                      extra={"payment_id": str(payment_id), "error_code": err.code})

    # ---- refund (compensation of step 2) -------------------------------------

    def refund(self, payment_id: UUID, reason: str) -> dict:
        payment = self._get_or_404(payment_id)
        # PENDING: if the charge may still be running, the caller has to retry later.
        # if it's over, ask the bank what happened before refunding anything
        if payment["status"] == PaymentStatus.PENDING:
            if self._charge_in_flight(payment):
                raise AppError(503, "PAYMENT_IN_PROGRESS", "Payment still being processed",
                               "A charge for this payment may still reach the bank, so its outcome cannot be "
                               "settled yet. Retry later.", headers={"Retry-After": "5"}, payment_id=payment_id)
            payment = self._reconcile(payment)
        if payment["status"] == PaymentStatus.REFUNDED:
            return payment
        # VOIDED with a refund row = late capture whose refund hasn't gone through yet
        late_refund_due = payment["status"] == PaymentStatus.VOIDED and payment["refund"] is not None
        if payment["status"] in rules.NOTHING_TAKEN and not late_refund_due:
            raise AppError(409, "PAYMENT_NOT_REFUNDABLE", "Nothing to refund",
                           f"Payment is {payment['status']}; no money was taken", rule="BR4",
                           payment_id=payment_id, payment_status=payment["status"])

        amount = rules.refund_amount_for(payment["status"], payment["amount_paise"], late_capture=late_refund_due)
        refund = self._get_or_create_refund(payment_id, amount, reason)
        # gateway key comes from our refund id, so retrying a refund can't pay out twice
        outcome = self.gateway.refund(f"refund-{refund['refund_id']}", {
            "charge_id": payment["gateway_charge_id"], "amount_paise": amount})

        if outcome.kind != "processed":
            log.error("refund could not be completed", extra={"payment_id": str(payment_id),
                                                             "outcome": outcome.kind, "error": outcome.detail})
            raise AppError(503, "REFUND_PENDING", "Refund not completed yet",
                           "The gateway did not confirm the refund. Retrying is safe: it reuses the same refund id.",
                           payment_id=payment_id)

        with self.engine.begin() as conn:
            repo.mark_refund_succeeded(conn, refund["refund_id"], outcome.body["refund_id"])
            repo.set_status(conn, payment_id, rules.statuses_that_can_move_to(PaymentStatus.REFUNDED),
                            PaymentStatus.REFUNDED)
        log.info("payment refunded", extra={"payment_id": str(payment_id), "amount_paise": amount,
                                            "reason": reason})
        return self.get(payment_id)

    def _charge_in_flight(self, payment: dict) -> bool:
        """PENDING, no outcome recorded yet and still inside the charge window, so charge()
        may still be retrying. Older than that we assume the charge died."""
        no_outcome_yet = payment["failure_reason"] is None and payment["gateway_attempts"] == 0
        return no_outcome_yet and datetime.now(timezone.utc) - payment["created_at"] < self.charge_window

    def _get_or_create_refund(self, payment_id: UUID, amount: int, reason: str) -> dict:
        with self.engine.begin() as conn:
            existing = repo.get_refund_for_payment(conn, payment_id)
            if existing:
                return existing
        try:
            with self.engine.begin() as conn:
                repo.insert_refund(conn, uuid.uuid4(), payment_id, amount, reason)
        except IntegrityError as exc:
            # another request inserted the refund first: fine, we read that one below
            if not is_unique_violation(exc, "uq_refunds_payment"):
                raise
        with self.engine.connect() as conn:
            return repo.get_refund_for_payment(conn, payment_id)

    def _reconcile(self, payment: dict) -> dict:
        """Charge is over (timed out or its process died), ask the bank what happened."""
        payment_id = payment["payment_id"]
        try:
            charge = self.gateway.find_charge(gateway_key(payment_id))
        except httpx.HTTPError as exc:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "Bank gateway unavailable",
                           "Could not reconcile the pending payment; retry later",
                           payment_id=payment_id) from exc
        with self.engine.begin() as conn:
            # no charge at the bank: it never saw the request, nothing was taken
            if charge is None:
                repo.set_status(conn, payment_id, (PaymentStatus.PENDING,), PaymentStatus.VOIDED,
                                failure_reason="NO_CHARGE_AT_GATEWAY")
            elif charge["status"] == "captured":
                repo.set_status(conn, payment_id, (PaymentStatus.PENDING,), PaymentStatus.CAPTURED,
                                gateway_charge_id=charge["charge_id"])
            else:
                repo.set_status(conn, payment_id, (PaymentStatus.PENDING,), PaymentStatus.DECLINED,
                                gateway_charge_id=charge["charge_id"], failure_reason=charge.get("decline_reason"))
        reconciled = self.get(payment_id)
        log.info("pending payment reconciled", extra={"payment_id": str(payment_id),
                                                     "status": reconciled["status"]})
        return reconciled

    # ---- void (compensation when booking-service sees no payment yet) ----------

    def void_for_booking(self, booking_id: UUID, amount_paise: int, currency: str, method: str,
                         payer_ref: str) -> dict:
        """Stops the booking from being charged later. No payment yet: record a VOIDED one,
        so a charge that turns up afterwards fails on UNIQUE(booking_id) and never reaches
        the bank. Payment already there: return it as is, the caller refunds it."""
        with self.engine.begin() as conn:
            voided = repo.insert_voided_if_absent(conn, uuid.uuid4(), booking_id, amount_paise, currency,
                                                  method, payer_ref)
        payment = self.find_by_booking(booking_id)
        if voided:
            log.info("no payment existed for the booking - recorded as VOIDED so a late charge cannot go through",
                     extra={"booking_id": str(booking_id), "payment_id": str(payment["payment_id"])})
        return payment

    # ---- reads ----------------------------------------------------------------

    def get(self, payment_id: UUID) -> dict | None:
        with self.engine.connect() as conn:
            payment = repo.get_payment(conn, payment_id)
            if payment is None:
                return None
            payment["refund"] = repo.get_refund_for_payment(conn, payment_id)
        return payment

    def find_by_booking(self, booking_id: UUID) -> dict | None:
        with self.engine.connect() as conn:
            payment = repo.get_payment_by_booking(conn, booking_id)
        return self.get(payment["payment_id"]) if payment else None

    def _get_or_404(self, payment_id: UUID) -> dict:
        payment = self.get(payment_id)
        if payment is None:
            raise AppError(404, "PAYMENT_NOT_FOUND", "Payment not found", f"No payment with id {payment_id}")
        return payment
