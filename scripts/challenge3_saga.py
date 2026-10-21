#!/usr/bin/env python3
"""Challenge 3C - saga with compensation.

The booking saga is orchestrated by booking-service:
    1 HOLD_SEATS (inventory)  ->  2 CHARGE_PAYMENT (payment)  ->  3 CONFIRM_SEATS (inventory)  -> notify (async)

Scenarios
  1. happy path, for comparison
  2. failure forced at step 3: compensations refund the payment (undo 2) and release the seats (undo 1)
  3. failure at step 2 (bank declines): only the seats need releasing
  4. a compensation that itself fails: step 3 fails while the bank is down, so the refund cannot run;
     the booking waits in COMPENSATING and the recovery job finishes it once the bank is back
Finally every booking of the run is checked across all four databases.

    python scripts/challenge3_saga.py
    python scripts/challenge3_saga.py --skip-recovery     # leave out scenario 4 (it waits ~30 s)
"""
import argparse
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
    compact_log,
    fresh_run_date,
    http,
    log_lines,
    open_quota,
    query,
    rupees,
    show_query,
    table,
)

TRAIN, CLASS, BASE_FARE, SEATS = "22691", "2A", 440_000, 4


def book(client, run_date, passengers, payer="asha@okmock", correlation_id=None):
    body = booking_body(TRAIN, run_date, CLASS, passengers, payer_ref=payer, email="asha@example.com")
    headers = {"Idempotency-Key": f"c3-{uuid.uuid4()}"}
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    r = client.post(f"{BOOKING}/api/v1/bookings", json=body, headers=headers)
    return r, r.json()


def trace(out, correlation_id):
    """Every log line, from every service, that carries this correlation id - in time order."""
    services = ("booking-service", "inventory-service", "payment-service", "mock-bank-gateway", "notification-service")
    entries = sorted((e for s in services for e in log_lines(s, correlation_id=correlation_id)), key=lambda e: e["ts"])
    for entry in entries:
        out("  " + compact_log(entry))
    if not entries:
        out("  (no logs/ folder - start the services with scripts/stack.py to see the trace)")
    return entries


def show_response(out, r, data):
    if r.status_code < 400:
        out(f"  HTTP {r.status_code}  status={data['status']}  pnr={data['pnr']}  booking={data['booking_id']}  "
            f"amount={rupees(data['amount_paise'])}")
    else:
        out(f"  HTTP {r.status_code}  code={data['code']}  booking_status={data.get('booking_status')}")
        out(f"  detail: {data.get('detail')}")


def show_saga_log(out, client, booking_id):
    steps = client.get(f"{BOOKING}/api/v1/bookings/{booking_id}/saga").json()
    out(f"\n  saga log  (GET /api/v1/bookings/{booking_id}/saga)")
    table(out, ["#", "step", "action", "outcome", "detail"],
          [(i, s["step"], s["action"], s["outcome"], (s["detail"] or "")[:70]) for i, s in enumerate(steps, 1)])
    return [(s["step"], s["action"], s["outcome"]) for s in steps]


def show_state(out, booking_id):
    """Where the seats and the money are for one booking, read from each service's own database."""
    b = {"b": booking_id}
    show_query(out, "booking", "booking-service",
               "SELECT status, failure_reason, amount_paise, refund_paise, pnr FROM bookings WHERE booking_id = %(b)s", b)
    show_query(out, "seat hold", "inventory-service",
               "SELECT status, seat_count, release_reason FROM seat_holds WHERE booking_id = %(b)s", b)
    show_query(out, "payment and refund", "payment-service",
               "SELECT p.status AS payment, p.amount_paise, p.failure_reason, r.status AS refund, "
               "r.amount_paise AS refund_paise FROM payments p LEFT JOIN refunds r USING (payment_id) "
               "WHERE p.booking_id = %(b)s", b)


def quota_line(out, client, run_date):
    q = client.get(f"{INVENTORY}/api/v1/inventory", params={"train_no": TRAIN, "journey_date": run_date.isoformat(),
                                                            "travel_class": CLASS}).json()
    out(f"\n  quota now: total={q['total_seats']} available={q['available_seats']} held={q['seats_held']} "
        f"confirmed={q['seats_confirmed']}")
    return q


