#!/usr/bin/env bash
# Step-by-step walk through the main flow with curl (macOS / Linux, bash).
# Prints each command before running it and waits for Enter between steps.
# Each run opens its own 2-seat quota on a random future date, so it can be rerun.
#
#   python scripts/stack.py start      # first
#   bash scripts/demo_flow.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
BOOKING=${BOOKING_URL:-http://localhost:8001}
INVENTORY=${INVENTORY_URL:-http://localhost:8002}
D=$($PY -c "import datetime, random; print(datetime.date.today() + datetime.timedelta(days=random.randint(60, 900)))")
RUN=$($PY -c "import uuid; print(uuid.uuid4().hex[:6])")

pretty() { $PY -m json.tool; }
step() { echo; echo "=== $1"; read -r -p "    [Enter] " _; }
show() { echo "\$ $*"; }

booking_json() {  # $1 = name, $2 = payer
  printf '{"train_no":"12951","journey_date":"%s","travel_class":"3A","passengers":[{"name":"%s","age":34,"gender":"F"}],"contact":{"mobile":"9876543210","email":"asha@example.com"},"payment":{"method":"UPI","payer_ref":"%s"}}' "$D" "$1" "$2"
}

step "1. all five services are up"
show python scripts/stack.py status
$PY scripts/stack.py status

step "2. open a Tatkal quota of 2 seats in 3A on train 12951 for $D (inventory-service)"
BODY="{\"train_no\":\"12951\",\"journey_date\":\"$D\",\"travel_class\":\"3A\",\"total_seats\":2,\"base_fare_paise\":249500}"
show curl -s -X POST $INVENTORY/api/v1/inventory -d "'$BODY'"
curl -s -X POST "$INVENTORY/api/v1/inventory" -H 'Content-Type: application/json' -d "$BODY" | pretty

step "3. availability and the Tatkal fare (BR3: Rs 2,495 base + Rs 400 charge = Rs 2,895)"
show curl -s "$INVENTORY/api/v1/trains/12951/availability?journey_date=$D"
curl -s "$INVENTORY/api/v1/trains/12951/availability?journey_date=$D" | pretty

step "4. book one seat - Idempotency-Key demo-$RUN-1, correlation id demo-$RUN-happy"
show curl -s -i -X POST $BOOKING/api/v1/bookings -H "'Idempotency-Key: demo-$RUN-1'" -H "'X-Correlation-ID: demo-$RUN-happy'" -d "'$(booking_json "Asha Rao" asha@okmock)'"
RESP=$(curl -s -i -X POST "$BOOKING/api/v1/bookings" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$RUN-1" -H "X-Correlation-ID: demo-$RUN-happy" -d "$(booking_json "Asha Rao" asha@okmock)")
echo "$RESP" | sed -n '1p;/^x-correlation-id/Ip;/^idempotent-replayed/Ip'
BODY_JSON=$(echo "$RESP" | tail -n 1)
echo "$BODY_JSON" | pretty
BOOKING_ID=$(echo "$BODY_JSON" | $PY -c "import json, sys; print(json.load(sys.stdin)['booking_id'])")

step "5. the client retries the same request (same key): same answer, nothing booked twice"
RESP=$(curl -s -i -X POST "$BOOKING/api/v1/bookings" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$RUN-1" -d "$(booking_json "Asha Rao" asha@okmock)")
echo "$RESP" | sed -n '1p;/^idempotent-replayed/Ip'
echo "$RESP" | tail -n 1 | $PY -c "import json, sys; b = json.load(sys.stdin); print('booking_id', b['booking_id'], ' pnr', b['pnr'])"

step "6. the saga log of that booking"
show curl -s $BOOKING/api/v1/bookings/$BOOKING_ID/saga
curl -s "$BOOKING/api/v1/bookings/$BOOKING_ID/saga" | pretty

step "7. the SMS and e-mail sent asynchronously through the outbox (notification-service)"
show curl -s "localhost:8004/api/v1/notifications?booking_id=$BOOKING_ID"
curl -s "localhost:8004/api/v1/notifications?booking_id=$BOOKING_ID" | $PY -c "
import json, sys
for n in json.load(sys.stdin):
    print(n['channel'], n['status'], '-', n['message'])"

step "8. FAILURE: make the next 2 seat confirmations fail (step 3), then book again"
show curl -s -X POST $INVENTORY/api/v1/admin/faults -d "'{\"operation\":\"confirm_hold\",\"times\":2}'"
curl -s -X POST "$INVENTORY/api/v1/admin/faults" -H 'Content-Type: application/json' -d '{"operation":"confirm_hold","times":2}'
echo
RESP=$(curl -s -i -X POST "$BOOKING/api/v1/bookings" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$RUN-2" -H "X-Correlation-ID: demo-$RUN-step3" -d "$(booking_json "Vikram Rao" vikram@okmock)")
echo "$RESP" | sed -n '1p;/^content-type/Ip'
FAILED_JSON=$(echo "$RESP" | tail -n 1)
echo "$FAILED_JSON" | pretty
FAILED_ID=$(echo "$FAILED_JSON" | $PY -c "import json, sys; print(json.load(sys.stdin)['booking_id'])")

step "9. its saga log: CONFIRM failed, then REFUND and RELEASE ran newest first"
curl -s "$BOOKING/api/v1/bookings/$FAILED_ID/saga" | $PY -c "
import json, sys
for i, s in enumerate(json.load(sys.stdin), 1):
    print(i, s['step'].ljust(15), s['action'].ljust(10), s['outcome'].ljust(7), (s['detail'] or '')[:70])"
echo
show curl -s "localhost:8003/api/v1/payments?booking_id=$FAILED_ID"
curl -s "localhost:8003/api/v1/payments?booking_id=$FAILED_ID" | $PY -c "
import json, sys
p = json.load(sys.stdin)[0]; print('payment', p['status'], ' refund', p['refund']['status'], p['refund']['amount_paise'], 'paise')"
curl -s "$INVENTORY/api/v1/inventory?train_no=12951&journey_date=$D&travel_class=3A" | $PY -c "
import json, sys
q = json.load(sys.stdin); print('quota: total', q['total_seats'], ' available', q['available_seats'], ' confirmed', q['seats_confirmed'], ' released', q['seats_released'])"

step "10. the same failed request traced through every service by its correlation id"
show python scripts/trace.py demo-$RUN-step3
$PY scripts/trace.py "demo-$RUN-step3" | cut -c1-170

step "11. book the last seat, then one more: sold out"
curl -s -o /dev/null -w "last seat: HTTP %{http_code}\n" -X POST "$BOOKING/api/v1/bookings" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$RUN-3" -d "$(booking_json "Meera Iyer" meera@okmock)"
curl -s -X POST "$BOOKING/api/v1/bookings" -H 'Content-Type: application/json' \
  -H "Idempotency-Key: demo-$RUN-4" -d "$(booking_json "Kiran Das" kiran@okmock)" | pretty
echo; echo "=== done"
