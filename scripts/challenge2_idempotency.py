#!/usr/bin/env python3
"""Challenge 2 - idempotency of unsafe requests (POST /bookings and POST /payments).

Scenarios
  A  same key + same body, sent 3 times one after another     -> one booking, same response replayed
  B  same key, failure response (declined payment) repeated   -> the stored 402 is replayed, bank not asked again
  C  same key + DIFFERENT body                                -> 422 IDEMPOTENCY_KEY_REUSED
  D  10 identical requests with one key at the same instant   -> one booking, 1 original + 9 replays
  E  no Idempotency-Key                                       -> 400
  F  payment-service directly: same key twice, then a new key for the same booking -> one payment (BR4)
Then the database rows and the log lines that show the replays.

    python scripts/challenge2_idempotency.py
"""
import hashlib
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from common import (
    BOOKING,
    GATEWAY,
    PAYMENT,
    Evidence,
    booking_body,
    check_services_up,
    compact_log,
    fresh_run_date,
    http,
    log_lines,
    open_quota,
    query,
    show_query,
    table,
)

TRAIN, CLASS, BASE_FARE = "12951", "3A", 249_500
PASSENGERS = [("Asha Rao", 34, "F"), ("Vikram Rao", 36, "M")]


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


def describe(r) -> tuple:
    data = r.json()
    return (r.status_code, data.get("status") if r.status_code < 400 else data.get("code"),
            str(data.get("booking_id", "-"))[:8], data.get("pnr") or "-",
            r.headers.get("idempotent-replayed", "-"), r.headers.get("x-correlation-id", "")[:12], digest(r.content))


HEADERS = ["http", "outcome", "booking", "pnr", "Idempotent-Replayed", "correlation_id", "sha256(body)"]


