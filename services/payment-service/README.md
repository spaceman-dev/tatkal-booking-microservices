# payment-service

Owns payments and refunds for bookings and is the only service that talks to the bank gateway.
Owner: Atharva Kadam.

- **Port** 8003, **database** `payment_db` (user `payment_svc`), tables `payments`, `refunds`,
  `idempotency_keys`.
- Business rule BR4: a booking is charged at most once; only a captured payment can be refunded, in full,
  once.

## Why a booking cannot be charged twice

1. `POST /api/v1/payments` needs an `Idempotency-Key`. The key, a SHA-256 fingerprint of the request and
   the exact response are stored; a repeat gets the stored response, a different body with the same key
   gets 422, and duplicates arriving together wait for the first one (`services/idempotency.py`).
2. `UNIQUE (booking_id)` on `payments`: even with a different key, a second payment for the same booking
   is refused with 409 `PAYMENT_ALREADY_EXISTS`.
3. Every call to the bank carries the same gateway key (`pay-{payment_id}`), so the retries below can
   never create a second charge at the bank.

## Talking to a bank that is slow or down

`clients/gateway.py`: each attempt has a timeout (`GATEWAY_TIMEOUT_SECONDS`), failures are retried up to
`GATEWAY_MAX_ATTEMPTS` with exponential backoff and full jitter, 4xx answers are never retried. The outcome
is classified carefully:

| What happened | Payment status | HTTP to booking-service |
|---|---|---|
| bank captured | CAPTURED | 201 |
| bank declined | DECLINED | 402 |
| bank unreachable or 503 on every attempt (request never processed) | FAILED | 503 |
| a request was sent but never answered | stays PENDING | 504 `PAYMENT_OUTCOME_UNKNOWN` |

A PENDING payment is reconciled with the bank (`GET /gateway/v1/charges?idempotency_key=`) before a refund,
so "refund whatever may have been taken" is always safe.

## When a refund overlaps the charge it undoes

booking-service may give up on a charge (its own timeout) while this service is still retrying the bank,
and start compensating. Each overlap has an answer:

- **Refund while the charge may still be running** (PENDING, no outcome yet, younger than the longest a
  charge can take): `503 PAYMENT_IN_PROGRESS` with `Retry-After`. The saga stays COMPENSATING and its
  recovery job asks again later, when the payment is settled.
- **No payment yet** (the charge request has not arrived): booking-service calls
  `POST /api/v1/payments/void`, which records a VOIDED payment (`VOIDED_BEFORE_CHARGE`). The charge that
  arrives afterwards hits `UNIQUE (booking_id)` and never reaches the bank.
- **Payment closed between two attempts**: before every attempt the charge checks that its payment is
  still PENDING; if not, no further attempt is sent.
- **Capture lands on a payment already voided**: it is refunded at once (VOIDED -> REFUNDED). If the bank
  refuses that refund, the refund row stays PENDING and `POST /payments/{id}/refund` retries it.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/payments` | charge; needs `Idempotency-Key` |
| GET | `/api/v1/payments?booking_id=`, `/api/v1/payments/{id}` | look up |
| POST | `/api/v1/payments/{id}/refund` | full refund; harmless to repeat; 503 while the charge may still be running |
| POST | `/api/v1/payments/void` | compensation when booking-service sees no payment yet; returns an existing payment unchanged |
| GET | `/health` | includes a database check |

Spec: [`docs/openapi/payment-service.yaml`](../../docs/openapi/payment-service.yaml).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v
```
