"""Releases holds still HELD after HOLD_TTL_SECONDS, for the rare hold the saga never finished
(like one that reached the DB after booking-service had already compensated)."""
import logging
import threading

from sqlalchemy.engine import Engine

from app.core.context import correlation_id_var, new_correlation_id
from app.repository import inventory_repo as repo

log = logging.getLogger(__name__)


class HoldExpiry:
    def __init__(self, engine: Engine, ttl_seconds: int, interval_seconds: float):
        self.engine = engine
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="hold-expiry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.run_once()
            except Exception:
                log.exception("hold expiry round failed")

    def run_once(self) -> int:
        token = correlation_id_var.set(f"hold-expiry-{new_correlation_id()[:12]}")
        try:
            with self.engine.begin() as conn:
                expired = repo.expire_stale_holds(conn, self.ttl_seconds)
                for hold in expired:
                    repo.increment_available(conn, hold["inventory_id"], hold["seat_count"])
            for hold in expired:
                log.warning("hold expired, seats returned to the quota",
                            extra={"hold_id": str(hold["hold_id"]), "booking_id": str(hold["booking_id"]),
                                   "seat_count": hold["seat_count"], "ttl_seconds": self.ttl_seconds})
            return len(expired)
        finally:
            correlation_id_var.reset(token)
