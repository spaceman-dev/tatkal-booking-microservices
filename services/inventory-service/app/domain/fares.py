"""BR3 - the price of a Tatkal seat is always computed here, never taken from the client.

Per-seat fare = base fare + Tatkal charge, where the Tatkal charge is a percentage
of the base fare clamped to a minimum and maximum for the class. The table follows
the published Indian Railways Tatkal charges (2S is 10 %, all other classes 30 %).
All money is integer paise so there is no floating point anywhere.
"""
from dataclasses import dataclass

from app.domain.errors import RuleViolation

# class -> (percent of base fare, minimum Rs, maximum Rs)
TATKAL_CHARGES: dict[str, tuple[int, int, int]] = {
    "2S": (10, 10, 15),
    "SL": (30, 100, 200),
    "CC": (30, 125, 225),
    "3A": (30, 300, 400),
    "2A": (30, 400, 500),
    "EC": (30, 400, 500),
}


@dataclass(frozen=True)
class FareQuote:
    travel_class: str
    seat_count: int
    base_fare_paise: int
    tatkal_charge_paise: int
    fare_per_seat_paise: int
    total_amount_paise: int


def tatkal_charge_paise(travel_class: str, base_fare_paise: int) -> int:
    if travel_class not in TATKAL_CHARGES:
        raise RuleViolation("BR3", "TATKAL_NOT_AVAILABLE", f"Tatkal quota is not offered in class {travel_class}")
    if base_fare_paise <= 0:
        raise RuleViolation("BR3", "INVALID_BASE_FARE", "Base fare must be positive")
    percent, min_rupees, max_rupees = TATKAL_CHARGES[travel_class]
    raw = (base_fare_paise * percent + 50) // 100  # round half up, in paise
    return max(min_rupees * 100, min(raw, max_rupees * 100))


def quote(travel_class: str, base_fare_paise: int, seat_count: int) -> FareQuote:
    charge = tatkal_charge_paise(travel_class, base_fare_paise)
    per_seat = base_fare_paise + charge
    return FareQuote(
        travel_class=travel_class,
        seat_count=seat_count,
        base_fare_paise=base_fare_paise,
        tatkal_charge_paise=charge,
        fare_per_seat_paise=per_seat,
        total_amount_paise=per_seat * seat_count,
    )
