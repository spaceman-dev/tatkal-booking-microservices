"""Idempotency-Key handling for unsafe POSTs (same file in booking and payment).

The first request claims the key with INSERT ... ON CONFLICT DO NOTHING (primary key, so
only one can win), does the work and stores the status code and response body. Others
with the same key get 422 if the body differs, the stored response if it is done, or
wait for it while it runs. If the owner dies its lease runs out and the next request
takes over. The owner token stops an old owner from overwriting the new one's record.
"""
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from fastapi import Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from app.core.errors import PROBLEM_MEDIA_TYPE, AppError
from app.repository.tables import idempotency_keys as keys

log = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")
REPLAY_HEADER = "Idempotent-Replayed"


def require_key(raw: str | None) -> str:
    if raw is None:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header required",
                       "This operation is not safe to repeat, so it needs a unique Idempotency-Key header.")
    if not KEY_PATTERN.match(raw):
        raise AppError(400, "IDEMPOTENCY_KEY_INVALID", "Invalid Idempotency-Key",
                       "Use 8-100 characters: letters, digits, '.', '_', ':' or '-'.")
    return raw


def fingerprint(method: str, path: str, payload: dict) -> str:
    """SHA-256 over method, path and body (sorted keys), so formatting doesn't matter
    but any changed value does."""
    canonical = json.dumps({"method": method.upper(), "path": path, "body": payload},
                           sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class Claim:
    owner: bool
    token: str | None = None
    status_code: int | None = None
    body_text: str | None = None


class IdempotencyStore:
    def __init__(self, engine: Engine, lease_seconds: int = 60, wait_seconds: float = 15,
                 poll_seconds: float = 0.1):
        self.engine = engine
        self.lease = timedelta(seconds=lease_seconds)
        self.wait_seconds = wait_seconds
        self.poll_seconds = poll_seconds

    def claim(self, key: str, request_hash: str) -> Claim:
        deadline = time.monotonic() + self.wait_seconds
        announced_wait = False
        while True:
            token = uuid.uuid4().hex
            with self.engine.begin() as conn:
                won = conn.execute(
                    pg_insert(keys)
                    .values(idem_key=key, request_hash=request_hash, status="IN_PROGRESS",
                            owner_token=token, locked_until=func.now() + self.lease)
                    .on_conflict_do_nothing(index_elements=[keys.c.idem_key])
                    .returning(keys.c.idem_key)
                ).first()
                if won:
                    return Claim(owner=True, token=token)

                row = conn.execute(select(keys).where(keys.c.idem_key == key)).mappings().first()
                if row is not None:
                    if row["request_hash"] != request_hash:
                        log.warning("idempotency key reused with a different payload",
                                    extra={"idempotency_key": key})
                        raise AppError(422, "IDEMPOTENCY_KEY_REUSED",
                                       "Idempotency-Key was used for a different request",
                                       "This key belongs to a request with a different body. "
                                       "Use a new key for a new request.")
                    if row["status"] == "COMPLETED":
                        log.info("replaying stored response for idempotency key",
                                 extra={"idempotency_key": key, "stored_status": row["response_code"],
                                        "resource_id": row["resource_id"]})
                        return Claim(owner=False, status_code=row["response_code"], body_text=row["response_body"])
                    took_over = conn.execute(
                        update(keys)
                        .where(keys.c.idem_key == key, keys.c.status == "IN_PROGRESS",
                               keys.c.locked_until < func.now())
                        .values(owner_token=token, locked_until=func.now() + self.lease, updated_at=func.now())
                        .returning(keys.c.idem_key)
                    ).first()
                    if took_over:
                        log.warning("earlier attempt with this key timed out, taking over",
                                    extra={"idempotency_key": key})
                        return Claim(owner=True, token=token)

            if not announced_wait:
                log.info("same idempotency key is being processed, waiting for its result",
                         extra={"idempotency_key": key})
                announced_wait = True
            if time.monotonic() >= deadline:
                raise AppError(409, "IDEMPOTENCY_REQUEST_IN_PROGRESS",
                               "A request with this Idempotency-Key is still being processed",
                               "Retry later with the same key to get its result.",
                               headers={"Retry-After": "2"})
            time.sleep(self.poll_seconds)

    def complete(self, key: str, token: str, status_code: int, body_text: str, resource_id: str | None) -> bool:
        with self.engine.begin() as conn:
            stored = conn.execute(
                update(keys)
                .where(keys.c.idem_key == key, keys.c.owner_token == token, keys.c.status == "IN_PROGRESS")
                .values(status="COMPLETED", response_code=status_code, response_body=body_text,
                        resource_id=resource_id, updated_at=func.now())
            ).rowcount == 1
        if not stored:
            log.warning("lost ownership of the idempotency key before finishing; response not stored",
                        extra={"idempotency_key": key})
        return stored

    def release(self, key: str, token: str) -> None:
        """Delete our IN_PROGRESS claim (unexpected error or a non-final answer) so the
        client can retry with the same key."""
        with self.engine.begin() as conn:
            conn.execute(delete(keys).where(keys.c.idem_key == key, keys.c.owner_token == token,
                                            keys.c.status == "IN_PROGRESS"))


def run_idempotent(store: IdempotencyStore, key: str, request_hash: str, path: str,
                   action: Callable[[], tuple[int, dict]], resource_field: str) -> Response:
    claim = store.claim(key, request_hash)
    if not claim.owner:
        return _response(claim.status_code, claim.body_text, replayed=True)

    try:
        status_code, body = action()
    except AppError as err:
        if not err.final:
            # an answer that can still change (still in progress): send it, but don't store it
            store.release(key, claim.token)
            return _response(err.status, json.dumps(err.to_problem(path), separators=(",", ":")),
                              replayed=False, headers=err.headers)
        # business failures (declined, sold out...) are final, so they get stored and replayed too
        status_code, body = err.status, err.to_problem(path)
    except Exception:
        store.release(key, claim.token)
        raise

    body = jsonable_encoder(body)
    body_text = json.dumps(body, separators=(",", ":"))
    resource_id = body.get(resource_field)
    store.complete(key, claim.token, status_code, body_text, resource_id=str(resource_id) if resource_id else None)
    return _response(status_code, body_text, replayed=False)


def _response(status_code: int, body_text: str, replayed: bool, headers: dict | None = None) -> Response:
    headers = dict(headers or {})
    if replayed:
        headers[REPLAY_HEADER] = "true"
    media_type = PROBLEM_MEDIA_TYPE if status_code >= 400 else "application/json"
    return Response(content=body_text, status_code=status_code, headers=headers or None, media_type=media_type)
