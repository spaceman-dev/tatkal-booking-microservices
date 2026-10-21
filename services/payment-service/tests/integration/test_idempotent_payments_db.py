"""Challenge 2 / BR4 against the real payment database: duplicates that arrive at
the same moment still produce one payment and one gateway charge."""
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


def test_same_booking_with_different_keys_is_still_charged_once(engine):
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