def wait_for_status(client, booking_id, wanted, timeout):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"{BOOKING}/api/v1/bookings/{booking_id}").json()["status"]
        if status == wanted:
            return status, round(timeout - (deadline - time.time()), 1)
        time.sleep(1)
    return status, timeout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-recovery", action="store_true", help="skip scenario 4")
    args = parser.parse_args()
    check_services_up()

    out = Evidence("challenge-3c-saga", "run", "CHALLENGE 3C - SAGA WITH COMPENSATION")
    client = http()
    client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "normal"})
    client.delete(f"{INVENTORY}/api/v1/admin/faults")
    run_date = fresh_run_date()
    open_quota(client, TRAIN, run_date, CLASS, SEATS, BASE_FARE)
    out(f"quota opened: train {TRAIN}, {run_date}, class {CLASS}, {SEATS} seats")
    checks = []

    # ---- 1. happy path ----------------------------------------------------------------
    out.section("1. happy path")
    r, data = book(client, run_date, [("Asha Rao", 34, "F")])
    show_response(out, r, data)
    steps = show_saga_log(out, client, data["booking_id"])
    checks.append(("1: CONFIRMED after HOLD, CHARGE, CONFIRM",
                   r.status_code == 201 and [s[0] for s in steps] == ["HOLD_SEATS", "CHARGE_PAYMENT", "CONFIRM_SEATS"]))
    quota_line(out, client, run_date)

    # ---- 2. failure at step 3 -----------------------------------------------------------
    out.section("2. failure forced at step 3 (CONFIRM_SEATS)")
    out("  arming fault: the next 2 confirm calls fail with HTTP 500 (booking-service retries once)")
    client.post(f"{INVENTORY}/api/v1/admin/faults", json={"operation": "confirm_hold", "times": 2, "status_code": 500})
    step3_cid = f"c3-step3-{uuid.uuid4().hex[:12]}"
    out(f"  X-Correlation-ID: {step3_cid}")
    r, data = book(client, run_date, [("Vikram Rao", 36, "M"), ("Neha Rao", 9, "F")], correlation_id=step3_cid)
    show_response(out, r, data)
    failed_3 = data["booking_id"]
    steps = show_saga_log(out, client, failed_3)
    show_state(out, failed_3)
    q = quota_line(out, client, run_date)
    checks.append(("2: response 502 SEAT_CONFIRMATION_FAILED, booking FAILED",
                   r.status_code == 502 and data.get("booking_status") == "FAILED"))
    checks.append(("2: compensations ran in reverse order (refund, then release)", steps == [
        ("HOLD_SEATS", "EXECUTE", "OK"), ("CHARGE_PAYMENT", "EXECUTE", "OK"), ("CONFIRM_SEATS", "EXECUTE", "FAILED"),
        ("REFUND_PAYMENT", "COMPENSATE", "OK"), ("RELEASE_SEATS", "COMPENSATE", "OK")]))
    _, pay = query("payment-service", "SELECT p.status, r.status FROM payments p LEFT JOIN refunds r USING (payment_id) "
                                      "WHERE p.booking_id = %(b)s", {"b": failed_3})
    _, hold = query("inventory-service", "SELECT status FROM seat_holds WHERE booking_id = %(b)s", {"b": failed_3})
    checks.append(("2: payment REFUNDED and refund SUCCEEDED", pay == [("REFUNDED", "SUCCEEDED")]))
    checks.append(("2: hold RELEASED and seats back in the quota", hold == [("RELEASED",)] and q["available_seats"] == SEATS - 1))

    # ---- 3. failure at step 2 -----------------------------------------------------------
    out.section("3. failure at step 2 (bank declines the payment)")
    r, data = book(client, run_date, [("Kiran Das", 41, "M")], payer="decline@okmock")
    show_response(out, r, data)
    steps = show_saga_log(out, client, data["booking_id"])
    show_state(out, data["booking_id"])
    q = quota_line(out, client, run_date)
    checks.append(("3: 402 PAYMENT_DECLINED, only RELEASE_SEATS compensated", r.status_code == 402 and steps[-1] == (
        "RELEASE_SEATS", "COMPENSATE", "OK") and not any(s[0] == "REFUND_PAYMENT" for s in steps)))
    checks.append(("3: seats back in the quota", q["available_seats"] == SEATS - 1))

    # ---- 4. compensation that fails, finished by the recovery job ----------------------
    if not args.skip_recovery:
        out.section("4. step 3 fails while the bank's refund API is DOWN, so the refund cannot run yet")
        out("  bank: partial outage - charges work, refunds return 503;  inventory: next 2 confirms fail")
        client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "down", "affects": ["refund"]})
        client.post(f"{INVENTORY}/api/v1/admin/faults", json={"operation": "confirm_hold", "times": 2})
        r, data = book(client, run_date, [("Farhan Shah", 30, "M")])
        show_response(out, r, data)
        stuck = data["booking_id"]
        show_saga_log(out, client, stuck)
        show_state(out, stuck)
        quota_line(out, client, run_date)
        status_now = client.get(f"{BOOKING}/api/v1/bookings/{stuck}").json()["status"]
        out(f"\n  booking is {status_now}: the seats are already back in the quota, the refund is still owed")
        checks.append(("4: booking waits in COMPENSATING while refunds are down", status_now == "COMPENSATING"))

        out("\n  bank refunds are back; waiting for the recovery job (checks every 10 s, retries after 20 s) ...")
        client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "normal"})
        final, waited = wait_for_status(client, stuck, "FAILED", timeout=60)
        out(f"  booking reached {final} after {waited} s without anyone calling the API")
        show_saga_log(out, client, stuck)
        show_state(out, stuck)
        _, pay = query("payment-service", "SELECT p.status, r.status FROM payments p LEFT JOIN refunds r "
                                          "USING (payment_id) WHERE p.booking_id = %(b)s", {"b": stuck})
        checks.append(("4: recovery job completed the refund, booking FAILED",
                       final == "FAILED" and pay == [("REFUNDED", "SUCCEEDED")]))

    # ---- consistency across all services ------------------------------------------------
    out.section("consistency check for every booking of this run")
    time.sleep(3)  # let the outbox deliver the notifications
    _, bookings = query("booking-service", "SELECT booking_id, status, amount_paise, refund_paise FROM bookings "
                                           "WHERE train_no = %(t)s AND journey_date = %(d)s ORDER BY created_at",
                        {"t": TRAIN, "d": run_date})
    ids = [b[0] for b in bookings]
    _, holds = query("inventory-service", "SELECT booking_id, status, seat_count FROM seat_holds "
                                          "WHERE booking_id = ANY(%(i)s)", {"i": ids})
    _, payments = query("payment-service", "SELECT booking_id, status FROM payments WHERE booking_id = ANY(%(i)s)",
                        {"i": ids})
    _, notes = query("notification-service", "SELECT booking_id, string_agg(DISTINCT event_type, ',') "
                                             "FROM notifications WHERE booking_id = ANY(%(i)s) GROUP BY booking_id",
                     {"i": ids})
    hold_of, pay_of, note_of = dict((h[0], h[1]) for h in holds), dict(payments), dict(notes)
    rows, consistent = [], True
    for booking_id, status, amount, refund in bookings:
        hold, pay = hold_of.get(booking_id, "-"), pay_of.get(booking_id, "-")
        ok = ((status == "CONFIRMED" and hold == "CONFIRMED" and pay == "CAPTURED") or
              (status == "FAILED" and hold in ("RELEASED", "-") and pay in ("REFUNDED", "DECLINED", "FAILED", "VOIDED", "-")))
        consistent &= ok
        rows.append((str(booking_id)[:8], status, hold, pay, rupees(amount), rupees(refund),
                     note_of.get(booking_id, "-"), "yes" if ok else "NO"))
    table(out, ["booking", "booking status", "seat hold", "payment", "amount", "refunded", "notified", "consistent"], rows)
    q = quota_line(out, client, run_date)
    confirmed_seats = sum(h[2] for h in holds if h[1] == "CONFIRMED")
    checks.append(("all bookings consistent across booking/inventory/payment", consistent))
    checks.append((f"quota: available ({q['available_seats']}) == total ({SEATS}) - confirmed ({confirmed_seats}), "
                   f"nothing left HELD", q["available_seats"] == SEATS - confirmed_seats and q["seats_held"] == 0))

    out.section(f"scenario 2 traced through all service logs (correlation id {step3_cid})")
    out.save_raw_logs("trace_step3_failure", trace(out, step3_cid))
    recovery_logs = [e for e in log_lines("booking-service", since=out.started_utc)
                     if str(e.get("correlation_id", "")).startswith("recovery-")]
    if recovery_logs:
        out.section("recovery job log lines (scenario 4)")
        for entry in recovery_logs:
            out("  " + compact_log(entry))
        out.save_raw_logs("recovery_job", recovery_logs)

    client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "normal"})
    client.delete(f"{INVENTORY}/api/v1/admin/faults")
    out.section("result")
    for text, passed in checks:
        out(f"  [{'PASS' if passed else 'FAIL'}] {text}")
    out.save()
    sys.exit(0 if all(p for _, p in checks) else 1)


if __name__ == "__main__":
    main()
