#!/usr/bin/env python3
"""Challenge 1 - concurrency control on the Tatkal seat quota.

Fires N parallel booking requests (default 20) at a fresh quota of CAPACITY seats
(default 5). A barrier releases all threads at the same instant. Expected with the
real (atomic) implementation: exactly CAPACITY bookings CONFIRMED, every other
request rejected with 409 SEATS_UNAVAILABLE, and the databases consistent.

    python scripts/challenge1_concurrency.py                   # atomic - the real code
    python scripts/challenge1_concurrency.py --strategy naive  # check-then-update, to show the race
    python scripts/challenge1_concurrency.py --strategy both   # naive first, then atomic
"""
import argparse
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from common import (
    BOOKING,
    INVENTORY,
    Evidence,
    booking_body,
    check_services_up,
    fresh_run_date,
    http,
    log_lines,
    open_quota,
    passenger_name,
    query,
    show_query,
    table,
)

TRAIN, CLASS, BASE_FARE = "12627", "SL", 90_500

WHY = """
Why a plain check-then-update in application code fails
  available = SELECT available_seats ...     -- request A reads 5, request B reads 5
  if available >= 1: ...                     -- both pass the check
  UPDATE ... SET available_seats = 4         -- both write 4: one decrement is lost
Every request that reads before the others write "sees" a free seat, so more holds
are created than there are seats, and the counter stops matching the hold rows (a lost
update). The CHECK (available_seats >= 0) constraint cannot catch this either: the
counter never goes negative, it is simply wrong.

What inventory-service does instead (app/repository/inventory_repo.py)
  UPDATE seat_inventory SET available_seats = available_seats - :n
   WHERE inventory_id = :id AND available_seats >= :n
  RETURNING available_seats
Check and write are a single statement. PostgreSQL row-locks the quota row; a
concurrent UPDATE waits for the lock and then re-evaluates the WHERE clause against
the committed value, so once the seats are gone the UPDATE matches no row and the
request is rejected with 409 SEATS_UNAVAILABLE. The hold row is inserted in the same
transaction, so counter and holds always agree.
"""


