# booking-service

Owns bookings and their lifecycle, and orchestrates the booking saga. Owner: Dhruv Bhat.

- **Port** 8001, **database** `booking_db` (user `booking_svc`), tables `bookings`, `passengers`,
  `saga_log`, `idempotency_keys`, `outbox`.
- Keeps only the ids of other services' data: `hold_id` (inventory-service) and `payment_id`
  (payment-service). It never reads their databases.
- Business rules: BR2 (passengers), BR5 (state machine), BR6 (compensation), BR7 (Tatkal cancellation).

## The saga

| Step | Call | Compensation |
|---|---|---|
| 1. hold seats | `POST inventory /api/v1/holds` | `POST /api/v1/holds/{id}/release` |
| 2. take payment | `POST payment /api/v1/payments` (Idempotency-Key `booking-{id}`) | `POST /api/v1/payments/{id}/refund`, or `POST /api/v1/payments/void` if no payment exists yet |
| 3. confirm seats (pivot) | `POST inventory /api/v1/holds/{id}/confirm` | none - after this the booking stands |
| 4. notify | outbox row, delivered later to `POST notification /api/v1/notifications` | none - retried until delivered |

Each state change is committed before the next call, so the database always shows how far a booking got.
Two background threads run inside the service:

- **outbox dispatcher** (`services/outbox.py`) sends pending events to notification-service every
  `OUTBOX_POLL_SECONDS`, with exponential backoff when it is down;
- **saga recovery** (`services/recovery.py`) retries compensations that failed and undoes sagas left half
  way by a crash.

`POST /api/v1/bookings` needs an `Idempotency-Key` (`services/idempotency.py`, Challenge 2). A repeat
replays the stored response. If the request that owned a key died, the next one takes the key over but
does not start a second saga: `bookings.idempotency_key` is UNIQUE, so it answers with the booking the
first request made, or `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` (not stored) while that saga is still running.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/bookings` | runs the saga; needs `Idempotency-Key`; 201, 402, 404, 409, 422, 502, 503, 504 |
| GET | `/api/v1/bookings` | filters: `status`, `train_no`, `journey_date`, `limit` |
| GET | `/api/v1/bookings/{id}` | one booking |
| GET | `/api/v1/bookings/{id}/saga` | every step and compensation, in order |
| POST | `/api/v1/bookings/{id}/cancel` | BR7; harmless to repeat |
| GET | `/health` | includes a database check |

Spec: [`docs/openapi/booking-service.yaml`](../../docs/openapi/booking-service.yaml), live at
http://localhost:8001/docs.

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app                     # needs booking_db and, for bookings, the other services
python -m pytest -v               # unit tests always; integration tests when booking_db is up
```

Configuration is described in [`.env.example`](.env.example) and the root README.
