# Two more problems we found on the way

## 1. A rolling update dropped a few requests

The first time we rolled booking-service from 1.0 to 1.1 and back (03:58 UTC, 3 replicas), the client
pod that calls booking-service twice a second got 3 failures out of 141 requests: one `Connection
refused` during the update and two timeouts during the rollback. Each one came just as an old pod was
being stopped.

When Kubernetes stops a pod it sends SIGTERM and, at the same time, starts removing the pod from the
Service's endpoints. The second part takes a moment to reach every node, so for a short while new
requests still went to a pod that was already shutting down.

**Fix** (commit `c264040`): each service container has `lifecycle.preStop: sleep 5s` and the pods have
`terminationGracePeriodSeconds: 30`. The pod keeps serving for 5 seconds while it is taken out of the
endpoints, and only then gets SIGTERM and shuts down cleanly. The same update and rollback afterwards:
139 requests, 0 failed. The recorded demo in [`../4-kubernetes/rollout.txt`](../4-kubernetes/rollout.txt)
also has 0 failures out of 159.

| File | What it shows |
|---|---|
| [`rolling-update/1-before-fix-rollout.txt`](rolling-update/1-before-fix-rollout.txt) | update to 1.1 at 03:58:29 UTC, rollback at 03:58:46, the pods and revisions in between |
| [`rolling-update/1-before-fix-watch.txt`](rolling-update/1-before-fix-watch.txt) | what the client pod saw: `ERROR` at 03:58:32, 03:58:54 and 03:58:57 |
| [`rolling-update/2-after-fix-rollout.txt`](rolling-update/2-after-fix-rollout.txt), [`rolling-update/2-after-fix-watch.txt`](rolling-update/2-after-fix-watch.txt) | the same after the fix (04:01 UTC): no `ERROR` line |

These were saved with `tee` while we were working, so there are no screenshots.

## 2. Replicas creating their tables at the same moment

Each service creates its tables when it starts (`metadata.create_all`). With `replicas: 2` on a new
database, both booking-service pods do that at the same moment. It did not go wrong in our deploys (both
pods started with 0 restarts), but we wanted to know what happens when they collide. So we wrote
[`race_check.py`](create-tables-race/race_check.py), which starts four "replicas" at once on an empty
schema. On the old code 3 of the 4 crash with `UniqueViolation ... pg_type_typname_nsp_index`:
`create_all` first checks whether a table exists and then creates it, so two sessions that check at the
same time both try to create it and PostgreSQL rejects the second one. In Kubernetes the crashed pod would
restart and work the second time, but a crash on the first deploy is not something we wanted to keep.

**Fix** (commit `4511fed`): `create_tables()` in `app/core/db.py` takes a PostgreSQL advisory lock
(`pg_advisory_xact_lock`) in the same transaction, so the replicas create the tables one after another and
the later ones find them already there. Same change in all four services with a database. Regression
test: `services/booking-service/tests/integration/test_create_tables_db.py` (it fails without the lock).

| File | What it shows |
|---|---|
| [`create-tables-race/1-before-fix.txt`](create-tables-race/1-before-fix.txt), [`screenshot`](create-tables-race/screenshots/1-before-fix.png) | the code before the fix (commit `d056976`, checked out in a temporary worktree): 1 replica ok, 3 `IntegrityError (UniqueViolation)` |
| [`create-tables-race/2-after-fix.txt`](create-tables-race/2-after-fix.txt), [`screenshot`](create-tables-race/screenshots/2-after-fix.png) | the current code: 4 ok, 0 errors, and the regression test passing |

Both runs use the development booking database from `docker-compose.db.yml` and a throwaway schema that
the script drops at the end.