def main() -> None:
    check_services_up()
    out = Evidence("challenge-2-idempotency", "run", "CHALLENGE 2 - IDEMPOTENCY")
    started = datetime.now(timezone.utc)
    client = http()
    run_date = fresh_run_date()
    open_quota(client, TRAIN, run_date, CLASS, 10, BASE_FARE)
    body = booking_body(TRAIN, run_date, CLASS, PASSENGERS, email="asha@example.com")
    checks = []

    # ---- A ---------------------------------------------------------------------------
    key_a = f"c2-seq-{uuid.uuid4()}"
    out.section(f"A. same key, same body, three times in a row   Idempotency-Key: {key_a}")
    responses = []
    for attempt in range(1, 4):
        responses.append(client.post(f"{BOOKING}/api/v1/bookings", json=body, headers={"Idempotency-Key": key_a}))
        time.sleep(0.3)
    table(out, ["attempt", *HEADERS], [(i, *describe(r)) for i, r in enumerate(responses, 1)])
    booking_a = responses[0].json()["booking_id"]
    checks.append(("A: all three responses byte-identical", len({r.content for r in responses}) == 1))
    checks.append(("A: 2nd and 3rd marked Idempotent-Replayed",
                   [r.headers.get("idempotent-replayed") for r in responses] == [None, "true", "true"]))

    # ---- B ---------------------------------------------------------------------------
    key_b = f"c2-fail-{uuid.uuid4()}"
    out.section(f"B. a FAILED request is replayed too (declined payment)   Idempotency-Key: {key_b}")
    declined = booking_body(TRAIN, run_date, CLASS, [("Kiran Das", 41, "M")], payer_ref="decline@okmock")
    gateway_before = client.get(f"{GATEWAY}/gateway/v1/stats").json()["charge_requests"]
    first = client.post(f"{BOOKING}/api/v1/bookings", json=declined, headers={"Idempotency-Key": key_b})
    gateway_mid = client.get(f"{GATEWAY}/gateway/v1/stats").json()["charge_requests"]
    second = client.post(f"{BOOKING}/api/v1/bookings", json=declined, headers={"Idempotency-Key": key_b})
    gateway_after = client.get(f"{GATEWAY}/gateway/v1/stats").json()["charge_requests"]
    table(out, ["attempt", *HEADERS], [(1, *describe(first)), (2, *describe(second))])
    out(f"  bank gateway charge requests: before={gateway_before} after 1st={gateway_mid} after 2nd={gateway_after}")
    checks.append(("B: declined result replayed (402 twice, same body)",
                   first.status_code == second.status_code == 402 and first.content == second.content))
    checks.append(("B: the bank was not asked again on the repeat", gateway_after == gateway_mid))

    # ---- C ---------------------------------------------------------------------------
    out.section("C. same key as A but a different body (one passenger instead of two)")
    other = booking_body(TRAIN, run_date, CLASS, PASSENGERS[:1], email="asha@example.com")
    reused = client.post(f"{BOOKING}/api/v1/bookings", json=other, headers={"Idempotency-Key": key_a})
    out(f"  HTTP {reused.status_code}  {reused.text}")
    checks.append(("C: key reuse with a different body rejected with 422",
                   reused.status_code == 422 and reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"))

    # ---- D ---------------------------------------------------------------------------
    key_d = f"c2-par-{uuid.uuid4()}"
    parallel = 10
    out.section(f"D. {parallel} identical requests released at the same instant   Idempotency-Key: {key_d}")
    body_d = booking_body(TRAIN, run_date, CLASS, [("Meera Iyer", 29, "F")])
    gate = threading.Barrier(parallel)

    def send(number):
        with http() as c:
            gate.wait()
            sent = time.perf_counter()
            r = c.post(f"{BOOKING}/api/v1/bookings", json=body_d, headers={"Idempotency-Key": key_d})
            return (number, *describe(r), round((time.perf_counter() - sent) * 1000)), r.content

    with ThreadPoolExecutor(parallel) as pool:
        results = list(pool.map(send, range(1, parallel + 1)))
    rows = sorted((row for row, _ in results), key=lambda row: row[-1])
    table(out, ["thread", *HEADERS, "latency_ms"], rows)
    booking_d = {row[3] for row in rows}
    originals = sum(1 for row in rows if row[5] == "-")
    out(f"  distinct bookings: {len(booking_d)}   original responses: {originals}   replays: {parallel - originals}")
    checks.append(("D: one booking for 10 simultaneous requests", len(booking_d) == 1))
    checks.append(("D: exactly one original, nine replays", originals == 1))
    checks.append(("D: all ten bodies identical", len({content for _, content in results}) == 1))

    # ---- E ---------------------------------------------------------------------------
    out.section("E. no Idempotency-Key header")
    missing = client.post(f"{BOOKING}/api/v1/bookings", json=body)
    out(f"  HTTP {missing.status_code}  {missing.text}")
    checks.append(("E: missing key rejected with 400", missing.status_code == 400))

    # ---- F ---------------------------------------------------------------------------
    out.section("F. payment-service on its own: same key twice, then a new key for the same booking (BR4)")
    pay_booking = str(uuid.uuid4())
    charge = {"booking_id": pay_booking, "amount_paise": 289_500, "currency": "INR", "method": "UPI",
              "payer_ref": "ravi@okmock"}
    key_f = f"c2-pay-{uuid.uuid4()}"
    p1 = client.post(f"{PAYMENT}/api/v1/payments", json=charge, headers={"Idempotency-Key": key_f})
    p2 = client.post(f"{PAYMENT}/api/v1/payments", json=charge, headers={"Idempotency-Key": key_f})
    p3 = client.post(f"{PAYMENT}/api/v1/payments", json=charge, headers={"Idempotency-Key": f"c2-pay-{uuid.uuid4()}"})
    for label, r in (("same key, 1st", p1), ("same key, 2nd", p2), ("NEW key, same booking", p3)):
        data = r.json()
        out(f"  {label:22} HTTP {r.status_code}  payment={str(data.get('payment_id'))[:8]}  "
            f"status={data.get('status') if r.status_code < 400 else data.get('code')}  "
            f"replayed={r.headers.get('idempotent-replayed', '-')}  rule={data.get('rule', '-')}")
    checks.append(("F: same key -> same payment", p1.json()["payment_id"] == p2.json()["payment_id"]))
    checks.append(("F: new key for same booking refused (409, BR4)",
                   p3.status_code == 409 and p3.json()["code"] == "PAYMENT_ALREADY_EXISTS"))

    # ---- database evidence -------------------------------------------------------------
    out.section("database state")
    time.sleep(0.5)
    keys = [key_a, key_b, key_d]
    show_query(out, "Bookings created for the three keys (one row per key)", "booking-service",
               "SELECT idempotency_key, booking_id, status, pnr, failure_reason FROM bookings "
               "WHERE idempotency_key = ANY(%(k)s) ORDER BY created_at", {"k": keys})
    show_query(out, "Stored idempotency records (key, fingerprint, stored response)", "booking-service",
               "SELECT idem_key, status, response_code, left(request_hash, 16) AS request_hash, resource_id, "
               "left(response_body, 60) AS response_body_start FROM idempotency_keys WHERE idem_key = ANY(%(k)s) "
               "ORDER BY created_at", {"k": keys})
    _, booking_rows = query("booking-service", "SELECT booking_id FROM bookings WHERE idempotency_key = ANY(%(k)s)",
                            {"k": keys})
    booking_ids = [r[0] for r in booking_rows]
    payments = show_query(out, "Payments for those bookings (no duplicate charge)", "payment-service",
                          "SELECT booking_id, payment_id, status, amount_paise, gateway_attempts FROM payments "
                          "WHERE booking_id = ANY(%(b)s) ORDER BY created_at", {"b": booking_ids})
    stats = client.get(f"{GATEWAY}/gateway/v1/stats").json()["captures_per_reference"]
    out("\nCaptures recorded by the bank gateway per booking   [mock-bank-gateway /stats]")
    table(out, ["booking_id", "captures"], [(b, stats.get(str(b), 0)) for b in booking_ids])
    checks.append(("DB: exactly one booking per key", len(booking_ids) == len(keys)))
    checks.append(("DB: at most one payment per booking", len(payments) == len({p[0] for p in payments})))
    checks.append(("gateway: at most one capture per booking", all(stats.get(str(b), 0) <= 1 for b in booking_ids)))

    # ---- logs --------------------------------------------------------------------------
    out.section("log lines showing the replays (logs/booking-service.log)")
    replay_logs = [e for e in log_lines("booking-service", since=started)
                   if e.get("idempotency_key") in keys]
    for entry in replay_logs:
        out("  " + compact_log(entry))
    if not replay_logs:
        out("  (no log file found - start the services with scripts/stack.py to get logs/)")

    out.section("result")
    for text, passed in checks:
        out(f"  [{'PASS' if passed else 'FAIL'}] {text}")
    out.save()
    sys.exit(0 if all(p for _, p in checks) else 1)


if __name__ == "__main__":
    main()
