"""BR3 - Tatkal fare is computed on the server: base fare + class-specific
Tatkal charge (percentage of base fare, clamped to a min/max)."""
import pytest

from app.domain.errors import RuleViolation
from app.domain.fares import quote, tatkal_charge_paise


def rupees(value: float) -> int:
    return int(round(value * 100))


def test_br3_charge_is_raised_to_the_class_minimum():
    # Sleeper, base Rs 200 -> 30% is Rs 60, below the Rs 100 minimum
    assert tatkal_charge_paise("SL", rupees(200)) == rupees(100)


def test_br3_charge_is_percentage_when_inside_the_band():
    # Sleeper, base Rs 500 -> 30% is Rs 150, inside [100, 200]
    assert tatkal_charge_paise("SL", rupees(500)) == rupees(150)


def test_br3_charge_is_capped_at_the_class_maximum():
    # 3A, base Rs 2495 -> 30% is Rs 748.50, capped at Rs 400
    assert tatkal_charge_paise("3A", rupees(2495)) == rupees(400)


def test_br3_second_sitting_uses_ten_percent():
    assert tatkal_charge_paise("2S", rupees(120)) == rupees(12)
    assert tatkal_charge_paise("2S", rupees(50)) == rupees(10)   # min
    assert tatkal_charge_paise("2S", rupees(400)) == rupees(15)  # max


def test_br3_percentage_rounds_half_up_in_paise():
    # SL base 50005 paise -> 30% = 15001.5 paise -> 15002
    assert tatkal_charge_paise("SL", 50_005) == 15_002


def test_br3_quote_multiplies_per_seat_fare_by_seat_count():
    q = quote("3A", rupees(2495), 4)
    assert q.fare_per_seat_paise == rupees(2495 + 400)
    assert q.total_amount_paise == 4 * rupees(2895)


@pytest.mark.parametrize("travel_class", ["1A", "3E", "XX"])
def test_br3_rejects_classes_without_tatkal(travel_class):
    with pytest.raises(RuleViolation) as err:
        tatkal_charge_paise(travel_class, rupees(1000))
    assert err.value.rule == "BR3"
    assert err.value.code == "TATKAL_NOT_AVAILABLE"


def test_br3_rejects_non_positive_base_fare():
    with pytest.raises(RuleViolation):
        tatkal_charge_paise("SL", 0)
