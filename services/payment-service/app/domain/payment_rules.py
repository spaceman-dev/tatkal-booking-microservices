"""BR4: a booking is charged at most once, and only captured money is refunded, in full
and once. Pure logic here, the DB constraints and idempotency keys do the rest."""
from app.domain.errors import RuleViolation

MAX_AMOUNT_PAISE = 5_00_000 * 100  # Rs 5 lakh, way above any Tatkal fare


class PaymentStatus:
    PENDING = "PENDING"        # sent to the bank, outcome not known yet
    CAPTURED = "CAPTURED"
    DECLINED = "DECLINED"      # bank said no, nothing taken
    FAILED = "FAILED"          # bank unreachable or it rejected the request, nothing taken
    REFUNDED = "REFUNDED"
    VOIDED = "VOIDED"          # no charge: voided before it was sent, or the bank had none


PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    PaymentStatus.PENDING: {PaymentStatus.CAPTURED, PaymentStatus.DECLINED, PaymentStatus.FAILED,
                            PaymentStatus.VOIDED},
    PaymentStatus.CAPTURED: {PaymentStatus.REFUNDED},
    PaymentStatus.DECLINED: set(),
    PaymentStatus.FAILED: set(),
    PaymentStatus.REFUNDED: set(),
    # only for a late capture (charge landed after the void), which we refund straight away
    PaymentStatus.VOIDED: {PaymentStatus.REFUNDED},
}

# no money moved in these states
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
    """BR4: always the full amount, and only for CAPTURED or a VOIDED payment with a late capture."""
    if status == PaymentStatus.REFUNDED:
        raise RuleViolation("BR4", "ALREADY_REFUNDED", "Payment has already been refunded")
    if status == PaymentStatus.CAPTURED or (status == PaymentStatus.VOIDED and late_capture):
        return captured_amount_paise
    raise RuleViolation("BR4", "PAYMENT_NOT_REFUNDABLE",
                        f"Only a CAPTURED payment can be refunded (payment is {status})")
