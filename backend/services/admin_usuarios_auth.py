"""Auth, session, and Google-login endpoints owned by admin_usuarios router."""

from __future__ import annotations

import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import requests
from fastapi import Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.schemas import GoogleLoginRequest, LoginRequest, LoginResponse, UserChangePasswordRequest
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_auth_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


def trocar_minha_senha(payload: UserChangePasswordRequest, authorization: Optional[str] = Header(default=None)):
    sessao = _payload_sessao_por_authorization(authorization)
    usuario = _obter_usuario_sql(sessao["username"])
    client_usuario = str(usuario.get("client_id") or "").strip()
    if client_usuario and client_usuario != sessao["client_id"]:
        raise HTTPException(status_code=403, detail="Sessão inválida para esse usuário.")

    senha_atual = str(payload.current_password or "")
    nova_senha = str(payload.new_password or "").strip()
    confirmacao = str(payload.confirm_password if payload.confirm_password is not None else payload.new_password or "").strip()
    if not senha_atual:
        raise HTTPException(status_code=400, detail="Informe a senha atual.")
    if not nova_senha:
        raise HTTPException(status_code=400, detail="Informe a nova senha.")
    if len(nova_senha) < 6:
        raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 6 caracteres.")
    if nova_senha != confirmacao:
        raise HTTPException(status_code=400, detail="A confirmação da senha não confere.")
    if not _login_senha_confere(senha_atual, usuario.get("password") or ""):
        raise HTTPException(status_code=400, detail="Senha atual incorreta.")

    _atualizar_senha_usuario_sql(usuario["username"], nova_senha)
    return {"success": True, "message": "Senha alterada com sucesso."}

def minha_sessao_auth(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    machine_id: str = "",
):
    sessao = _payload_sessao_por_authorization(authorization)
    usuario = _obter_usuario_sql(sessao["username"])
    client_usuario = str(usuario.get("client_id") or sessao["client_id"] or "default").strip() or "default"
    if client_usuario != sessao["client_id"]:
        raise HTTPException(status_code=403, detail="Sessão inválida para esse usuário.")

    if not _login_usuario_ativo(usuario):
        return LoginResponse(success=False, message="Usuario inativo. Contate o administrador.")

    validade_ok, msg_validade = _login_validade_ok(usuario)
    if not validade_ok:
        return LoginResponse(success=False, message=msg_validade or "Acesso expirado ou invalido.")

    try:
        permissoes = _carregar_permissoes_usuario(sessao["username"], client_usuario)
    except Exception:
        permissoes = _normalizar_permissoes(usuario.get("permissions") or {})

    machine_final = str(machine_id or usuario.get("machine_id") or "").strip()
    if machine_final:
        try:
            _machine_presence_save(_machine_presence_record(sessao["username"], client_usuario, machine_final, request, "session-refresh"))
        except Exception as exc:
            logger.warning("[LOGIN] Nao foi possivel atualizar presenca da sessao para %s: %s", sessao["username"], exc)

    return _montar_resposta_login_sucesso(sessao["username"], usuario, permissoes, client_usuario, machine_final)

def google_auth_config(request: Request):
    client_id = _google_login_client_id()
    client_secret = _google_login_client_secret()
    return {
        "success": True,
        "enabled": bool(client_id and client_secret),
        "oauth_enabled": bool(client_id and client_secret),
        "client_id": client_id,
        "redirect_uri": _google_login_redirect_uri(request),
        "scopes": _google_login_scopes(),
    }

