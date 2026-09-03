"""Automated public-question polling workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.contracts import _PERGUNTAS_AUTOMACAO_PROCESS_BATCH
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import perguntas_pos_venda_codex
from backend.services.perguntas_pos_venda_state import PerguntasIARespostaIndisponivel
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake
from backend.modules.perguntas_pos_venda.endpoints.customer_reply import (
    _customer_reply_approval_job_current,
    _customer_reply_automation_terminal_blocker,
    _customer_reply_job_already_reconciled,
    _customer_reply_job_draft,
    _customer_reply_late_reconciliation_candidate,
    _customer_reply_question_approval,
)
from backend.modules.perguntas_pos_venda.endpoints.jobs import (
    _customer_reply_requires_approval,
)
from backend.modules.perguntas_pos_venda.endpoints.question_paging import (
    _perguntas_automacao_buscar_todas,
    _perguntas_automacao_question_ids,
)

_ml_api_request = runtime_adapter("_ml_api_request")
_ml_buscar_itens_batch = runtime_adapter("_ml_buscar_itens_batch")
_ml_oauth_status = runtime_adapter("_ml_oauth_status")
_ml_perguntas_anexar_historico_comprador = runtime_adapter("_ml_perguntas_anexar_historico_comprador")
_ml_perguntas_buscar_usuarios = runtime_adapter("_ml_perguntas_buscar_usuarios")
_ml_perguntas_completar_skus_itens = runtime_adapter("_ml_perguntas_completar_skus_itens")
_ml_perguntas_normalizar = runtime_adapter("_ml_perguntas_normalizar")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
_perguntas_ia_aprovacao_pendente = runtime_adapter("_perguntas_ia_aprovacao_pendente")
_perguntas_ia_aprovacoes_carregar = runtime_adapter("_perguntas_ia_aprovacoes_carregar")
_perguntas_ia_aprovacoes_salvar = runtime_adapter("_perguntas_ia_aprovacoes_salvar")
_perguntas_ia_gerar_resposta = runtime_adapter("_perguntas_ia_gerar_resposta")
_perguntas_ia_ja_processada = runtime_adapter("_perguntas_ia_ja_processada")
_perguntas_ia_resolver_aprovacao = runtime_adapter("_perguntas_ia_resolver_aprovacao")
_perguntas_ia_state_carregar = runtime_adapter("_perguntas_ia_state_carregar")
_perguntas_ia_state_salvar = runtime_adapter("_perguntas_ia_state_salvar")
_perguntas_loja_config_normalizar = runtime_adapter("_perguntas_loja_config_normalizar")
_perguntas_loja_configs_carregar = runtime_adapter("_perguntas_loja_configs_carregar")
carregar_lojas = runtime_adapter("carregar_lojas")
logger = runtime_adapter("logger")


@dataclass
class _QuestionPoll:
    client_id: str
    state: dict
    approvals: list[dict]
    max_per_store: int
    pending: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    deferred: list[dict] = field(default_factory=list)
    snapshots: list[dict] = field(default_factory=list)
    approvals_changed: bool = False
    state_changed: bool = False
    queue: dict[str, Any] = field(default_factory=lambda: {
        "queue_saturated": False,
        "limit_per_store": perguntas_pos_venda_codex.AUTOMATION_QUEUE_PER_STORE_LIMIT,
        "limit_total": perguntas_pos_venda_codex.AUTOMATION_QUEUE_TOTAL_LIMIT,
    })


def _question_poll_candidates(
    poll: _QuestionPoll, store: str, questions: list[dict]
) -> list[dict]:
    candidates = []
    for question in questions:
        question_id = str(question.get("id") or "").strip()
        if not question_id or str(question.get("status") or "").upper() != "UNANSWERED":
            continue
        if question.get("hold") or question.get("deleted_from_listing") or question.get("suspected_spam"):
            continue
        if _perguntas_ia_ja_processada(poll.state, store, question_id):
            continue
        approval = _perguntas_ia_aprovacao_pendente(poll.approvals, store, question_id)
        if approval:
            if _customer_reply_approval_job_current(poll.client_id, approval):
                continue
            poll.approvals_changed = _perguntas_ia_resolver_aprovacao(
                approval, "stale_contract", "contrato_ia_anterior_ou_sem_job_verificavel"
            ) or poll.approvals_changed
        candidates.append(question)
    return candidates


def _question_poll_load_batch(
    poll: _QuestionPoll,
    store: str,
    cfg: dict,
    seller_id: str,
    batch: list[dict],
) -> tuple[list[dict], dict[str, dict], dict]:
    item_ids = list(dict.fromkeys(
        str(question.get("item_id") or "").strip()
        for question in batch
        if str(question.get("item_id") or "").strip()
    ))
    items, cfg = _ml_buscar_itens_batch(poll.client_id, store, cfg, item_ids)
    items = _ml_perguntas_completar_skus_itens(poll.client_id, store, cfg, items)
    for item in items:
        if isinstance(item, dict):
            item["_ppv_official_current_listing"] = True
    item_by_id = {str(item.get("id") or "").strip(): item for item in items if isinstance(item, dict)}
    user_ids = list(dict.fromkeys(
        str((question.get("from") or {}).get("id") or "").strip()
        for question in batch
        if isinstance(question.get("from"), dict)
        and str(question["from"].get("id") or "").strip()
    ))
    users, cfg = _ml_perguntas_buscar_usuarios(poll.client_id, store, cfg, user_ids)
    normalized = [_ml_perguntas_normalizar(item, item_by_id, users) for item in batch]
    normalized, cfg = _ml_perguntas_anexar_historico_comprador(
        poll.client_id, store, cfg, seller_id, normalized
    )
    return normalized, item_by_id, cfg


def _question_poll_defer(
    poll: _QuestionPoll, store: str, question_id: str, reason: str
) -> None:
    poll.deferred.append({
        "loja": _corrigir_texto_mojibake(store),
        "question_id": question_id,
        "reason": reason,
    })


def _question_poll_add_approval(
    poll: _QuestionPoll,
    store: str,
    question_id: str,
    question: dict,
    answer: str,
    context: dict,
) -> None:
    approval = _customer_reply_question_approval(
        nome_loja=store, question_id=question_id, pergunta=question,
        resposta=answer, contexto=context,
    )
    poll.approvals.append(approval)
    poll.pending.append(approval)
    poll.approvals_changed = True


def _question_poll_process_candidate(
    poll: _QuestionPoll,
    *,
    store: str,
    cfg: dict,
    question: dict,
    item: dict,
) -> tuple[dict, int, bool]:
    question_id = str(question.get("id") or "").strip()
    request = {
        "pergunta": question, "item": item,
        "question_text": str(question.get("text") or ""),
        "sku": str(question.get("item_sku") or question.get("sku") or ""),
    }
    if perguntas_pos_venda_codex.enabled():
        latest, reconciled = _customer_reply_late_reconciliation_candidate(
            client_id=poll.client_id, task_type="question", store=store,
            subject_key=question_id, request=request, approvals=poll.approvals,
        )
        if latest:
            if reconciled:
                return cfg, 0, False
            answer, context = _customer_reply_job_draft(latest)
            _question_poll_add_approval(poll, store, question_id, question, answer, context)
            return cfg, 1, False
        blocker = _customer_reply_automation_terminal_blocker(
            client_id=poll.client_id, task_type="question", store=store,
            subject_key=question_id, request=request,
        )
        if blocker:
            _question_poll_defer(poll, store, question_id, blocker)
            return cfg, 0, False
    try:
        if perguntas_pos_venda_codex.enabled():
            admission = perguntas_pos_venda_codex.automation_queue_admission(poll.client_id, store)
            poll.queue.update(admission)
            if not admission.get("allowed"):
                _question_poll_defer(poll, store, question_id, "queue_backpressure")
                return cfg, 0, True
            job = perguntas_pos_venda_codex.create_job(
                client_id=poll.client_id, task_type="question", store=store,
                subject_key=question_id, request=request, channel="app",
                created_by="perguntas_automacao",
            )
            if job.get("queue_saturated") or str(job.get("status") or "") == "deferred":
                _question_poll_defer(poll, store, question_id, "queue_backpressure")
                poll.queue.update(dict(job.get("queue_backpressure") or {}))
                return cfg, 0, True
            if str(job.get("status") or "") in perguntas_pos_venda_codex.ACTIVE_STATUSES:
                return cfg, 1, False
            answer, context = _customer_reply_job_draft(job)
        else:
            answer, cfg, context = _perguntas_ia_gerar_resposta(
                poll.client_id, store, cfg, question, item
            )
    except PerguntasIARespostaIndisponivel as exc:
        logger.warning("[ML PERGUNTAS IA] evento=resposta_indisponivel status=erro tipo=%s", type(exc).__name__)
        poll.errors.append({"loja": store, "question_id": question_id, "erro": str(exc)})
        return cfg, 0, False
    if not answer or _customer_reply_job_already_reconciled(
        poll.approvals, context.get("codex_job_id")
    ):
        return cfg, 0, False
    if _customer_reply_requires_approval():
        _question_poll_add_approval(poll, store, question_id, question, answer, context)
        return cfg, 1, False
    return cfg, 0, False


def _question_poll_store(
    poll: _QuestionPoll,
    *,
    store: str,
    cfg: dict,
    seller_id: str,
    questions: list[dict],
) -> None:
    candidates = _question_poll_candidates(poll, store, questions)
    processed = 0
    stop = False
    for start in range(0, len(candidates), _PERGUNTAS_AUTOMACAO_PROCESS_BATCH):
        if processed >= poll.max_per_store or stop:
            break
        batch = candidates[start:start + _PERGUNTAS_AUTOMACAO_PROCESS_BATCH]
        normalized, item_by_id, cfg = _question_poll_load_batch(
            poll, store, cfg, seller_id, batch
        )
        for question in normalized:
            if processed >= poll.max_per_store:
                return
            item = item_by_id.get(str(question.get("item_id") or "").strip()) or {}
            cfg, increment, stop = _question_poll_process_candidate(
                poll, store=store, cfg=cfg, question=question, item=item
            )
            processed += increment
            if stop:
                break




def ml_perguntas_automacao_poll(
    loja: Optional[str] = None,
    max_per_store: int = 3,
    client_id: str = Depends(get_tenant_id),
):
    approvals = _perguntas_ia_aprovacoes_carregar(client_id)
    poll = _QuestionPoll(
        client_id=client_id,
        state=_perguntas_ia_state_carregar(client_id),
        approvals=approvals,
        max_per_store=max(1, min(int(max_per_store or 3), 5)),
    )
    configs = _perguntas_loja_configs_carregar(client_id)
    store_filter = str(loja or "").strip()
    for store_config in carregar_lojas(client_id) or []:
        if not isinstance(store_config, dict):
            continue
        store = str(store_config.get("nome") or "").strip()
        if not store or (store_filter and store != store_filter):
            continue
        config = _perguntas_loja_config_normalizar(configs.get(store))
        integrations = store_config.get("integracoes") or {}
        ml_config = integrations.get("mercadolivre") if isinstance(integrations, dict) else {}
        if not config.get("responder_automaticamente") or not _ml_oauth_status(ml_config).get("conectado"):
            continue
        snapshot = {
            "loja": _corrigir_texto_mojibake(store),
            "question_ids": [],
            "question_snapshot_complete": False,
        }
        poll.snapshots.append(snapshot)
        try:
            cfg = _obter_cfg_ml(client_id, store)
            seller_id = str(cfg.get("user_id") or "").strip()
            if not seller_id:
                poll.errors.append({
                    "loja": store, "erro": "ID do vendedor Mercado Livre nao encontrado."
                })
                continue
            questions, cfg = _perguntas_automacao_buscar_todas(
                client_id=client_id, nome_loja=store, cfg=cfg,
                seller_id=seller_id, request_fn=_ml_api_request,
            )
            snapshot["question_ids"] = _perguntas_automacao_question_ids(questions)
            snapshot["question_snapshot_complete"] = True
            _question_poll_store(
                poll, store=store, cfg=cfg, seller_id=seller_id, questions=questions
            )
        except HTTPException as exc:
            poll.errors.append({"loja": store, "erro": exc.detail})
        except Exception as exc:
            logger.warning("[ML PERGUNTAS IA] evento=automacao_loja status=erro tipo=%s", type(exc).__name__)
            poll.errors.append({"loja": store, "erro": str(exc)})
    if poll.approvals_changed:
        _perguntas_ia_aprovacoes_salvar(client_id, approvals)
    if poll.state_changed:
        _perguntas_ia_state_salvar(client_id, poll.state)
    pending = [
        item for item in approvals
        if isinstance(item, dict) and str(item.get("status") or "pending") == "pending"
    ]
    saturated = any(
        str(item.get("reason") or "") == "queue_backpressure"
        for item in poll.deferred if isinstance(item, dict)
    )
    poll.queue["queue_saturated"] = saturated
    return jsonable_encoder({
        "success": True, "novas_pendentes": poll.pending, "pendentes": pending,
        "enviadas": poll.sent, "erros": poll.errors, "deferred": poll.deferred,
        "queue_saturated": saturated, "queue_backpressure": poll.queue,
        "question_snapshots": poll.snapshots,
    })


__all__ = [
    "ml_perguntas_automacao_poll",
]
