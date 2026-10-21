"""In-memory stand-ins for inventory-service and payment-service, plugged into the
real HTTP client through httpx.MockTransport. They record every call so tests can
check the order of saga steps and compensations."""
import json
import uuid

import httpx


def _problem(status, code):
    return httpx.Response(status, json={"status": status, "code": code, "title": code})


class FakeInventory:
    def __init__(self):
        self.calls: list[str] = []
        self.holds: dict[str, dict] = {}
        self.confirm_failures = 0
        self.release_failures = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/holds":
            body = json.loads(request.content)
            self.calls.append("hold")
            hold = {"hold_id": str(uuid.uuid4()), "booking_id": body["booking_id"],
                    "seat_count": body["seat_count"], "total_amount_paise": 289_500 * body["seat_count"],
                    "status": "HELD"}
            self.holds[hold["hold_id"]] = hold
            return httpx.Response(201, json=hold)
        if request.method == "GET" and path == "/api/v1/holds":
            booking_id = request.url.params["booking_id"]
            return httpx.Response(200, json=[h for h in self.holds.values() if h["booking_id"] == booking_id])
        hold_id = path.split("/")[4]
        if hold_id not in self.holds:
            return _problem(404, "HOLD_NOT_FOUND")
        if path.endswith("/confirm"):
            self.calls.append("confirm")
            if self.confirm_failures > 0:
                self.confirm_failures -= 1
                return _problem(500, "INJECTED_FAULT")
            self.holds[hold_id]["status"] = "CONFIRMED"
            return httpx.Response(200, json=self.holds[hold_id])
        if path.endswith("/release"):
            self.calls.append("release")
            if self.release_failures > 0:
                self.release_failures -= 1
                return _problem(503, "UNAVAILABLE")
            self.holds[hold_id]["status"] = "RELEASED"
            return httpx.Response(200, json=self.holds[hold_id])
        return _problem(404, "NOT_FOUND")


class FakePayment:
    def __init__(self):
        self.calls: list[str] = []
        self.payments: dict[str, dict] = {}
        self.refund_in_progress = 0  # answer 503 PAYMENT_IN_PROGRESS this many times

    def for_booking(self, booking_id: str) -> dict | None:
        return next((p for p in self.payments.values() if p["booking_id"] == booking_id), None)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/payments":
            body = json.loads(request.content)
            self.calls.append("charge")
            if self.for_booking(body["booking_id"]):  # UNIQUE(booking_id), as in payment-service
                return _problem(409, "PAYMENT_ALREADY_EXISTS")
            payment = {"payment_id": str(uuid.uuid4()), "booking_id": body["booking_id"],
                       "amount_paise": body["amount_paise"], "status": "CAPTURED", "failure_reason": None}
            self.payments[payment["payment_id"]] = payment
            return httpx.Response(201, json=payment)
        if request.method == "POST" and path == "/api/v1/payments/void":
            body = json.loads(request.content)
            self.calls.append("void")
            payment = self.for_booking(body["booking_id"])
            if payment is None:
                payment = {"payment_id": str(uuid.uuid4()), "booking_id": body["booking_id"],
                           "amount_paise": body["amount_paise"], "status": "VOIDED",
                           "failure_reason": "VOIDED_BEFORE_CHARGE"}
                self.payments[payment["payment_id"]] = payment
            return httpx.Response(200, json=payment)
        if request.method == "GET" and path == "/api/v1/payments":
            payment = self.for_booking(request.url.params["booking_id"])
            return httpx.Response(200, json=[payment] if payment else [])
        if path.endswith("/refund"):
            self.calls.append("refund")
            payment = self.payments.get(path.split("/")[4])
            if payment is None:
                return _problem(404, "PAYMENT_NOT_FOUND")
            if self.refund_in_progress > 0:
                self.refund_in_progress -= 1
                return _problem(503, "PAYMENT_IN_PROGRESS")
            payment["status"] = "REFUNDED"
            return httpx.Response(200, json=payment)
        return _problem(404, "NOT_FOUND")
