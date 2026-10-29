import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from common import BOOKING, GATEWAY, INVENTORY, NOTIFICATION, PAYMENT  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def services_running():
    with httpx.Client(timeout=3) as client:
        for url in (BOOKING, INVENTORY, PAYMENT, NOTIFICATION, GATEWAY):
            try:
                client.get(f"{url}/health").raise_for_status()
            except httpx.HTTPError:
                pytest.skip(f"{url} is not running - start it with docker compose up -d (or python scripts/stack.py start)")
    with httpx.Client(timeout=5) as client:
        client.put(f"{GATEWAY}/gateway/v1/admin/mode", json={"mode": "normal"})
        client.delete(f"{INVENTORY}/api/v1/admin/faults")


@pytest.fixture
def api():
    with httpx.Client(timeout=60) as client:
        yield client
