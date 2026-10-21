# The bank is slow, down or flaky (condition e)

**Claim:** our mock bank (`mock-bank-gateway`) can be slow, down or flaky without anyone being charged
twice, without requests hanging, and without seats staying blocked.

payment-service gives every bank call a 2 s timeout and up to 3 attempts with exponential backoff and
full jitter, always with the same gateway idempotency key
([`clients/gateway.py`](../../services/payment-service/app/clients/gateway.py)).

| File | What it shows |
|---|---|
| [`run_20261021-171618.txt`](run_20261021-171618.txt) | output of `python scripts/bank_failure_modes.py`: one booking per bank mode, all checks PASS. **Slow (3 s):** the first attempt timed out and the retry got the bank's result, so the booking was CONFIRMED after 3.1 s with 2 attempts and 1 capture. **Down:** 503 `PAYMENT_UNAVAILABLE` after 0.5 s (3 quick attempts), payment FAILED, seats RELEASED, nothing captured. **Flaky (60 % of calls fail):** all four bookings went through, two of them on the second attempt, and each was captured once |
| [`screenshots/bank_failure_modes.png`](screenshots/bank_failure_modes.png) | the terminal for the same run, with the command and the time |

The saga depends on this. A failure the bank reports clearly (down, declined) only needs the seats
released. A request that was sent but never answered is treated as "may have been charged" and is
refunded after checking with the bank (unit tests in
[`test_gateway_client.py`](../../services/payment-service/tests/unit/test_gateway_client.py)).
