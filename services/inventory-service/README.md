# inventory-service

Trains, fares and the Tatkal seat quota. The only service that can hold, confirm or release seats.
Owner: Ratani Snehi Rajesh Darshana.

- Port 8002, database `inventory_db` (user `inventory_svc`), tables `trains`, `seat_inventory`, `seat_holds`.
- `seat_holds.booking_id` is booking-service's id (unique, no foreign key).
- BR1 (no over-allocation) and BR3 (Tatkal fare, `app/domain/fares.py`).

## Taking seats (BR1)

```sql
UPDATE seat_inventory
   SET available_seats = available_seats - :n, version = version + 1, updated_at = now()
 WHERE inventory_id = :inventory_id AND available_seats >= :n
RETURNING available_seats
```

Check and decrement are one statement. A concurrent hold waits for the row lock and re-checks the `WHERE`,
so once the seats are gone it updates nothing and gets `409 SEATS_UNAVAILABLE`. The hold row is inserted
first in the same transaction (`ON CONFLICT (booking_id) DO NOTHING`), so a retry for the same booking
waits for the first attempt and gets its hold back. `CHECK (available_seats >= 0)` only catches bugs.

`HOLD_STRATEGY=naive` swaps in a check-then-update version that oversells. Only for the race demo.

## Hold expiry

`app/services/expiry.py` releases holds still HELD after `HOLD_TTL_SECONDS` (600) and gives the seats back.
CONFIRMED holds are never touched and `SKIP LOCKED` lets several replicas run it. A confirm after expiry
gets `409 HOLD_NOT_ACTIVE` and the saga refunds.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/trains` | catalogue |
| GET | `/api/v1/trains/{train_no}/availability?journey_date=` | seats left and fare per class |
| POST | `/api/v1/inventory` | open a quota for a train/date/class |
| GET | `/api/v1/inventory?train_no=&journey_date=&travel_class=` | counters incl. seats held/confirmed/released |
| POST | `/api/v1/holds` | saga step 1: 201 new, 200 if the booking already has one, 409 sold out |
| GET | `/api/v1/holds?booking_id=`, `/api/v1/holds/{id}` | look up a hold |
| POST | `/api/v1/holds/{id}/confirm` | saga step 3 |
| POST | `/api/v1/holds/{id}/release` | compensation or cancellation, safe to repeat |
| PUT/GET | `/api/v1/admin/hold-strategy` | dev only: `atomic` or `naive` |
| POST/GET/DELETE | `/api/v1/admin/faults` | dev only: fail the next N calls of an operation |
| GET | `/health` | includes a DB check |

Admin routes only exist with `ADMIN_API_ENABLED=true`.
Spec: [`docs/openapi/inventory-service.yaml`](../../docs/openapi/inventory-service.yaml)

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # app + pytest
cp .env.example .env
python -m app
python -m pytest -v    # integration tests (20 threads vs 5 seats) need the DB container
```