def google_auth_start(request: Request, machine_id: str = "", mode: str = "", app_version: str = ""):
    app_version_ok = _validar_versao_minima_app_ou_426(app_version)
    mode = str(mode or "").strip().lower()
    client_id_google = _google_login_client_id()
    client_secret_google = _google_login_client_secret()
    if not client_id_google or not client_secret_google:
        if mode == "json":
            return {
                "success": False,
                "message": "Login Google nao configurado. Informe GOOGLE_LOGIN_CLIENT_ID e GOOGLE_LOGIN_CLIENT_SECRET no servidor.",
            }
        return _google_login_error_redirect("Login Google nao configurado. Informe GOOGLE_LOGIN_CLIENT_ID e GOOGLE_LOGIN_CLIENT_SECRET no servidor.")

    state = secrets.token_urlsafe(32)
    redirect_uri = _google_login_redirect_uri(request)
    agora = time.time()
    with GOOGLE_LOGIN_STATE_LOCK:
        expirados = [chave for chave, item in GOOGLE_LOGIN_STATES.items() if agora - float((item or {}).get("created_at") or 0) > 900]
        for chave in expirados:
            GOOGLE_LOGIN_STATES.pop(chave, None)
        GOOGLE_LOGIN_STATES[state] = {
            "created_at": agora,
            "machine_id": str(machine_id or ""),
            "app_version": app_version_ok,
            "redirect_uri": redirect_uri,
            "poll": mode == "json",
        }

    params = {
        "client_id": client_id_google,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _google_login_scopes(),
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    if mode == "json":
        return {
            "success": True,
            "auth_url": auth_url,
            "state": state,
            "redirect_uri": redirect_uri,
        }
    return RedirectResponse(url=auth_url, status_code=303)

def google_auth_result(state: str):
    state = str(state or "").strip()
    agora = time.time()
    with GOOGLE_LOGIN_STATE_LOCK:
        expirados = [chave for chave, item in GOOGLE_LOGIN_RESULTS.items() if agora - float((item or {}).get("created_at") or 0) > 900]
        for chave in expirados:
            GOOGLE_LOGIN_RESULTS.pop(chave, None)
        resultado = GOOGLE_LOGIN_RESULTS.get(state)
        if not isinstance(resultado, dict):
            return {"success": False, "pending": True}
        resultado = dict(resultado)
        GOOGLE_LOGIN_RESULTS.pop(state, None)

    resultado.pop("created_at", None)
    resultado["pending"] = False
    return resultado

def google_auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    state = str(state or "").strip()
    if error:
        with GOOGLE_LOGIN_STATE_LOCK:
            state_data_error = GOOGLE_LOGIN_STATES.pop(state, None) if state else None
        if isinstance(state_data_error, dict) and state_data_error.get("poll"):
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message=f"Google recusou o login: {error}."),
            )
        return _google_login_error_redirect(f"Google recusou o login: {error}.")

    if not code or not state:
        return _google_login_error_redirect("Retorno do Google incompleto.")

    with GOOGLE_LOGIN_STATE_LOCK:
        state_data = GOOGLE_LOGIN_STATES.pop(state, None)
    poll_mode = bool(isinstance(state_data, dict) and state_data.get("poll"))
    if not isinstance(state_data, dict):
        return _google_login_error_redirect("Sessao do login Google expirou. Tente novamente.")
    if time.time() - float(state_data.get("created_at") or 0) > 900:
        if poll_mode:
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message="Sessao do login Google expirou. Tente novamente."),
            )
        return _google_login_error_redirect("Sessao do login Google expirou. Tente novamente.")

    client_id_google = _google_login_client_id()
    client_secret_google = _google_login_client_secret()
    redirect_uri = str(state_data.get("redirect_uri") or _google_login_redirect_uri(request)).strip()

    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id_google,
                "client_secret": client_secret_google,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        token_payload = token_resp.json() if token_resp.content else {}
    except Exception as exc:
        logger.warning("[LOGIN] Falha ao trocar codigo Google: %s", exc)
        if poll_mode:
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message="Nao foi possivel concluir o login Google."),
            )
        return _google_login_error_redirect("Nao foi possivel concluir o login Google.")

    if token_resp.status_code != 200:
        detalhe = ""
        if isinstance(token_payload, dict):
            detalhe = str(token_payload.get("error_description") or token_payload.get("error") or "").strip()
        logger.warning("[LOGIN] Google token exchange falhou: %s", detalhe or token_resp.text[:300])
        if poll_mode:
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message=detalhe or "Google nao autorizou o login."),
            )
        return _google_login_error_redirect(detalhe or "Google nao autorizou o login.")

    id_token_value = str((token_payload or {}).get("id_token") or "").strip()
    if not id_token_value:
        if poll_mode:
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message="Google nao retornou a identidade da conta."),
            )
        return _google_login_error_redirect("Google nao retornou a identidade da conta.")

    try:
        token_info = _google_login_verify_id_token(id_token_value, client_id_google)
    except (ValueError, GoogleAuthError) as exc:
        logger.warning("[LOGIN] ID token Google invalido no callback: %s", exc)
        if poll_mode:
            return _google_login_finish_poll(
                state,
                LoginResponse(success=False, message="Nao foi possivel validar sua conta Google."),
            )
        return _google_login_error_redirect("Nao foi possivel validar sua conta Google.")

    login_resp = _autenticar_usuario_por_google_info(
        token_info,
        str(state_data.get("machine_id") or ""),
        request,
        str(state_data.get("app_version") or ""),
    )
    if login_resp.success:
        try:
            google_email = _normalizar_email((token_info or {}).get("email"))
            _google_oauth_salvar_tokens_usuario(
                str((login_resp.user_data or {}).get("username") or ""),
                str((login_resp.user_data or {}).get("client_id") or ""),
                google_email,
                token_payload,
            )
        except Exception as exc:
            logger.warning("[DRIVE-SYNC] Nao foi possivel salvar token OAuth Google: %s", exc)
    if poll_mode:
        return _google_login_finish_poll(state, login_resp)
    if not login_resp.success:
        return _google_login_error_redirect(login_resp.message)

    return _google_login_success_html(login_resp)

