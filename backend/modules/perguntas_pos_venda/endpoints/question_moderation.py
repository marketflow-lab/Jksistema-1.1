"""Explicit moderation of a question in its authenticated seller account."""

from __future__ import annotations

import logging
import re

from fastapi import Depends, HTTPException, Request
from requests import RequestException, Timeout

from backend.modules.perguntas_pos_venda.endpoints import questions_loading_support as support
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas.perguntas_pos_venda import PerguntasModeracaoRequest
from backend.services.perguntas_loading_cache import invalidate_store


_ml_api_request = runtime_adapter("_ml_api_request")
_UNKNOWN_RESULT = "O Mercado Livre não confirmou o resultado. Confira a operação antes de repetir."


def _confirmed_question(request: Request, client_id: str, req: PerguntasModeracaoRequest):
    question_id = support.validate_id(req.question_id, "ID da pergunta", r"[1-9][0-9]{0,29}")
    with support.api_budget():
        scope = support.resolve_scope(request, client_id, req.store_id)
        cfg = support.config_for(scope, request)
        try:
            question = support.remote(scope, cfg, f"/questions/{question_id}", params={"api_version": 4})
        except Timeout:
            raise
        except (RequestException, ValueError) as error:
            raise HTTPException(502, "Não foi possível consultar a pergunta no Mercado Livre.") from error
        if not isinstance(question, dict) or str(question.get("id")) != question_id:
            raise HTTPException(502, "O Mercado Livre não confirmou a identidade da pergunta.")
        support.verify_seller(question, scope)
        item_id = str(question.get("item_id") or "")
        if not re.fullmatch(r"[A-Z]{3}[0-9]{1,20}", item_id):
            raise HTTPException(502, "O Mercado Livre não confirmou o anúncio da pergunta.")
        if item_id[:3] != scope.site_id:
            raise HTTPException(403, "O anúncio da pergunta não pertence ao site desta conta.")
    return scope, cfg, question_id, question


def _apply_moderation(scope: support.Scope, cfg: dict, method: str, path: str, *, body: dict | None = None) -> None:
    try:
        response, _ = _ml_api_request(
            scope.client_id, scope.name, cfg, method, f"https://api.mercadolibre.com{path}",
            json=body, timeout=15, retry_unauthorized=False,
        )
    except Timeout as error:
        raise HTTPException(504, _UNKNOWN_RESULT) from error
    except RequestException as error:
        raise HTTPException(502, _UNKNOWN_RESULT) from error
    status = response.status_code
    if status in {200, 201, 204}:
        return
    if status in {401, 403}:
        raise HTTPException(status, "O Mercado Livre não autorizou a ação nesta conta.")
    if status == 404:
        raise HTTPException(404, "O registro não está mais disponível no Mercado Livre.")
    if status == 429:
        raise HTTPException(429, "O Mercado Livre limitou temporariamente as ações. Tente novamente mais tarde.")
    if status == 408 or not 400 <= status < 500:
        raise HTTPException(502, _UNKNOWN_RESULT)
    raise HTTPException(status, "O Mercado Livre recusou a ação.")


def ml_perguntas_excluir(
    request: Request, req: PerguntasModeracaoRequest, client_id: str = Depends(get_tenant_id),
):
    scope, cfg, question_id, _ = _confirmed_question(request, client_id, req)
    _apply_moderation(scope, cfg, "DELETE", f"/questions/{question_id}")
    try:
        invalidate_store(scope.client_id, scope.store_id)
    except Exception:
        # A local cache failure must not turn a confirmed deletion into a retry.
        logging.getLogger(__name__).warning("Falha ao invalidar o cache após exclusão confirmada de pergunta.")
    return {"success": True}


def ml_perguntas_bloquear_comprador(
    request: Request, req: PerguntasModeracaoRequest, client_id: str = Depends(get_tenant_id),
):
    scope, cfg, _, question = _confirmed_question(request, client_id, req)
    author = question.get("from")
    buyer_id = str(author.get("id") or "") if isinstance(author, dict) else ""
    if not re.fullmatch(r"[1-9][0-9]{0,29}", buyer_id) or buyer_id == scope.seller_id:
        raise HTTPException(409, "O Mercado Livre não confirmou o comprador desta pergunta.")
    if not re.fullmatch(r"[1-9][0-9]{0,29}", scope.seller_id):
        raise HTTPException(409, "O Mercado Livre não confirmou o vendedor desta conta.")
    _apply_moderation(
        scope, cfg, "POST", f"/users/{scope.seller_id}/questions_blacklist", body={"user_id": int(buyer_id)},
    )
    return {"success": True}
