#!/usr/bin/env bash
# Self-healing and scaling of booking-service in one run: watch.sh keeps calling the service
# from inside the cluster while we delete one of its pods and then scale it to 3 replicas.
#   bash deploy/k8s/demo-scaling.sh
set -u
cd "$(dirname "$0")"
NS=tatkal
WATCH_LOG=${WATCH_LOG:-/tmp/tatkal-scaling-watch.log}
pods() { kubectl -n $NS get pods -l app=booking-service; }

bash watch.sh booking-service:8001 40 > "$WATCH_LOG" 2>&1 &
sleep 8

echo "== booking-service pods"
pods
victim=$(kubectl -n $NS get pods -l app=booking-service -o jsonpath='{.items[0].metadata.name}')
echo
echo "== kubectl delete pod $victim"
kubectl -n $NS delete pod "$victim" --wait=false
sleep 2
pods
kubectl -n $NS rollout status deployment/booking-service --timeout=90s
pods

echo
echo "== kubectl scale deployment booking-service --replicas=3"
kubectl -n $NS scale deployment booking-service --replicas=3
kubectl -n $NS rollout status deployment/booking-service --timeout=90s
pods

wait
echo
echo "== what the client inside the cluster saw: $(wc -l < "$WATCH_LOG" | tr -d ' ') requests, answers per pod"
awk '$3 != "ERROR" {print $NF}' "$WATCH_LOG" | sort | uniq -c
echo "failed requests: $(grep -c ERROR "$WATCH_LOG")   (every line is in $WATCH_LOG)"
