# payment-service

Payments and refunds for bookings, and the only service that talks to the bank. Owner: Atharva Kadam.
Port 8003, database `payment_db` (user `payment_svc`), tables `payments`, `refunds`, `idempotency_keys`.

BR4: a booking is charged at most once, and only a captured payment is refunded (full amount, once).

## Why a booking can't be charged twice

1. `POST /api/v1/payments` needs an `Idempotency-Key` (`services/idempotency.py`). A repeat gets the stored
   response, the same key with another body gets 422, and parallel duplicates wait for the first one.
2. `UNIQUE (booking_id)` on `payments`, so a second payment gets 409 `PAYMENT_ALREADY_EXISTS` whatever key
   it uses.
3. Retries to the bank reuse one gateway key (`pay-{payment_id}`), so the bank never creates a second charge.

## Slow or down bank

`clients/gateway.py`: timeout per attempt (`GATEWAY_TIMEOUT_SECONDS`), up to `GATEWAY_MAX_ATTEMPTS`
attempts with exponential backoff + full jitter, no retry on 4xx.

| What happened | Payment | HTTP to booking-service |
|---|---|---|
| bank captured | CAPTURED | 201 |
| bank said no | DECLINED | 402 |
| bank unreachable or 503 every time (nothing processed) | FAILED | 503 |
| sent, never heard back (may or may not be charged) | stays PENDING | 504 `PAYMENT_OUTCOME_UNKNOWN` |

Before refunding a PENDING payment we ask the bank what happened
(`GET /gateway/v1/charges?idempotency_key=`), so booking-service can always just call refund.

## Refund overlapping the charge

booking-service can time out and compensate while we are still retrying the bank:

- Charge may still be running (PENDING, no outcome, younger than the longest a charge can take): refund
  gets `503 PAYMENT_IN_PROGRESS` + `Retry-After`, the saga stays COMPENSATING and tries again later.
- No payment yet: booking-service calls `POST /api/v1/payments/void`, which stores a VOIDED payment
  (`VOIDED_BEFORE_CHARGE`). The late charge then fails on `UNIQUE (booking_id)` and never reaches the bank.
- Payment closed between two attempts: the charge checks for PENDING before each attempt and stops if not.
- Capture lands on a voided payment: refunded right away (VOIDED -> REFUNDED). If the bank refuses, the
  refund row stays PENDING and `POST /payments/{id}/refund` retries it.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/payments` | charge, needs `Idempotency-Key` |
| GET | `/api/v1/payments?booking_id=`, `/api/v1/payments/{id}` | look up |
| POST | `/api/v1/payments/{id}/refund` | full refund, safe to repeat, 503 while the charge may still run |
| POST | `/api/v1/payments/void` | when booking-service sees no payment yet (an existing one comes back unchanged) |
| GET | `/health` | includes a DB check |

Spec: [`docs/openapi/payment-service.yaml`](../../docs/openapi/payment-service.yaml)

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v
```
