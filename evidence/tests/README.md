# Test runs

| File | What it proves |
|---|---|
| [`service-tests_20261021-124234.txt`](service-tests_20261021-124234.txt) | `python scripts/stack.py test`: every service's own suite, unit + integration, all passing - mock-bank-gateway 8, inventory 25, payment 30, notification 12, booking 35 (110 tests). Integration tests run against the real PostgreSQL of the service, in a throwaway schema. |
| [`e2e_20261021-124239.txt`](e2e_20261021-124239.txt) | `python -m pytest tests/e2e -v` with the whole stack running: the main transaction end to end (book, pay, confirm, notify, correlation id in every service's log), step-3 failure with refund and release, declined payment, sold-out train, Tatkal cancellation, BR2 violations - 7 passed. |

Business-rule tests (one or more per rule) are listed in [`docs/business-rules.md`](../../docs/business-rules.md).
