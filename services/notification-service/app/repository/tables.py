"""Our only table. booking_id comes from booking-service, no FK since that's a different DB."""
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
    # which delivery holds a PENDING row and until when. once locked_until has passed
    # that delivery is assumed dead and the next delivery of the event takes the row over
    Column("claim_token", String(32), nullable=False),
    Column("locked_until", DateTime(timezone=True), nullable=False),
    # one row per event per channel, however many times the outbox redelivers the event
    UniqueConstraint("event_id", "channel", name="uq_notifications_event_channel"),
)
