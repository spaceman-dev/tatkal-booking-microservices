from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["BOOKING_CONFIRMED", "BOOKING_FAILED", "BOOKING_CANCELLED"]


class Recipient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(pattern=r"^[6-9]\d{9}$", description="10-digit Indian mobile number")
    email: str | None = Field(default=None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "event_id": "4f7d2c1e-5b8a-4c3d-9e2f-1a2b3c4d5e6f",
        "event_type": "BOOKING_CONFIRMED",
        "booking_id": "0b3f6a52-8d4e-4b8e-9a51-1f1c2b7d9e10",
        "recipient": {"mobile": "9876543210", "email": "asha@example.com"},
        "data": {"pnr": "4521876390", "train_no": "12951", "journey_date": "2026-11-20",
                 "travel_class": "3A", "passenger_count": 2, "amount_paise": 579000},
    }]})

    event_id: str = Field(min_length=8, max_length=64, description="Unique per event, used to spot duplicates")
    event_type: EventType
    booking_id: UUID
    recipient: Recipient
    data: dict


class DeliveryOut(BaseModel):
    notification_id: UUID
    event_id: str
    event_type: str
    booking_id: UUID
    channel: Literal["SMS", "EMAIL"]
    recipient: str
    message: str
    status: Literal["PENDING", "SENT"]
    created_at: datetime
    sent_at: datetime | None = None


class PublishOut(BaseModel):
    event_id: str
    duplicate: bool
    deliveries: list[DeliveryOut]


class ProviderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_rate: float = Field(ge=0, le=1)
