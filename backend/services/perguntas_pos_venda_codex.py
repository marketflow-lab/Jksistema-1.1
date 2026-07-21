"""Persistent Codex orchestration for Mercado Livre customer replies.

This service owns operational jobs and technical Codex threads.  It deliberately
does not expose shell/file tools: all business context is collected by the
existing Perguntas/Pós-venda read-only adapters injected at application startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from backend.services import codex_agent_runtime, codex_assistant_storage
from backend.services.codex_turn_context import (
    EVIDENCE_ENVELOPE_V2,
    conversation_key,
    normalize_evidence_envelope,
)


PROFILE = "mercado_livre_customer_reply"
TASK_TYPE_PUBLIC_QUESTION = "public_question"
TASK_TYPE_POST_SALE = "post_sale"
TASK_TYPE_ALIASES = {"question": TASK_TYPE_PUBLIC_QUESTION}
TASK_TYPES = {TASK_TYPE_PUBLIC_QUESTION, TASK_TYPE_POST_SALE}
PUBLIC_SUBQUESTION_INTENTS = frozenset({
    "compatibility",
    "shipping",
    "invoice",
    "stock",
    "price",
    "warranty",
    "warranty_originality",
    "product_feature",
    "other_product",
    "general",
})
PROMPT_VERSION = "jk_ml_customer_reply_codex_v4"
SCHEMA_VERSION = "4.0"
PROMPT_HASH = hashlib.sha256(
    (
        "codex-native|public-question-by-item-buyer|post-sale-by-pack|"
        "evidence-envelope-v3|ai-only-subquestions|human-review-required|no-direct-publish"
    ).encode("utf-8")
).hexdigest()
THREAD_IDLE_TTL_SECONDS = 30 * 24 * 60 * 60
TERMINAL_STATUSES = {"completed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "waiting_retry"}
MAX_GLOBAL_JOBS = 2
MAX_SECONDS = 180.0
RESEARCH_DEADLINE_SECONDS = 180.0
RETRY_DELAYS_SECONDS = (5, 15, 30, 60, 120, 300)
RETRY_HISTORY_LIMIT = 12
RESEARCH_DEADLINE_WARNING = (
    "Limite de 3 minutos atingido; rascunho gerado com as informacoes disponiveis. "
    "Revise antes de responder."
)

logger = logging.getLogger(__name__)
_RUNTIME: Any = None
_SCHEDULER_LOCK = threading.RLock()
_ACTIVE_JOBS: set[str] = set()
_ACTIVE_STORES: set[str] = set()
_RETRY_TIMERS: dict[str, threading.Timer] = {}
_WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_RECOVERY_STARTED = False


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normal(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.lower()).strip()


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _created_at_epoch(job: dict[str, Any]) -> float:
    raw = str(job.get("updated_at") or job.get("completed_at") or job.get("created_at") or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return float(parsed.timestamp())
        return float(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _canonical_task_type(task_type: Any) -> str:
    normalized = str(task_type or "").strip().lower()
    return TASK_TYPE_ALIASES.get(normalized, normalized)


def _request_question(request: Optional[dict[str, Any]]) -> dict[str, Any]:
    data = request if isinstance(request, dict) else {}
    question = data.get("pergunta") if isinstance(data.get("pergunta"), dict) else {}
    return question


def _conversation_subject_key(
    task_type: str,
    event_subject_key: str,
    request: Optional[dict[str, Any]],
) -> tuple[str, dict[str, str]]:
    """Return the durable Codex thread scope without changing the external event id."""

    data = request if isinstance(request, dict) else {}
    canonical_type = _canonical_task_type(task_type)
    if canonical_type == TASK_TYPE_POST_SALE:
        pack_id = str(data.get("pack_id") or event_subject_key or "").strip()
        order_id = str(data.get("order_id") or "").strip()
        buyer_id = str(data.get("buyer_id") or "").strip()
        return f"pack:{pack_id}", {
            "pack_id": pack_id,
            "order_id": order_id,
            "buyer_id": buyer_id,
        }

    question = _request_question(data)
    item_id = str(question.get("item_id") or data.get("item_id") or "").strip()
    buyer_id = str(
        question.get("buyer_id")
        or question.get("from_id")
        or ((question.get("from") or {}).get("id") if isinstance(question.get("from"), dict) else "")
        or data.get("buyer_id")
        or ""
    ).strip()
    question_id = str(question.get("id") or event_subject_key or "").strip()
    if item_id and buyer_id:
        subject = f"item:{item_id}|buyer:{buyer_id}"
    else:
        subject = f"question:{question_id}"
    return subject, {
        "question_id": question_id,
        "item_id": item_id,
        "buyer_id": buyer_id,
    }


def _thread_reuse_decision(
    latest: Optional[dict[str, Any]],
    *,
    task_type: str,
    scope_verifiers: dict[str, str],
) -> tuple[str, str, bool]:
    """Return thread id, restart reason and whether the previous thread is reusable."""

    if not isinstance(latest, dict):
        return "", "new_conversation", False
    thread_id = str(latest.get("thread_id") or "").strip()
    if not thread_id:
        return "", "previous_thread_missing", False
    if str(latest.get("prompt_version") or "") != PROMPT_VERSION:
        return "", "prompt_version_changed", False
    if str(latest.get("schema_version") or "") != SCHEMA_VERSION:
        return "", "schema_version_changed", False
    if str(latest.get("prompt_hash") or "") != PROMPT_HASH:
        return "", "prompt_hash_changed", False
    last_activity = _created_at_epoch(latest)
    if not last_activity or (time.time() - last_activity) > THREAD_IDLE_TTL_SECONDS:
        return "", "thread_expired", False
    if _canonical_task_type(task_type) == TASK_TYPE_POST_SALE:
        previous = latest.get("scope_verifiers") if isinstance(latest.get("scope_verifiers"), dict) else {}
        for field in ("pack_id", "order_id", "buyer_id"):
            current_value = str(scope_verifiers.get(field) or "").strip()
            previous_value = str(previous.get(field) or "").strip()
            if current_value and previous_value and current_value != previous_value:
                return "", f"{field}_changed", False
    return thread_id, "", True


def _job_deadline_epoch(job: dict[str, Any]) -> float:
    try:
        explicit = float(job.get("deadline_at_epoch") or 0.0)
    except (TypeError, ValueError, OverflowError):
        explicit = 0.0
    if explicit > 0.0:
        return explicit
    created_epoch = _created_at_epoch(job)
    deadline = (created_epoch or time.time()) + RESEARCH_DEADLINE_SECONDS
    job["deadline_at_epoch"] = deadline
    job["deadline_seconds"] = int(RESEARCH_DEADLINE_SECONDS)
    return deadline


def _job_deadline_expired(job: dict[str, Any], *, now: Optional[float] = None) -> bool:
    current = time.time() if now is None else float(now)
    return current >= _job_deadline_epoch(job)


def _unique_warnings(*groups: Any) -> list[str]:
    result: list[str] = []
    for group in groups:
        values = group if isinstance(group, (list, tuple, set)) else [group]
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
    return result[:12]


def _public_classification_missing(job: dict[str, Any]) -> bool:
    return bool(
        _canonical_task_type(job.get("task_type")) == TASK_TYPE_PUBLIC_QUESTION
        and not list(job.get("subquestions") or [])
    )


def _complete_without_draft(
    job: dict[str, Any], *, warning: str, completion_reason: str
) -> dict[str, Any]:
    """Finish safely without inventing customer-facing text outside the model."""

    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    result = {
        "resposta": "",
        "contexto": {},
        "evidence_envelope": {},
        "evidence_status": [],
        "data_sufficient": False,
        "warnings": _unique_warnings(job.get("warnings"), [warning]),
        "requires_approval": False,
        "publish_attempted": False,
        "blocked_without_draft": True,
        "completion_reason": completion_reason,
    }
    job.update(
        {
            "status": "completed",
            "agent_state": "revisao_humana",
            "current_step": "revisar",
            "result": result,
            "warnings": list(result["warnings"]),
            "deadline_reached": True,
            "completed_with_partial": False,
            "blocked_without_draft": True,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "completed_at": _now(),
        }
    )
    job.pop("last_partial_result", None)
    job.pop("error", None)
    _cancel_retry_timer(str(job.get("job_id") or ""))
    return codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base, client_id, job
    )


def _retry_delay_seconds(retry_count: int, job_id: str = "") -> int:
    index = max(0, min(int(retry_count or 1) - 1, len(RETRY_DELAYS_SECONDS) - 1))
    base = RETRY_DELAYS_SECONDS[index]
    digest = hashlib.sha256(f"{job_id}|{retry_count}".encode("utf-8", "ignore")).digest()
    jitter = ((int.from_bytes(digest[:2], "big") / 65535.0) * 0.4) - 0.2
    return max(1, int(round(base * (1.0 + jitter))))


def _cancel_retry_timer(job_id: str) -> None:
    timer: Optional[threading.Timer]
    with _SCHEDULER_LOCK:
        timer = _RETRY_TIMERS.pop(str(job_id or ""), None)
    if timer is not None:
        timer.cancel()


def _schedule_retry_timer(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "")
    client_id = str(job.get("client_id") or "default")
    if not job_id or str(job.get("status") or "") != "waiting_retry" or job.get("cancel_requested"):
        return
    wake_at = min(
        float(job.get("next_retry_at_epoch") or 0.0),
        _job_deadline_epoch(job),
    )
    delay = max(0.1, wake_at - time.time())
    with _SCHEDULER_LOCK:
        previous = _RETRY_TIMERS.pop(job_id, None)
        if previous is not None:
            previous.cancel()
        timer = threading.Timer(delay, _wake_retry, args=(client_id, job_id))
        timer.daemon = True
        _RETRY_TIMERS[job_id] = timer
        timer.start()


def _wake_retry(client_id: str, job_id: str) -> None:
    with _SCHEDULER_LOCK:
        _RETRY_TIMERS.pop(str(job_id or ""), None)
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict) or job.get("cancel_requested"):
        return
    if str(job.get("status") or "") != "waiting_retry":
        return
    if _job_deadline_expired(job):
        _complete_with_best_available(job)
        return
    if float(job.get("next_retry_at_epoch") or 0.0) > time.time():
        _schedule_retry_timer(job)
        return
    job.update(
        {
            "status": "queued",
            "agent_state": "pesquisando",
            "current_step": "consultar",
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "retry_ready_at": _now(),
        }
    )
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    _schedule(saved)


def _research_history_entry(
    job: dict[str, Any],
    *,
    answer: str = "",
    context: Optional[dict[str, Any]] = None,
    matrix: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    error: str = "",
) -> dict[str, Any]:
    context = context if isinstance(context, dict) else {}
    diagnostic = _diagnostic_result(context)
    analysis = diagnostic.get("compatibility_analysis") if isinstance(diagnostic.get("compatibility_analysis"), dict) else {}
    queries = [
        str(item.get("query") or "").strip()[:300]
        for item in (analysis.get("queries") or [])
        if isinstance(item, dict) and str(item.get("query") or "").strip()
    ]
    sources = [str(item or "").strip()[:500] for item in (analysis.get("sources") or []) if str(item or "").strip()]
    return {
        "attempt": max(1, int(job.get("attempt_count") or 1)),
        "at": _now(),
        "decision": str(analysis.get("decision") or "insufficient")[:40],
        "confidence": float(analysis.get("confidence") or diagnostic.get("confidence") or 0.0),
        "missing_fields": [str(item or "")[:120] for item in (analysis.get("missing_fields") or [])[:12]],
        "queries": queries[:12],
        "sources": sources[:16],
        "evidence_status": list(matrix or [])[:8],
        "warnings": [str(item or "")[:300] for item in (warnings or [])[:12]],
        "answer": str(answer or "").strip()[:1200],
        "error": str(error or "").strip()[:1000],
    }


def _cancel_post_sale_job(job: dict[str, Any]) -> dict[str, Any]:
    """Cancel a legacy post-sale draft and remove every reusable suggestion."""

    current = dict(job or {})
    info_base = _runtime_info_base()
    client_id = str(current.get("client_id") or "default")
    current.pop("result", None)
    current.pop("last_partial_result", None)
    current.update(
        {
            "status": "cancelled",
            "cancel_requested": True,
            "agent_state": "cancelado",
            "current_step": "responder",
            "error": "pos_venda_somente_manual",
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "completed_at": _now(),
        }
    )
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base, client_id, current
    )
    plan_id = str(current.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                plan_id,
                "cancelado",
                current_step="responder",
                step_status="canceled",
                proposal={},
                details={"reason": "pos_venda_somente_manual"},
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao cancelar plano legado de pos-venda %s", plan_id)
    return saved


def _complete_with_best_available(
    job: dict[str, Any],
    *,
    answer: str = "",
    context: Optional[dict[str, Any]] = None,
    matrix: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Finish a bounded research job with the safest partial draft available."""

    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    job_id = str(job.get("job_id") or "")
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, job_id
    )
    current = dict(latest) if isinstance(latest, dict) else dict(job)
    if _canonical_task_type(current.get("task_type")) == TASK_TYPE_POST_SALE:
        return _cancel_post_sale_job(current)
    if current.get("cancel_requested"):
        current.update({"status": "cancelled", "agent_state": "cancelado", "current_step": "responder"})
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)
    if str(current.get("status") or "") == "completed" and isinstance(current.get("result"), dict):
        return current
    if _public_classification_missing(current):
        return _complete_without_draft(
            current,
            warning=(
                "Classificacao estruturada da IA indisponivel; nenhuma proposta de resposta foi gerada. "
                "Encaminhe a pergunta para revisao humana."
            ),
            completion_reason="ai_classification_unavailable",
        )

    partial = current.get("last_partial_result") if isinstance(current.get("last_partial_result"), dict) else {}
    final_answer = str(answer or partial.get("resposta") or "").strip()
    if not final_answer:
        for entry in reversed(list(current.get("research_history") or [])):
            if isinstance(entry, dict) and str(entry.get("answer") or "").strip():
                final_answer = str(entry.get("answer") or "").strip()
                break
    if not final_answer:
        return _complete_without_draft(
            current,
            warning=(
                "A IA nao produziu um rascunho valido dentro do prazo; nenhuma resposta local foi criada. "
                "Encaminhe a pergunta para revisao humana."
            ),
            completion_reason="ai_response_unavailable",
        )

    final_context = context if isinstance(context, dict) and context else partial.get("contexto")
    if not isinstance(final_context, dict):
        final_context = {}
    final_matrix = list(matrix or partial.get("evidence_status") or current.get("evidence_status") or [])
    final_warnings = _unique_warnings(
        warnings,
        partial.get("warnings"),
        current.get("warnings"),
        RESEARCH_DEADLINE_WARNING,
    )
    version = max(1, int(current.get("proposal_version") or 1))
    proposal_hash = _hash(
        {
            "job_id": job_id,
            "version": version,
            "store": current.get("store"),
            "subject": current.get("subject_key"),
            "answer": final_answer,
        }
    )
    diagnostic = _diagnostic_result(final_context)
    thread_id = str(
        final_context.get("codex_thread_id")
        or final_context.get("_codex_thread_id_result")
        or diagnostic.get("codex_thread_id")
        or current.get("thread_id")
        or ""
    )
    result = {
        "resposta": final_answer,
        "contexto": final_context,
        "model": str(
            final_context.get("model")
            or diagnostic.get("effective_model")
            or diagnostic.get("gemini_model")
            or ""
        ),
        "evidence_envelope": final_context.get("evidence_envelope") or {},
        "evidence_status": final_matrix,
        "data_sufficient": False,
        "warnings": final_warnings,
        "proposal_id": job_id,
        "proposal_version": version,
        "proposal_hash": proposal_hash,
        "requires_approval": True,
        "publish_attempted": False,
        "completed_with_partial": True,
        "completion_reason": "research_deadline_reached",
    }
    current.update(
        {
            "status": "completed",
            "agent_state": "aguardando_aprovacao",
            "current_step": "aprovar",
            "thread_id": thread_id,
            "evidence_status": final_matrix,
            "warnings": final_warnings,
            "result": result,
            "retry_policy": "bounded",
            "deadline_seconds": int(RESEARCH_DEADLINE_SECONDS),
            "deadline_at_epoch": _job_deadline_epoch(current),
            "deadline_reached": True,
            "completed_with_partial": True,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "completed_at": _now(),
        }
    )
    current.pop("error", None)
    history = list(current.get("agent_steps") or [])
    history.append(
        {
            "state": "aguardando_aprovacao",
            "step": "aprovar",
            "at": _now(),
            "message": "Limite de 3 minutos atingido; melhor rascunho disponivel enviado para revisao.",
        }
    )
    current["agent_steps"] = history[-60:]
    _cancel_retry_timer(job_id)
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base, client_id, current
    )
    plan_id = str(current.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                plan_id,
                "aguardando_aprovacao",
                current_step="aprovar",
                step_status="in_progress",
                proposal={
                    "proposal_id": job_id,
                    "version": version,
                    "proposal_hash": proposal_hash,
                    "action_id": "ml.pergunta_responder",
                    "channels_allowed": ["app", "whatsapp"],
                    "requires_confirmation": True,
                },
                details={
                    "data_sufficient": False,
                    "completed_with_partial": True,
                    "completion_reason": "research_deadline_reached",
                    "warnings": final_warnings,
                },
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao concluir plano parcial %s", plan_id)
    return saved


def _persist_retry(
    job: dict[str, Any],
    *,
    answer: str = "",
    context: Optional[dict[str, Any]] = None,
    matrix: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    error: str = "",
    immediate: bool = False,
) -> dict[str, Any]:
    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, str(job.get("job_id") or "")
    )
    current = dict(latest) if isinstance(latest, dict) else dict(job)
    if str(job.get("thread_id") or "").strip():
        current["thread_id"] = str(job.get("thread_id") or "").strip()
    current["attempt_count"] = max(
        int(current.get("attempt_count") or 0),
        int(job.get("attempt_count") or 0),
    )
    current["operational_failure_count"] = max(
        int(current.get("operational_failure_count") or 0),
        int(job.get("operational_failure_count") or 0),
    )
    if current.get("cancel_requested"):
        current.update({"status": "cancelled", "agent_state": "cancelado", "current_step": "responder"})
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)

    history = list(current.get("research_history") or [])
    history.append(
        _research_history_entry(
            job,
            answer=answer,
            context=context,
            matrix=matrix,
            warnings=warnings,
            error=error,
        )
    )
    retry_count = max(0, int(current.get("retry_count") or 0)) + 1
    partial_result = {
        "resposta": str(answer or "").strip(),
        "contexto": context if isinstance(context, dict) else {},
        "evidence_status": list(matrix or []),
        "data_sufficient": False,
        "warnings": list(warnings or []),
        "publish_attempted": False,
    }
    current.update(
        {
            "research_history": history[-RETRY_HISTORY_LIMIT:],
            "last_partial_result": partial_result,
            "evidence_status": list(matrix or []),
            "warnings": list(warnings or []),
            "retry_count": retry_count,
            "last_attempt_completed_at": _now(),
        }
    )
    deadline = _job_deadline_epoch(current)
    if _job_deadline_expired(current):
        return _complete_with_best_available(
            current,
            answer=answer,
            context=context,
            matrix=matrix,
            warnings=warnings,
        )
    delay = 0 if immediate else _retry_delay_seconds(retry_count, str(current.get("job_id") or ""))
    remaining = max(0.0, deadline - time.time())
    if not immediate:
        delay = min(delay, max(1, int(remaining)))
    current.update(
        {
            "status": "waiting_retry",
            "agent_state": "pesquisando",
            "current_step": "consultar",
            "retry_policy": "bounded",
            "retry_reason": str(error or "; ".join(warnings or []) or "evidencia_insuficiente")[:1000],
            "next_retry_at_epoch": time.time() + delay,
            "next_retry_delay_seconds": delay,
            "deadline_seconds": int(RESEARCH_DEADLINE_SECONDS),
            "deadline_at_epoch": deadline,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
        }
    )
    current.pop("result", None)
    current.pop("completed_at", None)
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)
    _schedule_retry_timer(saved)
    return saved


