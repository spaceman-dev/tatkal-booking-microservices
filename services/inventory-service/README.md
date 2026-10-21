# inventory-service

Owns the train catalogue, fares and the Tatkal seat quota, and is the only service that can hold, confirm
or release seats. Owner: Ratani Snehi Rajesh Darshana.

- **Port** 8002, **database** `inventory_db` (user `inventory_svc`), tables `trains`, `seat_inventory`,
  `seat_holds`.
- `seat_holds.booking_id` is booking-service's id, stored as a plain UUID with a UNIQUE constraint (one
  hold per booking) and no foreign key.
- Business rules: BR1 (no over-allocation), BR3 (Tatkal fare).

## How overselling is prevented (BR1, Challenge 1)

A hold takes seats with one statement that checks and decrements together:

```sql
UPDATE seat_inventory
   SET available_seats = available_seats - :n, version = version + 1, updated_at = now()
 WHERE inventory_id = :inventory_id AND available_seats >= :n
RETURNING available_seats
```

PostgreSQL locks the row for the update; a concurrent request waits and then re-checks the `WHERE`
clause against the committed value, so it matches no row once the seats are gone and gets
`409 SEATS_UNAVAILABLE`. The hold row is inserted in the same transaction, just before the decrement,
with `ON CONFLICT (booking_id) DO NOTHING`: a retried hold for the same booking waits for the first
attempt's transaction and returns its hold instead of competing for the last seats.
`CHECK (available_seats >= 0)` is only a safety net.

## Holds nobody finishes

The booking saga confirms or releases every hold within seconds, and booking-service's recovery job
compensates sagas that die half way. As a last safety net, `services/expiry.py` releases holds that are
still HELD after `HOLD_TTL_SECONDS` (10 minutes) and returns their seats, checking every
`HOLD_SWEEP_INTERVAL_SECONDS`. It only touches HELD holds (never a paid, CONFIRMED one) and uses
`FOR UPDATE SKIP LOCKED`, so it is safe with several replicas.

`HOLD_STRATEGY=naive` (or `PUT /api/v1/admin/hold-strategy`) switches to a check-then-update version that
exists only so the Challenge 1 script can show the race. It is off by default.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/trains` | catalogue |
| GET | `/api/v1/trains/{train_no}/availability?journey_date=` | seats left and fare per class (BR3) |
| POST | `/api/v1/inventory` | open a Tatkal quota for a train/date/class |
| GET | `/api/v1/inventory?train_no=&journey_date=&travel_class=` | counters, incl. seats held/confirmed/released |
| POST | `/api/v1/holds` | saga step 1; 201 new, 200 if the booking already has a hold, 409 sold out |
| GET | `/api/v1/holds?booking_id=`, `/api/v1/holds/{id}` | look up a hold |
| POST | `/api/v1/holds/{id}/confirm` | saga step 3 (HELD -> CONFIRMED) |
| POST | `/api/v1/holds/{id}/release` | compensation / cancellation; gives seats back exactly once |
| PUT/GET | `/api/v1/admin/hold-strategy` | dev only |
| POST/GET/DELETE | `/api/v1/admin/faults` | dev only: make the next N calls of an operation fail |
| GET | `/health` | includes a database check |

Spec: [`docs/openapi/inventory-service.yaml`](../../docs/openapi/inventory-service.yaml).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v               # includes 20 threads racing for 5 seats against inventory_db
```
