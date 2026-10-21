"""Schema owned by booking-service.

`hold_id` (inventory-service) and `payment_id` (payment-service) are plain UUID
columns: we keep the other services' ids, never their data, and never a foreign key.
"""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

bookings = Table(
    "bookings",
    metadata,
    Column("booking_id", Uuid, primary_key=True),
    Column("pnr", String(10)),
    Column("status", String(14), nullable=False),
    Column("train_no", String(5), nullable=False),
    Column("journey_date", Date, nullable=False),
    Column("travel_class", String(2), nullable=False),
    Column("passenger_count", Integer, nullable=False),
    Column("contact_mobile", String(10), nullable=False),
    Column("contact_email", String(120)),
    Column("payment_method", String(4), nullable=False),
    Column("payer_ref", String(64), nullable=False),
    Column("amount_paise", BigInteger),
    Column("refund_paise", BigInteger),
    Column("hold_id", Uuid),
    Column("payment_id", Uuid),
    Column("failure_reason", String(40)),
    Column("compensation_steps", String(60)),
    Column("idempotency_key", String(100)),
    Column("correlation_id", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("pnr", name="uq_bookings_pnr"),
    # One booking per Idempotency-Key, even if a key is taken over after a crash.
    UniqueConstraint("idempotency_key", name="uq_bookings_idempotency_key"),
    CheckConstraint("passenger_count BETWEEN 1 AND 4", name="ck_bookings_passenger_count"),
    CheckConstraint(
        "status IN ('PENDING','SEATS_HELD','PAYMENT_DONE','CONFIRMED','COMPENSATING','FAILED','CANCELLED')",
        name="ck_bookings_status"),
    Index("ix_bookings_status_updated", "status", "updated_at"),
)

passengers = Table(
    "passengers",
    metadata,
    Column("booking_id", Uuid, ForeignKey("bookings.booking_id"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
    Column("age", Integer, nullable=False),
    Column("gender", String(1), nullable=False),
    CheckConstraint("age >= 5", name="ck_passengers_min_age"),
)

# Append-only record of every saga step and compensation - the audit trail we
# show as evidence for Challenge 3.
saga_log = Table(
    "saga_log",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("booking_id", Uuid, nullable=False, index=True),
    Column("step", String(20), nullable=False),
    Column("action", String(10), nullable=False),
    Column("outcome", String(8), nullable=False),
    Column("detail", String(300)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Challenge 2: Idempotency-Key + request fingerprint + the stored response.
idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("idem_key", String(100), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(12), nullable=False),
    Column("owner_token", String(32), nullable=False),
    Column("response_code", Integer),
    Column("response_body", Text),
    Column("resource_id", String(64)),
    Column("locked_until", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('IN_PROGRESS','COMPLETED')", name="ck_idem_status"),
)

# Transactional outbox: events are written in the same transaction as the booking
# change and delivered to notification-service by a background dispatcher.
outbox = Table(
    "outbox",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("booking_id", Uuid, nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String(8), nullable=False, server_default="PENDING"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_error", String(300)),
    Column("correlation_id", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("sent_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('PENDING','SENT','DEAD')", name="ck_outbox_status"),
    Index("ix_outbox_due", "status", "next_attempt_at"),
)
