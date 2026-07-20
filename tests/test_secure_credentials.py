from __future__ import annotations

import pytest

from backend.services import secure_credentials


def _memory_store(monkeypatch):
    stored: dict[str, str] = {}

    monkeypatch.setattr(secure_credentials, "_write_target", lambda target, value: stored.__setitem__(target, value))
    monkeypatch.setattr(secure_credentials, "_read_target", lambda target: stored.get(target, ""))
    monkeypatch.setattr(secure_credentials, "_delete_target", lambda target: stored.pop(target, None) is not None)
    monkeypatch.setattr(secure_credentials, "secure_store_available", lambda: True)
    return stored


def test_scoped_secret_uses_hashed_credential_target_and_persists(monkeypatch):
    stored = _memory_store(monkeypatch)
    secret = "shared-sync-test-value"
    logical_target = "shared-sync/private-key/000002/caio/machine-123"

    assert secure_credentials.read_scoped_secret(logical_target) == ""
    assert secure_credentials.write_scoped_secret(logical_target, secret) is True

    expected_target = secure_credentials._scoped_target_name(logical_target)
    assert stored == {expected_target: secret}
    assert expected_target.startswith(f"{secure_credentials.SCOPED_TARGET_PREFIX}/")
    assert "000002" not in expected_target
    assert "caio" not in expected_target
    assert "machine-123" not in expected_target
    assert secure_credentials.read_scoped_secret(logical_target) == secret

    assert secure_credentials.delete_scoped_secret(logical_target) is True
    assert secure_credentials.read_scoped_secret(logical_target) == ""


def test_scoped_secret_empty_value_deletes_existing_value(monkeypatch):
    stored = _memory_store(monkeypatch)
    logical_target = "shared-sync/private-key/client/user/machine"

    assert secure_credentials.write_scoped_secret(logical_target, "test-value") is True
    assert stored
    assert secure_credentials.write_scoped_secret(logical_target, "") is True
    assert stored == {}


def test_scoped_secret_rejects_invalid_logical_targets(monkeypatch):
    _memory_store(monkeypatch)

    for target in ("", "   ", "bad\x00target", "x" * (secure_credentials.SCOPED_TARGET_MAX_CHARS + 1)):
        with pytest.raises(ValueError, match="Destino seguro"):
            secure_credentials.read_scoped_secret(target)


def test_scoped_secret_fails_closed_without_windows_store(monkeypatch):
    monkeypatch.setattr(secure_credentials, "_available", lambda: False)
    monkeypatch.setattr(secure_credentials, "CREDENTIALW", None)
    monkeypatch.setattr(secure_credentials, "PCREDENTIALW", None)
    logical_target = "shared-sync/private-key/client/user/machine"

    assert secure_credentials.secure_store_available() is False
    assert secure_credentials.read_scoped_secret(logical_target) == ""
    assert secure_credentials.delete_scoped_secret(logical_target) is False

    with pytest.raises(RuntimeError, match="Credential Manager indisponivel"):
        secure_credentials.write_scoped_secret(logical_target, "test-value")
