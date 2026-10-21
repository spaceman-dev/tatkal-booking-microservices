import random
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.db import build_engine
from app.repository.tables import metadata


@pytest.fixture(scope="session")
def engine():
    settings = get_settings()
    eng = build_engine(settings.database_url, pool_size=25, max_overflow=10)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"PostgreSQL for inventory-service is not reachable: {exc}")
    metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO trains (train_no, name, from_station, to_station, departure_time) "
            "VALUES ('12627', 'Karnataka Express', 'SBC', 'NDLS', '19:20') ON CONFLICT DO NOTHING"))
    yield eng
    eng.dispose()


@pytest.fixture
def fresh_run_date():
    """A far-future date nobody else uses, so every test gets its own quota row."""
    return date.today() + timedelta(days=random.randint(3_000, 30_000))
