# notification-service

Owner: N Abhishek Reddy

Sends the SMS / e-mail for booking events and keeps a log of them (`notifications` table).
Port 8004, DB `notification_db` (user `notification_svc`).

The provider is fake: "sending" just logs the message with the number masked (`98XXXXXX10`).
`PROVIDER_FAILURE_RATE` (or `PUT /api/v1/admin/provider`) makes it fail.

## Duplicates and failures

booking-service's outbox dispatcher POSTs events here in the background, never inside a booking, so
bookings work even when we're down. It can deliver the same event more than once:

- `UNIQUE (event_id, channel)`: one delivery owns each channel and marks it SENT right after sending.
  A repeat gets `200 {"duplicate": true}` and sends nothing.
- If the provider fails we release only the unsent channels and return 503, so the retry won't resend the SMS.
- A repeat that arrives mid-send waits up to `DUPLICATE_WAIT_SECONDS` (2 s), then gets duplicate, or
  `503 NOTIFICATION_IN_PROGRESS` and the dispatcher retries later.
- Claims expire after `CLAIM_LEASE_SECONDS` (30 s), so one left by a crashed delivery gets taken over.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/notifications` | 201 sent, 200 duplicate, 422 bad event, 503 provider down or still sending |
| GET | `/api/v1/notifications?booking_id=` | messages for a booking |
| PUT | `/api/v1/admin/provider` | dev only, sets the failure rate |
| GET | `/health` | includes a DB check |

Spec: [`docs/openapi/notification-service.yaml`](../../docs/openapi/notification-service.yaml)

## Run / test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v   # integration tests need the postgres container up
```
