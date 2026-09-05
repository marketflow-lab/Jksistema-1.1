"""HTTP-facing operations for the Integracoes module."""

from __future__ import annotations

import hashlib
import hmac
import inspect
import logging
import secrets
import time
from typing import Callable, Optional
from urllib.parse import urlencode

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.schemas import AuthRequest, StoreRequest, TokenRequest
from backend.services.integracoes import (
    _LOJAS_CONFIG_LOCK,
    _integracoes_atualizar_tombstone_payload,
    _integracoes_commit_lojas_tombstones,
    _integracoes_ler_tombstones_estrito,
    _integracoes_servico_key,
    auth_bling_exchange,
    auth_bling_get_link,
    auth_ml_exchange,
    auth_ml_get_link,
    atualizar_api_loja,
    buscar_loja,
    carregar_lojas,
    consumir_temp_auth,
    criar_loja as criar_loja_identidade,
    criar_temp_auth_loja,
    desconectar_api_loja,
    excluir_loja,
    ler_temp_auth,
    salvar_temp_auth,
)


logger = logging.getLogger("jk_sistema")
_TENANT_DEPENDENCY = None
_resolver_redirect_uri_publica: Callable[..., str] = lambda **kwargs: ""
_resolver_redirect_uri_bling: Callable[..., str] = lambda **kwargs: ""
_shared_sync_propagar_lojas_integracoes_cliente: Callable[[str, str], object] = lambda client_id, machine_id: []
_OAUTH_RESULT_TTL_SECONDS = 5 * 60
_OAUTH_RESULT_SIGNING_KEY = secrets.token_bytes(32)

_OAUTH_RESULT_REASONS = {
    "provider_denied": "A autorização foi cancelada ou recusada no provedor.",
    "session_missing": "A sessão de conexão não foi encontrada ou expirou.",
    "state_missing": "A resposta de segurança não foi recebida.",
    "state_invalid": "A resposta de segurança não corresponde à conexão iniciada.",
    "incomplete_data": "Faltaram dados para finalizar a conexão.",
    "exchange_failed": "O provedor não concluiu a troca de autorização.",
    "incomplete_tokens": "O provedor não devolveu credenciais OAuth completas.",
    "missing_refresh_token": (
        "O Mercado Livre não devolveu a permissão de acesso contínuo. "
        "Revise offline_access, read e write no DevCenter e tente novamente."
    ),
    "store_changed": (
        "A loja ou a tentativa de conexão mudou enquanto a autorização estava aberta. "
        "As credenciais salvas foram preservadas; inicie uma nova conexão no JK Sistema."
    ),
    "persistence_failed": (
        "A autorização foi recebida, mas não foi possível confirmar o salvamento da conexão. "
        "Volte ao JK Sistema e tente novamente."
    ),
    "result_invalid": "Este retorno não é válido ou expirou. Inicie a conexão novamente no JK Sistema.",
}


