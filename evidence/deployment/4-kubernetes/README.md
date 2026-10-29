# Kubernetes on Minikube (Part B)

**Claim:** the whole application runs on Minikube from the manifests in [`deploy/k8s`](../../../deploy/k8s),
and the main workflow works through the Kubernetes Services. booking-service replaces a deleted pod, scales
to 3 replicas, and can be updated to a new version and rolled back, while a client inside the cluster sees
no failed request.

Minikube v1.39.0 with the docker driver (2 CPUs, 3.5 GB), Kubernetes v1.37.0, containerd. The images are
the ones Compose builds, copied into Minikube with `minikube image load`.

| File | What it shows |
|---|---|
| [`deploy.txt`](deploy.txt), [`screenshots/deploy.png`](screenshots/deploy.png) | `bash deploy/k8s/deploy.sh` after deleting the namespace: the images, the `db-passwords` Secret made from `.env`, the manifests, every StatefulSet and Deployment rolled out, and all 10 pods `Running` with 0 restarts. 41 s, because the images were already in Minikube |
| [`resources.txt`](resources.txt), [`screenshots/resources.png`](screenshots/resources.png) | Deployments and StatefulSets with their images, the Services (ClusterIP for the services, headless for the databases), a 1 Gi volume per database, a ConfigMap per service and the keys (not the values) of the Secret |
| [`e2e.txt`](e2e.txt), [`screenshots/e2e.png`](screenshots/e2e.png) | the same 7 end-to-end tests, through `deploy/k8s/port-forward.sh`, with `LOG_SOURCE=k8s` so the log checks read `kubectl logs`. 7 passed |
| [`demo_flow.txt`](demo_flow.txt) | `scripts/demo_flow.sh` against the cluster: booking, the retry with the same key, the saga log, notifications, the forced step-3 failure and its compensation, sold out |
| [`trace.txt`](trace.txt), [`screenshots/trace.png`](screenshots/trace.png) | one booking followed by its correlation id across booking, inventory, payment, the bank and notification, read from the pods' logs |
| [`scaling.txt`](scaling.txt), [`screenshots/scaling.png`](screenshots/scaling.png), [`scaling-watch.txt`](scaling-watch.txt) | `deploy/k8s/demo-scaling.sh`: one of the two booking pods deleted (a new one is running 3 s later), then `kubectl scale --replicas=3`. A pod inside the cluster called booking-service twice a second the whole time: 80 requests, 0 failed, answered by 4 different pods |
| [`rollout.txt`](rollout.txt), [`screenshots/rollout.png`](screenshots/rollout.png), [`rollout-watch.txt`](rollout-watch.txt) | `deploy/k8s/demo-rollout.sh`: rolling update 1.0 to 1.1, `kubectl rollout undo`, then a broken release (there is no image `9.9`) that never gets ready while the 1.0 pods keep serving, and `rollout undo` again. 159 requests, 0 failed; the watch file shows the version changing |
| [`first-run/`](first-run) | our first deploy (3 min 36 s, most of it copying the images into Minikube) and the first self-healing and scaling run, saved with `tee` between 09:22 and 09:27 IST |

The watch files are written by [`deploy/k8s/watch.sh`](../../../deploy/k8s/watch.sh). Their times are UTC
(the pod's clock), the recordings show IST and UTC.

Inventory-service and the mock bank stay at one replica on purpose: the fault switch used by the tests and
the demo is kept in memory, so with two replicas only one of them would have it switched on. booking,
payment and notification keep all their state in their database. booking's outbox relay and recovery job
pick their rows with `FOR UPDATE SKIP LOCKED`, so two replicas never take the same row, and notification
stores each event once (unique key on event id and channel). That is why booking-service is the one we
scale.
