#!/usr/bin/env bash
# Deploys the whole application to Minikube.
#
#   minikube start --cpus=2 --memory=3500mb
#   bash deploy/k8s/deploy.sh
#
# Builds the images with docker compose, copies them into minikube, creates the
# db-passwords secret from .env (never committed) and applies the manifests.
set -euo pipefail
cd "$(dirname "$0")/../.."

NS=tatkal
TAG=${IMAGE_TAG:-1.0}
SERVICES=(mock-bank-gateway inventory-service payment-service notification-service booking-service)

if [ ! -f .env ]; then
  echo "No .env here. Copy .env.example to .env and set the database passwords first."
  exit 1
fi

echo "== 1. images (tag $TAG)"
IMAGE_TAG=$TAG docker compose build
for s in "${SERVICES[@]}"; do
  echo "   loading tatkal/$s:$TAG into minikube"
  minikube image load "tatkal/$s:$TAG"
done

echo "== 2. namespace and the db-passwords secret (from .env)"
kubectl apply -f deploy/k8s/namespace.yaml
kubectl -n "$NS" create secret generic db-passwords --from-env-file=.env \
  --dry-run=client -o yaml | kubectl apply -f -

echo "== 3. databases, services and their config"
kubectl apply -k deploy/k8s

echo "== 4. waiting until everything is ready"
for db in booking-db inventory-db payment-db notification-db; do
  kubectl -n "$NS" rollout status "statefulset/$db" --timeout=300s
done
for s in "${SERVICES[@]}"; do
  kubectl -n "$NS" rollout status "deployment/$s" --timeout=300s
done
kubectl -n "$NS" get pods -o wide

echo
echo "Next: bash deploy/k8s/port-forward.sh  (in another terminal), then use localhost:8001-8004 as usual"
