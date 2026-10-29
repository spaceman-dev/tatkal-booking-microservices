#!/usr/bin/env bash
# Rolling update of booking-service to 1.1, rollback to 1.0, then a release that cannot start
# and its rollback. watch.sh shows which version answers in the meantime.
#   bash deploy/k8s/demo-rollout.sh
set -u
cd "$(dirname "$0")/../.."
NS=tatkal
WATCH_LOG=${WATCH_LOG:-/tmp/tatkal-rollout-watch.log}
show() {
  kubectl -n $NS get pods -l app=booking-service \
    -o custom-columns=POD:.metadata.name,IMAGE:.spec.containers[0].image,READY:.status.containerStatuses[0].ready
}

echo "== booking-service 1.1: same code, APP_VERSION=1.1.0, loaded into minikube"
IMAGE_TAG=1.1 docker compose build --quiet --build-arg APP_VERSION=1.1.0 booking-service
minikube image load tatkal/booking-service:1.1

bash deploy/k8s/watch.sh booking-service:8001 80 > "$WATCH_LOG" 2>&1 &
sleep 8

echo
echo "== rolling update to 1.1"
kubectl -n $NS set image deployment/booking-service booking-service=tatkal/booking-service:1.1
kubectl -n $NS annotate deployment/booking-service kubernetes.io/change-cause="booking-service 1.1.0" --overwrite >/dev/null
kubectl -n $NS rollout status deployment/booking-service --timeout=120s
show
sleep 5

echo
echo "== rollback to the previous version"
kubectl -n $NS rollout undo deployment/booking-service
kubectl -n $NS rollout status deployment/booking-service --timeout=120s
kubectl -n $NS rollout history deployment/booking-service
show

echo
echo "== a release that cannot start: there is no image tatkal/booking-service:9.9"
kubectl -n $NS set image deployment/booking-service booking-service=tatkal/booking-service:9.9
kubectl -n $NS rollout status deployment/booking-service --timeout=20s || echo "(the rollout does not finish, the old pods keep serving)"
show
echo
echo "== rollback"
kubectl -n $NS rollout undo deployment/booking-service
kubectl -n $NS rollout status deployment/booking-service --timeout=120s
show

wait
echo
echo "== what the client inside the cluster saw: $(wc -l < "$WATCH_LOG" | tr -d ' ') requests, versions in order"
awk '$3 != "ERROR" {print $5}' "$WATCH_LOG" | uniq -c
echo "failed requests: $(grep -c ERROR "$WATCH_LOG")   (every line is in $WATCH_LOG)"
