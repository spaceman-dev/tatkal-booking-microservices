"""Pure ASGI middleware: correlation id in/out, one access log line per request,
and a last-resort handler so even unexpected errors come back as problem+json."""
import json
import logging
import time

from app.core.context import CORRELATION_HEADER, clean_correlation_id, correlation_id_var
from app.core.errors import PROBLEM_MEDIA_TYPE, problem

log = logging.getLogger("app.access")

_QUIET_PATHS = {"/health"}


class CorrelationIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name.decode("latin-1").lower() == CORRELATION_HEADER.lower():
                incoming = value.decode("latin-1")
                break
        correlation_id = clean_correlation_id(incoming)
        token = correlation_id_var.set(correlation_id)

        started = time.perf_counter()
        state = {"status": 500, "response_started": False}

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["response_started"] = True
                headers = list(message.get("headers", []))
                headers.append((CORRELATION_HEADER.lower().encode(), correlation_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            log.exception("unhandled error", extra={"path": scope.get("path")})
            if not state["response_started"]:
                body = problem(500, "INTERNAL_ERROR", "Internal server error",
                               "Unexpected error. Search the logs for this correlation id.",
                               scope.get("path"))
                await send_with_header({
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", PROBLEM_MEDIA_TYPE.encode())],
                })
                await send({"type": "http.response.body", "body": json.dumps(body).encode()})
        finally:
            path = scope.get("path", "")
            if path not in _QUIET_PATHS or state["status"] >= 400:
                log.info(
                    "%s %s -> %s",
                    scope.get("method"), path, state["status"],
                    extra={
                        "method": scope.get("method"),
                        "path": path,
                        "status": state["status"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            correlation_id_var.reset(token)
