"""Manual public-question generation and sending."""

from __future__ import annotations


from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas import PerguntasEnviarRespostaRequest, PerguntasGerarRespostaRequest
from backend.services import perguntas_pos_venda_codex
from backend.services.perguntas_pos_venda_state import ML_RESPOSTA_PERGUNTA_MAX_CHARS, PerguntasIARespostaIndisponivel
from backend.modules.perguntas_pos_venda.endpoints.jobs import (
    _customer_reply_wait_or_raise,
)
from backend.modules.perguntas_pos_venda.endpoints.questions_loading_support import resolve_scope
from backend.services.cadastro_compatibilidade import resolver_loja_ativa_para_leitura
from backend.modules.perguntas_pos_venda.ai.catalog_context import bind_official_listing_catalog_identity

_ml_api_item = runtime_adapter("_ml_api_item")
_ml_api_item_com_oauth_tenant = runtime_adapter("_ml_api_item_com_oauth_tenant")
_ml_api_request = runtime_adapter("_ml_api_request")
_ml_extrair_sku = runtime_adapter("_ml_extrair_sku")
_ml_perguntas_completar_skus_itens = runtime_adapter("_ml_perguntas_completar_skus_itens")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_enviar_resposta_ml = runtime_adapter("_perguntas_ia_enviar_resposta_ml")
_perguntas_ia_gerar_resposta = runtime_adapter("_perguntas_ia_gerar_resposta")
_perguntas_ia_marcar_processada = runtime_adapter("_perguntas_ia_marcar_processada")
_perguntas_ia_memoria_registrar_resposta_aprovada = runtime_adapter("_perguntas_ia_memoria_registrar_resposta_aprovada")
_perguntas_ia_resolver_aprovacoes_pendentes = runtime_adapter("_perguntas_ia_resolver_aprovacoes_pendentes")
_perguntas_ia_state_carregar = runtime_adapter("_perguntas_ia_state_carregar")
_perguntas_ia_state_salvar = runtime_adapter("_perguntas_ia_state_salvar")
logger = runtime_adapter("logger")


def _manual_generation_scope(
    request: Request | None,
    client_id: str,
    store_id: str,
    store_name: str,
):
    if request is None:
        return None
    exact_store_id = str(store_id or "").strip()
    if not exact_store_id:
        exact_store_id = str(
            resolver_loja_ativa_para_leitura(client_id, store_name).get("store_id") or ""
        ).strip()
    if not exact_store_id:
        raise HTTPException(status_code=400, detail="Informe o store_id exato da loja.")
    scope = resolve_scope(request, client_id, exact_store_id)
    if scope.name != store_name:
        raise HTTPException(status_code=409, detail="A loja selecionada mudou. Atualize a lista de lojas.")
    return scope


def ml_perguntas_gerar_resposta_manual(
    req: PerguntasGerarRespostaRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    # Compatibility for both historical direct-call orders used internally.
    if isinstance(req, Request):
        req, request = request, req
    elif isinstance(request, str):
        client_id, request = request, None
    loja = str(req.loja or "").strip()
    pergunta = req.pergunta if isinstance(req.pergunta, dict) else {}
    resposta_atual = str(req.resposta_atual or "")
    if resposta_atual.strip():
        pergunta = {**pergunta, "_resposta_atual": resposta_atual}
    orientacao_usuario = str(req.orientacao_usuario or "").strip()
    if orientacao_usuario:
        pergunta = {**pergunta, "_orientacao_usuario": orientacao_usuario[:1200]}
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not str(pergunta.get("id") or "").strip():
        raise HTTPException(status_code=400, detail="Informe a pergunta.")

    if perguntas_pos_venda_codex.enabled():
        scope = _manual_generation_scope(request, client_id, str(req.store_id or ""), loja)
        job = perguntas_pos_venda_codex.create_job(
            client_id=client_id,
            task_type="question",
            store=loja,
            subject_key=str(pergunta.get("id") or "").strip(),
            request={
                "pergunta": pergunta,
                "question_text": str(pergunta.get("text") or ""),
                "resposta_atual": resposta_atual,
                "orientacao_usuario": orientacao_usuario,
                "sku": str(pergunta.get("item_sku") or pergunta.get("sku") or ""),
            },
            store_id=scope.store_id if scope is not None else "",
            seller_id=scope.seller_id if scope is not None else "",
            site_id=scope.site_id if scope is not None else "",
            channel="app",
        )
        if req.async_mode:
            return jsonable_encoder(job)
        job = _customer_reply_wait_or_raise(client_id, job)
        if str(job.get("status") or "") != "completed":
            return jsonable_encoder(job)
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        return jsonable_encoder({
            **job,
            "success": bool(
                job.get("success", str(job.get("status") or "") == "completed")
            ) and not bool(job.get("blocked_without_draft")),
            "loja": loja,
            "question_id": str(pergunta.get("id") or "").strip(),
            "resposta": result.get("resposta") or "",
            "contexto": result.get("contexto") or {},
            "job_id": job.get("job_id") or "",
            "agent_state": job.get("agent_state") or "aguardando_aprovacao",
            "subquestions": job.get("subquestions") or [],
            "evidence_status": job.get("evidence_status") or [],
            "data_sufficient": bool(job.get("data_sufficient")),
            "proposal_version": job.get("proposal_version") or 1,
            "warnings": job.get("warnings") or [],
        })

    cfg = _obter_cfg_ml(client_id, loja)
    item_id = str(pergunta.get("item_id") or "").strip()
    item = {}
    official_current_listing = False
    if item_id:
        try:
            resp_item, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}",
                timeout=12,
            )
            if resp_item.status_code == 200:
                loaded_item = resp_item.json() or {}
                if isinstance(loaded_item, dict) and loaded_item:
                    item = loaded_item
                    official_current_listing = True
        except Exception as exc:
            logger.warning("[ML PERGUNTAS] evento=buscar_item_manual status=erro tipo=%s", type(exc).__name__)
    if not item:
        loaded_item = _ml_api_item_com_oauth_tenant(client_id, item_id) or _ml_api_item(item_id) or {}
        if isinstance(loaded_item, dict) and loaded_item:
            item = loaded_item
            official_current_listing = True
    if not isinstance(item, dict):
        item = {}
    bind_official_listing_catalog_identity(
        client_id, loja, cfg, pergunta, item if official_current_listing else {}, extract_sku=_ml_extrair_sku,
    )
    if item and not _ml_extrair_sku(item):
        item = _ml_perguntas_completar_skus_itens(client_id, loja, cfg, [item])[0]
    if not item:
        item = {
            "id": item_id,
            "title": pergunta.get("item_title") or "",
            "permalink": pergunta.get("item_permalink") or "",
            "thumbnail": pergunta.get("item_thumbnail") or "",
            "attributes": [
                {"id": "SELLER_SKU", "value_name": pergunta.get("item_sku") or ""}
            ] if pergunta.get("item_sku") else [],
        }
    item["_ppv_official_current_listing"] = official_current_listing

    try:
        resposta, cfg, contexto = _perguntas_ia_gerar_resposta(client_id, loja, cfg, pergunta, item)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "success": True, "loja": loja,
        "question_id": str(pergunta.get("id") or "").strip(),
        "resposta": resposta, "contexto": contexto,
    }


