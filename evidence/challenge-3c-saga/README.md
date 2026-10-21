# Challenge 3C - Saga with compensation

**Claim.** The booking is a multi-service transaction (booking, inventory, payment, notification) with
explicit states. If step 3 fails, compensating actions undo step 2 (refund) and step 1 (release seats),
and the system ends consistent. If a compensation itself fails, the booking waits in COMPENSATING and a
background job finishes it later.

**Design.** Orchestrated saga in booking-service
([`services/saga.py`](../../services/booking-service/app/services/saga.py)):

| Step | Service | Compensation |
|---|---|---|
| 1 HOLD_SEATS | inventory | RELEASE_SEATS |
| 2 CHARGE_PAYMENT | payment (-> bank) | REFUND_PAYMENT |
| 3 CONFIRM_SEATS (pivot) | inventory | - |
| 4 notify | notification, via outbox (async) | - (retried, never undone) |

Booking states: PENDING -> SEATS_HELD -> PAYMENT_DONE -> CONFIRMED (-> CANCELLED), or -> COMPENSATING ->
FAILED. Every state change is committed before the next remote call, and every step and compensation is
written to `saga_log` (exposed at `GET /api/v1/bookings/{id}/saga`).
The compensation plan (BR6) is a pure function, `compensation_plan()` in
[`domain/booking_rules.py`](../../services/booking-service/app/domain/booking_rules.py): undo newest first;
"may have happened" counts (after a timeout the refund runs anyway - refunding nothing is harmless).

The failure at step 3 is forced with inventory-service's dev-only fault injection
(`POST /api/v1/admin/faults {"operation": "confirm_hold", "times": 2}` - two, because booking-service retries
an idempotent call once).

## Files

| File | What it proves |
|---|---|
| [`run_20261021-135040.txt`](run_20261021-135040.txt) | Output of `python scripts/challenge3_saga.py`, all 11 checks PASS: |
| &nbsp;&nbsp;1. happy path | HOLD, CHARGE, CONFIRM all OK -> 201 CONFIRMED with PNR |
| &nbsp;&nbsp;2. failure at step 3 | 502 `SEAT_CONFIRMATION_FAILED`; saga log `HOLD ok, CHARGE ok, CONFIRM failed, REFUND ok, RELEASE ok`; booking FAILED with refund = amount (Rs 9,800); payment REFUNDED + refund SUCCEEDED; hold RELEASED; quota back to where it was |
| &nbsp;&nbsp;3. failure at step 2 | bank declines -> 402; only RELEASE_SEATS runs (nothing to refund) |
| &nbsp;&nbsp;4. compensation fails | bank refunds down (partial outage) while step 3 fails: response 502 with `booking_status: COMPENSATING`, seats already released, refund PENDING. Bank comes back; with no further API calls the recovery job completes the refund and the booking reaches FAILED (21 s later in this run) |
| &nbsp;&nbsp;consistency check | for all 4 bookings of the run: booking status, hold, payment and notification agree across the four databases; quota `available = total - confirmed`, nothing left HELD |
| [`trace_step3_failure_20261021-135040.jsonl`](trace_step3_failure_20261021-135040.jsonl) | Scenario 2 traced through **all five services** by its correlation id: hold, charge at the bank, two failed confirms, refund at the bank, release, the 502, and about a second later the BOOKING_FAILED SMS and e-mail sent by the outbox under the same id |
| [`recovery_job_20261021-135040.jsonl`](recovery_job_20261021-135040.jsonl) | The recovery job (its own correlation id `recovery-...`) retrying the unfinished REFUND_PAYMENT, finishing it and the outbox delivering the refund notification |

## Alternatives considered

- **Choreography** (each service reacts to the previous one's event): no central coordinator, but the
  flow is spread across four services and needs a broker; with one clear owner of the booking lifecycle,
  orchestration is easier to follow, test and explain. See the report, section 8.
- **2PC / distributed transaction**: needs every service up at the same time and locks across services
  (CS07). Not available across separate PostgreSQL instances behind REST anyway.
- **Forward recovery** (keep retrying step 3 until it works): friendlier for the passenger, but a
  confirm can fail for good (hold released, train cancelled), so we chose backward recovery.

## Limitations

- Saga isolation: between step 1 and step 3 the seats are held but not sold. That is a deliberate
  semantic lock (the HELD state), but a booking that is later compensated did block those seats for its
  duration (usually well under a second).
- The recovery job finds stuck sagas by `updated_at`; one that has been quiet for
  `SAGA_STUCK_AFTER_SECONDS` (60 s) is compensated. A request genuinely running longer than that would be
  compensated underneath it - timeouts keep the slowest saga well below 60 s.
- Seats of a crashed saga come back through the recovery job. As a last safety net inventory-service
  also releases holds still HELD after `HOLD_TTL_SECONDS` (10 min) - a coarse sweep, not the reservation
  timer of Challenge 3B.
- A compensation can overlap the charge it is undoing (booking gave up waiting, payment did not). Then
  payment-service answers the refund with `503 PAYMENT_IN_PROGRESS` until the charge settles (the saga
  stays COMPENSATING and the recovery job retries); if no payment exists yet, booking voids it
  (`POST /api/v1/payments/void`) so a charge arriving later is refused; a charge stops retrying once its
  payment is closed; and a capture that lands on a voided payment is refunded at once.
- Reconciliation trusts the bank's lookup by idempotency key to include requests still being processed.
  The mock bank does; with a real bank the daily settlement report would be the final check.

## Reproduce

```bash
python scripts/challenge3_saga.py                 # about 45 s because scenario 4 waits for the recovery job
python scripts/challenge3_saga.py --skip-recovery # scenarios 1-3 only
```

Also covered by integration tests with fake services:
[`test_saga_db.py`](../../services/booking-service/tests/integration/test_saga_db.py) (step-3 failure order,
failed compensation finished by recovery, saga abandoned after payment, unknown hold, payment voided before
it arrives, refund of a charge still in flight) and
[`test_payment_fencing_db.py`](../../services/payment-service/tests/integration/test_payment_fencing_db.py)
(the payment-service side of those overlaps, including a late capture refunded at once).
