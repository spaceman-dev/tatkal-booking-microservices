"""BR4 - a booking is charged at most once; only a CAPTURED payment can be
refunded, in full, and only once."""
import pytest

from app.domain.errors import RuleViolation
from app.domain.payment_rules import (
    NOTHING_TAKEN,
    PaymentStatus,
    can_transition,
    check_charge_amount,
    refund_amount_for,
    statuses_that_can_move_to,
)


def test_br4_refund_is_always_the_full_captured_amount():
    assert refund_amount_for(PaymentStatus.CAPTURED, 579_000) == 579_000


def test_br4_second_refund_is_refused():
    with pytest.raises(RuleViolation) as err:
        refund_amount_for(PaymentStatus.REFUNDED, 579_000)
    assert err.value.code == "ALREADY_REFUNDED"
    assert err.value.rule == "BR4"


@pytest.mark.parametrize("status", [PaymentStatus.PENDING, PaymentStatus.DECLINED, PaymentStatus.FAILED,
                                    PaymentStatus.VOIDED])
def test_br4_only_captured_payments_can_be_refunded(status):
    with pytest.raises(RuleViolation) as err:
        refund_amount_for(status, 579_000)
    assert err.value.code == "PAYMENT_NOT_REFUNDABLE"


def test_br4_refunded_is_terminal():
    assert can_transition(PaymentStatus.CAPTURED, PaymentStatus.REFUNDED)
    for target in (PaymentStatus.CAPTURED, PaymentStatus.REFUNDED, PaymentStatus.PENDING):
        assert not can_transition(PaymentStatus.REFUNDED, target)


def test_br4_a_failed_or_declined_payment_can_never_become_captured():
    assert statuses_that_can_move_to(PaymentStatus.CAPTURED) == (PaymentStatus.PENDING,)
    assert NOTHING_TAKEN == {PaymentStatus.DECLINED, PaymentStatus.FAILED, PaymentStatus.VOIDED}


def test_br4_refunded_comes_from_captured_or_a_late_capture_on_a_voided_payment():
    assert statuses_that_can_move_to(PaymentStatus.REFUNDED) == (PaymentStatus.CAPTURED, PaymentStatus.VOIDED)


@pytest.mark.parametrize("amount", [0, -100, 5_00_000 * 100 + 1])
def test_charge_amount_limits(amount):
    with pytest.raises(RuleViolation):
        check_charge_amount(amount)
