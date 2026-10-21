# Test runs

| File | What it shows |
|---|---|
| [`service-tests_20261021-171640.txt`](service-tests_20261021-171640.txt) | `python scripts/stack.py test`: each service's own unit and integration tests, all passing: mock-bank-gateway 8, inventory 27, payment 41, notification 15, booking 41 (132 tests). The integration tests use the service's real PostgreSQL, in a throwaway schema |
| [`e2e_20261021-171655.txt`](e2e_20261021-171655.txt) | `python -m pytest tests/e2e -v` with the whole stack running: the main transaction end to end (book, pay, confirm, notify, correlation id in every service's log), step-3 failure with refund and release, declined payment, sold-out train, Tatkal cancellation, BR2 violations. 7 passed |
| [`screenshots/service_tests.png`](screenshots/service_tests.png), [`screenshots/e2e_tests.png`](screenshots/e2e_tests.png) | the two runs in the terminal (both were saved with `tee`, so the files above are the same output) |

Both ran at commit `ccbc7d7`. The tests for each business rule are listed in
[`docs/business-rules.md`](../../docs/business-rules.md).
