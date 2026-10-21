# booking-service

Owns bookings and runs the booking saga. Owner: Dhruv Bhat.

Port 8001, database `booking_db` (user `booking_svc`): `bookings`, `passengers`, `saga_log`,
`idempotency_keys`, `outbox`. We only store the other services' ids (`hold_id`, `payment_id`), never their
data. Rules here: BR2 passengers, BR5 state machine, BR6 compensation, BR7 Tatkal cancellation.

## Saga

| Step | Call | Compensation |
|---|---|---|
| 1. hold seats | inventory `POST /api/v1/holds` | `POST /api/v1/holds/{id}/release` |
| 2. take payment | payment `POST /api/v1/payments`, Idempotency-Key `booking-{id}` | `POST /api/v1/payments/{id}/refund`, or `POST /api/v1/payments/void` if there is no payment yet |
| 3. confirm seats (pivot) | inventory `POST /api/v1/holds/{id}/confirm` | none, the booking stands |
| 4. notify | outbox row, sent to notification `POST /api/v1/notifications` | none, retried until delivered |

Status is committed before each remote call, so the row shows how far a booking got. Background threads:
`services/outbox.py` delivers outbox events, `services/recovery.py` retries failed compensations and undoes
sagas a crash left half way.

`POST /api/v1/bookings` needs an `Idempotency-Key` (`services/idempotency.py`) and a repeat gets the stored
response. If the first request with a key died, the retry takes the key over, but `bookings.idempotency_key`
is UNIQUE so no second saga starts: the retry gets the first request's booking, or
`409 IDEMPOTENCY_REQUEST_IN_PROGRESS` (not stored) while that saga is still running.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/bookings` | runs the saga (201, 402, 404, 409, 422, 502, 503, 504) |
| GET | `/api/v1/bookings` | filters `status`, `train_no`, `journey_date`, `limit` |
| GET | `/api/v1/bookings/{id}` | one booking |
| GET | `/api/v1/bookings/{id}/saga` | saga log |
| POST | `/api/v1/bookings/{id}/cancel` | BR7, safe to repeat |
| GET | `/health` | with DB check |

Spec: [`docs/openapi/booking-service.yaml`](../../docs/openapi/booking-service.yaml), Swagger UI at
http://localhost:8001/docs.

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app          # needs booking_db, plus the other services to actually book
python -m pytest -v    # integration tests skip if booking_db is down
```

Settings: [`.env.example`](.env.example), more in the [root README](../../README.md).