def _runtime_info_base() -> str:
    runtime = _require_runtime()
    base = str(getattr(runtime, "PASTA_INFO", "") or os.path.join(os.getcwd(), "info"))
    if not os.path.isabs(base):
        base = os.path.abspath(base)
    os.makedirs(base, exist_ok=True)
    return base


def _require_runtime() -> Any:
    if _RUNTIME is None:
        raise RuntimeError("Orquestrador de Perguntas/Pós-venda ainda não foi configurado.")
    return _RUNTIME


def _job_contract_current(job: Any) -> bool:
    return bool(
        isinstance(job, dict)
        and str(job.get("prompt_version") or "") == PROMPT_VERSION
        and str(job.get("schema_version") or "") == SCHEMA_VERSION
        and str(job.get("prompt_hash") or "") == PROMPT_HASH
    )


def _quarantine_outdated_job(
    job: dict[str, Any], *, client_id: Optional[str] = None
) -> dict[str, Any]:
    """Make a pre-current-contract job terminal and remove its actionable draft."""

    info_base = _runtime_info_base()
    scoped_client_id = str(client_id or job.get("client_id") or "default")
    previous_result = job.get("result") if isinstance(job.get("result"), dict) else {}
    warning = (
        "Tarefa criada com contrato de IA anterior e colocada em quarentena. "
        "Gere uma nova resposta antes de revisar ou aprovar."
    )
    result = {
        "resposta": "",
        "contexto": {},
        "evidence_status": [],
        "data_sufficient": False,
        "warnings": [warning],
        "requires_approval": False,
        "publish_attempted": False,
        "blocked_without_draft": True,
        "completion_reason": "contract_outdated",
    }
    job.update(
        {
            "status": "cancelled",
            "agent_state": "cancelado",
            "current_step": "revisar",
            "thread_id": "",
            "thread_reused": False,
            "thread_restart_reason": "contract_outdated",
            "idempotency_key": "",
            "contract_quarantined": True,
            "quarantined_result_hash": _hash(previous_result) if previous_result else "",
            "result": result,
            "warnings": [warning],
            "completed_with_partial": False,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "quarantined_at": _now(),
        }
    )
    job.pop("last_partial_result", None)
    job.pop("error", None)
    _cancel_retry_timer(str(job.get("job_id") or ""))
    return codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base, scoped_client_id, job
    )


