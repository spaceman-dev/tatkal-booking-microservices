import pytest

from app.domain.templates import TemplateError, format_rupees, mask_email, mask_mobile, render

CONFIRMED = {"pnr": "4521876390", "train_no": "12951", "journey_date": "2026-11-20", "travel_class": "3A",
             "passenger_count": 2, "amount_paise": 579000}


@pytest.mark.parametrize("paise, text", [
    (579000, "5,790.00"),
    (12345678, "1,23,456.78"),
    (100, "1.00"),
    (99, "0.99"),
    (1000000000, "1,00,00,000.00"),
])
def test_rupees_use_indian_digit_grouping(paise, text):
    assert format_rupees(paise) == text


def test_confirmed_message_contains_pnr_and_amount():
    message = render("BOOKING_CONFIRMED", CONFIRMED)
    assert "PNR 4521876390" in message
    assert "Rs 5,790.00" in message


def test_cancelled_message_states_the_tatkal_no_refund_rule():
    message = render("BOOKING_CANCELLED", {"pnr": "1", "train_no": "12951", "journey_date": "2026-11-20"})
    assert "no refund" in message


def test_missing_template_data_is_rejected():
    with pytest.raises(TemplateError) as err:
        render("BOOKING_CONFIRMED", {**CONFIRMED, "pnr": None})
    assert err.value.code == "TEMPLATE_DATA_MISSING"


def test_unknown_event_type_is_rejected():
    with pytest.raises(TemplateError):
        render("SOMETHING_ELSE", {})


def test_recipients_are_masked_in_logs():
    assert mask_mobile("9876543210") == "98XXXXXX10"
    assert mask_email("asha@example.com") == "a***@example.com"
