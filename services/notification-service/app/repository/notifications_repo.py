from uuid import UUID

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection

from app.repository.tables import notifications


def insert_pending(conn: Connection, rows: list[dict]) -> None:
    conn.execute(insert(notifications), [{**row, "status": "PENDING"} for row in rows])


def mark_sent(conn: Connection, event_id: str) -> None:
    conn.execute(update(notifications).where(notifications.c.event_id == event_id)
                 .values(status="SENT", sent_at=func.now()))


def delete_event(conn: Connection, event_id: str) -> None:
    conn.execute(delete(notifications).where(notifications.c.event_id == event_id,
                                             notifications.c.status == "PENDING"))


def list_by_event(conn: Connection, event_id: str) -> list[dict]:
    rows = conn.execute(select(notifications).where(notifications.c.event_id == event_id)
                        .order_by(notifications.c.channel)).mappings().all()
    return [dict(r) for r in rows]


def list_by_booking(conn: Connection, booking_id: UUID) -> list[dict]:
    rows = conn.execute(select(notifications).where(notifications.c.booking_id == booking_id)
                        .order_by(notifications.c.created_at, notifications.c.channel)).mappings().all()
    return [dict(r) for r in rows]
