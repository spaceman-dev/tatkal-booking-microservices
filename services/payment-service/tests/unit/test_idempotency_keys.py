import pytest

from app.core.errors import AppError
from app.services.idempotency import fingerprint, require_key


def test_fingerprint_ignores_key_order():
    a = fingerprint("POST", "/api/v1/payments", {"amount_paise": 100, "method": "UPI"})
    b = fingerprint("POST", "/api/v1/payments", {"method": "UPI", "amount_paise": 100})
    assert a == b


def test_fingerprint_changes_when_any_value_changes():
    a = fingerprint("POST", "/api/v1/payments", {"amount_paise": 100, "method": "UPI"})
    assert a != fingerprint("POST", "/api/v1/payments", {"amount_paise": 101, "method": "UPI"})
    assert a != fingerprint("POST", "/api/v1/other", {"amount_paise": 100, "method": "UPI"})


@pytest.mark.parametrize("raw, code", [(None, "IDEMPOTENCY_KEY_REQUIRED"), ("short", "IDEMPOTENCY_KEY_INVALID"),
                                       ("has spaces in it", "IDEMPOTENCY_KEY_INVALID")])
def test_bad_keys_are_rejected_with_400(raw, code):
    with pytest.raises(AppError) as err:
        require_key(raw)
    assert err.value.status == 400
    assert err.value.code == code


def test_uuid_keys_are_accepted():
    assert require_key("3f2b8c1e-7a4d-4e8a-9a51-1f1c2b7d9e10") == "3f2b8c1e-7a4d-4e8a-9a51-1f1c2b7d9e10"
