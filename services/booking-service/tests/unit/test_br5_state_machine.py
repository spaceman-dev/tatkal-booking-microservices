"""BR5: a booking only moves along the transitions in TRANSITIONS."""
import pytest

from app.domain.booking_rules import TRANSITIONS, BookingStatus as S, assert_transition
from app.domain.errors import RuleViolation

HAPPY_PATH = [S.PENDING, S.SEATS_HELD, S.PAYMENT_DONE, S.CONFIRMED, S.CANCELLED]


def test_br5_happy_path_is_allowed():
    for current, new in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        assert_transition(current, new)


@pytest.mark.parametrize("current", [S.PENDING, S.SEATS_HELD, S.PAYMENT_DONE])
def test_br5_any_in_flight_state_can_start_compensating(current):
    assert_transition(current, S.COMPENSATING)


@pytest.mark.parametrize("current, new", [
    (S.FAILED, S.CONFIRMED),        # a failed booking never comes back
    (S.CANCELLED, S.CONFIRMED),
    (S.CONFIRMED, S.PENDING),
    (S.PENDING, S.CONFIRMED),       # cannot skip hold and payment
    (S.SEATS_HELD, S.CONFIRMED),    # cannot skip payment
    (S.COMPENSATING, S.CONFIRMED),  # once compensating, the only way out is FAILED
    (S.CONFIRMED, S.FAILED),
])
def test_br5_illegal_transitions_are_rejected(current, new):
    with pytest.raises(RuleViolation) as err:
        assert_transition(current, new)
    assert (err.value.rule, err.value.code) == ("BR5", "INVALID_STATE_TRANSITION")


def test_br5_terminal_states_have_no_exits():
    assert TRANSITIONS[S.FAILED] == set()
    assert TRANSITIONS[S.CANCELLED] == set()
