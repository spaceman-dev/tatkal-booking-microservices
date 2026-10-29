# Images (Task 1)

**Claim:** each service has its own Dockerfile and image, configuration comes in through environment
variables, and no password or `.env` file ends up inside an image.

| File | What it shows |
|---|---|
| [`build.txt`](build.txt), [`screenshots/build.png`](screenshots/build.png) | `docker compose build --no-cache` with BuildKit: the five images built from `services/<name>/Dockerfile`, every step of every build |
| [`images.txt`](images.txt), [`screenshots/images.png`](screenshots/images.png) | the images and their sizes (348 MB for each service with a database, 274 MB for the mock bank, all on `python:3.12-slim`). For each image: the user it runs as (`tatkal`, uid 10001, not root), the port and the command (`python -m app`). For booking-service: the environment baked into the image (no passwords, no `DATABASE_URL`, only `APP_VERSION`, `HOST`, `PORT` and Python settings), the `HEALTHCHECK`, and the files in `/app` (the code and `requirements.txt`, no `.env`) |

The `tatkal/booking-service:1.1` in the list was built a few minutes earlier for the rolling update in
[`../4-kubernetes`](../4-kubernetes); it is the same code with `APP_VERSION=1.1.0`.

How the Dockerfiles are written and why is in the deployment report, section 2. In short: dependencies are
installed before the code is copied so a code change does not reinstall them, the code is owned by root so
the service user can read it but not change it, and `CMD` is in exec form so the service is PID 1 and gets
the stop signal from `docker stop` and Kubernetes.

The settings each service reads are listed in the README, section 5. Compose sets them in
`docker-compose.yml`, Kubernetes in a ConfigMap per service, and the passwords come from the root `.env`
(Compose) or the `db-passwords` Secret made from the same file (Kubernetes).
