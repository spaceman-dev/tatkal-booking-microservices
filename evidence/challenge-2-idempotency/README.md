# Challenge 2 - Idempotency

**Claim.** Repeating an unsafe request (`POST /api/v1/bookings`, `POST /api/v1/payments`) with the same
`Idempotency-Key` returns the same result and causes no second booking and no second charge - also when
the duplicates arrive at the same moment. The same key with a different body is rejected.

**Mechanism** ([`services/idempotency.py`](../../services/booking-service/app/services/idempotency.py),
identical in booking-service and payment-service):

1. `INSERT (key, request_hash, IN_PROGRESS, lease) ON CONFLICT DO NOTHING` - the primary key makes exactly
   one request the owner, however many arrive together.
2. The owner runs the booking saga and stores the status code and the exact response bytes against the key.
3. Any other request with the key: different SHA-256 fingerprint of method + path + canonical JSON body
   -> `422 IDEMPOTENCY_KEY_REUSED`; key COMPLETED -> the stored response is replayed with
   `Idempotent-Replayed: true`; key IN_PROGRESS -> wait (poll) until it completes, then replay.
4. A missing key -> `400 IDEMPOTENCY_KEY_REQUIRED`.

Behind it are two more layers: payment-service has `UNIQUE (booking_id)` (a different key for the same
booking gets `409 PAYMENT_ALREADY_EXISTS`, BR4), and every call to the bank carries the same gateway key so
retries after a timeout are deduplicated by the bank.

## Files

| File | What it proves |
|---|---|
| [`run_20261021-135032.txt`](run_20261021-135032.txt) | Output of `python scripts/challenge2_idempotency.py`, all 14 checks PASS: |
| &nbsp;&nbsp;A | same key + body sent 3 times: identical responses (same SHA-256 of the body), 2nd and 3rd carry `Idempotent-Replayed: true` |
| &nbsp;&nbsp;B | a **failed** request (declined payment, 402) is replayed too; the bank's request counter does not move on the repeat (32 -> 32) |
| &nbsp;&nbsp;C | same key, different body -> `422 IDEMPOTENCY_KEY_REUSED` |
| &nbsp;&nbsp;D | **10 identical requests released at the same instant**: 1 booking, 1 original response + 9 replays, 10 identical bodies |
| &nbsp;&nbsp;E | no key -> `400 IDEMPOTENCY_KEY_REQUIRED` |
| &nbsp;&nbsp;F | payment-service alone: same key -> same payment; new key, same booking -> `409 PAYMENT_ALREADY_EXISTS` (BR4) |
| &nbsp;&nbsp;database state | one row in `bookings` per key; the stored `idempotency_keys` rows (status, response code, fingerprint, start of the stored body); one payment per booking; the bank recorded one capture per booking |
| [`replay_logs_20261021-135032.jsonl`](replay_logs_20261021-135032.jsonl) | Raw booking-service log lines for those keys: `replaying stored response for idempotency key`, the 9 parallel duplicates logging `same idempotency key is being processed, waiting for its result` within 2 ms, and the `reused with a different payload` warning |

## Design choices

- **Store the response, not just "seen this key"**: the client gets the same answer (PNR, amount) on a
  retry, which is what the client needs after a lost response.
- **Exact bytes** are stored (TEXT, not JSONB, which reorders keys), so replays are byte-for-byte equal.
- **Business failures are stored** (402, 409, 502...) because they are the final answer for that attempt.
  Unexpected crashes release the key so the client can retry.
- **Concurrent duplicates wait** instead of failing immediately, so all of them get the real result. If
  the first request takes longer than `IDEMPOTENCY_WAIT_SECONDS`, the duplicate gets
  `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` with `Retry-After`.
- **Lease**: if the process handling a key dies, the key's lease expires after
  `IDEMPOTENCY_LEASE_SECONDS` and the next request with the key takes over. It does not start a second
  saga: `bookings.idempotency_key` is UNIQUE, so it answers with the booking the first request made (or
  `409 IDEMPOTENCY_REQUEST_IN_PROGRESS` while that saga is still running - an answer that is not stored
  against the key). Every claim carries an owner token, so the old owner can no longer overwrite the
  stored response. A half-finished booking is compensated by the recovery job.

## Limitations

- Keys are kept for `IDEMPOTENCY_TTL_HOURS` (24 h) and then purged. A repeated booking after that still
  finds its booking through `bookings.idempotency_key`, but is answered from the booking, not from the
  stored bytes.
- A duplicate waiting for the first request holds a worker thread while it polls (100 ms interval). With
  a large burst of duplicates this costs threads; a production version would use `LISTEN/NOTIFY` or
  return 409 straight away.
- Keys are global, not per user, because authentication is out of scope.

## Reproduce

```bash
python scripts/challenge2_idempotency.py
```

Covered by integration tests too:
[`test_idempotent_payments_db.py`](../../services/payment-service/tests/integration/test_idempotent_payments_db.py)
(10 threads, one key; 5 threads, five keys, one booking; an expired owner cannot overwrite the new one) and
[`test_idempotent_bookings_db.py`](../../services/booking-service/tests/integration/test_idempotent_bookings_db.py)
(a key taken over after a crash returns the existing booking; two racing requests start one saga).
