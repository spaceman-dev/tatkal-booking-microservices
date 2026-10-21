# Tatkal Booking System

Scalable Services (SEZG583), Assignment 1.

Team: Dhruv Bhat (2025TM93139), Ratani Snehi Rajesh Darshana (2025TM93530), Atharva Kadam (2025TM93221),
N Abhishek Reddy (2023MT93400).

Indian Railways opens a small **Tatkal** quota on every train at 10 AM the day before the journey, and a
very large number of people try to book those few seats in the same minute. We picked this because the
problems the assignment asks about show up on their own:

- the last 5 seats must never be sold to 20 people (concurrency),
- a passenger who taps "Pay" twice must not be charged twice (idempotency),
- "money debited but ticket not booked" must end with an automatic refund (saga with compensation),
- the bank tends to be slow or down right when everyone is paying (resilience).

The system is four business services and one mock bank, written in Python (FastAPI), each service with its
own PostgreSQL database and its own credentials. Services talk to each other only over REST.

![Component diagram](docs/diagrams/component.png)

| Service | Port | Database | What it does | Owner |
|---|---|---|---|---|
| [booking-service](services/booking-service) | 8001 | `booking_db` on :5441 | Owns bookings and runs the booking saga: hold seats, take payment, confirm seats, notify | Dhruv Bhat |
| [inventory-service](services/inventory-service) | 8002 | `inventory_db` on :5442 | Owns trains, fares and the Tatkal seat quota; the only place seats are held, confirmed or released | Ratani Snehi Rajesh Darshana |
| [payment-service](services/payment-service) | 8003 | `payment_db` on :5443 | Owns payments and refunds; the only service that talks to the bank | Atharva Kadam |
| [notification-service](services/notification-service) | 8004 | `notification_db` on :5444 | Sends booking SMS / e-mails and keeps the delivery log | N Abhishek Reddy |
| [mock-bank-gateway](services/mock-bank-gateway) | 8099 | none (in memory) | Simulated external UPI/card gateway that can be made slow, down or flaky | Atharva Kadam |

The design report (`docs/report/`) explains why the system is split this way, the data model, the APIs
and the trade-offs. Evidence for the three engineering challenges is in [`evidence/`](evidence).

## 1. Prerequisites

