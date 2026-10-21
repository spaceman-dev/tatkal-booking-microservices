#!/usr/bin/env python3
"""Condition (e) - a dependency that can be slow or fail: the mock bank.

Books one ticket through the normal API under each bank behaviour and shows what
the passenger gets, how many attempts payment-service made, and that the bank
never captured a booking twice.

    python scripts/bank_failure_modes.py
"""
import sys
import time
import uuid

from common import (
    BOOKING,
    GATEWAY,
    INVENTORY,
    Evidence,
    booking_body,
    check_services_up,
    fresh_run_date,
    http,
    open_quota,
    passenger_name,
    query,
    table,
)

TRAIN, CLASS, BASE_FARE = "12009", "CC", 105_500


def main() -> None:
    check_services_up()
    out = Evidence("bank-failures", "run", "CONDITION (e) - SLOW OR FAILING BANK GATEWAY")
    client = http()
    run_date = fresh_run_date()
    open_quota(client, TRAIN, run_date, CLASS, 20, BASE_FARE)
    out(f"quota: {TRAIN} / {run_date} / {CLASS}, 20 seats. payment-service: 2 s timeout per attempt, "
        "3 attempts, backoff with full jitter")
    rows, checks, number = [], [], 0

    def book(mode_label: str, mode: dict) -> dict:
        nonlocal number
        number += 1
        client.put(f"{GATEWAY}/gateway/v1/admin/mode", json=mode).raise_for_status()
        body = booking_body(TRAIN, run_date, CLASS, [(passenger_name(number), 30, "F")])
        started = time.perf_counter()
        r = client.post(f"{BOOKING}/api/v1/bookings", json=body, headers={"Idempotency-Key": f"bank-{uuid.uuid4()}"})
        elapsed = round(time.perf_counter() - started, 2)
        data = r.json()
        booking_id = data.get("booking_id")
        _, pay = query("payment-service", "SELECT status, gateway_attempts, failure_reason FROM payments "
                                          "WHERE booking_id = %(b)s", {"b": booking_id})
        _, hold = query("inventory-service", "SELECT status FROM seat_holds WHERE booking_id = %(b)s",
                        {"b": booking_id})
        captures = client.get(f"{GATEWAY}/gateway/v1/stats").json()["captures_per_reference"].get(str(booking_id), 0)
        row = {
            "mode": mode_label, "http": r.status_code,
            "outcome": data.get("status") if r.status_code == 201 else data.get("code"),
            "booking": data.get("status") if r.status_code == 201 else data.get("booking_status"),
            "payment": pay[0][0] if pay else "-", "attempts": pay[0][1] if pay else 0,
            "hold": hold[0][0] if hold else "-", "bank_captures": captures, "seconds": elapsed,
        }
        rows.append(tuple(row.values()))
        return row

    out.section("one booking per bank behaviour")
    normal = book("normal", {"mode": "normal", "latency_ms": 80})
    slow = book("slow 3 s", {"mode": "slow", "slow_latency_ms": 3000})
    down = book("down", {"mode": "down"})
    flaky = [book("flaky 60%", {"mode": "flaky", "failure_rate": 0.6}) for _ in range(4)]
    client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "normal", "latency_ms": 80})
    table(out, ["bank mode", "http", "outcome", "booking", "payment", "attempts", "seat hold",
                "bank captures", "seconds"], rows)

    out.section("what this shows")
    out("  normal : one attempt, captured, confirmed.")
    out("  slow   : the 1st attempt times out after 2 s; the retry carries the same key, waits for the bank's")
    out("           first (still running) request and gets its result - captured ONCE over two attempts.")
    out("  down   : every attempt gets 503; nothing was taken, so the booking fails fast and the seats are released.")
    out("  flaky  : 503s are retried with backoff; a booking either succeeds (one capture) or fails with its seats")
    out("           released. No booking is ever captured twice.")

    checks.append(("normal: confirmed in one attempt", normal["outcome"] == "CONFIRMED" and normal["attempts"] == 1))
    checks.append(("slow: confirmed after a retry, captured exactly once",
                   slow["outcome"] == "CONFIRMED" and slow["attempts"] >= 2 and slow["bank_captures"] == 1))
    checks.append(("down: 503 PAYMENT_UNAVAILABLE, payment FAILED, seats released",
                   down["http"] == 503 and down["payment"] == "FAILED" and down["hold"] == "RELEASED"))
    checks.append(("flaky: no booking captured more than once", all(f["bank_captures"] <= 1 for f in flaky)))
    checks.append(("every failed booking released its seats",
                   all(r["hold"] == "RELEASED" for r in [down, *flaky] if r["booking"] == "FAILED")))
    out.section("result")
    for text, passed in checks:
        out(f"  [{'PASS' if passed else 'FAIL'}] {text}")
    out.save()
    sys.exit(0 if all(p for _, p in checks) else 1)


if __name__ == "__main__":
    main()
