from datetime import date
from uuid import UUID

from app.clients.base import ServiceClient


class InventoryClient:
    """inventory-service calls: hold (step 1), confirm (step 3) and release (undo of step 1)."""

    def __init__(self, client: ServiceClient):
        self.client = client

    def hold(self, booking_id: UUID, train_no: str, journey_date: date, travel_class: str, seat_count: int) -> dict:
        # safe to retry, inventory keeps one hold per booking_id
        return self.client.call("POST", "/api/v1/holds", idempotent=True, json={
            "booking_id": str(booking_id), "train_no": train_no, "journey_date": journey_date.isoformat(),
            "travel_class": travel_class, "seat_count": seat_count})

    def confirm(self, hold_id: UUID) -> dict:
        return self.client.call("POST", f"/api/v1/holds/{hold_id}/confirm", idempotent=True)

    def release(self, hold_id: UUID, reason: str) -> dict:
        return self.client.call("POST", f"/api/v1/holds/{hold_id}/release", idempotent=True,
                                json={"reason": reason})

    def find_hold(self, booking_id: UUID) -> dict | None:
        found = self.client.call("GET", "/api/v1/holds", idempotent=True, params={"booking_id": str(booking_id)})
        return found[0] if found else None
