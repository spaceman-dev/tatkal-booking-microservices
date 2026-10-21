from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
)

metadata = MetaData()

trains = Table(
    "trains",
    metadata,
    Column("train_no", String(5), primary_key=True),
    Column("name", String(80), nullable=False),
    Column("from_station", String(5), nullable=False),
    Column("to_station", String(5), nullable=False),
    Column("departure_time", String(5), nullable=False),
)

seat_inventory = Table(
    "seat_inventory",
    metadata,
    Column("inventory_id", BigInteger, Identity(), primary_key=True),
    Column("train_no", String(5), ForeignKey("trains.train_no"), nullable=False),
    Column("journey_date", Date, nullable=False),
    Column("travel_class", String(2), nullable=False),
    Column("total_seats", Integer, nullable=False),
    Column("available_seats", Integer, nullable=False),
    Column("base_fare_paise", Integer, nullable=False),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("train_no", "journey_date", "travel_class", name="uq_inventory_train_date_class"),
    CheckConstraint("total_seats > 0", name="ck_inventory_total_positive"),
    # safety net for BR1, the conditional UPDATE is what actually stops overselling
    CheckConstraint("available_seats >= 0", name="ck_inventory_available_non_negative"),
    CheckConstraint("available_seats <= total_seats", name="ck_inventory_available_within_total"),
)

seat_holds = Table(
    "seat_holds",
    metadata,
    Column("hold_id", Uuid, primary_key=True),
    # booking-service's id, no FK since bookings live in another database.
    # Unique: one hold per booking, so the saga can retry POST /holds.
    Column("booking_id", Uuid, nullable=False),
    Column("inventory_id", BigInteger, ForeignKey("seat_inventory.inventory_id"), nullable=False),
    Column("seat_count", Integer, nullable=False),
    Column("fare_per_seat_paise", Integer, nullable=False),
    Column("total_amount_paise", Integer, nullable=False),
    Column("status", String(10), nullable=False),
    Column("release_reason", String(40)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("booking_id", name="uq_seat_holds_booking"),
    CheckConstraint("seat_count BETWEEN 1 AND 6", name="ck_holds_seat_count"),
    CheckConstraint("status IN ('HELD', 'CONFIRMED', 'RELEASED')", name="ck_holds_status"),
)
