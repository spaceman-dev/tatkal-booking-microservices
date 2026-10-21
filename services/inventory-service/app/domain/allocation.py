"""BR1 - seats are never over-allocated.

The rule itself is simple (a request is granted only if every requested seat is
available - no partial allocation). What makes it hard is enforcing it under
concurrency; that part lives in the SQL of services/holds.py. This module keeps
the pure parts that can be unit tested without a database.
"""
from app.domain.errors import RuleViolation

MAX_SEATS_PER_HOLD = 6


class HoldStatus:
    HELD = "HELD"
    CONFIRMED = "CONFIRMED"
    RELEASED = "RELEASED"


# HELD -> CONFIRMED (booking paid) | RELEASED (saga compensation)
# CONFIRMED -> RELEASED (ticket cancelled)
HOLD_TRANSITIONS: dict[str, set[str]] = {
    HoldStatus.HELD: {HoldStatus.CONFIRMED, HoldStatus.RELEASED},
    HoldStatus.CONFIRMED: {HoldStatus.RELEASED},
    HoldStatus.RELEASED: set(),
}


def check_seat_request(seat_count: int) -> None:
    if not 1 <= seat_count <= MAX_SEATS_PER_HOLD:
        raise RuleViolation("BR1", "INVALID_SEAT_COUNT",
                            f"A hold must be for 1 to {MAX_SEATS_PER_HOLD} seats, got {seat_count}")


def can_allocate(available_seats: int, requested: int) -> bool:
    """All-or-nothing: either every requested seat fits or the request is refused."""
    return 0 < requested <= available_seats


def can_transition(current: str, new: str) -> bool:
    return new in HOLD_TRANSITIONS.get(current, set())


def statuses_that_can_move_to(target: str) -> tuple[str, ...]:
    """E.g. RELEASED -> ('HELD', 'CONFIRMED'). Used to build the conditional UPDATEs,
    so the SQL can never allow a transition that this table does not."""
    return tuple(sorted(s for s, targets in HOLD_TRANSITIONS.items() if target in targets))
