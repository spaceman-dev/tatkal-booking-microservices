from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.engine import Connection

from app.domain.booking_rules import assert_transition
from app.repository.tables import bookings, outbox, passengers, saga_log


class ConcurrentUpdate(Exception):
    """The booking was not in the state we expected - someone else moved it."""


def insert_booking(conn: Connection, booking: dict, passenger_rows: list[dict]) -> None:
    conn.execute(insert(bookings).values(**booking))
    conn.execute(insert(passengers), [{"booking_id": booking["booking_id"], "seq": i, **p}
                                      for i, p in enumerate(passenger_rows, start=1)])


def get_booking(conn: Connection, booking_id: UUID) -> dict | None:
    row = conn.execute(select(bookings).where(bookings.c.booking_id == booking_id)).mappings().first()
    if row is None:
        return None
    booking = dict(row)
    booking["passengers"] = [dict(p) for p in conn.execute(
        select(passengers.c.name, passengers.c.age, passengers.c.gender)
        .where(passengers.c.booking_id == booking_id).order_by(passengers.c.seq)).mappings()]
    return booking


def list_bookings(conn: Connection, status: str | None, train_no: str | None, journey_date, limit: int) -> list[dict]:
    query = select(bookings.c.booking_id).order_by(bookings.c.created_at.desc()).limit(limit)
    if status:
        query = query.where(bookings.c.status == status)
    if train_no:
        query = query.where(bookings.c.train_no == train_no)
    if journey_date:
        query = query.where(bookings.c.journey_date == journey_date)
    return [get_booking(conn, booking_id) for booking_id in conn.execute(query).scalars()]


def transition(conn: Connection, booking_id: UUID, from_status: str, to_status: str, **fields) -> None:
    """BR5 in the database: the UPDATE only matches if the booking is still in
    `from_status`, so two actors can never both move the same booking."""
    assert_transition(from_status, to_status)
    result = conn.execute(
        update(bookings)
        .where(bookings.c.booking_id == booking_id, bookings.c.status == from_status)
        .values(status=to_status, updated_at=func.now(), **fields)
    )
    if result.rowcount != 1:
        raise ConcurrentUpdate(f"booking {booking_id} is no longer {from_status}")


def set_fields(conn: Connection, booking_id: UUID, **fields) -> None:
    conn.execute(update(bookings).where(bookings.c.booking_id == booking_id).values(updated_at=func.now(), **fields))


def log_step(conn: Connection, booking_id: UUID, step: str, action: str, outcome: str, detail: str | None) -> None:
    conn.execute(insert(saga_log).values(booking_id=booking_id, step=step, action=action, outcome=outcome,
                                         detail=(detail or "")[:300] or None))


def saga_steps(conn: Connection, booking_id: UUID) -> list[dict]:
    rows = conn.execute(select(saga_log).where(saga_log.c.booking_id == booking_id).order_by(saga_log.c.id))
    return [dict(r) for r in rows.mappings()]


def add_outbox_event(conn: Connection, event_id: UUID, booking_id: UUID, event_type: str, payload: dict,
                     correlation_id: str | None) -> None:
    conn.execute(insert(outbox).values(event_id=event_id, booking_id=booking_id, event_type=event_type,
                                       payload=payload, correlation_id=correlation_id))


def claim_stuck_bookings(conn: Connection, statuses: tuple[str, ...], older_than_seconds: int, limit: int) -> list[UUID]:
    """Picks bookings that stopped moving. Bumping updated_at hides them from other
    replicas for another `older_than_seconds`, and SKIP LOCKED avoids waiting on rows
    a live request is still updating."""
    rows = conn.execute(
        text(
            """
            UPDATE bookings SET updated_at = now()
             WHERE booking_id IN (
                   SELECT booking_id FROM bookings
                    WHERE status = ANY(:statuses)
                      AND updated_at < now() - make_interval(secs => :age)
                    ORDER BY updated_at
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED)
            RETURNING booking_id
            """
        ),
        {"statuses": list(statuses), "age": older_than_seconds, "limit": limit},
    )
    return [r[0] for r in rows]


def purge_idempotency_keys(conn: Connection, older_than: timedelta) -> int:
    result = conn.execute(text("DELETE FROM idempotency_keys WHERE created_at < now() - :age"),
                          {"age": older_than})
    return result.rowcount
