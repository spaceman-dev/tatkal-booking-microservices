"""Idempotency-Key takeover at the saga level: the retry gets the booking the first request
made and never starts a second saga."""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.errors import AppError
from app.services.idempotency import IdempotencyStore, fingerprint, run_idempotent
from app.services.saga import BookingRequest

pytestmark = pytest.mark.integration


def request(key):
    return BookingRequest(train_no="12951", journey_date=date.today() + timedelta(days=30), travel_class="3A",
                          passengers=[{"name": "Asha Rao", "age": 34, "gender": "F"}], mobile="9876543210",
                          email=None, payment_method="UPI", payer_ref="asha@okmock", idempotency_key=key)


def new_key():
    return f"test-{uuid.uuid4()}"


def bookings_with_key(engine, key):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM bookings WHERE idempotency_key = :k"),
                            {"k": key}).scalar_one()


def insert_booking(engine, status, key):
    booking_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO bookings (booking_id, status, train_no, journey_date, travel_class, passenger_count, "
            "contact_mobile, payment_method, payer_ref, idempotency_key) "
            "VALUES (:b, :s, '12951', CURRENT_DATE + 30, '3A', 1, '9876543210', 'UPI', 'asha@okmock', :k)"),
            {"b": booking_id, "s": status, "k": key})
    return booking_id


def test_key_takeover_returns_existing_booking(engine, saga, fakes):
    inventory, payment = fakes
    store = IdempotencyStore(engine, lease_seconds=0, wait_seconds=2, poll_seconds=0.05)
    key = new_key()
    request_hash = fingerprint("POST", "/api/v1/bookings", {"passengers": 1})

    # first request claims the key and books, then "dies" before storing its response
    assert store.claim(key, request_hash).owner
    _, booked = saga.place_booking(request(key))

    # client retries, the lease is already over (lease_seconds=0) so this one takes the key over
    response = run_idempotent(store, key, request_hash, "/api/v1/bookings",
                              lambda: saga.place_booking(request(key)), resource_field="booking_id")

    assert response.status_code == 201
    assert str(booked["booking_id"]) in response.body.decode()
    assert bookings_with_key(engine, key) == 1
    assert inventory.calls == ["hold", "confirm"] and payment.calls == ["charge"]


def test_retry_of_a_failed_booking_gets_the_same_failure(saga, fakes):
    inventory, payment = fakes
    inventory.confirm_failures = 2
    key = new_key()
    with pytest.raises(AppError) as first:
        saga.place_booking(request(key))
    calls_after_first = (list(inventory.calls), list(payment.calls))

    with pytest.raises(AppError) as again:
        saga.place_booking(request(key))

    assert (again.value.status, again.value.code) == (first.value.status, first.value.code) \
        == (502, "SEAT_CONFIRMATION_FAILED")
    assert again.value.extra["booking_id"] == first.value.extra["booking_id"]
    assert (inventory.calls, payment.calls) == calls_after_first


def test_retry_while_saga_running_gets_409(engine, saga, fakes):
    key = new_key()
    insert_booking(engine, "SEATS_HELD", key)

    with pytest.raises(AppError) as err:
        saga.place_booking(request(key))

    assert (err.value.status, err.value.code) == (409, "IDEMPOTENCY_REQUEST_IN_PROGRESS")
    assert err.value.final is False  # not stored against the key, the client may ask again
    assert err.value.headers == {"Retry-After": "5"}
    assert fakes[0].calls == [] and fakes[1].calls == []


def test_two_requests_racing_with_one_key_start_one_saga(engine, saga, fakes, monkeypatch):
    key = new_key()
    real_lookup = saga.find_by_idempotency_key
    lookups = []

    def lookup(k):  # first lookup misses, and right after it the other request inserts its booking
        lookups.append(k)
        if len(lookups) == 1:
            insert_booking(engine, "PENDING", k)
            return None
        return real_lookup(k)

    monkeypatch.setattr(saga, "find_by_idempotency_key", lookup)
    with pytest.raises(AppError) as err:
        saga.place_booking(request(key))

    assert err.value.code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    assert bookings_with_key(engine, key) == 1
    assert fakes[0].calls == [] and fakes[1].calls == []
