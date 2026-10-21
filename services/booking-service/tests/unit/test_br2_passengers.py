"""BR2: a Tatkal booking has 1 to 4 passengers, each at least 5 years old."""
import pytest

from app.domain.booking_rules import validate_passengers
from app.domain.errors import RuleViolation


def pax(*ages):
    return [{"name": f"P{i}", "age": age, "gender": "F"} for i, age in enumerate(ages)]


@pytest.mark.parametrize("ages", [(30,), (30, 31), (30, 31, 32, 33), (5,)])
def test_br2_accepts_one_to_four_passengers_aged_five_or_more(ages):
    validate_passengers(pax(*ages))


def test_br2_rejects_more_than_four_passengers():
    with pytest.raises(RuleViolation) as err:
        validate_passengers(pax(30, 31, 32, 33, 34))
    assert (err.value.rule, err.value.code) == ("BR2", "TOO_MANY_PASSENGERS")


def test_br2_rejects_empty_passenger_list():
    with pytest.raises(RuleViolation) as err:
        validate_passengers([])
    assert err.value.code == "NO_PASSENGERS"


def test_br2_rejects_children_under_five():
    with pytest.raises(RuleViolation) as err:
        validate_passengers(pax(30, 4))
    assert err.value.code == "PASSENGER_UNDER_MINIMUM_AGE"
    assert "Passenger 2" in err.value.message
