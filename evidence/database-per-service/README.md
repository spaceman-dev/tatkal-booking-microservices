# Database per service

**Claim:** each of the four business services has its own database, user and password, and cannot open
any other service's database.

| File | What it shows |
|---|---|
| [`isolation_20261021-171516.txt`](isolation_20261021-171516.txt) | output of `python scripts/check_db_isolation.py`: every service's credentials tried against every database. Only the diagonal (own database) connects, the other 12 attempts fail with `password authentication failed` |
| [`screenshots/check_db_isolation.png`](screenshots/check_db_isolation.png) | the terminal for the same run, with the command and the time |

The four databases are separate PostgreSQL containers (`docker-compose.db.yml`). There are no foreign
keys between services. Other services' entities are kept as plain ids (`seat_holds.booking_id`,
`payments.booking_id`, `bookings.hold_id`, `bookings.payment_id`, `notifications.booking_id`) and are
only reached through the owning service's REST API.
