# Business rules and where they live

Each rule belongs to one service and is written as plain Python in that service's `domain/` package, with
a database constraint as a backup where it made sense. The last column lists the tests that fail if the
rule breaks.

| Id | Rule | Owner | Enforced in code | Tests |
|---|---|---|---|---|
| **BR1** | The seats of a train/date/class are never over-allocated. A request that does not fully fit is refused (no partial allocation). | inventory-service | Atomic conditional `UPDATE` in [`inventory_repo.py` `decrement_if_available`](../services/inventory-service/app/repository/inventory_repo.py); used by [`holds.py` `HoldService.place_hold`](../services/inventory-service/app/services/holds.py); seat-count limits and the hold state machine in [`domain/allocation.py`](../services/inventory-service/app/domain/allocation.py); DB constraints `ck_inventory_available_non_negative` and `uq_seat_holds_booking` in [`tables.py`](../services/inventory-service/app/repository/tables.py) | unit: [`test_br1_allocation.py`](../services/inventory-service/tests/unit/test_br1_allocation.py); integration (real Postgres, 20 threads vs 5 seats): [`test_br1_concurrent_holds.py`](../services/inventory-service/tests/integration/test_br1_concurrent_holds.py), retried hold and hold expiry: [`test_hold_retry_and_expiry.py`](../services/inventory-service/tests/integration/test_hold_retry_and_expiry.py); e2e: `test_sold_out_train_is_refused_before_any_payment`; [Challenge 1 script](../scripts/challenge1_concurrency.py) |
| **BR2** | A Tatkal booking has 1 to 4 passengers, each aged 5 or more (children under 5 travel without a berth and are not booked). | booking-service | [`domain/booking_rules.py` `validate_passengers`](../services/booking-service/app/domain/booking_rules.py), called by [`api/routes.py` `create_booking`](../services/booking-service/app/api/routes.py) before anything is stored; DB checks `ck_bookings_passenger_count`, `ck_passengers_min_age` | unit: [`test_br2_passengers.py`](../services/booking-service/tests/unit/test_br2_passengers.py); e2e: `test_business_rule_violations_are_rejected_up_front` |
| **BR3** | The fare is computed by the server: per-seat fare = base fare + Tatkal charge, where the charge is 10 % (2S) or 30 % (other classes) of the base fare, clamped to the class minimum and maximum. The client never sends a price. | inventory-service | [`domain/fares.py` `tatkal_charge_paise`, `quote`](../services/inventory-service/app/domain/fares.py), used when a hold is placed and for availability; the booking request has no amount field ([`BookingIn`](../services/booking-service/app/api/schemas.py)) | unit: [`test_br3_fares.py`](../services/inventory-service/tests/unit/test_br3_fares.py); e2e: `test_book_pay_confirm_notify_happy_path` checks the amount |
| **BR4** | A booking is charged at most once. Only money that was really taken can be refunded, always in full and only once: a CAPTURED payment, or a capture that reached the bank after the payment was voided (refunded at once). | payment-service | [`domain/payment_rules.py`](../services/payment-service/app/domain/payment_rules.py) (`refund_amount_for`, `PAYMENT_TRANSITIONS`); [`services/payments.py`](../services/payment-service/app/services/payments.py) refuses a second payment for a booking (409); DB `uq_payments_booking`, `uq_refunds_payment`; Idempotency-Key handling in [`services/idempotency.py`](../services/payment-service/app/services/idempotency.py) | unit: [`test_br4_payment_rules.py`](../services/payment-service/tests/unit/test_br4_payment_rules.py); integration: [`test_idempotent_payments_db.py`](../services/payment-service/tests/integration/test_idempotent_payments_db.py), [`test_payment_fencing_db.py`](../services/payment-service/tests/integration/test_payment_fencing_db.py) (late charge refused after a void, late capture refunded); [Challenge 2 script](../scripts/challenge2_idempotency.py) scenario F |
| **BR5** | A booking only moves along its state machine (PENDING, SEATS_HELD, PAYMENT_DONE, CONFIRMED, COMPENSATING, FAILED, CANCELLED). | booking-service | [`domain/booking_rules.py` `TRANSITIONS`, `assert_transition`](../services/booking-service/app/domain/booking_rules.py); every status change goes through [`bookings_repo.py` `transition`](../services/booking-service/app/repository/bookings_repo.py), an `UPDATE ... WHERE status = <expected>` so two actors can never both move a booking; DB check `ck_bookings_status` | unit: [`test_br5_state_machine.py`](../services/booking-service/tests/unit/test_br5_state_machine.py) |
| **BR6** | If the booking saga fails, everything that may have happened is undone, newest first: the payment is refunded in full, then the seats are released. A compensation that cannot run now is retried until it succeeds. | booking-service | [`domain/booking_rules.py` `compensation_plan`](../services/booking-service/app/domain/booking_rules.py); [`services/saga.py`](../services/booking-service/app/services/saga.py) `_compensate`, `_run_compensations`, `recover`; [`services/recovery.py`](../services/booking-service/app/services/recovery.py) | unit: [`test_br6_compensation_plan.py`](../services/booking-service/tests/unit/test_br6_compensation_plan.py); integration: [`test_saga_db.py`](../services/booking-service/tests/integration/test_saga_db.py); e2e: `test_failure_at_step_3_refunds_and_releases`, `test_declined_payment_releases_the_seats`; [Challenge 3 script](../scripts/challenge3_saga.py) |
| **BR7** | Only a CONFIRMED booking can be cancelled. The seats go back to the quota, but a confirmed Tatkal ticket gets no refund. | booking-service | [`domain/booking_rules.py` `tatkal_cancellation`](../services/booking-service/app/domain/booking_rules.py); [`services/saga.py` `BookingSaga.cancel`](../services/booking-service/app/services/saga.py) | unit: [`test_br7_cancellation.py`](../services/booking-service/tests/unit/test_br7_cancellation.py); e2e: `test_cancelling_a_tatkal_ticket_returns_seats_without_refund` |

The e2e tests are in [`tests/e2e/test_main_flow.py`](../tests/e2e/test_main_flow.py).

## How a violated rule looks to a client

Rule violations come back in the common error format with the rule id, so the client can see which rule
fired:

```json
{
  "type": "https://tatkal.example/problems/too-many-passengers",
  "title": "Business rule violated",
  "status": 422,
  "detail": "A Tatkal booking can have at most 4 passengers, got 5",
  "instance": "/api/v1/bookings",
  "code": "TOO_MANY_PASSENGERS",
  "rule": "BR2",
  "correlation_id": "0c1f6f2f6f0b4a8e9d8a1b2c3d4e5f60"
}
```

BR1 and BR4 conflicts that depend on current state rather than on the request itself are reported with
409 instead (`SEATS_UNAVAILABLE`, `PAYMENT_ALREADY_EXISTS`).
