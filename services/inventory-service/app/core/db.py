"""Engine creation, start-up wait and the database health probe."""
import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError

log = logging.getLogger(__name__)


def build_engine(url: str, pool_size: int = 10, max_overflow: int = 20) -> Engine:
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_timeout=10,
    )


def wait_for_database(engine: Engine, attempts: int = 30, delay_seconds: float = 1.0) -> None:
    """Containers and pods start in any order, so keep trying for a while."""
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            log.warning("database not reachable yet (attempt %d/%d)", attempt, attempts)
            time.sleep(delay_seconds)
    raise RuntimeError("database is not reachable, giving up")


def database_is_up(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def is_unique_violation(exc: IntegrityError, constraint: str | None = None) -> bool:
    """True when the IntegrityError came from a UNIQUE/PK constraint (SQLSTATE 23505)."""
    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != "23505":
        return False
    if constraint is None:
        return True
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None) == constraint
