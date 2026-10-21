from app.clients.base import ServiceClient


class NotificationClient:
    """notification-service: only called by the outbox dispatcher, never inside a booking request."""

    def __init__(self, client: ServiceClient):
        self.client = client

    def publish(self, event: dict) -> dict:
        return self.client.call("POST", "/api/v1/notifications", idempotent=False, json=event)
