#!/usr/bin/env bash
# Calls a service from inside the cluster twice a second and prints which pod answered
# and its version. Run it while scaling, deleting a pod or rolling out a new version to
# see that requests keep being answered.
#
#   bash deploy/k8s/watch.sh                         # booking-service until Ctrl+C
#   bash deploy/k8s/watch.sh booking-service:8001 60 # stop after 60 s
NS=${K8S_NAMESPACE:-tatkal}
TARGET=${1:-booking-service:8001}
SECONDS_TO_RUN=${2:-0}

kubectl -n "$NS" delete pod health-watch --ignore-not-found --wait=true >/dev/null
kubectl -n "$NS" run health-watch --rm -i --restart=Never --quiet \
  --image=tatkal/booking-service:1.0 --image-pull-policy=IfNotPresent \
  --command -- python -u -c "
import json, time, urllib.request
stop = time.time() + $SECONDS_TO_RUN if $SECONDS_TO_RUN else None
while stop is None or time.time() < stop:
    try:
        body = json.load(urllib.request.urlopen('http://$TARGET/health', timeout=2))
        print(time.strftime('%H:%M:%S UTC', time.gmtime()), body['status'], 'version', body['version'], 'pod', body['instance'])
    except Exception as exc:
        print(time.strftime('%H:%M:%S UTC', time.gmtime()), 'ERROR', exc)
    time.sleep(0.5)
"
