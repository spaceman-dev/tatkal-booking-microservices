from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChargeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "booking_id": "0b3f6a52-8d4e-4b8e-9a51-1f1c2b7d9e10", "amount_paise": 579000,
        "currency": "INR", "method": "UPI", "payer_ref": "asha@okmock"}]})

    booking_id: UUID
    amount_paise: int = Field(gt=0, description="Amount in paise (Rs 1 = 100). Set by booking-service from the inventory quote.")
    currency: Literal["INR"] = "INR"
    method: Literal["UPI", "CARD"]
    payer_ref: str = Field(min_length=3, max_length=64, description="UPI id or card token. "
                           "Ids starting with 'decline' are declined by the mock bank.")


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="UNSPECIFIED", pattern=r"^[A-Z_]{3,40}$", examples=["SEAT_CONFIRMATION_FAILED"])


class RefundOut(BaseModel):
    refund_id: UUID
    amount_paise: int
    status: Literal["PENDING", "SUCCEEDED"]
    reason: str | None = None
    gateway_refund_id: str | None = None
    created_at: datetime


class PaymentOut(BaseModel):
    payment_id: UUID
    booking_id: UUID
    amount_paise: int
    currency: str
    method: str
    status: Literal["PENDING", "CAPTURED", "DECLINED", "FAILED", "REFUNDED", "VOIDED"]
    gateway_charge_id: str | None = None
    failure_reason: str | None = None
    gateway_attempts: int
    refund: RefundOut | None = None
    created_at: datetime
    updated_at: datetime
