from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.repository.tables import notifications

CLAIMED = (notifications.c.channel, notifications.c.recipient, notifications.c.message)


def claim_new(conn: Connection, rows: list[dict], token: str, lease: timedelta) -> list[dict]:
    """Inserts a PENDING row for every channel that has none yet and returns the ones
    inserted. A channel another delivery already has is skipped, not an error."""
    stmt = (pg_insert(notifications)
            .values([{**row, "status": "PENDING", "claim_token": token, "locked_until": func.now() + lease}
                     for row in rows])
            .on_conflict_do_nothing()
            .returning(*CLAIMED))
    return [dict(r) for r in conn.execute(stmt).mappings()]


def reclaim_expired(conn: Connection, event_id: str, token: str, lease: timedelta) -> list[dict]:
    """Takes over PENDING rows whose lease ran out (the delivery that claimed them died)."""
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
    """Gives back claims this delivery could not send; rows already SENT stay."""
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
