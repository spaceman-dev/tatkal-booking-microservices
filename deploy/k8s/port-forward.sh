#!/usr/bin/env bash
# Makes the services on Minikube reachable on localhost:8001-8004 and 8099, the same
# ports as with docker compose, so the e2e tests and scripts run unchanged.
# Leave it running in its own terminal. Ctrl+C stops it.
set -u
NS=${K8S_NAMESPACE:-tatkal}
trap 'trap - EXIT; kill 0' INT TERM EXIT

for pair in booking-service:8001 inventory-service:8002 payment-service:8003 notification-service:8004 \
            mock-bank-gateway:8099; do
  svc=${pair%%:*}
  port=${pair##*:}
  # a port-forward sticks to one pod, so start it again when that pod goes away
  ( while true; do kubectl -n "$NS" port-forward "svc/$svc" "$port:$port" >/dev/null 2>&1; sleep 1; done ) &
  echo "localhost:$port -> svc/$svc"
done
wait
