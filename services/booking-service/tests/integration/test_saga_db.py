"""Challenge 3 / BR6 - the saga against the real booking database, with fake
inventory and payment services so failures can be forced exactly where we want."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.services.saga import BookingRequest

pytestmark = pytest.mark.integration


def request():
    return BookingRequest(train_no="12951", journey_date=date.today() + timedelta(days=30), travel_class="3A",
                          passengers=[{"name": "Asha Rao", "age": 34, "gender": "F"},
                                      {"name": "Vikram Rao", "age": 36, "gender": "M"}],
                          mobile="9876543210", email=None, payment_method="UPI", payer_ref="asha@okmock")


def saga_steps(engine, booking_id):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT step, action, outcome FROM saga_log WHERE booking_id = :b ORDER BY id"),
                            {"b": booking_id}).all()
    return [tuple(r) for r in rows]


def outbox_events(engine, booking_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT event_type FROM outbox WHERE booking_id = :b"),
                            {"b": booking_id}).scalars().all()


def test_happy_path_confirms_and_queues_notification(engine, saga, fakes):
    inventory, payment = fakes
    status_code, booking = saga.place_booking(request())

    assert status_code == 201
    assert booking["status"] == "CONFIRMED" and len(booking["pnr"]) == 10
    assert inventory.calls == ["hold", "confirm"] and payment.calls == ["charge"]
    assert outbox_events(engine, booking["booking_id"]) == ["BOOKING_CONFIRMED"]


def test_br6_failure_at_step_3_refunds_then_releases(engine, saga, fakes):
    inventory, payment = fakes
    inventory.confirm_failures = 2  # the first try and the client's retry both fail

    with pytest.raises(AppError) as err:
        saga.place_booking(request())

    assert err.value.status == 502 and err.value.code == "SEAT_CONFIRMATION_FAILED"
    booking_id = err.value.extra["booking_id"]
    booking = saga.get(booking_id)
    assert booking["status"] == "FAILED"
    assert booking["refund_paise"] == booking["amount_paise"] == 579_000
    # compensations ran newest-first: refund (undo step 2) before release (undo step 1)
    assert payment.calls == ["charge", "refund"]
    assert inventory.calls == ["hold", "confirm", "confirm", "release"]
    assert saga_steps(engine, booking_id) == [
        ("HOLD_SEATS", "EXECUTE", "OK"),
        ("CHARGE_PAYMENT", "EXECUTE", "OK"),
        ("CONFIRM_SEATS", "EXECUTE", "FAILED"),
        ("REFUND_PAYMENT", "COMPENSATE", "OK"),
        ("RELEASE_SEATS", "COMPENSATE", "OK"),
    ]
    assert outbox_events(engine, booking_id) == ["BOOKING_FAILED"]


def test_failed_compensation_is_finished_by_recovery(engine, saga, fakes):
    inventory, _ = fakes
    inventory.confirm_failures = 2
    inventory.release_failures = 2  # inventory also refuses the release (both attempts)

    with pytest.raises(AppError) as err:
        saga.place_booking(request())
    booking_id = err.value.extra["booking_id"]
    stuck = saga.get(booking_id)
    assert stuck["status"] == "COMPENSATING"
    assert stuck["compensation_steps"] == "RELEASE_SEATS"  # the refund already succeeded

    # what the background job does on its next round
    assert saga.recover(booking_id) == "FAILED"
    assert inventory.calls[-1] == "release"
    assert all(h["status"] == "RELEASED" for h in inventory.holds.values())


def test_saga_abandoned_after_payment_is_compensated_by_recovery(engine, saga, fakes):
    """Simulates booking-service dying right after the payment step: the fakes hold
    what steps 1 and 2 did, and the booking row is left in PAYMENT_DONE."""
    inventory, payment = fakes
    booking_id, hold_id, payment_id = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
    inventory.holds[hold_id] = {"hold_id": hold_id, "booking_id": str(booking_id), "seat_count": 1,
                                "status": "HELD"}
    payment.payments[payment_id] = {"payment_id": payment_id, "booking_id": str(booking_id),
                                    "amount_paise": 289_500, "status": "CAPTURED"}
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO bookings (booking_id, status, train_no, journey_date, travel_class, passenger_count, "
            "contact_mobile, payment_method, payer_ref, amount_paise, hold_id, updated_at) "
            "VALUES (:b, 'PAYMENT_DONE', '12951', CURRENT_DATE + 30, '3A', 1, '9876543210', 'UPI', 'a@ok', "
            "289500, :h, now() - interval '5 minutes')"),
            {"b": booking_id, "h": hold_id})

    assert saga.recover(booking_id) == "FAILED"
    assert payment.payments[payment_id]["status"] == "REFUNDED"
    assert inventory.holds[hold_id]["status"] == "RELEASED"
    assert saga.get(booking_id)["failure_reason"] == "SAGA_TIMEOUT"
