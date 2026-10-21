from datetime import date
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.repository.tables import seat_holds, seat_inventory, trains


def list_trains(conn: Connection) -> list[dict]:
    rows = conn.execute(select(trains).order_by(trains.c.train_no)).mappings().all()
    return [dict(r) for r in rows]


def get_train(conn: Connection, train_no: str) -> dict | None:
    row = conn.execute(select(trains).where(trains.c.train_no == train_no)).mappings().first()
    return dict(row) if row else None


def get_inventory(conn: Connection, train_no: str, journey_date: date, travel_class: str) -> dict | None:
    row = conn.execute(
        select(seat_inventory).where(
            seat_inventory.c.train_no == train_no,
            seat_inventory.c.journey_date == journey_date,
            seat_inventory.c.travel_class == travel_class,
        )
    ).mappings().first()
    return dict(row) if row else None


def get_inventory_by_id(conn: Connection, inventory_id: int) -> dict | None:
    row = conn.execute(select(seat_inventory).where(seat_inventory.c.inventory_id == inventory_id)).mappings().first()
    return dict(row) if row else None


def list_inventory_for_run(conn: Connection, train_no: str, journey_date: date) -> list[dict]:
    rows = conn.execute(
        select(seat_inventory)
        .where(seat_inventory.c.train_no == train_no, seat_inventory.c.journey_date == journey_date)
        .order_by(seat_inventory.c.travel_class)
    ).mappings().all()
    return [dict(r) for r in rows]


def insert_inventory(conn: Connection, train_no: str, journey_date: date, travel_class: str,
                     total_seats: int, base_fare_paise: int) -> dict:
    row = conn.execute(
        insert(seat_inventory)
        .values(train_no=train_no, journey_date=journey_date, travel_class=travel_class,
                total_seats=total_seats, available_seats=total_seats, base_fare_paise=base_fare_paise)
        .returning(*seat_inventory.c)
    ).mappings().one()
    return dict(row)


# BR1 / challenge 1: check and decrement in one statement.
# A concurrent UPDATE on the same row waits for the row lock, then re-checks
# available_seats >= :n against the committed value, so it matches nothing once seats run out.
# TODO: hot row under heavy load (all holds for a train/date/class queue on this lock)
_ATOMIC_DECREMENT = text(
    """
    UPDATE seat_inventory
       SET available_seats = available_seats - :n,
           version         = version + 1,
           updated_at      = now()
     WHERE inventory_id = :inventory_id
       AND available_seats >= :n
    RETURNING available_seats
    """
)


def decrement_if_available(conn: Connection, inventory_id: int, seat_count: int) -> int | None:
    # seats left after the decrement, None if there weren't enough
    return conn.execute(_ATOMIC_DECREMENT, {"n": seat_count, "inventory_id": inventory_id}).scalar_one_or_none()


def read_available(conn: Connection, inventory_id: int) -> int:
    return conn.execute(
        select(seat_inventory.c.available_seats).where(seat_inventory.c.inventory_id == inventory_id)
    ).scalar_one()


def overwrite_available(conn: Connection, inventory_id: int, new_value: int) -> None:
    # naive strategy only: blind write of a value computed in Python
    conn.execute(
        update(seat_inventory)
        .where(seat_inventory.c.inventory_id == inventory_id)
        .values(available_seats=new_value, version=seat_inventory.c.version + 1, updated_at=func.now())
    )


def increment_available(conn: Connection, inventory_id: int, seat_count: int) -> None:
    conn.execute(
        update(seat_inventory)
        .where(seat_inventory.c.inventory_id == inventory_id)
        .values(available_seats=seat_inventory.c.available_seats + seat_count,
                version=seat_inventory.c.version + 1, updated_at=func.now())
    )


# --- holds ---

def _hold_query():
    return (
        select(
            seat_holds,
            seat_inventory.c.train_no,
            seat_inventory.c.journey_date,
            seat_inventory.c.travel_class,
        )
        .join(seat_inventory, seat_inventory.c.inventory_id == seat_holds.c.inventory_id)
    )


def get_hold(conn: Connection, hold_id: UUID) -> dict | None:
    row = conn.execute(_hold_query().where(seat_holds.c.hold_id == hold_id)).mappings().first()
    return dict(row) if row else None


def get_hold_by_booking(conn: Connection, booking_id: UUID) -> dict | None:
    row = conn.execute(_hold_query().where(seat_holds.c.booking_id == booking_id)).mappings().first()
    return dict(row) if row else None


def claim_hold(conn: Connection, hold_id: UUID, booking_id: UUID, inventory_id: int, seat_count: int,
               fare_per_seat_paise: int, total_amount_paise: int) -> bool:
    # False if the booking already has a hold. Runs before taking seats, so a retry for the
    # same booking waits on the unique index for the first attempt and then finds its hold.
    row = conn.execute(
        pg_insert(seat_holds)
        .values(
            hold_id=hold_id,
            booking_id=booking_id,
            inventory_id=inventory_id,
            seat_count=seat_count,
            fare_per_seat_paise=fare_per_seat_paise,
            total_amount_paise=total_amount_paise,
            status="HELD",
        )
        .on_conflict_do_nothing(index_elements=[seat_holds.c.booking_id])
        .returning(seat_holds.c.hold_id)
    ).first()
    return row is not None


def set_hold_status(conn: Connection, hold_id: UUID, from_statuses: tuple[str, ...], to_status: str,
                    release_reason: str | None = None) -> dict | None:
    # None if the hold is no longer in from_statuses (someone else moved it first)
    values = {"status": to_status, "updated_at": func.now()}
    if release_reason is not None:
        values["release_reason"] = release_reason
    row = conn.execute(
        update(seat_holds)
        .where(seat_holds.c.hold_id == hold_id, seat_holds.c.status.in_(from_statuses))
        .values(**values)
        .returning(seat_holds.c.hold_id, seat_holds.c.inventory_id, seat_holds.c.seat_count)
    ).mappings().first()
    return dict(row) if row else None


def count_holds_by_status(conn: Connection, inventory_id: int) -> dict[str, int]:
    rows = conn.execute(
        select(seat_holds.c.status, func.count(), func.coalesce(func.sum(seat_holds.c.seat_count), 0))
        .where(seat_holds.c.inventory_id == inventory_id)
        .group_by(seat_holds.c.status)
    ).all()
    return {status: int(seats) for status, _count, seats in rows}


# holds still HELD after the TTL (paid CONFIRMED ones never match). SKIP LOCKED and the
# status check let several sweeps run at once without giving seats back twice.
_EXPIRE_HOLDS = text(
    """
    WITH expired AS (
        SELECT hold_id FROM seat_holds
         WHERE status = 'HELD' AND created_at < now() - make_interval(secs => :ttl)
         ORDER BY created_at
         LIMIT :limit
         FOR UPDATE SKIP LOCKED)
    UPDATE seat_holds h
       SET status = 'RELEASED', release_reason = 'HOLD_EXPIRED', updated_at = now()
      FROM expired
     WHERE h.hold_id = expired.hold_id
    RETURNING h.hold_id, h.booking_id, h.inventory_id, h.seat_count
    """
)


def expire_stale_holds(conn: Connection, ttl_seconds: int, limit: int = 100) -> list[dict]:
    rows = conn.execute(_EXPIRE_HOLDS, {"ttl": ttl_seconds, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]
