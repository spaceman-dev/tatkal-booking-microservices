# notification-service

Turns booking events into SMS and e-mail messages and keeps the delivery log. Owner: N Abhishek Reddy.

- **Port** 8004, **database** `notification_db` (user `notification_svc`), table `notifications`.
- The provider is simulated: a "sent" message is a log line with the number masked (`98XXXXXX10`).
  `PROVIDER_FAILURE_RATE` (or `PUT /api/v1/admin/provider`) makes it fail on purpose.

## Called asynchronously, safe against duplicates

booking-service never calls this service inside a booking request. It writes an event to its outbox in the
same transaction as the booking change, and a background dispatcher delivers it here. The dispatcher may
deliver an event more than once (for example if it crashes after we answered), so this service is an
idempotent consumer: `UNIQUE (event_id, channel)` lets exactly one delivery through, and repeats get
`200 {"duplicate": true}` without a second SMS. If the provider fails, the claim is removed and 503 is
returned, so the dispatcher's retry can send it. If this service is down, bookings still complete and the
messages go out when it comes back.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/notifications` | 201 sent, 200 duplicate, 422 bad event, 503 provider down |
| GET | `/api/v1/notifications?booking_id=` | messages for a booking |
| PUT | `/api/v1/admin/provider` | dev only: failure rate |
| GET | `/health` | includes a database check |

Spec: [`docs/openapi/notification-service.yaml`](../../docs/openapi/notification-service.yaml).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v
```
