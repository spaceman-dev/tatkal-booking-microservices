"""Message templates and the small formatting helpers they need."""

EVENT_TYPES = ("BOOKING_CONFIRMED", "BOOKING_FAILED", "BOOKING_CANCELLED")


class TemplateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

TEMPLATES = {
    "BOOKING_CONFIRMED": (
        "Tatkal booking confirmed. PNR {pnr}, train {train_no} on {journey_date}, class {travel_class}, "
        "{passenger_count} passenger(s). Amount paid Rs {amount}."
    ),
    "BOOKING_FAILED": (
        "Your Tatkal booking for train {train_no} on {journey_date} could not be completed. "
        "Rs {amount} has been refunded to your account. Ref {booking_ref}."
    ),
    "BOOKING_CANCELLED": (
        "PNR {pnr} for train {train_no} on {journey_date} has been cancelled. "
        "As per Tatkal rules no refund is due."
    ),
}

_REQUIRED = {
    "BOOKING_CONFIRMED": ("pnr", "train_no", "journey_date", "travel_class", "passenger_count", "amount_paise"),
    "BOOKING_FAILED": ("train_no", "journey_date", "amount_paise", "booking_ref"),
    "BOOKING_CANCELLED": ("pnr", "train_no", "journey_date"),
}


def format_rupees(paise: int) -> str:
    """Indian digit grouping: 12345678 paise -> '1,23,456.78'."""
    rupees, rest = divmod(int(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{digits}.{rest:02d}"


def render(event_type: str, data: dict) -> str:
    if event_type not in TEMPLATES:
        raise TemplateError("UNKNOWN_EVENT_TYPE", f"No template for event type {event_type}")
    missing = [field for field in _REQUIRED[event_type] if data.get(field) in (None, "")]
    if missing:
        raise TemplateError("TEMPLATE_DATA_MISSING", f"{event_type} needs {', '.join(missing)}")
    values = dict(data)
    if "amount_paise" in values:
        values["amount"] = format_rupees(values["amount_paise"])
    return TEMPLATES[event_type].format(**values)


def mask_mobile(mobile: str) -> str:
    return mobile[:2] + "X" * (len(mobile) - 4) + mobile[-2:] if len(mobile) > 4 else "XXXX"


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"
