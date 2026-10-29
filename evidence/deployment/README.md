# Deployment evidence (Assignment 2)

Recorded on 29 Oct 2026 on one MacBook: Colima as the Docker engine (Docker 29.2, Compose 5.0.2 with
BuildKit) and Minikube 1.39 running Kubernetes 1.37. Each `.txt` file starts with the command, the time
(IST and UTC) and the commit it ran at. A screenshot with the same name is the terminal of that run.
Database passwords are masked (`***`) everywhere; the real ones are only in our local `.env`.

| Folder | Part of the brief | What it shows |
|---|---|---|
| [1-images](1-images) | Task 1 | the five images built from their Dockerfiles: sizes, non-root user, health check, no password or `.env` inside |
| [2-compose](2-compose) | Task 2 | `docker compose up --build`: nine containers healthy, services calling each other by name, the main flow (e2e tests and the demo script), logs, network isolation, and `down` / `up` again with the data kept |
| [3-troubleshooting](3-troubleshooting) | Task 3 | our deployment problem: a container started with the host `.env` could not reach its database, because `localhost` inside a container is the container. Symptom, diagnosis, fix and validation |
| [4-kubernetes](4-kubernetes) | Part B | the same application on Minikube: deploy, the main flow through Kubernetes Services, a deleted pod replaced, scaling to 3 replicas, rolling update and rollback, all with no failed requests |
| [5-other-problems](5-other-problems) | Part B, Task 3 | two more problems we found on the way: a rolling update that dropped requests (fixed with a preStop pause) and replicas crashing when they created their tables at the same moment (fixed with an advisory lock) |

Some files are older raw captures from the same morning, from before we had the recording scripts:
`2-compose/first-up.txt`, `3-troubleshooting/first-seen/`, `4-kubernetes/first-run/` and
`5-other-problems/rolling-update/`. They were saved with `tee` and are kept as they were. They have no
screenshots.

The service test suites were run again after the Assignment 2 changes, 133 tests passing:
[`../tests/service-tests_20261029-101418.txt`](../tests/service-tests_20261029-101418.txt).

## Running it again

```bash
cp .env.example .env                    # then put your own passwords in .env
docker compose up --build -d --wait
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt   # Python 3.11+, tests only
source .venv/bin/activate
python -m pytest tests/e2e -v
bash scripts/demo_flow.sh
bash scripts/restart_check.sh

# Kubernetes
docker compose down                     # frees ports 8001-8004 and 8099 for the port-forward
minikube start --cpus=2 --memory=3500mb
bash deploy/k8s/deploy.sh
bash deploy/k8s/port-forward.sh         # in a second terminal
LOG_SOURCE=k8s python -m pytest tests/e2e -v
bash deploy/k8s/demo-scaling.sh
bash deploy/k8s/demo-rollout.sh
```