def ml_perguntas_responder_manual(req: PerguntasEnviarRespostaRequest, client_id: str = Depends(get_tenant_id)):
    loja = str(req.loja or "").strip()
    question_id = str(req.question_id or "").strip()
    resposta = str(req.resposta or "")
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not question_id:
        raise HTTPException(status_code=400, detail="Informe a pergunta.")
    if not resposta.strip():
        raise HTTPException(status_code=400, detail="Informe a resposta.")

    proposal_info = None
    if str(req.proposal_id or "").strip():
        try:
            proposal_info = perguntas_pos_venda_codex.approve_or_refresh_proposal(
                client_id=client_id,
                proposal_id=str(req.proposal_id or "").strip(),
                proposal_version=int(req.proposal_version or 0),
                proposal_hash=str(req.proposal_hash or "").strip(),
                answer=resposta,
                store=loja,
                subject_key=question_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Proposta de resposta nao encontrada.") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    cfg = _obter_cfg_ml(client_id, loja)
    try:
        resposta_ml, cfg = _perguntas_ia_enviar_resposta_ml(client_id, loja, cfg, question_id, resposta)
    except Exception:
        if proposal_info:
            perguntas_pos_venda_codex.mark_verified(
                client_id=client_id,
                job_id=str(req.proposal_id or ""),
                success=False,
            )
        raise
    if proposal_info:
        perguntas_pos_venda_codex.mark_verified(
            client_id=client_id,
            job_id=str(req.proposal_id or ""),
            success=True,
            evidence={"question_id": question_id, "mercadolivre": resposta_ml},
        )
    resolvidas = _perguntas_ia_resolver_aprovacoes_pendentes(
        client_id,
        loja,
        question_id=question_id,
        status="sent_manual",
        motivo="pergunta_respondida_manualmente",
        resposta=resposta,
    )
    try:
        if resolvidas:
            for approval in resolvidas:
                registrado = _perguntas_ia_memoria_registrar_resposta_aprovada(
                    client_id,
                    loja,
                    resposta,
                    approval=approval,
                    origem="manual_com_aprovacao",
                    question_id=question_id,
                )
                if not registrado:
                    _perguntas_ia_memoria_registrar_resposta_aprovada(
                        client_id,
                        loja,
                        resposta,
                        pergunta=req.pergunta if isinstance(req.pergunta, dict) else {},
                        origem="manual_com_aprovacao",
                        sku=req.sku,
                        item_id=req.item_id,
                        question_id=question_id,
                    )
        else:
            _perguntas_ia_memoria_registrar_resposta_aprovada(
                client_id,
                loja,
                resposta,
                pergunta=req.pergunta if isinstance(req.pergunta, dict) else {},
                origem="manual",
                sku=req.sku,
                item_id=req.item_id,
                question_id=question_id,
            )
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] evento=registrar_memoria_manual status=erro tipo=%s", type(exc).__name__)
    state = _perguntas_ia_state_carregar(client_id)
    _perguntas_ia_marcar_processada(state, loja, question_id, "sent_manual")
    _perguntas_ia_state_salvar(client_id, state)
    return {
        "success": True,
        "loja": loja,
        "question_id": question_id,
        "resposta": resposta,
        "mercadolivre": resposta_ml,
        "proposal_version": (proposal_info or {}).get("proposal_version") if proposal_info else None,
        "proposal_hash": (proposal_info or {}).get("proposal_hash") if proposal_info else "",
    }


__all__ = [
    "ml_perguntas_gerar_resposta_manual",
    "ml_perguntas_responder_manual",
]
