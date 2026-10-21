from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TravelClass = Literal["2S", "SL", "CC", "3A", "2A", "EC"]
TrainNo = Annotated[str, Field(pattern=r"^\d{5}$", examples=["12951"])]
BookingStatus = Literal["PENDING", "SEATS_HELD", "PAYMENT_DONE", "CONFIRMED", "COMPENSATING", "FAILED", "CANCELLED"]


class PassengerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z][A-Za-z .'-]*$")
    # format check only, the age limit itself is BR2 (domain/booking_rules.py)
    age: int = Field(ge=0, le=125)
    gender: Literal["M", "F", "T"]


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(pattern=r"^[6-9]\d{9}$", description="10-digit Indian mobile number")
    email: str | None = Field(default=None, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["UPI", "CARD"]
    payer_ref: str = Field(min_length=3, max_length=64, description="UPI id or card token; ids starting "
                           "with 'decline' are declined by the mock bank")


class BookingIn(BaseModel):
    """No price in the request: the amount always comes from inventory-service (BR3)."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "train_no": "12951",
        "journey_date": "2026-11-20",
        "travel_class": "3A",
        "passengers": [{"name": "Asha Rao", "age": 34, "gender": "F"},
                       {"name": "Vikram Rao", "age": 36, "gender": "M"}],
        "contact": {"mobile": "9876543210", "email": "asha@example.com"},
        "payment": {"method": "UPI", "payer_ref": "asha@okmock"},
    }]})

    train_no: TrainNo
    journey_date: date
    travel_class: TravelClass
    # BR2 allows 1 to 4, this limit only stops silly payloads
    passengers: list[PassengerIn] = Field(max_length=10)
    contact: ContactIn
    payment: PaymentIn


class PassengerOut(BaseModel):
    name: str
    age: int
    gender: str


class ContactOut(BaseModel):
    mobile: str
    email: str | None = None


class BookingOut(BaseModel):
    booking_id: UUID
    pnr: str | None = None
    status: BookingStatus
    train_no: str
    journey_date: date
    travel_class: str
    passengers: list[PassengerOut]
    contact: ContactOut
    amount_paise: int | None = Field(default=None, description="Fare quoted by inventory-service (BR3)")
    refund_paise: int | None = None
    hold_id: UUID | None = Field(default=None, description="inventory-service hold (id only)")
    payment_id: UUID | None = Field(default=None, description="payment-service payment (id only)")
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class SagaStepOut(BaseModel):
    step: str
    action: Literal["EXECUTE", "COMPENSATE"]
    outcome: Literal["OK", "FAILED"]
    detail: str | None = None
    created_at: datetime
