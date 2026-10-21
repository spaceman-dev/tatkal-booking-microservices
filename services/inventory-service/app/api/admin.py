"""Development-only endpoints used by the challenge scripts. The router is only
mounted when ADMIN_API_ENABLED=true."""
from fastapi import APIRouter, Request, Response

from app.api.schemas import FaultIn, HoldStrategyIn

router = APIRouter(prefix="/api/v1/admin", tags=["admin (dev only)"])


@router.get("/hold-strategy", summary="Which hold implementation is active")
def get_strategy(request: Request):
    return {"strategy": request.app.state.holds.strategy}


@router.put("/hold-strategy", summary="Switch between atomic and naive (race demo) holds")
def set_strategy(body: HoldStrategyIn, request: Request):
    request.app.state.holds.strategy = body.strategy
    return {"strategy": body.strategy}


@router.post("/faults", status_code=201, summary="Make the next N calls of an operation fail")
def arm_fault(body: FaultIn, request: Request):
    return request.app.state.faults.arm(body.operation, body.times, body.status_code)


@router.get("/faults", summary="List armed faults")
def list_faults(request: Request):
    return request.app.state.faults.snapshot()


@router.delete("/faults", status_code=204, summary="Clear all faults")
def clear_faults(request: Request):
    request.app.state.faults.clear()
    return Response(status_code=204)
