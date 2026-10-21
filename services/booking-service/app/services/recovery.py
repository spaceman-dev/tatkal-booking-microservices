"""Background job that finishes sagas nobody is driving any more:

* bookings left in COMPENSATING because a compensation call failed
  (e.g. payment-service was down when we tried to refund)
* bookings stuck in PENDING / SEATS_HELD / PAYMENT_DONE because the process
  handling the request died half way

It also purges idempotency keys older than the configured TTL.
"""
import logging
import threading
from datetime import timedelta

from sqlalchemy.engine import Engine

from app.core.context import correlation_id_var, new_correlation_id
from app.domain.booking_rules import BookingStatus as S
from app.repository import bookings_repo as repo
from app.services.saga import BookingSaga

log = logging.getLogger(__name__)


class SagaRecovery:
    def __init__(self, engine: Engine, saga: BookingSaga, interval_seconds: float, compensation_retry_seconds: int,
                 stuck_after_seconds: int, idempotency_ttl_hours: int):
        self.engine = engine
        self.saga = saga
        self.interval_seconds = interval_seconds
        self.compensation_retry_seconds = compensation_retry_seconds
        self.stuck_after_seconds = stuck_after_seconds
        self.idempotency_ttl = timedelta(hours=idempotency_ttl_hours)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="saga-recovery", daemon=True)
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
                log.exception("saga recovery round failed")

    def run_once(self) -> int:
        # Failed compensations are retried once they have been quiet for a short while;
        # in-flight sagas are only touched once they have been quiet for much longer.
        with self.engine.begin() as conn:
            retry = repo.claim_stuck_bookings(conn, (S.COMPENSATING,), self.compensation_retry_seconds, 10)
            stuck = repo.claim_stuck_bookings(conn, (S.PENDING, S.SEATS_HELD, S.PAYMENT_DONE),
                                              self.stuck_after_seconds, 10)
            purged = repo.purge_idempotency_keys(conn, self.idempotency_ttl)
        if purged:
            log.info("expired idempotency keys purged", extra={"count": purged})

        for booking_id in [*retry, *stuck]:
            token = correlation_id_var.set(f"recovery-{new_correlation_id()[:12]}")
            try:
                final = self.saga.recover(booking_id)
                log.info("recovery processed booking", extra={"booking_id": str(booking_id), "status": final})
            except Exception:
                log.exception("recovery of booking failed", extra={"booking_id": str(booking_id)})
            finally:
                correlation_id_var.reset(token)
        return len(retry) + len(stuck)
