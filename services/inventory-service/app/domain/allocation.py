"""BR1 (no over-allocation), the parts that don't need a database.
The concurrent part is the conditional UPDATE in repository/inventory_repo.py."""
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
    # all or nothing, never a partial allocation
    return 0 < requested <= available_seats


def can_transition(current: str, new: str) -> bool:
    return new in HOLD_TRANSITIONS.get(current, set())


def statuses_that_can_move_to(target: str) -> tuple[str, ...]:
    # e.g. RELEASED -> ('CONFIRMED', 'HELD'). The hold UPDATEs filter on this,
    # so the SQL can't allow a move that HOLD_TRANSITIONS doesn't.
    return tuple(sorted(s for s, targets in HOLD_TRANSITIONS.items() if target in targets))
