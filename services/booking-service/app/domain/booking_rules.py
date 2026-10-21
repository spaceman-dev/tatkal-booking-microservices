"""Booking rules BR2, BR5, BR6 and BR7. No I/O in here, so the unit tests call these directly."""
from dataclasses import dataclass

from app.domain.errors import RuleViolation


class BookingStatus:
    PENDING = "PENDING"            # booking recorded, nothing reserved yet
    SEATS_HELD = "SEATS_HELD"      # step 1 done: inventory holds the seats
    PAYMENT_DONE = "PAYMENT_DONE"  # step 2 done: money captured
    CONFIRMED = "CONFIRMED"        # step 3 done: seats confirmed, PNR issued
    COMPENSATING = "COMPENSATING"  # a step failed, undoing earlier steps
    FAILED = "FAILED"              # no ticket, nothing held or charged
    CANCELLED = "CANCELLED"        # confirmed ticket cancelled by the passenger


# BR5: allowed status transitions

TRANSITIONS: dict[str, set[str]] = {
    BookingStatus.PENDING: {BookingStatus.SEATS_HELD, BookingStatus.FAILED, BookingStatus.COMPENSATING},
    BookingStatus.SEATS_HELD: {BookingStatus.PAYMENT_DONE, BookingStatus.COMPENSATING},
    BookingStatus.PAYMENT_DONE: {BookingStatus.CONFIRMED, BookingStatus.COMPENSATING},
    BookingStatus.COMPENSATING: {BookingStatus.FAILED},
    BookingStatus.CONFIRMED: {BookingStatus.CANCELLED},
    BookingStatus.FAILED: set(),
    BookingStatus.CANCELLED: set(),
}

IN_FLIGHT = (BookingStatus.PENDING, BookingStatus.SEATS_HELD, BookingStatus.PAYMENT_DONE,
             BookingStatus.COMPENSATING)


def assert_transition(current: str, new: str) -> None:
    if new not in TRANSITIONS.get(current, set()):
        raise RuleViolation("BR5", "INVALID_STATE_TRANSITION", f"A booking cannot move from {current} to {new}")


# BR2: passengers on a Tatkal booking

MAX_TATKAL_PASSENGERS = 4
MIN_AGE_FOR_BERTH = 5


def validate_passengers(passengers: list[dict]) -> None:
    if not passengers:
        raise RuleViolation("BR2", "NO_PASSENGERS", "A booking needs at least one passenger")
    if len(passengers) > MAX_TATKAL_PASSENGERS:
        raise RuleViolation("BR2", "TOO_MANY_PASSENGERS",
                            f"A Tatkal booking can have at most {MAX_TATKAL_PASSENGERS} passengers, "
                            f"got {len(passengers)}")
    for number, passenger in enumerate(passengers, start=1):
        if passenger["age"] < MIN_AGE_FOR_BERTH:
            raise RuleViolation("BR2", "PASSENGER_UNDER_MINIMUM_AGE",
                                f"Passenger {number} is {passenger['age']}. Children under {MIN_AGE_FOR_BERTH} "
                                "travel without a berth and are not booked on a Tatkal ticket")


# BR6: what to undo when the saga fails

class Step:
    HOLD_SEATS = "HOLD_SEATS"
    CHARGE_PAYMENT = "CHARGE_PAYMENT"
    CONFIRM_SEATS = "CONFIRM_SEATS"
    REFUND_PAYMENT = "REFUND_PAYMENT"
    RELEASE_SEATS = "RELEASE_SEATS"
    CANCEL = "CANCEL"


def compensation_plan(reached: str, payment_outcome_unknown: bool = False,
                      hold_outcome_unknown: bool = False) -> list[str]:
    """Undo steps for a saga that failed after reaching `reached`, newest first.

    After a timeout we can't tell if the other service did the work, so the *_unknown
    flags count it as done. Refund and release are both safe to run when it turns out
    there was nothing to undo.
    """
    money_may_be_taken = reached == BookingStatus.PAYMENT_DONE or payment_outcome_unknown
    seats_may_be_held = reached in (BookingStatus.SEATS_HELD, BookingStatus.PAYMENT_DONE) or hold_outcome_unknown
    plan = []
    if money_may_be_taken:
        plan.append(Step.REFUND_PAYMENT)   # undo step 2 first
    if seats_may_be_held:
        plan.append(Step.RELEASE_SEATS)    # then step 1
    return plan


# BR7: cancelling a confirmed Tatkal ticket

@dataclass(frozen=True)
class CancellationOutcome:
    release_seats: bool
    refund_paise: int


def tatkal_cancellation(status: str, amount_paid_paise: int) -> CancellationOutcome:
    """Only CONFIRMED can be cancelled. Seats go back to the quota but a Tatkal ticket gets no refund."""
    if status != BookingStatus.CONFIRMED:
        raise RuleViolation("BR7", "BOOKING_NOT_CANCELLABLE",
                            f"Only a CONFIRMED booking can be cancelled (this one is {status})")
    return CancellationOutcome(release_seats=True, refund_paise=0)
