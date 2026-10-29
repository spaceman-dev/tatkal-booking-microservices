# Deployment problem: the container could not reach its database (Task 3)

**What happened.** Once the Dockerfiles were written, the first thing we did was run one image on its
own, before writing `docker-compose.yml`. We started inventory-service with the `.env` file we had been
using on the laptop since Assignment 1 (`docker run --env-file services/inventory-service/.env ...`). Its
database was running and healthy, published on `localhost:5442` by `docker-compose.db.yml`. The container
started, stayed at `health: starting`, the port gave an empty reply, and after about 30 seconds the
container exited with code 3. The log only said `database not reachable yet (attempt n/30)`. The same
service with the same `.env` worked fine outside Docker.

**Cause.** `DATABASE_URL` in that file points at `localhost:5442`. Inside a container `localhost` is the
container itself, not the Mac, and nothing listens on 5442 in there, so every attempt was refused. The
database container can be reached by its name (`inventory-db`), but only from a container on the same
Docker network, and ours was on the default `bridge` network.

| File | What it shows |
|---|---|
| [`first-seen/1-image-smoke-test.txt`](first-seen/1-image-smoke-test.txt) | the first time, 29 Oct 09:10 IST, saved with `tee`: container up, `health: starting`, curl gets nothing, then `Exited (3)` and the log |
| [`first-seen/2-diagnosis.txt`](first-seen/2-diagnosis.txt) | how we found the cause that morning: `docker inspect` shows `DATABASE_URL` with `localhost:5442`, the database is healthy and published on 5442, the same connection made from a container of the same image is refused on `127.0.0.1` and `::1`, the database's network and DNS names. Then the same image on the database network with `inventory-db:5432` is healthy |
| [`1-symptom.txt`](1-symptom.txt) | the problem again at 09:57 for the screenshots. By then the log line had been improved (see the fix), the rest is the same |
| [`2-exited.txt`](2-exited.txt) | `Exited (3)`, unhealthy, 30 attempts one second apart, then `Application startup failed` |
| [`3-diagnose.txt`](3-diagnose.txt) | `docker logs` (what it tried to reach and why it failed), `docker inspect` (the env it got, its networks), where the database really is (`docker compose ps`, `docker inspect`), and `docker exec` inside the container: `localhost` is `127.0.0.1` / `::1` of the container itself, its own IP is on the bridge network, and `inventory-db` does not resolve from there |
| [`4-confirm.txt`](4-confirm.txt) | changing only the database host (`localhost:5442` to `inventory-db:5432`) and attaching the database network makes the same image healthy, with no failed attempts |
| [`5-fixed.txt`](5-fixed.txt) | the fix in `docker-compose.yml` and the check: the URL uses the service name, service and database share the `inventory-data` network, the development database is stopped, `/health` is `UP` and the 7 end-to-end tests pass |

All five numbered files have a screenshot in [`screenshots/`](screenshots).

**The fix.**

1. `docker-compose.yml` builds every `DATABASE_URL` from the database's service name (`inventory-db:5432`)
   and puts each service on a private network with its own database. The password comes from the root
   `.env`. The URLs of the other services work the same way (`http://inventory-service:8002`).
2. The per-service `.env` files are only for running on the host. `.dockerignore` keeps them out of the
   images and we no longer pass them to containers.
3. The `database not reachable` log line now also prints the target (password hidden) and the reason
   (commit `c6072d4`). With it the cause is visible in the first line:
   `localhost:5442 ... connection to server at "127.0.0.1", port 5442 failed: Connection refused`.

**What we learnt** (the longer version is in the deployment report):

- `localhost` means "this container". Containers talk to each other by service name on a shared network.
  A published port like 5442 is for people and programs on the host, not for other containers.
- Configuration that works on the host does not carry over into containers. Containers get their own
  configuration (Compose `environment`, a ConfigMap in Kubernetes) instead of the host's `.env`.
- A log line that says only "not reachable" costs time. Log what you tried to reach and why it failed,
  without the password.
- Running one image alone first kept the problem small: it could only be the image or its configuration.
