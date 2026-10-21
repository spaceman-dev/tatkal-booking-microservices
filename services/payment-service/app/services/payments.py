import logging
import uuid
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


def mask_payer(payer_ref: str) -> str:
    name, _, bank = payer_ref.partition("@")
    return f"{name[:2]}***@{bank}" if bank else f"{payer_ref[:2]}***"


def gateway_key(payment_id: UUID) -> str:
    return f"pay-{payment_id}"


class PaymentService:
    def __init__(self, engine: Engine, gateway: GatewayClient):
        self.engine = engine
        self.gateway = gateway

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
            existing = self.find_by_booking(booking_id)
            log.warning("second payment attempt for the same booking refused",
                        extra={"booking_id": str(booking_id), "payment_id": str(existing["payment_id"])})
            raise AppError(409, "PAYMENT_ALREADY_EXISTS", "Booking already has a payment",
                           f"Booking {booking_id} already has payment {existing['payment_id']} "
                           f"({existing['status']}); it will not be charged again",
                           rule="BR4", payment_id=existing["payment_id"],
                           payment_status=existing["status"]) from exc

        log.info("charging payer", extra={"payment_id": str(payment_id), "booking_id": str(booking_id),
                                          "amount_paise": amount_paise, "payer": mask_payer(payer_ref)})
        outcome = self.gateway.charge(gateway_key(payment_id), {
            "amount_paise": amount_paise, "currency": currency, "method": method,
            "payer_ref": payer_ref, "merchant_reference": str(booking_id),
        })
        return self._apply_charge_outcome(payment_id, outcome)

    def _apply_charge_outcome(self, payment_id: UUID, outcome: GatewayOutcome) -> tuple[int, dict]:
        pending = rules.statuses_that_can_move_to(PaymentStatus.CAPTURED)
        with self.engine.begin() as conn:
            repo.record_attempts(conn, payment_id, outcome.attempts)
            if outcome.kind == "captured":
                repo.set_status(conn, payment_id, pending, PaymentStatus.CAPTURED,
                                gateway_charge_id=outcome.body["charge_id"])
            elif outcome.kind == "declined":
                repo.set_status(conn, payment_id, pending, PaymentStatus.DECLINED,
                                gateway_charge_id=outcome.body["charge_id"],
                                failure_reason=outcome.body.get("decline_reason"))
            elif outcome.kind in ("unavailable", "rejected"):
                repo.set_status(conn, payment_id, pending, PaymentStatus.FAILED,
                                failure_reason="GATEWAY_UNAVAILABLE" if outcome.kind == "unavailable"
                                else "GATEWAY_REJECTED")
            else:  # timeout: we do not know - leave PENDING for reconciliation
                repo.set_status(conn, payment_id, (PaymentStatus.PENDING,), PaymentStatus.PENDING,
                                failure_reason="GATEWAY_TIMEOUT")

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
        raise AppError(504, "PAYMENT_OUTCOME_UNKNOWN", "Payment outcome unknown",
                       "The gateway did not answer in time. The payment stays PENDING until it is "
                       "reconciled; refunding it is safe.", payment_id=payment_id, payment_status=status)

    # ---- refund (compensation of step 2) -------------------------------------

    def refund(self, payment_id: UUID, reason: str) -> dict:
        payment = self._get_or_404(payment_id)
        if payment["status"] == PaymentStatus.PENDING:
            payment = self._reconcile(payment)
        if payment["status"] == PaymentStatus.REFUNDED:
            return payment
        if payment["status"] in rules.NOTHING_TAKEN:
            raise AppError(409, "PAYMENT_NOT_REFUNDABLE", "Nothing to refund",
                           f"Payment is {payment['status']}; no money was taken", rule="BR4",
                           payment_id=payment_id, payment_status=payment["status"])

        amount = rules.refund_amount_for(payment["status"], payment["amount_paise"])
        refund = self._get_or_create_refund(payment_id, amount, reason)
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

    def _get_or_create_refund(self, payment_id: UUID, amount: int, reason: str) -> dict:
        with self.engine.begin() as conn:
            existing = repo.get_refund_for_payment(conn, payment_id)
            if existing:
                return existing
        try:
            with self.engine.begin() as conn:
                repo.insert_refund(conn, uuid.uuid4(), payment_id, amount, reason)
        except IntegrityError as exc:
            if not is_unique_violation(exc, "uq_refunds_payment"):
                raise
        with self.engine.connect() as conn:
            return repo.get_refund_for_payment(conn, payment_id)

    def _reconcile(self, payment: dict) -> dict:
        """The charge timed out earlier, so ask the gateway what really happened."""
        payment_id = payment["payment_id"]
        try:
            charge = self.gateway.find_charge(gateway_key(payment_id))
        except httpx.HTTPError as exc:
            raise AppError(503, "GATEWAY_UNAVAILABLE", "Bank gateway unavailable",
                           "Could not reconcile the pending payment; retry later",
                           payment_id=payment_id) from exc
        with self.engine.begin() as conn:
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
