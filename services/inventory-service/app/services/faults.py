"""Fault injection: make the next N calls of an operation fail. Dev only, via the admin API."""
import logging
import threading

from app.core.errors import AppError

log = logging.getLogger(__name__)

OPERATIONS = ("place_hold", "confirm_hold", "release_hold")


class FaultInjector:
    def __init__(self):
        self._armed: dict[str, dict] = {}
        self._lock = threading.Lock()

    def arm(self, operation: str, times: int = 1, status_code: int = 500) -> dict:
        with self._lock:
            self._armed[operation] = {"operation": operation, "remaining": times, "status_code": status_code}
            log.warning("fault armed", extra={"operation": operation, "times": times, "status_code": status_code})
            return dict(self._armed[operation])

    def clear(self) -> None:
        with self._lock:
            self._armed.clear()

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(f) for f in self._armed.values()]

    def maybe_fail(self, operation: str) -> None:
        with self._lock:
            fault = self._armed.get(operation)
            if not fault:
                return
            fault["remaining"] -= 1
            if fault["remaining"] <= 0:
                del self._armed[operation]
            status_code = fault["status_code"]
        log.warning("injected fault triggered", extra={"operation": operation, "status_code": status_code})
        raise AppError(status_code, "INJECTED_FAULT", "Injected failure",
                       f"Fault injection is active for '{operation}' (test/demo only)")
