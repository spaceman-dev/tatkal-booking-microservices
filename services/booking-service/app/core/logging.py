"""JSON log lines on stdout, each carrying the service name and correlation id."""
import json
import logging
import sys
from datetime import datetime, timezone

from app.core.context import correlation_id_var

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {
    "message", "asctime", "taskName", "color_message"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service_name,
            "correlation_id": correlation_id_var.get(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())

    # uvicorn is started with log_config=None, so route its loggers through ours.
    for name in ("uvicorn", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True
    # We write our own access line from the middleware (it has the correlation id).
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
