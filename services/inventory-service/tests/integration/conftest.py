import random
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.repository.tables import metadata


@pytest.fixture(scope="session")
def engine():
    # real Postgres, but a throwaway schema per run so we never touch the service's own tables
    url = get_settings().database_url
    schema = f"test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL for inventory-service is not reachable: {exc}")
    eng = create_engine(url, pool_size=25, max_overflow=10, connect_args={"options": f"-csearch_path={schema}"})
    metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO trains (train_no, name, from_station, to_station, departure_time) "
            "VALUES ('12627', 'Karnataka Express', 'SBC', 'NDLS', '19:20') ON CONFLICT DO NOTHING"))
    yield eng
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def fresh_run_date():
    # random far-future date, so each test gets its own quota row
    return date.today() + timedelta(days=random.randint(3_000, 30_000))
