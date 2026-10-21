"""Schema owned by notification-service. `booking_id` is booking-service's id,
kept only as a reference."""
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)

metadata = MetaData()

notifications = Table(
    "notifications",
    metadata,
    Column("notification_id", Uuid, primary_key=True),
    Column("event_id", String(64), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("booking_id", Uuid, nullable=False, index=True),
    Column("channel", String(5), nullable=False),
    Column("recipient", String(120), nullable=False),
    Column("message", Text, nullable=False),
    Column("status", String(8), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("sent_at", DateTime(timezone=True)),
    # Who is sending a PENDING message, and until when. A claim whose lease ran out
    # belongs to a delivery that died, and the next delivery of the event takes it over.
    Column("claim_token", String(32), nullable=False),
    Column("locked_until", DateTime(timezone=True), nullable=False),
    # The idempotent-consumer guarantee: one message per event per channel, no matter
    # how many times booking-service's outbox delivers the event.
    UniqueConstraint("event_id", "channel", name="uq_notifications_event_channel"),
)
