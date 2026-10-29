import os
import socket

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.db import database_is_up

router = APIRouter(tags=["health"])

# container id under Docker, pod name under Kubernetes: shows which replica answered
INSTANCE = socket.gethostname()


@router.get("/health", summary="Readiness: the service and its database")
def health(request: Request):
    db_up = database_is_up(request.app.state.engine)
    body = {
        "status": "UP" if db_up else "DOWN",
        "service": request.app.state.settings.service_name,
        "version": os.environ.get("APP_VERSION", request.app.version),
        "instance": INSTANCE,
        "checks": {"database": "UP" if db_up else "DOWN"},
    }
    return JSONResponse(body, status_code=200 if db_up else 503)


@router.get("/health/live", summary="Liveness: the process answers (no database check)")
def live():
    return {"status": "UP"}
