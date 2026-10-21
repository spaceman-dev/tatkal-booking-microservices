# Challenge 1 - concurrency (no overselling)

**Claim:** when 20 people book the last 5 Tatkal seats at the same moment, 5 get a ticket and the other
15 get `409 SEATS_UNAVAILABLE`. Afterwards the seat counter, the holds, the bookings and the payments all
agree.

inventory-service takes seats with one conditional UPDATE
([`inventory_repo.py`](../../services/inventory-service/app/repository/inventory_repo.py)):

```sql
UPDATE seat_inventory SET available_seats = available_seats - :n, version = version + 1
 WHERE inventory_id = :id AND available_seats >= :n
RETURNING available_seats
```

The hold row goes into the same transaction. `UNIQUE (booking_id)` on `seat_holds` means a retried hold
for the same booking gets the existing hold instead of more seats. `CHECK (available_seats >= 0)` is only
a backup.

## Files

| File | What it shows |
|---|---|
| [`run_both_20261021-171440.txt`](run_both_20261021-171440.txt) | output of `python scripts/challenge1_concurrency.py --strategy both`: two runs of 20 parallel bookings (released together by a barrier) against new 5-seat quotas, through the real booking API |
| &nbsp;&nbsp;section `strategy=naive` | what happens without an atomic update (dev switch `HOLD_STRATEGY=naive`): all 20 confirmed for 5 seats, Rs 22,100 charged for Rs 5,525 worth of seats, counter left at 4 next to 20 holds. Three checks FAIL |
| &nbsp;&nbsp;section `strategy=atomic` | the real code: 5 x `201 CONFIRMED`, 15 x `409 SEATS_UNAVAILABLE`. Database afterwards: `available_seats = 0`, 5 CONFIRMED holds, 5 CONFIRMED and 15 FAILED bookings, 5 CAPTURED payments. All four checks PASS |
| [`inventory_logs_20261021-171440.jsonl`](inventory_logs_20261021-171440.jsonl) | raw inventory-service log lines of both runs: every `seats held` line with `seats_left`, and every 409 |
| [`screenshots/challenge1_naive_then_atomic.png`](screenshots/challenge1_naive_then_atomic.png) | the terminal for the same run, with the command and the time |

The database state in the output is read from each service's own database with that service's own
credentials. Payments are matched to bookings by id in Python, there is no cross-database join.

## Why check-then-update in the code fails

```
available = SELECT available_seats ...     -- request A reads 5, request B reads 5
if available >= 1:                         -- both pass
    UPDATE ... SET available_seats = 4     -- both write 4, one decrement is lost
```

Reading and writing are separate steps, so many requests can read the same value in between, and each
one thinks a seat is free. Under READ COMMITTED PostgreSQL does not stop this, the second UPDATE just
overwrites the first. We get more holds than seats and a counter that no longer matches them. The CHECK
constraint does not notice because the counter never goes negative. The naive run above shows it.

With the conditional UPDATE the check and the write are one statement. The first transaction locks the
row. The others wait, and when they get the lock PostgreSQL re-checks `available_seats >= :n` against the
committed value, so once the seats are gone the UPDATE matches no row and we return 409.

## Other options we looked at

- `SELECT ... FOR UPDATE`, then update: also correct, but the lock is held during a round trip to Python.
- Optimistic locking on `version`: correct too, but in a Tatkal rush most requests would fail the version
  check and retry even while seats are left.
- One row per seat with `SKIP LOCKED`: the right model if passengers pick seats. We sell counts, so a
  counter is simpler.

## Limits

Every booking for one train/date/class queues on the same row lock (a few milliseconds each). That is
fine for hundreds of requests at once. At real Tatkal scale we would split the quota into buckets or put
a waiting room in front.

## Re-run

```bash
python scripts/challenge1_concurrency.py --strategy both            # naive, then atomic
python scripts/challenge1_concurrency.py --capacity 1 --requests 30  # the last seat
python scripts/challenge1_concurrency.py --seats 2                   # 2 seats per booking
```

The same race also runs as an integration test, without HTTP:
[`test_br1_concurrent_holds.py`](../../services/inventory-service/tests/integration/test_br1_concurrent_holds.py).
