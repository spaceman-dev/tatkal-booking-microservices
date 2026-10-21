"""HTTP client for the external (mock) bank gateway.

Every call has a timeout, and failed calls are retried with exponential backoff
and full jitter. Retrying a charge is only safe because every attempt carries the
same Idempotency-Key, so the gateway processes it at most once.

Outcome kinds:
  captured / declined / processed  - the gateway answered
  rejected                         - the gateway refused the request (4xx); not retried
  unavailable                      - the request certainly did not get processed
  timeout                          - we sent it but never heard back: outcome UNKNOWN
  cancelled                        - the payment was closed by someone else; no further attempt sent
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from app.core.context import CORRELATION_HEADER, get_correlation_id

log = logging.getLogger(__name__)


def backoff_delay(attempt: int, base_seconds: float, cap_seconds: float,
                  rand: Callable[[], float] = random.random) -> float:
    """Full jitter: uniform in [0, min(cap, base * 2^(attempt-1))]. Spreads the
    retries of many clients out instead of having them hit the gateway together."""
    return rand() * min(cap_seconds, base_seconds * (2 ** (attempt - 1)))


@dataclass
class GatewayOutcome:
    kind: str
    body: dict | None = None
    attempts: int = 0
    detail: str | None = None


def _add_correlation_id(request: httpx.Request) -> None:
    request.headers[CORRELATION_HEADER] = get_correlation_id()


class GatewayClient:
    def __init__(self, base_url: str, timeout_seconds: float, max_attempts: int, backoff_base_seconds: float,
                 backoff_cap_seconds: float, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 1.0)),
            transport=transport,
            event_hooks={"request": [_add_correlation_id]},
        )
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base_seconds
        self.backoff_cap = backoff_cap_seconds
        self.sleep = sleep

    def close(self) -> None:
        self.http.close()

    def charge(self, idempotency_key: str, payload: dict,
               still_wanted: Callable[[], bool] | None = None) -> GatewayOutcome:
        outcome = self._post_with_retries("/gateway/v1/charges", idempotency_key, payload, still_wanted)
        if outcome.kind == "ok":
            outcome.kind = "captured" if outcome.body["status"] == "captured" else "declined"
        return outcome

    def refund(self, idempotency_key: str, payload: dict) -> GatewayOutcome:
        outcome = self._post_with_retries("/gateway/v1/refunds", idempotency_key, payload)
        if outcome.kind == "ok":
            outcome.kind = "processed"
        return outcome

    def find_charge(self, idempotency_key: str) -> dict | None:
        """Reconciliation: ask the gateway what happened to a charge. Raises
        httpx.HTTPError if the gateway cannot be reached."""
        response = self.http.get("/gateway/v1/charges", params={"idempotency_key": idempotency_key})
        response.raise_for_status()
        found = response.json()
        return found[0] if found else None

    def _post_with_retries(self, path: str, idempotency_key: str, payload: dict,
                           still_wanted: Callable[[], bool] | None = None) -> GatewayOutcome:
        maybe_processed = False
        detail = None
        for attempt in range(1, self.max_attempts + 1):
            if still_wanted is not None and not still_wanted():
                log.warning("payment was closed meanwhile, not sending another attempt",
                            extra={"path": path, "attempt": attempt, "idempotency_key": idempotency_key})
                return GatewayOutcome("cancelled", None, attempt - 1, f"closed before attempt {attempt}")
            try:
                response = self.http.post(path, json=payload, headers={"Idempotency-Key": idempotency_key})
            except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
                detail = type(exc).__name__  # never reached the gateway
            except httpx.TimeoutException as exc:
                maybe_processed = True       # sent, but no answer - it may have been processed
                detail = type(exc).__name__
            except httpx.TransportError as exc:
                maybe_processed = True
                detail = type(exc).__name__
            else:
                if response.status_code < 500:
                    kind = "ok" if response.status_code == 200 else "rejected"
                    if attempt > 1:
                        log.info("gateway call succeeded after retry",
                                 extra={"path": path, "attempt": attempt, "idempotency_key": idempotency_key,
                                        "replayed": response.headers.get("Idempotent-Replayed") == "true"})
                    return GatewayOutcome(kind, response.json(), attempt, f"HTTP {response.status_code}")
                detail = f"HTTP {response.status_code}"

            if attempt < self.max_attempts:
                delay = backoff_delay(attempt, self.backoff_base, self.backoff_cap)
                log.warning("gateway call failed, retrying with the same idempotency key",
                            extra={"path": path, "attempt": attempt, "error": detail,
                                   "retry_in_ms": round(delay * 1000), "idempotency_key": idempotency_key})
                self.sleep(delay)
            else:
                log.error("gateway call failed, giving up",
                          extra={"path": path, "attempts": attempt, "error": detail,
                                 "outcome_unknown": maybe_processed, "idempotency_key": idempotency_key})

        return GatewayOutcome("timeout" if maybe_processed else "unavailable", None, self.max_attempts, detail)
