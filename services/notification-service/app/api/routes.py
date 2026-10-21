from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.api.schemas import DeliveryOut, EventIn, ProviderIn, PublishOut
from app.services.notifications import NotificationService

router = APIRouter(prefix="/api/v1", tags=["notifications"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin (dev only)"])


def notification_service(request: Request) -> NotificationService:
    return request.app.state.notifications


@router.post(
    "/notifications",
    status_code=201,
    response_model=PublishOut,
    summary="Send a booking event as SMS (and e-mail if given)",
    description=(
        "Called by booking-service's outbox, which can deliver the same event more than once. "
        "Each channel is sent at most once per `event_id`. A repeat gets 200 with `duplicate: true`. "
        "If the first delivery is still sending, the repeat waits for it (up to 2 s)."
    ),
    responses={
        200: {"description": "Duplicate event, nothing sent again"},
        422: {"description": "Invalid event or missing template data"},
        503: {"description": "PROVIDER_UNAVAILABLE (sent channels stay sent, the rest are released) or "
                             "NOTIFICATION_IN_PROGRESS (another delivery is still sending). Retry later."},
    },
)
def publish(event: EventIn, service: NotificationService = Depends(notification_service)):
    status_code, body = service.publish(event.event_id, event.event_type, event.booking_id,
                                        event.recipient.mobile, event.recipient.email, event.data)
    return JSONResponse(jsonable_encoder(PublishOut.model_validate(body)), status_code=status_code)


@router.get("/notifications", response_model=list[DeliveryOut], summary="Messages sent for a booking")
def list_for_booking(booking_id: UUID = Query(...), service: NotificationService = Depends(notification_service)):
    return service.for_booking(booking_id)


@admin_router.put("/provider", summary="Change the simulated provider's failure rate")
def set_provider(body: ProviderIn, request: Request):
    request.app.state.provider.failure_rate = body.failure_rate
    return {"failure_rate": body.failure_rate}
