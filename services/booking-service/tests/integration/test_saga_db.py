"""BR6 saga tests against the real booking_db, with fake inventory and payment services
so we can make any step fail."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.clients.base import DownstreamRejected
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
    # undone newest first: refund (step 2) before release (step 1)
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


def test_recovery_undoes_saga_stuck_after_payment(engine, saga, fakes):
    # service died right after step 2: hold and payment exist in the fakes, row is left in PAYMENT_DONE
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


def test_release_of_unknown_hold_counts_as_done(engine, saga, fakes):
    # 404 from inventory means there is nothing to give back, so don't keep retrying
    booking_id, unknown_hold = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO bookings (booking_id, status, train_no, journey_date, travel_class, passenger_count, "
            "contact_mobile, payment_method, payer_ref, hold_id, failure_reason, compensation_steps, updated_at) "
            "VALUES (:b, 'COMPENSATING', '12951', CURRENT_DATE + 30, '3A', 1, '9876543210', 'UPI', 'a@ok', :h, "
            "'SAGA_TIMEOUT', 'RELEASE_SEATS', now() - interval '5 minutes')"),
            {"b": booking_id, "h": unknown_hold})

    assert saga.recover(booking_id) == "FAILED"
    assert saga_steps(engine, booking_id)[-1] == ("RELEASE_SEATS", "COMPENSATE", "OK")


def stuck_after_hold(engine, inventory):
    """Booking whose request died after the hold, while the charge was in flight."""
    booking_id, hold_id = uuid.uuid4(), str(uuid.uuid4())
    inventory.holds[hold_id] = {"hold_id": hold_id, "booking_id": str(booking_id), "seat_count": 1,
                                "status": "HELD"}
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO bookings (booking_id, status, train_no, journey_date, travel_class, passenger_count, "
            "contact_mobile, payment_method, payer_ref, amount_paise, hold_id, updated_at) "
            "VALUES (:b, 'SEATS_HELD', '12951', CURRENT_DATE + 30, '3A', 1, '9876543210', 'UPI', 'a@ok', "
            "289500, :h, now() - interval '5 minutes')"),
            {"b": booking_id, "h": hold_id})
    return booking_id, hold_id


def test_void_blocks_late_charge(engine, saga, fakes):
    # The charge is still on its way when the refund step runs. No payment is visible yet,
    # so we void instead of saying "nothing to refund", and the late charge then gets a 409.
    inventory, payment = fakes
    booking_id, hold_id = stuck_after_hold(engine, inventory)

    assert saga.recover(booking_id) == "FAILED"
    with pytest.raises(DownstreamRejected) as late:
        saga.payment.charge(booking_id, 289_500, "UPI", "a@ok")

    assert payment.calls == ["void", "charge"]
    assert late.value.code == "PAYMENT_ALREADY_EXISTS"
    assert payment.for_booking(str(booking_id))["status"] == "VOIDED"
    assert inventory.holds[hold_id]["status"] == "RELEASED"
    with engine.connect() as conn:
        detail = conn.execute(text("SELECT detail FROM saga_log WHERE booking_id = :b AND step = 'REFUND_PAYMENT'"),
                              {"b": booking_id}).scalar_one()
    assert "voided" in detail


def test_refund_of_in_flight_charge_finished_later(engine, saga, fakes):
    inventory, payment = fakes
    booking_id, hold_id = stuck_after_hold(engine, inventory)
    payment_id = str(uuid.uuid4())
    payment.payments[payment_id] = {"payment_id": payment_id, "booking_id": str(booking_id),
                                    "amount_paise": 289_500, "status": "PENDING", "failure_reason": None}
    payment.refund_in_progress = 2  # the call and the client's one retry

    assert saga.recover(booking_id) == "COMPENSATING"
    assert saga.get(booking_id)["compensation_steps"] == "REFUND_PAYMENT"
    assert inventory.holds[hold_id]["status"] == "RELEASED"  # seats are not kept while we wait

    payment.payments[payment_id]["status"] = "CAPTURED"  # the charge went through in the end
    assert saga.recover(booking_id) == "FAILED"
    assert payment.payments[payment_id]["status"] == "REFUNDED"
    assert saga.get(booking_id)["refund_paise"] == 289_500
    assert outbox_events(engine, booking_id) == ["BOOKING_FAILED"]
