from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.encoders import jsonable_encoder

from app.api.schemas import ChargeIn, PaymentOut, RefundIn, VoidIn
from app.core.errors import AppError
from app.services.idempotency import IdempotencyStore, fingerprint, require_key, run_idempotent
from app.services.payments import PaymentService

router = APIRouter(prefix="/api/v1", tags=["payments"])


def payment_service(request: Request) -> PaymentService:
    return request.app.state.payments


def idempotency_store(request: Request) -> IdempotencyStore:
    return request.app.state.idempotency


@router.post(
    "/payments",
    status_code=201,
    response_model=PaymentOut,
    summary="Charge a booking (saga step 2)",
    description=(
        "Unsafe to repeat, so an `Idempotency-Key` header is required. The same key with the same body "
        "returns the stored response (header `Idempotent-Replayed: true`); the same key with a different "
        "body is rejected with 422. A booking can only ever have one payment (BR4)."
    ),
    responses={
        400: {"description": "Missing or malformed Idempotency-Key"},
        402: {"description": "Declined by the bank (PAYMENT_DECLINED)"},
        409: {"description": "Booking already has a payment (PAYMENT_ALREADY_EXISTS) or the same key is "
                             "still in progress (IDEMPOTENCY_REQUEST_IN_PROGRESS)"},
        503: {"description": "Gateway unreachable, nothing charged (GATEWAY_UNAVAILABLE)"},
        504: {"description": "Gateway timed out, outcome unknown (PAYMENT_OUTCOME_UNKNOWN)"},
    },
)
def create_payment(
    body: ChargeIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    payments: PaymentService = Depends(payment_service),
    store: IdempotencyStore = Depends(idempotency_store),
):
    key = require_key(idempotency_key)
    payload = body.model_dump(mode="json")
    request_hash = fingerprint("POST", "/api/v1/payments", payload)

    def action():
        status_code, payment = payments.charge(body.booking_id, body.amount_paise, body.currency, body.method,
                                               body.payer_ref)
        return status_code, jsonable_encoder(PaymentOut.model_validate(payment))

    return run_idempotent(store, key, request_hash, request.url.path, action, resource_field="payment_id")


@router.post(
    "/payments/void",
    response_model=PaymentOut,
    summary="Void the payment of a booking (compensation when no payment is visible yet)",
    description=(
        "If the booking has no payment yet, records a VOIDED one (failure_reason VOIDED_BEFORE_CHARGE), so a "
        "charge request still on its way is refused by UNIQUE(booking_id) and never reaches the bank. If the "
        "booking already has a payment, it is returned unchanged and can be refunded as usual. Safe to repeat."
    ),
)
def void_payment(body: VoidIn, payments: PaymentService = Depends(payment_service)):
    return payments.void_for_booking(body.booking_id, body.amount_paise, body.currency, body.method,
                                     body.payer_ref)


@router.get("/payments", response_model=list[PaymentOut], summary="Find the payment of a booking")
def find_payments(booking_id: UUID = Query(...), payments: PaymentService = Depends(payment_service)):
    payment = payments.find_by_booking(booking_id)
    return [payment] if payment else []


@router.get("/payments/{payment_id}", response_model=PaymentOut, summary="Get a payment",
            responses={404: {"description": "No such payment (PAYMENT_NOT_FOUND)"}})
def get_payment(payment_id: UUID, payments: PaymentService = Depends(payment_service)):
    payment = payments.get(payment_id)
    if payment is None:
        raise AppError(404, "PAYMENT_NOT_FOUND", "Payment not found", f"No payment with id {payment_id}")
    return payment


@router.post(
    "/payments/{payment_id}/refund",
    response_model=PaymentOut,
    summary="Refund a payment in full (compensation of step 2)",
    description=(
        "Naturally idempotent - a payment has at most one refund (BR4), so repeating the call returns "
        "the same REFUNDED payment. A PENDING payment is first reconciled with the gateway."
    ),
    responses={
        404: {"description": "No such payment (PAYMENT_NOT_FOUND)"},
        409: {"description": "Nothing was taken, nothing to refund (PAYMENT_NOT_REFUNDABLE)"},
        503: {"description": "Charge still in flight (PAYMENT_IN_PROGRESS) or the gateway did not confirm "
                             "(REFUND_PENDING / GATEWAY_UNAVAILABLE); safe to retry"},
    },
)
def refund_payment(payment_id: UUID, body: RefundIn | None = None,
                   payments: PaymentService = Depends(payment_service)):
    return payments.refund(payment_id, body.reason if body else "UNSPECIFIED")
