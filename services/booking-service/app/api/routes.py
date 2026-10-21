from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder

from app.api.schemas import BookingIn, BookingOut, BookingStatus, SagaStepOut
from app.core.errors import AppError
from app.domain.booking_rules import validate_passengers
from app.services.idempotency import IdempotencyStore, fingerprint, require_key, run_idempotent
from app.services.saga import BookingRequest, BookingSaga

router = APIRouter(prefix="/api/v1", tags=["bookings"])


def saga_service(request: Request) -> BookingSaga:
    return request.app.state.saga


def idempotency_store(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency


def to_out(booking: dict) -> dict:
    view = {**booking, "contact": {"mobile": booking["contact_mobile"], "email": booking["contact_email"]}}
    return jsonable_encoder(BookingOut.model_validate(view))


@router.post(
    "/bookings",
    status_code=201,
    response_model=BookingOut,
    summary="Book Tatkal seats (runs the booking saga)",
    description=(
        "Holds the seats, takes the payment and confirms the seats, compensating if a step fails. "
        "Requires an `Idempotency-Key`: repeating the request with the same key and body returns the "
        "stored result (header `Idempotent-Replayed: true`) and never books or charges twice; the same key "
        "with a different body is rejected with 422."
    ),
    responses={
        400: {"description": "Missing or malformed Idempotency-Key"},
        402: {"description": "Payment declined; seats released (PAYMENT_DECLINED)"},
        404: {"description": "No Tatkal quota for this train/date/class (INVENTORY_NOT_FOUND)"},
        409: {"description": "Not enough seats (SEATS_UNAVAILABLE) or the same key is still in progress"},
        422: {"description": "Invalid request, BR2 violated, or key reused with another body"},
        502: {"description": "Failed after payment; refunded and seats released (SEAT_CONFIRMATION_FAILED)"},
        503: {"description": "Inventory or bank unreachable; nothing charged"},
        504: {"description": "Payment outcome unknown; booking failed and any charge is refunded"},
    },
)
def create_booking(
    body: BookingIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    saga: BookingSaga = Depends(saga_service),
    store: IdempotencyStore = Depends(idempotency_store),
):
    key = require_key(idempotency_key)
    passengers = [p.model_dump() for p in body.passengers]
    validate_passengers(passengers)  # BR2 - rejected before anything is stored or reserved

    command = BookingRequest(
        train_no=body.train_no, journey_date=body.journey_date, travel_class=body.travel_class,
        passengers=passengers, mobile=body.contact.mobile, email=body.contact.email,
        payment_method=body.payment.method, payer_ref=body.payment.payer_ref, idempotency_key=key,
    )
    request_hash = fingerprint("POST", "/api/v1/bookings", body.model_dump(mode="json"))

    def action():
        status_code, booking = saga.place_booking(command)
        return status_code, to_out(booking)

    return run_idempotent(store, key, request_hash, request.url.path, action, resource_field="booking_id")


@router.get("/bookings", response_model=list[BookingOut], summary="List bookings (newest first)")
def list_bookings(
    status: BookingStatus | None = Query(default=None),
    train_no: str | None = Query(default=None, pattern=r"^\d{5}$"),
    journey_date: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    saga: BookingSaga = Depends(saga_service),
):
    return [to_out(b) for b in saga.list_bookings(status, train_no, journey_date, limit)]


@router.get("/bookings/{booking_id}", response_model=BookingOut, summary="Get a booking",
            responses={404: {"description": "No such booking (BOOKING_NOT_FOUND)"}})
def get_booking(booking_id: UUID, saga: BookingSaga = Depends(saga_service)):
    booking = saga.get(booking_id)
    if booking is None:
        raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found", f"No booking with id {booking_id}")
    return to_out(booking)


@router.get("/bookings/{booking_id}/saga", response_model=list[SagaStepOut],
            summary="Saga log: every step and compensation for a booking",
            responses={404: {"description": "No such booking (BOOKING_NOT_FOUND)"}})
def get_saga_log(booking_id: UUID, saga: BookingSaga = Depends(saga_service)):
    if saga.get(booking_id) is None:
        raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found", f"No booking with id {booking_id}")
    return saga.saga_log(booking_id)


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=BookingOut,
    summary="Cancel a confirmed booking (BR7)",
    description="Seats go back to the quota; a confirmed Tatkal ticket gets no refund. Cancelling twice is harmless.",
    responses={
        404: {"description": "No such booking (BOOKING_NOT_FOUND)"},
        409: {"description": "Booking is not CONFIRMED (BOOKING_NOT_CANCELLABLE)"},
        503: {"description": "Seats could not be released; still CONFIRMED, retry is safe"},
    },
)
def cancel_booking(booking_id: UUID, saga: BookingSaga = Depends(saga_service)):
    return to_out(saga.cancel(booking_id))
