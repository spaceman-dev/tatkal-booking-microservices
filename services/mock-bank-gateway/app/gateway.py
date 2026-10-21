"""In-memory behaviour of the fake bank. State is lost on restart, which is fine
for a mock of an external system we do not own."""
import asyncio
import hashlib
import json
import logging
import random
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.core.errors import AppError

log = logging.getLogger(__name__)

UPI_LIMIT_PAISE = 1_00_000 * 100  # Rs 1 lakh per UPI transaction


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Gateway:
    def __init__(self, mode: str, latency_ms: int, slow_latency_ms: int, failure_rate: float):
        self.mode = mode
        self.latency_ms = latency_ms
        self.slow_latency_ms = slow_latency_ms
        self.failure_rate = failure_rate
        self.affects = {"charge", "refund"}  # which operations "down"/"flaky" apply to
        self.reset()

    def reset(self) -> None:
        self.charges: dict[str, dict] = {}
        self.charge_by_key: dict[str, tuple[str, dict]] = {}
        self.refunds: dict[str, dict] = {}
        self.refund_by_key: dict[str, tuple[str, dict]] = {}
        self.refund_by_charge: dict[str, str] = {}
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.stats: Counter = Counter()
        self.captures_per_reference: Counter = Counter()

    # ---- behaviour knobs -------------------------------------------------------

    def config(self) -> dict:
        return {"mode": self.mode, "latency_ms": self.latency_ms, "slow_latency_ms": self.slow_latency_ms,
                "failure_rate": self.failure_rate, "affects": sorted(self.affects)}

    def configure(self, mode: str, latency_ms: int | None, slow_latency_ms: int | None,
                  failure_rate: float | None, affects: list[str] | None = None) -> dict:
        self.mode = mode
        if latency_ms is not None:
            self.latency_ms = latency_ms
        if slow_latency_ms is not None:
            self.slow_latency_ms = slow_latency_ms
        if failure_rate is not None:
            self.failure_rate = failure_rate
        self.affects = set(affects) if affects else {"charge", "refund"}
        log.warning("gateway behaviour changed", extra=self.config())
        return self.config()

    def _reject_if_unavailable(self, operation: str) -> None:
        if operation not in self.affects:
            return
        if self.mode == "down" or (self.mode == "flaky" and random.random() < self.failure_rate):
            self.stats[f"{operation}_rejected_unavailable"] += 1
            log.warning("simulated outage, rejecting %s", operation, extra={"mode": self.mode})
            raise AppError(503, "GATEWAY_UNAVAILABLE", "Bank gateway unavailable",
                           f"Simulated outage (mode={self.mode}); the request was not processed")

    async def _processing_delay(self) -> None:
        delay = self.slow_latency_ms if self.mode == "slow" else self.latency_ms
        await asyncio.sleep(delay / 1000)

    # ---- charges -------------------------------------------------------------

    async def charge(self, idempotency_key: str, payload: dict) -> tuple[dict, bool]:
        """Returns (charge, replayed)."""
        self.stats["charge_requests"] += 1
        self._reject_if_unavailable("charge")
        fp = fingerprint(payload)
        async with self.locks[f"charge:{idempotency_key}"]:
            if idempotency_key in self.charge_by_key:
                previous_fp, charge = self.charge_by_key[idempotency_key]
                if previous_fp != fp:
                    raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key reused",
                                   "This key was already used with a different payload")
                self.stats["charge_replays"] += 1
                log.info("duplicate charge request answered from idempotency store",
                         extra={"idempotency_key": idempotency_key, "charge_id": charge["charge_id"]})
                return charge, True

            await self._processing_delay()
            charge = self._decide(payload)
            self.charges[charge["charge_id"]] = charge
            self.charge_by_key[idempotency_key] = (fp, charge)
            self.stats["charges_created"] += 1
            if charge["status"] == "captured":
                self.captures_per_reference[payload["merchant_reference"]] += 1
            log.info("charge %s", charge["status"],
                     extra={"charge_id": charge["charge_id"], "amount_paise": charge["amount_paise"],
                            "merchant_reference": charge["merchant_reference"]})
            return charge, False

    @staticmethod
    def _decide(payload: dict) -> dict:
        payer = payload["payer_ref"].lower()
        decline_reason = None
        if payer.startswith(("decline", "fail")):
            decline_reason = "INSUFFICIENT_FUNDS"
        elif payload["method"] == "UPI" and payload["amount_paise"] > UPI_LIMIT_PAISE:
            decline_reason = "UPI_LIMIT_EXCEEDED"
        return {
            "charge_id": "ch_" + uuid.uuid4().hex[:20],
            "status": "declined" if decline_reason else "captured",
            "decline_reason": decline_reason,
            "amount_paise": payload["amount_paise"],
            "currency": payload["currency"],
            "method": payload["method"],
            "merchant_reference": payload["merchant_reference"],
            "created_at": now_iso(),
        }

    async def find_by_key(self, idempotency_key: str) -> dict | None:
        # Waits for an in-flight request with the same key, so the answer is final.
        async with self.locks[f"charge:{idempotency_key}"]:
            entry = self.charge_by_key.get(idempotency_key)
            return entry[1] if entry else None

    def find_by_reference(self, merchant_reference: str) -> list[dict]:
        return [c for c in self.charges.values() if c["merchant_reference"] == merchant_reference]

    # ---- refunds -------------------------------------------------------------

    async def refund(self, idempotency_key: str, payload: dict) -> tuple[dict, bool]:
        self.stats["refund_requests"] += 1
        self._reject_if_unavailable("refund")
        fp = fingerprint(payload)
        async with self.locks[f"refund:{idempotency_key}"]:
            if idempotency_key in self.refund_by_key:
                previous_fp, refund = self.refund_by_key[idempotency_key]
                if previous_fp != fp:
                    raise AppError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key reused",
                                   "This key was already used with a different payload")
                self.stats["refund_replays"] += 1
                return refund, True

            charge = self.charges.get(payload["charge_id"])
            if charge is None:
                raise AppError(404, "CHARGE_NOT_FOUND", "Charge not found", f"No charge {payload['charge_id']}")
            if charge["status"] != "captured":
                raise AppError(409, "CHARGE_NOT_CAPTURED", "Charge not captured",
                               f"Charge {charge['charge_id']} is {charge['status']}")
            if payload["charge_id"] in self.refund_by_charge:
                raise AppError(409, "ALREADY_REFUNDED", "Charge already refunded",
                               f"Charge {charge['charge_id']} was refunded by {self.refund_by_charge[payload['charge_id']]}")
            if payload["amount_paise"] > charge["amount_paise"]:
                raise AppError(422, "REFUND_EXCEEDS_CHARGE", "Refund larger than charge",
                               "Refund amount is more than the captured amount")

            await self._processing_delay()
            refund = {
                "refund_id": "rf_" + uuid.uuid4().hex[:20],
                "charge_id": charge["charge_id"],
                "status": "processed",
                "amount_paise": payload["amount_paise"],
                "created_at": now_iso(),
            }
            self.refunds[refund["refund_id"]] = refund
            self.refund_by_key[idempotency_key] = (fp, refund)
            self.refund_by_charge[charge["charge_id"]] = refund["refund_id"]
            self.stats["refunds_created"] += 1
            log.info("refund processed", extra={"refund_id": refund["refund_id"], "charge_id": charge["charge_id"],
                                                "amount_paise": refund["amount_paise"]})
            return refund, False

    def snapshot_stats(self) -> dict:
        return {
            **{k: self.stats.get(k, 0) for k in (
                "charge_requests", "charges_created", "charge_replays", "charge_rejected_unavailable",
                "refund_requests", "refunds_created", "refund_replays", "refund_rejected_unavailable")},
            "captures_per_reference": dict(self.captures_per_reference),
            "config": self.config(),
        }
