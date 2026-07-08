"""Compatibility facade for the Admin Usuarios module."""

from __future__ import annotations

from backend.schemas import GoogleLoginRequest, LoginRequest, LoginResponse
from backend.services.admin_usuarios_context import *
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services import admin_usuarios_common
from backend.services import admin_usuarios_firebase
from backend.services import admin_usuarios_store
from backend.services import admin_usuarios_messages_core
from backend.services import admin_usuarios_chat_core
from backend.services import admin_usuarios_presence_core
from backend.services import admin_usuarios_login_core
from backend.services import admin_usuarios_users
from backend.services import admin_usuarios_presence
from backend.services import admin_usuarios_chat
from backend.services import admin_usuarios_auth
from backend.services.admin_usuarios_common import *
from backend.services.admin_usuarios_firebase import *
from backend.services.admin_usuarios_store import *
from backend.services.admin_usuarios_messages_core import *
from backend.services.admin_usuarios_chat_core import *
from backend.services.admin_usuarios_presence_core import *
from backend.services.admin_usuarios_login_core import *
from backend.services.admin_usuarios_users import *
from backend.services.admin_usuarios_presence import *
from backend.services.admin_usuarios_chat import *
from backend.services.admin_usuarios_auth import *


_CORE_MODULES = (
    admin_usuarios_common,
    admin_usuarios_firebase,
    admin_usuarios_store,
    admin_usuarios_messages_core,
    admin_usuarios_chat_core,
    admin_usuarios_presence_core,
    admin_usuarios_login_core,
)
_ENDPOINT_MODULES = (
    admin_usuarios_users,
    admin_usuarios_presence,
    admin_usuarios_chat,
    admin_usuarios_auth,
)


def _core_exports() -> dict[str, object]:
    exports: dict[str, object] = {}
    for module in _CORE_MODULES:
        for name in getattr(module, "__all__", ()): 
            if name.startswith("configure_"):
                continue
            if hasattr(module, name):
                exports[name] = getattr(module, name)
    return exports


def _wire_admin_usuarios_globals() -> dict[str, object]:
    exports = _core_exports()
    for module in (*_CORE_MODULES, *_ENDPOINT_MODULES):
        module.__dict__.update(exports)
    globals().update(exports)
    return exports


def firebase_auth_last_error() -> str:
    return str(getattr(admin_usuarios_firebase, "FIREBASE_AUTH_LAST_ERROR", "") or "")


def configure_admin_usuarios_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    admin_usuarios_common.configure_admin_usuarios_common_runtime(runtime)
    admin_usuarios_firebase.configure_admin_usuarios_firebase_runtime(runtime)
    admin_usuarios_store.configure_admin_usuarios_store_runtime(runtime)
    admin_usuarios_messages_core.configure_admin_usuarios_messages_core_runtime(runtime)
    admin_usuarios_chat_core.configure_admin_usuarios_chat_core_runtime(runtime)
    admin_usuarios_presence_core.configure_admin_usuarios_presence_core_runtime(runtime)
    admin_usuarios_login_core.configure_admin_usuarios_login_core_runtime(runtime)
    _wire_admin_usuarios_globals()
    admin_usuarios_users.configure_admin_usuarios_users_runtime(runtime)
    admin_usuarios_presence.configure_admin_usuarios_presence_runtime(runtime)
    admin_usuarios_chat.configure_admin_usuarios_chat_runtime(runtime)
    admin_usuarios_auth.configure_admin_usuarios_auth_runtime(runtime)
    _wire_admin_usuarios_globals()
    return runtime


configure_admin_usuarios_runtime()

