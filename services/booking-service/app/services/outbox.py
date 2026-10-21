"""Sends outbox events to notification-service, at least once.

A row is marked SENT only after notification-service accepts it, so a crash in between
sends it again. That's fine, notification-service drops duplicates by event_id.
"""
import logging
import threading

from sqlalchemy.engine import Engine

from app.clients.base import DownstreamError, DownstreamRejected
from app.clients.notification import NotificationClient
from app.core.context import correlation_id_var, new_correlation_id
from app.repository import outbox_repo as repo

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 60


class OutboxDispatcher:
    def __init__(self, engine: Engine, notifications: NotificationClient, poll_seconds: float, batch_size: int = 20):
        self.engine = engine
        self.notifications = notifications
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="outbox-dispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.dispatch_once()
            except Exception:
                log.exception("outbox dispatch round failed")

    def dispatch_once(self) -> int:
        delivered = 0
        with self.engine.begin() as conn:
            for event in repo.claim_due(conn, self.batch_size):
                token = correlation_id_var.set(event["correlation_id"] or new_correlation_id())
                attempts = event["attempts"] + 1
                try:
                    result = self.notifications.publish(event["payload"])
                    repo.mark_sent(conn, event["event_id"])
                    delivered += 1
                    log.info("outbox event delivered",
                             extra={"event_id": str(event["event_id"]), "event_type": event["event_type"],
                                    "booking_id": str(event["booking_id"]), "attempt": attempts,
                                    "duplicate_at_consumer": bool(result and result.get("duplicate"))})
                except DownstreamRejected as rej:
                    # 4xx means the event itself is bad, retrying won't help
                    repo.mark_dead(conn, event["event_id"], attempts, f"{rej.status} {rej.code}")
                    log.error("outbox event rejected by notification-service",
                              extra={"event_id": str(event["event_id"]), "error_code": rej.code})
                except DownstreamError as err:
                    delay = min(MAX_BACKOFF_SECONDS, 2 ** attempts)
                    repo.mark_retry(conn, event["event_id"], attempts, delay, err.detail)
                    log.warning("outbox delivery failed, will retry",
                                extra={"event_id": str(event["event_id"]), "attempt": attempts,
                                       "retry_in_s": delay, "error": err.detail})
                finally:
                    correlation_id_var.reset(token)
        return delivered
