"""Human approval workflows for questions and post-sale."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, HTTPException

from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.modules.perguntas_pos_venda.endpoints.state import ENDPOINTS_STATE
from backend.schemas import PerguntasAprovacaoRequest
from backend.services import perguntas_pos_venda_codex
from backend.services.perguntas_pos_venda_state import (
    ML_POS_VENDA_DEFAULT_MAX_CHARS,
    ML_RESPOSTA_PERGUNTA_MAX_CHARS,
)
from backend.modules.perguntas_pos_venda.endpoints.customer_reply import (
    _customer_reply_approval_job_current,
)

_ia_modo_perguntas_configurado = runtime_adapter("_ia_modo_perguntas_configurado")
_ia_modo_pos_venda_configurado = runtime_adapter("_ia_modo_pos_venda_configurado")
_ml_pos_venda_conversa_respondida_pela_loja = runtime_adapter("_ml_pos_venda_conversa_respondida_pela_loja")
_ml_pos_venda_enviar_resposta_ml = runtime_adapter("_ml_pos_venda_enviar_resposta_ml")
_ml_pos_venda_memoria_registrar_resposta_enviada = runtime_adapter("_ml_pos_venda_memoria_registrar_resposta_enviada")
_ml_pos_venda_montar_conversa_normalizada = runtime_adapter("_ml_pos_venda_montar_conversa_normalizada")
_ml_pos_venda_preparar_conversa_ia = runtime_adapter("_ml_pos_venda_preparar_conversa_ia")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_aprovacoes_carregar = runtime_adapter("_perguntas_ia_aprovacoes_carregar")
_perguntas_ia_aprovacoes_salvar = runtime_adapter("_perguntas_ia_aprovacoes_salvar")
_perguntas_ia_enviar_resposta_ml = runtime_adapter("_perguntas_ia_enviar_resposta_ml")
_perguntas_ia_limpar_resposta = runtime_adapter("_perguntas_ia_limpar_resposta")
_perguntas_ia_marcar_processada = runtime_adapter("_perguntas_ia_marcar_processada")
_perguntas_ia_memoria_registrar_resposta_aprovada = runtime_adapter("_perguntas_ia_memoria_registrar_resposta_aprovada")
_perguntas_ia_pergunta_respondida_ml = runtime_adapter("_perguntas_ia_pergunta_respondida_ml")
_perguntas_ia_resolver_aprovacao = runtime_adapter("_perguntas_ia_resolver_aprovacao")
_perguntas_ia_state_carregar = runtime_adapter("_perguntas_ia_state_carregar")
_perguntas_ia_state_salvar = runtime_adapter("_perguntas_ia_state_salvar")
_pos_venda_ia_limpar_resposta = runtime_adapter("_pos_venda_ia_limpar_resposta")
logger = runtime_adapter("logger")


def _aprovacao_eh_pos_venda(approval: Any) -> bool:
    if not isinstance(approval, dict):
        return False
    def _marcador(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    tipo = _marcador(approval.get("tipo") or approval.get("approval_type"))
    origens = (
        approval.get("origem"),
        approval.get("ia_origem"),
        approval.get("ia_finalidade"),
    )
    return tipo == "pos_venda" or any("pos_venda" in _marcador(origem) for origem in origens)


def ml_perguntas_aprovacoes_listar(client_id: str = Depends(get_tenant_id)):
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    mudou = False
    mudou_state = False
    state = None
    pendentes = []
    for approval in aprovacoes:
        if not isinstance(approval, dict) or str(approval.get("status") or "pending") != "pending":
            continue
        tipo = str(approval.get("tipo") or approval.get("approval_type") or "").strip().lower()
        if _aprovacao_eh_pos_venda(approval):
            # Mantem o registro historico intacto, mas nao o expoe nem consulta
            # o Mercado Livre para um fluxo de sugestao que foi desativado.
            continue
        if not _customer_reply_approval_job_current(client_id, approval):
            approval.update(
                {
                    "status": "stale_contract",
                    "resolved_at": datetime.now().isoformat(timespec="seconds"),
                    "resolved_reason": "contrato_ia_anterior_ou_sem_job_verificavel",
                }
            )
            mudou = True
            continue
        loja = str(approval.get("loja") or "").strip()
        question_id = str(approval.get("question_id") or "").strip()
        origem_padrao = "mercado_livre_pos_venda" if tipo == "pos_venda" else "mercado_livre_perguntas"
        finalidade_padrao = "pos_venda" if tipo == "pos_venda" else "perguntas_anuncio"
        if not approval.get("ia_origem"):
            approval["ia_origem"] = origem_padrao
            mudou = True
        if not approval.get("ia_finalidade"):
            approval["ia_finalidade"] = finalidade_padrao
            mudou = True
        if not approval.get("ia_modo"):
            approval["ia_modo"] = _ia_modo_pos_venda_configurado() if tipo == "pos_venda" else _ia_modo_perguntas_configurado()
            mudou = True
        if tipo == "pos_venda":
            pack_id = str(approval.get("pack_id") or "").strip()
            order_id = str(approval.get("order_id") or "").strip()
            try:
                if loja and pack_id:
                    cfg = _obter_cfg_ml(client_id, loja)
                    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(client_id, loja, cfg, pack_id, order_id)
                    if _ml_pos_venda_conversa_respondida_pela_loja(conversa):
                        if _perguntas_ia_resolver_aprovacao(approval, "answered_elsewhere", "pos_venda_respondido_por_outro_fluxo"):
                            state = state or _perguntas_ia_state_carregar(client_id)
                            _perguntas_ia_marcar_processada(state, loja, question_id, "answered_elsewhere")
                            mudou = True
                            mudou_state = True
                        continue
                    conversa_aprovacao = {
                        "pack_id": conversa.get("pack_id") or pack_id,
                        "order_id": conversa.get("order_id") or order_id,
                        "buyer_id": conversa.get("buyer_id") or approval.get("buyer_id") or "",
                        "buyer_nickname": conversa.get("buyer_nickname") or "",
                        "items": conversa.get("items") or [],
                        "messages": conversa.get("messages") or [],
                        "last_message_text": conversa.get("last_message_text") or approval.get("pergunta") or "",
                        "seller_max_message_length": conversa.get("seller_max_message_length") or approval.get("max_chars") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
                    }
                    approval["conversa"] = conversa_aprovacao
                    approval["mensagens"] = conversa_aprovacao["messages"]
                    approval["buyer_id"] = approval.get("buyer_id") or conversa_aprovacao["buyer_id"]
                    approval["pergunta"] = approval.get("pergunta") or conversa_aprovacao["last_message_text"]
                    mudou = True
            except Exception as exc:
                logger.warning("[PERGUNTAS IA] evento=validar_aprovacao_pos_venda status=erro tipo=%s", type(exc).__name__)
        else:
            try:
                if loja and question_id:
                    cfg = _obter_cfg_ml(client_id, loja)
                    respondida, pergunta_ml, cfg = _perguntas_ia_pergunta_respondida_ml(client_id, loja, cfg, question_id)
                    if respondida:
                        resposta_ml = ""
                        answer = pergunta_ml.get("answer") if isinstance(pergunta_ml.get("answer"), dict) else {}
                        if isinstance(answer, dict):
                            resposta_ml = str(answer.get("text") or "")
                        if _perguntas_ia_resolver_aprovacao(approval, "answered_elsewhere", "pergunta_respondida_por_outro_fluxo", resposta_ml):
                            state = state or _perguntas_ia_state_carregar(client_id)
                            _perguntas_ia_marcar_processada(state, loja, question_id, "answered_elsewhere")
                            mudou = True
                            mudou_state = True
                        continue
            except Exception as exc:
                logger.warning("[PERGUNTAS IA] evento=validar_aprovacao_pergunta status=erro tipo=%s", type(exc).__name__)
        pendentes.append(approval)
    if mudou:
        _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    if mudou_state and isinstance(state, dict):
        _perguntas_ia_state_salvar(client_id, state)
    return {"success": True, "pendentes": pendentes}


@dataclass
class _ApprovalSend:
    client_id: str
    approvals: list[dict]
    index: int
    approval: dict
    approval_id: str
    store: str
    question_id: str
    answer: str
    draft_hash: str
    idempotency_key: str
    existing_idempotency_key: str
    previous_status: str
    proposal_id: str
    cfg: dict


def _approval_prepare(
    req: PerguntasAprovacaoRequest, client_id: str
) -> tuple[_ApprovalSend, Optional[dict]]:
    approval_id = str(req.approval_id or "").strip()
    requested_store = str(req.store or "").strip()
    requested_question_id = str(req.question_id or "").strip()
    approvals = _perguntas_ia_aprovacoes_carregar(client_id)
    id_matches = [
        (index, item) for index, item in enumerate(approvals)
        if isinstance(item, dict) and str(item.get("id") or "").strip() == approval_id
    ]
    if not id_matches:
        raise HTTPException(status_code=404, detail="Aprovação não encontrada.")
    compatible = [
        (index, item) for index, item in id_matches
        if (not requested_store or str(item.get("loja") or "").strip().casefold() == requested_store.casefold())
        and (
            not requested_question_id
            or str(item.get("question_id") or item.get("pergunta_id") or "").strip()
            == requested_question_id
        )
    ]
    if len(compatible) != 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "Aprovacao ambigua: informe store e question_id para identificar uma unica pergunta."
                if len(compatible) > 1
                else "O escopo informado nao corresponde a aprovacao solicitada."
            ),
        )
    index, approval = compatible[0]
    if _aprovacao_eh_pos_venda(approval):
        raise HTTPException(
            status_code=409,
            detail="Sugestoes e aprovacoes de pos-venda estao desativadas. Envie somente uma resposta manual.",
        )
    store = str(approval.get("loja") or "").strip()
    question_id = str(approval.get("question_id") or "").strip()
    edited = req.resposta if req.resposta is not None else req.texto
    answer = str(edited if edited is not None else approval.get("resposta_sugerida") or "")
    draft_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    expected_key = hashlib.sha256(
        f"{client_id}|{store}|{question_id}|{draft_hash}".encode("utf-8")
    ).hexdigest()
    requested_key = str(req.idempotency_key or "").strip().lower()
    if requested_key and requested_key != expected_key:
        raise HTTPException(
            status_code=409,
            detail="Chave idempotente nao corresponde ao cliente, loja, pergunta e rascunho.",
        )
    idempotency_key = requested_key or expected_key
    existing_key = str(approval.get("send_idempotency_key") or "").strip().lower()
    if existing_key and existing_key != idempotency_key:
        raise HTTPException(
            status_code=409,
            detail="Esta pergunta possui outro envio em andamento ou concluido.",
        )
    previous_status = str(approval.get("status") or "pending")
    context = _ApprovalSend(
        client_id=client_id, approvals=approvals, index=index, approval=approval,
        approval_id=approval_id, store=store, question_id=question_id, answer=answer,
        draft_hash=draft_hash, idempotency_key=idempotency_key,
        existing_idempotency_key=existing_key, previous_status=previous_status,
        proposal_id=str(approval.get("proposal_id") or approval.get("codex_job_id") or "").strip(),
        cfg={},
    )
    if previous_status not in {"pending", "sending"}:
        replay = bool(existing_key == idempotency_key and previous_status.startswith("sent"))
        return context, {"success": True, "approval": approval, "idempotent_replay": replay}
    context.cfg = _obter_cfg_ml(client_id, store)
    return context, None


def _approval_question_preflight(context: _ApprovalSend) -> Optional[dict]:
    if not context.answer.strip():
        raise HTTPException(status_code=400, detail="Informe uma resposta antes de aprovar.")
    if len(context.answer) > ML_RESPOSTA_PERGUNTA_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"A resposta possui {len(context.answer)} caracteres. Edite o rascunho para no maximo "
                f"{ML_RESPOSTA_PERGUNTA_MAX_CHARS} caracteres antes de enviar."
            ),
        )
    context.approval["manual_edit_required"] = False
    context.approval["manual_edit_reason"] = ""
    responded, question, context.cfg = _perguntas_ia_pergunta_respondida_ml(
        context.client_id, context.store, context.cfg, context.question_id
    )
    if not question:
        raise HTTPException(
            status_code=503, detail="Nao foi possivel confirmar se a pergunta ainda esta pendente."
        )
    if responded:
        answer_data = question.get("answer") if isinstance(question.get("answer"), dict) else {}
        existing_answer = str(answer_data.get("text") or "")
        reconciled = context.previous_status == "sending" and existing_answer == context.answer
        context.approval.update({
            "status": "sent_reconciled" if reconciled else "answered_elsewhere",
            "resolved_at": dt.datetime.now().isoformat(timespec="seconds"),
            "resolution_reason": (
                "idempotent_reconciliation" if reconciled else "pergunta_respondida_por_outro_fluxo"
            ),
            "resposta_enviada": existing_answer,
            "mercadolivre": question,
            "send_idempotency_key": (
                context.idempotency_key if reconciled else context.existing_idempotency_key
            ),
        })
        context.approvals[context.index] = context.approval
        _perguntas_ia_aprovacoes_salvar(context.client_id, context.approvals)
        return {"success": True, "approval": context.approval, "idempotent_replay": reconciled}
    if not context.proposal_id or not _customer_reply_approval_job_current(
        context.client_id, context.approval
    ):
        context.approval.update({
            "status": "stale_contract",
            "resolved_at": datetime.now().isoformat(timespec="seconds"),
            "resolution_reason": "contrato_ia_anterior_ou_sem_job_verificavel",
        })
        context.approvals[context.index] = context.approval
        _perguntas_ia_aprovacoes_salvar(context.client_id, context.approvals)
        raise HTTPException(
            status_code=409,
            detail="Esta sugestao pertence a um contrato de IA anterior. Gere uma nova resposta antes de aprovar.",
        )
    return None


def _approval_send_question(context: _ApprovalSend) -> tuple[Any, Optional[dict]]:
    proposal_info = perguntas_pos_venda_codex.approve_or_refresh_proposal(
        client_id=context.client_id,
        proposal_id=context.proposal_id,
        proposal_version=int(context.approval.get("proposal_version") or 0),
        proposal_hash=str(context.approval.get("proposal_hash") or ""),
        answer=context.answer,
        store=context.store,
        subject_key=context.question_id,
    )
    context.approval.update({
        "status": "sending", "send_idempotency_key": context.idempotency_key,
        "send_draft_hash": context.draft_hash,
        "send_started_at": dt.datetime.now().isoformat(timespec="seconds"),
    })
    context.approvals[context.index] = context.approval
    _perguntas_ia_aprovacoes_salvar(context.client_id, context.approvals)
    try:
        response, context.cfg = _perguntas_ia_enviar_resposta_ml(
            context.client_id, context.store, context.cfg,
            context.question_id, context.answer,
        )
    except Exception:
        context.approval["status"] = "pending"
        context.approval["send_last_error_at"] = dt.datetime.now().isoformat(timespec="seconds")
        context.approvals[context.index] = context.approval
        _perguntas_ia_aprovacoes_salvar(context.client_id, context.approvals)
        perguntas_pos_venda_codex.mark_verified(
            client_id=context.client_id, job_id=context.proposal_id, success=False
        )
        raise
    return response, proposal_info


def _approval_finalize(
    context: _ApprovalSend, response: Any, proposal_info: Optional[dict]
) -> dict:
    perguntas_pos_venda_codex.mark_verified(
        client_id=context.client_id,
        job_id=context.proposal_id,
        success=True,
        evidence={"mercadolivre": response, "approval_id": context.approval_id},
    )
    if proposal_info:
        context.approval["proposal_version"] = (
            proposal_info.get("proposal_version") or context.approval.get("proposal_version")
        )
        context.approval["proposal_hash"] = (
            proposal_info.get("proposal_hash") or context.approval.get("proposal_hash")
        )
    context.approval.update({
        "status": "sent",
        "sent_at": dt.datetime.now().isoformat(timespec="seconds"),
        "resposta_enviada": context.answer,
        "mercadolivre": response,
    })
    context.approvals[context.index] = context.approval
    _perguntas_ia_aprovacoes_salvar(context.client_id, context.approvals)
    state = _perguntas_ia_state_carregar(context.client_id)
    _perguntas_ia_marcar_processada(
        state, context.store, context.question_id, "sent_approved"
    )
    _perguntas_ia_state_salvar(context.client_id, state)
    try:
        _perguntas_ia_memoria_registrar_resposta_aprovada(
            context.client_id, context.store, context.answer,
            approval=context.approval, origem="aprovacao",
            question_id=context.question_id,
        )
    except Exception as exc:
        logger.warning("[ML PERGUNTAS IA] evento=registrar_memoria_aprovada status=erro tipo=%s", type(exc).__name__)
    return {"success": True, "approval": context.approval}


def ml_perguntas_aprovacoes_aprovar(req: PerguntasAprovacaoRequest, client_id: str = Depends(get_tenant_id)):
    # A reserva, a consulta remota e o POST precisam formar uma única seção
    # crítica no backend local. Isso fecha cliques simultâneos e permite
    # reconciliar um processo reiniciado depois de a API ter aceitado a resposta.
    with ENDPOINTS_STATE.approval_send_lock:
        return _ml_perguntas_aprovacoes_aprovar_locked(req, client_id)




def _ml_perguntas_aprovacoes_aprovar_locked(
    req: PerguntasAprovacaoRequest, client_id: str
):
    context, replay = _approval_prepare(req, client_id)
    if replay is not None:
        return replay
    preflight = _approval_question_preflight(context)
    if preflight is not None:
        return preflight
    response, proposal_info = _approval_send_question(context)
    return _approval_finalize(context, response, proposal_info)


def ml_perguntas_aprovacoes_rejeitar(req: PerguntasAprovacaoRequest, client_id: str = Depends(get_tenant_id)):
    approval_id = str(req.approval_id or "").strip()
    aprovacoes = _perguntas_ia_aprovacoes_carregar(client_id)
    idx = next((i for i, item in enumerate(aprovacoes) if isinstance(item, dict) and str(item.get("id") or "") == approval_id), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="AprovaÃ§Ã£o nÃ£o encontrada.")
    approval = aprovacoes[idx]
    approval["status"] = "rejected"
    approval["rejected_at"] = dt.datetime.now().isoformat(timespec="seconds")
    proposal_id = str(approval.get("proposal_id") or approval.get("codex_job_id") or "").strip()
    if proposal_id:
        perguntas_pos_venda_codex.mark_rejected(client_id=client_id, job_id=proposal_id)
    aprovacoes[idx] = approval
    _perguntas_ia_aprovacoes_salvar(client_id, aprovacoes)
    state = _perguntas_ia_state_carregar(client_id)
    _perguntas_ia_marcar_processada(state, str(approval.get("loja") or ""), str(approval.get("question_id") or ""), "rejected")
    _perguntas_ia_state_salvar(client_id, state)
    return {"success": True, "approval": approval}


__all__ = [
    "ml_perguntas_aprovacoes_listar",
    "ml_perguntas_aprovacoes_aprovar",
    "ml_perguntas_aprovacoes_rejeitar",
]
