from uuid import UUID

from app.clients.base import ServiceClient


class PaymentClient:
    """payment-service calls: charge (step 2), and refund or void to undo it."""

    def __init__(self, client: ServiceClient):
        self.client = client

    def charge(self, booking_id: UUID, amount_paise: int, method: str, payer_ref: str) -> dict:
        # no retry here: payment-service already retries the bank, and a repeat would only
        # wait on the same Idempotency-Key anyway
        return self.client.call(
            "POST", "/api/v1/payments", idempotent=False,
            headers={"Idempotency-Key": f"booking-{booking_id}"},
            json={"booking_id": str(booking_id), "amount_paise": amount_paise, "currency": "INR",
                  "method": method, "payer_ref": payer_ref})

    def find_payment(self, booking_id: UUID) -> dict | None:
        found = self.client.call("GET", "/api/v1/payments", idempotent=True, params={"booking_id": str(booking_id)})
        return found[0] if found else None

    def void(self, booking_id: UUID, amount_paise: int, method: str, payer_ref: str) -> dict:
        """Undo for step 2 when no payment is visible yet. payment-service stores a VOIDED
        payment for the booking (or returns the one that showed up in the meantime), so a
        charge still in flight is rejected when it arrives. Safe to retry."""
        return self.client.call("POST", "/api/v1/payments/void", idempotent=True, json={
            "booking_id": str(booking_id), "amount_paise": amount_paise, "currency": "INR",
            "method": method, "payer_ref": payer_ref})

    def refund(self, payment_id: UUID, reason: str) -> dict:
        # safe to retry, a payment is only ever refunded once (BR4)
        return self.client.call("POST", f"/api/v1/payments/{payment_id}/refund", idempotent=True,
                                json={"reason": reason})
