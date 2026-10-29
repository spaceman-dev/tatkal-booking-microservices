# Kubernetes (Minikube)

The same images as `docker-compose.yml`, deployed on Minikube with Deployments, StatefulSets and Services
in the namespace `tatkal`.

| Kind | What |
|---|---|
| StatefulSet + headless Service | `booking-db`, `inventory-db`, `payment-db`, `notification-db`: PostgreSQL 16, one per service, 1 Gi PersistentVolumeClaim each |
| Deployment + Service | `booking-service` (2 replicas), `inventory-service`, `payment-service`, `notification-service`, `mock-bank-gateway` (1 each) |
| ConfigMap | `<service>-config`: other services' URLs, log level, demo switches |
| Secret | `db-passwords`, created by `deploy.sh` from the root `.env`; never committed |

## Deploy

```bash
minikube start --cpus=2 --memory=3500mb    # we used minikube 1.39 with the docker driver
cp .env.example .env                       # if you have not already (database passwords)
bash deploy/k8s/deploy.sh
```

`deploy.sh` builds the images with `docker compose build`, copies them into minikube with
`minikube image load` (the pods use `imagePullPolicy: IfNotPresent`, there is no registry), creates the
namespace and the `db-passwords` secret, runs `kubectl apply -k deploy/k8s` and waits for every rollout.

```bash
kubectl -n tatkal get pods,svc,pvc
```

## Use it

```bash
bash deploy/k8s/port-forward.sh            # own terminal: localhost:8001-8004 and 8099, like docker compose
curl localhost:8001/health                 # "instance" is the pod that answered
LOG_SOURCE=k8s .venv/bin/python -m pytest tests/e2e -v
LOG_SOURCE=k8s bash scripts/demo_flow.sh   # trace.py then reads kubectl logs
```

Stop the compose stack before port-forwarding, both use the same local ports.

## Scaling and self-healing

`watch.sh` calls booking-service from inside the cluster twice a second and prints the pod that answered.
A port-forward always goes to one pod, so it would not show the load being spread.

```bash
bash deploy/k8s/watch.sh                   # own terminal, Ctrl+C to stop
kubectl -n tatkal scale deployment booking-service --replicas=3
kubectl -n tatkal delete pod <one of the booking-service pods>
kubectl -n tatkal get pods -l app=booking-service -w
```

The ReplicaSet replaces a deleted pod within seconds, and the other replicas keep answering meanwhile.
`bash deploy/k8s/demo-scaling.sh` does both steps with the watcher running and counts failed requests.

## Rolling update and rollback

```bash
docker build --build-arg APP_VERSION=1.1.0 -t tatkal/booking-service:1.1 services/booking-service
minikube image load tatkal/booking-service:1.1
kubectl -n tatkal set image deployment/booking-service booking-service=tatkal/booking-service:1.1
kubectl -n tatkal rollout status deployment/booking-service
kubectl -n tatkal rollout history deployment/booking-service
kubectl -n tatkal rollout undo deployment/booking-service
```

`/health` reports the version, so `watch.sh` shows the pods switching from 1.0.0 to 1.1.0 and back. With
`maxUnavailable: 0` and `maxSurge: 1` a new pod has to be ready before an old one is stopped. A version
that cannot start (try a tag that does not exist) stays stuck at the first new pod while the old ones keep
serving, until `rollout undo`. `bash deploy/k8s/demo-rollout.sh` runs the whole sequence (update, rollback,
broken release, rollback) with the watcher and counts failed requests.

## Choices in the manifests

- **Probes.** `startupProbe` and `livenessProbe` use `/health/live`, which does not touch the database, so
  a slow database never gets a pod restarted. `readinessProbe` uses `/health`, which does check the
  database, so a pod that cannot reach it stops getting traffic.
- **Waiting for the database.** An init container runs `pg_isready` until the service's database answers,
  the Kubernetes version of `depends_on: service_healthy` in compose.
- **preStop pause.** A pod being replaced gets SIGTERM at the same time as the Service starts removing it
  from its endpoints. Without the 5 s `preStop` sleep some requests still reached stopping pods during a
  rolling update (connection refused / timeouts). With it, none did.
- **Creating tables.** Replicas that start together on an empty database all ran `CREATE TABLE` and all but
  one crashed. Tables are now created under a PostgreSQL advisory lock (`create_tables` in `app/core/db.py`).
- **`DATABASE_URL`** is put together from the secret: `$(DB_PASSWORD)` inside the env value.
- **`enableServiceLinks: false`**, so Kubernetes does not add `<SERVICE>_PORT=tcp://...` variables that
  could clash with settings.
- **One replica** for the mock bank and inventory-service: the mock bank keeps its state in memory, and
  inventory's dev-only fault injector (used by the tests) is per pod.

## Limits

- No NetworkPolicies: the default minikube network plugin does not enforce them, so inside the cluster
  every pod can reach every database (in compose the networks do separate them).
- Images are loaded into minikube by hand; a real cluster would pull them from a registry.
- The databases are single pods with local storage, fine for Minikube but not highly available.

## Remove

```bash
kubectl delete namespace tatkal            # everything, including the volumes
minikube stop
```
