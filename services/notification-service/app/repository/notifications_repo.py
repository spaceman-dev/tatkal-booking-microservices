from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.repository.tables import notifications

CLAIMED = (notifications.c.channel, notifications.c.recipient, notifications.c.message)


def claim_new(conn: Connection, rows: list[dict], token: str, lease: timedelta) -> list[dict]:
    """Insert PENDING claims and return the ones we got (channels already taken are skipped)."""
    stmt = (pg_insert(notifications)
            .values([{**row, "status": "PENDING", "claim_token": token, "locked_until": func.now() + lease}
                     for row in rows])
            .on_conflict_do_nothing()
            .returning(*CLAIMED))
    return [dict(r) for r in conn.execute(stmt).mappings()]


def reclaim_expired(conn: Connection, event_id: str, token: str, lease: timedelta) -> list[dict]:
    """Take over PENDING rows whose lease ran out (the delivery holding them died)."""
    stmt = (update(notifications)
            .where(notifications.c.event_id == event_id, notifications.c.status == "PENDING",
                   notifications.c.locked_until < func.now())
            .values(claim_token=token, locked_until=func.now() + lease)
            .returning(*CLAIMED))
    return [dict(r) for r in conn.execute(stmt).mappings()]


def mark_sent(conn: Connection, event_id: str, channel: str, token: str) -> None:
    conn.execute(update(notifications)
                 .where(notifications.c.event_id == event_id, notifications.c.channel == channel,
                        notifications.c.claim_token == token, notifications.c.status == "PENDING")
                 .values(status="SENT", sent_at=func.now()))


def release(conn: Connection, event_id: str, channels: list[str], token: str) -> None:
    """Delete our unsent claims so the retry can claim them again. SENT rows stay."""
    conn.execute(delete(notifications).where(notifications.c.event_id == event_id,
                                             notifications.c.channel.in_(channels),
                                             notifications.c.claim_token == token,
                                             notifications.c.status == "PENDING"))


def list_by_event(conn: Connection, event_id: str) -> list[dict]:
    rows = conn.execute(select(notifications).where(notifications.c.event_id == event_id)
                        .order_by(notifications.c.channel)).mappings().all()
    return [dict(r) for r in rows]


def list_by_booking(conn: Connection, booking_id: UUID) -> list[dict]:
    rows = conn.execute(select(notifications).where(notifications.c.booking_id == booking_id)
                        .order_by(notifications.c.created_at, notifications.c.channel)).mappings().all()
    return [dict(r) for r in rows]
