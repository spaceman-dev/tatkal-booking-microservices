# Condition (e) - a slow or failing bank

**Claim.** The external bank (our `mock-bank-gateway`) can be slow, down or flaky without causing a double
charge, a hung request or seats stuck on hold.

payment-service gives every bank call a 2 s timeout and up to 3 attempts with exponential backoff and full
jitter, always reusing the same gateway idempotency key
([`clients/gateway.py`](../../services/payment-service/app/clients/gateway.py)).

| File | What it proves |
|---|---|
| [`run_20261021-125151.txt`](run_20261021-125151.txt) | Output of `python scripts/bank_failure_modes.py`, one booking per bank behaviour, all checks PASS. **slow (3 s)**: the first attempt timed out, the retry got the bank's result - CONFIRMED after 3.1 s with 2 attempts and exactly 1 capture at the bank. **down**: 503 `PAYMENT_UNAVAILABLE` after 0.4 s (3 quick attempts), payment FAILED, seats RELEASED, nothing captured. **flaky (60 % of calls fail)**: retries succeeded on attempt 2 or 3; every booking captured exactly once. |

This is the behaviour Challenge 3C relies on: a failure the bank reports clearly ("down", "declined")
only needs the seats released, while an unanswered request is treated as "may have been charged" and
refunded after reconciling with the bank (unit-tested in
[`test_gateway_client.py`](../../services/payment-service/tests/unit/test_gateway_client.py)).
