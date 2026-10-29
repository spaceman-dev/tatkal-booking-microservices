"""Replicas that start together on an empty database (a new deployment with
replicas: 2 or more) must not crash while creating their tables."""
import threading
import uuid

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.core.db import create_tables
from app.repository.tables import metadata

pytestmark = pytest.mark.integration

REPLICAS = 4


def test_replicas_creating_tables_at_the_same_time_all_start():
    url = get_settings().database_url
    schema = f"test_race_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL for booking-service is not reachable: {exc}")

    start_line, errors = threading.Barrier(REPLICAS), []

    def replica():
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        start_line.wait()
        try:
            create_tables(engine, metadata)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=replica) for _ in range(REPLICAS)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        with admin.connect() as conn:
            created = conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :s"),
                                   {"s": schema}).scalar_one()
        assert created == len(metadata.tables)
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
