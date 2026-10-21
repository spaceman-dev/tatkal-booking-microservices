"""BR1 - no over-allocation. The pure parts of the rule: all-or-nothing allocation,
seat-count limits and the hold state machine. The concurrent behaviour is tested
against PostgreSQL in tests/integration/test_br1_concurrent_holds.py."""
import pytest

from app.domain.allocation import (
    HoldStatus,
    can_allocate,
    can_transition,
    check_seat_request,
    statuses_that_can_move_to,
)
from app.domain.errors import RuleViolation


@pytest.mark.parametrize("available, requested, expected", [
    (5, 1, True),
    (5, 5, True),
    (5, 6, False),   # never a partial allocation
    (0, 1, False),
    (3, 0, False),
])
def test_br1_allocation_is_all_or_nothing(available, requested, expected):
    assert can_allocate(available, requested) is expected


@pytest.mark.parametrize("seat_count", [0, 7, -1])
def test_br1_rejects_seat_counts_outside_limits(seat_count):
    with pytest.raises(RuleViolation) as err:
        check_seat_request(seat_count)
    assert err.value.rule == "BR1"


@pytest.mark.parametrize("seat_count", [1, 4, 6])
def test_br1_accepts_seat_counts_within_limits(seat_count):
    check_seat_request(seat_count)


def test_hold_state_machine():
    assert can_transition(HoldStatus.HELD, HoldStatus.CONFIRMED)
    assert can_transition(HoldStatus.HELD, HoldStatus.RELEASED)
    assert can_transition(HoldStatus.CONFIRMED, HoldStatus.RELEASED)
    assert not can_transition(HoldStatus.CONFIRMED, HoldStatus.HELD)
    assert not can_transition(HoldStatus.RELEASED, HoldStatus.CONFIRMED)
    assert not can_transition(HoldStatus.RELEASED, HoldStatus.HELD)


def test_sql_guards_are_derived_from_the_state_machine():
    assert statuses_that_can_move_to(HoldStatus.CONFIRMED) == ("HELD",)
    assert statuses_that_can_move_to(HoldStatus.RELEASED) == ("CONFIRMED", "HELD")
    assert statuses_that_can_move_to(HoldStatus.HELD) == ()
