"""httpx wrapper used for every call to another service: correlation id header, timeouts,
retries for idempotent calls only, and two exception types the saga can tell apart."""
import logging
import random
import time

import httpx

from app.core.context import CORRELATION_HEADER, get_correlation_id

log = logging.getLogger(__name__)


class DownstreamRejected(Exception):
    """4xx from the other service, a definite no."""

    def __init__(self, service: str, status: int, body: dict):
        self.service = service
        self.status = status
        self.body = body
        self.code = body.get("code", "UNKNOWN")
        super().__init__(f"{service} rejected the request: {status} {self.code}")


class DownstreamError(Exception):
    """5xx from the other service, or it could not be reached.

    `uncertain` is set once a request went out but no answer came back, so the other
    side may have done the work anyway."""

    def __init__(self, service: str, detail: str, status: int | None = None, body: dict | None = None,
                 uncertain: bool = False):
        self.service = service
        self.detail = detail
        self.status = status
        self.body = body or {}
        self.code = self.body.get("code")
        self.uncertain = uncertain
        super().__init__(f"{service} failed: {detail}")


def _add_correlation_id(request: httpx.Request) -> None:
    request.headers[CORRELATION_HEADER] = get_correlation_id()


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {"items": body}
    except ValueError:
        return {}


class ServiceClient:
    def __init__(self, name: str, base_url: str, timeout_seconds: float, retries: int = 1,
                 transport: httpx.BaseTransport | None = None):
        self.name = name
        self.retries = retries
        self.http = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 2.0)),
            transport=transport,
            event_hooks={"request": [_add_correlation_id]},
        )

    def close(self) -> None:
        self.http.close()

    def call(self, method: str, path: str, *, idempotent: bool, json: dict | None = None,
             params: dict | None = None, headers: dict | None = None):
        attempts = 1 + (self.retries if idempotent else 0)
        # once an attempt was sent without getting an answer, later errors are uncertain too
        uncertain = False
        last_error: DownstreamError | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.http.request(method, path, json=json, params=params, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last_error = DownstreamError(self.name, f"cannot reach {self.name}: {type(exc).__name__}",
                                             uncertain=uncertain)
            except httpx.TransportError as exc:  # sent, but no (complete) answer
                uncertain = True
                last_error = DownstreamError(self.name, f"no answer from {self.name}: {type(exc).__name__}",
                                             uncertain=True)
            else:
                if response.status_code < 400:
                    return response.json() if response.content else None
                body = _json_or_empty(response)
                if response.status_code < 500:
                    raise DownstreamRejected(self.name, response.status_code, body)
                last_error = DownstreamError(self.name, f"{self.name} returned {response.status_code} "
                                                        f"{body.get('code', '')}".strip(),
                                             status=response.status_code, body=body, uncertain=uncertain)
            if attempt < attempts:
                # exponential backoff with full jitter
                delay = random.uniform(0, 0.2 * 2 ** (attempt - 1))
                log.warning("call to %s failed, retrying", self.name,
                            extra={"path": path, "attempt": attempt, "error": last_error.detail,
                                   "retry_in_ms": round(delay * 1000)})
                time.sleep(delay)
        raise last_error
