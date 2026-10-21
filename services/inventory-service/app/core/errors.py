"""One error format for every service: RFC 9457 problem details plus a
machine-readable `code` and the request's `correlation_id`."""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_correlation_id
from app.domain.errors import RuleViolation

log = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://tatkal.example/problems/"


class AppError(Exception):
    """Raised by the service layer; turned into a problem+json response."""

    def __init__(self, status: int, code: str, title: str, detail: str | None = None, *,
                 headers: dict[str, str] | None = None, **extra):
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers
        self.extra = extra

    def to_problem(self, instance: str | None = None) -> dict:
        return problem(self.status, self.code, self.title, self.detail, instance, **self.extra)


def problem(status: int, code: str, title: str, detail: str | None = None,
            instance: str | None = None, **extra) -> dict:
    body = {
        "type": PROBLEM_TYPE_BASE + code.lower().replace("_", "-"),
        "title": title,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    body["code"] = code
    for key, value in extra.items():
        if value is not None:
            body[key] = value
    body["correlation_id"] = get_correlation_id()
    return jsonable_encoder(body)


def problem_response(body: dict, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=body["status"], media_type=PROBLEM_MEDIA_TYPE, headers=headers)


_HTTP_CODES = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    415: "UNSUPPORTED_MEDIA_TYPE",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        if exc.status >= 500:
            log.warning("request failed: %s", exc.code, extra={"error_code": exc.code})
        return problem_response(exc.to_problem(request.url.path), headers=exc.headers)

    @app.exception_handler(RuleViolation)
    async def _rule_violation(request: Request, exc: RuleViolation):
        log.info("business rule %s rejected request: %s", exc.rule, exc.message,
                 extra={"rule": exc.rule, "error_code": exc.code})
        return problem_response(problem(422, exc.code, "Business rule violated", exc.message,
                                        request.url.path, rule=exc.rule))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            loc = [str(part) for part in err.get("loc", ()) if part != "body"]
            errors.append({"field": ".".join(loc) or "body", "message": err.get("msg", "invalid")})
        return problem_response(problem(422, "VALIDATION_FAILED", "Request validation failed",
                                        "One or more fields are missing or invalid.",
                                        request.url.path, errors=errors))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(problem(exc.status_code, code, detail or code.replace("_", " ").capitalize(),
                                        None, request.url.path))
