"""Authentication use cases independent from Firebase and HTTP adapters."""

from __future__ import annotations

from typing import Any, Protocol

import bcrypt

from .contracts import GatewayChangePasswordRequest, GatewayLoginRequest
from .errors import AuthRejected, GatewayUnavailable
from .policy import (
    AUTH_PROTOCOL_VERSION,
    LoginRateLimiter,
    assert_access_allowed,
    assert_registered_machine,
    assert_supported_version,
    canonical_hash,
    machine_claim_updates,
    machine_hash,
    normalize_machine_ids,
    normalize_max_machines,
    normalize_permissions,
    password_matches,
    password_value,
    uid_for_username,
    user_can_use_unlimited_devices,
    user_client_id,
)


DUMMY_PASSWORD_HASH = "$2b$12$110.wdzOzrIf8hqQsjoIZOPs94qpVworZsDUAuGG74fivqjjKuv1O"


class UserRepository(Protocol):
    def get_user(self, username: str) -> dict[str, Any] | None: ...

    def claim_machine(
        self,
        username: str,
        expected_password_value: str,
        machine_id: str,
    ) -> dict[str, Any]: ...

    def change_password(
        self,
        username: str,
        expected_password_value: str,
        new_password_hash: str,
        machine_id: str,
    ) -> dict[str, Any]: ...


class IdentityTokenIssuer(Protocol):
    def issue(self, uid: str, claims: dict[str, Any]) -> tuple[str, int]: ...


class AuthenticationService:
    def __init__(
        self,
        repository: UserRepository,
        token_issuer: IdentityTokenIssuer,
        minimum_app_version: str,
    ) -> None:
        self._repository = repository
        self._token_issuer = token_issuer
        self._minimum_app_version = minimum_app_version

    def _signed_response(
        self,
        current: dict[str, Any],
        username: str,
        machine_id: str,
        request_nonce: str,
        *,
        code: str,
    ) -> dict[str, Any]:
        expiry = assert_access_allowed(current)
        permissions = normalize_permissions(current.get("permissions") or current.get("permissoes"))
        client_id = user_client_id(current)
        machine_ids = normalize_machine_ids(
            current.get("machine_ids") or current.get("maquinas") or current.get("machines"),
            current.get("machine_id"),
        )
        policy = {
            "active": True,
            "valid_until": expiry,
            "max_machines": normalize_max_machines(
                current.get("max_machines", current.get("limite_maquinas", 1))
            ),
        }
        user_data = {
            "username": username,
            "name": str(current.get("name") or current.get("nome") or username).strip() or username,
            "email": str(current.get("email") or current.get("google_email") or "").strip().lower(),
            "client_id": client_id,
            "machine_id": machine_id,
        }
        signed_payload = {
            "user_data": user_data,
            "permissions": permissions,
            "policy": policy,
            "request_nonce": request_nonce,
        }
        claims = {
            "jk_auth_v": AUTH_PROTOCOL_VERSION,
            "jk_username": username,
            "jk_client_id": client_id,
            "jk_machine_hash": machine_hash(machine_id),
            "jk_response_hash": canonical_hash(signed_payload),
        }
        identity_token, expires_in = self._token_issuer.issue(uid_for_username(username), claims)
        return {
            "success": True,
            "code": code,
            **signed_payload,
            "identity_token": identity_token,
            "expires_in": expires_in,
            "registered_machines": len(machine_ids),
        }

    def authenticate(self, request: GatewayLoginRequest) -> dict[str, Any]:
        assert_supported_version(request.app_version, self._minimum_app_version)
        username = request.username
        supplied_password = request.password.get_secret_value()
        user = self._repository.get_user(username)
        stored_password = password_value(user or {})
        password_is_valid = password_matches(
            supplied_password,
            stored_password or DUMMY_PASSWORD_HASH,
        )
        if not user or not stored_password or not password_is_valid:
            raise AuthRejected("invalid_credentials", 401)
        assert_access_allowed(user)
        current = self._repository.claim_machine(username, stored_password, request.machine_id)
        return self._signed_response(
            current,
            username,
            request.machine_id,
            request.request_nonce,
            code="authenticated",
        )

    def change_password(self, request: GatewayChangePasswordRequest) -> dict[str, Any]:
        assert_supported_version(request.app_version, self._minimum_app_version)
        username = request.username
        current_password = request.current_password.get_secret_value()
        user = self._repository.get_user(username)
        stored_password = password_value(user or {})
        password_is_valid = password_matches(
            current_password,
            stored_password or DUMMY_PASSWORD_HASH,
        )
        if not user or not stored_password or not password_is_valid:
            raise AuthRejected("invalid_credentials", 401)
        assert_access_allowed(user)

        new_password = request.new_password.get_secret_value()
        if len(new_password.encode("utf-8")) > 72:
            raise AuthRejected("invalid_request", 400)
        if password_matches(new_password, stored_password):
            raise AuthRejected("password_unchanged", 400)
        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        current = self._repository.change_password(
            username,
            stored_password,
            new_hash,
            request.machine_id,
        )
        return self._signed_response(
            current,
            username,
            request.machine_id,
            request.request_nonce,
            code="password_changed",
        )


ERROR_MESSAGES = {
    "invalid_credentials": "Usuario ou senha invalidos.",
    "inactive_user": "Usuario inativo. Contate o administrador.",
    "expired_access": "Acesso expirado. Contate o administrador.",
    "access_policy_invalid": "O cadastro de acesso precisa ser corrigido pelo administrador.",
    "device_limit": "Limite de dispositivos atingido. Peca ao administrador para resetar os dispositivos.",
    "machine_not_registered": "Esta maquina nao esta autorizada para alterar a senha.",
    "update_required": "Atualize o JK Sistema para continuar.",
    "rate_limited": "Muitas tentativas de login. Aguarde alguns minutos e tente novamente.",
    "password_unchanged": "A nova senha deve ser diferente da senha atual.",
    "invalid_request": "Os dados enviados sao invalidos.",
    "auth_unavailable": "Servico de autenticacao temporariamente indisponivel.",
}


__all__ = [
    "AUTH_PROTOCOL_VERSION",
    "AuthRejected",
    "AuthenticationService",
    "ERROR_MESSAGES",
    "GatewayUnavailable",
    "LoginRateLimiter",
    "assert_access_allowed",
    "assert_registered_machine",
    "canonical_hash",
    "machine_claim_updates",
    "machine_hash",
    "normalize_machine_ids",
    "normalize_max_machines",
    "normalize_permissions",
    "password_value",
    "uid_for_username",
    "user_can_use_unlimited_devices",
    "user_client_id",
]
