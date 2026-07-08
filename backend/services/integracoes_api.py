"""HTTP-facing operations for the Integracoes module."""

from __future__ import annotations

import inspect
import logging
import secrets
import time
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import quote_plus

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from backend.schemas import AuthRequest, StoreRequest, TokenRequest
from backend.services.integracoes import (
    auth_bling_exchange,
    auth_bling_get_link,
    auth_ml_exchange,
    auth_ml_get_link,
    atualizar_api_loja,
    buscar_loja,
    carregar_lojas,
    desconectar_api_loja,
    ler_temp_auth,
    limpar_temp_auth,
    salvar_lojas,
    salvar_temp_auth,
)


logger = logging.getLogger("jk_sistema")
_TENANT_DEPENDENCY = None
_resolver_redirect_uri_publica: Callable[..., str] = lambda **kwargs: ""
_resolver_redirect_uri_bling: Callable[..., str] = lambda **kwargs: ""
_shared_sync_propagar_lojas_integracoes_cliente: Callable[[str, str], object] = lambda client_id, machine_id: []


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
    return carregar_lojas(client_id)


async def get_loja(nome_loja: str, client_id: str = Depends(get_tenant_id)):
    loja = buscar_loja(client_id, nome_loja)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja nao encontrada")
    return loja


async def create_loja(store_request: StoreRequest, client_id: str = Depends(get_tenant_id)):
    lojas = carregar_lojas(client_id)
    if any(loja["nome"] == store_request.nome for loja in lojas):
        raise HTTPException(status_code=400, detail="Loja com este nome ja existe.")
    atualizar_api_loja(client_id, store_request.nome, "criacao", {"data": str(datetime.now())})
    return {"success": True, "loja": buscar_loja(client_id, store_request.nome)}


async def delete_loja(nome_loja: str, client_id: str = Depends(get_tenant_id)):
    lojas = carregar_lojas(client_id)
    lojas_filtradas = [loja for loja in lojas if loja["nome"] != nome_loja]
    if len(lojas) == len(lojas_filtradas):
        raise HTTPException(status_code=404, detail="Loja nao encontrada para deletar.")
    salvar_lojas(client_id, lojas_filtradas)
    return {"success": True}


async def save_turbo_token(loja_nome: str, token_req: TokenRequest, client_id: str = Depends(get_tenant_id)):
    if not buscar_loja(client_id, loja_nome):
        raise HTTPException(status_code=404, detail="Loja nao encontrada.")
    is_connected = bool(token_req.token and token_req.token.strip())
    atualizar_api_loja(client_id, loja_nome, "mercadoturbo", {"token": token_req.token, "connected": is_connected})
    return {"success": True}


async def disconnect_integracao(loja_nome: str, servico_nome: str, client_id: str = Depends(get_tenant_id)):
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
    loja = desconectar_api_loja(client_id, loja_nome, api_nome)
    return {"success": True, "loja": loja_nome, "servico": api_nome, "dados": loja.get("integracoes", {}).get(api_nome) or {}}


async def save_temp_auth_endpoint(temp_data: dict, client_id: str = Depends(get_tenant_id)):
    """Salva dados temporarios para OAuth antes do redirecionamento."""
    del client_id
    salvar_temp_auth(temp_data)
    return {"success": True}


