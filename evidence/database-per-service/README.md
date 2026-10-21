# Database per service

**Claim.** Each of the four business services has its own database, user and password, and cannot open
any other service's database.

| File | What it proves |
|---|---|
| [`isolation_20261021-135009.txt`](isolation_20261021-135009.txt) | Output of `python scripts/check_db_isolation.py`: every service's credentials tried against every database. Only the diagonal (own database) connects; the other 12 attempts fail with `password authentication failed`. |

The databases are four separate PostgreSQL containers (`docker-compose.db.yml`), i.e. physical
separation. There are no cross-service foreign keys: other services' entities are kept as plain ids
(`seat_holds.booking_id`, `payments.booking_id`, `bookings.hold_id`, `bookings.payment_id`,
`notifications.booking_id`) and reached only through the owning service's REST API.
