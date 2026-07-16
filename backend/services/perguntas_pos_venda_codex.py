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


PROFILE = "mercado_livre_customer_reply"
TASK_TYPES = {"question", "post_sale"}
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
    raw = str(job.get("created_at") or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return float(parsed.timestamp())
        return float(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0.0


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


def _fallback_partial_answer(job: dict[str, Any]) -> str:
    if str(job.get("task_type") or "") == "post_sale":
        return (
            "Com as informacoes disponiveis, ainda nao foi possivel confirmar todos os detalhes do caso. "
            "Revise o historico e complemente a resposta antes de enviar."
        )
    return (
        "Com as informacoes disponiveis, ainda nao foi possivel confirmar a compatibilidade com seguranca. "
        "Confirme o codigo da peca, o chassi ou envie uma foto da etiqueta antes da compra."
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
    if current.get("cancel_requested"):
        current.update({"status": "cancelled", "agent_state": "cancelado", "current_step": "responder"})
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)
    if str(current.get("status") or "") == "completed" and isinstance(current.get("result"), dict):
        return current

    partial = current.get("last_partial_result") if isinstance(current.get("last_partial_result"), dict) else {}
    final_answer = str(answer or partial.get("resposta") or "").strip()
    if not final_answer:
        for entry in reversed(list(current.get("research_history") or [])):
            if isinstance(entry, dict) and str(entry.get("answer") or "").strip():
                final_answer = str(entry.get("answer") or "").strip()
                break
    if not final_answer:
        final_answer = _fallback_partial_answer(current)

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
        "model": str(final_context.get("model") or diagnostic.get("gemini_model") or ""),
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
                    "action_id": "ml.pergunta_responder" if current.get("task_type") == "question" else "ml.pos_venda_responder",
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


def _subject_conversation_id(client_id: str, task_type: str, store: str, subject_key: str) -> str:
    digest = _hash({"client": client_id, "type": task_type, "store": store, "subject": subject_key})[:28]
    return f"mlcr_{digest}"


def _subquestions(text: str, task_type: str) -> list[dict[str, Any]]:
    normalized = _normal(text)
    found: list[tuple[str, str]] = []

    def add(intent: str, evidence: str) -> None:
        if intent not in {item[0] for item in found}:
            found.append((intent, evidence))

    if task_type == "post_sale":
        add("post_sale", "pedido, envio, conversa, reclamação ou regra de atendimento")
    if re.search(r"\b(serve|servir|compativ|aplica|encaix|motor|modelo|ano|manual|automatic|cambio|furacao|estria|conector)\b", normalized):
        add("compatibility", "uma fonte oficial/fabricante ou duas fontes técnicas independentes concordantes")
    if re.search(r"\b(frete|entrega|prazo|envio|chega|data prevista|antes da data)\b", normalized):
        add("shipping", "prazo ou modalidade retornada pelo Mercado Livre; nunca promessa informal")
    if re.search(r"\b(estoque|disponivel|pronta entrega|tem quant)\b", normalized):
        add("stock", "estoque comum atual da Bling ou Full exclusivamente do Mercado Livre")
    if re.search(r"\b(preco|valor|desconto|quanto custa|faz por)\b", normalized):
        add("price", "preço vigente do anúncio")
    if re.search(r"\b(nota fiscal|danfe|\bnf\b)\b", normalized):
        add("invoice", "regra fiscal cadastrada para a loja")
    if re.search(r"\b(garantia|original|genuino|paralelo|procedencia)\b", normalized):
        add("warranty_originality", "descrição, atributos ou regra oficial confirmada")
    if re.search(r"\b(medida|tamanho|material|lado|voltagem|acompanha|inclui|quantas|cor|rosca|diametro)\b", normalized):
        add("product_feature", "descrição e atributos do anúncio ou ficha técnica confiável")
    if not found:
        add("general", "anúncio, histórico e orientações salvas")
    return [
        {
            "id": f"sq_{index + 1}",
            "intent": intent,
            "question": str(text or "").strip()[:1200],
            "required_evidence": evidence,
            "status": "pending",
        }
        for index, (intent, evidence) in enumerate(found)
    ]


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


def _evidence_matrix(
    subquestions: list[dict[str, Any]],
    *,
    answer: str,
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, list[str]]:
    diagnostic = _diagnostic_result(context)
    validation_ok = diagnostic.get("validation_ok") is not False and bool(answer.strip())
    validation_issues = [str(item) for item in (diagnostic.get("validation_issues") or []) if str(item).strip()]
    analysis = diagnostic.get("compatibility_analysis") if isinstance(diagnostic.get("compatibility_analysis"), dict) else {}
    compatibility_ok, compatibility_warnings = _compatibility_evidence(analysis)
    sources = list(analysis.get("sources") or [])
    matrix: list[dict[str, Any]] = []
    warnings = list(dict.fromkeys(validation_issues + compatibility_warnings))
    for item in subquestions:
        row = dict(item)
        intent = str(row.get("intent") or "general")
        if intent == "compatibility":
            confirmed = compatibility_ok
            row["status"] = "confirmed" if confirmed else ("partial" if answer.strip() else "no_evidence")
            row["confidence"] = float(analysis.get("confidence") or diagnostic.get("confidence") or 0.0)
            row["sources"] = sources[:12]
        else:
            row["status"] = "confirmed" if validation_ok else ("partial" if answer.strip() else "no_evidence")
            row["confidence"] = float(diagnostic.get("confidence") or (0.65 if validation_ok else 0.35))
            row["sources"] = []
        matrix.append(row)
    sufficient = bool(matrix) and all(str(item.get("status")) == "confirmed" for item in matrix)
    if not sufficient:
        warnings.append("O rascunho responde apenas os pontos sustentados pelas fontes e requer revisão humana.")
    return matrix, sufficient, list(dict.fromkeys(warnings))[:12]


def _public_job(job: dict[str, Any], *, queue_position: int = 0) -> dict[str, Any]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return {
        "success": str(job.get("status") or "") == "completed",
        "queued": str(job.get("status") or "") in ACTIVE_STATUSES,
        "job_id": str(job.get("job_id") or ""),
        "profile": PROFILE,
        "task_type": str(job.get("task_type") or ""),
        "store": str(job.get("store") or ""),
        "subject_key": str(job.get("subject_key") or ""),
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
    if task_type not in TASK_TYPES:
        raise ValueError("Tipo de tarefa de atendimento inválido.")
    if not str(store or "").strip() or not str(subject_key or "").strip():
        raise ValueError("Loja e identificação da conversa são obrigatórias.")
    info_base = _runtime_info_base()
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
        info_base,
        client_id,
        task_type=task_type,
        store=str(store),
        subject_key=str(subject_key),
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
    ):
        return _public_job(latest)
    if isinstance(latest, dict) and str(latest.get("status") or "") in ACTIVE_STATUSES:
        if revision_requested:
            latest_request = dict(latest.get("request") or {}) if isinstance(latest.get("request"), dict) else {}
            latest_request.update(dict(request or {}))
            latest.update(
                {
                    "request": latest_request,
                    "request_hash": _hash(latest_request),
                    "subquestions": _subquestions(
                        str(
                            request.get("question_text")
                            or ((request.get("pergunta") or {}).get("text") if isinstance(request.get("pergunta"), dict) else "")
                            or latest_request.get("question_text")
                            or ""
                        ),
                        task_type,
                    ),
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
            "subject": subject_key,
            "request": request,
            "bucket": int(time.time() // 5),
        }
    )
    existing = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, idempotency_key=idempotency_key
    )
    if isinstance(existing, dict):
        return _public_job(existing, queue_position=_queue_position(info_base, client_id, str(existing.get("job_id") or "")))
    job_id = uuid.uuid4().hex
    text = str(request.get("question_text") or request.get("last_message_text") or "").strip()
    if not text:
        question = request.get("pergunta") if isinstance(request.get("pergunta"), dict) else {}
        text = str(question.get("text") or "").strip()
    subquestions = _subquestions(text, task_type)
    conversation_id = _subject_conversation_id(client_id, task_type, store, subject_key)
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
        message=text or f"{task_type}:{subject_key}",
        mutable=True,
        idempotency_key=idempotency_key,
        guidance_applied=guidance,
    )
    thread_id = str(latest.get("thread_id") or "") if isinstance(latest, dict) else ""
    job = {
        "job_id": job_id,
        "profile": PROFILE,
        "task_type": task_type,
        "subject_key": str(subject_key),
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
        "conversation_id": conversation_id,
        "plan_id": str(plan.get("plan_id") or ""),
        "proposal_version": max(1, proposal_version),
        "idempotency_key": idempotency_key,
        "guidance_applied": guidance,
        "cancel_requested": False,
        "request_generation": 1,
        "attempt_count": 0,
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
        payload={"task_type": task_type, "store": store, "subject_key": subject_key},
    )
    _schedule(saved)
    return _public_job(saved, queue_position=_queue_position(info_base, client_id, job_id))


def get_job(client_id: str, job_id: str) -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if str(job.get("status") or "") == "waiting_retry" and _job_deadline_expired(job):
        job = _complete_with_best_available(job)
    return _public_job(job, queue_position=_queue_position(info_base, client_id, job_id))


def resume_incomplete_job(client_id: str, job_id: str, reason: str = "evidencia_tecnica_insuficiente") -> Optional[dict[str, Any]]:
    """Resume a legacy job that was incorrectly completed without enough evidence."""

    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if job.get("cancel_requested") or str(job.get("status") or "") == "cancelled":
        return _public_job(job)
    if str(job.get("status") or "") in ACTIVE_STATUSES:
        if str(job.get("status") or "") == "waiting_retry":
            _schedule_retry_timer(job)
        else:
            _schedule(job)
        return _public_job(job, queue_position=_queue_position(info_base, client_id, job_id))
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
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
        if str(job.get("status") or "") in TERMINAL_STATUSES | {"waiting_retry"}:
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


def _load_question_context(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = _require_runtime()
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    client_id = str(job.get("client_id") or "default")
    store = str(job.get("store") or "")
    question = dict(request.get("pergunta") or {}) if isinstance(request.get("pergunta"), dict) else {}
    question["_codex_thread_id"] = str(job.get("thread_id") or "")
    question["_codex_job_id"] = str(job.get("job_id") or "")
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
    return str(answer or "").strip(), context if isinstance(context, dict) else {}


def _load_post_sale_context(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = _require_runtime()
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    client_id = str(job.get("client_id") or "default")
    store = str(job.get("store") or "")
    pack_id = str(request.get("pack_id") or job.get("subject_key") or "")
    order_id = str(request.get("order_id") or "")
    cfg = runtime._obter_cfg_ml(client_id, store)
    conversation, cfg = runtime._ml_pos_venda_montar_conversa_normalizada(
        client_id, store, cfg, pack_id, order_id
    )
    if request.get("buyer_id") and not conversation.get("buyer_id"):
        conversation["buyer_id"] = str(request.get("buyer_id") or "")
    conversation["_codex_thread_id"] = str(job.get("thread_id") or "")
    conversation["_codex_job_id"] = str(job.get("job_id") or "")
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


def _run_job(client_id: str, job_id: str) -> None:
    info_base = _runtime_info_base()
    claimed = codex_assistant_storage.codex_assistant_customer_reply_job_claim(
        info_base, client_id, job_id, owner=_WORKER_ID, lease_seconds=60.0
    )
    if not isinstance(claimed, dict):
        return
    job = claimed
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
        if str(job.get("task_type") or "") == "post_sale":
            answer, context = _load_post_sale_context(job)
        else:
            answer, context = _load_question_context(job)
        if time.monotonic() - started > MAX_SECONDS:
            raise TimeoutError("O ciclo do agente excedeu 180 segundos.")
        if _cancelled(job):
            raise InterruptedError("Tarefa cancelada pelo usuário.")
        job = _save_step(job, "validando", "validar", "Validando suficiência e consistência das evidências.")
        matrix, sufficient, warnings = _evidence_matrix(
            list(job.get("subquestions") or []), answer=answer, context=context
        )
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
                "subject": job.get("subject_key"),
                "answer": answer,
            }
        )
        result = {
            "resposta": answer,
            "contexto": context,
            "model": str(context.get("model") or _diagnostic_result(context).get("gemini_model") or ""),
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
                    "action_id": "ml.pergunta_responder" if job.get("task_type") == "question" else "ml.pos_venda_responder",
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
        retry_job = _persist_retry(
            job,
            error=str(exc)[:1200],
            warnings=["A tentativa falhou; o melhor rascunho disponivel sera usado ao atingir 3 minutos."],
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
            if job.get("cancel_requested"):
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
    if str(job.get("store") or "") != str(store or "") or str(job.get("subject_key") or "") != str(subject_key or ""):
        raise PermissionError("A proposta não pertence a esta loja ou conversa.")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
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
    "resume_incomplete_job",
    "wait_job",
    "cancel_job",
    "recover_pending_jobs",
    "approve_or_refresh_proposal",
    "mark_verified",
    "mark_rejected",
]