__all__ = [
    "configure_admin_usuarios_runtime",
    "firebase_auth_last_error",
    "GoogleLoginRequest",
    "LoginRequest",
    "LoginResponse",
    "admin_status_controle_acesso",
    "admin_listar_usuarios",
    "admin_enviar_mensagem_usuario",
    "admin_salvar_usuario",
    "admin_trocar_senha_usuario",
    "admin_resetar_dispositivos_usuario",
    "admin_alterar_status_usuario",
    "admin_alterar_permissoes_usuario",
    "admin_alterar_limite_dispositivos_usuario",
    "admin_excluir_usuario",
    "firebase_realtime_presence_session",
    "_montar_payload_usuarios_online",
    "admin_listar_usuarios_online",
    "user_machine_heartbeat",
    "user_machines_online",
    "user_chat_contacts",
    "user_chat_typing",
    "user_chat_typing_get",
    "user_chat_unread",
    "user_chat_history",
    "user_chat_send",
    "user_admin_messages",
    "user_admin_messages_stream",
    "user_admin_message_read",
    "trocar_minha_senha",
    "minha_sessao_auth",
    "google_auth_config",
    "google_auth_start",
    "google_auth_result",
    "google_auth_callback",
    "google_login_endpoint",
    "login_endpoint",
    "_permissao_exigida_por_rota",
    "_normalizar_permissoes_exigidas",
    "_permissoes_autorizam_rota",
    "_carregar_permissoes_usuario",
    "_firebase_http_exception_permite_fallback",
    "_payload_sessao_por_authorization",
    "_require_full_admin_user_management",
    "_require_admin_usuarios_access",
    "_require_online_presence_access",
    "autenticar_google_sheets",
    "_salvar_cache_usuarios",
    "_carregar_cache_usuarios",
    "_normalizar_permissoes",
    "_normalizar_max_machines",
    "_normalizar_lista_maquinas",
    "_usuario_pode_logar_em_qualquer_dispositivo",
    "_normalizar_data_sistema",
    "_normalizar_email",
    "_normalizar_empresa",
    "_empresa_chat_key",
    "_pydantic_campo_enviado",
    "_hash_password_se_preciso",
    "_env_texto",
    "_firebase_access_mode",
    "_firebase_access_obrigatorio",
    "_firebase_access_desativado",
    "_firebase_users_collection_name",
    "_firebase_users_index_collection_name",
    "_firebase_audit_collection_name",
    "_firebase_presence_collection_name",
    "_firebase_admin_messages_collection_name",
    "_firebase_user_chat_collection_name",
    "_firebase_user_chat_typing_collection_name",
    "_firebase_user_status_collection_name",
    "_firebase_live_features_ativas",
    "_firebase_realtime_database_url",
    "_firebase_web_api_key",
    "_firebase_web_app_id",
    "_firebase_web_auth_domain",
    "_firebase_presence_safe_key",
    "_firebase_presence_root_path",
    "_firebase_presence_client_key",
    "_firebase_presence_user_key",
    "_firebase_presence_machine_key_hash",
    "_firebase_project_id",
    "_firebase_json_service_account_valido",
    "_firebase_ler_service_account",
    "_firebase_service_account_candidates",
    "_firebase_service_account_file",
    "_firebase_tem_configuracao",
    "_firebase_deve_usar",
    "_firebase_credencial",
    "_firebase_app",
    "_firebase_db",
    "_firebase_doc_id",
    "_firebase_now_iso",
    "_firebase_bool",
    "_firebase_user_from_data",
    "_firebase_user_to_data",
    "_firebase_collection",
    "_firebase_users_index_doc_id",
    "_firebase_users_index_ref",
    "_firebase_user_index_summary",
    "_firebase_users_index_payload",
    "_firebase_users_index_save",
    "_firebase_users_index_get",
    "_firebase_users_index_all",
    "_firebase_users_index_rebuild",
    "_firebase_users_index_update_user",
    "_firebase_users_index_remove_user",
    "_firebase_listar_usuarios",
    "_firebase_obter_usuario",
    "_firebase_salvar_usuario",
    "_firebase_excluir_usuario",
    "_firebase_registrar_login",
    "_auth_db_conexao",
    "_init_auth_db",
    "_auth_db_tem_usuarios",
    "_salvar_usuarios_sql",
    "_carregar_usuarios_sql",
    "_listar_usuarios_admin_sql",
    "_obter_usuario_sql",
    "_salvar_usuario_admin_firebase",
    "_salvar_usuario_admin_sql",
    "_atualizar_senha_usuario_sql",
    "_atualizar_permissoes_usuario_sql",
    "_atualizar_status_usuario_sql",
    "_resetar_maquinas_usuario_sql",
    "_atualizar_max_machines_usuario_sql",
    "_remover_usuario_sql",
    "_resumo_usuario_admin",
    "_admin_messages_local_read",
    "_admin_messages_local_write",
    "_admin_message_public",
    "_admin_message_for_user",
    "_admin_messages_firebase_list",
    "_admin_messages_for_user",
    "_admin_messages_save",
    "_admin_messages_mark_read",
    "_sse_event",
    "_admin_messages_stream",
    "_user_chat_norm_username",
    "_user_chat_norm_client",
    "_user_chat_user_name",
    "_user_chat_local_read",
    "_user_chat_local_write",
    "_user_chat_typing_firebase_enabled",
    "_user_chat_typing_key",
    "_user_chat_typing_public",
    "_user_chat_typing_local_read",
    "_user_chat_typing_local_write",
    "_user_chat_typing_save",
    "_user_chat_typing_status",
    "_user_chat_attachments_normalizar",
    "_user_chat_attachment_summary",
    "_user_chat_call_normalizar",
    "_user_chat_public",
    "_user_status_doc_id",
    "_user_status_cache_key",
    "_user_status_empty",
    "_user_status_public",
    "_user_status_local_read",
    "_user_status_local_write",
    "_user_status_local_get",
    "_user_status_local_save",
    "_user_status_firebase_ref",
    "_user_status_firebase_get",
    "_user_status_firebase_set",
    "_user_status_firebase_delta",
    "_user_status_get",
    "_user_status_apply_delta",
    "_user_status_rebuild_local",
    "_user_status_rebuild_and_save",
    "_user_chat_is_between",
    "_user_chat_save",
    "_user_chat_int_ts",
    "_user_chat_firebase_for_user",
    "_user_chat_firebase_between",
    "_user_chat_merge_messages",
    "_user_chat_all_for_user",
    "_user_chat_all_between",
    "_user_chat_conversation_key",
    "_user_chat_cache_remote_messages",
    "_user_chat_local_between",
    "_user_chat_latest_ts",
    "_user_chat_remote_sync_plan",
    "_user_chat_history_cached",
    "_user_chat_update_incoming_status",
    "_user_chat_mark_delivered_for_user",
    "_user_chat_mark_read_between",
    "_user_chat_history",
    "_user_chat_unread_conversations",
    "_usuarios_podem_conversar_chat",
    "_user_chat_usuarios_locais",
    "_user_chat_mesclar_usuario_preferindo_principal",
    "_user_chat_obter_usuario",
    "_user_chat_encontrar_usuario_sessao",
    "_user_chat_resolver_destino",
    "_extrair_ip_request",
    "_normalizar_machine_tag",
    "_machine_id_eh_generico",
    "_resolver_nome_computador_por_ip",
    "_resolver_mac_por_ip",
    "_montar_machine_id_login",
    "_vincular_maquina_ao_usuario_se_vazia",
    "_registrar_login_maquina",
    "_machine_presence_write_interval_seconds",
    "_machine_presence_timeout_seconds",
    "_machine_presence_doc_id",
    "_machine_presence_label",
    "_machine_presence_machine_key",
    "_machine_presence_sanitize_page",
    "_machine_presence_normalize_app_version",
    "_machine_presence_app_version_from_user_agent",
    "_machine_presence_resolve_app_version",
    "_machine_presence_preserve_app_version",
    "_machine_presence_record",
    "_machine_presence_auto_touch",
    "_machine_presence_local_read",
    "_machine_presence_local_write",
    "_machine_presence_prune_local",
    "_machine_presence_save",
    "_machine_presence_list_firebase",
    "_machine_presence_list_firebase_client",
    "_machine_presence_list_from_records",
    "_machine_presence_list",
    "_machine_presence_mark_current",
    "_carregar_usuarios_local",
    "verificar_validade_acesso",
    "extrair_permissoes",
    "verificar_trava_seguranca",
    "carregar_usuarios_sheets",
    "_login_senha_confere",
    "_login_validade_ok",
    "_login_usuario_ativo",
    "_firebase_validar_e_registrar_maquina",
    "_login_validar_e_registrar_maquina",
    "_google_login_client_id",
    "_google_login_client_secret",
    "_google_login_scopes",
    "_google_login_verify_id_token",
    "_google_login_redirect_uri",
    "_google_login_configurado",
    "_buscar_usuario_por_email_google",
    "_montar_resposta_login_sucesso",
    "_autenticar_usuario_por_google_info",
    "_google_login_error_redirect",
    "_google_login_success_html",
    "_google_login_poll_html",
    "_google_login_poll_response_payload",
    "_google_login_store_poll_result",
    "_google_login_finish_poll",
    "_google_oauth_init_db",
    "_google_oauth_carregar_tokens",
    "_google_oauth_salvar_tokens_usuario",
    "_google_oauth_parse_expiry",
]
