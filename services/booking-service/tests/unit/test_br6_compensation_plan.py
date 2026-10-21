"""BR6: when the saga fails, whatever may have happened is undone newest first
(full refund, then release the seats)."""
from app.domain.booking_rules import BookingStatus as S, Step, compensation_plan


def test_br6_failure_after_payment_refunds_then_releases():
    assert compensation_plan(S.PAYMENT_DONE) == [Step.REFUND_PAYMENT, Step.RELEASE_SEATS]


def test_br6_declined_payment_only_releases_seats():
    assert compensation_plan(S.SEATS_HELD) == [Step.RELEASE_SEATS]


def test_br6_unknown_payment_outcome_is_refunded():
    assert compensation_plan(S.SEATS_HELD, payment_outcome_unknown=True) == [Step.REFUND_PAYMENT,
                                                                             Step.RELEASE_SEATS]


def test_br6_nothing_to_undo_before_any_step_succeeded():
    assert compensation_plan(S.PENDING) == []


def test_br6_timed_out_hold_is_released():
    assert compensation_plan(S.PENDING, hold_outcome_unknown=True) == [Step.RELEASE_SEATS]