def enabled() -> bool:
    return str(os.getenv("JK_CUSTOMER_REPLY_ORCHESTRATOR_ENABLED", "1")).strip().lower() not in {
        "0", "false", "no", "off",
    }


def configure_perguntas_pos_venda_codex_runtime(runtime: Any) -> None:
    global _RUNTIME, _RECOVERY_STARTED
    _RUNTIME = runtime
    if not enabled():
        return
    with _SCHEDULER_LOCK:
        if _RECOVERY_STARTED:
            return
        _RECOVERY_STARTED = True
    thread = threading.Thread(target=_recover_after_startup, name="ppv-codex-recovery", daemon=True)
    thread.start()


def _recover_after_startup() -> None:
    time.sleep(1.0)
    try:
        recover_pending_jobs()
    except Exception:
        logger.exception("[PPV CODEX] Falha ao recuperar jobs pendentes.")


def _subject_conversation_id(client_id: str, task_type: str, store: str, conversation_subject_key: str) -> str:
    canonical_type = _canonical_task_type(task_type)
    return conversation_key(
        f"mercado_livre_{canonical_type}",
        client_id,
        store,
        subject=conversation_subject_key,
    )


def _initial_subquestions(task_type: str) -> list[dict[str, Any]]:
    if _canonical_task_type(task_type) != TASK_TYPE_POST_SALE:
        return []
    return [{
        "id": "sq_1",
        "intent": "post_sale",
        "question": "Atendimento pós-venda",
        "required_evidence": "pedido, envio, conversa, reclamação ou regra de atendimento",
        "status": "pending",
    }]


