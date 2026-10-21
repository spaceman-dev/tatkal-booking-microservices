"""BR4 - a booking is charged at most once, and only money that was really taken
(a captured payment) can be refunded - in full, and only once.

"At most once" is enforced in three layers: the Idempotency-Key on the API, a
UNIQUE(booking_id) constraint on payments, and the gateway's own idempotency key.
This module holds the parts of the rule that are pure logic.
"""
from app.domain.errors import RuleViolation

MAX_AMOUNT_PAISE = 5_00_000 * 100  # Rs 5 lakh - far above any Tatkal ticket


class PaymentStatus:
    PENDING = "PENDING"        # sent to the gateway, outcome not known yet
    CAPTURED = "CAPTURED"
    DECLINED = "DECLINED"      # the bank said no; nothing was taken
    FAILED = "FAILED"          # gateway unreachable; nothing was taken
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"          # no charge: never sent (cancelled first) or the bank had none


PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    PaymentStatus.PENDING: {PaymentStatus.CAPTURED, PaymentStatus.DECLINED, PaymentStatus.FAILED,
                            PaymentStatus.VOIDED},
    PaymentStatus.CAPTURED: {PaymentStatus.REFUNDED},
    PaymentStatus.DECLINED: set(),
    PaymentStatus.FAILED: set(),
    PaymentStatus.REFUNDED: set(),
    # Only when a charge reached the bank after the payment had been voided: that
    # money is refunded straight away, so it ends REFUNDED instead of VOIDED.
    PaymentStatus.VOIDED: {PaymentStatus.REFUNDED},
}

# A payment in one of these states never moved money.
NOTHING_TAKEN = {PaymentStatus.DECLINED, PaymentStatus.FAILED, PaymentStatus.VOIDED}


def can_transition(current: str, new: str) -> bool:
    return new in PAYMENT_TRANSITIONS.get(current, set())


def statuses_that_can_move_to(target: str) -> tuple[str, ...]:
    return tuple(sorted(s for s, targets in PAYMENT_TRANSITIONS.items() if target in targets))


def check_charge_amount(amount_paise: int) -> None:
    if not 0 < amount_paise <= MAX_AMOUNT_PAISE:
        raise RuleViolation("BR4", "INVALID_AMOUNT",
                            f"Amount must be between 1 paisa and Rs {MAX_AMOUNT_PAISE // 100}")


def refund_amount_for(status: str, captured_amount_paise: int, late_capture: bool = False) -> int:
    """Refunds are always for the full captured amount, and only for money that was
    really taken: a CAPTURED payment, or a capture that reached the bank after the
    payment had been VOIDED (`late_capture`)."""
    if status == PaymentStatus.REFUNDED:
        raise RuleViolation("BR4", "ALREADY_REFUNDED", "Payment has already been refunded")
    if status == PaymentStatus.CAPTURED or (status == PaymentStatus.VOIDED and late_capture):
        return captured_amount_paise
    raise RuleViolation("BR4", "PAYMENT_NOT_REFUNDABLE",
                        f"Only a CAPTURED payment can be refunded (payment is {status})")
