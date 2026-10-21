import logging
import uuid
from uuid import UUID

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.db import is_unique_violation
from app.core.errors import AppError
from app.domain.templates import TemplateError, render
from app.repository import notifications_repo as repo
from app.services.provider import ProviderUnavailable, SimulatedProvider

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, engine: Engine, provider: SimulatedProvider):
        self.engine = engine
        self.provider = provider

    def publish(self, event_id: str, event_type: str, booking_id: UUID, mobile: str, email: str | None,
                data: dict) -> tuple[int, dict]:
        try:
            message = render(event_type, data)
        except TemplateError as exc:
            raise AppError(422, exc.code, "Cannot build the message", str(exc)) from exc
        channels = [("SMS", mobile)] + ([("EMAIL", email)] if email else [])
        rows = [{"notification_id": uuid.uuid4(), "event_id": event_id, "event_type": event_type,
                 "booking_id": booking_id, "channel": channel, "recipient": recipient, "message": message}
                for channel, recipient in channels]

        # 1. Claim the event. The UNIQUE(event_id, channel) constraint lets exactly one
        #    delivery of a duplicated event through.
        try:
            with self.engine.begin() as conn:
                repo.insert_pending(conn, rows)
        except IntegrityError as exc:
            if not is_unique_violation(exc, "uq_notifications_event_channel"):
                raise
            log.info("duplicate event ignored", extra={"event_id": event_id, "booking_id": str(booking_id)})
            return 200, self._view(event_id, duplicate=True)

        # 2. Send. If the provider fails, give the claim back so the sender's retry works.
        try:
            for channel, recipient in channels:
                self.provider.send(channel, recipient, message)
        except ProviderUnavailable as exc:
            with self.engine.begin() as conn:
                repo.delete_event(conn, event_id)
            raise AppError(503, "PROVIDER_UNAVAILABLE", "Message provider unavailable",
                           f"{exc}. Nothing was recorded; retry the event later.") from exc

        with self.engine.begin() as conn:
            repo.mark_sent(conn, event_id)
        log.info("notification delivered", extra={"event_id": event_id, "event_type": event_type,
                                                  "booking_id": str(booking_id), "channels": len(channels)})
        return 201, self._view(event_id, duplicate=False)

    def _view(self, event_id: str, duplicate: bool) -> dict:
        with self.engine.connect() as conn:
            deliveries = repo.list_by_event(conn, event_id)
        return {"event_id": event_id, "duplicate": duplicate, "deliveries": deliveries}

    def for_booking(self, booking_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.list_by_booking(conn, booking_id)