def _ai_subquestions(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize only the structured subquestions returned by the AI pipeline."""

    context = context if isinstance(context, dict) else {}
    intent_context = context.get("intencao_atendimento")
    intent_context = intent_context if isinstance(intent_context, dict) else {}
    raw_subquestions = intent_context.get("subperguntas")
    if not isinstance(raw_subquestions, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_subquestions[:12]:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "").strip()
        if intent not in PUBLIC_SUBQUESTION_INTENTS:
            continue
        question = str(item.get("question") or "").strip()[:1200]
        required_evidence = str(item.get("required_evidence") or "").strip()[:1200]
        if not question or not required_evidence:
            continue
        marker = (intent, question, required_evidence)
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append({
            "id": f"sq_{len(normalized) + 1}",
            "intent": intent,
            "question": question,
            "required_evidence": required_evidence,
            "status": "pending",
        })
    return normalized


def _source_domains(values: list[Any]) -> set[str]:
    domains: set[str] = set()
    for value in values:
        url = str(value.get("url") if isinstance(value, dict) else value or "").strip()
        if not url:
            continue
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            host = ""
        if host:
            domains.add(host.removeprefix("www."))
    return domains


def _compatibility_evidence(analysis: dict[str, Any]) -> tuple[bool, list[str]]:
    evidence = analysis.get("evidence") if isinstance(analysis.get("evidence"), dict) else {}
    records: list[dict[str, Any]] = []
    for key in ("product", "target", "target_vehicle", "equivalence"):
        records.extend(item for item in (evidence.get(key) or []) if isinstance(item, dict))
    official_authorities = {
        "manufacturer", "official", "official_document", "oem", "oem_catalog", "technical_manual",
    }
    official = any(_normal(item.get("authority") or item.get("source_type")) in official_authorities for item in records)
    sources = list(analysis.get("sources") or []) + records
    independent = len(_source_domains(sources)) >= 2
    decision = str(analysis.get("decision") or "insufficient").strip().lower()
    sufficient = decision in {"yes", "no", "conditional"} and (official or independent)
    warnings: list[str] = []
    if decision == "insufficient":
        warnings.append("Compatibilidade sem conclusão técnica segura.")
    elif not (official or independent):
        warnings.append("Compatibilidade sem uma fonte oficial ou duas fontes independentes concordantes.")
    return sufficient, warnings


def _diagnostic_result(context: dict[str, Any]) -> dict[str, Any]:
    diagnostics = context.get("diagnostico_ia") if isinstance(context.get("diagnostico_ia"), list) else []
    first = diagnostics[0] if diagnostics and isinstance(diagnostics[0], dict) else {}
    return first.get("result") if isinstance(first.get("result"), dict) else {}


def _evidence_envelope(
    task_type: str,
    *,
    store: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Normalize evidence for every customer-reply intent, including legacy V1 contexts."""

    diagnostic = _diagnostic_result(context)
    existing = context.get("evidence_envelope")
    if not isinstance(existing, dict):
        existing = diagnostic.get("evidence_envelope")
    records = list(existing.get("records") or []) if isinstance(existing, dict) else []
    normalized_records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(field: str, value: Any, source: str, *, coverage: str = "confirmed") -> None:
        if value in (None, "", [], {}):
            return
        coverage_value = str(coverage or "").strip().lower()
        if coverage_value in {"confirmed", "canonical", "source", "generated_verified", "versioned_technical", "official"}:
            coverage_value = "confirmed"
        else:
            coverage_value = "partial"
        record = {
            "field": str(field or "context"),
            "value": value,
            "store": str(store or ""),
            "source": str(source or "runtime"),
            "coverage": coverage_value,
            "authority": coverage_value,
        }
        marker = _hash(record)
        if marker not in seen:
            seen.add(marker)
            normalized_records.append(record)

    for record in records:
        if not isinstance(record, dict):
            continue
        add(
            str(record.get("field") or "context"),
            record.get("value"),
            str(record.get("source") or record.get("reference") or "runtime"),
            coverage=str(record.get("coverage") or record.get("authority") or record.get("status") or "partial"),
        )

    compatibility = diagnostic.get("compatibility_analysis")
    if isinstance(compatibility, dict):
        for source in list(compatibility.get("sources") or [])[:12]:
            add("compatibility", source, "compatibility_analysis")

    canonical_type = _canonical_task_type(task_type)
    if canonical_type == TASK_TYPE_POST_SALE:
        for field, source in (
            ("pedido", "mercado_livre_order"),
            ("envio", "mercado_livre_shipping"),
            ("pagamento", "mercado_livre_payment"),
            ("nota_fiscal", "local_invoice_index"),
            ("reclamacao_mediacao", "mercado_livre_claim"),
            ("anuncios", "mercado_livre_listing"),
            ("perguntas_anteriores_anuncio", "mercado_livre_question_history"),
        ):
            add(field, context.get(field), source)
        add("mensagem_comprador", context.get("last_message_text"), "mercado_livre_messages")
    else:
        listing = context.get("item") or context.get("anuncio")
        listing = listing if isinstance(listing, dict) else {}
        for field, value in (
            ("pergunta", context.get("pergunta") or context.get("question")),
            ("anuncio", listing),
            ("historico_comprador", context.get("historico_comprador")),
            ("context_hub", context.get("context_hub")),
        ):
            add(
                field,
                value,
                "mercado_livre" if field != "context_hub" else "context_hub",
                coverage="partial",
            )
        add(
            "descricao_anuncio",
            context.get("descricao") or listing.get("description"),
            "mercado_livre_listing",
            coverage="partial",
        )
        add(
            "atributos_anuncio",
            context.get("atributos") or listing.get("attributes"),
            "mercado_livre_listing",
            coverage="partial",
        )
        add(
            "envio_anuncio",
            context.get("envio") or context.get("shipping") or listing.get("shipping"),
            "mercado_livre_shipping",
            coverage="partial",
        )
        add(
            "nota_fiscal",
            context.get("nota_fiscal") or context.get("invoice"),
            "store_invoice_policy",
            coverage="partial",
        )
        add(
            "other_product_search",
            context.get("busca_outra_peca"),
            "internal_registry_and_mercado_livre",
            coverage="partial",
        )

    coverage = "none"
    if normalized_records:
        coverage = (
            "full"
            if all(str(item.get("coverage") or "") == "confirmed" for item in normalized_records)
            else "partial"
        )
    existing_gaps = [str(item) for item in (existing.get("gaps") or []) if str(item).strip()] if isinstance(existing, dict) else []
    if isinstance(existing, dict) and (
        existing.get("evidence_sufficient") is False
        or existing.get("coverage_complete") is False
        or existing_gaps
    ):
        coverage = "partial" if normalized_records else "none"
    raw_envelope = {
        "schema_version": EVIDENCE_ENVELOPE_V2,
        "task_type": canonical_type,
        "status": "completed" if coverage == "full" else ("partial" if normalized_records else "missing"),
        "records": normalized_records[:48],
        "sources": list(dict.fromkeys(str(item.get("source") or "") for item in normalized_records if item.get("source"))),
        "gaps": [] if coverage == "full" else (existing_gaps or ["coverage_incomplete"]),
        "confidence": "high" if coverage == "full" else ("medium" if normalized_records else "unknown"),
        "evidence_sufficient": coverage == "full",
        "coverage_complete": coverage == "full",
        "scope": {"task_type": canonical_type, "store": str(store or "")},
    }
    return normalize_evidence_envelope(raw_envelope).to_dict()


def _intent_evidence_records(intent: str, envelope: dict[str, Any]) -> list[dict[str, Any]]:
    records = [item for item in (envelope.get("records") or []) if isinstance(item, dict)]
    prefixes = {
        "compatibility": ("compatibility",),
        "shipping": ("envio", "frete", "prazo_entrega", "shipping", "shipment"),
        "invoice": ("nota_fiscal", "fiscal", "invoice", "nfe", "danfe"),
        "stock": ("estoque", "stock", "inventory", "available_quantity"),
        "price": ("preco", "price"),
        "warranty": ("garantia", "warranty", "originalidade", "procedencia"),
        "warranty_originality": ("garantia", "warranty", "originalidade", "procedencia"),
        "post_sale": ("pedido", "envio", "pagamento", "nota_fiscal", "reclamacao", "mensagem"),
        "product_feature": ("atributos", "descricao", "description", "ficha_tecnica", "compatibility"),
        "other_product": ("other_product", "outra_peca", "busca_outra_peca", "cadastro_outra_peca"),
        "general": ("anuncio", "historico", "context_hub", "pedido", "mensagem"),
    }.get(intent, ())
    if not prefixes:
        return []
    matched = [
        item
        for item in records
        if str(item.get("field") or "").lower().startswith(prefixes)
    ]
    # Public-question evidence can arrive as one canonical listing snapshot.
    # Materialize only the nested fields that directly support the intent;
    # the generic existence of a listing is never sufficient by itself.
    nested_keys = {
        "shipping": ("shipping", "shipping_options", "shipment"),
        "invoice": ("invoice", "invoice_data", "fiscal_data", "nota_fiscal", "nfe"),
        "product_feature": ("attributes", "description", "descricao", "variations"),
        "other_product": ("other_product_search", "busca_outra_peca"),
    }.get(intent, ())
    for record in records:
        field = str(record.get("field") or "").strip().lower()
        value = record.get("value")
        if field not in {"anuncio", "item", "listing"} or not isinstance(value, dict):
            continue
        for key in nested_keys:
            nested = value.get(key)
            if nested in (None, "", [], {}):
                continue
            matched.append({**record, "field": f"{intent}_{key}", "value": nested})
        if intent == "invoice":
            fiscal_terms = []
            for term in [*(value.get("attributes") or []), *(value.get("sale_terms") or [])]:
                if not isinstance(term, dict):
                    continue
                marker = _normal(f"{term.get('id') or ''} {term.get('name') or ''}")
                if any(token in marker for token in ("invoice", "nota fiscal", "nfe", "danfe")):
                    fiscal_terms.append(term)
            if fiscal_terms:
                matched.append({**record, "field": "invoice_listing_terms", "value": fiscal_terms[:12]})
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in matched:
        if intent == "other_product":
            value = record.get("value")
            if not isinstance(value, dict):
                continue
            has_match = any(
                value.get(key) not in (None, "", [], {})
                for key in ("cadastro", "anuncios_ativos_ml", "matches", "items", "results")
            )
            authoritative_empty = bool(value.get("coverage_complete") is True and str(value.get("query") or "").strip())
            if not (has_match or authoritative_empty):
                continue
        marker = _hash({
            "field": record.get("field"),
            "value": record.get("value"),
            "store": record.get("store"),
            "source": record.get("source"),
            "coverage": record.get("coverage") or record.get("authority"),
        })
        if marker not in seen:
            seen.add(marker)
            unique.append(record)
    return unique


def _evidence_matrix(
    subquestions: list[dict[str, Any]],
    *,
    answer: str,
    context: dict[str, Any],
    envelope: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    diagnostic = _diagnostic_result(context)
    validation_ok = diagnostic.get("validation_ok") is True and bool(answer.strip())
    validation_issues = [str(item) for item in (diagnostic.get("validation_issues") or []) if str(item).strip()]
    analysis = diagnostic.get("compatibility_analysis") if isinstance(diagnostic.get("compatibility_analysis"), dict) else {}
    compatibility_ok, compatibility_warnings = _compatibility_evidence(analysis)
    normalized_envelope = envelope if isinstance(envelope, dict) else _evidence_envelope(
        TASK_TYPE_PUBLIC_QUESTION,
        store=str(context.get("store") or context.get("loja") or ""),
        context=context,
    )
    sources = list(analysis.get("sources") or [])
    matrix: list[dict[str, Any]] = []
    warnings = list(dict.fromkeys(validation_issues + compatibility_warnings))
    for item in subquestions:
        row = dict(item)
        intent = str(row.get("intent") or "general")
        if intent == "compatibility":
            confirmed = bool(validation_ok and compatibility_ok)
            row["status"] = "confirmed" if confirmed else ("partial" if answer.strip() else "no_evidence")
            row["confidence"] = float(analysis.get("confidence") or diagnostic.get("confidence") or 0.0)
            row["sources"] = sources[:12]
        else:
            intent_records = _intent_evidence_records(intent, normalized_envelope)
            records_confirmed = bool(intent_records) and all(
                str(record.get("coverage") or record.get("authority") or "") == "confirmed"
                for record in intent_records
            )
            confirmed = bool(
                validation_ok
                and records_confirmed
            )
            row["status"] = "confirmed" if confirmed else ("partial" if answer.strip() else "no_evidence")
            row["confidence"] = float(diagnostic.get("confidence") or (0.65 if validation_ok else 0.35))
            row["sources"] = [
                {
                    "field": record.get("field"),
                    "source": record.get("source"),
                    "store": record.get("store"),
                }
                for record in intent_records[:12]
            ]
        matrix.append(row)
    sufficient = bool(matrix) and all(str(item.get("status")) == "confirmed" for item in matrix)
    if not sufficient:
        warnings.append("O rascunho responde apenas os pontos sustentados pelas fontes e requer revisão humana.")
    return matrix, sufficient, list(dict.fromkeys(warnings))[:12]


def _public_job(job: dict[str, Any], *, queue_position: int = 0) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    blocked_without_draft = bool(
        result.get("blocked_without_draft")
        or job.get("blocked_without_draft")
        or job.get("contract_quarantined")
    )
    return {
        "success": str(job.get("status") or "") == "completed" and not blocked_without_draft,
        "queued": str(job.get("status") or "") in ACTIVE_STATUSES,
        "job_id": str(job.get("job_id") or ""),
        "profile": PROFILE,
        "task_type": str(job.get("task_type") or ""),
        "store": str(job.get("store") or ""),
        "subject_key": str(job.get("event_subject_key") or job.get("subject_key") or ""),
        "conversation_subject_key": str(job.get("subject_key") or ""),
        "conversation_id": str(job.get("conversation_id") or ""),
        "thread_id": str(job.get("thread_id") or ""),
        "thread_reused": bool(job.get("thread_reused")),
        "thread_restart_reason": str(job.get("thread_restart_reason") or ""),
        "prompt_version": str(job.get("prompt_version") or ""),
        "prompt_hash": str(job.get("prompt_hash") or ""),
        "schema_version": str(job.get("schema_version") or ""),
        "status": str(job.get("status") or "queued"),
        "agent_state": str(job.get("agent_state") or "entendendo"),
        "current_step": str(job.get("current_step") or ""),
        "agent_steps": list(job.get("agent_steps") or []),
        "subquestions": list(job.get("subquestions") or []),
        "evidence_status": list(result.get("evidence_status") or job.get("evidence_status") or []),
        "data_sufficient": bool(result.get("data_sufficient")) if result else False,
        "proposal_id": str(result.get("proposal_id") or ""),
        "proposal_version": int(result.get("proposal_version") or job.get("proposal_version") or 1),
        "proposal_hash": str(result.get("proposal_hash") or ""),
        "warnings": list(result.get("warnings") or job.get("warnings") or []),
        "result": result,
        "error": str(job.get("error") or ""),
        "retry_policy": str(job.get("retry_policy") or ""),
        "retry_count": max(0, int(job.get("retry_count") or 0)),
        "retry_reason": str(job.get("retry_reason") or ""),
        "next_retry_at_epoch": float(job.get("next_retry_at_epoch") or 0.0),
        "deadline_seconds": int(job.get("deadline_seconds") or RESEARCH_DEADLINE_SECONDS),
        "deadline_at_epoch": float(job.get("deadline_at_epoch") or 0.0),
        "deadline_reached": bool(job.get("deadline_reached")),
        "completed_with_partial": bool(result.get("completed_with_partial") or job.get("completed_with_partial")),
        "blocked_without_draft": blocked_without_draft,
        "contract_quarantined": bool(job.get("contract_quarantined")),
        "research_history": list(job.get("research_history") or [])[-RETRY_HISTORY_LIMIT:],
        "queue_position": max(0, int(queue_position or 0)),
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
    }


def _queue_position(info_base: str, client_id: str, job_id: str) -> int:
    queued = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
        info_base, client_id, statuses=list(ACTIVE_STATUSES), limit=500
    )
    for index, item in enumerate(queued, start=1):
        if str(item.get("job_id") or "") == str(job_id or ""):
            return index
    return 0


def create_job(
    *,
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: dict[str, Any],
    channel: str = "app",
    created_by: str = "module_user",
) -> dict[str, Any]:
    task_type = _canonical_task_type(task_type)
    if task_type not in TASK_TYPES:
        raise ValueError("Tipo de tarefa de atendimento inválido.")
    if task_type == TASK_TYPE_POST_SALE:
        raise PermissionError("Sugestoes do Black Jhon estao desativadas no pos-venda.")
    if not str(store or "").strip() or not str(subject_key or "").strip():
        raise ValueError("Loja e identificação da conversa são obrigatórias.")
    event_subject_key = str(subject_key or "").strip()
    conversation_subject_key, scope_verifiers = _conversation_subject_key(
        task_type,
        event_subject_key,
        request,
    )
    info_base = _runtime_info_base()
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
        info_base,
        client_id,
        task_type=task_type,
        store=str(store),
        subject_key=conversation_subject_key,
    )
    if not isinstance(latest, dict) and task_type == TASK_TYPE_PUBLIC_QUESTION:
        # V1 compatibility reader: old jobs used task_type=question and the event id as subject.
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
            info_base,
            client_id,
            task_type="question",
            store=str(store),
            subject_key=event_subject_key,
        )
    latest_result = latest.get("result") if isinstance(latest, dict) and isinstance(latest.get("result"), dict) else {}
    revision_requested = bool(
        str(request.get("resposta_atual") or request.get("orientacao_usuario") or "").strip()
    )
    request_hash = _hash(request)
    proposal_version = int(latest_result.get("proposal_version") or latest.get("proposal_version") or 0) + 1 if latest else 1
    if (
        not revision_requested
        and isinstance(latest, dict)
        and str(latest.get("status") or "") == "completed"
        and latest_result.get("data_sufficient") is True
        and str(latest.get("request_hash") or "") == request_hash
        and str(latest.get("prompt_version") or "") == PROMPT_VERSION
        and str(latest.get("schema_version") or "") == SCHEMA_VERSION
        and str(latest.get("prompt_hash") or "") == PROMPT_HASH
    ):
        return _public_job(latest)
    same_event = bool(
        isinstance(latest, dict)
        and str(latest.get("event_subject_key") or latest.get("subject_key") or "") == event_subject_key
    )
    if (
        isinstance(latest, dict)
        and str(latest.get("status") or "") in ACTIVE_STATUSES
        and same_event
        and str(latest.get("prompt_version") or "") == PROMPT_VERSION
        and str(latest.get("schema_version") or "") == SCHEMA_VERSION
        and str(latest.get("prompt_hash") or "") == PROMPT_HASH
    ):
        if revision_requested:
            latest_request = dict(latest.get("request") or {}) if isinstance(latest.get("request"), dict) else {}
            latest_request.update(dict(request or {}))
            latest.update(
                {
                    "request": latest_request,
                    "request_hash": _hash(latest_request),
                    "subquestions": _initial_subquestions(task_type),
                    "request_generation": max(1, int(latest.get("request_generation") or 1)) + 1,
                    "proposal_version": max(1, int(latest.get("proposal_version") or 1)) + 1,
                    "restart_requested": True,
                    "next_retry_at_epoch": 0.0,
                    "retry_reason": "orientacao_do_operador_atualizada",
                    "retry_count": 0,
                    "retry_policy": "bounded",
                    "deadline_seconds": int(RESEARCH_DEADLINE_SECONDS),
                    "deadline_at_epoch": time.time() + RESEARCH_DEADLINE_SECONDS,
                }
            )
            if str(latest.get("status") or "") == "waiting_retry":
                latest.update({"status": "queued", "agent_state": "pesquisando", "current_step": "consultar"})
                _cancel_retry_timer(str(latest.get("job_id") or ""))
            latest = codex_assistant_storage.codex_assistant_customer_reply_job_save(
                info_base, client_id, latest
            )
            _schedule(latest)
        elif _job_deadline_expired(latest):
            latest = _complete_with_best_available(latest)
        return _public_job(
            latest,
            queue_position=_queue_position(info_base, client_id, str(latest.get("job_id") or "")),
        )
    idempotency_key = _hash(
        {
            "client": client_id,
            "type": task_type,
            "store": store,
            "subject": event_subject_key,
            "request": request,
            "bucket": int(time.time() // 5),
        }
    )
    existing = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, idempotency_key=idempotency_key
    )
    if isinstance(existing, dict):
        if _job_contract_current(existing):
            return _public_job(existing, queue_position=_queue_position(info_base, client_id, str(existing.get("job_id") or "")))
        _quarantine_outdated_job(existing, client_id=client_id)
    job_id = uuid.uuid4().hex
    text = str(request.get("question_text") or request.get("last_message_text") or "").strip()
    if not text:
        question = request.get("pergunta") if isinstance(request.get("pergunta"), dict) else {}
        text = str(question.get("text") or "").strip()
    subquestions = _initial_subquestions(task_type)
    conversation_id = _subject_conversation_id(client_id, task_type, store, conversation_subject_key)
    guidance = codex_agent_runtime.resolve_guidance(
        info_base,
        client_id,
        context={
            "module": "pos_venda" if task_type == "post_sale" else "perguntas",
            "store": store,
            "sku": str(request.get("sku") or ""),
        },
    )
    plan = codex_agent_runtime.create_plan(
        info_base,
        client_id,
        task_id=job_id,
        conversation_id=conversation_id,
        conversation_generation=1,
        username=created_by,
        channel=channel,
        message=text or f"{task_type}:{event_subject_key}",
        mutable=True,
        idempotency_key=idempotency_key,
        guidance_applied=guidance,
    )
    thread_id, restart_reason, thread_reused = _thread_reuse_decision(
        latest,
        task_type=task_type,
        scope_verifiers=scope_verifiers,
    )
    job = {
        "job_id": job_id,
        "profile": PROFILE,
        "task_type": task_type,
        "subject_key": conversation_subject_key,
        "event_subject_key": event_subject_key,
        "scope_verifiers": scope_verifiers,
        "store": str(store),
        "client_id": str(client_id),
        "channel": str(channel or "app"),
        "created_by": str(created_by or "module_user"),
        "status": "queued",
        "agent_state": "entendendo",
        "current_step": "entender",
        "agent_steps": [{"state": "entendendo", "at": _now(), "message": "Solicitação recebida."}],
        "subquestions": subquestions,
        "request": dict(request or {}),
        "request_hash": request_hash,
        "thread_id": thread_id,
        "thread_reused": thread_reused,
        "thread_restart_reason": restart_reason,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "previous_job_id": str(latest.get("job_id") or "") if isinstance(latest, dict) else "",
        "plan_id": str(plan.get("plan_id") or ""),
        "proposal_version": max(1, proposal_version),
        "idempotency_key": idempotency_key,
        "guidance_applied": guidance,
        "cancel_requested": False,
        "request_generation": 1,
        "attempt_count": 0,
        "operational_failure_count": 0,
        "retry_count": 0,
        "retry_policy": "bounded",
        "deadline_seconds": int(RESEARCH_DEADLINE_SECONDS),
        "deadline_at_epoch": time.time() + RESEARCH_DEADLINE_SECONDS,
        "research_history": [],
        "created_at": _now(),
    }
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    codex_agent_runtime.audit(
        info_base,
        client_id,
        event_type="customer_reply_job_created",
        entity_type="job",
        entity_id=job_id,
        actor=created_by,
        channel=channel,
        payload={
            "task_type": task_type,
            "store": store,
            "event_subject_key": event_subject_key,
            "conversation_subject_key": conversation_subject_key,
            "conversation_id": conversation_id,
            "thread_reused": thread_reused,
            "thread_restart_reason": restart_reason,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "schema_version": SCHEMA_VERSION,
        },
    )
    _schedule(saved)
    return _public_job(saved, queue_position=_queue_position(info_base, client_id, job_id))


