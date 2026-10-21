import uuid

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.repository.tables import metadata


@pytest.fixture(scope="session")
def engine():
    """Real PostgreSQL, but a fresh schema per test run so we never touch the service's own tables."""
    url = get_settings().database_url
    schema = f"test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(url, pool_size=1, max_overflow=0)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # pragma: no cover (depends on the machine)
        pytest.skip(f"PostgreSQL for payment-service is not reachable: {exc}")
    eng = create_engine(url, pool_size=25, max_overflow=10, connect_args={"options": f"-csearch_path={schema}"})
    metadata.create_all(eng)
    yield eng
    eng.dispose()
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()
