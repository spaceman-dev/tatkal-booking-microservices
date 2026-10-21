"""Stand-in for an SMS / e-mail provider. Nothing leaves the machine - a 'sent'
message is a log line. The failure rate lets us show the outbox retrying."""
import logging
import random
import time

from app.domain.templates import mask_email, mask_mobile

log = logging.getLogger(__name__)


class ProviderUnavailable(Exception):
    pass


class SimulatedProvider:
    def __init__(self, failure_rate: float, latency_ms: int):
        self.failure_rate = failure_rate
        self.latency_ms = latency_ms

    def send(self, channel: str, recipient: str, message: str) -> None:
        time.sleep(self.latency_ms / 1000)
        if random.random() < self.failure_rate:
            log.warning("simulated provider failure", extra={"channel": channel})
            raise ProviderUnavailable(f"{channel} provider did not accept the message")
        shown = mask_mobile(recipient) if channel == "SMS" else mask_email(recipient)
        log.info("%s sent to %s", channel, shown, extra={"channel": channel, "recipient": shown,
                                                         "text": message})
