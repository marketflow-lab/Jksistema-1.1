"""Authorized scopes and bounded remote reads for the question-screen API."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, Request
from requests import Timeout

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.services import perguntas_loading_cache as cache
from backend.services.perguntas_loading_transport import read_budget


@contextmanager
def api_budget():
    try:
        with read_budget(seconds=15):
            yield
    except Timeout as error:
        raise HTTPException(504, "Tempo de consulta das perguntas esgotado.") from error

carregar_lojas = runtime_adapter("carregar_lojas")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_ml_oauth_status = runtime_adapter("_ml_oauth_status")
_ml_api_request = runtime_adapter("_ml_api_request")
_ml_perguntas_normalizar = runtime_adapter("_ml_perguntas_normalizar")
_ml_perguntas_resumir_status = runtime_adapter("_ml_perguntas_resumir_status")


@dataclass(frozen=True)
class Scope:
    client_id: str
    store_id: str
    username: str
    seller_id: str
    site_id: str
    name: str

    def key(self, kind: str, *parameters: Any) -> tuple:
        return (self.client_id, self.store_id, self.username, self.seller_id,
                self.site_id, kind, *parameters)


def authorized_stores(request: Request, client_id: str) -> list[dict]:
    username = str(getattr(request.state, "username", "") or "").strip()
    authenticated_tenant = str(getattr(request.state, "client_id", "") or "")
    if not username or authenticated_tenant != client_id:
        raise HTTPException(401, "Sessao autenticada necessaria para consultar perguntas.")
    with read_budget(seconds=15):
        return [row for row in (carregar_lojas(client_id) or []) if isinstance(row, dict)]


def resolve_scope(request: Request, client_id: str, store_id: str, *, rows=None, resolve_site=True) -> Scope:
    exact = str(store_id or "").strip()
    if not exact:
        raise HTTPException(400, "Informe o store_id exato da loja.")
    rows = authorized_stores(request, client_id) if rows is None else rows
    matches = [row for row in rows if str(row.get("store_id") or "").strip() == exact]
    if len(matches) != 1:
        raise HTTPException(403, "Loja nao autorizada nesta sessao.")
    row = matches[0]
    cfg = (row.get("integracoes") or {}).get("mercadolivre") or {}
    if not _ml_oauth_status(cfg).get("conectado"):
        raise HTTPException(401, "Reconecte esta loja ao Mercado Livre.")
    seller = str(cfg.get("user_id") or "").strip()
    if not seller:
        raise HTTPException(409, "A loja ainda nao possui seller_id confirmado.")
    scope = Scope(client_id, exact, str(request.state.username), seller,
                  str(cfg.get("site_id") or row.get("site_id") or "").strip(),
                  str(row.get("nome") or "").strip())
    if scope.site_id or not resolve_site:
        return scope
    return replace(scope, site_id=resolve_seller_site(scope))


def resolve_seller_site(scope: Scope) -> str:
    def load():
        cfg = _obter_cfg_ml(scope.client_id, scope.name, store_id=scope.store_id)
        seller = remote(scope, cfg, f"/users/{scope.seller_id}")
        if (not isinstance(seller, dict) or str(seller.get("id")) != scope.seller_id
                or not re.fullmatch(r"[A-Z]{3}", str(seller.get("site_id") or ""))):
            raise HTTPException(502, "Nao foi possivel confirmar o site desta conta.")
        return {"site_id": seller["site_id"]}
    result = cache.read(scope.key("identity"), load, ttl=3600, stale_seconds=0)
    return result["site_id"]


def config_for(scope: Scope, request: Request) -> dict:
    # Recheck authorization inside the initiating session context for background work.
    current = resolve_scope(request, scope.client_id, scope.store_id)
    if current != scope:
        raise HTTPException(409, "A conexao da loja mudou. Atualize a lista de lojas.")
    return _obter_cfg_ml(scope.client_id, scope.name, store_id=scope.store_id)


def remote(scope: Scope, cfg: dict, path: str, *, params=None) -> Any:
    response, updated = _ml_api_request(
        scope.client_id, scope.name, cfg, "GET", "https://api.mercadolibre.com" + path,
        params=params, timeout=15,
    )
    cfg.update(updated or {})
    if response.status_code != 200:
        code = response.status_code
        message = "Nao foi possivel consultar o Mercado Livre."
        if code in {401, 403}:
            message = "A conexao da loja nao autorizou esta consulta."
        elif code == 404:
            message = "O registro nao esta mais disponivel no Mercado Livre."
        elif code == 429:
            message = "O Mercado Livre limitou temporariamente as consultas."
        raise HTTPException(code if 400 <= code <= 599 else 502, message)
    return response.json()


def verify_seller(payload: dict, scope: Scope) -> None:
    if str(payload.get("seller_id") or "").strip() != scope.seller_id:
        raise HTTPException(403, "O registro nao pertence a loja selecionada.")


def validate_id(value: str, label: str, pattern: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(pattern, value):
        raise HTTPException(400, f"{label} invalido.")
    return value


def cached(scope: Scope, kind: str, parameters: tuple, loader, *, ttl: int,
           force: bool, stale_seconds: int = 600) -> dict:
    result = cache.read(scope.key(kind, *parameters), loader, ttl=ttl,
                        stale_seconds=stale_seconds, force=force)
    result.update(success=True, store_id=scope.store_id, loja=scope.name,
                  seller_id=scope.seller_id, site_id=scope.site_id)
    return result


def normalized_history(questions: list[dict], selected: dict) -> dict:
    """Keep history to this exact buyer and item, always retaining the selection."""
    buyer = str((selected.get("from") or {}).get("id") or "")
    item_id = str(selected.get("item_id") or "")
    rows = {str(selected.get("id")): selected}
    for question in questions:
        if (buyer and str((question.get("from") or {}).get("id") or "") == buyer
                and str(question.get("item_id") or "") == item_id):
            rows[str(question.get("id"))] = question
    history = [_ml_perguntas_normalizar(q, {}, {}) for q in rows.values()]
    history.sort(key=lambda q: (str(q.get("date_created") or ""), str(q.get("id") or "")))
    chat = []
    for question in history:
        if question.get("text"):
            chat.append({"role": "buyer", "label": "Comprador", "text": question["text"],
                         "date": question.get("date_created"), "question_id": question["id"]})
        answer = question.get("answer") or {}
        if answer.get("text"):
            chat.append({"role": "seller", "label": "Loja", "text": answer["text"],
                         "date": answer.get("date_created"), "question_id": question["id"]})
    normalized = _ml_perguntas_normalizar(selected, {}, {})
    normalized.update(buyer_question_history=history, buyer_question_chat=chat,
                      buyer_question_history_count=len(history))
    return normalized