async def google_login_endpoint(payload: GoogleLoginRequest, request: Request):
    _validar_versao_minima_app_ou_426(payload.app_version)
    client_id_google = _google_login_client_id()
    if not client_id_google:
        return LoginResponse(success=False, message="Login com Google ainda nao configurado no servidor.")

    credential = str(payload.credential or "").strip()
    if not credential:
        return LoginResponse(success=False, message="Credencial Google nao recebida.")

    try:
        token_info = _google_login_verify_id_token(credential, client_id_google)
    except (ValueError, GoogleAuthError) as exc:
        logger.warning("[LOGIN] Token Google invalido: %s", exc)
        return LoginResponse(success=False, message="Nao foi possivel validar sua conta Google.")
    except Exception as exc:
        logger.warning("[LOGIN] Falha ao validar token Google: %s", exc)
        return LoginResponse(success=False, message="Erro ao validar login Google.")

    return _autenticar_usuario_por_google_info(token_info, payload.machine_id, request, payload.app_version)

async def login_endpoint(payload: LoginRequest, request: Request):
    app_version_ok = _validar_versao_minima_app_ou_426(payload.app_version)
    username = str(payload.username or "").strip().lower()
    senha = str(payload.password or "")
    if not username or not senha:
        return LoginResponse(success=False, message="Informe usuario e senha.")

    usuarios, ws, headers = carregar_usuarios_sheets()
    if not isinstance(usuarios, dict) or not usuarios:
        return LoginResponse(success=False, message="Nenhum usuario foi encontrado para autenticar.")

    usuario = usuarios.get(username)
    if not isinstance(usuario, dict):
        return LoginResponse(success=False, message="Usuario ou senha invalidos.")

    if not _login_senha_confere(senha, usuario.get("password") or ""):
        return LoginResponse(success=False, message="Usuario ou senha invalidos.")

    if not _login_usuario_ativo(usuario):
        return LoginResponse(success=False, message="Usuario inativo. Contate o administrador.")

    validade_ok, msg_validade = _login_validade_ok(usuario)
    sheet_id_validade = None
    if validade_ok and ws is not None:
        validade_ok, msg_validade, sheet_id_validade = verificar_validade_acesso(username, ws)
    if not validade_ok:
        return LoginResponse(success=False, message=msg_validade or "Acesso expirado ou invalido.")

    permissoes = (
        usuario.get("permissions")
        if isinstance(usuario.get("permissions"), dict)
        else extrair_permissoes(usuario.get("original_row") or [], headers or [])
    )
    permissoes = _normalizar_permissoes(permissoes)

    client_id = str(payload.client_id or usuario.get("client_id") or sheet_id_validade or "default").strip() or "default"
    usuario["client_id"] = client_id
    maquina_ok, msg_maquina, machine_final = _login_validar_e_registrar_maquina(
        username,
        usuario,
        permissoes,
        payload.machine_id,
        request,
    )
    if not maquina_ok:
        return LoginResponse(success=False, message=msg_maquina)

    try:
        _registrar_login_maquina(username, client_id, machine_final, request)
    except Exception as exc:
        logger.warning("[LOGIN] Nao foi possivel registrar auditoria de login para %s: %s", username, exc)
    try:
        _machine_presence_save(_machine_presence_record(username, client_id, machine_final, request, "login", app_version_ok))
    except Exception as exc:
        logger.warning("[LOGIN] Nao foi possivel registrar presenca inicial para %s: %s", username, exc)

    return _montar_resposta_login_sucesso(username, usuario, permissoes, client_id, machine_final)


configure_admin_usuarios_auth_runtime()

__all__ = [
    "configure_admin_usuarios_auth_runtime",
    "trocar_minha_senha",
    "minha_sessao_auth",
    "google_auth_config",
    "google_auth_start",
    "google_auth_result",
    "google_auth_callback",
    "google_login_endpoint",
    "login_endpoint",
]
