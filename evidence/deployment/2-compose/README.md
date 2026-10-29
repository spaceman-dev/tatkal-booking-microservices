# Docker Compose (Task 2)

**Claim:** `docker compose up --build` starts the complete application (four databases and five services)
with one command, the services reach each other by service name, the main workflow works, and everything
comes back with its data after `docker compose down` and `up`.

| File | What it shows |
|---|---|
| [`first-up.txt`](first-up.txt) | our very first `docker compose up --build` (09:15 IST, saved with `tee`). Compose warned that buildx was missing and used the old builder; all nine containers were still healthy after 19 s. We installed buildx afterwards, the later runs use BuildKit |
| [`up.txt`](up.txt), [`screenshots/up.png`](screenshots/up.png) | `docker compose up --build -d --wait` right after the clean build in [`../1-images`](../1-images): the databases start first, each service once its dependencies are healthy, all nine containers healthy about 11 s after the start |
| [`health.txt`](health.txt), [`screenshots/health.png`](screenshots/health.png) | `docker compose ps` with the health status, `/health` of each service (database `UP`, version, container id as `instance`), and health and restart count from `docker inspect` |
| [`e2e.txt`](e2e.txt), [`screenshots/e2e.png`](screenshots/e2e.png) | the 7 end-to-end tests against the containers: book, pay, confirm seats, notify (with the correlation id found in every service's log, read with `docker compose logs`), a failure at step 3 refunded and released, a declined payment, a sold-out train, a cancellation and the business-rule checks. 7 passed |
| [`demo_flow.txt`](demo_flow.txt) | `scripts/demo_flow.sh`: every request and answer of the main flow, the retry with the same Idempotency-Key, the saga log, the notifications, a forced failure at step 3 and its compensation, sold out |
| [`trace.txt`](trace.txt), [`screenshots/trace.png`](screenshots/trace.png) | the booking from the demo followed by its correlation id through booking, inventory, payment, the bank and notification, from the containers' logs |
| [`networks.txt`](networks.txt), [`screenshots/networks.png`](screenshots/networks.png) | name lookups from inside booking-service: its own database and the three services it calls resolve, the other databases and the bank do not. Then the six networks and who is on each (the four data networks are `internal`) |
| [`logs.txt`](logs.txt) | `docker compose logs --tail=4`: the last lines of all nine containers |
| [`restart.txt`](restart.txt), [`screenshots/restart.png`](screenshots/restart.png) | `scripts/restart_check.sh`: the newest confirmed booking, `docker compose down` (containers and networks gone, the four volumes stay), `docker compose up -d --wait`, the same booking is still there, and the end-to-end tests pass again |

Everything except `first-up.txt` was recorded between 09:53 and 09:57 IST at commits `d5275cd` and
`b118ea2` (the restart check was fixed in between, see the git log).
