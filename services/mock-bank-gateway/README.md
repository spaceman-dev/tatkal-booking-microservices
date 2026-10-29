# mock-bank-gateway

Fake UPI / card gateway for payment-service that we can make slow, down or flaky on purpose. No database,
all state is in memory. Port 8099. Owner: Atharva Kadam.

Charges and refunds are deduped on `Idempotency-Key`, also while the first request is still running.
Payers starting with `decline` or `fail` are declined (`INSUFFICIENT_FUNDS`), UPI over Rs 1 lakh too.

| Mode | Effect |
|---|---|
| `normal` | answers after `latency_ms` (80 ms) |
| `slow` | answers after `slow_latency_ms` (4 s), longer than payment-service's 2 s timeout |
| `down` | 503 right away, nothing processed |
| `flaky` | 503 with probability `failure_rate`, otherwise normal |

`affects` limits `down`/`flaky` to charges or refunds only (partial outage).

payment-service calls `POST /gateway/v1/charges`, `POST /gateway/v1/refunds` and
`GET /gateway/v1/charges?idempotency_key=` (reconcile). The scripts flip modes with `PUT /gateway/v1/admin/mode`
and read `GET /gateway/v1/stats` to show no booking was captured twice:

```bash
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "slow", "slow_latency_ms": 3000}'
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "down", "affects": ["refund"]}'
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "normal"}'
curl localhost:8099/gateway/v1/stats
```

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # app + pytest
cp .env.example .env
python -m app
python -m pytest -v
```
