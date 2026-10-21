"""BR7: only a CONFIRMED booking can be cancelled. The seats go back, nothing is refunded."""
import pytest

from app.domain.booking_rules import BookingStatus as S, tatkal_cancellation
from app.domain.errors import RuleViolation


def test_br7_confirmed_ticket_releases_seats_without_refund():
    outcome = tatkal_cancellation(S.CONFIRMED, amount_paid_paise=579_000)
    assert outcome.release_seats is True
    assert outcome.refund_paise == 0


@pytest.mark.parametrize("status", [S.PENDING, S.SEATS_HELD, S.PAYMENT_DONE, S.COMPENSATING, S.FAILED])
def test_br7_only_confirmed_bookings_can_be_cancelled(status):
    with pytest.raises(RuleViolation) as err:
        tatkal_cancellation(status, 579_000)
    assert (err.value.rule, err.value.code) == ("BR7", "BOOKING_NOT_CANCELLABLE")
