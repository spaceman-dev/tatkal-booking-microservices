"""A compensation can run while the charge it is undoing is still being processed
(booking-service gave up waiting, payment-service did not). None of these
interleavings may end with money taken and no refund."""
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

from app.clients.gateway import GatewayClient
from app.core.errors import AppError
from app.services.payments import PaymentService

pytestmark = pytest.mark.integration

AMOUNT = 289_500


class FakeBank:
    """Captures charges unless told otherwise, processes refunds, records every call."""

    def __init__(self, on_charge=None):
        self.charges, self.refunds, self.calls = {}, [], []
        self.on_charge = on_charge  # called with the attempt number; may raise or have side effects

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gateway/v1/charges" and request.method == "POST":
            self.calls.append("charge")
            if self.on_charge:
                self.on_charge(len(self.calls), request)
            charge = {"charge_id": f"ch_{len(self.charges) + 1}", "status": "captured",
                      "amount_paise": json.loads(request.content)["amount_paise"]}
            self.charges[request.headers["Idempotency-Key"]] = charge
            return httpx.Response(200, json=charge)
        if path == "/gateway/v1/charges" and request.method == "GET":
            found = self.charges.get(request.url.params["idempotency_key"])
            return httpx.Response(200, json=[found] if found else [])
        if path == "/gateway/v1/refunds":
            self.calls.append("refund")
            self.refunds.append(json.loads(request.content))
            return httpx.Response(200, json={"refund_id": f"rf_{len(self.refunds)}", "status": "processed"})
        return httpx.Response(404)


def service_with(engine, bank, sleep=lambda s: None, charge_window=30):
    gateway = GatewayClient("http://bank", 1, 3, 0.01, 0.01, transport=httpx.MockTransport(bank.handler),
                            sleep=sleep)
    return PaymentService(engine, gateway, charge_window_seconds=charge_window)


def status_of(engine, booking_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT status, failure_reason FROM payments WHERE booking_id = :b"),
                            {"b": booking_id}).one()


def test_void_before_the_charge_arrives_fences_the_late_charge(engine):
    bank = FakeBank()
    service = service_with(engine, bank)
    booking_id = uuid.uuid4()

    voided = service.void_for_booking(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")
    with pytest.raises(AppError) as late:
        service.charge(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")

    assert voided["status"] == "VOIDED" and voided["failure_reason"] == "VOIDED_BEFORE_CHARGE"
    assert late.value.code == "PAYMENT_ALREADY_EXISTS"
    assert bank.calls == []  # the late charge never reached the bank


def test_void_returns_an_existing_payment_unchanged(engine):
    bank = FakeBank()
    service = service_with(engine, bank)
    booking_id = uuid.uuid4()
    status_code, paid = service.charge(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")

    again = service.void_for_booking(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")

    assert status_code == 201
    assert again["payment_id"] == paid["payment_id"] and again["status"] == "CAPTURED"


def test_refund_during_a_retry_backoff_waits_instead_of_voiding(engine):
    """The reviewer's case: attempt 1 fails, the compensation's refund arrives during
    the backoff, attempt 2 captures. Previously the refund voided the payment and the
    capture was lost; now it is told to retry, and the later refund returns the money."""
    bank = FakeBank(on_charge=lambda attempt, request: _refuse_first(attempt, request))
    during_backoff = []
    holder = {}

    def sleep(_seconds):
        if not during_backoff:
            with pytest.raises(AppError) as err:
                holder["service"].refund(holder["payment_id"](), "PAYMENT_OUTCOME_UNKNOWN")
            during_backoff.append(err.value.code)

    service = service_with(engine, bank, sleep=sleep)
    holder["service"] = service
    booking_id = uuid.uuid4()
    holder["payment_id"] = lambda: service.find_by_booking(booking_id)["payment_id"]

    status_code, payment = service.charge(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")
    refunded = service.refund(payment["payment_id"], "PAYMENT_OUTCOME_UNKNOWN")

    assert during_backoff == ["PAYMENT_IN_PROGRESS"]
    assert status_code == 201 and payment["status"] == "CAPTURED"
    assert refunded["status"] == "REFUNDED" and refunded["refund"]["status"] == "SUCCEEDED"
    assert bank.calls == ["charge", "charge", "refund"]


def _refuse_first(attempt, request):
    if attempt == 1:
        raise httpx.ConnectError("bank refused the connection", request=request)


def test_a_charge_that_died_long_ago_is_reconciled_with_the_bank(engine):
    bank = FakeBank()
    service = service_with(engine, bank, charge_window=30)
    booking_id, payment_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:  # PENDING, no outcome recorded, 5 minutes old: its process died
        conn.execute(text(
            "INSERT INTO payments (payment_id, booking_id, amount_paise, currency, method, payer_ref, status, "
            "created_at) VALUES (:p, :b, :a, 'INR', 'UPI', 'asha@okmock', 'PENDING', now() - interval '5 minutes')"),
            {"p": payment_id, "b": booking_id, "a": AMOUNT})

    with pytest.raises(AppError) as err:
        service.refund(payment_id, "SAGA_TIMEOUT")

    assert err.value.code == "PAYMENT_NOT_REFUNDABLE"
    assert tuple(status_of(engine, booking_id)) == ("VOIDED", "NO_CHARGE_AT_GATEWAY")


def test_charge_stops_retrying_once_the_payment_was_closed(engine):
    bank = FakeBank(on_charge=lambda attempt, request: _refuse_first(attempt, request))
    holder = {}

    def sleep(_seconds):  # someone closes the payment while the charge waits to retry
        with engine.begin() as conn:
            conn.execute(text("UPDATE payments SET status = 'VOIDED', failure_reason = 'NO_CHARGE_AT_GATEWAY' "
                              "WHERE booking_id = :b"), {"b": holder["booking_id"]})

    service = service_with(engine, bank, sleep=sleep)
    holder["booking_id"] = booking_id = uuid.uuid4()

    with pytest.raises(AppError) as err:
        service.charge(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")

    assert err.value.code == "PAYMENT_CANCELLED"
    assert bank.calls == ["charge"]  # the second attempt was never sent


def test_a_capture_after_the_payment_was_voided_is_refunded_at_once(engine):
    holder = {}

    def void_while_at_the_bank(attempt, request):
        with engine.begin() as conn:
            conn.execute(text("UPDATE payments SET status = 'VOIDED', failure_reason = 'NO_CHARGE_AT_GATEWAY' "
                              "WHERE booking_id = :b"), {"b": holder["booking_id"]})

    bank = FakeBank(on_charge=void_while_at_the_bank)
    service = service_with(engine, bank)
    holder["booking_id"] = booking_id = uuid.uuid4()

    with pytest.raises(AppError) as err:
        service.charge(booking_id, AMOUNT, "INR", "UPI", "asha@okmock")

    payment = service.find_by_booking(booking_id)
    assert err.value.code == "PAYMENT_CANCELLED"
    assert payment["status"] == "REFUNDED" and payment["refund"]["status"] == "SUCCEEDED"
    assert bank.calls == ["charge", "refund"]
