"""Safety net for seat holds that nobody will ever confirm or release.

The booking saga normally confirms or releases every hold within seconds, and its
recovery job compensates sagas that die half way. A hold can still be left behind in
rare cases - for example a hold request that reaches the database only after
booking-service has given up on it and compensated. Such a hold would keep its seats
forever, so holds still HELD after HOLD_TTL_SECONDS (10 minutes by default, far longer
than any saga) are released here.

Only HELD holds are touched, so a paid (CONFIRMED) hold is never released. If a saga
were somehow still alive after the TTL, its confirm would get 409 HOLD_NOT_ACTIVE and
the saga would refund the payment - the system stays consistent either way.
"""
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
