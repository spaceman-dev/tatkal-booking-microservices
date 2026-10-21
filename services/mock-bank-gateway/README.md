# mock-bank-gateway

A stand-in for an external UPI / card payment gateway (assignment condition (e): a dependency that can be
slow or fail). It is not one of our business services: there is no database and all state is in memory,
the way a third-party sandbox would look to us. Owner: Atharva Kadam.

- **Port** 8099.
- Deduplicates charges and refunds on the `Idempotency-Key` header, including requests that arrive while
  the first one is still being processed.
- Payer ids starting with `decline` or `fail` are declined (`INSUFFICIENT_FUNDS`); UPI amounts above
  Rs 1 lakh are declined (`UPI_LIMIT_EXCEEDED`).

## Behaviour switch

| Mode | Effect |
|---|---|
| `normal` | answers after `latency_ms` (80 ms) |
| `slow` | answers after `slow_latency_ms` (4 s) - longer than payment-service's 2 s timeout |
| `down` | 503 straight away, nothing processed |
| `flaky` | 503 with probability `failure_rate`, otherwise normal |

```bash
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "slow", "slow_latency_ms": 3000}'
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "down", "affects": ["refund"]}'
curl -X PUT localhost:8099/gateway/v1/admin/mode -H "Content-Type: application/json" -d '{"mode": "normal"}'
curl localhost:8099/gateway/v1/stats      # requests, charges created, replays, captures per booking
```

`affects` limits `down`/`flaky` to charges or refunds only (a partial outage).

## Run and test

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app
python -m pytest -v
```
