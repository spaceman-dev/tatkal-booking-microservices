# Evidence

Every file here was produced by running a script from this repository against the running system (all
five services plus the four PostgreSQL containers) on 21 Oct 2026. Text outputs start with the exact
command, the local and UTC time and the git commit they ran on. Raw service logs (JSON lines, exactly as
the services wrote them) are saved next to the output they belong to.

| Folder | Claim | Main files |
|---|---|---|
| [challenge-1-concurrency](challenge-1-concurrency) | 20 simultaneous bookings for 5 seats: exactly 5 succeed, 15 get 409, databases consistent. The naive check-then-update oversells (20 of 20 sold). | `run_both_*.txt`, `inventory_logs_*.jsonl` |
| [challenge-2-idempotency](challenge-2-idempotency) | Repeating a booking with the same Idempotency-Key returns the same response and creates nothing new - sequential repeats, a failed request, a mismatched body and 10 simultaneous duplicates. | `run_*.txt`, `replay_logs_*.jsonl` |
| [challenge-3c-saga](challenge-3c-saga) | A failure forced at step 3 is undone by refunding the payment (step 2) and releasing the seats (step 1); a compensation that fails is finished later by the recovery job. | `run_*.txt`, `trace_step3_failure_*.jsonl`, `recovery_job_*.jsonl` |
| [bank-failures](bank-failures) | Condition (e): with the bank slow, down or flaky, bookings either succeed with exactly one capture or fail cleanly with their seats released. | `run_*.txt` |
| [database-per-service](database-per-service) | Each service's database only accepts that service's credentials. | `isolation_*.txt` |
| [tests](tests) | 132 unit/integration tests across the five services and 7 end-to-end tests pass. | `service-tests_*.txt`, `e2e_*.txt` |

## Re-running

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

Each run adds new timestamped files; the older ones can be deleted.

## Screenshots

Terminal screenshots (command + output + clock visible) are taken while recording the demo video and are
added to a `screenshots/` folder inside each challenge folder, named after the scenario they show. The
text files above are the complete, unedited output of the same commands.
