"""Question protocol v2 handlers."""

from __future__ import annotations

import datetime as dt
import json
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.schemas import MLQuestionsV2ProcessRequest, MLQuestionsV2ReviewActionRequest, PerguntasAprovacaoRequest
from backend.services import perguntas_pos_venda_codex
from backend.services.perguntas_pos_venda_state import PerguntasIARespostaIndisponivel
from backend.modules.perguntas_pos_venda.endpoints.approvals import (
    ml_perguntas_aprovacoes_aprovar,
    ml_perguntas_aprovacoes_listar,
    ml_perguntas_aprovacoes_rejeitar,
)
from backend.modules.perguntas_pos_venda.endpoints.jobs import (
    _customer_reply_wait_or_raise,
)

MercadoLivreWebhookReceiver = runtime_adapter("MercadoLivreWebhookReceiver")
_ml_questions_v2_webhook_events_path = runtime_adapter("_ml_questions_v2_webhook_events_path")
_ml_api_item = runtime_adapter("_ml_api_item")
_ml_api_item_com_oauth_tenant = runtime_adapter("_ml_api_item_com_oauth_tenant")
_ml_api_request = runtime_adapter("_ml_api_request")
_ml_extrair_sku = runtime_adapter("_ml_extrair_sku")
_ml_perguntas_completar_skus_itens = runtime_adapter("_ml_perguntas_completar_skus_itens")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_aprovacoes_carregar = runtime_adapter("_perguntas_ia_aprovacoes_carregar")
_perguntas_ia_gerar_resposta = runtime_adapter("_perguntas_ia_gerar_resposta")
_perguntas_ia_ler_json = runtime_adapter("_perguntas_ia_ler_json")
_perguntas_ia_salvar_json = runtime_adapter("_perguntas_ia_salvar_json")


async def ml_questions_v2_webhook(request: Request, client_id: str = Depends(get_tenant_id)):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Payload JSON invalido.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload JSON precisa ser um objeto.")
    receiver = MercadoLivreWebhookReceiver()
    event = receiver.normalize_event(payload)
    event["id"] = str(uuid.uuid4())
    event["received_at"] = dt.datetime.now().isoformat(timespec="seconds")
    event["status"] = "received"
    caminho = _ml_questions_v2_webhook_events_path(client_id)
    events = _perguntas_ia_ler_json(caminho, [])
    if not isinstance(events, list):
        events = []
    events.append(event)
    _perguntas_ia_salvar_json(caminho, events[-1000:])
    return {"success": True, "event": event, "processed": False}


def ml_questions_v2_process(question_id: str, req: MLQuestionsV2ProcessRequest, client_id: str = Depends(get_tenant_id)):
    loja = str(req.loja or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    pergunta = req.pergunta if isinstance(req.pergunta, dict) else {}
    pergunta = {**pergunta, "id": str(question_id or pergunta.get("id") or "").strip()}
    if str(req.resposta_atual or "").strip():
        pergunta["_resposta_atual"] = str(req.resposta_atual or "")
    if not pergunta.get("id"):
        raise HTTPException(status_code=400, detail="Informe a pergunta.")
    if not str(pergunta.get("text") or "").strip() and not pergunta.get("buyer_question_chat"):
        raise HTTPException(status_code=400, detail="Informe o texto da pergunta.")
    item = req.item if isinstance(req.item, dict) else {}
    if perguntas_pos_venda_codex.enabled():
        job = perguntas_pos_venda_codex.create_job(
            client_id=client_id,
            task_type="question",
            store=loja,
            subject_key=str(pergunta.get("id") or ""),
            request={
                "pergunta": pergunta,
                "item": item,
                "question_text": str(pergunta.get("text") or ""),
                "resposta_atual": str(req.resposta_atual or ""),
                "sku": str(pergunta.get("item_sku") or pergunta.get("sku") or ""),
            },
            channel="app",
        )
        job = _customer_reply_wait_or_raise(client_id, job)
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        return jsonable_encoder({
            **job,
            "success": bool(
                job.get("success", str(job.get("status") or "") == "completed")
            ) and not bool(job.get("blocked_without_draft")),
            "question_id": pergunta.get("id"),
            "answer": result.get("resposta") or "",
            "context": result.get("contexto") or {},
            "publish_attempted": False,
        })
    cfg = _obter_cfg_ml(client_id, loja)
    request_item = dict(item)
    item = {}
    official_current_listing = False
    item_id = str(pergunta.get("item_id") or request_item.get("id") or "").strip()
    if item_id:
        try:
            response, cfg = _ml_api_request(
                client_id,
                loja,
                cfg,
                "GET",
                f"https://api.mercadolibre.com/items/{item_id}",
                timeout=12,
            )
            if response.status_code == 200:
                loaded_item = response.json() or {}
                if isinstance(loaded_item, dict) and loaded_item:
                    item = loaded_item
                    official_current_listing = True
        except Exception:
            item = {}
    if not item:
        loaded_item = _ml_api_item_com_oauth_tenant(client_id, item_id) or _ml_api_item(item_id) or {}
        if isinstance(loaded_item, dict) and loaded_item:
            item = loaded_item
            official_current_listing = True
    if not item:
        item = request_item
    if item and not _ml_extrair_sku(item):
        item = _ml_perguntas_completar_skus_itens(client_id, loja, cfg, [item])[0]
    item["_ppv_official_current_listing"] = official_current_listing
    try:
        resposta, cfg, contexto = _perguntas_ia_gerar_resposta(client_id, loja, cfg, pergunta, item)
    except PerguntasIARespostaIndisponivel as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "success": True,
        "question_id": pergunta.get("id"),
        "answer": resposta,
        "context": contexto,
        "publish_attempted": False,
    }


