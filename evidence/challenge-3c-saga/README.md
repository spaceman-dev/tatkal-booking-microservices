# Challenge 3C - saga with compensation

**Claim:** booking a ticket is a transaction across booking, inventory, payment and notification with
explicit states. When step 3 fails, compensations undo step 2 (refund) and step 1 (release the seats)
and everything ends consistent. When a compensation itself fails, the booking waits in COMPENSATING and
a background job finishes it later.

The saga is orchestrated by booking-service
([`services/saga.py`](../../services/booking-service/app/services/saga.py)):

| Step | Service | Undo |
|---|---|---|
| 1 HOLD_SEATS | inventory | RELEASE_SEATS |
| 2 CHARGE_PAYMENT | payment (calls the bank) | REFUND_PAYMENT |
| 3 CONFIRM_SEATS (pivot) | inventory | none |
| 4 notify | notification, through the outbox | none, retried until delivered |

States: PENDING -> SEATS_HELD -> PAYMENT_DONE -> CONFIRMED (-> CANCELLED), or -> COMPENSATING -> FAILED.
Each state is committed before the next remote call, and every step and compensation goes into
`saga_log` (`GET /api/v1/bookings/{id}/saga`). What to undo comes from `compensation_plan()` (BR6) in
[`domain/booking_rules.py`](../../services/booking-service/app/domain/booking_rules.py): newest first,
and steps that *may* have happened count too (after a timeout we refund anyway, refunding nothing is
harmless).

The step-3 failure is forced with inventory-service's dev-only fault injector
(`POST /api/v1/admin/faults {"operation": "confirm_hold", "times": 2}`). It needs two failures because
booking-service retries a safe call once.

## Files

| File | What it shows |
|---|---|
| [`run_20261021-171541.txt`](run_20261021-171541.txt) | output of `python scripts/challenge3_saga.py`, all 11 checks PASS: |
| &nbsp;&nbsp;1. happy path | HOLD, CHARGE, CONFIRM all OK, 201 CONFIRMED with a PNR |
| &nbsp;&nbsp;2. failure at step 3 | 502 `SEAT_CONFIRMATION_FAILED`; saga log `HOLD ok, CHARGE ok, CONFIRM failed, REFUND ok, RELEASE ok`; booking FAILED with Rs 9,800 refunded; payment REFUNDED and refund SUCCEEDED; hold RELEASED; quota back where it was |
| &nbsp;&nbsp;3. failure at step 2 | the bank declines: 402, only RELEASE_SEATS runs (nothing to refund) |
| &nbsp;&nbsp;4. a compensation fails | step 3 fails while the bank's refunds are down: 502 with `booking_status: COMPENSATING`, seats already released, refund PENDING. Once the bank is back, the recovery job finishes the refund with no further API calls and the booking ends FAILED, 24.4 s later in this run |
| &nbsp;&nbsp;consistency check | all 4 bookings of the run: booking status, hold, payment and notification agree across the four databases; quota `available = total - confirmed`, nothing left HELD |
| [`trace_step3_failure_20261021-171541.jsonl`](trace_step3_failure_20261021-171541.jsonl) | scenario 2 followed through **all five services** by its correlation id: hold, charge at the bank, two failed confirms, refund at the bank, release, the 502, and about two seconds later the BOOKING_FAILED SMS and e-mail sent by the outbox under the same id |
| [`recovery_job_20261021-171541.jsonl`](recovery_job_20261021-171541.jsonl) | the recovery job (correlation id `recovery-...`) retrying the unfinished REFUND_PAYMENT, finishing it, and the outbox sending the refund message |
| [`screenshots/challenge3_saga.png`](screenshots/challenge3_saga.png) | the terminal for the same run, with the command and the time |

## Why we did it this way

- **Orchestration, not choreography.** Choreography needs a broker and spreads the flow over four
  services. booking-service clearly owns the booking lifecycle, so one orchestrator is easier to follow,
  test and explain (report, section 8).
- **No 2PC.** A distributed transaction needs every service up at once and locks across services, and it
  is not available across separate PostgreSQL servers behind REST APIs.
- **Backward recovery.** Retrying step 3 until it works would be nicer for the passenger, but a confirm
  can fail for good, so we undo instead.

## Limits

- Between step 1 and step 3 the seats are HELD (a semantic lock). A booking that ends up compensated did
  block its seats for that time, usually well under a second.
- The recovery job treats a saga with no progress for `SAGA_STUCK_AFTER_SECONDS` (60 s) as dead. Our
  timeouts keep real sagas far below that.
- Seats of a crashed saga come back through the recovery job. If even that fails, inventory-service
  releases holds still HELD after `HOLD_TTL_SECONDS` (10 min). That is a coarse sweep, not the 30-second
  reservation timer of Challenge 3B.
- A compensation can overlap the charge it is undoing (booking gave up waiting, payment did not). Then
  the refund gets `503 PAYMENT_IN_PROGRESS` until the charge settles and the recovery job asks again; if
  no payment exists yet, booking voids it (`POST /api/v1/payments/void`) so a late charge is refused; the
  charge stops retrying once its payment is closed; and a capture that lands on a voided payment is
  refunded straight away.
- Reconciliation relies on the bank's lookup by key including requests it is still processing. Our mock
  bank does that. With a real bank the daily settlement report would be the final check.

## Re-run

```bash
python scripts/challenge3_saga.py                 # about 45 s, scenario 4 waits for the recovery job
python scripts/challenge3_saga.py --skip-recovery # scenarios 1-3 only
```

Also covered by integration tests with fake services:
[`test_saga_db.py`](../../services/booking-service/tests/integration/test_saga_db.py) (order of the
step-3 compensations, failed compensation finished by recovery, saga stuck after payment, unknown hold,
payment voided before it arrives, refund of a charge still in flight) and
[`test_payment_fencing_db.py`](../../services/payment-service/tests/integration/test_payment_fencing_db.py)
(the payment-service side of those overlaps, including a late capture refunded at once).
