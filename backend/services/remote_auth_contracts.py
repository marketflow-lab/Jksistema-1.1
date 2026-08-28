"""Configuration and result contracts for central authentication."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse


AUTH_PROTOCOL_VERSION = 1
PERMISSION_KEYS = (
    "analise_promo",
    "renovacao_fixa",
    "vendas",
    "estoque",
    "integracao",
    "etiquetas",
    "full",
    "favoritos",
    "avant",
    "perguntas_pos_venda",
    "anuncios_ml",
    "medias_compras",
    "mercado_full",
    "cadastro",
    "impostos",
    "configuracoes",
    "importacoes",
    "simulador",
    "sala_reuniao",
    "admin_usuarios",
)


class RemoteAuthState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    SUCCESS = "success"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    INTEGRITY_FAILURE = "integrity_failure"


@dataclass(frozen=True)
class RemoteAuthAttempt:
    state: RemoteAuthState
    message: str = ""
    allow_legacy_fallback: bool = False
    user_data: dict[str, Any] = field(default_factory=dict)
    permissions: dict[str, bool] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.state is RemoteAuthState.SUCCESS


@dataclass(frozen=True)
class RemoteAuthConfiguration:
    url: str
    project_id: str
    mode: str
    timeout_seconds: int


REMOTE_ERROR_MESSAGES = {
    "invalid_credentials": "Usuario ou senha invalidos.",
    "inactive_user": "Usuario inativo. Contate o administrador.",
    "expired_access": "Acesso expirado. Contate o administrador.",
    "access_policy_invalid": "O cadastro de acesso precisa ser corrigido pelo administrador.",
    "device_limit": "Limite de dispositivos atingido. Peca ao administrador para resetar os dispositivos.",
    "update_required": "Atualize o JK Sistema para continuar.",
    "rate_limited": "Muitas tentativas de login. Aguarde alguns minutos e tente novamente.",
    "invalid_request": "Os dados de login enviados sao invalidos.",
    "password_unchanged": "A nova senha deve ser diferente da senha atual.",
    "machine_not_registered": "Esta maquina nao esta autorizada para alterar a senha.",
}
UNAVAILABLE_MESSAGE = "Nao foi possivel conectar ao servico de autenticacao. Verifique a internet e tente novamente."
INTEGRITY_MESSAGE = "A resposta do servico de autenticacao nao pode ser validada. Tente novamente mais tarde."


def load_remote_auth_configuration(environ: Mapping[str, str]) -> RemoteAuthConfiguration | None:
    url = str(environ.get("JK_REMOTE_AUTH_URL") or "").strip()
    if not url:
        return None
    project_id = str(
        environ.get("JK_REMOTE_AUTH_PROJECT_ID")
        or environ.get("FIREBASE_PROJECT_ID")
        or ""
    ).strip()
    mode = str(environ.get("JK_REMOTE_AUTH_MODE") or "prefer").strip().lower()
    if mode not in {"prefer", "required"}:
        raise ValueError("invalid_remote_auth_mode")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("invalid_remote_auth_url")
    if parsed.query or parsed.fragment or not parsed.path.endswith("/login"):
        raise ValueError("invalid_remote_auth_url")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,29}", project_id):
        raise ValueError("invalid_remote_auth_project")
    try:
        timeout_seconds = int(str(environ.get("JK_REMOTE_AUTH_TIMEOUT_SECONDS") or "10"))
    except ValueError as exc:
        raise ValueError("invalid_remote_auth_timeout") from exc
    if timeout_seconds < 2 or timeout_seconds > 30:
        raise ValueError("invalid_remote_auth_timeout")
    return RemoteAuthConfiguration(
        url=url.rstrip("/"),
        project_id=project_id,
        mode=mode,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "AUTH_PROTOCOL_VERSION",
    "INTEGRITY_MESSAGE",
    "PERMISSION_KEYS",
    "REMOTE_ERROR_MESSAGES",
    "RemoteAuthAttempt",
    "RemoteAuthConfiguration",
    "RemoteAuthState",
    "UNAVAILABLE_MESSAGE",
    "load_remote_auth_configuration",
]
