# Challenge 2 - idempotency

**Claim:** sending an unsafe request (`POST /api/v1/bookings`, `POST /api/v1/payments`) again with the
same `Idempotency-Key` gives back the same result and creates nothing new: no second booking and no
second charge, also when the copies arrive at the same moment. The same key with a different body is
rejected.

How it works ([`services/idempotency.py`](../../services/booking-service/app/services/idempotency.py),
the same file in booking-service and payment-service):

1. `INSERT (key, request_hash, IN_PROGRESS, lease) ON CONFLICT DO NOTHING`. The primary key lets one
   request become the owner, however many arrive together.
2. The owner runs the booking and stores the status code and the exact response bytes for the key.
3. Another request with the key: different SHA-256 fingerprint (method + path + body) gives
   `422 IDEMPOTENCY_KEY_REUSED`; a COMPLETED key replays the stored response with
   `Idempotent-Replayed: true`; an IN_PROGRESS key makes it wait, then replay.
4. No key at all gives `400 IDEMPOTENCY_KEY_REQUIRED`.

Two more layers sit behind this: `UNIQUE (booking_id)` in payment-service (a new key for the same
booking gets `409 PAYMENT_ALREADY_EXISTS`, BR4), and every call to the bank carries the same gateway key,
so the bank drops retries after a timeout.

## Files

| File | What it shows |
|---|---|
| [`run_20261021-171523.txt`](run_20261021-171523.txt) | output of `python scripts/challenge2_idempotency.py`, all 14 checks PASS: |
| &nbsp;&nbsp;A | same key and body sent 3 times: identical responses (same SHA-256 of the body), the 2nd and 3rd have `Idempotent-Replayed: true` |
| &nbsp;&nbsp;B | a **failed** request (declined payment, 402) is replayed too, and the bank's request counter does not move on the repeat (26 -> 27 -> 27) |
| &nbsp;&nbsp;C | same key, different body: `422 IDEMPOTENCY_KEY_REUSED` |
| &nbsp;&nbsp;D | **10 identical requests at the same moment**: 1 booking, 1 original response + 9 replays, all 10 bodies identical |
| &nbsp;&nbsp;E | no key: `400 IDEMPOTENCY_KEY_REQUIRED` |
| &nbsp;&nbsp;F | payment-service on its own: same key gives the same payment, a new key for the same booking gets `409 PAYMENT_ALREADY_EXISTS` (BR4) |
| &nbsp;&nbsp;database state | one `bookings` row per key, the stored `idempotency_keys` rows (status, response code, fingerprint, start of the stored body), one payment per booking, one capture per booking at the bank |
| [`replay_logs_20261021-171523.jsonl`](replay_logs_20261021-171523.jsonl) | raw booking-service log lines for these keys: `replaying stored response for idempotency key`, the 9 parallel duplicates logging `same idempotency key is being processed, waiting for its result` within 1 ms, and the `reused with a different payload` warning |
| [`screenshots/challenge2_idempotency.png`](screenshots/challenge2_idempotency.png) | the terminal for the same run, with the command and the time |

## Choices we made

- We store the whole response, not just "seen this key", because after a lost response the client needs
  the same answer (PNR, amount).
- The body is stored as text, not JSONB (JSONB reorders keys), so a replay is byte-for-byte the same.
- Business failures (402, 409, 502...) are stored too, they are the final answer for that request.
  Unexpected crashes release the key so the client can try again.
- Duplicates that arrive while the first request runs wait for it, so they get the real result. After
  `IDEMPOTENCY_WAIT_SECONDS` they get `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` with `Retry-After`.
- If the process handling a key dies, its lease (`IDEMPOTENCY_LEASE_SECONDS`) runs out and the next
  request takes the key over. It does not start a second saga: `bookings.idempotency_key` is UNIQUE, so it
  answers with the booking the first request made (or `409 IDEMPOTENCY_REQUEST_IN_PROGRESS`, not stored,
  while that saga is still running). An owner token stops the old owner from overwriting the new record.

## Limits

- Keys are deleted after `IDEMPOTENCY_TTL_HOURS` (24 h). A repeated booking after that is still found
  through `bookings.idempotency_key`, but answered from the booking, not from the stored bytes.
- A waiting duplicate holds a worker thread while it polls every 100 ms. With a big burst of duplicates
  that costs threads, `LISTEN/NOTIFY` would be better.
- Keys are global, not per user, since there is no login in this assignment.

## Re-run

```bash
python scripts/challenge2_idempotency.py
```

Also covered by integration tests:
[`test_idempotent_payments_db.py`](../../services/payment-service/tests/integration/test_idempotent_payments_db.py)
(10 threads with one key, 5 threads with five keys for one booking, an expired owner cannot overwrite
the new one) and
[`test_idempotent_bookings_db.py`](../../services/booking-service/tests/integration/test_idempotent_bookings_db.py)
(a key taken over after a crash returns the existing booking, two racing requests start one saga).
