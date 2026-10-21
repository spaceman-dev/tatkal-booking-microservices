"""Gateway client: timeouts are retried with backoff + jitter, every attempt
reuses the same Idempotency-Key, and 4xx answers are never retried.
Uses httpx.MockTransport, so no network and no real sleeping."""
import httpx
import pytest

from app.clients.gateway import GatewayClient, backoff_delay

CHARGE = {"amount_paise": 100, "currency": "INR", "method": "UPI", "payer_ref": "a@ok", "merchant_reference": "b1"}
CAPTURED = {"charge_id": "ch_1", "status": "captured", "amount_paise": 100}


def client_with(handler, attempts=3):
    sleeps = []
    client = GatewayClient("http://gw", timeout_seconds=1, max_attempts=attempts, backoff_base_seconds=0.2,
                           backoff_cap_seconds=2.0, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return client, sleeps


def test_timeouts_are_retried_with_the_same_idempotency_key():
    seen_keys = []

    def handler(request):
        seen_keys.append(request.headers["Idempotency-Key"])
        if len(seen_keys) < 3:
            raise httpx.ReadTimeout("slow bank", request=request)
        return httpx.Response(200, json=CAPTURED)

    client, sleeps = client_with(handler)
    outcome = client.charge("pay-123", CHARGE)

    assert outcome.kind == "captured"
    assert outcome.attempts == 3
    assert seen_keys == ["pay-123"] * 3
    assert len(sleeps) == 2


def test_gateway_down_gives_unavailable_not_unknown():
    client, _ = client_with(lambda request: httpx.Response(503, json={"code": "GATEWAY_UNAVAILABLE"}))
    outcome = client.charge("pay-1", CHARGE)
    assert outcome.kind == "unavailable"
    assert outcome.attempts == 3


def test_any_timeout_without_a_final_answer_means_outcome_unknown():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(503)

    client, _ = client_with(handler)
    assert client.charge("pay-1", CHARGE).kind == "timeout"


def test_connection_refused_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client, _ = client_with(handler)
    assert client.charge("pay-1", CHARGE).kind == "unavailable"


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(409, json={"code": "IDEMPOTENCY_KEY_REUSED"})

    client, sleeps = client_with(handler)
    outcome = client.charge("pay-1", CHARGE)
    assert outcome.kind == "rejected"
    assert len(calls) == 1 and sleeps == []


def test_declined_charge_is_reported_as_declined():
    client, _ = client_with(lambda r: httpx.Response(200, json={**CAPTURED, "status": "declined"}))
    assert client.charge("pay-1", CHARGE).kind == "declined"


@pytest.mark.parametrize("attempt, ceiling", [(1, 0.2), (2, 0.4), (3, 0.8), (6, 2.0), (10, 2.0)])
def test_backoff_is_exponential_with_full_jitter_and_a_cap(attempt, ceiling):
    assert backoff_delay(attempt, 0.2, 2.0, rand=lambda: 1.0) == pytest.approx(ceiling)
    assert backoff_delay(attempt, 0.2, 2.0, rand=lambda: 0.0) == 0.0
