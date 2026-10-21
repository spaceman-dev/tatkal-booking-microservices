import logging
import time
import uuid
from datetime import timedelta
from uuid import UUID

from sqlalchemy.engine import Engine

from app.core.errors import AppError
from app.domain.templates import TemplateError, render
from app.repository import notifications_repo as repo
from app.services.provider import ProviderUnavailable, SimulatedProvider

log = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, engine: Engine, provider: SimulatedProvider, lease_seconds: float = 30,
                 wait_seconds: float = 2.0, poll_seconds: float = 0.1):
        self.engine = engine
        self.provider = provider
        self.lease = timedelta(seconds=lease_seconds)
        # how long a duplicate waits for the delivery already sending the event.
        # keep it under booking-service's 3s timeout for this call
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    def publish(self, event_id: str, event_type: str, booking_id: UUID, mobile: str, email: str | None,
                data: dict) -> tuple[int, dict]:
        try:
            message = render(event_type, data)
        except TemplateError as exc:
            raise AppError(422, exc.code, "Cannot build the message", str(exc)) from exc
        recipients = {"SMS": mobile, **({"EMAIL": email} if email else {})}

        deadline = time.monotonic() + self.wait_seconds
        sent_now: list[str] = []
        while True:
            # claim the channels nobody has yet, plus dead claims (lease ran out).
            # UNIQUE(event_id, channel) so only one delivery can own a channel
            token = uuid.uuid4().hex
            rows = [{"notification_id": uuid.uuid4(), "event_id": event_id, "event_type": event_type,
                     "booking_id": booking_id, "channel": channel, "recipient": recipient, "message": message}
                    for channel, recipient in recipients.items()]
            with self.engine.begin() as conn:
                claimed = repo.claim_new(conn, rows, token, self.lease)
                claimed += repo.reclaim_expired(conn, event_id, token, self.lease)

            if claimed:
                sent_now += self._send(event_id, booking_id, claimed, token)

            # done once every channel is SENT, by us or by an earlier delivery
            deliveries = self._deliveries(event_id)
            if set(recipients) <= {d["channel"] for d in deliveries if d["status"] == "SENT"}:
                break
            # another delivery is still sending. keep polling till the deadline, then 503 so the outbox retries
            if time.monotonic() >= deadline:
                log.info("event is still being sent by another delivery", extra={"event_id": event_id})
                raise AppError(503, "NOTIFICATION_IN_PROGRESS", "Event is still being delivered",
                               "Another delivery of this event is sending it right now. Retry later; a repeat "
                               "never sends a channel twice.", headers={"Retry-After": "2"})
            time.sleep(self.poll_seconds)

        if not sent_now:
            log.info("duplicate event ignored", extra={"event_id": event_id, "booking_id": str(booking_id)})
            return 200, {"event_id": event_id, "duplicate": True, "deliveries": deliveries}
        log.info("notification delivered", extra={"event_id": event_id, "event_type": event_type,
                                                  "booking_id": str(booking_id), "channels": sent_now})
        return 201, {"event_id": event_id, "duplicate": False, "deliveries": deliveries}

    def _send(self, event_id: str, booking_id: UUID, claimed: list[dict], token: str) -> list[str]:
        sent: list[str] = []
        for row in claimed:
            try:
                self.provider.send(row["channel"], row["recipient"], row["message"])
            except ProviderUnavailable as exc:
                # release only what we haven't sent, so the retry sends just that
                unsent = [r["channel"] for r in claimed if r["channel"] not in sent]
                with self.engine.begin() as conn:
                    repo.release(conn, event_id, unsent, token)
                log.warning("provider failed, unsent channels released for the retry",
                            extra={"event_id": event_id, "booking_id": str(booking_id), "sent": sent,
                                   "released": unsent})
                raise AppError(503, "PROVIDER_UNAVAILABLE", "Message provider unavailable",
                               f"{exc}. Channels already sent stay sent; retry the event later.") from exc
            # commit SENT per channel, so if the next channel fails this one isn't sent again
            with self.engine.begin() as conn:
                repo.mark_sent(conn, event_id, row["channel"], token)
            sent.append(row["channel"])
        return sent

    def _deliveries(self, event_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.list_by_event(conn, event_id)

    def for_booking(self, booking_id: UUID) -> list[dict]:
        with self.engine.connect() as conn:
            return repo.list_by_booking(conn, booking_id)
