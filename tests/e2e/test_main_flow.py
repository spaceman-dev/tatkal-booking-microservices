"""End-to-end test of the main transaction - booking a Tatkal ticket across
booking, inventory, payment and notification services - plus its failure paths.

Every check goes through the services' public HTTP APIs, exactly as a client would.
Needs the whole stack running:  python scripts/stack.py start
Run with:                       .venv/bin/python -m pytest tests/e2e -v
"""
import time
import uuid

import pytest

from common import (
    BOOKING,
    INVENTORY,
    NOTIFICATION,
    PAYMENT,
    ROOT,
    booking_body,
    fresh_run_date,
    log_lines,
    open_quota,
)

TRAIN, CLASS, BASE_FARE = "12951", "3A", 249_500
FARE_PER_SEAT = 249_500 + 40_000  # base + Tatkal charge (30 % capped at Rs 400 for 3A, BR3)


def post_booking(api, run_date, passengers, payer="asha@okmock", correlation_id=None):
    headers = {"Idempotency-Key": f"e2e-{uuid.uuid4()}"}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    body = booking_body(TRAIN, run_date, CLASS, passengers, payer_ref=payer, email="asha@example.com")
    response = api.post(f"{BOOKING}/api/v1/bookings", json=body, headers=headers)
    return response, response.json()


def quota(api, run_date):
    return api.get(f"{INVENTORY}/api/v1/inventory", params={
        "train_no": TRAIN, "journey_date": run_date.isoformat(), "travel_class": CLASS}).json()


def hold_of(api, booking_id):
    holds = api.get(f"{INVENTORY}/api/v1/holds", params={"booking_id": booking_id}).json()
    return holds[0] if holds else None


def payment_of(api, booking_id):
    payments = api.get(f"{PAYMENT}/api/v1/payments", params={"booking_id": booking_id}).json()
    return payments[0] if payments else None


def wait_for_notification(api, booking_id, event_type, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sent = [n for n in api.get(f"{NOTIFICATION}/api/v1/notifications", params={"booking_id": booking_id}).json()
                if n["event_type"] == event_type and n["status"] == "SENT"]
        if sent:
            return sent
        time.sleep(0.5)
    return []


def test_book_pay_confirm_notify_happy_path(api):
    run_date = fresh_run_date()
    open_quota(api, TRAIN, run_date, CLASS, 5, BASE_FARE)
    correlation_id = f"e2e-{uuid.uuid4().hex[:20]}"

    response, booking = post_booking(api, run_date, [("Asha Rao", 34, "F"), ("Vikram Rao", 36, "M")],
                                     correlation_id=correlation_id)

    assert response.status_code == 201, booking
    assert booking["status"] == "CONFIRMED" and len(booking["pnr"]) == 10
    assert booking["amount_paise"] == 2 * FARE_PER_SEAT                       # BR3: priced by inventory
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert hold_of(api, booking["booking_id"])["status"] == "CONFIRMED"      # inventory agrees
    payment = payment_of(api, booking["booking_id"])
    assert payment["status"] == "CAPTURED" and payment["amount_paise"] == booking["amount_paise"]
    assert quota(api, run_date)["available_seats"] == 3
    messages = wait_for_notification(api, booking["booking_id"], "BOOKING_CONFIRMED")
    assert {m["channel"] for m in messages} == {"SMS", "EMAIL"}
    assert booking["pnr"] in messages[0]["message"]

    steps = api.get(f"{BOOKING}/api/v1/bookings/{booking['booking_id']}/saga").json()
    assert [s["step"] for s in steps] == ["HOLD_SEATS", "CHARGE_PAYMENT", "CONFIRM_SEATS"]

    if (ROOT / "logs").exists():  # started through scripts/stack.py: the id must show up in every hop
        for service in ("booking-service", "inventory-service", "payment-service", "mock-bank-gateway"):
            assert log_lines(service, correlation_id=correlation_id), f"no log line with the id in {service}"


def test_failure_at_step_3_refunds_and_releases(api):
    run_date = fresh_run_date()
    open_quota(api, TRAIN, run_date, CLASS, 5, BASE_FARE)
    api.post(f"{INVENTORY}/api/v1/admin/faults", json={"operation": "confirm_hold", "times": 2})

    response, problem = post_booking(api, run_date, [("Meera Iyer", 29, "F")])

    assert response.status_code == 502 and problem["code"] == "SEAT_CONFIRMATION_FAILED"
    assert response.headers["content-type"].startswith("application/problem+json")
    booking_id = problem["booking_id"]
    booking = api.get(f"{BOOKING}/api/v1/bookings/{booking_id}").json()
    assert booking["status"] == "FAILED" and booking["refund_paise"] == FARE_PER_SEAT
    assert payment_of(api, booking_id)["status"] == "REFUNDED"
    assert hold_of(api, booking_id)["status"] == "RELEASED"
    assert quota(api, run_date)["available_seats"] == 5
    assert wait_for_notification(api, booking_id, "BOOKING_FAILED")


def test_declined_payment_releases_the_seats(api):
    run_date = fresh_run_date()
    open_quota(api, TRAIN, run_date, CLASS, 2, BASE_FARE)

    response, problem = post_booking(api, run_date, [("Kiran Das", 41, "M")], payer="decline@okmock")

    assert response.status_code == 402 and problem["code"] == "PAYMENT_DECLINED"
    assert payment_of(api, problem["booking_id"])["status"] == "DECLINED"
    assert hold_of(api, problem["booking_id"])["status"] == "RELEASED"
    assert quota(api, run_date)["available_seats"] == 2


def test_sold_out_train_is_refused_before_any_payment(api):
    run_date = fresh_run_date()
    open_quota(api, TRAIN, run_date, CLASS, 1, BASE_FARE)
    first, _ = post_booking(api, run_date, [("Rohan Patil", 25, "M")])

    second, problem = post_booking(api, run_date, [("Sneha Patil", 24, "F")])

    assert first.status_code == 201
    assert second.status_code == 409 and problem["code"] == "SEATS_UNAVAILABLE"
    assert payment_of(api, problem["booking_id"]) is None   # nobody was charged for a seat that did not exist


def test_cancelling_a_tatkal_ticket_returns_seats_without_refund(api):
    run_date = fresh_run_date()
    open_quota(api, TRAIN, run_date, CLASS, 3, BASE_FARE)
    _, booking = post_booking(api, run_date, [("Arjun Nair", 45, "M")])

    cancelled = api.post(f"{BOOKING}/api/v1/bookings/{booking['booking_id']}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED" and cancelled.json()["refund_paise"] == 0   # BR7
    assert quota(api, run_date)["available_seats"] == 3
    assert payment_of(api, booking["booking_id"])["status"] == "CAPTURED"                    # money kept
    assert wait_for_notification(api, booking["booking_id"], "BOOKING_CANCELLED")


@pytest.mark.parametrize("passengers, rule", [
    ([("A Five", 30, "M")] * 5, "BR2"),
    ([("Little One", 3, "F")], "BR2"),
])
def test_business_rule_violations_are_rejected_up_front(api, passengers, rule):
    response, problem = post_booking(api, fresh_run_date(), passengers)
    assert response.status_code == 422
    assert problem["rule"] == rule
