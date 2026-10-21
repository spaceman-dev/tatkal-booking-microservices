from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from app.repository.tables import outbox


def claim_due(conn: Connection, limit: int) -> list[dict]:
    """Rows stay locked until the caller's transaction ends, so another replica's
    dispatcher skips them."""
    rows = conn.execute(
        select(outbox)
        .where(outbox.c.status == "PENDING", outbox.c.next_attempt_at <= func.now())
        .order_by(outbox.c.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).mappings().all()
    return [dict(r) for r in rows]


def mark_sent(conn: Connection, event_id: UUID) -> None:
    conn.execute(update(outbox).where(outbox.c.event_id == event_id)
                 .values(status="SENT", sent_at=func.now(), attempts=outbox.c.attempts + 1, last_error=None))


def mark_retry(conn: Connection, event_id: UUID, attempts: int, delay_seconds: float, error: str) -> None:
    conn.execute(
        update(outbox).where(outbox.c.event_id == event_id)
        .values(attempts=attempts, last_error=error[:300],
                next_attempt_at=func.now() + timedelta(seconds=delay_seconds))
    )


def mark_dead(conn: Connection, event_id: UUID, attempts: int, error: str) -> None:
    conn.execute(update(outbox).where(outbox.c.event_id == event_id)
                 .values(status="DEAD", attempts=attempts, last_error=error[:300]))


def events_for_booking(conn: Connection, booking_id: UUID) -> list[dict]:
    rows = conn.execute(select(outbox).where(outbox.c.booking_id == booking_id).order_by(outbox.c.created_at))
    return [dict(r) for r in rows.mappings()]