def get_job(client_id: str, job_id: str) -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
        return _public_job(_cancel_post_sale_job(job))
    if not _job_contract_current(job):
        job = _quarantine_outdated_job(job, client_id=client_id)
        return _public_job(job)
    if str(job.get("status") or "") == "waiting_retry" and _job_deadline_expired(job):
        job = _complete_with_best_available(job)
    return _public_job(job, queue_position=_queue_position(info_base, client_id, job_id))


def approval_job_current(client_id: str, job_id: str) -> bool:
    """Return whether a public draft belongs to the current AI contract and is approvable."""

    try:
        job = get_job(client_id, job_id)
    except Exception:
        return False
    result = job.get("result") if isinstance(job, dict) and isinstance(job.get("result"), dict) else {}
    return bool(
        isinstance(job, dict)
        and str(job.get("status") or "") == "completed"
        and str(job.get("agent_state") or "") == "aguardando_aprovacao"
        and not job.get("contract_quarantined")
        and not job.get("blocked_without_draft")
        and str(result.get("resposta") or "").strip()
        and result.get("requires_approval") is not False
    )


def job_contract_current(client_id: str, job_id: str) -> bool:
    """Check the current contract without requiring the job to be approval-ready."""

    try:
        job = get_job(client_id, job_id)
    except Exception:
        return False
    return bool(isinstance(job, dict) and not job.get("contract_quarantined"))