def ml_questions_v2_pending_review(client_id: str = Depends(get_tenant_id)):
    return ml_perguntas_aprovacoes_listar(client_id)


def ml_questions_v2_review_approve(
    approval_id: str,
    req: MLQuestionsV2ReviewActionRequest,
    client_id: str = Depends(get_tenant_id),
):
    return ml_perguntas_aprovacoes_aprovar(
        PerguntasAprovacaoRequest(approval_id=approval_id, resposta=req.resposta, texto=req.texto),
        client_id,
    )


def ml_questions_v2_review_reject(
    approval_id: str,
    req: MLQuestionsV2ReviewActionRequest,
    client_id: str = Depends(get_tenant_id),
):
    return ml_perguntas_aprovacoes_rejeitar(
        PerguntasAprovacaoRequest(approval_id=approval_id, resposta=req.resposta, texto=req.texto),
        client_id,
    )


def ml_questions_v2_audit_question(question_id: str, client_id: str = Depends(get_tenant_id)):
    qid = str(question_id or "").strip()
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    eventos = _perguntas_ia_ler_json(_ml_questions_v2_webhook_events_path(client_id), [])
    if not isinstance(eventos, list):
        eventos = []
    eventos_match = [
        event for event in eventos
        if isinstance(event, dict) and qid and qid in json.dumps(event, ensure_ascii=False, default=str)
    ]
    aprovacoes_match = [
        item for item in aprovacoes
        if isinstance(item, dict) and str(item.get("question_id") or "") == qid
    ]
    return {
        "success": True,
        "question_id": qid,
        "approvals": aprovacoes_match,
        "webhook_events": eventos_match[-50:],
    }


def ml_questions_v2_metrics(client_id: str = Depends(get_tenant_id)):
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    metrics = {
        "total_reviews": 0,
        "pending": 0,
        "sent": 0,
        "rejected": 0,
        "by_category": {},
        "by_decision": {},
    }
    for item in aprovacoes:
        if not isinstance(item, dict):
            continue
        metrics["total_reviews"] += 1
        status = str(item.get("status") or "pending").strip() or "pending"
        if status in metrics:
            metrics[status] += 1
        category = str(item.get("ia_categoria") or item.get("ia_finalidade") or "unknown").strip() or "unknown"
        decision = str(item.get("ia_decision") or "human_review").strip() or "human_review"
        metrics["by_category"][category] = metrics["by_category"].get(category, 0) + 1
        metrics["by_decision"][decision] = metrics["by_decision"].get(decision, 0) + 1
    return {"success": True, "metrics": metrics}


__all__ = [
    "ml_questions_v2_webhook",
    "ml_questions_v2_process",
    "ml_questions_v2_pending_review",
    "ml_questions_v2_review_approve",
    "ml_questions_v2_review_reject",
    "ml_questions_v2_audit_question",
    "ml_questions_v2_metrics",
]