async def start_bling_auth(auth_req: AuthRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    if not buscar_loja(client_id, auth_req.loja):
        raise HTTPException(status_code=404, detail="Loja nao encontrada.")
    state = secrets.token_urlsafe(24)
    redirect_uri = _resolver_redirect_uri_bling(request=request)
    salvar_temp_auth({
        "client_id": client_id,
        "loja": auth_req.loja,
        "servico": "bling",
        "id": auth_req.client_id,
        "secret": auth_req.client_secret,
        "state": state,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    })
    return {"success": True, "url": auth_bling_get_link(auth_req.client_id, state, redirect_uri=redirect_uri), "redirect_uri": redirect_uri}


async def start_mercadolivre_auth(auth_req: AuthRequest, request: Request, client_id: str = Depends(get_tenant_id)):
    if not buscar_loja(client_id, auth_req.loja):
        raise HTTPException(status_code=404, detail="Loja nao encontrada.")
    state = secrets.token_urlsafe(24)
    redirect_uri = _resolver_redirect_uri_publica(request=request)
    salvar_temp_auth({
        "client_id": client_id,
        "loja": auth_req.loja,
        "servico": "mercadolivre",
        "id": auth_req.client_id,
        "secret": auth_req.client_secret,
        "state": state,
        "redirect_uri": redirect_uri,
        "created_at": time.time(),
    })
    return {"success": True, "url": auth_ml_get_link(auth_req.client_id, state, redirect_uri=redirect_uri)}


async def integracoes_auth_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None, error_description: Optional[str] = None):
    del request

    def _redirect(status, msg=""):
        destino = f"/integracoes.html?status={quote_plus(status)}"
        if msg:
            destino += f"&msg={quote_plus(str(msg)[:240])}"
        return RedirectResponse(destino)

    if error:
        limpar_temp_auth()
        return _redirect("error", error_description or error)

    temp = ler_temp_auth()
    if not isinstance(temp, dict):
        return _redirect("error", "Dados temporarios da integracao nao encontrados.")

    if temp.get("state") and state and str(temp.get("state")) != str(state):
        limpar_temp_auth()
        return _redirect("error", "State OAuth invalido. Inicie a integracao novamente.")

    servico = str(temp.get("servico") or "").strip().lower()
    loja = str(temp.get("loja") or "").strip()
    client_id = str(temp.get("client_id") or "default").strip() or "default"
    app_id = str(temp.get("id") or "").strip()
    secret = str(temp.get("secret") or "").strip()
    redirect_uri = temp.get("redirect_uri")

    if not code or not loja or not app_id or not secret or servico not in {"bling", "mercadolivre"}:
        limpar_temp_auth()
        return _redirect("error", "Dados incompletos para finalizar a integracao.")

    if servico == "bling":
        ok, result = auth_bling_exchange(app_id, secret, code, redirect_uri=redirect_uri)
        if not ok:
            limpar_temp_auth()
            return _redirect("error", result)
        atualizar_api_loja(client_id, loja, "bling", {
            "id": app_id,
            "secret": secret,
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "connected": True,
            "updated_at": str(time.time()),
        })
        try:
            _shared_sync_propagar_lojas_integracoes_cliente(client_id, "bling-oauth-auth")
        except Exception as exc:
            logger.warning("[BLING] Nao foi possivel propagar autenticacao para compartilhamentos: %s", exc)
    else:
        ok, result = auth_ml_exchange(app_id, secret, code, redirect_uri=redirect_uri)
        if not ok:
            limpar_temp_auth()
            return _redirect("error", result)
        loja_atual = buscar_loja(client_id, loja) or {}
        cfg_atual = ((loja_atual.get("integracoes") or {}).get("mercadolivre") or {}) if isinstance(loja_atual, dict) else {}
        refresh_token = result.get("refresh_token") or (cfg_atual.get("refresh_token") if isinstance(cfg_atual, dict) else None)
        scope_retorno = str(result.get("scope") or "").strip()

        if not result.get("access_token") or not refresh_token:
            atualizar_api_loja(client_id, loja, "mercadolivre", {
                "id": app_id,
                "app_id": app_id,
                "secret": secret,
                "client_secret": secret,
                "access_token": result.get("access_token"),
                "refresh_token": refresh_token,
                "user_id": result.get("user_id"),
                "connected": False,
                "status": "sem_refresh_token",
                "motivo": "Mercado Livre nao retornou refresh_token/offline_access.",
                "scope": scope_retorno,
                "updated_at": str(time.time()),
            })
            limpar_temp_auth()
            return _redirect(
                "error",
                "Mercado Livre autorizou sem refresh_token. O app retornou sem offline_access; habilite offline_access/read/write no DevCenter do Mercado Livre, remova a autorizacao antiga e conecte novamente.",
            )
        atualizar_api_loja(client_id, loja, "mercadolivre", {
            "id": app_id,
            "app_id": app_id,
            "secret": secret,
            "client_secret": secret,
            "access_token": result.get("access_token"),
            "refresh_token": refresh_token,
            "user_id": result.get("user_id"),
            "connected": True,
            "status": "conectado",
            "motivo": "",
            "scope": scope_retorno,
            "updated_at": str(time.time()),
        })

    limpar_temp_auth()
    return _redirect("success")


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
    "integracoes_auth_callback",
]
