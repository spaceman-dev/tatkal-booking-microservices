from uuid import UUID

from app.clients.base import ServiceClient


class PaymentClient:
    """payment-service: charge (saga step 2) and refund (its compensation)."""

    def __init__(self, client: ServiceClient):
        self.client = client

    def charge(self, booking_id: UUID, amount_paise: int, method: str, payer_ref: str) -> dict:
        # Not retried here: payment-service already retries the bank with backoff, and a
        # repeat would only wait on the same Idempotency-Key anyway.
        return self.client.call(
            "POST", "/api/v1/payments", idempotent=False,
            headers={"Idempotency-Key": f"booking-{booking_id}"},
            json={"booking_id": str(booking_id), "amount_paise": amount_paise, "currency": "INR",
                  "method": method, "payer_ref": payer_ref})

    def find_payment(self, booking_id: UUID) -> dict | None:
        found = self.client.call("GET", "/api/v1/payments", idempotent=True, params={"booking_id": str(booking_id)})
        return found[0] if found else None

    def refund(self, payment_id: UUID, reason: str) -> dict:
        # Safe to retry: a payment can be refunded only once (BR4).
        return self.client.call("POST", f"/api/v1/payments/{payment_id}/refund", idempotent=True,
                                json={"reason": reason})
