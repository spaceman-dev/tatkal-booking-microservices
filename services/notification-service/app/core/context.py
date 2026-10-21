"""Request-scoped context shared by logging and outbound HTTP calls."""
import re
import uuid
from contextvars import ContextVar

CORRELATION_HEADER = "X-Correlation-ID"

# keep a caller's id only if it looks sane, otherwise we make a new one
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def clean_correlation_id(raw: str | None) -> str:
    if raw and _VALID_ID.match(raw):
        return raw
    return new_correlation_id()


def get_correlation_id() -> str:
    return correlation_id_var.get()
