from __future__ import annotations

from copy import deepcopy

import bcrypt
import pytest

from cloud.auth_gateway.app.contracts import GatewayLoginRequest
from cloud.auth_gateway.app.contracts import GatewayChangePasswordRequest
from cloud.auth_gateway.app.domain import (
    AuthRejected,
    AuthenticationService,
    assert_registered_machine,
    canonical_hash,
    machine_claim_updates,
    normalize_permissions,
    uid_for_username,
)

NONCE = "n" * 43


class FakeRepository:
    def __init__(self, user):
        self.user = deepcopy(user)
        self.claims = []

    def get_user(self, username):
        return deepcopy(self.user) if username == self.user.get("username") else None

    def claim_machine(self, username, expected_password_value, machine_id):
        assert username == self.user["username"]
        assert expected_password_value == self.user["password_hash"]
        updates = machine_claim_updates(self.user, username, machine_id)
        self.user.update(updates)
        self.claims.append((username, machine_id))
        return deepcopy(self.user)

    def change_password(self, username, expected_password_value, new_password_hash, machine_id):
        assert username == self.user["username"]
        assert expected_password_value == self.user["password_hash"]
        assert_registered_machine(self.user, machine_id)
        self.user["password_hash"] = new_password_hash
        return deepcopy(self.user)


class FakeIssuer:
    def __init__(self):
        self.calls = []

    def issue(self, uid, claims):
        self.calls.append((uid, claims))
        return "firebase-token", 3600


def base_user():
    return {
        "username": "usuario",
        "password_hash": bcrypt.hashpw(b"senha correta", bcrypt.gensalt(rounds=4)).decode("utf-8"),
        "name": "Usuario",
        "email": "usuario@example.test",
        "client_id": "cliente-central",
        "permissions": {"vendas": True},
        "active": True,
        "valid_until": "31/12/2030",
        "machine_ids": [],
        "max_machines": 1,
    }


def test_gateway_authenticates_from_central_tenant_and_signs_complete_response():
    repository = FakeRepository(base_user())
    issuer = FakeIssuer()
    service = AuthenticationService(repository, issuer, "1.0.124")
    request = GatewayLoginRequest(
        username="usuario",
        password="senha correta",
        machine_id="pc:novo",
        app_version="1.0.124",
        request_nonce=NONCE,
    )

    result = service.authenticate(request)

    assert result["success"] is True
    assert result["user_data"]["client_id"] == "cliente-central"
    assert repository.claims == [("usuario", "pc:novo")]
    uid, claims = issuer.calls[0]
    assert uid == uid_for_username("usuario")
    assert claims["jk_client_id"] == "cliente-central"
    signed = {
        "user_data": result["user_data"],
        "permissions": result["permissions"],
        "policy": result["policy"],
        "request_nonce": result["request_nonce"],
    }
    assert claims["jk_response_hash"] == canonical_hash(signed)


def test_gateway_does_not_claim_device_or_issue_token_for_wrong_password():
    repository = FakeRepository(base_user())
    issuer = FakeIssuer()
    service = AuthenticationService(repository, issuer, "1.0.124")

    with pytest.raises(AuthRejected) as error:
        service.authenticate(
            GatewayLoginRequest(
                username="usuario",
                password="senha errada",
                machine_id="pc:novo",
                app_version="1.0.124",
                request_nonce=NONCE,
            )
        )

    assert error.value.code == "invalid_credentials"
    assert repository.claims == []
    assert issuer.calls == []


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"active": False}, "inactive_user"),
        ({"valid_until": "01/01/2020"}, "expired_access"),
        ({"valid_until": "data-invalida"}, "access_policy_invalid"),
    ],
)
def test_gateway_fails_closed_for_revoked_expired_or_malformed_policy(change, expected_code):
    user = base_user()
    user.update(change)
    service = AuthenticationService(FakeRepository(user), FakeIssuer(), "1.0.124")

    with pytest.raises(AuthRejected) as error:
        service.authenticate(
            GatewayLoginRequest(
                username="usuario",
                password="senha correta",
                machine_id="pc:novo",
                app_version="1.0.124",
                request_nonce=NONCE,
            )
        )

    assert error.value.code == expected_code


def test_machine_limit_is_atomic_policy_and_full_user_remains_unrestricted():
    user = base_user()
    user["machine_ids"] = ["pc:existente"]

    with pytest.raises(AuthRejected) as error:
        machine_claim_updates(user, "usuario", "pc:novo")
    assert error.value.code == "device_limit"

    user["permissions"] = {"full": True}
    updates = machine_claim_updates(user, "usuario", "pc:novo")
    assert updates["machine_ids"] == ["pc:existente", "pc:novo"]


def test_permissions_contract_is_closed_and_full_expands_all_permissions():
    normalized = normalize_permissions({"vendas": "sim", "unknown": True})
    assert normalized["vendas"] is True
    assert "unknown" not in normalized

    full = normalize_permissions({"full": True})
    assert all(full.values())


def test_gateway_requires_minimum_desktop_version():
    service = AuthenticationService(FakeRepository(base_user()), FakeIssuer(), "1.0.124")
    with pytest.raises(AuthRejected) as error:
        service.authenticate(
            GatewayLoginRequest(
                username="usuario",
                password="senha correta",
                machine_id="pc:novo",
                app_version="1.0.123",
                request_nonce=NONCE,
            )
        )
    assert error.value.code == "update_required"


def test_gateway_changes_central_password_only_from_registered_machine():
    user = base_user()
    user["machine_ids"] = ["pc:registrado"]
    repository = FakeRepository(user)
    issuer = FakeIssuer()
    service = AuthenticationService(repository, issuer, "1.0.124")

    result = service.change_password(
        GatewayChangePasswordRequest(
            username="usuario",
            current_password="senha correta",
            new_password="senha nova segura",
            machine_id="pc:registrado",
            app_version="1.0.124",
            request_nonce=NONCE,
        )
    )

    assert result["code"] == "password_changed"
    assert bcrypt.checkpw(b"senha nova segura", repository.user["password_hash"].encode("utf-8"))
    assert not bcrypt.checkpw(b"senha correta", repository.user["password_hash"].encode("utf-8"))
    assert issuer.calls


def test_gateway_refuses_password_change_from_unregistered_machine():
    user = base_user()
    user["machine_ids"] = ["pc:registrado"]
    service = AuthenticationService(FakeRepository(user), FakeIssuer(), "1.0.124")

    with pytest.raises(AuthRejected) as error:
        service.change_password(
            GatewayChangePasswordRequest(
                username="usuario",
                current_password="senha correta",
                new_password="senha nova segura",
                machine_id="pc:nao-registrado",
                app_version="1.0.124",
                request_nonce=NONCE,
            )
        )

    assert error.value.code == "machine_not_registered"


def test_gateway_rejects_new_password_above_bcrypt_byte_limit():
    user = base_user()
    user["machine_ids"] = ["pc:registrado"]
    service = AuthenticationService(FakeRepository(user), FakeIssuer(), "1.0.124")

    with pytest.raises(AuthRejected) as error:
        service.change_password(
            GatewayChangePasswordRequest(
                username="usuario",
                current_password="senha correta",
                new_password="á" * 40,
                machine_id="pc:registrado",
                app_version="1.0.124",
                request_nonce=NONCE,
            )
        )

    assert error.value.code == "invalid_request"
