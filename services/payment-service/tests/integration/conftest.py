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
        pytest.skip(f"PostgreSQL for payment-service is not reachable: {exc}")
    metadata.create_all(eng)
    yield eng
    eng.dispose()
