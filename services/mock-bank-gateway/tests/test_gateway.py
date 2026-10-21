import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

CHARGE = {"amount_paise": 579000, "currency": "INR", "method": "UPI",
          "payer_ref": "asha@okmock", "merchant_reference": "booking-1"}


@pytest.fixture(autouse=True)
def clean_gateway():
    app.state.gateway.reset()
    app.state.gateway.configure("normal", 5, 4000, 0.5)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_charge_is_captured_and_replayed_for_same_key(client):
    first = client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k1"})
    again = client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k1"})

    assert first.status_code == 200 and first.json()["status"] == "captured"
    assert again.json()["charge_id"] == first.json()["charge_id"]
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert client.get("/gateway/v1/stats").json()["charges_created"] == 1


def test_same_key_with_different_payload_is_rejected(client):
    client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k1"})
    other = client.post("/gateway/v1/charges", json={**CHARGE, "amount_paise": 1},
                        headers={"Idempotency-Key": "k1"})
    assert other.status_code == 409
    assert other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_missing_key_is_a_bad_request(client):
    assert client.post("/gateway/v1/charges", json=CHARGE).status_code == 400


def test_decline_payer_is_declined(client):
    r = client.post("/gateway/v1/charges", json={**CHARGE, "payer_ref": "decline@okmock"},
                    headers={"Idempotency-Key": "k2"})
    assert r.json()["status"] == "declined"
    assert r.json()["decline_reason"] == "INSUFFICIENT_FUNDS"


def test_down_mode_processes_nothing(client):
    client.put("/gateway/v1/admin/mode", json={"mode": "down"})
    r = client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k3"})
    assert r.status_code == 503
    assert client.get("/gateway/v1/stats").json()["charges_created"] == 0


def test_partial_outage_can_hit_refunds_only(client):
    client.put("/gateway/v1/admin/mode", json={"mode": "down", "affects": ["refund"]})
    charge = client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k5"})
    refund = client.post("/gateway/v1/refunds", json={"charge_id": charge.json()["charge_id"], "amount_paise": 1},
                         headers={"Idempotency-Key": "r5"})
    assert charge.status_code == 200 and charge.json()["status"] == "captured"
    assert refund.status_code == 503


def test_refund_only_once_and_never_more_than_charged(client):
    charge = client.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "k4"}).json()
    body = {"charge_id": charge["charge_id"], "amount_paise": charge["amount_paise"]}

    ok = client.post("/gateway/v1/refunds", json=body, headers={"Idempotency-Key": "r1"})
    replay = client.post("/gateway/v1/refunds", json=body, headers={"Idempotency-Key": "r1"})
    second = client.post("/gateway/v1/refunds", json=body, headers={"Idempotency-Key": "r2"})
    too_big = client.post("/gateway/v1/refunds", json={**body, "amount_paise": 10**9},
                          headers={"Idempotency-Key": "r3"})

    assert ok.status_code == 200 and ok.json()["status"] == "processed"
    assert replay.json()["refund_id"] == ok.json()["refund_id"]
    assert second.status_code == 409 and second.json()["code"] == "ALREADY_REFUNDED"
    assert too_big.status_code in (409, 422)


def test_parallel_requests_with_one_key_create_one_charge():
    async def fire():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as ac:
            return await asyncio.gather(*[
                ac.post("/gateway/v1/charges", json=CHARGE, headers={"Idempotency-Key": "same"})
                for _ in range(10)
            ])

    responses = asyncio.run(fire())
    charge_ids = {r.json()["charge_id"] for r in responses}
    assert len(charge_ids) == 1
    assert app.state.gateway.snapshot_stats()["charges_created"] == 1
    assert app.state.gateway.snapshot_stats()["charge_replays"] == 9
