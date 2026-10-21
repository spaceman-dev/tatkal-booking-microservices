from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.api.schemas import (
    AvailabilityOut,
    HoldIn,
    HoldOut,
    OpenQuotaIn,
    QuotaOut,
    ReleaseIn,
    TrainOut,
    TravelClass,
)
from app.core.errors import AppError
from app.services.catalog import CatalogService
from app.services.holds import HoldService

router = APIRouter(prefix="/api/v1")


def catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog


def hold_service(request: Request) -> HoldService:
    return request.app.state.holds


@router.get("/trains", response_model=list[TrainOut], tags=["catalogue"], summary="List trains")
def list_trains(catalog: CatalogService = Depends(catalog_service)):
    return catalog.list_trains()


@router.get(
    "/trains/{train_no}/availability",
    response_model=AvailabilityOut,
    tags=["catalogue"],
    summary="Tatkal availability and fares for one run",
    responses={404: {"description": "Unknown train (TRAIN_NOT_FOUND)"}},
)
def availability(
    train_no: str = Path(pattern=r"^\d{5}$"),
    journey_date: date = Query(..., description="YYYY-MM-DD"),
    catalog: CatalogService = Depends(catalog_service),
):
    return catalog.availability(train_no, journey_date)


@router.post(
    "/inventory",
    status_code=201,
    response_model=QuotaOut,
    tags=["inventory"],
    summary="Open the Tatkal quota for a train run and class",
    responses={
        404: {"description": "Unknown train (TRAIN_NOT_FOUND)"},
        409: {"description": "Quota already open (QUOTA_ALREADY_OPEN)"},
    },
)
def open_quota(body: OpenQuotaIn, catalog: CatalogService = Depends(catalog_service)):
    return catalog.open_quota(body.train_no, body.journey_date, body.travel_class, body.total_seats,
                              body.base_fare_paise)


@router.get(
    "/inventory",
    response_model=QuotaOut,
    tags=["inventory"],
    summary="Current quota counters for one train/date/class",
    responses={404: {"description": "No quota (INVENTORY_NOT_FOUND)"}},
)
def get_quota(
    train_no: str = Query(..., pattern=r"^\d{5}$"),
    journey_date: date = Query(...),
    travel_class: TravelClass = Query(...),
    catalog: CatalogService = Depends(catalog_service),
):
    return catalog.get_quota(train_no, journey_date, travel_class)


@router.post(
    "/holds",
    status_code=201,
    response_model=HoldOut,
    tags=["holds"],
    summary="Hold seats for a booking (saga step 1)",
    description=(
        "Takes `seat_count` seats out of the quota atomically and returns the price (BR3). "
        "Idempotent per `booking_id`: a repeat call returns the existing hold with 200 "
        "and does not take more seats."
    ),
    responses={
        200: {"description": "A hold for this booking already existed and is returned unchanged"},
        404: {"description": "No quota for this train/date/class (INVENTORY_NOT_FOUND)"},
        409: {"description": "Not enough seats left (SEATS_UNAVAILABLE)"},
    },
)
def place_hold(body: HoldIn, response: Response, holds: HoldService = Depends(hold_service)):
    hold, created = holds.place_hold(body.booking_id, body.train_no, body.journey_date, body.travel_class,
                                     body.seat_count)
    if not created:
        response.status_code = 200
    return hold


@router.get("/holds", response_model=list[HoldOut], tags=["holds"], summary="Find the hold of a booking")
def find_holds(booking_id: UUID = Query(...), holds: HoldService = Depends(hold_service)):
    hold = holds.find_by_booking(booking_id)
    return [hold] if hold else []


@router.get(
    "/holds/{hold_id}",
    response_model=HoldOut,
    tags=["holds"],
    summary="Get a hold",
    responses={404: {"description": "No such hold (HOLD_NOT_FOUND)"}},
)
def get_hold(hold_id: UUID, holds: HoldService = Depends(hold_service)):
    hold = holds.get(hold_id)
    if hold is None:
        raise AppError(404, "HOLD_NOT_FOUND", "Hold not found", f"No hold with id {hold_id}")
    return hold


@router.post(
    "/holds/{hold_id}/confirm",
    response_model=HoldOut,
    tags=["holds"],
    summary="Confirm a hold after payment (saga step 3)",
    description="HELD -> CONFIRMED. Repeating it on a CONFIRMED hold is a no-op.",
    responses={
        404: {"description": "No such hold (HOLD_NOT_FOUND)"},
        409: {"description": "Hold was released (HOLD_NOT_ACTIVE)"},
    },
)
def confirm_hold(hold_id: UUID, holds: HoldService = Depends(hold_service)):
    return holds.confirm(hold_id)


@router.post(
    "/holds/{hold_id}/release",
    response_model=HoldOut,
    tags=["holds"],
    summary="Release a hold (compensation or cancellation)",
    description="HELD/CONFIRMED -> RELEASED and the seats go back to the quota exactly once. Safe to repeat.",
    responses={404: {"description": "No such hold (HOLD_NOT_FOUND)"}},
)
def release_hold(hold_id: UUID, body: ReleaseIn | None = None, holds: HoldService = Depends(hold_service)):
    reason = body.reason if body else "UNSPECIFIED"
    return holds.release(hold_id, reason)