- **Python 3.11 or newer** (we developed on 3.12)
- **Docker** with Compose (Docker Desktop, Colima, Rancher Desktop...). It is only used for the four
  PostgreSQL databases. If you already have PostgreSQL 14+ installed, see
  [Running without Docker](#running-without-docker).
- Git

Free ports needed: 8001-8004, 8099 and 5441-5444.

## 2. Quick start

```bash
git clone <repo-url> tatkal-booking-microservices
cd tatkal-booking-microservices

# 1. the four databases (one container, user and password per service)
docker compose -f docker-compose.db.yml up -d

# 2. one virtualenv per service + a tooling venv for the scripts; creates every .env from .env.example
python scripts/stack.py setup

# 3. start all five services in the background (logs go to ./logs)
python scripts/stack.py start
python scripts/stack.py status
```

On Windows use `py scripts\stack.py ...` (or `python`); everything in `stack.py` is plain Python and works
the same on Windows, macOS and Linux.

### Book a ticket

The demo data has trains 12951, 12627, 22691 and 12009 with Tatkal quota for the next 7 days.

```bash
# what is available tomorrow on the Mumbai Rajdhani (replace the date)
curl "http://localhost:8002/api/v1/trains/12951/availability?journey_date=2026-11-05"

# book two seats - the Idempotency-Key is required because booking is not safe to repeat
curl -X POST http://localhost:8001/api/v1/bookings \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 6f1c0d0b-7d0e-4a5c-9a3c-2f0a1b2c3d4e" \
  -d '{
        "train_no": "12951", "journey_date": "2026-11-05", "travel_class": "3A",
        "passengers": [{"name": "Asha Rao", "age": 34, "gender": "F"},
                       {"name": "Vikram Rao", "age": 36, "gender": "M"}],
        "contact": {"mobile": "9876543210", "email": "asha@example.com"},
        "payment": {"method": "UPI", "payer_ref": "asha@okmock"}
      }'
```

Send the same request again with the same key and you get the same response back with the header
`Idempotent-Replayed: true`, and no second booking or charge. Use a `payer_ref` starting with `decline` to
see a declined payment.

Every service has Swagger UI at `/docs` (for example http://localhost:8001/docs) and the raw spec at
`/openapi.json`. Exported copies of all specs are in [`docs/openapi/`](docs/openapi).

Stop everything with `python scripts/stack.py stop` and `docker compose -f docker-compose.db.yml down`
(add `-v` to delete the data).

## 3. Running one service by hand

Every service is independently runnable from its own folder with its own dependency file:

```bash
cd services/inventory-service
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m app                      # starts on the PORT from .env
```

`python -m app` reads `HOST` and `PORT` from the environment. Tables are created on start-up and
inventory-service also inserts the demo trains.

## 4. Configuration

All configuration comes from environment variables. Each service reads its `.env` file (created from
`.env.example`), and real environment variables override it - which is how Assignment 2 (Docker /
Kubernetes) will configure them. `PORT`, `DATABASE_URL` and the URLs of other services have no defaults,
so a missing value stops the service at start-up instead of quietly using the wrong thing.

| Variable | Service(s) | Default in .env.example | Meaning |
|---|---|---|---|
| `PORT`, `HOST` | all | 8001-8004, 8099 / `0.0.0.0` | where the service listens |
| `DATABASE_URL` | the four business services | `postgresql+psycopg://<svc>_svc:<svc>_dev_pw@localhost:544x/<svc>_db` | the service's own database |
| `LOG_LEVEL` | all | `INFO` | JSON logs on stdout |
| `INVENTORY_SERVICE_URL`, `PAYMENT_SERVICE_URL`, `NOTIFICATION_SERVICE_URL` | booking | `http://localhost:8002` / `8003` / `8004` | other services, HTTP only |
| `INVENTORY_TIMEOUT_SECONDS`, `PAYMENT_TIMEOUT_SECONDS` | booking | `3`, `10` | timeout per downstream call |
| `DOWNSTREAM_RETRIES` | booking | `1` | extra attempts for idempotent calls (hold, confirm, release, refund) |
| `IDEMPOTENCY_LEASE_SECONDS`, `IDEMPOTENCY_WAIT_SECONDS`, `IDEMPOTENCY_TTL_HOURS` | booking, payment | `60`, `20`/`15`, `24` | Idempotency-Key handling |
| `OUTBOX_POLL_SECONDS`, `RECOVERY_INTERVAL_SECONDS`, `COMPENSATION_RETRY_SECONDS`, `SAGA_STUCK_AFTER_SECONDS` | booking | `2`, `10`, `20`, `60` | background jobs |
| `GATEWAY_URL`, `GATEWAY_TIMEOUT_SECONDS`, `GATEWAY_MAX_ATTEMPTS`, `GATEWAY_BACKOFF_BASE_SECONDS`, `GATEWAY_BACKOFF_CAP_SECONDS` | payment | `http://localhost:8099`, `2.0`, `3`, `0.2`, `2.0` | bank calls: timeout, retries, exponential backoff with full jitter |
| `SEED_DEMO_DATA`, `SEED_DAYS` | inventory | `true`, `7` | demo trains and quota |
| `HOLD_STRATEGY`, `NAIVE_THINK_TIME_MS` | inventory | `atomic`, `150` | `naive` exists only to demonstrate the race in Challenge 1 |
| `HOLD_EXPIRY_ENABLED`, `HOLD_TTL_SECONDS`, `HOLD_SWEEP_INTERVAL_SECONDS` | inventory | `true`, `600`, `30` | fallback: holds still HELD after 10 minutes are released |
| `ADMIN_API_ENABLED` | inventory, notification | `true` | dev-only `/api/v1/admin/*` (fault injection, strategy switch). Turn off outside a dev machine |
| `PROVIDER_FAILURE_RATE`, `PROVIDER_LATENCY_MS` | notification | `0.0`, `30` | simulated SMS provider |
| `CLAIM_LEASE_SECONDS`, `DUPLICATE_WAIT_SECONDS` | notification | `30`, `2.0` | how long a message being sent stays claimed; how long a duplicate event waits for it |
| `GATEWAY_MODE`, `GATEWAY_LATENCY_MS`, `GATEWAY_SLOW_LATENCY_MS`, `GATEWAY_FAILURE_RATE` | mock-bank-gateway | `normal`, `80`, `4000`, `0.5` | start-up behaviour; can be changed at runtime with `PUT /gateway/v1/admin/mode` |

Example: `HOLD_STRATEGY=naive python scripts/stack.py restart inventory-service`.

## 5. Tests

```bash
python scripts/stack.py test        # unit + integration tests of every service (databases must be up)
```

- **Unit tests** (`services/*/tests/unit`) cover every business rule BR1-BR7 and need nothing running.
- **Integration tests** (`services/*/tests/integration`) use the service's real PostgreSQL database:
  20 threads racing for 5 seats, a retried hold overlapping the first one, parallel duplicate payments,
  refunds racing a charge that is still running, duplicate and partly failed notification events, the
  saga with forced failures, and recovery after a simulated crash. They skip themselves if the database
  is down.
- **End-to-end test** of the main transaction, through the public APIs of all services (stack running):

```bash
source .venv/bin/activate           # tooling venv created by setup; Windows: .venv\Scripts\activate
python -m pytest tests/e2e -v
```

## 6. Engineering challenge scripts

With the stack running and the tooling venv active:

| Script | Challenge | What it shows |
|---|---|---|
| `python scripts/challenge1_concurrency.py --strategy both` | 1. Concurrency | 20 parallel bookings for 5 seats: the naive check-then-update oversells, the real code confirms 5 and rejects 15 with 409 |
| `python scripts/challenge2_idempotency.py` | 2. Idempotency | sequential, failed, mismatched and 10 simultaneous repeats of one key; one booking, one charge |
| `python scripts/challenge3_saga.py` | 3C. Saga with compensation | failure forced at step 3 is undone (refund, then release); a failed compensation is finished by the recovery job (waits ~30 s) |
| `python scripts/bank_failure_modes.py` | condition (e) | one booking each with the bank normal, slow, down and flaky: one capture at most, seats released on failure |
| `python scripts/check_db_isolation.py` | database per service | every service's credentials against every database; only its own connects |

Each script prints its result and saves a timestamped copy under `evidence/`. Useful options:
`--capacity`, `--requests`, `--seats` (challenge 1) and `--skip-recovery` (challenge 3).

## 7. Business rules

| Id | Rule | Where |
|---|---|---|
| BR1 | Seats of a train/date/class are never over-allocated; a request that does not fully fit is refused | inventory-service |
| BR2 | A Tatkal booking has 1 to 4 passengers, each at least 5 years old | booking-service |
| BR3 | The fare is computed on the server: base fare + Tatkal charge (class percentage, clamped to min/max) | inventory-service |
| BR4 | A booking is charged at most once; only money that was really taken can be refunded, in full, once | payment-service |
| BR5 | A booking only moves along its state machine | booking-service |
| BR6 | If the saga fails, whatever may have happened is undone newest-first: refund, then release | booking-service |
| BR7 | Cancelling a confirmed Tatkal ticket returns the seats but refunds nothing | booking-service |

[`docs/business-rules.md`](docs/business-rules.md) maps every rule to the code and to its tests.

## 8. Repository layout

```
docker-compose.db.yml        four PostgreSQL containers, one per service
services/
  booking-service/           app/{api,services,domain,repository,clients,core}, tests/, requirements.txt, .env.example
  inventory-service/         same layout
  payment-service/           same layout
  notification-service/      same layout
  mock-bank-gateway/         small stand-alone mock, no database
scripts/                     stack.py, challenge scripts, export_openapi.py
tests/e2e/                   end-to-end test of the main transaction
docs/                        report, diagrams (Mermaid source + PNG/SVG), OpenAPI specs, business rules
evidence/                    one folder per challenge with outputs and a README linking files to claims
```

Inside a service: `api/` is HTTP only (routes, request/response models), `services/` holds the use cases
(the saga, holds, payments), `domain/` holds the business rules as plain functions, `repository/` holds the
tables and SQL, and `clients/` the HTTP clients for other services. `core/` is small shared plumbing
(logging, correlation id, error format); it is copied into each service on purpose so that no service
depends on a shared library.

## 9. Troubleshooting

- **A service does not become healthy**: look at `logs/<service>.log`. Most often the database is not up
  (`docker compose -f docker-compose.db.yml ps`) or a port is taken.
- **Port already in use**: change `PORT` in that service's `.env`, and the matching URL in
  `services/booking-service/.env` if it is one booking-service calls.
- **Start again from empty databases**: `docker compose -f docker-compose.db.yml down -v`, then `up -d`.
- **Python version errors**: `python --version` must be 3.11+; on Windows try `py -3.12 scripts\stack.py setup`.

### Running without Docker

With a local PostgreSQL 14+, create the four databases and users once (as a superuser):

```sql
CREATE USER booking_svc PASSWORD 'booking_dev_pw';           CREATE DATABASE booking_db OWNER booking_svc;
CREATE USER inventory_svc PASSWORD 'inventory_dev_pw';       CREATE DATABASE inventory_db OWNER inventory_svc;
CREATE USER payment_svc PASSWORD 'payment_dev_pw';           CREATE DATABASE payment_db OWNER payment_svc;
CREATE USER notification_svc PASSWORD 'notification_dev_pw'; CREATE DATABASE notification_db OWNER notification_svc;
REVOKE CONNECT ON DATABASE booking_db, inventory_db, payment_db, notification_db FROM PUBLIC;
```

Then change `DATABASE_URL` in each service's `.env` to port 5432.

## 10. Notes for Assignment 2

The services are already shaped for containers: configuration only through environment variables,
`GET /health` checks the database, logs are JSON on stdout with a correlation id, the services keep no
state in memory (except the mock bank), and start-up waits for the database and creates its tables.
Assignment 2 adds a Dockerfile per service, a full compose file and Kubernetes manifests for Minikube.
