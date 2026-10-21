from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TravelClass = Literal["2S", "SL", "CC", "3A", "2A", "EC"]
TrainNo = Annotated[str, Field(pattern=r"^\d{5}$", examples=["12951"])]
HoldStatus = Literal["HELD", "CONFIRMED", "RELEASED"]


class TrainOut(BaseModel):
    train_no: str
    name: str
    from_station: str
    to_station: str
    departure_time: str


class ClassAvailability(BaseModel):
    travel_class: str
    total_seats: int
    available_seats: int
    base_fare_paise: int
    tatkal_charge_paise: int
    fare_per_seat_paise: int


class AvailabilityOut(BaseModel):
    train_no: str
    journey_date: date
    classes: list[ClassAvailability]


class OpenQuotaIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "train_no": "12951", "journey_date": "2026-11-20", "travel_class": "3A",
        "total_seats": 5, "base_fare_paise": 249500}]})

    train_no: TrainNo
    journey_date: date
    travel_class: TravelClass
    total_seats: int = Field(ge=1, le=500)
    base_fare_paise: int = Field(ge=100, le=10_000_000, description="Base fare per seat in paise (Rs 1 = 100)")


class QuotaOut(BaseModel):
    inventory_id: int
    train_no: str
    journey_date: date
    travel_class: str
    total_seats: int
    available_seats: int
    base_fare_paise: int
    version: int
    seats_held: int
    seats_confirmed: int
    seats_released: int
    updated_at: datetime


class HoldIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "booking_id": "0b3f6a52-8d4e-4b8e-9a51-1f1c2b7d9e10", "train_no": "12951",
        "journey_date": "2026-11-20", "travel_class": "3A", "seat_count": 2}]})

    booking_id: UUID
    train_no: TrainNo
    journey_date: date
    travel_class: TravelClass
    seat_count: int = Field(ge=1, le=6)


class HoldOut(BaseModel):
    hold_id: UUID
    booking_id: UUID
    train_no: str
    journey_date: date
    travel_class: str
    seat_count: int
    status: HoldStatus
    fare_per_seat_paise: int
    total_amount_paise: int
    release_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="UNSPECIFIED", pattern=r"^[A-Z_]{3,40}$", examples=["PAYMENT_DECLINED"])


class HoldStrategyIn(BaseModel):
    strategy: Literal["atomic", "naive"]


class FaultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["place_hold", "confirm_hold", "release_hold"]
    times: int = Field(default=1, ge=1, le=100)
    status_code: int = Field(default=500, ge=500, le=599)