def run(out: Evidence, strategy: str, capacity: int, requests: int, seats: int) -> bool:
    client = http()
    client.put(f"{INVENTORY}/api/v1/admin/hold-strategy", json={"strategy": strategy}).raise_for_status()
    run_date = fresh_run_date()
    open_quota(client, TRAIN, run_date, CLASS, capacity, BASE_FARE)
    expected = capacity // seats
    out.section(f"strategy={strategy}: {requests} parallel bookings of {seats} seat(s) against a quota of "
                f"{capacity} on {TRAIN} / {run_date} / {CLASS}")

    gate = threading.Barrier(requests)
    started = time.perf_counter()

    def attempt(number: int):
        body = booking_body(TRAIN, run_date, CLASS, [(passenger_name(number), 30 + number % 40, "M")] * seats)
        with http() as c:
            gate.wait()
            sent = time.perf_counter()
            r = c.post(f"{BOOKING}/api/v1/bookings", json=body, headers={"Idempotency-Key": f"c1-{uuid.uuid4()}"})
            data = r.json()
            outcome = data.get("status") if r.status_code == 201 else data.get("code")
            return (number, r.status_code, outcome, str(data.get("booking_id", ""))[:8], data.get("pnr") or "-",
                    round((time.perf_counter() - sent) * 1000), round((time.perf_counter() - started) * 1000))

    with ThreadPoolExecutor(requests) as pool:
        results = sorted(pool.map(attempt, range(1, requests + 1)), key=lambda row: row[6])
    table(out, ["req", "http", "outcome", "booking", "pnr", "latency_ms", "finished_at_ms"], results)

    accepted = sum(1 for r in results if r[1] == 201)
    rejected = sum(1 for r in results if r[1] == 409 and r[2] == "SEATS_UNAVAILABLE")
    out(f"\n  accepted (201 CONFIRMED): {accepted}   rejected (409 SEATS_UNAVAILABLE): {rejected}   "
        f"other: {requests - accepted - rejected}")

    params = {"t": TRAIN, "d": run_date, "c": CLASS}
    quota = show_query(out, "Seat quota after the run", "inventory-service",
                       "SELECT train_no, journey_date, travel_class, total_seats, available_seats, version "
                       "FROM seat_inventory WHERE train_no = %(t)s AND journey_date = %(d)s AND travel_class = %(c)s",
                       params)
    holds = show_query(out, "Seat holds by status", "inventory-service",
                       "SELECT h.status, count(*) AS holds, sum(h.seat_count) AS seats FROM seat_holds h "
                       "JOIN seat_inventory i USING (inventory_id) WHERE i.train_no = %(t)s "
                       "AND i.journey_date = %(d)s AND i.travel_class = %(c)s GROUP BY h.status ORDER BY h.status",
                       params)
    bookings = show_query(out, "Bookings by status", "booking-service",
                          "SELECT status, failure_reason, count(*) AS bookings FROM bookings "
                          "WHERE train_no = %(t)s AND journey_date = %(d)s GROUP BY status, failure_reason "
                          "ORDER BY status", {"t": TRAIN, "d": run_date})
    # No cross-database join: take the ids from booking-service, then ask payment-service's DB.
    _, id_rows = query("booking-service", "SELECT booking_id FROM bookings WHERE train_no = %(t)s "
                                          "AND journey_date = %(d)s", {"t": TRAIN, "d": run_date})
    payments = show_query(out, "Payments for these bookings", "payment-service",
                          "SELECT status, count(*) AS payments, sum(amount_paise) AS amount_paise FROM payments "
                          "WHERE booking_id = ANY(%(ids)s) GROUP BY status", {"ids": [r[0] for r in id_rows]})

    total, available = quota[0][3], quota[0][4]
    seats_taken = sum(r[2] for r in holds if r[0] in ("HELD", "CONFIRMED"))
    confirmed = sum(r[2] for r in bookings if r[0] == "CONFIRMED")
    captured = sum(r[1] for r in payments if r[0] == "CAPTURED")
    checks = [
        (f"exactly {expected} request(s) accepted (got {accepted})", accepted == expected),
        (f"seats taken by holds ({seats_taken}) <= total seats ({total})", seats_taken <= total),
        (f"available ({available}) + taken ({seats_taken}) == total ({total})", available + seats_taken == total),
        (f"confirmed bookings ({confirmed}) == captured payments ({captured})", confirmed == captured),
    ]
    out("\n  checks")
    for text, passed in checks:
        out(f"   [{'PASS' if passed else 'FAIL'}] {text}")
    return all(passed for _, passed in checks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capacity", type=int, default=5)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--seats", type=int, default=1, help="seats per booking (1-4)")
    parser.add_argument("--strategy", choices=["atomic", "naive", "both"], default="atomic")
    args = parser.parse_args()
    if args.requests < 2 or not 1 <= args.seats <= 4:
        parser.error("need at least 2 requests and 1-4 seats per booking")

    check_services_up()
    out = Evidence("challenge-1-concurrency", f"run_{args.strategy}", "CHALLENGE 1 - CONCURRENCY CONTROL")
    verdicts = {}
    try:
        for strategy in (["naive", "atomic"] if args.strategy == "both" else [args.strategy]):
            verdicts[strategy] = run(out, strategy, args.capacity, args.requests, args.seats)
    finally:
        http().put(f"{INVENTORY}/api/v1/admin/hold-strategy", json={"strategy": "atomic"})

    out.section("result")
    if "naive" in verdicts:
        out(f"  naive  (check-then-update): {'consistent?!' if verdicts['naive'] else 'OVERSOLD / inconsistent - the race is real'}")
    if "atomic" in verdicts:
        out(f"  atomic (conditional UPDATE): {'PASS - never over-allocated' if verdicts['atomic'] else 'FAIL'}")
    out(WHY)
    out.save_raw_logs("inventory_logs", log_lines("inventory-service", since=out.started_utc))
    out.save()
    sys.exit(0 if verdicts.get("atomic", True) else 1)


if __name__ == "__main__":
    main()
