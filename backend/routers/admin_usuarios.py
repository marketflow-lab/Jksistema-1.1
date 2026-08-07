"""Admin, auth, users, presence, and internal chat router definitions."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from fastapi import APIRouter

from backend.schemas import LoginResponse
from backend.services import admin_usuarios


@dataclass(frozen=True)
class LegacyRouteSpec:
    method: str
    path: str
    endpoint_name: str
    response_model_name: str = ""


LEGACY_ADMIN_USUARIOS_ROUTES: tuple[LegacyRouteSpec, ...] = (
    LegacyRouteSpec("GET", "/api/admin/access-backend", "admin_status_controle_acesso"),
    LegacyRouteSpec("GET", "/api/firebase/realtime-presence/session", "firebase_realtime_presence_session"),
    LegacyRouteSpec("GET", "/api/admin/users", "admin_listar_usuarios"),
    LegacyRouteSpec("POST", "/api/admin/users/message", "admin_enviar_mensagem_usuario"),
    LegacyRouteSpec("POST", "/api/admin/users", "admin_salvar_usuario"),
    LegacyRouteSpec("PUT", "/api/admin/users/{username}/password", "admin_trocar_senha_usuario"),
    LegacyRouteSpec("PUT", "/api/auth/change-password", "trocar_minha_senha"),
    LegacyRouteSpec("GET", "/api/auth/me", "minha_sessao_auth", "LoginResponse"),
    LegacyRouteSpec("PUT", "/api/admin/users/{username}/reset-devices", "admin_resetar_dispositivos_usuario"),
    LegacyRouteSpec("PUT", "/api/admin/users/{username}/status", "admin_alterar_status_usuario"),
    LegacyRouteSpec("PUT", "/api/admin/users/{username}/permissions", "admin_alterar_permissoes_usuario"),
    LegacyRouteSpec("PUT", "/api/admin/users/{username}/max-machines", "admin_alterar_limite_dispositivos_usuario"),
    LegacyRouteSpec("DELETE", "/api/admin/users/{username}", "admin_excluir_usuario"),
    LegacyRouteSpec("GET", "/api/admin/users/online", "admin_listar_usuarios_online"),
    LegacyRouteSpec("POST", "/api/user/machines/heartbeat", "user_machine_heartbeat"),
    LegacyRouteSpec("GET", "/api/user/machines/online", "user_machines_online"),
    LegacyRouteSpec("GET", "/api/user/chat/contacts", "user_chat_contacts"),
    LegacyRouteSpec("POST", "/api/user/chat/typing", "user_chat_typing"),
    LegacyRouteSpec("GET", "/api/user/chat/typing", "user_chat_typing_get"),
    LegacyRouteSpec("GET", "/api/user/chat/unread", "user_chat_unread"),
    LegacyRouteSpec("GET", "/api/user/chat/history", "user_chat_history"),
    LegacyRouteSpec("POST", "/api/user/chat/send", "user_chat_send"),
    LegacyRouteSpec("GET", "/api/user/messages", "user_admin_messages"),
    LegacyRouteSpec("GET", "/api/user/messages/stream", "user_admin_messages_stream"),
    LegacyRouteSpec("POST", "/api/user/messages/{message_id}/read", "user_admin_message_read"),
    LegacyRouteSpec("GET", "/api/auth/google/config", "google_auth_config"),
    LegacyRouteSpec("GET", "/api/auth/google/start", "google_auth_start"),
    LegacyRouteSpec("GET", "/api/auth/google/result/{state}", "google_auth_result"),
    LegacyRouteSpec("GET", "/auth/google/callback", "google_auth_callback"),
    LegacyRouteSpec("POST", "/api/auth/google", "google_login_endpoint", "LoginResponse"),
    LegacyRouteSpec("POST", "/api/login", "login_endpoint", "LoginResponse"),
)


router = APIRouter(tags=["admin-usuarios"])


def create_admin_usuarios_router() -> APIRouter:
    admin_usuarios_router = APIRouter(tags=["admin-usuarios"])

    for spec in LEGACY_ADMIN_USUARIOS_ROUTES:
        endpoint = getattr(admin_usuarios, spec.endpoint_name)
        kwargs = {}
        if spec.response_model_name:
            kwargs["response_model"] = LoginResponse
        admin_usuarios_router.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.endpoint_name,
            **kwargs,
        )

    from backend.routers.codex_console import create_codex_console_router
    from backend.services import codex_assistant as codex_assistant_service
    from backend.services.codex.console import bootstrap as codex_console_service

    codex_console_service.configure(sys.modules.get("backend_api"))
    codex_assistant_service.configure_codex_assistant_runtime(sys.modules.get("backend_api"))
    admin_usuarios_router.include_router(create_codex_console_router())

    return admin_usuarios_router
