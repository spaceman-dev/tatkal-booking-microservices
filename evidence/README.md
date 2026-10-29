# Evidence

The Assignment 1 evidence comes from running our scripts against the running system (all five services
and the four PostgreSQL containers), on fresh databases, on 21 Oct 2026, at commit `ccbc7d7`. Each `.txt`
file starts with the command, the time and the commit. The `.jsonl` files are raw log lines exactly as
the services wrote them. `screenshots/` in each folder shows the terminal of the same run (command,
output and time).

The Assignment 2 evidence (images, Docker Compose, the deployment problem, Kubernetes) is in
[deployment](deployment), recorded on 29 Oct 2026. Its own README explains each folder.

| Folder | What it shows |
|---|---|
| [challenge-1-concurrency](challenge-1-concurrency) | 20 bookings at the same moment for 5 seats: 5 confirmed, 15 rejected with 409, all databases agree. The naive check-then-update version sells all 20. |
| [challenge-2-idempotency](challenge-2-idempotency) | the same Idempotency-Key sent again (one after another, after a failure, with a different body, and 10 at once): same answer, one booking, one charge |
| [challenge-3c-saga](challenge-3c-saga) | a failure forced at step 3 is undone (refund, then release), and a compensation that fails is finished later by the recovery job |
| [bank-failures](bank-failures) | slow, down and flaky bank: nobody is charged twice, seats are released when the payment fails |
| [database-per-service](database-per-service) | each service's credentials open only its own database |
| [tests](tests) | 132 unit/integration tests and 7 end-to-end tests passing (133 on 29 Oct, after the Assignment 2 changes) |
| [deployment](deployment) | Assignment 2: the images, `docker compose up --build`, the deployment problem we diagnosed and fixed, and the application on Minikube (scaling, self-healing, rolling update and rollback) |

## Running it again

```bash
docker compose -f docker-compose.db.yml up -d
python scripts/stack.py setup        # once
python scripts/stack.py start
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python scripts/check_db_isolation.py
python scripts/challenge1_concurrency.py --strategy both
python scripts/challenge2_idempotency.py
python scripts/challenge3_saga.py
python scripts/bank_failure_modes.py
python scripts/stack.py test
python -m pytest tests/e2e -v
```

Each run writes new timestamped files next to the ones here.
