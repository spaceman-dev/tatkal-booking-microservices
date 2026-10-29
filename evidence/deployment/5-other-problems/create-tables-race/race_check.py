"""Four booking-service "replicas" create their tables on an empty schema at the same moment,
like the first rollout of a Deployment with replicas: 2 or more on a new database.

Run it from services/booking-service of whichever version you want to check, with that
folder's .env pointing at a running booking_db (docker compose -f docker-compose.db.yml up -d):

    cd services/booking-service && python ../../evidence/deployment/5-other-problems/create-tables-race/race_check.py

Versions without app.core.db.create_tables (before commit 4511fed) call metadata.create_all
directly, which is what the services did at startup then. A throwaway schema is used and dropped.
"""
import sys
import threading
import uuid

sys.path.insert(0, ".")
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core import db as core_db  # noqa: E402
from app.repository.tables import metadata  # noqa: E402

url = get_settings().database_url
schema = f"race_{uuid.uuid4().hex[:8]}"
admin = create_engine(url)
with admin.begin() as conn:
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))

start = threading.Barrier(4)
errors = []
create = getattr(core_db, "create_tables", None)
print("startup code:", "create_tables (advisory lock)" if create else "metadata.create_all")


def replica(n):
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    start.wait()
    try:
        if create:
            create(engine, metadata)
        else:
            metadata.create_all(engine)
        print(f"replica {n}: ok")
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
        print(f"replica {n}: {type(exc).__name__}: {str(exc).splitlines()[0][:150]}")
    finally:
        engine.dispose()


threads = [threading.Thread(target=replica, args=(n,)) for n in range(1, 5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
with admin.begin() as conn:
    conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
admin.dispose()
print("errors:", len(errors))
