"""Tables in payment_db. booking_id is just an id from booking-service, no FK across services."""
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)

metadata = MetaData()

payments = Table(
    "payments",
    metadata,
    Column("payment_id", Uuid, primary_key=True),
    Column("booking_id", Uuid, nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("method", String(4), nullable=False),
    Column("payer_ref", String(64), nullable=False),
    Column("status", String(10), nullable=False),
    Column("gateway_charge_id", String(40)),
    Column("failure_reason", String(40)),
    Column("gateway_attempts", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # BR4: never two payments for one booking, whatever happens upstream
    UniqueConstraint("booking_id", name="uq_payments_booking"),
    CheckConstraint("amount_paise > 0", name="ck_payments_amount_positive"),
    CheckConstraint("status IN ('PENDING','CAPTURED','DECLINED','FAILED','REFUNDED','VOIDED')",
                    name="ck_payments_status"),
)

refunds = Table(
    "refunds",
    metadata,
    Column("refund_id", Uuid, primary_key=True),
    Column("payment_id", Uuid, ForeignKey("payments.payment_id"), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("status", String(10), nullable=False),
    Column("reason", String(40)),
    Column("gateway_refund_id", String(40)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # BR4: one refund per payment
    UniqueConstraint("payment_id", name="uq_refunds_payment"),
    CheckConstraint("amount_paise > 0", name="ck_refunds_amount_positive"),
    CheckConstraint("status IN ('PENDING','SUCCEEDED')", name="ck_refunds_status"),
)

# Idempotency-Key store for POST /payments (Challenge 2): key, request hash, stored response
idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("idem_key", String(100), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("status", String(12), nullable=False),
    Column("owner_token", String(32), nullable=False),
    Column("response_code", Integer),
    # the response text as it was sent, so a replay is byte-for-byte the same
    Column("response_body", Text),
    Column("resource_id", String(64)),
    Column("locked_until", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('IN_PROGRESS','COMPLETED')", name="ck_idem_status"),
)
