#!/usr/bin/env bash
# Stops the docker compose deployment, starts it again and checks that the data and the
# main flow survive.
#   bash scripts/restart_check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

newest() {
  curl -s "localhost:8001/api/v1/bookings?limit=1" | $PY -c '
import json, sys
b = json.load(sys.stdin)
print(f"{b[0][\"booking_id\"]}  {b[0][\"status\"]}  PNR {b[0][\"pnr\"]}" if b else "no bookings yet")'
}

echo "== newest booking before the restart"
newest
echo
echo "== docker compose down   (containers and networks are removed, the volumes stay)"
docker compose down
docker volume ls --filter name=tatkal_
echo
echo "== docker compose up -d --wait"
docker compose up -d --wait
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
echo
echo "== newest booking after the restart"
newest
echo
echo "== the main flow still works"
$PY -m pytest tests/e2e -q -p no:cacheprovider
