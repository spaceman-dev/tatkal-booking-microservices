from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from app.repository.tables import payments, refunds


def insert_payment(conn: Connection, payment_id: UUID, booking_id: UUID, amount_paise: int, currency: str,
                   method: str, payer_ref: str) -> None:
    conn.execute(insert(payments).values(
        payment_id=payment_id, booking_id=booking_id, amount_paise=amount_paise, currency=currency,
        method=method, payer_ref=payer_ref, status="PENDING"))


def insert_voided_if_absent(conn: Connection, payment_id: UUID, booking_id: UUID, amount_paise: int,
                            currency: str, method: str, payer_ref: str) -> bool:
    """VOIDED row for a booking with no payment yet. Shares UNIQUE(booking_id) with a real
    charge, so whoever inserts first wins. False if the booking already had a payment."""
    row = conn.execute(
        pg_insert(payments)
        .values(payment_id=payment_id, booking_id=booking_id, amount_paise=amount_paise, currency=currency,
                method=method, payer_ref=payer_ref, status="VOIDED", failure_reason="VOIDED_BEFORE_CHARGE")
        .on_conflict_do_nothing(index_elements=[payments.c.booking_id])
        .returning(payments.c.payment_id)
    ).first()
    return row is not None


def get_status(conn: Connection, payment_id: UUID) -> str | None:
    return conn.execute(select(payments.c.status).where(payments.c.payment_id == payment_id)).scalar()


def get_payment(conn: Connection, payment_id: UUID) -> dict | None:
    row = conn.execute(select(payments).where(payments.c.payment_id == payment_id)).mappings().first()
    return dict(row) if row else None


def get_payment_by_booking(conn: Connection, booking_id: UUID) -> dict | None:
    row = conn.execute(select(payments).where(payments.c.booking_id == booking_id)).mappings().first()
    return dict(row) if row else None


def set_status(conn: Connection, payment_id: UUID, from_statuses: tuple[str, ...], to_status: str,
               **fields) -> bool:
    """Only moves the payment if it is still in one of from_statuses. False means
    another request moved it first."""
    result = conn.execute(
        update(payments)
        .where(payments.c.payment_id == payment_id, payments.c.status.in_(from_statuses))
        .values(status=to_status, updated_at=func.now(), **fields)
    )
    return result.rowcount == 1


def record_attempts(conn: Connection, payment_id: UUID, attempts: int) -> None:
    conn.execute(update(payments).where(payments.c.payment_id == payment_id)
                 .values(gateway_attempts=payments.c.gateway_attempts + attempts, updated_at=func.now()))


def set_fields(conn: Connection, payment_id: UUID, **fields) -> None:
    conn.execute(update(payments).where(payments.c.payment_id == payment_id).values(updated_at=func.now(), **fields))


def get_refund_for_payment(conn: Connection, payment_id: UUID) -> dict | None:
    row = conn.execute(select(refunds).where(refunds.c.payment_id == payment_id)).mappings().first()
    return dict(row) if row else None


def insert_refund(conn: Connection, refund_id: UUID, payment_id: UUID, amount_paise: int, reason: str) -> None:
    conn.execute(insert(refunds).values(refund_id=refund_id, payment_id=payment_id, amount_paise=amount_paise,
                                        status="PENDING", reason=reason))


def mark_refund_succeeded(conn: Connection, refund_id: UUID, gateway_refund_id: str) -> None:
    conn.execute(update(refunds).where(refunds.c.refund_id == refund_id)
                 .values(status="SUCCEEDED", gateway_refund_id=gateway_refund_id, updated_at=func.now()))
