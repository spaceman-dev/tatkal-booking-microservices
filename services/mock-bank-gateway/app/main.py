import logging
from typing import Literal

from fastapi import APIRouter, FastAPI, Header, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import Mode, get_settings
from app.core.errors import AppError, install_error_handlers
from app.core.logging import setup_logging
from app.core.middleware import CorrelationIdMiddleware
from app.core.openapi import install_problem_openapi
from app.gateway import Gateway

log = logging.getLogger(__name__)

DESCRIPTION = """
Stand-in for an external UPI/card payment gateway. Not one of our business
services: it has no database and keeps everything in memory.

* Deduplicates on the `Idempotency-Key` header like real gateways do.
* Payer ids starting with `decline` or `fail` are declined (`INSUFFICIENT_FUNDS`).
* Behaviour can be switched at runtime: `normal`, `slow`, `down`, `flaky`.
"""


class ChargeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: int = Field(gt=0)
    currency: Literal["INR"] = "INR"
    method: Literal["UPI", "CARD"]
    payer_ref: str = Field(min_length=3, max_length=64)
    merchant_reference: str = Field(min_length=1, max_length=64)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_id: str
    amount_paise: int = Field(gt=0)


class ModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [
        {"mode": "down"}, {"mode": "slow", "slow_latency_ms": 3000}, {"mode": "down", "affects": ["refund"]}]})

    mode: Mode
    latency_ms: int | None = Field(default=None, ge=0, le=60_000)
    slow_latency_ms: int | None = Field(default=None, ge=0, le=120_000)
    failure_rate: float | None = Field(default=None, ge=0, le=1)
    affects: list[Literal["charge", "refund"]] | None = Field(
        default=None, description="Operations hit by down/flaky (default both) - e.g. only refunds failing")


def _require_key(key: str | None) -> str:
    if not key or len(key) > 100:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header required",
                       "Send a unique Idempotency-Key (max 100 chars) with every charge and refund")
    return key


def gw(request: Request) -> Gateway:
    return request.app.state.gateway


router = APIRouter()


@router.get("/health", tags=["health"])
def health(request: Request):
    return {"status": "UP", "service": "mock-bank-gateway", "version": request.app.version,
            "mode": gw(request).mode}


@router.post("/gateway/v1/charges", tags=["charges"], summary="Charge a payer",
             responses={400: {"description": "Missing Idempotency-Key"},
                        409: {"description": "Key reused with another payload"},
                        503: {"description": "Simulated outage"}})
async def create_charge(body: ChargeIn, request: Request,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    charge, replayed = await gw(request).charge(_require_key(idempotency_key), body.model_dump())
    return JSONResponse(charge, headers={"Idempotent-Replayed": "true"} if replayed else None)


@router.get("/gateway/v1/charges", tags=["charges"], summary="Look up charges (reconciliation)")
async def find_charges(request: Request, merchant_reference: str | None = Query(default=None),
                       idempotency_key: str | None = Query(default=None)):
    if idempotency_key:
        charge = await gw(request).find_by_key(idempotency_key)
        return [charge] if charge else []
    if merchant_reference:
        return gw(request).find_by_reference(merchant_reference)
    raise AppError(400, "FILTER_REQUIRED", "Filter required", "Pass merchant_reference or idempotency_key")


@router.get("/gateway/v1/charges/{charge_id}", tags=["charges"], responses={404: {"description": "Unknown charge"}})
def get_charge(charge_id: str, request: Request):
    charge = gw(request).charges.get(charge_id)
    if charge is None:
        raise AppError(404, "CHARGE_NOT_FOUND", "Charge not found", f"No charge {charge_id}")
    return charge


@router.post("/gateway/v1/refunds", tags=["refunds"], summary="Refund a captured charge",
             responses={400: {"description": "Missing Idempotency-Key"},
                        404: {"description": "Unknown charge"},
                        409: {"description": "Not captured / already refunded / key reused"},
                        503: {"description": "Simulated outage"}})
async def create_refund(body: RefundIn, request: Request,
                        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    refund, replayed = await gw(request).refund(_require_key(idempotency_key), body.model_dump())
    return JSONResponse(refund, headers={"Idempotent-Replayed": "true"} if replayed else None)


@router.get("/gateway/v1/stats", tags=["test support"], summary="Counters used as evidence (no duplicates)")
def stats(request: Request):
    return gw(request).snapshot_stats()


@router.get("/gateway/v1/admin/mode", tags=["test support"])
def get_mode(request: Request):
    return gw(request).config()


@router.put("/gateway/v1/admin/mode", tags=["test support"], summary="Switch normal/slow/down/flaky")
def set_mode(body: ModeIn, request: Request):
    return gw(request).configure(body.mode, body.latency_ms, body.slow_latency_ms, body.failure_rate, body.affects)


@router.post("/gateway/v1/admin/reset", tags=["test support"], status_code=204)
def reset(request: Request):
    gw(request).reset()
    return Response(status_code=204)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)
    app = FastAPI(title="Mock Bank Gateway", version="1.0.0", description=DESCRIPTION.strip())
    app.state.settings = settings
    app.state.gateway = Gateway(settings.gateway_mode, settings.gateway_latency_ms,
                                settings.gateway_slow_latency_ms, settings.gateway_failure_rate)
    app.add_middleware(CorrelationIdMiddleware)
    install_error_handlers(app)
    app.include_router(router)
    install_problem_openapi(app)
    log.info("mock gateway configured", extra=app.state.gateway.config())
    return app


app = create_app()
