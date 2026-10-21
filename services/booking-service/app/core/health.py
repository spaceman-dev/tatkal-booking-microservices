from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.db import database_is_up

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness and database check")
def health(request: Request):
    db_up = database_is_up(request.app.state.engine)
    body = {
        "status": "UP" if db_up else "DOWN",
        "service": request.app.state.settings.service_name,
        "version": request.app.version,
        "checks": {"database": "UP" if db_up else "DOWN"},
    }
    return JSONResponse(body, status_code=200 if db_up else 503)