def _oauth_result_signature(status: str, reason: str, expires: int) -> str:
    payload = f"{status}\n{reason}\n{expires}".encode("utf-8")
    return hmac.new(_OAUTH_RESULT_SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def _oauth_result_redirect(status: str, reason: str = "") -> RedirectResponse:
    status_final = "success" if status == "success" else "error"
    reason_final = ""
    if status_final == "error":
        reason_final = reason if reason in _OAUTH_RESULT_REASONS else "exchange_failed"
    expires = int(time.time()) + _OAUTH_RESULT_TTL_SECONDS
    params = {
        "status": status_final,
        "expires": str(expires),
        "sig": _oauth_result_signature(status_final, reason_final, expires),
    }
    if reason_final:
        params["reason"] = reason_final
    response = RedirectResponse(
        url=f"/auth/callback/result?{urlencode(params)}",
        status_code=303,
    )
    response.headers.update({
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    })
    return response


def _buscar_loja_oauth_exata(
    client_id: str,
    nome_loja: str,
    store_id: str,
) -> dict | None:
    """OAuth nunca pode escolher uma loja equivalente nem recriar uma loja removida."""
    nome_exato = str(nome_loja or "").strip()
    store_id_exato = str(store_id or "").strip()
    if not store_id_exato:
        return None
    loja = buscar_loja(client_id, nome_exato, store_id=store_id_exato)
    if not isinstance(loja, dict):
        return None
    if (
        str(loja.get("store_id") or "").strip() != store_id_exato
        or str(loja.get("nome") or "").strip() != nome_exato
    ):
        return None
    return loja


def _ml_oauth_config_do_fluxo(
    client_id: str,
    nome_loja: str,
    store_id: str,
    state: str,
    app_id: str,
    client_secret: str,
) -> dict | None:
    loja = _buscar_loja_oauth_exata(client_id, nome_loja, store_id)
    if not loja:
        return None
    integracoes = loja.get("integracoes")
    cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else None
    draft = cfg.get("oauth_draft") if isinstance(cfg, dict) else None
    if not isinstance(draft, dict):
        return None
    draft_state = str(draft.get("state") or "").strip()
    draft_app_id = str(draft.get("app_id") or "").strip()
    draft_secret = str(draft.get("client_secret") or "").strip()
    if not all((draft_state, draft_app_id, draft_secret)):
        return None
    if not (
        secrets.compare_digest(draft_state, state)
        and secrets.compare_digest(draft_app_id, app_id)
        and secrets.compare_digest(draft_secret, client_secret)
    ):
        return None
    return cfg


def _bling_oauth_config_do_fluxo(
    client_id: str,
    nome_loja: str,
    store_id: str,
    state: str,
) -> dict | None:
    loja = _buscar_loja_oauth_exata(client_id, nome_loja, store_id)
    if not loja:
        return None
    integracoes = loja.get("integracoes")
    cfg = integracoes.get("bling") if isinstance(integracoes, dict) else None
    pending_state = str(
        cfg.get("oauth_pending_state") if isinstance(cfg, dict) else ""
    ).strip()
    if not pending_state or not secrets.compare_digest(pending_state, state):
        return None
    return cfg


async def integracoes_auth_callback_result(
    status: str = "error",
    reason: str = "",
    expires: str = "",
    sig: str = "",
) -> HTMLResponse:
    status_recebido = str(status or "").strip().lower()
    reason_recebido = str(reason or "").strip().lower()
    assinatura_recebida = str(sig or "").strip().lower()
    try:
        expires_recebido = int(str(expires or "").strip())
    except (TypeError, ValueError):
        expires_recebido = 0

    status_valido = status_recebido in {"success", "error"}
    reason_valido = (
        (status_recebido == "success" and not reason_recebido)
        or (status_recebido == "error" and reason_recebido in _OAUTH_RESULT_REASONS)
    )
    assinatura_esperada = _oauth_result_signature(
        status_recebido,
        reason_recebido,
        expires_recebido,
    )
    assinatura_formato_valido = (
        len(assinatura_recebida) == 64
        and all(char in "0123456789abcdef" for char in assinatura_recebida)
    )
    retorno_valido = (
        status_valido
        and reason_valido
        and expires_recebido >= int(time.time())
        and assinatura_formato_valido
        and hmac.compare_digest(assinatura_recebida, assinatura_esperada)
    )
    if not retorno_valido:
        status_recebido = "error"
        reason_recebido = "result_invalid"

    sucesso = status_recebido == "success"
    if sucesso:
        page_status = "success"
        titulo = "Conexão concluída"
        mensagem = "A integração foi autorizada com sucesso."
        orientacao = "Volte ao JK Sistema e atualize a tela de Integrações."
    else:
        page_status = "error"
        titulo = "Não foi possível concluir a conexão"
        mensagem = _OAUTH_RESULT_REASONS.get(
            reason_recebido,
            "Ocorreu uma falha ao finalizar a integração.",
        )
        orientacao = "Volte ao JK Sistema, confira os dados e tente novamente."

    content = f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>JK Sistema - {titulo}</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      align-items: center;
      background: #071326;
      color: #e8f3ff;
      display: grid;
      font-family: Arial, sans-serif;
      margin: 0;
      min-height: 100vh;
      padding: 24px;
    }}
    main {{
      background: #0d1d33;
      border: 1px solid #29476d;
      border-radius: 16px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, .35);
      margin: auto;
      max-width: 560px;
      padding: 36px;
      text-align: center;
      width: 100%;
    }}
    .status {{
      background: {"#173d2a" if sucesso else "#44232a"};
      border-radius: 999px;
      color: {"#8ff0b6" if sucesso else "#ffb5c0"};
      display: inline-block;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: .04em;
      margin-bottom: 18px;
      padding: 8px 13px;
      text-transform: uppercase;
    }}
    h1 {{ font-size: 26px; margin: 0 0 14px; }}
    p {{ color: #c3d2e6; line-height: 1.55; margin: 0 0 12px; }}
    .close {{ color: #91a8c4; font-size: 14px; margin-top: 24px; }}
  </style>
</head>
<body>
  <main data-oauth-result="{page_status}">
    <div class="status">{"Concluído" if sucesso else "Atenção"}</div>
    <h1>{titulo}</h1>
    <p>{mensagem}</p>
    <p>{orientacao}</p>
    <p class="close">Você pode fechar esta aba.</p>
  </main>
</body>
</html>
"""
    return HTMLResponse(
        content=content,
        status_code=200,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
        },
    )


def configure_integracoes_api_context(
    *,
    get_tenant_id_fn,
    logger_ref=None,
    resolver_redirect_uri_publica: Callable[..., str] | None = None,
    resolver_redirect_uri_bling: Callable[..., str] | None = None,
    shared_sync_propagar_lojas_integracoes_cliente: Callable[[str, str], object] | None = None,
) -> None:
    global logger, _TENANT_DEPENDENCY, _resolver_redirect_uri_publica, _resolver_redirect_uri_bling
    global _shared_sync_propagar_lojas_integracoes_cliente
    _TENANT_DEPENDENCY = get_tenant_id_fn
    if logger_ref is not None:
        logger = logger_ref
    if resolver_redirect_uri_publica is not None:
        _resolver_redirect_uri_publica = resolver_redirect_uri_publica
    if resolver_redirect_uri_bling is not None:
        _resolver_redirect_uri_bling = resolver_redirect_uri_bling
    if shared_sync_propagar_lojas_integracoes_cliente is not None:
        _shared_sync_propagar_lojas_integracoes_cliente = shared_sync_propagar_lojas_integracoes_cliente


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if _TENANT_DEPENDENCY is None:
        raise RuntimeError("Integracoes API context was not configured.")
    result = _TENANT_DEPENDENCY(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


async def get_lojas(client_id: str = Depends(get_tenant_id)):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        return central.public_stores()
    return carregar_lojas(client_id)


def _store_id_mutacao_exato(store_id: object) -> str:
    identidade = str(store_id or "").strip()
    if not identidade:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_id_required",
                "message": "Informe o store_id exato para alterar a loja.",
            },
        )
    return identidade


async def get_loja(
    nome_loja: str,
    store_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        rows = [row for row in central.public_stores() if row["store_id"] == store_id]
        if len(rows) != 1:
            raise HTTPException(404, "Loja não encontrada na sessão.")
        return rows[0]
    loja = buscar_loja(client_id, nome_loja, store_id=store_id)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nao encontrada")
    return loja


async def create_loja(store_request: StoreRequest, client_id: str = Depends(get_tenant_id)):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        loja = central.call("POST", "/stores", {"name": store_request.nome, "request_id": secrets.token_hex(16)})
        central.refresh_stores()
        return {"success": True, "store_id": loja["store_id"], "loja": loja}
    loja = criar_loja_identidade(client_id, store_request.nome)
    return {"success": True, "store_id": loja["store_id"], "loja": loja}


async def delete_loja(
    nome_loja: str,
    store_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    store_id_exato = _store_id_mutacao_exato(store_id)
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        result = central.call("DELETE", f"/stores/{store_id_exato}")
        central.refresh_stores()
        return result
    removida = excluir_loja(
        client_id,
        nome_loja,
        store_id=store_id_exato,
    )
    return {"success": True, "store_id": removida.get("store_id")}


async def save_turbo_token(
    loja_nome: str,
    token_req: TokenRequest,
    store_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    from backend.services.central_accounts_client import current
    if current(client_id):
        raise HTTPException(409, "A central suporta conexões Mercado Livre e Bling. Turbo não está habilitado nesta modalidade.")
    store_id_exato = _store_id_mutacao_exato(store_id)
    loja = buscar_loja(client_id, loja_nome, store_id=store_id_exato)
    if not isinstance(loja, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "store_config_changed",
                "message": "A identidade da loja nao existe mais.",
                "store_id": store_id_exato,
            },
        )
    is_connected = bool(token_req.token and token_req.token.strip())
    atualizar_api_loja(
        client_id,
        loja_nome,
        "mercadoturbo",
        {"token": token_req.token, "connected": is_connected},
        store_id=store_id_exato,
    )
    return {"success": True}


async def disconnect_integracao(
    loja_nome: str,
    servico_nome: str,
    store_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        provider = "mercadolivre" if servico_nome == "ml" else servico_nome
        if provider not in ("mercadolivre", "bling"):
            raise HTTPException(400, "Integração não suportada pela central.")
        result = central.call("POST", f"/stores/{_store_id_mutacao_exato(store_id)}/disconnect", {"provider": provider})
        central.refresh_stores()
        return result
    store_id_exato = _store_id_mutacao_exato(store_id)
    servico = str(servico_nome or "").strip().lower()
    mapa_servicos = {
        "bling": "bling",
        "mercadolivre": "mercadolivre",
        "ml": "mercadolivre",
        "turbo": "mercadoturbo",
        "mercadoturbo": "mercadoturbo",
    }
    api_nome = mapa_servicos.get(servico)
    if not api_nome:
        raise HTTPException(status_code=400, detail="Integracao desconhecida.")
    loja = desconectar_api_loja(
        client_id,
        loja_nome,
        api_nome,
        store_id=store_id_exato,
    )
    return {"success": True, "loja": loja_nome, "servico": api_nome, "dados": loja.get("integracoes", {}).get(api_nome) or {}}


async def save_temp_auth_endpoint(temp_data: dict, client_id: str = Depends(get_tenant_id)):
    """Salva dados temporarios para OAuth antes do redirecionamento."""
    from backend.services.central_accounts_client import current
    if current(client_id):
        raise HTTPException(409, "Inicie a conexão pela central em Lojas e APIs.")
    registro = dict(temp_data or {})
    store_id = str(registro.get("store_id") or "").strip()
    if not store_id:
        raise HTTPException(
            status_code=400,
            detail="Store_id exato e obrigatorio para OAuth.",
        )
    fluxo = criar_temp_auth_loja(client_id, store_id, registro)
    return {"success": True, "state": fluxo["state"], "store_id": fluxo["store_id"]}


async def start_bling_auth(auth_req: AuthRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        return central.call("POST", f"/stores/{_store_id_mutacao_exato(auth_req.store_id)}/connect", {
            "provider": "bling", "app_id": auth_req.client_id, "app_secret": auth_req.client_secret})
    client_id = str(client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Cliente OAuth invalido.")
    app_id = str(auth_req.client_id or "").strip()
    client_secret = str(auth_req.client_secret or "").strip()
    if not app_id or not client_secret:
        raise HTTPException(status_code=400, detail="App ID e Client Secret sao obrigatorios.")
    store_id_exato = _store_id_mutacao_exato(auth_req.store_id)
    loja_alvo = buscar_loja(
        client_id,
        auth_req.loja,
        store_id=store_id_exato,
    )
    if not isinstance(loja_alvo, dict):
        raise HTTPException(status_code=409, detail="A identidade da loja nao existe mais.")
    loja_nome = str(loja_alvo.get("nome") or "").strip()
    if not loja_nome:
        raise HTTPException(status_code=409, detail="A identidade da loja nao existe mais.")
    state = secrets.token_urlsafe(24)
    redirect_uri = _resolver_redirect_uri_bling(request=request)
    atualizar_api_loja(
        client_id,
        loja_nome,
        "bling",
        {"oauth_pending_state": state},
        store_id=store_id_exato,
        require_existing=True,
    )
    salvar_temp_auth({
        "client_id": client_id,
        "loja": loja_nome,
        "store_id": store_id_exato,
        "servico": "bling",
        "id": app_id,
        "secret": client_secret,
        "state": state,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    })
    return {
        "success": True,
        "url": auth_bling_get_link(app_id, state, redirect_uri=redirect_uri),
        "redirect_uri": redirect_uri,
    }


async def start_mercadolivre_auth(auth_req: AuthRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    from backend.services.central_accounts_client import current
    central = current(client_id)
    if central:
        return central.call("POST", f"/stores/{_store_id_mutacao_exato(auth_req.store_id)}/connect", {
            "provider": "mercadolivre", "app_id": auth_req.client_id, "app_secret": auth_req.client_secret})
    client_id = str(client_id or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Cliente OAuth invalido.")
    app_id = str(auth_req.client_id or "").strip()
    client_secret = str(auth_req.client_secret or "").strip()
    if not app_id or not client_secret:
        raise HTTPException(status_code=400, detail="App ID e Client Secret sao obrigatorios.")
    store_id_exato = _store_id_mutacao_exato(auth_req.store_id)
    loja_alvo = buscar_loja(
        client_id,
        auth_req.loja,
        store_id=store_id_exato,
    )
    if not isinstance(loja_alvo, dict):
        raise HTTPException(status_code=409, detail="A identidade da loja nao existe mais.")
    loja_nome = str(loja_alvo.get("nome") or "").strip()
    if not loja_nome:
        raise HTTPException(status_code=409, detail="A identidade da loja nao existe mais.")
    state = secrets.token_urlsafe(24)
    redirect_uri = _resolver_redirect_uri_publica(request=request)
    atualizar_api_loja(client_id, loja_nome, "mercadolivre", {
        "oauth_draft": {
            "state": state,
            "app_id": app_id,
            "client_secret": client_secret,
            "saved_at": str(time.time()),
        },
    }, store_id=store_id_exato, require_existing=True)
    salvar_temp_auth({
        "client_id": client_id,
        "loja": loja_nome,
        "store_id": store_id_exato,
        "servico": "mercadolivre",
        "id": app_id,
        "secret": client_secret,
        "state": state,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    })
    return {"success": True, "url": auth_ml_get_link(app_id, state, redirect_uri=redirect_uri)}


async def integracoes_auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None, error_description: Optional[str] = None):
    del request, error_description

    state_recebido = str(state or "").strip()
    if not state_recebido:
        return _oauth_result_redirect("error", "state_missing")

    # O consumo atomico torna o callback one-shot e nao remove outros fluxos.
    temp = consumir_temp_auth(state_recebido)
    if not isinstance(temp, dict):
        motivo = "state_invalid" if ler_temp_auth() else "session_missing"
        return _oauth_result_redirect("error", motivo)

    state_esperado = str(temp.get("state") or "").strip()
    if not state_esperado or not secrets.compare_digest(state_esperado, state_recebido):
        return _oauth_result_redirect("error", "state_invalid")

    if error:
        return _oauth_result_redirect("error", "provider_denied")

    servico = str(temp.get("servico") or "").strip().lower()
    loja = str(temp.get("loja") or "").strip()
    store_id = str(temp.get("store_id") or "").strip()
    client_id = str(temp.get("client_id") or "").strip()
    app_id = str(temp.get("id") or "").strip()
    secret = str(temp.get("secret") or "").strip()
    redirect_uri = str(temp.get("redirect_uri") or "").strip() or None
    code_recebido = str(code or "").strip()

    if not all((code_recebido, loja, store_id, client_id, app_id, secret)) or servico not in {"bling", "mercadolivre"}:
        return _oauth_result_redirect("error", "incomplete_data")

    if servico == "bling":
        try:
            cfg_atual = _bling_oauth_config_do_fluxo(
                client_id,
                loja,
                store_id,
                state_recebido,
            )
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")
        if cfg_atual is None:
            return _oauth_result_redirect("error", "store_changed")
        ok, result = auth_bling_exchange(app_id, secret, code_recebido, redirect_uri=redirect_uri)
        if not ok or not isinstance(result, dict):
            return _oauth_result_redirect("error", "exchange_failed")
        access_token = str(result.get("access_token") or "").strip()
        refresh_token = str(result.get("refresh_token") or "").strip()
        if not access_token or not refresh_token:
            return _oauth_result_redirect("error", "incomplete_tokens")
        oauth_connection_id = secrets.token_urlsafe(24)
        try:
            atualizar_api_loja(client_id, loja, "bling", {
                "id": app_id,
                "secret": secret,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "oauth_connection_id": oauth_connection_id,
                "connected": True,
                "status": "conectado",
                "motivo": "",
                "oauth_invalid": False,
                "shared_without_oauth_tokens": False,
                "oauth_pending_state": None,
                "updated_at": str(time.time()),
            }, store_id=store_id, require_existing=True, expected_oauth_state=state_recebido)
        except HTTPException as exc:
            if exc.status_code == 409:
                return _oauth_result_redirect("error", "store_changed")
            return _oauth_result_redirect("error", "persistence_failed")
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")
        try:
            loja_persistida = _buscar_loja_oauth_exata(client_id, loja, store_id) or {}
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")
        integracoes_persistidas = loja_persistida.get("integracoes") or {}
        cfg_persistida = integracoes_persistidas.get("bling") or {}
        if not (
            isinstance(cfg_persistida, dict)
            and cfg_persistida.get("connected") is True
            and not cfg_persistida.get("oauth_invalid")
            and not cfg_persistida.get("oauth_pending_state")
            and secrets.compare_digest(str(cfg_persistida.get("access_token") or "").strip(), access_token)
            and secrets.compare_digest(str(cfg_persistida.get("refresh_token") or "").strip(), refresh_token)
            and secrets.compare_digest(
                str(cfg_persistida.get("oauth_connection_id") or "").strip(),
                oauth_connection_id,
            )
        ):
            return _oauth_result_redirect("error", "persistence_failed")
    else:
        try:
            cfg_atual = _ml_oauth_config_do_fluxo(
                client_id,
                loja,
                store_id,
                state_recebido,
                app_id,
                secret,
            )
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")
        if cfg_atual is None:
            return _oauth_result_redirect("error", "store_changed")

        ok, result = auth_ml_exchange(app_id, secret, code_recebido, redirect_uri=redirect_uri)
        if not ok or not isinstance(result, dict):
            return _oauth_result_redirect("error", "exchange_failed")
        app_id_atual = str(
            cfg_atual.get("app_id")
            or cfg_atual.get("id")
            or cfg_atual.get("client_id")
            or ""
        ).strip()
        secret_atual = str(
            cfg_atual.get("client_secret")
            or cfg_atual.get("secret")
            or ""
        ).strip()
        config_ativa = bool(
            cfg_atual.get("connected")
            and cfg_atual.get("access_token")
            and cfg_atual.get("refresh_token")
            and not cfg_atual.get("oauth_invalid")
        )
        credenciais_iguais = config_ativa and bool(app_id_atual and secret_atual) and (
            secrets.compare_digest(app_id, app_id_atual)
            and secrets.compare_digest(secret, secret_atual)
        )
        access_token = str(result.get("access_token") or "").strip()
        refresh_token = str(result.get("refresh_token") or "").strip()
        user_id_retorno = str(result.get("user_id") or "").strip()
        user_id_ativo = str(cfg_atual.get("user_id") or "").strip()
        mesmo_seller = bool(user_id_retorno and user_id_ativo) and user_id_retorno == user_id_ativo
        if not refresh_token and credenciais_iguais and mesmo_seller:
            refresh_token = str(cfg_atual.get("refresh_token") or "").strip()
        scope_retorno = str(result.get("scope") or "").strip()

        if not access_token:
            return _oauth_result_redirect("error", "incomplete_tokens")
        if not refresh_token:
            return _oauth_result_redirect("error", "missing_refresh_token")
        try:
            atualizar_api_loja(client_id, loja, "mercadolivre", {
                "id": app_id,
                "app_id": app_id,
                "secret": secret,
                "client_secret": secret,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "user_id": result.get("user_id"),
                "connected": True,
                "status": "conectado",
                "motivo": "",
                "oauth_invalid": False,
                "shared_without_oauth_tokens": False,
                "scope": scope_retorno,
                "oauth_draft": None,
                "updated_at": str(time.time()),
            }, store_id=store_id, require_existing=True, expected_oauth_state=state_recebido)
        except HTTPException as exc:
            if exc.status_code == 409:
                return _oauth_result_redirect("error", "store_changed")
            return _oauth_result_redirect("error", "persistence_failed")
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")

        try:
            loja_persistida = _buscar_loja_oauth_exata(client_id, loja, store_id) or {}
        except Exception:
            return _oauth_result_redirect("error", "persistence_failed")
        integracoes_persistidas = loja_persistida.get("integracoes") or {}
        cfg_persistida = integracoes_persistidas.get("mercadolivre") or {}
        if not (
            isinstance(cfg_persistida, dict)
            and cfg_persistida.get("connected") is True
            and not cfg_persistida.get("oauth_invalid")
            and not cfg_persistida.get("oauth_draft")
            and secrets.compare_digest(str(cfg_persistida.get("app_id") or "").strip(), app_id)
            and secrets.compare_digest(str(cfg_persistida.get("client_secret") or "").strip(), secret)
            and secrets.compare_digest(str(cfg_persistida.get("access_token") or "").strip(), access_token)
            and secrets.compare_digest(str(cfg_persistida.get("refresh_token") or "").strip(), refresh_token)
        ):
            return _oauth_result_redirect("error", "persistence_failed")

    return _oauth_result_redirect("success")


__all__ = [
    "configure_integracoes_api_context",
    "get_lojas",
    "get_loja",
    "create_loja",
    "delete_loja",
    "save_turbo_token",
    "disconnect_integracao",
    "save_temp_auth_endpoint",
    "start_bling_auth",
    "start_mercadolivre_auth",
    "integracoes_auth_callback_result",
    "integracoes_auth_callback",
]
