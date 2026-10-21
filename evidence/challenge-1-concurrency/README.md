# Challenge 1 - Concurrency control

**Claim.** When many people try to book the last Tatkal seats at the same moment, the quota is never
over-allocated: with a capacity of N, exactly N bookings succeed, every other request is rejected with
`409 SEATS_UNAVAILABLE`, and the seat counter, the hold rows, the bookings and the payments all agree.

**Mechanism.** inventory-service takes seats with one atomic conditional update
([`inventory_repo.py`](../../services/inventory-service/app/repository/inventory_repo.py)):

```sql
UPDATE seat_inventory SET available_seats = available_seats - :n, version = version + 1
 WHERE inventory_id = :id AND available_seats >= :n
RETURNING available_seats
```

The hold row is inserted in the same transaction. `CHECK (available_seats >= 0)` is a second line of
defence, and `UNIQUE (booking_id)` on `seat_holds` stops one booking from holding seats twice.

## Files

| File | What it proves |
|---|---|
| [`run_both_20261021-124246.txt`](run_both_20261021-124246.txt) | Output of `python scripts/challenge1_concurrency.py --strategy both`. Two runs against fresh quotas of 5 seats, 20 requests each, released together by a barrier, going through the real booking API (hold, pay, confirm). |
| &nbsp;&nbsp;section `strategy=naive` | The **failure case we prevent**. With the check-then-update version (dev switch `HOLD_STRATEGY=naive`), all 20 of 20 requests were confirmed for 5 seats, 20 customers were charged (Rs 22,100 for seats worth Rs 5,525), and the counter ends at 4 while 20 holds exist: a lost update. |
| &nbsp;&nbsp;section `strategy=atomic` | The real code. 5 x `201 CONFIRMED`, 15 x `409 SEATS_UNAVAILABLE`. Final DB state: `available_seats = 0`, 5 CONFIRMED holds, 5 CONFIRMED + 15 FAILED bookings, 5 CAPTURED payments. All four checks PASS. |
| [`inventory_logs_20261021-124246.jsonl`](inventory_logs_20261021-124246.jsonl) | Raw inventory-service log lines for both runs: every `seats held` line with `seats_left`, and every `POST /api/v1/holds -> 409`. |

The final database state is printed in the output by querying each service's own database with that
service's credentials (inventory: quota and holds; booking: bookings; payment: payments). The payment
query uses the booking ids read from booking-service - there is no cross-database join.

## Why a plain check-then-update in application code fails

```
available = SELECT available_seats ...     -- request A reads 5, request B reads 5
if available >= 1:                         -- both pass
    UPDATE ... SET available_seats = 4     -- both write 4: one decrement is lost
```

The read and the write are separate steps, so any number of requests can read the same value in between
and each "sees" a free seat. Under READ COMMITTED PostgreSQL does not stop this - the second UPDATE simply
overwrites the first. More holds are created than seats exist, and the counter stops matching the holds.
The CHECK constraint does not help either because the counter never goes negative; it is just wrong.
The naive run above shows exactly this.

With the conditional UPDATE, the check and the write are one statement. The first transaction locks the
row; the others wait, and when they get the lock PostgreSQL re-evaluates `available_seats >= :n` against
the committed value, so once the seats are gone the UPDATE matches no row and we return 409.

## Alternatives we considered

- `SELECT ... FOR UPDATE` then update: also correct, but holds the lock across a round trip from Python.
- Optimistic locking with the `version` column (`WHERE version = :seen`): correct, but under a Tatkal
  rush most requests would fail the version check and have to retry, even when seats are still free.
- One row per seat with `UNIQUE` / `SKIP LOCKED`: needed if passengers pick seat numbers; we only sell
  counts per class, so a counter is simpler.

## Limitations

The quota row is a single hot row - every booking for one train/date/class serialises on it. For a few
hundred concurrent requests that is fine (each lock is held for a few milliseconds); at much larger scale
the quota would have to be split into buckets or moved into an in-memory counter in front of the database.

## Reproduce

```bash
python scripts/challenge1_concurrency.py --strategy both            # naive, then atomic
python scripts/challenge1_concurrency.py --capacity 1 --requests 30  # last seat
python scripts/challenge1_concurrency.py --seats 2                   # 2 seats per booking
```

Also covered by an integration test without HTTP:
[`test_br1_concurrent_holds.py`](../../services/inventory-service/tests/integration/test_br1_concurrent_holds.py).
