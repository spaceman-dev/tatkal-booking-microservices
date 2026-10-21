import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest
from sqlalchemy import text

from app.clients.gateway import GatewayClient
from app.core.errors import AppError
from app.services.idempotency import IdempotencyStore, fingerprint, run_idempotent
from app.services.payments import PaymentService

pytestmark = pytest.mark.integration

PARALLEL = 10


def slow_gateway():
    calls = []
    lock = threading.Lock()

    def handler(request):
        with lock:
            calls.append(request.headers["Idempotency-Key"])
        time.sleep(0.2)  # keep the first request busy while the duplicates arrive
        return httpx.Response(200, json={"charge_id": f"ch_{len(calls)}", "status": "captured"})

    client = GatewayClient("http://gw", 2, 1, 0.1, 0.1, transport=httpx.MockTransport(handler))
    return client, calls


def count_payments(engine, booking_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM payments WHERE booking_id = :b"),
                            {"b": booking_id}).scalar_one()


def test_parallel_requests_with_the_same_key_charge_once(engine):
    gateway, gateway_calls = slow_gateway()
    service = PaymentService(engine, gateway)
    store = IdempotencyStore(engine, lease_seconds=60, wait_seconds=10, poll_seconds=0.05)
    booking_id = uuid.uuid4()
    key = f"test-{uuid.uuid4()}"
    body = {"booking_id": str(booking_id), "amount_paise": 579000, "currency": "INR", "method": "UPI",
            "payer_ref": "asha@okmock"}
    request_hash = fingerprint("POST", "/api/v1/payments", body)
    start_line = Barrier(PARALLEL)

    def send(_):
        start_line.wait()
        response = run_idempotent(
            store, key, request_hash, "/api/v1/payments",
            lambda: service.charge(booking_id, 579000, "INR", "UPI", "asha@okmock"),
            resource_field="payment_id")
        return response.status_code, response.body, response.headers.get("Idempotent-Replayed")

    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(send, range(PARALLEL)))

    assert {status for status, _, _ in results} == {201}
    assert len({body for _, body, _ in results}) == 1           # byte-identical responses
    assert sum(1 for *_, replayed in results if replayed) == PARALLEL - 1
    assert count_payments(engine, booking_id) == 1
    assert len(gateway_calls) == 1


def test_same_booking_different_keys_charged_once(engine):
    gateway, gateway_calls = slow_gateway()
    service = PaymentService(engine, gateway)
    booking_id = uuid.uuid4()
    start_line = Barrier(5)

    def send(_):
        start_line.wait()
        try:
            return service.charge(booking_id, 579000, "INR", "UPI", "asha@okmock")[0]
        except AppError as err:
            return err.code

    with ThreadPoolExecutor(5) as pool:
        outcomes = list(pool.map(send, range(5)))

    assert outcomes.count(201) == 1
    assert outcomes.count("PAYMENT_ALREADY_EXISTS") == 4
    assert count_payments(engine, booking_id) == 1
    assert len(gateway_calls) == 1


def test_expired_owner_cannot_overwrite_new_owner(engine):
    store = IdempotencyStore(engine, lease_seconds=0, wait_seconds=2, poll_seconds=0.05)
    key, request_hash = f"test-{uuid.uuid4()}", "a" * 64
    slow = store.claim(key, request_hash)   # its lease runs out at once...
    taken = store.claim(key, request_hash)  # ...so the next request takes the key over
    assert slow.owner and taken.owner and slow.token != taken.token

    assert store.complete(key, slow.token, 201, '{"payment_id":"slow"}', None) is False
    assert store.complete(key, taken.token, 201, '{"payment_id":"new-owner"}', None) is True
    store.release(key, slow.token)  # must not delete the stored result either

    replay = store.claim(key, request_hash)
    assert not replay.owner and replay.body_text == '{"payment_id":"new-owner"}'


def test_non_final_error_is_not_stored(engine):
    store = IdempotencyStore(engine, lease_seconds=60, wait_seconds=2, poll_seconds=0.05)
    key, request_hash = f"test-{uuid.uuid4()}", "b" * 64
    answers = [AppError(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS", "Still being processed",
                        headers={"Retry-After": "5"}, final=False)]

    def action():
        if answers:
            raise answers.pop()
        return 201, {"payment_id": "p-1"}

    first = run_idempotent(store, key, request_hash, "/api/v1/payments", action, resource_field="payment_id")
    second = run_idempotent(store, key, request_hash, "/api/v1/payments", action, resource_field="payment_id")

    assert first.status_code == 409 and first.headers["Retry-After"] == "5"
    assert second.status_code == 201 and "Idempotent-Replayed" not in second.headers