def latest_job_for_request(
    *,
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Return the latest job for the exact customer event without creating work.

    Public-question threads may span multiple questions from the same buyer and
    listing.  The persisted conversation key is therefore broader than the
    external question id, so the event id is checked again before a completed
    draft can be reconciled into the approval queue.
    """

    canonical_type = _canonical_task_type(task_type)
    if canonical_type not in TASK_TYPES:
        return None
    if canonical_type == TASK_TYPE_POST_SALE:
        return None
    event_subject_key = str(subject_key or "").strip()
    if not str(store or "").strip() or not event_subject_key:
        return None
    conversation_subject_key, _scope_verifiers = _conversation_subject_key(
        canonical_type,
        event_subject_key,
        request,
    )
    info_base = _runtime_info_base()
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
        info_base,
        client_id,
        task_type=canonical_type,
        store=str(store),
        subject_key=conversation_subject_key,
    )
    if not isinstance(latest, dict) and canonical_type == TASK_TYPE_PUBLIC_QUESTION:
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
            info_base,
            client_id,
            task_type="question",
            store=str(store),
            subject_key=event_subject_key,
        )
    if not isinstance(latest, dict):
        return None
    if not _job_contract_current(latest):
        _quarantine_outdated_job(latest, client_id=client_id)
        return None
    persisted_event_key = str(
        latest.get("event_subject_key") or latest.get("subject_key") or ""
    ).strip()
    if persisted_event_key != event_subject_key:
        return None
    return _public_job(
        latest,
        queue_position=_queue_position(info_base, client_id, str(latest.get("job_id") or "")),
    )


def resume_incomplete_job(client_id: str, job_id: str, reason: str = "evidencia_tecnica_insuficiente") -> Optional[dict[str, Any]]:
    """Resume a legacy job that was incorrectly completed without enough evidence."""

    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
        return _public_job(_cancel_post_sale_job(job))
    if not _job_contract_current(job):
        return _public_job(_quarantine_outdated_job(job, client_id=client_id))
    if job.get("cancel_requested") or str(job.get("status") or "") == "cancelled":
        return _public_job(job)
    if str(job.get("status") or "") in ACTIVE_STATUSES:
        if str(job.get("status") or "") == "waiting_retry":
            _schedule_retry_timer(job)
        else:
            _schedule(job)
        return _public_job(job, queue_position=_queue_position(info_base, client_id, job_id))
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("blocked_without_draft") or job.get("blocked_without_draft"):
        return _public_job(job)
    if str(job.get("status") or "") != "completed" or result.get("data_sufficient") is not False:
        return _public_job(job)
    resumed = _persist_retry(
        job,
        answer=str(result.get("resposta") or ""),
        context=result.get("contexto") if isinstance(result.get("contexto"), dict) else {},
        matrix=list(result.get("evidence_status") or job.get("evidence_status") or []),
        warnings=list(result.get("warnings") or job.get("warnings") or []),
        error=str(reason or "evidencia_tecnica_insuficiente"),
        immediate=True,
    )
    return _public_job(resumed, queue_position=_queue_position(info_base, client_id, job_id))


def wait_job(client_id: str, job_id: str, timeout: float = MAX_SECONDS) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, min(float(timeout or MAX_SECONDS), MAX_SECONDS))
    while time.monotonic() < deadline:
        job = get_job(client_id, job_id)
        if not isinstance(job, dict):
            raise KeyError(job_id)
        if str(job.get("status") or "") in TERMINAL_STATUSES:
            return job
        time.sleep(0.2)
    job = get_job(client_id, job_id)
    if isinstance(job, dict):
        return job
    raise KeyError(job_id)


def cancel_job(client_id: str, job_id: str) -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_request_cancel(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    _cancel_retry_timer(job_id)
    plan_id = str(job.get("plan_id") or "")
    if plan_id and str(job.get("status") or "") == "cancelled":
        try:
            codex_agent_runtime.transition_plan(
                info_base, client_id, plan_id, "cancelado", current_step="responder", step_status="canceled"
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao cancelar plano %s", plan_id)
    return _public_job(job)


def _store_slot(job: dict[str, Any]) -> str:
    return f"{job.get('client_id')}|{_normal(job.get('store'))}"


def _schedule(job: dict[str, Any]) -> bool:
    job_id = str(job.get("job_id") or "")
    store_slot = _store_slot(job)
    with _SCHEDULER_LOCK:
        if not job_id or job_id in _ACTIVE_JOBS:
            return False
        if len(_ACTIVE_JOBS) >= MAX_GLOBAL_JOBS or store_slot in _ACTIVE_STORES:
            return False
        _ACTIVE_JOBS.add(job_id)
        _ACTIVE_STORES.add(store_slot)
    thread = threading.Thread(
        target=_worker_entry,
        args=(str(job.get("client_id") or "default"), job_id, store_slot),
        name=f"ppv-codex-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return True


def _worker_entry(client_id: str, job_id: str, store_slot: str) -> None:
    try:
        _run_job(client_id, job_id)
    finally:
        with _SCHEDULER_LOCK:
            _ACTIVE_JOBS.discard(job_id)
            _ACTIVE_STORES.discard(store_slot)
        _dispatch_pending()


def _save_step(job: dict[str, Any], state: str, step: str, message: str) -> dict[str, Any]:
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        _runtime_info_base(),
        str(job.get("client_id") or "default"),
        str(job.get("job_id") or ""),
    )
    if (
        isinstance(latest, dict)
        and int(latest.get("request_generation") or 1) > int(job.get("request_generation") or 1)
    ):
        for key in (
            "request",
            "request_hash",
            "request_generation",
            "subquestions",
            "proposal_version",
            "restart_requested",
        ):
            job[key] = latest.get(key)
    job["agent_state"] = state
    job["current_step"] = step
    history = list(job.get("agent_steps") or [])
    history.append({"state": state, "step": step, "at": _now(), "message": str(message or "")[:500]})
    job["agent_steps"] = history[-60:]
    job["lease_owner"] = _WORKER_ID
    job["lease_expires_ts"] = time.time() + 60.0
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        _runtime_info_base(), str(job.get("client_id") or "default"), job
    )
    plan_id = str(job.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                _runtime_info_base(),
                str(job.get("client_id") or "default"),
                plan_id,
                state,
                current_step=step,
                step_status="in_progress",
                details={"job_id": job.get("job_id"), "message": message},
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao atualizar plano %s", plan_id)
    return saved


def _cancelled(job: dict[str, Any]) -> bool:
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        _runtime_info_base(), str(job.get("client_id") or "default"), str(job.get("job_id") or "")
    )
    return bool(isinstance(latest, dict) and latest.get("cancel_requested"))


def _heartbeat_loop(client_id: str, job_id: str, stop_event: threading.Event) -> None:
    while not stop_event.wait(15.0):
        try:
            renewed = codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
                _runtime_info_base(),
                client_id,
                job_id,
                owner=_WORKER_ID,
                lease_seconds=60.0,
            )
            if not isinstance(renewed, dict):
                return
        except Exception:
            logger.exception("[PPV CODEX] Falha ao renovar lease do job %s", job_id)


def _is_operational_failure(exc: BaseException) -> bool:
    """Classify failures that may unlock the optional provider fallback.

    Validation, missing evidence and application-contract errors can still be
    retried by the job, but they must never count as a Codex outage.
    """

    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    try:
        if int(status_code or 0) == 429 or int(status_code or 0) >= 500:
            return True
    except (TypeError, ValueError):
        pass
    class_name = exc.__class__.__name__.lower()
    message = str(exc or "").strip().lower()
    operational_markers = (
        "timeout",
        "timed out",
        "connection",
        "broken pipe",
        "rate limit",
        "too many requests",
        "service unavailable",
        "temporarily unavailable",
        "provider unavailable",
        "codex indispon",
        "respostaindisponivel",
    )
    return any(marker in class_name or marker in message for marker in operational_markers)


def _load_question_context(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = _require_runtime()
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    client_id = str(job.get("client_id") or "default")
    store = str(job.get("store") or "")
    question = dict(request.get("pergunta") or {}) if isinstance(request.get("pergunta"), dict) else {}
    question["_codex_thread_id"] = str(job.get("thread_id") or "")
    question["_codex_job_id"] = str(job.get("job_id") or "")
    question["_codex_conversation_key"] = str(job.get("conversation_id") or "")
    question["_codex_operational_failure_count"] = max(0, int(job.get("operational_failure_count") or 0))
    question["_codex_prompt_version"] = str(job.get("prompt_version") or PROMPT_VERSION)
    question["_codex_schema_version"] = str(job.get("schema_version") or SCHEMA_VERSION)
    question["_agent_subquestions"] = list(job.get("subquestions") or [])
    question["_research_attempt"] = max(1, int(job.get("attempt_count") or 0) + 1)
    question["_research_history"] = list(job.get("research_history") or [])[-6:]
    question["_force_external_research"] = bool(job.get("retry_count") or job.get("research_history"))
    question["_research_directive"] = (
        "Nao repita somente as consultas anteriores. Procure novas fontes oficiais, manuais, catalogos OEM, "
        "referencias cruzadas e especificacoes tecnicas para resolver os campos ainda sem evidencia."
        if question["_force_external_research"]
        else ""
    )
    if str(request.get("resposta_atual") or "").strip():
        question["_resposta_atual"] = str(request.get("resposta_atual") or "").strip()[:1200]
    if str(request.get("orientacao_usuario") or "").strip():
        question["_orientacao_usuario"] = str(request.get("orientacao_usuario") or "").strip()[:1200]
    cfg = runtime._obter_cfg_ml(client_id, store)
    item = dict(request.get("item") or {}) if isinstance(request.get("item"), dict) else {}
    item_id = str(question.get("item_id") or item.get("id") or "").strip()
    if item_id and not item:
        try:
            response, cfg = runtime._ml_api_request(
                client_id, store, cfg, "GET", f"https://api.mercadolibre.com/items/{item_id}", timeout=12
            )
            if response.status_code == 200:
                item = response.json() or {}
        except Exception as exc:
            logger.warning("[PPV CODEX] Falha ao atualizar anúncio %s: %s", item_id, exc)
    if not item:
        item = runtime._ml_api_item_com_oauth_tenant(client_id, item_id) or runtime._ml_api_item(item_id) or {}
    if isinstance(item, dict) and item and not runtime._ml_extrair_sku(item):
        item = runtime._ml_perguntas_completar_skus_itens(client_id, store, cfg, [item])[0]
    answer, _cfg, context = runtime._perguntas_ia_gerar_resposta(client_id, store, cfg, question, item or {})
    context = context if isinstance(context, dict) else {}
    context.setdefault("loja", store)
    context.setdefault("pergunta", {
        "id": question.get("id") or job.get("event_subject_key") or "",
        "item_id": question.get("item_id") or item_id,
        "buyer_id": question.get("buyer_id") or question.get("from_id") or "",
        "text": question.get("text") or "",
    })
    context.setdefault("item", item or {})
    history = question.get("history") if isinstance(question.get("history"), list) else []
    if history:
        context.setdefault("historico_comprador", history[-10:])
    return str(answer or "").strip(), context


def _load_post_sale_context(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = _require_runtime()
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    client_id = str(job.get("client_id") or "default")
    store = str(job.get("store") or "")
    pack_id = str(request.get("pack_id") or (job.get("scope_verifiers") or {}).get("pack_id") or "")
    order_id = str(request.get("order_id") or "")
    cfg = runtime._obter_cfg_ml(client_id, store)
    conversation, cfg = runtime._ml_pos_venda_montar_conversa_normalizada(
        client_id, store, cfg, pack_id, order_id
    )
    if request.get("buyer_id") and not conversation.get("buyer_id"):
        conversation["buyer_id"] = str(request.get("buyer_id") or "")
    conversation["_codex_thread_id"] = str(job.get("thread_id") or "")
    conversation["_codex_job_id"] = str(job.get("job_id") or "")
    conversation["_codex_conversation_key"] = str(job.get("conversation_id") or "")
    conversation["_codex_operational_failure_count"] = max(0, int(job.get("operational_failure_count") or 0))
    conversation["_codex_prompt_version"] = str(job.get("prompt_version") or PROMPT_VERSION)
    conversation["_codex_schema_version"] = str(job.get("schema_version") or SCHEMA_VERSION)
    conversation["_agent_subquestions"] = list(job.get("subquestions") or [])
    conversation["_research_attempt"] = max(1, int(job.get("attempt_count") or 0) + 1)
    conversation["_research_history"] = list(job.get("research_history") or [])[-6:]
    conversation["_force_external_research"] = bool(job.get("retry_count") or job.get("research_history"))
    if str(request.get("resposta_atual") or "").strip():
        conversation["_resposta_atual"] = str(request.get("resposta_atual") or "").strip()[:1200]
    if str(request.get("orientacao_usuario") or "").strip():
        conversation["_orientacao_usuario"] = str(request.get("orientacao_usuario") or "").strip()[:1200]
    conversation, cfg = runtime._ml_pos_venda_preparar_conversa_ia(client_id, store, cfg, conversation)
    max_chars = int(request.get("max_chars") or conversation.get("seller_max_message_length") or 350)
    result, _cfg = runtime._ml_pos_venda_executar_pipeline_ia(client_id, store, cfg, conversation, max_chars)
    context = result.get("contexto_ia") if isinstance(result.get("contexto_ia"), dict) else {}
    context = {
        **context,
        "model": result.get("model") or "",
        "validacao": result.get("validacao") or {},
        "decisao": result.get("decisao") or {},
        "codex_thread_id": conversation.get("_codex_thread_id_result") or conversation.get("_codex_thread_id") or "",
    }
    return str(result.get("resposta") or "").strip(), context


def _refresh_thread_from_previous_job(job: dict[str, Any]) -> dict[str, Any]:
    """Resolve a thread that was still running when a later event was queued."""

    if str(job.get("thread_id") or "").strip() or not str(job.get("previous_job_id") or "").strip():
        return job
    previous = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        _runtime_info_base(),
        str(job.get("client_id") or "default"),
        str(job.get("previous_job_id") or ""),
    )
    thread_id, reason, reused = _thread_reuse_decision(
        previous,
        task_type=str(job.get("task_type") or ""),
        scope_verifiers=(job.get("scope_verifiers") if isinstance(job.get("scope_verifiers"), dict) else {}),
    )
    job["thread_id"] = thread_id
    job["thread_reused"] = reused
    job["thread_restart_reason"] = reason
    return job


def _run_job(client_id: str, job_id: str) -> None:
    info_base = _runtime_info_base()
    claimed = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base, client_id, job_id, owner=_WORKER_ID, lease_seconds=60.0
    )
    if not isinstance(claimed, dict):
        return
    if _canonical_task_type(claimed.get("task_type")) == TASK_TYPE_POST_SALE:
        _cancel_post_sale_job(claimed)
        return
    if not _job_contract_current(claimed):
        _quarantine_outdated_job(claimed, client_id=client_id)
        return
    job = _refresh_thread_from_previous_job(claimed)
    if _job_deadline_expired(job):
        _complete_with_best_available(job)
        return
    attempt_generation = max(1, int(job.get("request_generation") or 1))
    job["attempt_count"] = max(0, int(job.get("attempt_count") or 0)) + 1
    job["restart_requested"] = False
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(client_id, job_id, heartbeat_stop),
        name=f"ppv-codex-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    started = time.monotonic()
    try:
        job = _save_step(job, "planejando", "entender", "Separando os assuntos da pergunta.")
        if _cancelled(job):
            raise InterruptedError("Tarefa cancelada pelo usuário.")
        job = _save_step(job, "consultando", "consultar", "Consultando anúncio, histórico, regras e fontes autorizadas.")
        if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
            answer, context = _load_post_sale_context(job)
        else:
            answer, context = _load_question_context(job)
            job["subquestions"] = _ai_subquestions(context)
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("O ciclo do agente excedeu 180 segundos.")
        if _cancelled(job):
            raise InterruptedError("Tarefa cancelada pelo usuário.")
        job = _save_step(job, "validando", "validar", "Validando suficiência e consistência das evidências.")
        envelope = _evidence_envelope(
            str(job.get("task_type") or ""),
            store=str(job.get("store") or ""),
            context=context,
        )
        context["evidence_envelope"] = envelope
        context["codex_conversation"] = {
            "conversation_id": str(job.get("conversation_id") or ""),
            "thread_reused": bool(job.get("thread_reused")),
            "thread_restart_reason": str(job.get("thread_restart_reason") or ""),
            "prompt_version": str(job.get("prompt_version") or PROMPT_VERSION),
            "prompt_hash": str(job.get("prompt_hash") or PROMPT_HASH),
            "schema_version": str(job.get("schema_version") or SCHEMA_VERSION),
            "operational_failure_count": max(0, int(job.get("operational_failure_count") or 0)),
        }
        matrix, sufficient, warnings = _evidence_matrix(
            list(job.get("subquestions") or []), answer=answer, context=context, envelope=envelope
        )
        missing_intents = [
            str(item.get("intent") or item.get("id") or "evidence")
            for item in matrix
            if str(item.get("status") or "") != "confirmed"
        ]
        envelope = normalize_evidence_envelope({
            **envelope,
            "status": "completed" if sufficient else ("partial" if envelope.get("records") else "missing"),
            "gaps": [] if sufficient else list(dict.fromkeys(missing_intents or ["coverage_incomplete"])),
            "confidence": "high" if sufficient else ("medium" if envelope.get("records") else "unknown"),
            "evidence_sufficient": sufficient,
            "coverage_complete": sufficient,
        }).to_dict()
        context["evidence_envelope"] = envelope
        thread_id = str(
            context.get("codex_thread_id")
            or context.get("_codex_thread_id_result")
            or _diagnostic_result(context).get("codex_thread_id")
            or job.get("thread_id")
            or ""
        )
        if thread_id:
            job["thread_id"] = thread_id
        version = max(1, int(job.get("proposal_version") or 1))
        proposal_hash = _hash(
            {
                "job_id": job_id,
                "version": version,
                "store": job.get("store"),
                "subject": job.get("event_subject_key") or job.get("subject_key"),
                "answer": answer,
            }
        )
        result = {
            "resposta": answer,
            "contexto": context,
            "model": str(
                context.get("model")
                or _diagnostic_result(context).get("effective_model")
                or _diagnostic_result(context).get("gemini_model")
                or ""
            ),
            "evidence_envelope": envelope,
            "evidence_status": matrix,
            "data_sufficient": sufficient,
            "warnings": warnings,
            "proposal_id": job_id,
            "proposal_version": version,
            "proposal_hash": proposal_hash,
            "requires_approval": True,
            "publish_attempted": False,
        }
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
        if (
            isinstance(latest, dict)
            and int(latest.get("request_generation") or 1) > attempt_generation
        ):
            _persist_retry(
                latest,
                answer=answer,
                context=context,
                matrix=matrix,
                warnings=[*warnings, "A orientacao do operador mudou durante a pesquisa; iniciando nova tentativa."],
                error="orientacao_do_operador_atualizada",
                immediate=True,
            )
            return
        if not sufficient:
            if _job_deadline_expired(job):
                _complete_with_best_available(
                    job,
                    answer=answer,
                    context=context,
                    matrix=matrix,
                    warnings=warnings,
                )
            else:
                _persist_retry(
                    job,
                    answer=answer,
                    context=context,
                    matrix=matrix,
                    warnings=warnings,
                    error="evidencia_tecnica_insuficiente",
                )
            return
        job.update(
            {
                "status": "completed",
                "agent_state": "aguardando_aprovacao",
                "current_step": "aprovar",
                "thread_id": thread_id,
                "evidence_status": matrix,
                "warnings": warnings,
                "result": result,
                "lease_owner": "",
                "lease_expires_ts": 0.0,
                "completed_at": _now(),
            }
        )
        history = list(job.get("agent_steps") or [])
        history.append(
            {
                "state": "aguardando_aprovacao",
                "step": "aprovar",
                "at": _now(),
                "message": "Rascunho concluído e aguardando aprovação humana.",
            }
        )
        job["agent_steps"] = history[-60:]
        codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
        plan_id = str(job.get("plan_id") or "")
        if plan_id:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                plan_id,
                "aguardando_aprovacao",
                current_step="aprovar",
                step_status="in_progress",
                proposal={
                    "proposal_id": job_id,
                    "version": version,
                    "proposal_hash": proposal_hash,
                    "action_id": "ml.pergunta_responder",
                    "channels_allowed": ["app", "whatsapp"],
                    "requires_confirmation": True,
                },
                details={"data_sufficient": sufficient, "warnings": warnings},
            )
    except InterruptedError as exc:
        job.update(
            {
                "status": "cancelled",
                "agent_state": "cancelado",
                "current_step": "responder",
                "error": str(exc),
                "lease_owner": "",
                "lease_expires_ts": 0.0,
            }
        )
        codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    except Exception as exc:
        logger.exception("[PPV CODEX] Falha no job %s", job_id)
        if _is_operational_failure(exc):
            job["operational_failure_count"] = (
                max(0, int(job.get("operational_failure_count") or 0)) + 1
            )
        retry_job = _persist_retry(
            job,
            error=str(exc)[:1200],
            warnings=[
                "A tentativa operacional falhou; o melhor rascunho disponivel sera usado ao atingir 3 minutos."
            ],
        )
        plan_id = str(job.get("plan_id") or "")
        if plan_id and str(retry_job.get("status") or "") != "completed":
            try:
                codex_agent_runtime.transition_plan(
                    info_base,
                    client_id,
                    plan_id,
                    "consultando",
                    current_step="consultar",
                    step_status="in_progress",
                    verification={"status": "retry", "confirmed": False, "error": str(exc)[:1000]},
                )
            except Exception:
                logger.exception("[PPV CODEX] Falha ao registrar nova tentativa no plano.")
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)


def _known_clients(info_base: str) -> list[str]:
    clients: list[str] = []
    try:
        for entry in Path(info_base).iterdir():
            if entry.is_dir() and (entry / "codex_assistant" / "codex_assistant.db").exists():
                clients.append(entry.name)
    except Exception:
        return []
    return clients


def recover_pending_jobs() -> None:
    info_base = _runtime_info_base()
    for client_id in _known_clients(info_base):
        jobs = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
            info_base, client_id, statuses=list(ACTIVE_STATUSES | {"failed"}), limit=500
        )
        for job in jobs:
            if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
                _cancel_post_sale_job(job)
                continue
            if job.get("cancel_requested"):
                continue
            if not _job_contract_current(job):
                _quarantine_outdated_job(job, client_id=client_id)
                continue
            if str(job.get("status") or "") == "failed":
                job.update({"status": "waiting_retry", "next_retry_at_epoch": 0.0})
                job = codex_assistant_storage.codex_assistant_customer_reply_job_save(
                    info_base, client_id, job
                )
            if str(job.get("status") or "") == "waiting_retry":
                if float(job.get("next_retry_at_epoch") or 0.0) <= time.time():
                    _wake_retry(client_id, str(job.get("job_id") or ""))
                else:
                    _schedule_retry_timer(job)
                continue
            if str(job.get("status") or "") == "running" and float(job.get("lease_expires_ts") or 0.0) > time.time():
                continue
            _schedule(job)


def _dispatch_pending() -> None:
    try:
        recover_pending_jobs()
    except Exception:
        logger.exception("[PPV CODEX] Falha ao despachar fila pendente.")


def approve_or_refresh_proposal(
    *,
    client_id: str,
    proposal_id: str,
    proposal_version: int,
    proposal_hash: str,
    answer: str,
    store: str,
    subject_key: str,
) -> dict[str, Any]:
    """Validate a generated draft; edited text becomes a new confirmed version."""

    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, proposal_id)
    if not isinstance(job, dict):
        raise KeyError(proposal_id)
    if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
        _cancel_post_sale_job(job)
        raise PermissionError("Sugestoes do Black Jhon estao desativadas no pos-venda.")
    if not _job_contract_current(job):
        _quarantine_outdated_job(job, client_id=client_id)
        raise ValueError("A proposta usa um contrato de IA anterior. Gere uma nova resposta antes de aprovar.")
    event_subject_key = str(job.get("event_subject_key") or job.get("subject_key") or "")
    if str(job.get("store") or "") != str(store or "") or event_subject_key != str(subject_key or ""):
        raise PermissionError("A proposta não pertence a esta loja ou conversa.")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    if result.get("blocked_without_draft") or job.get("blocked_without_draft"):
        raise ValueError("A tarefa nao possui proposta de resposta aprovavel. Gere uma nova resposta.")
    current_answer = str(result.get("resposta") or "").strip()
    current_version = max(1, int(result.get("proposal_version") or job.get("proposal_version") or 1))
    current_hash = str(result.get("proposal_hash") or "")
    requested_answer = str(answer or "").strip()
    unchanged = requested_answer == current_answer
    if unchanged and int(proposal_version or 0) not in {0, current_version}:
        raise ValueError("A proposta foi atualizada. Revise a versão mais recente antes de aprovar.")
    if unchanged and proposal_hash and proposal_hash != current_hash:
        raise ValueError("A proposta foi alterada. Gere ou revise novamente antes de aprovar.")
    if not unchanged:
        current_version += 1
        current_hash = _hash(
            {
                "job_id": job.get("job_id"),
                "version": current_version,
                "store": store,
                "subject": subject_key,
                "answer": requested_answer,
            }
        )
        result.update(
            {
                "resposta": requested_answer,
                "proposal_version": current_version,
                "proposal_hash": current_hash,
                "revised_by_operator": True,
            }
        )
    result.update({"approved": True, "approved_at": _now()})
    job.update(
        {
            "proposal_version": current_version,
            "result": result,
            "agent_state": "executando",
            "current_step": "executar",
        }
    )
    codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    return {"job": job, "proposal_version": current_version, "proposal_hash": current_hash}


def mark_verified(
    *,
    client_id: str,
    job_id: str,
    success: bool,
    evidence: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    verification = {
        "status": "confirmed" if success else "failed",
        "confirmed": bool(success),
        "verified_at": _now(),
        "evidence": codex_agent_runtime.redact_sensitive(evidence or {}),
    }
    job.update(
        {
            "agent_state": "concluido" if success else "falhou",
            "current_step": "verificar",
            "verification": verification,
        }
    )
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    plan_id = str(job.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                plan_id,
                "concluido" if success else "falhou",
                current_step="verificar",
                step_status="completed" if success else "failed",
                verification=verification,
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao persistir verificação do job %s", job_id)
    return _public_job(saved)


def mark_rejected(*, client_id: str, job_id: str, reason: str = "rejeitada_pelo_operador") -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    verification = {
        "status": "rejected",
        "confirmed": False,
        "reason": str(reason or "rejeitada_pelo_operador")[:500],
        "verified_at": _now(),
    }
    _cancel_retry_timer(job_id)
    job.update({
        "status": "cancelled",
        "cancel_requested": True,
        "agent_state": "cancelado",
        "current_step": "aprovar",
        "verification": verification,
        "lease_owner": "",
        "lease_expires_ts": 0.0,
    })
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, job)
    plan_id = str(job.get("plan_id") or "")
    if plan_id:
        try:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                plan_id,
                "cancelado",
                current_step="aprovar",
                step_status="canceled",
                verification=verification,
            )
        except Exception:
            logger.exception("[PPV CODEX] Falha ao registrar rejeicao do job %s", job_id)
    return _public_job(saved)


__all__ = [
    "PROFILE",
    "configure_perguntas_pos_venda_codex_runtime",
    "enabled",
    "create_job",
    "get_job",
    "approval_job_current",
    "job_contract_current",
    "latest_job_for_request",
    "resume_incomplete_job",
    "wait_job",
    "cancel_job",
    "recover_pending_jobs",
    "approve_or_refresh_proposal",
    "mark_verified",
    "mark_rejected",
]
