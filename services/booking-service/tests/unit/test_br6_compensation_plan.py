"""BR6 - if the saga fails, everything it may have done is undone, newest first:
money is refunded in full, then the seats are released."""
from app.domain.booking_rules import BookingStatus as S, Step, compensation_plan


def test_br6_failure_after_payment_refunds_then_releases():
    assert compensation_plan(S.PAYMENT_DONE) == [Step.REFUND_PAYMENT, Step.RELEASE_SEATS]


def test_br6_declined_payment_only_releases_seats():
    assert compensation_plan(S.SEATS_HELD) == [Step.RELEASE_SEATS]


def test_br6_unknown_payment_outcome_is_treated_as_maybe_charged():
    assert compensation_plan(S.SEATS_HELD, payment_outcome_unknown=True) == [Step.REFUND_PAYMENT,
                                                                             Step.RELEASE_SEATS]


def test_br6_nothing_to_undo_before_any_step_succeeded():
    assert compensation_plan(S.PENDING) == []


def test_br6_hold_that_timed_out_may_exist_so_it_is_released():
    assert compensation_plan(S.PENDING, hold_outcome_unknown=True) == [Step.RELEASE_SEATS]
