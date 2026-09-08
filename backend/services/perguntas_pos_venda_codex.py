"""Persistent Codex orchestration for Mercado Livre customer replies.

This service owns operational jobs and technical Codex threads.  It deliberately
does not expose shell/file tools: all business context is collected by the
existing Perguntas/Pós-venda read-only adapters injected at application startup.
"""

from __future__ import annotations

import contextlib
import errno
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
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from requests import exceptions as requests_exceptions

from backend.services import codex_agent_runtime, codex_assistant_storage
from backend.services.codex.storage import customer_replies as customer_reply_storage
from backend.services.codex_turn_context import (
    EVIDENCE_ENVELOPE_V2,
    conversation_key,
    normalize_evidence_envelope,
)
from backend.modules.perguntas_pos_venda.ai.contracts import PerguntasIARespostaPoliticaInvalida
from backend.modules.perguntas_pos_venda.ai.deep_research import (
    load_product_research_evidence,
    load_verified_product_evidence,
)
from backend.services.vehicle_identity import empty_vehicle_identity
from backend.services.vehicle_identity_vpic import VpicPublicVinDecoder
from backend.services.vin_transient import (
    DEFAULT_VIN_ENVELOPE_STORE,
    VIN_MARKER,
    capture_vin_payload,
    contains_vin_like_identifier,
)
from backend.services.perguntas_pos_venda_state import (
    PerguntasIAClassificacaoInconclusiva,
    PerguntasIAProviderIndisponivel,
    PerguntasIASegurancaBloqueada,
    _perguntas_ia_assunto_atual_autossuficiente,
    _perguntas_ia_continuation_facts,
    _perguntas_ia_continuation_has_negated_qualifier,
    _perguntas_ia_continuation_negated_identity,
    _perguntas_ia_current_facts_explicit,
    _perguntas_ia_mensagem_pos_venda_evidente,
    _perguntas_ia_prompt_injection_evidente,
    _perguntas_ia_texto_autoritativo_atual,
    _perguntas_ia_turno_continuacao_positiva,
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
    "installation_location",
    "other_product",
    "general",
    "post_sale",
})
PROMPT_VERSION = "jk_ml_customer_reply_codex_v18"
SCHEMA_VERSION = "5.2"
QUEUE_POLICY_VERSION = "jk_ppv_queue_v3"
VEHICLE_IDENTITY_POLICY = "jk_public_vin_decode_v1"
PRODUCT_EVIDENCE_POLICY = "jk_product_evidence_v2"
TECHNICAL_QUESTION_PLAN_VERSION = "jk_ml_technical_question_plan_v1"
TECHNICAL_EVIDENCE_GRAPH_VERSION = "jk_ml_evidence_graph_v2"
TECHNICAL_RESOLUTION_VERSION = "jk_ml_technical_resolution_v1"
FACTUAL_REVIEW_VERSION = "jk_ml_factual_review_v1"
FACTUAL_CRITIC_POLICY = "jk_black_jhon_factual_critic_v1"
PRODUCT_DOCUMENT_VISION_POLICY = "jk_product_document_vision_v1"
PUBLIC_RESEARCH_POLICY = "jk_black_jhon_research_v3"
PROMPT_HASH = hashlib.sha256(
    (
        "codex-native|public-question-by-item-buyer|post-sale-by-pack|"
        "evidence-envelope-v3|bounded-public-research|ai-only-subquestions|"
        "classification-contract-v3|continuity-repair-v1|typed-provider-failures|"
        "contextual-fallback-v1|response-policy-v8|compatibility-coverage-advisory-v1|"
        "compatibility-interface-evidence|seller-conversion-v1|seller-profile-v2|priority-queue-v3|"
        "public-technical-research-sol-high-v1|public-research-resilience-v2|"
        f"vehicle-identity-policy:{VEHICLE_IDENTITY_POLICY}|"
        f"product-evidence-policy:{PRODUCT_EVIDENCE_POLICY}|"
        "product-evidence-editorial-candidates-v1|black-jhon-factual-discretion-v1|"
        f"technical-question-plan:{TECHNICAL_QUESTION_PLAN_VERSION}|"
        f"technical-evidence-graph:{TECHNICAL_EVIDENCE_GRAPH_VERSION}|"
        f"technical-resolution:{TECHNICAL_RESOLUTION_VERSION}|"
        f"factual-review:{FACTUAL_REVIEW_VERSION}|"
        f"factual-critic:{FACTUAL_CRITIC_POLICY}|"
        f"document-vision:{PRODUCT_DOCUMENT_VISION_POLICY}|"
        f"public-research-policy:{PUBLIC_RESEARCH_POLICY}|"
        "store-sku-binding:jk_context_store_sku_binding_v1|"
        "store-sku-question-context:jk_ml_store_sku_question_context_v1|"
        "store-sku-migration:jk_context_store_sku_migration_v1|"
        "adaptive-public-flow-v1|curation-schema:3|integral-context-no-truncation-v1|"
        "canonical-document-max:24000|guidance-max:4000|operational-max:2000|"
        "question-history-max:1500|integral-envelope-max:32000|"
        "simple-prompt-max:40000|high-risk-stage-max:48000|global-transport-max:52000|"
        "conditional-public-web-v1|"
        "six-stage-sol-high|two-round-gap-research|directed-reference-relations|"
        "nonempty-ai-draft-preserved|public-signature-append-only-v1|oversize-manual-edit-v1|"
        "human-approval-required|no-direct-publish"
    ).encode("utf-8")
).hexdigest()
THREAD_IDLE_TTL_SECONDS = 30 * 24 * 60 * 60
TERMINAL_STATUSES = {"completed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running", "waiting_retry"}
MAX_GLOBAL_JOBS = 2
MAX_SECONDS = 15 * 60.0
PUBLIC_RESEARCH_DEADLINE_SECONDS = 15 * 60.0
PUBLIC_DEEP_RESEARCH_MAX_SECONDS = 5 * 60.0
POST_SALE_DEADLINE_SECONDS = 180.0
RESEARCH_DEADLINE_SECONDS = PUBLIC_RESEARCH_DEADLINE_SECONDS
RETRY_DELAYS_SECONDS = (5, 15, 30)
MAX_EVIDENCE_ATTEMPTS = 2
MAX_OPERATIONAL_FAILURES = 3
MAX_TOTAL_ATTEMPTS = 5
PROVIDER_RETRY_REASONS = frozenset({
    "provider_timeout",
    "provider_connection",
    "provider_http_429",
    "provider_http_5xx",
})
AUTOMATION_QUEUE_PER_STORE_LIMIT = 3
AUTOMATION_QUEUE_TOTAL_LIMIT = 12
QUEUE_ORIGIN_MANUAL = "manual"
QUEUE_ORIGIN_AUTOMATION = "automation"
QUEUE_PRIORITY_MANUAL = 100
QUEUE_PRIORITY_AUTOMATION = 10
RETRY_HISTORY_LIMIT = 12
RESEARCH_DEADLINE_WARNING = "Rascunho gerado com as informacoes disponiveis."

logger = logging.getLogger(__name__)
_RUNTIME: Any = None
_SCHEDULER_LOCK = threading.RLock()
_ACTIVE_JOBS: set[str] = set()
_ACTIVE_STORES: set[str] = set()
_RETRY_TIMERS: dict[str, threading.Timer] = {}
_WORKER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_RECOVERY_STARTED = False
_INITIAL_CREATION_GUARD = threading.RLock()
_INITIAL_CREATION_LOCKS: dict[tuple[str, str, str, str], tuple[threading.Lock, int]] = {}
_INITIAL_CREATION_PROCESS_STRIPES = 256


class _LeaseLost(RuntimeError):
    pass


def _lease_generation(job: Any) -> int:
    try:
        return max(0, int((job or {}).get("lease_generation") or 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def _saved_by_same_lease(
    saved: Any,
    attempted: dict[str, Any],
    *,
    status: str = "",
    proposal_hash: str = "",
) -> bool:
    if not isinstance(saved, dict) or _lease_generation(saved) != _lease_generation(attempted):
        return False
    if status and str(saved.get("status") or "") != status:
        return False
    if proposal_hash and str(saved.get("proposal_hash") or (saved.get("result") or {}).get("proposal_hash") or "") != proposal_hash:
        return False
    return True


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


def _task_retry_policy(task_type: Any) -> str:
    return "bounded"


def _task_deadline_seconds(task_type: Any) -> int:
    return (
        int(POST_SALE_DEADLINE_SECONDS)
        if _canonical_task_type(task_type) == TASK_TYPE_POST_SALE
        else int(PUBLIC_RESEARCH_DEADLINE_SECONDS)
    )


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
    verification = latest.get("verification") if isinstance(latest.get("verification"), dict) else {}
    result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
    if not verification and isinstance(result.get("verification"), dict):
        verification = result.get("verification") or {}
    if str(verification.get("status") or "").strip().lower() == "rejected":
        return "", "previous_draft_rejected", False
    if _previous_job_has_zero_research_facts(latest):
        return "", "zero_research_facts", False
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


def _research_payload_has_any_fact(roots: list[Any]) -> bool:
    """Count candidate, verified and conflicting facts without reading raw documents."""

    fact_list_keys = {
        "product_research_evidence", "verified_product_evidence",
        "verified_target_evidence", "claims",
    }
    pending: list[tuple[Any, int]] = [(value, 0) for value in roots if value is not None]
    seen = 0
    while pending and seen < 600:
        value, depth = pending.pop()
        seen += 1
        if depth > 8:
            continue
        if isinstance(value, dict):
            for key in fact_list_keys:
                facts = value.get(key)
                if isinstance(facts, list) and any(isinstance(item, dict) for item in facts):
                    return True
            metrics = value.get("research_metrics")
            if isinstance(metrics, dict):
                for key in ("fields_candidate", "fields_conflict", "fields_confirmed"):
                    try:
                        if int(metrics.get(key) or 0) > 0:
                            return True
                    except (TypeError, ValueError, OverflowError):
                        continue
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend((item, depth + 1) for item in value)
    return False


def _previous_job_has_zero_research_facts(latest: dict[str, Any]) -> bool:
    """Detect a completed research attempt that yielded no facts in any state."""

    result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
    roots: list[Any] = [latest.get("research_summary"), result, result.get("contexto")]
    if _research_payload_has_any_fact(roots):
        return False
    pending: list[tuple[Any, int]] = [(value, 0) for value in roots if value is not None]
    seen = 0
    while pending and seen < 600:
        value, depth = pending.pop()
        seen += 1
        if depth > 8:
            continue
        if isinstance(value, dict):
            metrics = value.get("research_metrics")
            if isinstance(metrics, dict):
                has_count = "fields_confirmed" in metrics
                try:
                    confirmed = int(metrics.get("fields_confirmed") or 0)
                except (TypeError, ValueError, OverflowError):
                    confirmed = -1
                stop_reason = str(metrics.get("stop_reason") or "").strip().lower()
                if (
                    has_count
                    and confirmed == 0
                    and metrics.get("coverage_complete") is False
                    and stop_reason in {
                        "no_new_facts",
                        "provider_unavailable",
                        "deadline",
                        "page_limit",
                        "query_limit",
                    }
                ):
                    return True
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend((item, depth + 1) for item in value)
    return False


def _job_deadline_epoch(job: dict[str, Any]) -> float:
    deadline_seconds = _task_deadline_seconds(job.get("task_type"))
    if deadline_seconds <= 0:
        job["deadline_at_epoch"] = 0.0
        job["deadline_seconds"] = 0
        return 0.0
    try:
        explicit = float(job.get("deadline_at_epoch") or 0.0)
    except (TypeError, ValueError, OverflowError):
        explicit = 0.0
    if explicit > 0.0:
        return explicit
    try:
        first_started = float(job.get("first_started_at_epoch") or 0.0)
    except (TypeError, ValueError, OverflowError):
        first_started = 0.0
    if first_started <= 0.0:
        job["deadline_seconds"] = deadline_seconds
        return 0.0
    deadline = first_started + deadline_seconds
    job["deadline_at_epoch"] = deadline
    job["execution_deadline_epoch"] = deadline
    job["deadline_seconds"] = deadline_seconds
    return deadline


def _job_deadline_expired(job: dict[str, Any], *, now: Optional[float] = None) -> bool:
    current = time.time() if now is None else float(now)
    deadline = _job_deadline_epoch(job)
    return bool(deadline > 0.0 and current >= deadline)


def _unique_warnings(*groups: Any) -> list[str]:
    result: list[str] = []
    for group in groups:
        values = group if isinstance(group, (list, tuple, set)) else [group]
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
    return result[:12]


def _merge_unique_items(*groups: Any, limit: int = 64) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for group in groups:
        values = group if isinstance(group, (list, tuple, set)) else []
        for value in values:
            if value in (None, "", [], {}):
                continue
            marker = _hash(value)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(value)
            if len(merged) >= limit:
                return merged
    return merged


def _evidence_rank(value: Any) -> int:
    return {
        "": 0,
        "missing": 0,
        "pending": 0,
        "unknown": 0,
        "insufficient": 1,
        "partial": 1,
        "completed": 2,
        "confirmed": 3,
    }.get(_normal(value), 1)


def _merge_evidence_envelopes(previous: Any, incoming: Any) -> dict[str, Any]:
    old = previous if isinstance(previous, dict) else {}
    new = incoming if isinstance(incoming, dict) else {}
    records: list[dict[str, Any]] = []
    record_indexes: dict[str, int] = {}
    for candidate in list(old.get("records") or []) + list(new.get("records") or []):
        if not isinstance(candidate, dict):
            continue
        identity = _hash({
            "field": candidate.get("field"),
            "value": candidate.get("value"),
            "store": candidate.get("store"),
            "source": candidate.get("source") or candidate.get("reference"),
        })
        if identity not in record_indexes:
            record_indexes[identity] = len(records)
            records.append(dict(candidate))
            continue
        index = record_indexes[identity]
        current = records[index]
        current_rank = max(_evidence_rank(current.get("coverage")), _evidence_rank(current.get("authority")))
        candidate_rank = max(_evidence_rank(candidate.get("coverage")), _evidence_rank(candidate.get("authority")))
        merged_record = {**current, **{key: value for key, value in candidate.items() if value not in (None, "", [], {})}}
        if current_rank > candidate_rank:
            merged_record["coverage"] = current.get("coverage") or current.get("authority")
            merged_record["authority"] = current.get("authority") or current.get("coverage")
        records[index] = merged_record
    sources = _merge_unique_items(
        old.get("sources"),
        new.get("sources"),
        [item.get("source") for item in records if item.get("source")],
        limit=64,
    )
    gaps = _merge_unique_items(old.get("gaps"), new.get("gaps"), limit=32)
    confidence_order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    old_confidence = str(old.get("confidence") or "unknown")
    new_confidence = str(new.get("confidence") or "unknown")
    confidence = old_confidence if confidence_order.get(old_confidence, 0) > confidence_order.get(new_confidence, 0) else new_confidence
    merged = {
        **old,
        **new,
        "schema_version": str(new.get("schema_version") or old.get("schema_version") or EVIDENCE_ENVELOPE_V2),
        "records": records[:64],
        "sources": sources,
        "gaps": gaps,
        "confidence": confidence,
        "evidence_sufficient": bool(old.get("evidence_sufficient") or new.get("evidence_sufficient")),
        "coverage_complete": bool(old.get("coverage_complete") or new.get("coverage_complete")),
        "scope": _merge_context_values(old.get("scope"), new.get("scope")),
    }
    if merged["evidence_sufficient"] or merged["coverage_complete"]:
        merged["status"] = "completed"
        merged["gaps"] = []
    if _evidence_rank(old.get("status")) > _evidence_rank(new.get("status")):
        merged["status"] = old.get("status")
    return normalize_evidence_envelope(merged).to_dict()


def _merge_context_values(previous: Any, incoming: Any) -> Any:
    if isinstance(previous, dict) or isinstance(incoming, dict):
        old = previous if isinstance(previous, dict) else {}
        new = incoming if isinstance(incoming, dict) else {}
        merged: dict[str, Any] = {}
        for key in dict.fromkeys([*old.keys(), *new.keys()]):
            if key == "evidence_envelope":
                merged[key] = _merge_evidence_envelopes(old.get(key), new.get(key))
            elif key == "diagnostico_ia" and (isinstance(old.get(key), list) or isinstance(new.get(key), list)):
                old_rows = list(old.get(key) or [])
                new_rows = list(new.get(key) or [])
                first = _merge_context_values(old_rows[0] if old_rows else {}, new_rows[0] if new_rows else {})
                merged[key] = [first, *_merge_unique_items(old_rows[1:], new_rows[1:], limit=15)]
            else:
                merged[key] = _merge_context_values(old.get(key), new.get(key))
        return merged
    if isinstance(previous, (list, tuple, set)) or isinstance(incoming, (list, tuple, set)):
        return _merge_unique_items(previous, incoming)
    return incoming if incoming not in (None, "") else previous


def _merge_evidence_matrix(previous: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for candidate in list(previous or []) + list(incoming or []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = _normal(candidate.get("id"))
        candidate_intent = _normal(candidate.get("intent"))
        index = next((
            idx for idx, row in enumerate(merged)
            if (candidate_id and candidate_id == _normal(row.get("id")))
            or (candidate_intent and candidate_intent == _normal(row.get("intent")))
        ), -1)
        if index < 0:
            merged.append(dict(candidate))
            continue
        current = merged[index]
        combined = _merge_context_values(current, candidate)
        if _evidence_rank(current.get("status")) > _evidence_rank(candidate.get("status")):
            combined["status"] = current.get("status")
        merged[index] = combined
    return merged[:16]


def _context_evidence_sources(context: Any) -> list[str]:
    data = context if isinstance(context, dict) else {}
    diagnostic = _diagnostic_result(data)
    analysis = diagnostic.get("compatibility_analysis") if isinstance(diagnostic.get("compatibility_analysis"), dict) else {}
    envelopes = [data.get("evidence_envelope"), diagnostic.get("evidence_envelope")]
    raw_sources: list[Any] = [*(analysis.get("sources") or []), *(data.get("sources") or [])]
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        raw_sources.extend(envelope.get("sources") or [])
        for record in envelope.get("records") or []:
            if isinstance(record, dict):
                raw_sources.extend([record.get("source"), record.get("reference"), record.get("url")])
    result: list[str] = []
    for source in raw_sources:
        text = str(source or "").strip()[:500]
        if text and text not in result:
            result.append(text)
    return result[:32]


def _public_classification_missing(job: dict[str, Any]) -> bool:
    return bool(
        _canonical_task_type(job.get("task_type")) == TASK_TYPE_PUBLIC_QUESTION
        and not list(job.get("subquestions") or [])
    )


def _job_question_text(job: dict[str, Any]) -> str:
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    question = request.get("pergunta") if isinstance(request.get("pergunta"), dict) else {}
    parts = [
        str(question.get("text") or ""),
        str(request.get("question_text") or ""),
    ]
    parts.extend(
        str(item.get("question") or "")
        for item in (job.get("subquestions") or [])
        if isinstance(item, dict)
    )
    return " ".join(part.strip() for part in parts if part.strip())


def _fallback_signature(job: dict[str, Any]) -> str:
    store = str(job.get("store") or "da loja").strip()
    return f"Equipe {store} agradece pelo contato, Precisando estamos a disposição!"


def _fallback_sanitize_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[dado removido]", text, flags=re.I)
    text = re.sub(r"\b[A-HJ-NPR-Z0-9]{17}\b", "[identificador removido]", text, flags=re.I)
    text = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "[dado removido]", text)
    text = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "[dado removido]", text)
    text = re.sub(
        r"(?<!\d)(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}(?!\d)",
        "[dado removido]",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()[: max(1, int(limit or 1))]


def _fallback_sanitize_classification(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    continuity = data.get("continuidade") if isinstance(data.get("continuidade"), dict) else {}
    compatibility = (
        data.get("compatibilidade")
        if isinstance(data.get("compatibilidade"), dict)
        else {}
    )
    raw_categories = data.get("categorias") if isinstance(data.get("categorias"), list) else []
    return {
        "categoria": str(data.get("categoria") or "")[:40],
        "categorias": [str(item or "")[:40] for item in raw_categories[:8]],
        "continuidade": {
            "tipo": str(continuity.get("tipo") or "")[:40],
            "herdou_historico": continuity.get("herdou_historico") is True,
        },
        "compatibilidade": {
            "aplicavel": compatibility.get("aplicavel") is True,
            "target_item": _fallback_sanitize_text(compatibility.get("target_item"), 300),
            "target_type": str(compatibility.get("target_type") or "")[:40],
        },
    }


def _fallback_context(
    job: dict[str, Any],
    supplied: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build an in-memory view; callers must never add it to the persisted job."""

    transient = supplied if isinstance(supplied, dict) else {}
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    question = request.get("pergunta") if isinstance(request.get("pergunta"), dict) else {}
    item = transient.get("item") if isinstance(transient.get("item"), dict) else {}
    if not item and isinstance(request.get("item"), dict):
        item = request.get("item") or {}
    history = transient.get("history") if isinstance(transient.get("history"), list) else []
    if not history and isinstance(question.get("buyer_question_chat"), list):
        history = question.get("buyer_question_chat") or []
    raw_classification = (
        transient.get("classification")
        if isinstance(transient.get("classification"), dict)
        else {}
    )
    classification = _fallback_sanitize_classification(raw_classification)
    return {
        "question_text": _fallback_sanitize_text(
            ((transient.get("question") or {}).get("text") if isinstance(transient.get("question"), dict) else "")
            or question.get("text")
            or request.get("question_text")
            or "",
            1200,
        ),
        "item": {
            "title": _fallback_sanitize_text(item.get("title"), 500),
            "description": _fallback_sanitize_text(
                item.get("description") or item.get("descricao"),
                3500,
            ),
        },
        "history": [
            {
                "role": str(event.get("role") or event.get("from_role") or "")[:20],
                "text": _fallback_sanitize_text(event.get("text"), 500),
            }
            for event in history[-10:]
            if isinstance(event, dict) and str(event.get("text") or "").strip()
        ],
        "classification": classification,
    }


def _fallback_buyer_context_text(context: dict[str, Any]) -> str:
    texts = [str(context.get("question_text") or "")]
    classification = (
        context.get("classification")
        if isinstance(context.get("classification"), dict)
        else {}
    )
    continuity = (
        classification.get("continuidade")
        if isinstance(classification.get("continuidade"), dict)
        else {}
    )
    if str(continuity.get("tipo") or "") in {"novo_assunto", "independente"}:
        return " ".join(text.strip() for text in texts if text.strip())
    for event in context.get("history") or []:
        if not isinstance(event, dict):
            continue
        role = _normal(event.get("role"))
        if role not in {"seller", "loja", "store"}:
            texts.append(str(event.get("text") or ""))
    return " ".join(text.strip() for text in texts if text.strip())


def _fallback_announced_codes(description: str) -> list[str]:
    match = re.search(
        r"c[oó]digos?\s+da\s+pe[cç]a\s*:\s*(.+?)(?:\s+(?:descri[cç][aã]o|aplica[cç][oõ]es?)\s*:|$)",
        str(description or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    candidates = re.findall(
        r"\b(?:[A-Z]{1,5}\d[A-Z0-9]*(?:-[A-Z0-9]+)+|[A-Z]{1,5}\d{5,}[A-Z0-9]*)\b",
        match.group(1).upper(),
    )
    return list(dict.fromkeys(candidates))[:8]


def _fallback_buyer_years(text: str) -> list[int]:
    years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", str(text or ""))]
    for first, second in re.findall(r"\b(\d{2})\s*/\s*(\d{2})\b", str(text or "")):
        for value in (first, second):
            numeric = int(value)
            years.append(2000 + numeric if numeric <= 49 else 1900 + numeric)
    return list(dict.fromkeys(years))[:4]


def _fallback_application_ranges(description: str) -> list[tuple[int, int]]:
    return [
        (int(first), int(second))
        for first, second in re.findall(
            r"\b((?:19|20)\d{2})\s*[-–]\s*((?:19|20)\d{2})\b",
            str(description or ""),
        )
        if int(first) <= int(second)
    ]


def _fallback_application_text(description: str) -> str:
    match = re.search(
        r"aplica[cç][oõ]es?\s*:\s*(.+)$",
        str(description or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )
    return str(match.group(1) if match else "").strip()


_FALLBACK_NON_IDENTITY_TOKENS = frozenset({
    "a", "as", "o", "os", "um", "uma", "de", "da", "do", "das", "dos", "e",
    "em", "na", "no", "nas", "nos", "ao", "aos", "para", "por", "com", "sem",
    "serve", "servir", "servem", "compativel", "compatibilidade", "aplica", "aplicacao",
    "aplicacoes", "modelo", "carro", "veiculo", "meu", "minha", "seu", "sua", "este",
    "esta", "esse", "essa", "peca", "produto", "original", "codigo", "codigos", "nao",
    "chassi", "chassis", "vin", "final", "ano", "anos", "identificador", "removido", "dado",
    "sim", "bom", "boa", "dia", "tarde", "noite", "ola", "amigo", "amiga", "quero",
    "gostaria", "preciso", "saber", "qual", "quais", "pode", "posso", "antes", "ainda",
    "conferi", "conferir", "desmontei", "desmontar", "monta", "montam", "encaixa", "usar",
    "uso", "tenho", "tem", "compra", "comprar", "fiz", "verdade", "corrigindo", "correcao",
    "quis", "dizer", "realidade", "correto", "corrijo", "enganei",
    "bomba", "combustivel", "filtro", "sensor", "amortecedor", "mola", "gas", "pressao",
    "tampa", "cacamba", "porta", "torneira", "registro", "kit", "par", "unidade",
    "unidades", "lado", "motor", "cambio", "caixa", "automatico", "automatica", "manual",
    "automatizado", "cvt", "gasolina", "diesel", "flex", "etanol", "alcool", "gnv",
    "hibrido", "eletrico", "dianteiro", "dianteira", "traseiro", "traseira", "direito",
    "direita", "esquerdo", "esquerda", "dynamic", "black", "novo", "nova",
    "audi", "bmw", "chevrolet", "citroen", "fiat", "ford", "honda", "hyundai", "jeep",
    "kia", "land", "range", "rover", "mercedes", "mitsubishi", "nissan", "peugeot",
    "renault", "subaru", "suzuki", "toyota", "volkswagen", "volvo",
})

_FALLBACK_NEGATIVE_APPLICATION_MARKERS = (
    "nao aplica",
    "nao se aplica",
    "nao aplicavel",
    "sem aplicacao",
    "aplicacao nao confirmada",
    "sem compatibilidade",
    "nao compativel",
    "nao serve",
    "exceto",
)


def _fallback_application_segments(application_text: str) -> list[str]:
    """Split application clauses without breaking engine sizes such as 2.0."""

    return [
        segment.strip()
        for segment in re.split(
            r"(?:[;|\r\n\u2022]+|\.(?!\d))",
            str(application_text or ""),
        )
        if segment.strip()
    ]


def _fallback_identity_tokens(text: Any) -> list[str]:
    normalized = re.sub(
        r"\b(?:chassi|chassis|vin)\b(?:\s+final)?\s*[:#-]?\s*[a-z0-9-]{4,25}\b",
        " ",
        _normal(text),
    )
    tokens = re.findall(r"\b[a-z0-9]{2,}\b", normalized)
    return [
        token
        for token in tokens
        if token not in _FALLBACK_NON_IDENTITY_TOKENS
        and not re.fullmatch(r"(?:19|20)\d{2}", token)
        and not re.fullmatch(r"\d+(?:[.,]\d+)?", token)
        and not re.fullmatch(r"(?:v[468]|\d{1,2}v)", token)
        and not re.fullmatch(r"[a-hj-npr-z0-9]{17}", token)
    ]


def _fallback_model_label(tokens: list[str]) -> str:
    return " ".join(
        token.upper() if any(char.isdigit() for char in token) else token.capitalize()
        for token in tokens
    )


def _fallback_target_label(model: str, target_text: str) -> str:
    qualifiers = _fallback_qualifiers(target_text)
    parts = [model]
    for category in ("displacement", "fuel", "engine", "transmission"):
        values = qualifiers.get(category) or set()
        if len(values) != 1:
            continue
        value = next(iter(values))
        if category == "fuel":
            value = value.capitalize()
        elif category == "transmission":
            value = {
                "automatico": "Automático",
                "manual": "Manual",
                "automatizado": "Automatizado",
                "cvt": "CVT",
            }.get(value, value)
        elif category == "engine":
            value = value.upper()
        parts.append(value)
    return " ".join(parts)


def _fallback_qualifiers(text: Any) -> dict[str, set[str]]:
    normalized = _normal(text).replace(",", ".")
    fuels = {
        value
        for value in (
            "gasolina", "diesel", "flex", "etanol", "alcool", "gnv", "hibrido", "eletrico",
        )
        if re.search(rf"\b{re.escape(value)}\b", normalized)
    }
    displacements = {
        re.sub(r"\s+", "", value)
        for value in re.findall(r"\b\d\s*\.\s*\d\b", normalized)
    }
    transmissions = {
        canonical
        for pattern, canonical in (
            (r"\bautomatic[oa]s?\b", "automatico"),
            (r"\bmanual(?:is)?\b", "manual"),
            (r"\bautomatizad[oa]s?\b", "automatizado"),
            (r"\bcvt\b", "cvt"),
        )
        if re.search(pattern, normalized)
    }
    sides = {
        canonical
        for pattern, canonical in (
            (r"\bdireit[oa]s?\b", "direito"),
            (r"\besquerd[oa]s?\b", "esquerdo"),
            (r"\bdianteir[oa]s?\b", "dianteiro"),
            (r"\btraseir[oa]s?\b", "traseiro"),
        )
        if re.search(pattern, normalized)
    }
    engines = set(re.findall(r"\b(?:v[468]|\d{1,2}v)\b", normalized))
    return {
        "fuel": fuels,
        "displacement": displacements,
        "transmission": transmissions,
        "side": sides,
        "engine": engines,
    }


def _fallback_has_negated_qualifier(text: Any) -> bool:
    normalized = _normal(text).replace(",", ".")
    qualifier = (
        r"(?:gasolina|diesel|flex|etanol|alcool|gnv|hibrido|eletrico|"
        r"automatic[oa]|manual|automatizad[oa]|cvt|direit[oa]|esquerd[oa]|"
        r"dianteir[oa]|traseir[oa]|v[468]|\d{1,2}v|\d\s*\.\s*\d)"
    )
    return bool(
        re.search(rf"\b(?:nao|sem|exceto)\s+(?:motor\s+)?{qualifier}\b", normalized)
        or re.search(rf"\b{qualifier}\s+(?:nao|excluido|excluida)\b", normalized)
    )


def _fallback_target_qualifiers_supported(
    target_text: str,
    application_segment: str,
    listing_text: str,
) -> bool:
    if any(
        _fallback_has_negated_qualifier(value)
        for value in (target_text, application_segment, listing_text)
    ):
        return False
    target = _fallback_qualifiers(target_text)
    segment = _fallback_qualifiers(application_segment)
    listing = _fallback_qualifiers(listing_text)
    for category, target_values in target.items():
        if not target_values:
            continue
        segment_values = segment.get(category) or set()
        if segment_values:
            if not target_values.issubset(segment_values):
                return False
            continue
        listing_values = listing.get(category) or set()
        if listing_values != target_values:
            return False
    return True


def _fallback_current_target_override(text: Any) -> bool:
    normalized = _normal(text)
    facts = _perguntas_ia_continuation_facts(
        _perguntas_ia_texto_autoritativo_atual(text)
    )
    return bool(
        _perguntas_ia_current_facts_explicit(text, facts)
        or re.search(
            r"\b(?:na\s+verdade|corrigindo|correcao|modelo\s+correto|"
            r"meu\s+carro|meu\s+veiculo|e\s+um|e\s+uma|"
            r"(?:eu\s+)?quis\s+dizer|na\s+realidade|o\s+correto\s+(?:e|eh)|"
            r"corrijo|me\s+enganei)\b",
            normalized,
        )
        or re.search(r"^\s*e\s+[a-z0-9]", normalized)
    )


def _fallback_application_match(
    item: dict[str, Any],
    description: str,
    target_text: str,
) -> Optional[tuple[str, tuple[int, int]]]:
    application_text = _fallback_application_text(description)
    title = str(item.get("title") or "")
    title_tokens = set(_fallback_identity_tokens(title))
    target_tokens = list(dict.fromkeys(_fallback_identity_tokens(target_text)))
    normalized_target = _normal(target_text)
    if (
        not application_text
        or not target_tokens
        or not all(token in title_tokens for token in target_tokens)
        or re.search(
            r"\bnao\s+(?:e|eh|serve|se\s+trata|corresponde|encaixa|aplica)\b",
            normalized_target,
        )
    ):
        return None

    matched: list[tuple[str, tuple[int, int]]] = []
    for segment in _fallback_application_segments(application_text):
        normalized_segment = _normal(segment)
        segment_tokens = set(_fallback_identity_tokens(segment))
        if not all(token in segment_tokens for token in target_tokens):
            continue
        if any(marker in normalized_segment for marker in _FALLBACK_NEGATIVE_APPLICATION_MARKERS):
            return None
        ranges = _fallback_application_ranges(segment)
        if len(ranges) != 1:
            return None
        if not _fallback_target_qualifiers_supported(
            target_text,
            segment,
            " ".join((title, segment)),
        ):
            return None
        matched.append((_fallback_model_label(target_tokens), ranges[0]))
    if len(matched) != 1:
        return None
    return matched[0]


def _fallback_application_details(
    item: dict[str, Any],
    description: str,
    target_text: str,
) -> Optional[tuple[str, list[int], list[str]]]:
    match = _fallback_application_match(item, description, target_text)
    years = _fallback_buyer_years(target_text)
    codes = _fallback_announced_codes(description)
    if not match or not years or not codes:
        return None
    model, application_range = match
    if not all(application_range[0] <= year <= application_range[1] for year in years):
        return None
    return model, years, codes


def _contextual_compatibility_fallback(job: dict[str, Any], context: dict[str, Any]) -> str:
    item = context.get("item") if isinstance(context.get("item"), dict) else {}
    description = str(item.get("description") or "")
    current_text = str(context.get("question_text") or "")
    classification = (
        context.get("classification")
        if isinstance(context.get("classification"), dict)
        else {}
    )
    continuity = (
        classification.get("continuidade")
        if isinstance(classification.get("continuidade"), dict)
        else {}
    )
    inherit_history = str(continuity.get("tipo") or "") not in {"novo_assunto", "independente"}
    buyer_texts = [current_text]
    if inherit_history:
        buyer_texts.extend(
            str(event.get("text") or "")
            for event in reversed(context.get("history") or [])
            if isinstance(event, dict)
            and _normal(event.get("role")) not in {"seller", "loja", "store"}
        )
    if _fallback_current_target_override(current_text):
        buyer_texts = [current_text]

    compatibility = (
        classification.get("compatibilidade")
        if isinstance(classification.get("compatibilidade"), dict)
        else {}
    )
    classified_target = str(compatibility.get("target_item") or "").strip()
    if compatibility.get("aplicavel") is True and str(compatibility.get("target_type") or "") == "vehicle":
        classified_details = _fallback_application_details(item, description, classified_target)
        if classified_details and any(
            _fallback_buyer_supports_target(
                item,
                description,
                classified_target,
                buyer_text,
            )
            for buyer_text in buyer_texts
        ):
            buyer_texts.insert(0, classified_target)

    matches: list[tuple[tuple[str, list[int], list[str]], str]] = []
    for target_text in buyer_texts:
        details = _fallback_application_details(item, description, target_text)
        if details and details not in [match[0] for match in matches]:
            matches.append((details, target_text))
    if len(matches) != 1:
        return ""
    details, matched_target = matches[0]
    model, years, codes = details
    target_label = _fallback_target_label(model, matched_target)
    year_label = "/".join(str(year) for year in years)
    return (
        f"Boa tarde! O modelo {target_label} {year_label} está dentro da aplicação anunciada para este produto. "
        "Porém, a confirmação final depende da correspondência do código da peça original com um dos códigos "
        f"anunciados: {' / '.join(codes)}.\n\n{_fallback_signature(job)}"
    )


def _fallback_buyer_supports_target(
    item: dict[str, Any],
    description: str,
    target_text: str,
    buyer_text: str,
) -> bool:
    target_details = _fallback_application_details(item, description, target_text)
    buyer_details = _fallback_application_details(item, description, buyer_text)
    if not target_details or not buyer_details:
        return False
    target_model, target_years, _target_codes = target_details
    buyer_model, buyer_years, _buyer_codes = buyer_details
    if _normal(target_model) != _normal(buyer_model) or target_years != buyer_years:
        return False
    target_qualifiers = _fallback_qualifiers(target_text)
    buyer_qualifiers = _fallback_qualifiers(buyer_text)
    return all(
        not values or values.issubset(buyer_qualifiers.get(category) or set())
        for category, values in target_qualifiers.items()
    )


def _fallback_context_blocked_by_safety(context: dict[str, Any]) -> bool:
    classification = (
        context.get("classification")
        if isinstance(context.get("classification"), dict)
        else {}
    )
    continuity = (
        classification.get("continuidade")
        if isinstance(classification.get("continuidade"), dict)
        else {}
    )
    continuity_type = str(continuity.get("tipo") or "")
    current_text = str(context.get("question_text") or "")
    if _perguntas_ia_prompt_injection_evidente(current_text):
        return True
    if _perguntas_ia_mensagem_pos_venda_evidente(current_text):
        return True
    if _perguntas_ia_continuation_negated_identity(current_text):
        return True
    if _perguntas_ia_continuation_has_negated_qualifier(current_text):
        return True
    if _perguntas_ia_assunto_atual_autossuficiente(current_text):
        return True
    if continuity_type in {"continuacao", "inconclusiva"} and not (
        _perguntas_ia_turno_continuacao_positiva(current_text)
    ):
        return True
    if continuity_type in {"novo_assunto", "independente"}:
        return False
    for event in context.get("history") or []:
        if not isinstance(event, dict):
            continue
        if _normal(event.get("role") or event.get("from_role")) in {"seller", "loja", "store"}:
            continue
        buyer_text = str(event.get("text") or "")
        if _perguntas_ia_prompt_injection_evidente(buyer_text):
            return True
        if _perguntas_ia_mensagem_pos_venda_evidente(buyer_text):
            return True
    return False


def _neutral_fallback_with_source(job: dict[str, Any]) -> tuple[str, str]:
    return (
        "Boa tarde! Essa informação não está confirmada nos dados disponíveis do produto; "
        "antes da compra, considere somente a especificação descrita no anúncio."
        f"\n\n{_fallback_signature(job)}",
        "neutral_fallback",
    )


def _safe_fallback_with_source(
    job: dict[str, Any],
    *,
    fallback_context: Optional[dict[str, Any]] = None,
    allow_contextual: bool = False,
) -> tuple[str, str]:
    if not allow_contextual:
        return _neutral_fallback_with_source(job)
    context = _fallback_context(job, fallback_context)
    if _fallback_context_blocked_by_safety(context):
        return _neutral_fallback_with_source(job)
    classification = (
        context.get("classification")
        if isinstance(context.get("classification"), dict)
        else {}
    )
    continuity = (
        classification.get("continuidade")
        if isinstance(classification.get("continuidade"), dict)
        else {}
    )
    current_text = str(context.get("question_text") or "")
    current_target_override = _fallback_current_target_override(current_text)
    inherit_previous_subject = not current_target_override and str(continuity.get("tipo") or "") not in {
        "novo_assunto",
        "independente",
    }
    question_parts = [current_text if current_target_override else _fallback_buyer_context_text(context)]
    if inherit_previous_subject:
        question_parts.insert(0, _job_question_text(job))
    normalized = _normal(" ".join(part for part in question_parts if part))
    raw_categories = (
        classification.get("categorias")
        if isinstance(classification.get("categorias"), list)
        else []
    )
    categories = {
        _normal(value)
        for value in [classification.get("categoria"), *raw_categories]
        if _normal(value)
    }
    if inherit_previous_subject:
        categories.update({
            _normal(item.get("intent"))
            for item in (job.get("subquestions") or [])
            if isinstance(item, dict) and _normal(item.get("intent"))
        })
    asks_compatibility = bool(
        "compatibility" in categories
        or any(marker in normalized for marker in ("serve", "compativ", "aplicacao", "encaix"))
    )
    if asks_compatibility:
        contextual = _contextual_compatibility_fallback(job, context)
        if contextual:
            return contextual, "contextual_fallback"
    asks_kit_quantity = bool(
        re.search(r"\b(?:par|unidades?|quantidade)\b", normalized)
        or re.search(r"\b(?:duas|2)\s+pecas?\b", normalized)
    )
    content: list[str] = []
    if asks_compatibility:
        content.append(
            "Para o modelo informado, a confirmação final depende da correspondência entre a aplicação "
            "e o código da peça original com os dados descritos no anúncio."
        )
    if asks_kit_quantity:
        content.append("A quantidade do kit deve ser considerada exatamente como descrita no anúncio.")
    if content and fallback_context:
        return (
            "Boa tarde! " + " ".join(content[:3]) + f"\n\n{_fallback_signature(job)}",
            "contextual_fallback",
        )
    return _neutral_fallback_with_source(job)


def _subject_aware_safe_fallback(
    job: dict[str, Any],
    fallback_context: Optional[dict[str, Any]] = None,
) -> str:
    """Compatibility facade for callers that only consume the editable draft."""

    explicit_context = (
        fallback_context
        if isinstance(fallback_context, dict)
        else _fallback_context(job)
    )
    answer, _source = _safe_fallback_with_source(
        job,
        fallback_context=explicit_context,
        allow_contextual=True,
    )
    return answer


def _complete_without_draft(
    job: dict[str, Any],
    *,
    warning: str,
    completion_reason: str,
    fallback_context: Optional[dict[str, Any]] = None,
    allow_contextual: bool = False,
    deadline_reached: bool = True,
) -> dict[str, Any]:
    """Compatibility entrypoint that now always returns an editable safe draft."""

    partial = job.get("last_partial_result") if isinstance(job.get("last_partial_result"), dict) else {}
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    partial_answer = str(partial.get("resposta") or "")
    request_answer = str(request.get("resposta_atual") or "")
    preserved_answer = partial_answer if partial_answer.strip() else request_answer
    if preserved_answer.strip():
        return _complete_with_best_available(
            job,
            answer=preserved_answer,
            context=(partial.get("contexto") if isinstance(partial.get("contexto"), dict) else {}),
            matrix=list(partial.get("evidence_status") or []),
            warnings=_unique_warnings(partial.get("warnings"), [warning]),
            draft_source=str(partial.get("draft_source") or ("ai" if partial_answer.strip() else "existing_draft")),
            completion_reason=completion_reason,
            deadline_reached=deadline_reached,
        )

    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    job_id = str(job.get("job_id") or "")
    version = max(1, int(job.get("proposal_version") or 1))
    fallback_answer, draft_source = _safe_fallback_with_source(
        job,
        fallback_context=fallback_context,
        allow_contextual=allow_contextual,
    )
    proposal_hash = _hash(
        {
            "job_id": job_id,
            "version": version,
            "store": job.get("store"),
            "subject": job.get("event_subject_key") or job.get("subject_key"),
            "answer": fallback_answer,
        }
    )
    result = {
        "resposta": fallback_answer,
        "contexto": {},
        "evidence_envelope": {},
        "evidence_status": [],
        "data_sufficient": False,
        "warnings": _unique_warnings(job.get("warnings"), [warning]),
        "proposal_id": job_id,
        "proposal_version": version,
        "proposal_hash": proposal_hash,
        "requires_approval": True,
        "publish_attempted": False,
        "completed_with_partial": True,
        "blocked_without_draft": False,
        "completion_reason": completion_reason,
        "review_required": False,
        "draft_source": draft_source,
    }
    job.update(
        {
            "status": "completed",
            "agent_state": "aguardando_aprovacao",
            "current_step": "aprovar",
            "result": result,
            "warnings": list(result["warnings"]),
            "deadline_reached": bool(deadline_reached),
            "completed_with_partial": True,
            "blocked_without_draft": False,
            "completion_reason": completion_reason,
            "review_required": False,
            "draft_source": draft_source,
            "proposal_id": job_id,
            "proposal_version": version,
            "proposal_hash": proposal_hash,
            "requires_approval": True,
            "publish_attempted": False,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
            "completed_at": _now(),
        }
    )
    job.pop("last_partial_result", None)
    job.pop("error", None)
    _cancel_retry_timer(str(job.get("job_id") or ""))
    return codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        job,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )


def _retry_delay_seconds(retry_count: int, job_id: str = "") -> int:
    index = max(0, min(int(retry_count or 1) - 1, len(RETRY_DELAYS_SECONDS) - 1))
    return int(RETRY_DELAYS_SECONDS[index])


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
    wake_at = float(job.get("next_retry_at_epoch") or 0.0)
    deadline = _job_deadline_epoch(job)
    if deadline > 0.0:
        wake_at = min(wake_at, deadline)
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
        _complete_retry_limit(job)
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
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        job,
        expected_lease_generation=_lease_generation(job),
    )
    if _lease_generation(saved) == _lease_generation(job) and str(saved.get("status") or "") == "queued":
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
    sources = _context_evidence_sources(context)
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
        "answer": str(answer or ""),
        "error": str(error or "").strip()[:1000],
    }


def _pending_research_gaps(job: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    partial = job.get("last_partial_result") if isinstance(job.get("last_partial_result"), dict) else {}
    matrix = list(partial.get("evidence_status") or job.get("evidence_status") or [])
    confirmed_markers: set[str] = set()
    for row in matrix:
        if not isinstance(row, dict):
            continue
        values = (row.get("intent"), row.get("id"), row.get("required_evidence"))
        if str(row.get("status") or "") == "confirmed":
            confirmed_markers.update(_normal(value) for value in values if _normal(value))
            continue
        text = str(row.get("required_evidence") or row.get("intent") or row.get("id") or "").strip()[:160]
        if text and text not in gaps:
            gaps.append(text)
    for entry in reversed(list(job.get("research_history") or [])[-6:]):
        if not isinstance(entry, dict):
            continue
        for value in entry.get("missing_fields") or []:
            text = str(value or "").strip()[:160]
            if text and _normal(text) not in confirmed_markers and text not in gaps:
                gaps.append(text)
    context = partial.get("contexto") if isinstance(partial.get("contexto"), dict) else {}
    envelope = context.get("evidence_envelope") if isinstance(context.get("evidence_envelope"), dict) else {}
    for value in envelope.get("gaps") or []:
        text = str(value or "").strip()[:160]
        if text and text != "coverage_incomplete" and _normal(text) not in confirmed_markers and text not in gaps:
            gaps.append(text)
    return gaps[:16]


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
        info_base,
        client_id,
        current,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )
    if not _saved_by_same_lease(saved, job, status="cancelled"):
        return saved
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
            logger.warning("[PPV CODEX] evento=cancelar_plano_legado status=erro")
    return saved


def _safe_insufficient_draft(answer: Any, context: Any) -> bool:
    """Accept a safe informative draft; questions are optional and bounded."""

    text = str(answer or "").strip()
    normalized = _normal(text)
    if not text or len(text) > 2000 or text.count("?") > 2:
        return False
    diagnostic = _diagnostic_result(context if isinstance(context, dict) else {})
    category = str(diagnostic.get("category") or "").strip().lower()
    if category and category != "compatibility":
        return diagnostic.get("validation_ok") is True
    analysis = (
        diagnostic.get("compatibility_analysis")
        if isinstance(diagnostic.get("compatibility_analysis"), dict)
        else {}
    )
    decision = _normal(analysis.get("decision"))
    if decision and decision != "insufficient":
        return False
    prohibited_requests = (
        "foto", "imagem", "anexo", "chassi", " vin ", "mecanico", "mecânico", "oficina",
    )
    padded = f" {normalized} "
    if any(marker in padded for marker in prohibited_requests):
        return False
    conditional = normalized.replace("se serve", "").replace("se e compativel", "")
    unsupported_assertions = (
        "sim, serve", "sim serve", "serve perfeitamente", "e compativel",
        "nao serve", "nao e compativel", "pode usar", "nao pode usar", "garantimos",
        "confirma que serve", "confirma compatibilidade", "compatibilidade confirmada",
    )
    if any(marker in conditional for marker in unsupported_assertions):
        return False
    if re.search(r"\bserve\s+(?:no|na|para|em|o|a|seu|sua|esse|essa|neste|nesta)\b", conditional):
        return False
    request_markers = (
        "informe", "informar", "confirme", "confirmar", "qual ", "quais ",
        "precisamos do", "precisamos da", "envie o codigo", "envie a medida",
    )
    if not any(marker in normalized for marker in request_markers):
        return True
    requested_fields = 0
    for match in re.finditer(
        r"(?:informe|confirme|qual|quais|precisamos d[oa]|envie)\s+([^?.!]+)",
        normalized,
    ):
        parts = [
            part.strip(" ,;:")
            for part in re.split(r"\s*,\s*|\s+e\s+", match.group(1))
            if part.strip(" ,;:")
        ]
        requested_fields += max(1, len(parts))
    return requested_fields <= 2


def _continuation_safe_partial_draft(job: dict[str, Any], context: Any) -> str:
    """Build a deterministic conditional draft from one positive application segment."""

    data = context if isinstance(context, dict) else {}
    intent = (
        data.get("intencao_atendimento")
        if isinstance(data.get("intencao_atendimento"), dict)
        else {}
    )
    continuity = intent.get("continuidade") if isinstance(intent.get("continuidade"), dict) else {}
    categories = intent.get("categorias") if isinstance(intent.get("categorias"), list) else []
    if (
        str(continuity.get("tipo") or "") != "continuacao"
        or continuity.get("herdou_historico") is not True
    ):
        return ""
    if str(intent.get("categoria") or "") != "compatibility" and "compatibility" not in categories:
        return ""
    question_value = data.get("pergunta") or data.get("question") or ""
    current_text = str(
        question_value.get("text") if isinstance(question_value, dict) else question_value
        or ""
    )
    canonical_history = (
        data.get("buyer_question_chat")
        if isinstance(data.get("buyer_question_chat"), list)
        else data.get("history") if isinstance(data.get("history"), list) else []
    )
    if _fallback_context_blocked_by_safety({
        "question_text": current_text,
        "history": canonical_history,
        "classification": intent,
    }):
        return ""
    diagnostic = _diagnostic_result(data)
    analysis = (
        diagnostic.get("compatibility_analysis")
        if isinstance(diagnostic.get("compatibility_analysis"), dict)
        else {}
    )
    if _normal(analysis.get("decision")) != "insufficient":
        return ""
    description = str(data.get("descricao") or "")
    listing = data.get("item") or data.get("anuncio")
    listing = listing if isinstance(listing, dict) else {}
    compatibility = (
        intent.get("compatibilidade")
        if isinstance(intent.get("compatibilidade"), dict)
        else {}
    )
    target_text = str(compatibility.get("target_item") or "").strip()
    listing_item = {
        "title": listing.get("title") or data.get("titulo") or "",
        "description": description,
    }
    details = _fallback_application_details(
        listing_item,
        description,
        target_text,
    )
    if not details:
        return ""
    model, target_years, _codes = details
    buyer_texts = [current_text]
    buyer_texts.extend(
        str(event.get("text") or "")
        for event in canonical_history
        if isinstance(event, dict)
        and _normal(event.get("role") or event.get("from_role")) not in {"seller", "loja", "store"}
    )
    if not any(
        _fallback_buyer_supports_target(
            listing_item,
            description,
            target_text,
            buyer_text,
        )
        for buyer_text in buyer_texts
    ):
        return ""
    if _fallback_current_target_override(current_text):
        current_details = _fallback_application_details(
            listing_item,
            description,
            current_text,
        )
        if not current_details:
            return ""
        current_model, current_years, _current_codes = current_details
        if _normal(current_model) != _normal(model) or current_years != target_years:
            return ""
        current_qualifiers = _fallback_qualifiers(current_text)
        target_qualifiers = _fallback_qualifiers(target_text)
        if any(
            values and set(target_qualifiers.get(category) or set()) != set(values)
            for category, values in current_qualifiers.items()
        ):
            return ""

    safe_context = {
        "question_text": target_text,
        "history": [],
        "item": listing_item,
        "classification": {
            "categoria": "compatibility",
            "categorias": ["compatibility"],
            "continuidade": {"tipo": "independente", "herdou_historico": False},
        },
    }
    draft = _contextual_compatibility_fallback(job, safe_context)
    if not draft or not _safe_insufficient_draft(draft, data):
        return ""
    return draft


def _complete_with_best_available(
    job: dict[str, Any],
    *,
    answer: str = "",
    context: Optional[dict[str, Any]] = None,
    matrix: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    draft_source: str = "",
    completion_reason: str = "evidence_insufficient_after_retry_limit",
    deadline_reached: bool = True,
    empty_completion_reason: str = "ai_response_unavailable",
    empty_warning: str = "A IA nao concluiu a resposta; foi gerado um rascunho seguro editavel.",
) -> dict[str, Any]:
    """Finish a bounded research job while preserving any nonempty AI draft."""

    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    job_id = str(job.get("job_id") or "")
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, job_id
    )
    current = dict(latest) if isinstance(latest, dict) else dict(job)
    if (
        isinstance(latest, dict)
        and _lease_generation(job) > 0
        and _lease_generation(current) != _lease_generation(job)
    ):
        return current
    for counter in ("attempt_count", "evidence_attempt_count", "operational_failure_count"):
        current[counter] = max(
            int(current.get(counter) or 0),
            int(job.get(counter) or 0),
        )
    if _canonical_task_type(current.get("task_type")) == TASK_TYPE_POST_SALE:
        return _cancel_post_sale_job(current)
    if current.get("cancel_requested"):
        current.update({"status": "cancelled", "agent_state": "cancelado", "current_step": "responder"})
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)
    if str(current.get("status") or "") == "completed" and isinstance(current.get("result"), dict):
        return current
    partial = current.get("last_partial_result") if isinstance(current.get("last_partial_result"), dict) else {}
    current_answer = str(answer or "")
    final_answer = current_answer if current_answer.strip() else str(partial.get("resposta") or "")
    if not final_answer.strip():
        for entry in reversed(list(current.get("research_history") or [])):
            if isinstance(entry, dict) and str(entry.get("answer") or "").strip():
                final_answer = str(entry.get("answer") or "")
                break
    if not final_answer.strip():
        if _public_classification_missing(current):
            return _complete_without_draft(
                current,
                warning=(
                    "A classificacao estruturada ficou indisponivel; foi gerado um rascunho neutro com as informacoes disponiveis."
                ),
                completion_reason="ai_classification_unavailable",
            )
        return _complete_without_draft(
            current,
            warning=empty_warning,
            completion_reason=empty_completion_reason,
        )
    if completion_reason == "ai_response_preserved_unvalidated":
        current["operational_failure_count"] = 0

    final_context = context if isinstance(context, dict) and context else partial.get("contexto")
    if not isinstance(final_context, dict):
        final_context = {}
    final_matrix = list(matrix or partial.get("evidence_status") or current.get("evidence_status") or [])
    final_warnings = _unique_warnings(
        warnings,
        partial.get("warnings"),
        current.get("warnings"),
        [RESEARCH_DEADLINE_WARNING] if deadline_reached else [],
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
        "completion_reason": completion_reason,
        "review_required": False,
        "draft_source": str(
            draft_source
            or partial.get("draft_source")
            or current.get("draft_source")
            or "ai"
        ),
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
            "retry_policy": _task_retry_policy(current.get("task_type")),
            "deadline_seconds": _task_deadline_seconds(current.get("task_type")),
            "deadline_at_epoch": _job_deadline_epoch(current),
            "deadline_reached": bool(deadline_reached),
            "completed_with_partial": True,
            "completion_reason": completion_reason,
            "review_required": False,
            "draft_source": result["draft_source"],
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
            "message": (
                "Evidencia insuficiente apos as pesquisas; rascunho gerado com os fatos disponiveis."
                if deadline_reached
                else "A tentativa mais recente foi rejeitada; o ultimo rascunho validado foi preservado."
            ),
        }
    )
    current["agent_steps"] = history[-60:]
    _cancel_retry_timer(job_id)
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        current,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )
    if not _saved_by_same_lease(saved, job, status="completed", proposal_hash=proposal_hash):
        return saved
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
                    "completion_reason": completion_reason,
                },
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=concluir_plano_parcial status=erro")
    return saved


def _complete_retry_limit(job: dict[str, Any]) -> dict[str, Any]:
    if str(job.get("retry_kind") or "") == "evidence":
        return _complete_with_best_available(job)
    return _complete_without_draft(
        job,
        warning="O limite operacional foi atingido; foi gerado um rascunho neutro editavel.",
        completion_reason="operational_retry_exhausted",
    )


def _persist_retry(
    job: dict[str, Any],
    *,
    answer: str = "",
    context: Optional[dict[str, Any]] = None,
    matrix: Optional[list[dict[str, Any]]] = None,
    warnings: Optional[list[str]] = None,
    error: str = "",
    immediate: bool = False,
    retry_kind: str = "evidence",
) -> dict[str, Any]:
    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        info_base, client_id, str(job.get("job_id") or "")
    )
    current = dict(latest) if isinstance(latest, dict) else dict(job)
    if (
        isinstance(latest, dict)
        and _lease_generation(job) > 0
        and _lease_generation(latest) != _lease_generation(job)
    ):
        return latest
    if (
        isinstance(latest, dict)
        and str(latest.get("status") or "") == "running"
        and str(latest.get("lease_owner") or "")
        and str(latest.get("lease_owner") or "") != _WORKER_ID
    ):
        return latest
    if str(job.get("thread_id") or "").strip():
        current["thread_id"] = str(job.get("thread_id") or "").strip()
    current["attempt_count"] = max(
        int(current.get("attempt_count") or 0),
        int(job.get("attempt_count") or 0),
    )
    current["operational_failure_count"] = (
        max(
            int(current.get("operational_failure_count") or 0),
            int(job.get("operational_failure_count") or 0),
        )
        if retry_kind == "operational"
        else max(0, int(job.get("operational_failure_count") or 0))
    )
    current["evidence_attempt_count"] = max(
        int(current.get("evidence_attempt_count") or 0),
        int(job.get("evidence_attempt_count") or 0),
    )
    if current.get("cancel_requested"):
        current.update({"status": "cancelled", "agent_state": "cancelado", "current_step": "responder"})
        return codex_assistant_storage.codex_assistant_customer_reply_job_save(info_base, client_id, current)

    previous_partial = (
        current.get("last_partial_result")
        if isinstance(current.get("last_partial_result"), dict)
        else {}
    )
    merged_context = _merge_context_values(
        previous_partial.get("contexto") if isinstance(previous_partial.get("contexto"), dict) else {},
        context if isinstance(context, dict) else {},
    )
    merged_matrix = _merge_evidence_matrix(previous_partial.get("evidence_status"), matrix)
    merged_envelope = merged_context.get("evidence_envelope") if isinstance(merged_context, dict) else None
    if isinstance(merged_envelope, dict):
        confirmed_markers = {
            _normal(value)
            for row in merged_matrix
            if isinstance(row, dict) and str(row.get("status") or "") == "confirmed"
            for value in (row.get("intent"), row.get("id"), row.get("required_evidence"))
            if _normal(value)
        }
        merged_envelope["gaps"] = [
            gap for gap in (merged_envelope.get("gaps") or [])
            if _normal(gap) not in confirmed_markers
        ]
        if merged_matrix and all(str(row.get("status") or "") == "confirmed" for row in merged_matrix if isinstance(row, dict)):
            merged_envelope.update({
                "status": "completed",
                "gaps": [],
                "evidence_sufficient": True,
                "coverage_complete": True,
            })
        merged_context["evidence_envelope"] = normalize_evidence_envelope(merged_envelope).to_dict()
    history = list(current.get("research_history") or [])
    history.append(
        _research_history_entry(
            job,
            answer=answer,
            context=merged_context,
            matrix=merged_matrix,
            warnings=warnings,
            error=error,
        )
    )
    retry_count = max(0, int(current.get("retry_count") or 0)) + 1
    partial_result = {
        "resposta": (
            str(answer or "")
            if str(answer or "").strip()
            else str(previous_partial.get("resposta") or "")
        ),
        "contexto": merged_context,
        "evidence_status": merged_matrix,
        "data_sufficient": False,
        "warnings": _unique_warnings(previous_partial.get("warnings"), warnings),
        "publish_attempted": False,
        "draft_source": str(previous_partial.get("draft_source") or ("ai" if answer else "")),
    }
    current.update(
        {
            "research_history": history[-RETRY_HISTORY_LIMIT:],
            "last_partial_result": partial_result,
            "evidence_status": merged_matrix,
            "warnings": _unique_warnings(current.get("warnings"), warnings),
            "retry_count": retry_count,
            "retry_kind": retry_kind,
            "last_attempt_completed_at": _now(),
        }
    )
    deadline = _job_deadline_epoch(current)
    if _job_deadline_expired(current) or int(current.get("attempt_count") or 0) >= MAX_TOTAL_ATTEMPTS:
        if retry_kind == "evidence":
            return _complete_with_best_available(
                current,
                answer=answer,
                context=context,
                matrix=matrix,
                warnings=warnings,
            )
        return _complete_without_draft(
            current,
            warning="O limite operacional foi atingido; foi gerado um rascunho neutro editavel.",
            completion_reason="operational_retry_exhausted",
        )
    retry_index = (
        int(current.get("operational_failure_count") or 1)
        if retry_kind == "operational"
        else 1
    )
    delay = 0 if immediate else _retry_delay_seconds(retry_index, str(current.get("job_id") or ""))
    remaining = max(0.0, deadline - time.time()) if deadline > 0.0 else 0.0
    if not immediate and deadline > 0.0:
        delay = min(delay, max(1, int(remaining)))
    current.update(
        {
            "status": "waiting_retry",
            "agent_state": "pesquisando",
            "current_step": "consultar",
            "retry_policy": _task_retry_policy(current.get("task_type")),
            "retry_kind": retry_kind,
            "retry_reason": str(error or "; ".join(warnings or []) or "evidencia_insuficiente")[:1000],
            "next_retry_at_epoch": time.time() + delay,
            "next_retry_delay_seconds": delay,
            "deadline_seconds": _task_deadline_seconds(current.get("task_type")),
            "deadline_at_epoch": deadline,
            "lease_owner": "",
            "lease_expires_ts": 0.0,
        }
    )
    current.pop("result", None)
    current.pop("completed_at", None)
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        current,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )
    if not _saved_by_same_lease(saved, job, status="waiting_retry"):
        return saved
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
        and str(job.get("queue_policy_version") or "") == QUEUE_POLICY_VERSION
    )


def _job_terminal(job: Any) -> bool:
    return bool(
        isinstance(job, dict)
        and str(job.get("status") or "") in TERMINAL_STATUSES
    )


def _quarantine_outdated_job(
    job: dict[str, Any], *, client_id: Optional[str] = None
) -> dict[str, Any]:
    """Make a pre-current-contract job terminal and remove its actionable draft."""

    info_base = _runtime_info_base()
    scoped_client_id = str(client_id or job.get("client_id") or "default")
    previous_result = job.get("result") if isinstance(job.get("result"), dict) else {}
    queue_policy_outdated = str(job.get("queue_policy_version") or "") != QUEUE_POLICY_VERSION
    completion_reason = "queue_policy_outdated" if queue_policy_outdated else "contract_outdated"
    warning = (
        "Tarefa criada com contrato ou politica de fila anterior e colocada em quarentena. "
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
        "completion_reason": completion_reason,
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
            "completion_reason": completion_reason,
            "review_required": True,
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
    _discard_vin_envelope_family(str(job.get("job_id") or ""))
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
        logger.warning("[PPV CODEX] evento=recuperar_jobs status=erro")


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
            ("buyer_question_chat", context.get("buyer_question_chat")),
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
        elif intent == "post_sale":
            # A post-sale classification inside the public-question surface is
            # allowed to produce a draft and is not a missing classification.
            confirmed = bool(validation_ok)
            row["status"] = "confirmed" if confirmed else ("partial" if answer.strip() else "no_evidence")
            row["confidence"] = float(diagnostic.get("confidence") or (0.65 if validation_ok else 0.35))
            row["sources"] = []
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
        warnings.append("O rascunho responde somente com as informacoes disponiveis.")
    return matrix, sufficient, list(dict.fromkeys(warnings))[:12]


def _friendly_retry_reason(reason: Any) -> str:
    normalized = _normal(reason)
    if "evidencia" in normalized or "coverage" in normalized:
        return "Ainda faltam evidencias confiaveis para responder com seguranca."
    if "orientacao" in normalized:
        return "A orientacao foi atualizada e a resposta sera revisada."
    if any(marker in normalized for marker in ("429", "timeout", "connection", "indispon")):
        return "O servico ficou temporariamente indisponivel."
    return "A tentativa ainda nao produziu uma resposta valida."


def _status_message(job: dict[str, Any], *, queue_position: int = 0) -> str:
    status = str(job.get("status") or "queued")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    attempt = max(0, int(job.get("attempt_count") or 0))
    if status == "waiting_retry":
        remaining = max(0, int(float(job.get("next_retry_at_epoch") or 0.0) - time.time() + 0.999))
        return f"{_friendly_retry_reason(job.get('retry_reason'))} Nova tentativa em {remaining}s."
    if status == "queued":
        suffix = f" Posicao na fila: {queue_position}." if queue_position else ""
        prefix = (
            "Solicitacao manual prioritaria aguardando execucao."
            if str(job.get("queue_origin") or "") == QUEUE_ORIGIN_MANUAL
            else "Pesquisa automatica aguardando execucao."
        )
        return f"{prefix}{suffix}"
    if status == "running":
        step = str(job.get("current_step") or "consultar")
        labels = {
            "entender": "Entendendo a pergunta",
            "consultar": "Consultando fontes e pesquisando lacunas",
            "validar": "Validando as evidencias encontradas",
        }
        return f"{labels.get(step, 'Pesquisa em andamento')}. Tentativa {max(1, attempt)}."
    if status == "completed":
        if job.get("blocked_without_draft") or result.get("blocked_without_draft"):
            return "A pesquisa terminou e disponibilizou uma resposta alternativa."
        if job.get("completed_with_partial") or result.get("completed_with_partial"):
            return "Rascunho gerado com as informacoes disponiveis."
        return "Resposta gerada e pronta para uso."
    if status == "cancelled":
        return "Pesquisa cancelada pelo usuario."
    return "Acompanhando a pesquisa."


def _public_job(job: dict[str, Any], *, queue_position: int = 0) -> dict[str, Any]:
    result = dict(job.get("result")) if isinstance(job.get("result"), dict) else {}
    draft_source = str(result.get("draft_source") or job.get("draft_source") or "").strip()
    if not draft_source and str(result.get("resposta") or "").strip():
        draft_source = "ai"
    if draft_source:
        result["draft_source"] = draft_source
    blocked_without_draft = bool(
        result.get("blocked_without_draft")
        or job.get("blocked_without_draft")
        or job.get("contract_quarantined")
    )
    next_retry_at = float(job.get("next_retry_at_epoch") or 0.0)
    next_retry_in = max(0, int(next_retry_at - time.time() + 0.999)) if next_retry_at else 0
    status = str(job.get("status") or "queued")
    try:
        queue_metrics = codex_assistant_storage.codex_assistant_customer_reply_queue_metrics(
            _runtime_info_base(),
            str(job.get("client_id") or "default"),
        )
    except Exception:
        queue_metrics = {"queued": 0, "running": 0, "waiting_retry": 0, "active": 0}
    completion_reason = str(result.get("completion_reason") or job.get("completion_reason") or "")
    if completion_reason:
        result["completion_reason"] = completion_reason
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
        "queue_policy_version": str(job.get("queue_policy_version") or ""),
        "queue_origin": str(job.get("queue_origin") or "legacy"),
        "queue_priority": max(0, int(job.get("queue_priority") or 0)),
        "status": status,
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
        "retry_policy": str(job.get("retry_policy") or _task_retry_policy(job.get("task_type"))),
        "retry_count": max(0, int(job.get("retry_count") or 0)),
        "retry_reason": str(job.get("retry_reason") or ""),
        "next_retry_at_epoch": next_retry_at,
        "next_retry_in_seconds": next_retry_in,
        "deadline_seconds": int(
            job.get("deadline_seconds")
            if job.get("deadline_seconds") is not None
            else _task_deadline_seconds(job.get("task_type"))
        ),
        "deadline_at_epoch": float(job.get("deadline_at_epoch") or 0.0),
        "deadline_reached": bool(job.get("deadline_reached")),
        "completed_with_partial": bool(result.get("completed_with_partial") or job.get("completed_with_partial")),
        "blocked_without_draft": blocked_without_draft,
        "contract_quarantined": bool(job.get("contract_quarantined")),
        "completion_reason": completion_reason,
        "draft_source": draft_source,
        "review_required": bool(result.get("review_required") or job.get("review_required")),
        "research_history": list(job.get("research_history") or [])[-RETRY_HISTORY_LIMIT:],
        "queue_position": max(0, int(queue_position or 0)),
        "queue_total": max(0, int(queue_metrics.get("queued") or 0)),
        "running_total": max(0, int(queue_metrics.get("running") or 0)),
        "waiting_retry_total": max(0, int(queue_metrics.get("waiting_retry") or 0)),
        "attempt_count": max(0, int(job.get("attempt_count") or 0)),
        "evidence_attempt_count": max(0, int(job.get("evidence_attempt_count") or 0)),
        "evidence_attempt_limit": max(1, int(job.get("evidence_attempt_limit") or MAX_EVIDENCE_ATTEMPTS)),
        "operational_failure_count": max(0, int(job.get("operational_failure_count") or 0)),
        "operational_failure_limit": max(1, int(job.get("operational_failure_limit") or MAX_OPERATIONAL_FAILURES)),
        "total_attempt_limit": max(1, int(job.get("total_attempt_limit") or MAX_TOTAL_ATTEMPTS)),
        "last_activity_at": str(
            job.get("updated_at")
            or job.get("heartbeat_at")
            or job.get("last_attempt_completed_at")
            or job.get("created_at")
            or ""
        ),
        "can_cancel": bool(status in ACTIVE_STATUSES and not job.get("cancel_requested")),
        "status_message": _status_message(job, queue_position=queue_position),
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
    }


def _queue_position(info_base: str, client_id: str, job_id: str) -> int:
    offset = 0
    while True:
        queued = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
            info_base,
            client_id,
            statuses=["queued"],
            limit=500,
            offset=offset,
            priority_order=True,
        )
        for page_index, item in enumerate(queued, start=1):
            if str(item.get("job_id") or "") == str(job_id or ""):
                return offset + page_index
        if len(queued) < 500:
            break
        offset += len(queued)
    return 0


def automation_queue_admission(client_id: str, store: str) -> dict[str, Any]:
    info_base = _runtime_info_base()
    tenant_metrics = codex_assistant_storage.codex_assistant_customer_reply_queue_metrics(
        info_base,
        client_id,
        origin=QUEUE_ORIGIN_AUTOMATION,
    )
    store_metrics = codex_assistant_storage.codex_assistant_customer_reply_queue_metrics(
        info_base,
        client_id,
        origin=QUEUE_ORIGIN_AUTOMATION,
        store=str(store or ""),
    )
    tenant_active = max(0, int(tenant_metrics.get("active") or 0))
    store_active = max(0, int(store_metrics.get("active") or 0))
    allowed = bool(
        tenant_active < AUTOMATION_QUEUE_TOTAL_LIMIT
        and store_active < AUTOMATION_QUEUE_PER_STORE_LIMIT
    )
    return {
        "allowed": allowed,
        "queue_saturated": not allowed,
        "active_automatic": tenant_active,
        "active_automatic_store": store_active,
        "limit_total": AUTOMATION_QUEUE_TOTAL_LIMIT,
        "limit_per_store": AUTOMATION_QUEUE_PER_STORE_LIMIT,
    }


def automation_terminal_blocker(job: Any) -> str:
    """Prevent periodic polls from recreating a current terminal review job."""

    current = job if isinstance(job, dict) else {}
    result = current.get("result") if isinstance(current.get("result"), dict) else {}
    status = str(current.get("status") or "").strip().lower()
    completion_reason = str(
        result.get("completion_reason") or current.get("completion_reason") or ""
    ).strip().lower()
    blocked_without_draft = bool(
        result.get("blocked_without_draft")
        or current.get("blocked_without_draft")
    )
    if status not in TERMINAL_STATUSES or not blocked_without_draft:
        return ""
    if current.get("contract_quarantined") or completion_reason in {
        "queue_policy_outdated",
        "contract_outdated",
    }:
        return ""
    return "terminal_review_required"


def _request_generation(job: Any) -> int:
    try:
        return max(1, int((job or {}).get("request_generation") or 1))
    except (AttributeError, TypeError, ValueError):
        return 1


def _vin_envelope_key(job_id: object, request_generation: object) -> str:
    safe_job_id = str(job_id or "").strip()
    try:
        generation = max(1, int(request_generation or 1))
    except (TypeError, ValueError):
        generation = 1
    return f"{safe_job_id}:{generation}" if safe_job_id else ""


def _discard_vin_envelope_family(job_id: object) -> None:
    DEFAULT_VIN_ENVELOPE_STORE.discard_family(str(job_id or "").strip())


def _vehicle_identity_from_capture(
    job_id: str,
    capture_status: str,
    request_generation: int = 1,
) -> dict[str, str]:
    """Consume a VIN envelope once and expose only the allowlisted decoder contract."""

    if capture_status == "captured":
        transient_vin = DEFAULT_VIN_ENVELOPE_STORE.consume(
            _vin_envelope_key(job_id, request_generation)
        )
        if transient_vin:
            try:
                return VpicPublicVinDecoder().decode(transient_vin).as_dict()
            except Exception:
                return empty_vehicle_identity(
                    "unavailable",
                    reason="decoder_unavailable",
                ).as_dict()
        return empty_vehicle_identity(
            "unavailable",
            reason="transient_envelope_unavailable",
        ).as_dict()
    if capture_status == "ambiguous":
        return empty_vehicle_identity(
            "ambiguous",
            reason="multiple_vins",
        ).as_dict()
    if capture_status == "invalid":
        return empty_vehicle_identity(
            "invalid",
            reason="vin_invalid",
        ).as_dict()
    return {}


def _consume_vehicle_identity_for_worker(job: dict[str, Any]) -> dict[str, Any]:
    """Use only pre-queue safe facts; a worker never consumes or decodes a VIN."""

    current = dict(job or {})
    _discard_vin_envelope_family(str(current.get("job_id") or ""))
    if (
        str(current.get("status") or "") == "running"
        and str(current.get("client_id") or "").strip()
        and str(current.get("job_id") or "").strip()
    ):
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
            _runtime_info_base(),
            str(current.get("client_id") or "default"),
            str(current.get("job_id") or ""),
        )
        if (
            isinstance(latest, dict)
            and _request_generation(latest) >= _request_generation(current)
        ):
            current = dict(latest)
    for _attempt in range(8):
        existing = current.get("vehicle_identity")
        if isinstance(existing, dict) and existing:
            return current
        generation = _request_generation(current)
        capture_status = str(
            current.get("vehicle_identity_capture_status") or "absent"
        )
        if capture_status == "captured":
            facts = empty_vehicle_identity(
                "unavailable",
                reason="transient_envelope_unavailable",
            ).as_dict()
        elif capture_status == "ambiguous":
            facts = empty_vehicle_identity(
                "ambiguous",
                reason="multiple_vins",
            ).as_dict()
        elif capture_status == "invalid":
            facts = empty_vehicle_identity(
                "invalid",
                reason="vin_invalid",
            ).as_dict()
        else:
            facts = {}
        if not facts:
            return current
        candidate = dict(current)
        candidate["vehicle_identity"] = facts
        if (
            str(candidate.get("status") or "") != "running"
            or not str(candidate.get("client_id") or "").strip()
            or not str(candidate.get("lease_owner") or "").strip()
        ):
            return candidate
        saved = customer_reply_storage._codex_assistant_customer_reply_job_save_cas(
            _runtime_info_base(),
            str(candidate.get("client_id") or "default"),
            candidate,
            expected_lease_owner=_WORKER_ID,
            expected_lease_generation=_lease_generation(candidate),
            expected_request_generation=generation,
        )
        cas_applied = saved.pop("_request_generation_cas_applied", None)
        if not _saved_by_same_lease(saved, candidate, status="running"):
            raise _LeaseLost("Lease transferida durante a resolucao do chassi.")
        if cas_applied is not True or _request_generation(saved) != generation:
            current = dict(saved)
            continue
        persisted = saved.get("vehicle_identity") if isinstance(saved.get("vehicle_identity"), dict) else {}
        if persisted != facts:
            raise _LeaseLost("Identidade do veiculo nao foi persistida pela geracao atual.")
        return saved
    raise _LeaseLost("A geracao da solicitacao mudou repetidamente durante a resolucao do chassi.")


def _active_revision_candidate(
    current: dict[str, Any],
    *,
    request: dict[str, Any],
    task_type: str,
    queue_origin: str,
    queue_priority: int,
    capture_status: str,
    vehicle_identity: dict[str, str],
) -> dict[str, Any]:
    latest_request = (
        dict(current.get("request") or {})
        if isinstance(current.get("request"), dict)
        else {}
    )
    latest_request.update(dict(request or {}))
    candidate = dict(current)
    candidate.update(
        {
            "request": latest_request,
            "request_hash": _hash(latest_request),
            "subquestions": _initial_subquestions(task_type),
            "request_generation": _request_generation(current) + 1,
            "proposal_version": max(
                1, int(current.get("proposal_version") or 1)
            ) + 1,
            "restart_requested": True,
            "next_retry_at_epoch": 0.0,
            "retry_reason": "orientacao_do_operador_atualizada",
            "retry_count": 0,
            "retry_policy": _task_retry_policy(task_type),
            "deadline_seconds": _task_deadline_seconds(task_type),
            "deadline_at_epoch": (
                time.time() + _task_deadline_seconds(task_type)
                if _task_deadline_seconds(task_type) > 0
                else 0.0
            ),
        }
    )
    if capture_status != "absent":
        candidate["vehicle_identity_capture_status"] = capture_status
        candidate["vehicle_identity"] = dict(vehicle_identity)
    if (
        queue_origin == QUEUE_ORIGIN_MANUAL
        and str(current.get("queue_origin") or "") == QUEUE_ORIGIN_AUTOMATION
    ):
        candidate.update(
            {
                "queue_origin": QUEUE_ORIGIN_MANUAL,
                "queue_priority": queue_priority,
                "queue_policy_version": QUEUE_POLICY_VERSION,
            }
        )
    if str(candidate.get("status") or "") == "waiting_retry":
        candidate.update(
            {
                "status": "queued",
                "agent_state": "pesquisando",
                "current_step": "consultar",
            }
        )
    return candidate


def _persist_active_revision_with_cas(
    latest: dict[str, Any],
    *,
    info_base: str,
    client_id: str,
    event_subject_key: str,
    request: dict[str, Any],
    task_type: str,
    queue_origin: str,
    queue_priority: int,
    capture_status: str,
    vehicle_identity: dict[str, str],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Allocate one request generation atomically for every revision."""

    current = dict(latest)
    for _attempt in range(8):
        same_event = str(
            current.get("event_subject_key") or current.get("subject_key") or ""
        ) == event_subject_key
        if (
            str(current.get("status") or "") not in ACTIVE_STATUSES
            or not same_event
            or not _job_contract_current(current)
        ):
            return None, current
        expected_generation = _request_generation(current)
        candidate = _active_revision_candidate(
            current,
            request=request,
            task_type=task_type,
            queue_origin=queue_origin,
            queue_priority=queue_priority,
            capture_status=capture_status,
            vehicle_identity=vehicle_identity,
        )
        saved = customer_reply_storage._codex_assistant_customer_reply_job_save_cas(
            info_base,
            client_id,
            candidate,
            expected_lease_generation=_lease_generation(current),
            expected_request_generation=expected_generation,
        )
        cas_applied = saved.pop("_request_generation_cas_applied", None)
        if cas_applied is True:
            if _request_generation(saved) != expected_generation + 1:
                raise RuntimeError("Geracao VIN persistida fora da revisao reservada.")
            if capture_status != "absent" and saved.get("vehicle_identity") != vehicle_identity:
                raise RuntimeError("Identidade veicular persistida na geracao incorreta.")
            return saved, saved
        current = dict(saved)
    raise RuntimeError("Concorrencia excessiva ao reservar a geracao da revisao VIN.")


def _create_job_unserialized(
    *,
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: dict[str, Any],
    channel: str = "app",
    created_by: str = "module_user",
) -> dict[str, Any]:
    queue_origin = (
        QUEUE_ORIGIN_AUTOMATION
        if str(created_by or "").strip() == "perguntas_automacao"
        else QUEUE_ORIGIN_MANUAL
    )
    queue_priority = (
        QUEUE_PRIORITY_AUTOMATION
        if queue_origin == QUEUE_ORIGIN_AUTOMATION
        else QUEUE_PRIORITY_MANUAL
    )
    task_type = _canonical_task_type(task_type)
    if task_type not in TASK_TYPES:
        raise ValueError("Tipo de tarefa de atendimento inválido.")
    if task_type == TASK_TYPE_POST_SALE:
        raise PermissionError("Sugestoes do Black Jhon estao desativadas no pos-venda.")
    if not str(store or "").strip() or not str(subject_key or "").strip():
        raise ValueError("Loja e identificação da conversa são obrigatórias.")
    for field_name, value in (
        ("cliente", client_id),
        ("loja", store),
        ("conversa", subject_key),
        ("canal", channel),
        ("criador", created_by),
    ):
        if contains_vin_like_identifier(value):
            raise ValueError(
                f"O campo operacional {field_name} não pode conter chassi/VIN."
            )
    job_id = uuid.uuid4().hex
    vin_capture = capture_vin_payload(
        dict(request or {}),
        envelope_key=_vin_envelope_key(job_id, 1),
    )
    request = (
        dict(vin_capture.sanitized_payload)
        if isinstance(vin_capture.sanitized_payload, dict)
        else {}
    )
    vin_capture_status = str(vin_capture.status or "absent")
    vehicle_identity = _vehicle_identity_from_capture(
        job_id,
        vin_capture_status,
        1,
    )
    _discard_vin_envelope_family(job_id)
    try:
        request_hash = _hash(request)
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
    except Exception:
        _discard_vin_envelope_family(job_id)
        raise
    latest_result = latest.get("result") if isinstance(latest, dict) and isinstance(latest.get("result"), dict) else {}
    operator_revision_requested = bool(
        str(request.get("resposta_atual") or request.get("orientacao_usuario") or "").strip()
    )
    same_event = bool(
        isinstance(latest, dict)
        and str(latest.get("event_subject_key") or latest.get("subject_key") or "") == event_subject_key
    )
    revision_requested = bool(
        operator_revision_requested
        or vin_capture_status != "absent"
    )
    proposal_version = int(latest_result.get("proposal_version") or latest.get("proposal_version") or 0) + 1 if latest else 1
    if (
        not revision_requested
        and isinstance(latest, dict)
        and str(latest.get("status") or "") == "completed"
        and latest_result.get("data_sufficient") is True
        and str(latest.get("request_hash") or "") == request_hash
        and _job_contract_current(latest)
        and vin_capture_status == "absent"
    ):
        return _public_job(latest)
    if (
        isinstance(latest, dict)
        and same_event
        and str(latest.get("status") or "") == "completed"
        and str(latest.get("agent_state") or "") == "aguardando_aprovacao"
        and not codex_assistant_storage.codex_assistant_customer_reply_job_has_transient(
            info_base, client_id, str(latest.get("job_id") or "")
        )
    ):
        latest.update(
            {
                "status": "completed",
                "agent_state": "concluido",
                "current_step": "responder",
                "draft_expired": True,
                "blocked_without_draft": False,
                "review_required": False,
                "completion_reason": "draft_expired",
                "lease_owner": "",
                "lease_expires_ts": 0.0,
            }
        )
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base,
            client_id,
            latest,
            expected_lease_generation=_lease_generation(latest),
        )
        latest_result = {}
    if (
        isinstance(latest, dict)
        and str(latest.get("status") or "") in ACTIVE_STATUSES
        and same_event
        and _job_contract_current(latest)
    ):
        if revision_requested:
            revised, latest = _persist_active_revision_with_cas(
                latest,
                info_base=info_base,
                client_id=client_id,
                event_subject_key=event_subject_key,
                request=request,
                task_type=task_type,
                queue_origin=queue_origin,
                queue_priority=queue_priority,
                capture_status=vin_capture_status,
                vehicle_identity=vehicle_identity,
            )
            if revised is not None:
                _cancel_retry_timer(str(revised.get("job_id") or ""))
                _schedule(revised)
                return _public_job(
                    revised,
                    queue_position=_queue_position(
                        info_base,
                        client_id,
                        str(revised.get("job_id") or ""),
                    ),
                )
        else:
            if (
                queue_origin == QUEUE_ORIGIN_MANUAL
                and str(latest.get("queue_origin") or "") == QUEUE_ORIGIN_AUTOMATION
            ):
                latest.update(
                    {
                        "queue_origin": QUEUE_ORIGIN_MANUAL,
                        "queue_priority": queue_priority,
                        "queue_policy_version": QUEUE_POLICY_VERSION,
                    }
                )
                latest = codex_assistant_storage.codex_assistant_customer_reply_job_save(
                    info_base,
                    client_id,
                    latest,
                    expected_lease_generation=_lease_generation(latest),
                )
                if str(latest.get("status") or "") == "queued":
                    _schedule(latest)
                elif str(latest.get("status") or "") == "waiting_retry":
                    _schedule_retry_timer(latest)
            if _job_deadline_expired(latest):
                latest = _complete_retry_limit(latest)
            return _public_job(
                latest,
                queue_position=_queue_position(
                    info_base,
                    client_id,
                    str(latest.get("job_id") or ""),
                ),
            )
    idempotency_key = _hash(
        {
            "client": client_id,
            "type": task_type,
            "store": store,
            "subject": event_subject_key,
            "request": request,
            "origin": queue_origin,
            "bucket": int(time.time() // 5),
        }
    )
    existing = (
        codex_assistant_storage.codex_assistant_customer_reply_job_get(
            info_base, client_id, idempotency_key=idempotency_key
        )
        if vin_capture_status == "absent"
        else None
    )
    if isinstance(existing, dict):
        if _job_contract_current(existing):
            return _public_job(existing, queue_position=_queue_position(info_base, client_id, str(existing.get("job_id") or "")))
        _quarantine_outdated_job(existing, client_id=client_id)
    question = request.get("pergunta") if isinstance(request.get("pergunta"), dict) else {}
    question_id = str(question.get("id") or event_subject_key).strip()
    item = request.get("item") if isinstance(request.get("item"), dict) else {}
    item_id = str(question.get("item_id") or item.get("id") or "").strip()
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
        message=f"{task_type}:{event_subject_key}",
        mutable=True,
        idempotency_key=idempotency_key,
        guidance_applied=[],
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
        "question_id": question_id,
        "item_id": item_id,
        "scope_verifiers": scope_verifiers,
        "store": str(store),
        "client_id": str(client_id),
        "channel": str(channel or "app"),
        "created_by": str(created_by or "module_user"),
        "queue_origin": queue_origin,
        "queue_priority": queue_priority,
        "queue_policy_version": QUEUE_POLICY_VERSION,
        "status": "queued",
        "agent_state": "entendendo",
        "current_step": "entender",
        "agent_steps": [{"state": "entendendo", "at": _now(), "message": "Solicitação recebida."}],
        "subquestions": subquestions,
        "request": dict(request or {}),
        "request_hash": request_hash,
        "vehicle_identity_capture_status": vin_capture_status,
        "vehicle_identity": dict(vehicle_identity),
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
        "evidence_attempt_count": 0,
        "evidence_attempt_limit": MAX_EVIDENCE_ATTEMPTS,
        "operational_failure_limit": MAX_OPERATIONAL_FAILURES,
        "total_attempt_limit": MAX_TOTAL_ATTEMPTS,
        "first_started_at_epoch": 0.0,
        "execution_deadline_epoch": 0.0,
        "retry_count": 0,
        "retry_policy": _task_retry_policy(task_type),
        "deadline_seconds": _task_deadline_seconds(task_type),
        "deadline_at_epoch": 0.0,
        "research_history": [],
        "created_at": _now(),
    }
    save_limits = (
        {
            "max_origin_active": AUTOMATION_QUEUE_TOTAL_LIMIT,
            "max_origin_active_store": AUTOMATION_QUEUE_PER_STORE_LIMIT,
        }
        if queue_origin == QUEUE_ORIGIN_AUTOMATION
        else {}
    )
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        job,
        **save_limits,
    )
    if saved.get("queue_admission_blocked"):
        _discard_vin_envelope_family(job_id)
        try:
            codex_agent_runtime.transition_plan(
                info_base,
                client_id,
                str(plan.get("plan_id") or ""),
                "cancelado",
                current_step="entender",
                step_status="canceled",
                details={"reason": "queue_backpressure"},
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=encerrar_plano_backpressure status=erro")
        admission = automation_queue_admission(client_id, str(store))
        return {
            "job_id": job_id,
            "status": "deferred",
            "queued": False,
            "queue_saturated": True,
            "blocked_reason": "queue_backpressure",
            "queue_origin": queue_origin,
            "queue_policy_version": QUEUE_POLICY_VERSION,
            "queue_backpressure": admission,
        }
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
            "queue_origin": queue_origin,
            "queue_policy_version": QUEUE_POLICY_VERSION,
        },
    )
    _schedule(saved)
    return _public_job(saved, queue_position=_queue_position(info_base, client_id, job_id))


def _subject_has_persisted_job(
    client_id: str,
    task_type: str,
    store: str,
    subject_key: str,
    request: dict[str, Any],
) -> bool:
    """Read the durable subject lane without retaining or decoding a VIN."""

    sanitized = capture_vin_payload(
        dict(request or {}),
        retain_envelope=False,
    ).sanitized_payload
    safe_request = dict(sanitized) if isinstance(sanitized, dict) else {}
    event_subject_key = str(subject_key or "").strip()
    conversation_subject_key, _scope = _conversation_subject_key(
        task_type,
        event_subject_key,
        safe_request,
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
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_latest(
            info_base,
            client_id,
            task_type="question",
            store=str(store),
            subject_key=event_subject_key,
        )
    return isinstance(latest, dict)


def _acquire_initial_creation_lock(
    key: tuple[str, str, str, str],
) -> threading.Lock:
    with _INITIAL_CREATION_GUARD:
        lock, users = _INITIAL_CREATION_LOCKS.get(key, (threading.Lock(), 0))
        _INITIAL_CREATION_LOCKS[key] = (lock, users + 1)
    lock.acquire()
    return lock


def _release_initial_creation_lock(
    key: tuple[str, str, str, str],
    lock: threading.Lock,
) -> None:
    lock.release()
    with _INITIAL_CREATION_GUARD:
        current_lock, users = _INITIAL_CREATION_LOCKS.get(key, (lock, 1))
        if current_lock is lock and users <= 1:
            _INITIAL_CREATION_LOCKS.pop(key, None)
        elif current_lock is lock:
            _INITIAL_CREATION_LOCKS[key] = (lock, users - 1)


@contextlib.contextmanager
def _initial_creation_process_lock(
    key: tuple[str, str, str, str],
    *,
    timeout: float = 75.0,
):
    """Serialize first creation across local backend processes for one subject."""

    lock_root = Path(_runtime_info_base()) / ".codex_runtime" / "ppv-initial-creation"
    lock_root.mkdir(parents=True, exist_ok=True)
    stripe = int(_hash(list(key))[:8], 16) % _INITIAL_CREATION_PROCESS_STRIPES
    lock_path = lock_root / f"stripe-{stripe:03d}.lock"
    deadline = time.monotonic() + max(0.0, float(timeout))
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            def try_lock() -> bool:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        return False
                    raise
                return True

            def unlock() -> None:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def try_lock() -> bool:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if isinstance(error, BlockingIOError) or error.errno in {
                        errno.EACCES,
                        errno.EAGAIN,
                    }:
                        return False
                    raise
                return True

            def unlock() -> None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

        while not try_lock():
            if time.monotonic() >= deadline:
                raise RuntimeError("Outra criacao inicial desta conversa ainda esta em andamento.")
            time.sleep(0.025)
        try:
            yield
        finally:
            unlock()


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
    """Create or revise one subject; serialize only its first durable creation."""

    canonical_task = _canonical_task_type(task_type)
    call = {
        "client_id": client_id,
        "task_type": task_type,
        "store": store,
        "subject_key": subject_key,
        "request": request,
        "channel": channel,
        "created_by": created_by,
    }
    operational_values = (client_id, store, subject_key, channel, created_by)
    if (
        canonical_task not in TASK_TYPES
        or canonical_task == TASK_TYPE_POST_SALE
        or any(contains_vin_like_identifier(value) for value in operational_values)
        or _subject_has_persisted_job(
            client_id,
            canonical_task,
            store,
            subject_key,
            request,
        )
    ):
        return _create_job_unserialized(**call)
    key = (
        str(client_id or ""),
        canonical_task,
        str(store or ""),
        str(subject_key or ""),
    )
    lock = _acquire_initial_creation_lock(key)
    try:
        with _initial_creation_process_lock(key):
            return _create_job_unserialized(**call)
    finally:
        _release_initial_creation_lock(key, lock)


def get_job(client_id: str, job_id: str) -> Optional[dict[str, Any]]:
    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
        return _public_job(_cancel_post_sale_job(job))
    if not _job_contract_current(job):
        if _job_terminal(job):
            return _public_job(job)
        job = _quarantine_outdated_job(job, client_id=client_id)
        return _public_job(job)
    if (
        str(job.get("status") or "") == "completed"
        and (
            str(job.get("agent_state") or "") == "aguardando_aprovacao"
            or bool(job.get("draft_expired"))
        )
        and not codex_assistant_storage.codex_assistant_customer_reply_job_has_transient(
            info_base, client_id, job_id
        )
    ):
        job.update(
            {
                "status": "completed",
                "agent_state": "concluido",
                "current_step": "responder",
                "draft_expired": True,
                "blocked_without_draft": True,
                "review_required": True,
                "requires_approval": False,
                "completion_reason": "draft_unavailable_after_restart",
                "warnings": _unique_warnings(
                    job.get("warnings"),
                    [
                        "O rascunho nao esta mais disponivel apos a reinicializacao; "
                        "gere uma nova resposta. Nenhum texto substituto foi criado."
                    ],
                ),
                "lease_owner": "",
                "lease_expires_ts": 0.0,
            }
        )
        job = codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base,
            client_id,
            job,
            expected_lease_generation=_lease_generation(job),
        )
    if str(job.get("status") or "") == "waiting_retry" and _job_deadline_expired(job):
        job = _complete_retry_limit(job)
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
        and str(job.get("prompt_version") or "") == PROMPT_VERSION
        and str(job.get("prompt_hash") or "") == PROMPT_HASH
        and str(job.get("schema_version") or "") == SCHEMA_VERSION
        and str(job.get("queue_policy_version") or "") == QUEUE_POLICY_VERSION
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
    return _job_contract_current(job)


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
        if not _job_terminal(latest):
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
    """Keep active jobs scheduled without ever reactivating a terminal job."""

    info_base = _runtime_info_base()
    job = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(job, dict):
        return None
    if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
        return _public_job(_cancel_post_sale_job(job))
    if not _job_contract_current(job):
        if _job_terminal(job):
            return _public_job(job)
        return _public_job(_quarantine_outdated_job(job, client_id=client_id))
    if job.get("cancel_requested") or str(job.get("status") or "") == "cancelled":
        return _public_job(job)
    if str(job.get("status") or "") in ACTIVE_STATUSES:
        if str(job.get("status") or "") == "waiting_retry":
            _schedule_retry_timer(job)
        else:
            _schedule(job)
        return _public_job(job, queue_position=_queue_position(info_base, client_id, job_id))
    return _public_job(job)


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
    _discard_vin_envelope_family(job_id)
    _cancel_retry_timer(job_id)
    try:
        from backend.services import ia_providers

        ia_providers.cancel_codex_persistent_turn(job_id)
    except Exception:
        logger.warning("[PPV CODEX] evento=interromper_turno status=erro")
    plan_id = str(job.get("plan_id") or "")
    if plan_id and str(job.get("status") or "") == "cancelled":
        try:
            codex_agent_runtime.transition_plan(
                info_base, client_id, plan_id, "cancelado", current_step="responder", step_status="canceled"
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=cancelar_plano status=erro")
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
    if isinstance(latest, dict) and _lease_generation(latest) != _lease_generation(job):
        raise _LeaseLost("Geracao da lease transferida para outro worker.")
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
            "vehicle_identity_capture_status",
            "vehicle_identity",
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
        _runtime_info_base(),
        str(job.get("client_id") or "default"),
        job,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )
    if str(saved.get("status") or "") == "cancelled" or saved.get("cancel_requested"):
        raise InterruptedError("Tarefa cancelada pelo usuario.")
    if not _saved_by_same_lease(saved, job, status="running") or str(saved.get("lease_owner") or "") != _WORKER_ID:
        raise _LeaseLost("Lease transferida para outro worker.")
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
                details={"job_id": job.get("job_id"), "state": state, "step": step},
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=atualizar_plano status=erro")
    return saved


def _cancelled(job: dict[str, Any]) -> bool:
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(
        _runtime_info_base(), str(job.get("client_id") or "default"), str(job.get("job_id") or "")
    )
    return bool(isinstance(latest, dict) and latest.get("cancel_requested"))


def _heartbeat_loop(
    client_id: str,
    job_id: str,
    lease_generation: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(15.0):
        try:
            renewed = codex_assistant_storage.codex_assistant_customer_reply_job_heartbeat(
                _runtime_info_base(),
                client_id,
                job_id,
                owner=_WORKER_ID,
                lease_generation=lease_generation,
                lease_seconds=60.0,
            )
            if not isinstance(renewed, dict):
                return
        except Exception:
            logger.warning("[PPV CODEX] evento=renovar_lease status=erro")


def _is_operational_failure(exc: BaseException) -> bool:
    """Classify failures that may unlock the optional provider fallback.

    Validation, missing evidence and application-contract errors can still be
    retried by the job, but they must never count as a Codex outage.
    """

    if isinstance(exc, (PerguntasIAClassificacaoInconclusiva, PerguntasIASegurancaBloqueada)):
        return False
    if isinstance(exc, PerguntasIAProviderIndisponivel):
        return str(getattr(exc, "reason", "") or "") in PROVIDER_RETRY_REASONS
    current: Optional[BaseException] = exc
    visited: set[int] = set()
    while isinstance(current, BaseException) and id(current) not in visited:
        visited.add(id(current))
        if isinstance(
            current,
            (
                TimeoutError,
                ConnectionError,
                BrokenPipeError,
                requests_exceptions.Timeout,
                requests_exceptions.ConnectionError,
            ),
        ):
            return True
        status_code = getattr(current, "status_code", None)
        if status_code is None:
            response = getattr(current, "response", None)
            status_code = getattr(response, "status_code", None)
        try:
            normalized_status = int(status_code or 0)
        except (TypeError, ValueError):
            normalized_status = 0
        if normalized_status == 429 or 500 <= normalized_status <= 599:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_classification_contract_failure(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            PerguntasIAClassificacaoInconclusiva,
            PerguntasIAProviderIndisponivel,
            PerguntasIASegurancaBloqueada,
        ),
    ):
        return False
    code = str(getattr(exc, "violation_code", "") or "").strip()
    message = _normal(exc)
    return bool(
        code
        or "classificacao de intencao da ia" in message
        or "classificacao estruturada da ia" in message
    )


def _is_response_policy_failure(exc: BaseException) -> bool:
    return isinstance(exc, PerguntasIARespostaPoliticaInvalida)


def _persist_thread_ready(job: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Persist Codex continuity before the turn can fail or the process can stop."""

    resolved = str(thread_id or "").strip()
    if not resolved:
        return job
    info_base = _runtime_info_base()
    client_id = str(job.get("client_id") or "default")
    job_id = str(job.get("job_id") or "")
    latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
    if not isinstance(latest, dict):
        raise _LeaseLost("Job indisponivel ao persistir thread Codex.")
    if latest.get("cancel_requested") or str(latest.get("status") or "") == "cancelled":
        raise InterruptedError("Tarefa cancelada pelo usuario.")
    if (
        str(latest.get("status") or "") != "running"
        or str(latest.get("lease_owner") or "") != _WORKER_ID
        or _lease_generation(latest) != _lease_generation(job)
    ):
        raise _LeaseLost("Lease transferida antes de persistir thread Codex.")
    previous_thread_id = str(latest.get("thread_id") or job.get("thread_id") or "").strip()
    latest["thread_id"] = resolved
    latest["lease_owner"] = _WORKER_ID
    if previous_thread_id and previous_thread_id != resolved:
        latest["thread_reused"] = False
        latest["thread_restart_reason"] = "resume_failed_new_thread"
    elif previous_thread_id == resolved:
        latest["thread_reused"] = True
    saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
        info_base,
        client_id,
        latest,
        expected_lease_owner=_WORKER_ID,
        expected_lease_generation=_lease_generation(job),
    )
    if not _saved_by_same_lease(saved, job, status="running") or str(saved.get("thread_id") or "") != resolved:
        raise _LeaseLost("Thread Codex nao persistida pela lease atual.")
    for key in ("thread_id", "thread_reused", "thread_restart_reason", "updated_at"):
        if key in saved:
            job[key] = saved.get(key)
    return saved


def _sanitize_question_and_decode_vehicle(
    job: dict[str, Any],
    question: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Sanitize late input while using only facts decoded before queueing."""

    current_capture = capture_vin_payload(
        question,
        retain_envelope=False,
    )
    sanitized = (
        dict(current_capture.sanitized_payload)
        if isinstance(current_capture.sanitized_payload, dict)
        else {}
    )

    existing = job.get("vehicle_identity") if isinstance(job.get("vehicle_identity"), dict) else {}
    if existing:
        # The identity decoded from the current public question is authoritative.
        # A VIN discovered later in enriched history may belong to an older
        # vehicle and must be sanitized without replacing those safe facts.
        return sanitized, dict(existing)

    capture_status = str(current_capture.status or "absent")
    if capture_status in {"ambiguous", "invalid"}:
        status = "ambiguous" if capture_status == "ambiguous" else "invalid"
        reason = "multiple_vins" if status == "ambiguous" else "vin_invalid"
        facts = empty_vehicle_identity(status, reason=reason).as_dict()
        job["vehicle_identity"] = facts
        return sanitized, facts

    contains_marker = VIN_MARKER in str(sanitized)
    original_capture_status = str(job.get("vehicle_identity_capture_status") or "absent")
    if capture_status == "captured" or original_capture_status == "captured" or contains_marker:
        facts = empty_vehicle_identity(
            "unavailable",
            reason="transient_envelope_unavailable",
        ).as_dict()
        job["vehicle_identity"] = facts
        return sanitized, facts
    return sanitized, {}


def _variation_id_from_payload(payload: Mapping[str, Any]) -> str:
    nested = payload.get("variation") if isinstance(payload.get("variation"), Mapping) else {}
    return str(
        payload.get("variation_id")
        or payload.get("item_variation_id")
        or payload.get("selected_variation_id")
        or nested.get("id")
        or ""
    ).strip()


def _item_variations(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(value)
        for value in (item.get("variations") or [])
        if isinstance(value, Mapping) and str(value.get("id") or "").strip()
    ]


def _resolve_product_variation(
    question: Mapping[str, Any],
    item: Mapping[str, Any],
    request: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None, str]:
    request_item = request.get("item") if isinstance(request.get("item"), Mapping) else {}
    requested = (
        _variation_id_from_payload(question)
        or _variation_id_from_payload(request)
        or _variation_id_from_payload(request_item)
        or _variation_id_from_payload(item)
    )
    variations = _item_variations(item)
    if requested and variations:
        selected = next(
            (value for value in variations if str(value.get("id") or "").strip() == requested),
            None,
        )
        if selected is None:
            return "", None, "invalid"
        return requested, selected, "selected"
    if requested:
        return requested, None, "selected_unverified"
    if len(variations) == 1:
        return str(variations[0].get("id") or "").strip(), variations[0], "single"
    if variations:
        return "", None, "unresolved_multi"
    return "", None, "global"


def _product_sku_for_variation(
    runtime: Any,
    question: Mapping[str, Any],
    item: Mapping[str, Any],
    request: Mapping[str, Any],
    selected: Mapping[str, Any] | None,
    variation_state: str,
) -> str:
    if variation_state in {"invalid", "unresolved_multi"}:
        return ""
    parent = dict(item)
    parent.pop("variations", None)
    parent.pop("variations_data", None)
    candidates = [
        runtime._ml_extrair_sku(dict(selected or {})) if selected else "",
        question.get("item_sku"),
        request.get("sku"),
        runtime._ml_extrair_sku(parent),
    ]
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def _product_evidence_identity(
    runtime: Any,
    *,
    store: str,
    cfg: dict[str, Any],
    question: dict[str, Any],
    item: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, str]:
    from backend.services.cadastro_compatibilidade import (
        resolver_loja_ativa_para_leitura,
    )

    store_identity = resolver_loja_ativa_para_leitura(
        str(question.get("_tenant_id") or request.get("client_id") or ""),
        store,
    ) if str(question.get("_tenant_id") or request.get("client_id") or "").strip() else {}
    store_ref = str(store_identity.get("store_id") or "").strip()
    seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
    item_id = str(item.get("id") or question.get("item_id") or request.get("item_id") or "").strip()
    site_id = str(item.get("site_id") or cfg.get("site_id") or "").strip()
    if not site_id and re.match(r"^[A-Z]{3}", item_id, flags=re.IGNORECASE):
        site_id = item_id[:3].upper()
    variation_id, selected_variation, variation_state = _resolve_product_variation(
        question,
        item,
        request,
    )
    question["_product_evidence_variation_state"] = variation_state
    sku = _product_sku_for_variation(
        runtime,
        question,
        item,
        request,
        selected_variation,
        variation_state,
    )
    raw_identity = {
        "store_ref": store_ref,
        "seller_id": str(
            item.get("seller_id")
            or seller.get("id")
            or cfg.get("user_id")
            or cfg.get("seller_id")
            or ""
        ).strip(),
        "site_id": site_id,
        "sku": sku,
        "item_id": item_id,
        "variation_id": variation_id,
    }
    # Public V18 must fail closed when the opaque store identity is unresolved;
    # never replace it with the human-readable store name.
    return {
        key: str(value or "").strip()[:180]
        for key, value in raw_identity.items()
    }


def _load_question_context(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    runtime = _require_runtime()
    request = job.get("request") if isinstance(job.get("request"), dict) else {}
    client_id = str(job.get("client_id") or "default")
    store = str(job.get("store") or "")
    question = dict(request.get("pergunta") or {}) if isinstance(request.get("pergunta"), dict) else {}
    question_id = str(job.get("question_id") or question.get("id") or job.get("event_subject_key") or "").strip()
    if not question and question_id:
        reload_cfg = runtime._obter_cfg_ml(client_id, store)
        try:
            response, reload_cfg = runtime._ml_api_request(
                client_id,
                store,
                reload_cfg,
                "GET",
                f"https://api.mercadolibre.com/questions/{question_id}",
                timeout=12,
            )
            if response.status_code == 200:
                payload = response.json() or {}
                question = dict(payload.get("question") or payload) if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("[PPV CODEX] evento=recarregar_pergunta status=erro tipo=%s", type(exc).__name__)
    if not question:
        raise RuntimeError("pergunta_canonica_indisponivel")
    question.setdefault("id", question_id)
    question["_codex_thread_id"] = str(job.get("thread_id") or "")
    question["_codex_job_id"] = str(job.get("job_id") or "")
    question["_codex_conversation_key"] = str(job.get("conversation_id") or "")
    question["_codex_active_turn_key"] = str(job.get("job_id") or "")
    question["_codex_on_thread_ready"] = lambda thread_id: _persist_thread_ready(job, thread_id)
    question["_codex_operational_failure_count"] = max(0, int(job.get("operational_failure_count") or 0))
    question["_codex_prompt_version"] = str(job.get("prompt_version") or PROMPT_VERSION)
    question["_tenant_id"] = client_id
    question["_codex_schema_version"] = str(job.get("schema_version") or SCHEMA_VERSION)
    question["_agent_subquestions"] = list(job.get("subquestions") or [])
    question["_research_attempt"] = max(1, int(job.get("attempt_count") or 0) + 1)
    question["_research_history"] = list(job.get("research_history") or [])[-6:]
    question["_research_gaps"] = _pending_research_gaps(job)
    question["_force_external_research"] = bool(job.get("retry_count") or job.get("research_history"))
    gaps_text = ", ".join(question["_research_gaps"][:8])
    question["_research_directive"] = (
        "Nao repita somente as consultas anteriores. Reutilize os resultados e fontes ja confirmados e pesquise "
        "apenas as lacunas restantes em fontes oficiais, manuais, catalogos OEM, referencias cruzadas ou duas "
        f"fontes tecnicas independentes. Lacunas atuais: {gaps_text or 'evidencias ainda nao confirmadas'}."
        if question["_force_external_research"]
        else ""
    )
    if str(request.get("resposta_atual") or "").strip():
        question["_resposta_atual"] = str(request.get("resposta_atual") or "")
    if str(request.get("orientacao_usuario") or "").strip():
        question["_orientacao_usuario"] = str(request.get("orientacao_usuario") or "").strip()[:1200]
    cfg = runtime._obter_cfg_ml(client_id, store)
    request_item = dict(request.get("item") or {}) if isinstance(request.get("item"), dict) else {}
    item: dict[str, Any] = {}
    official_current_listing = False
    item_id = str(question.get("item_id") or job.get("item_id") or request_item.get("id") or "").strip()
    if item_id:
        try:
            response, cfg = runtime._ml_api_request(
                client_id, store, cfg, "GET", f"https://api.mercadolibre.com/items/{item_id}", timeout=12
            )
            if response.status_code == 200:
                loaded_item = response.json() or {}
                if isinstance(loaded_item, dict) and loaded_item:
                    item = loaded_item
                    official_current_listing = True
        except Exception as exc:
            logger.warning("[PPV CODEX] evento=atualizar_anuncio status=erro tipo=%s", type(exc).__name__)
    if not item:
        loaded_item = runtime._ml_api_item_com_oauth_tenant(client_id, item_id) or runtime._ml_api_item(item_id) or {}
        if isinstance(loaded_item, dict) and loaded_item:
            item = loaded_item
            official_current_listing = True
    if not item:
        item = request_item
    if isinstance(item, dict) and item and not runtime._ml_extrair_sku(item):
        item = runtime._ml_perguntas_completar_skus_itens(client_id, store, cfg, [item])[0]
    if isinstance(item, dict):
        item["_ppv_official_current_listing"] = official_current_listing
    codex_fields = {key: value for key, value in question.items() if str(key).startswith("_")}
    try:
        normalizer = getattr(runtime, "_ml_perguntas_normalizar", None)
        if callable(normalizer):
            question = normalizer(question, {item_id: item} if item_id else {}, {})
        history_loader = getattr(runtime, "_ml_perguntas_anexar_historico_comprador", None)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        if callable(history_loader) and seller_id:
            enriched, cfg = history_loader(client_id, store, cfg, seller_id, [question])
            if enriched and isinstance(enriched[0], dict):
                question = enriched[0]
    except Exception as exc:
        logger.warning("[PPV CODEX] evento=recarregar_historico status=erro tipo=%s", type(exc).__name__)
    question.update(codex_fields)
    question, vehicle_identity = _sanitize_question_and_decode_vehicle(job, question)
    product_identity = _product_evidence_identity(
        runtime,
        store=store,
        cfg=cfg if isinstance(cfg, dict) else {},
        question=question,
        item=item if isinstance(item, dict) else {},
        request=request,
    )
    verified_product_evidence = load_verified_product_evidence(client_id, product_identity)
    product_research_evidence = load_product_research_evidence(
        client_id,
        product_identity,
        recalculate=False,
    )
    question["_vehicle_identity"] = dict(vehicle_identity) if vehicle_identity else {}
    question["_product_evidence_identity"] = product_identity
    question["_verified_product_evidence"] = verified_product_evidence
    question["_product_research_evidence"] = product_research_evidence
    answer, _cfg, context = runtime._perguntas_ia_gerar_resposta(client_id, store, cfg, question, item or {})
    context = context if isinstance(context, dict) else {}
    context.setdefault("loja", store)
    if vehicle_identity:
        context.setdefault("vehicle_identity", vehicle_identity)
    context.setdefault("verified_product_evidence", verified_product_evidence)
    context.setdefault("product_research_evidence", product_research_evidence)
    context.setdefault("product_evidence_policy", PRODUCT_EVIDENCE_POLICY)
    context.setdefault("vehicle_identity_policy", VEHICLE_IDENTITY_POLICY)
    context.setdefault("pergunta", {
        "id": question.get("id") or job.get("event_subject_key") or "",
        "item_id": question.get("item_id") or item_id,
        "buyer_id": question.get("buyer_id") or question.get("from_id") or "",
        "text": question.get("text") or "",
    })
    public_item = {
        key: value for key, value in (item or {}).items()
        if key != "_ppv_official_current_listing"
    }
    context.setdefault("item", public_item)
    history = (
        question.get("buyer_question_chat")
        if isinstance(question.get("buyer_question_chat"), list)
        else question.get("history") if isinstance(question.get("history"), list) else []
    )
    if history:
        context.setdefault("buyer_question_chat", history[-10:])
        context.setdefault("historico_comprador", history[-10:])
    return str(answer or ""), context


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
    conversation["_codex_active_turn_key"] = str(job.get("job_id") or "")
    conversation["_codex_operational_failure_count"] = max(0, int(job.get("operational_failure_count") or 0))
    conversation["_codex_prompt_version"] = str(job.get("prompt_version") or PROMPT_VERSION)
    conversation["_codex_schema_version"] = str(job.get("schema_version") or SCHEMA_VERSION)
    conversation["_agent_subquestions"] = list(job.get("subquestions") or [])
    conversation["_research_attempt"] = max(1, int(job.get("attempt_count") or 0) + 1)
    conversation["_research_history"] = list(job.get("research_history") or [])[-6:]
    conversation["_force_external_research"] = bool(job.get("retry_count") or job.get("research_history"))
    if str(request.get("resposta_atual") or "").strip():
        conversation["_resposta_atual"] = str(request.get("resposta_atual") or "")
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
    return str(result.get("resposta") or ""), context


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
        info_base,
        client_id,
        job_id,
        owner=_WORKER_ID,
        lease_seconds=60.0,
        max_running=MAX_GLOBAL_JOBS,
        enforce_priority=True,
        queue_policy_version=QUEUE_POLICY_VERSION,
    )
    if not isinstance(claimed, dict):
        return
    if _canonical_task_type(claimed.get("task_type")) == TASK_TYPE_POST_SALE:
        _cancel_post_sale_job(claimed)
        return
    if not _job_contract_current(claimed):
        _quarantine_outdated_job(claimed, client_id=client_id)
        return
    job = _consume_vehicle_identity_for_worker(claimed)
    job = _refresh_thread_from_previous_job(job)
    now_epoch = time.time()
    if float(job.get("first_started_at_epoch") or 0.0) <= 0.0:
        job["first_started_at_epoch"] = now_epoch
    job.update(
        {
            "queue_policy_version": QUEUE_POLICY_VERSION,
            "evidence_attempt_limit": MAX_EVIDENCE_ATTEMPTS,
            "operational_failure_limit": MAX_OPERATIONAL_FAILURES,
            "total_attempt_limit": MAX_TOTAL_ATTEMPTS,
        }
    )
    _job_deadline_epoch(job)
    if _job_deadline_expired(job):
        _complete_without_draft(
            job,
            warning="O limite total da pesquisa foi atingido; foi gerado um rascunho neutro editavel.",
            completion_reason="execution_deadline_reached",
        )
        return
    attempt_generation = max(1, int(job.get("request_generation") or 1))
    job["attempt_count"] = max(0, int(job.get("attempt_count") or 0)) + 1
    if int(job["attempt_count"]) > MAX_TOTAL_ATTEMPTS:
        _complete_without_draft(
            job,
            warning="O limite total de tentativas foi atingido; foi gerado um rascunho neutro editavel.",
            completion_reason="total_attempt_limit_reached",
        )
        return
    job["restart_requested"] = False
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(client_id, job_id, _lease_generation(job), heartbeat_stop),
        name=f"ppv-codex-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    started = time.monotonic()
    answer = ""
    context: dict[str, Any] = {}
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
            raise TimeoutError("O ciclo do agente excedeu o prazo operacional permitido.")
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
        job["operational_failure_count"] = 0
        job["evidence_attempt_count"] = max(0, int(job.get("evidence_attempt_count") or 0)) + 1
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
            "completion_reason": "evidence_confirmed",
            "draft_source": "ai",
        }
        latest = codex_assistant_storage.codex_assistant_customer_reply_job_get(info_base, client_id, job_id)
        if isinstance(latest, dict) and (
            latest.get("cancel_requested")
            or str(latest.get("status") or "") == "cancelled"
        ):
            return
        if (
            isinstance(latest, dict)
            and str(latest.get("status") or "") == "running"
            and str(latest.get("lease_owner") or "")
            and str(latest.get("lease_owner") or "") != _WORKER_ID
        ):
            return
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
            _complete_with_best_available(
                job,
                answer=answer,
                context=context,
                matrix=matrix,
                warnings=warnings,
                draft_source="ai",
                completion_reason="ai_response_preserved_unvalidated",
                deadline_reached=False,
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
                "completion_reason": "evidence_confirmed",
                "draft_source": "ai",
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
                "message": "Rascunho concluido e disponivel para uso.",
            }
        )
        job["agent_steps"] = history[-60:]
        saved = codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base,
            client_id,
            job,
            expected_lease_owner=_WORKER_ID,
            expected_lease_generation=_lease_generation(job),
        )
        if not _saved_by_same_lease(saved, job, status="completed", proposal_hash=proposal_hash):
            return
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
                details={"data_sufficient": sufficient, "completion_reason": "evidence_confirmed"},
            )
    except _LeaseLost:
        logger.info("[PPV CODEX] evento=lease_perdida resultado=descartado")
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
        codex_assistant_storage.codex_assistant_customer_reply_job_save(
            info_base,
            client_id,
            job,
            expected_lease_owner=_WORKER_ID,
            expected_lease_generation=_lease_generation(job),
        )
    except Exception as exc:
        if isinstance(answer, str) and answer.strip():
            _complete_with_best_available(
                job,
                answer=answer,
                context=context,
                warnings=[
                    "A resposta da IA foi preservada literalmente apos uma falha de pos-processamento."
                ],
                draft_source="ai",
                completion_reason="ai_response_preserved_after_postprocessing_failure",
                deadline_reached=False,
            )
            return
        fallback_context = getattr(exc, "ppv_fallback_context", None)
        if isinstance(exc, PerguntasIAClassificacaoInconclusiva):
            logger.info("[PPV CODEX] evento=classificacao_inconclusiva")
            job["operational_failure_count"] = 0
            _complete_without_draft(
                job,
                warning=(
                    "A classificacao permaneceu inconclusiva; foi gerado um rascunho seguro "
                    "editavel para revisao."
                ),
                completion_reason="classification_inconclusive",
                fallback_context=(fallback_context if isinstance(fallback_context, dict) else None),
                allow_contextual=bool(getattr(exc, "allow_contextual", True)),
                deadline_reached=False,
            )
            return
        if isinstance(exc, PerguntasIASegurancaBloqueada):
            logger.warning("[PPV CODEX] evento=seguranca_bloqueada")
            job["operational_failure_count"] = 0
            _complete_without_draft(
                job,
                warning="A pergunta foi bloqueada pela politica de seguranca; revise antes de responder.",
                completion_reason="security_blocked",
                allow_contextual=False,
                deadline_reached=False,
            )
            return
        logger.warning("[PPV CODEX] evento=executar_job status=erro tipo=%s", type(exc).__name__)
        if _is_response_policy_failure(exc):
            _complete_with_best_available(
                job,
                warnings=[
                    "A tentativa mais recente nao passou pelas regras de seguranca; "
                    "o ultimo rascunho validado foi preservado para revisao."
                ],
                completion_reason="response_policy_violation_best_available",
                deadline_reached=False,
                empty_completion_reason="response_policy_violation",
                empty_warning=(
                    "Nenhuma tentativa passou integralmente pelas regras de seguranca; "
                    "foi gerado um rascunho conservador para revisao."
                ),
            )
            return
        if _is_classification_contract_failure(exc):
            _complete_without_draft(
                job,
                warning=(
                    "Classificacao estruturada da IA indisponivel apos a regeneracao controlada; "
                    "foi gerado um rascunho neutro editavel."
                ),
                completion_reason="classification_contract_violation",
                fallback_context=(fallback_context if isinstance(fallback_context, dict) else None),
                deadline_reached=False,
            )
            return
        if not _is_operational_failure(exc):
            _complete_without_draft(
                job,
                warning="A pesquisa encontrou uma violacao de contrato interno; foi gerado um rascunho neutro editavel.",
                completion_reason="non_operational_failure",
                fallback_context=(fallback_context if isinstance(fallback_context, dict) else None),
                deadline_reached=False,
            )
            return
        job["operational_failure_count"] = (
            max(0, int(job.get("operational_failure_count") or 0)) + 1
        )
        if (
            int(job.get("operational_failure_count") or 0) >= MAX_OPERATIONAL_FAILURES
            or int(job.get("attempt_count") or 0) >= MAX_TOTAL_ATTEMPTS
            or _job_deadline_expired(job)
        ):
            _complete_without_draft(
                job,
                warning="O limite de falhas operacionais foi atingido; foi gerado um rascunho neutro editavel.",
                completion_reason="operational_retry_exhausted",
            )
            return
        retry_job = _persist_retry(
            job,
            error=str(getattr(exc, "reason", "") or "provider_unavailable")[:120],
            warnings=["Falha operacional temporaria; uma nova tentativa sera executada."],
            retry_kind="operational",
        )
        if str(retry_job.get("status") or "") == "cancelled" or retry_job.get("cancel_requested"):
            return
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
                    verification={"status": "retry", "confirmed": False},
                )
            except Exception:
                logger.warning("[PPV CODEX] evento=registrar_tentativa status=erro")
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
        codex_assistant_storage.codex_assistant_customer_reply_jobs_cleanup(info_base, client_id)
        jobs: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = codex_assistant_storage.codex_assistant_customer_reply_jobs_list(
                info_base,
                client_id,
                statuses=list(ACTIVE_STATUSES | {"failed", "completed"}),
                limit=500,
                offset=offset,
                priority_order=True,
            )
            jobs.extend(page)
            if len(page) < 500:
                break
            offset += len(page)
        for job in jobs:
            if _canonical_task_type(job.get("task_type")) == TASK_TYPE_POST_SALE:
                _cancel_post_sale_job(job)
                continue
            if job.get("cancel_requested"):
                continue
            if str(job.get("status") or "") == "completed":
                if not _job_contract_current(job):
                    continue
                if (
                    str(job.get("agent_state") or "") == "aguardando_aprovacao"
                    and not codex_assistant_storage.codex_assistant_customer_reply_job_has_transient(
                        info_base, client_id, str(job.get("job_id") or "")
                    )
                ):
                    job.update(
                        {
                            "status": "completed",
                            "agent_state": "concluido",
                            "current_step": "responder",
                            "draft_expired": True,
                            "blocked_without_draft": True,
                            "review_required": True,
                            "requires_approval": False,
                            "completion_reason": "draft_unavailable_after_restart",
                            "warnings": _unique_warnings(
                                job.get("warnings"),
                                [
                                    "O rascunho nao esta mais disponivel apos a reinicializacao; "
                                    "gere uma nova resposta. Nenhum texto substituto foi criado."
                                ],
                            ),
                            "lease_owner": "",
                            "lease_expires_ts": 0.0,
                        }
                    )
                    codex_assistant_storage.codex_assistant_customer_reply_job_save(
                        info_base,
                        client_id,
                        job,
                        expected_lease_generation=_lease_generation(job),
                    )
                continue
            if not _job_contract_current(job):
                _quarantine_outdated_job(job, client_id=client_id)
                continue
            if str(job.get("status") or "") == "failed":
                _complete_without_draft(
                    job,
                    warning="Falha anterior encerrada pela politica limitada; foi gerado um rascunho neutro editavel.",
                    completion_reason="legacy_failed_job_closed",
                )
                continue
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
        logger.warning("[PPV CODEX] evento=despachar_fila status=erro")


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
        if not _job_terminal(job):
            _quarantine_outdated_job(job, client_id=client_id)
        raise ValueError("A proposta usa um contrato de IA anterior. Gere uma nova resposta antes de aprovar.")
    event_subject_key = str(job.get("event_subject_key") or job.get("subject_key") or "")
    if str(job.get("store") or "") != str(store or "") or event_subject_key != str(subject_key or ""):
        raise PermissionError("A proposta não pertence a esta loja ou conversa.")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    requested_answer = str(answer or "")
    if (
        str(job.get("status") or "") != "completed"
        or result.get("blocked_without_draft")
        or job.get("blocked_without_draft")
        or not requested_answer.strip()
    ):
        raise ValueError("A tarefa nao possui proposta de resposta aprovavel. Gere uma nova resposta.")
    current_version = max(1, int(job.get("proposal_version") or result.get("proposal_version") or 1))
    current_hash = str(job.get("proposal_hash") or result.get("proposal_hash") or "")
    if int(proposal_version or 0) not in {0, current_version}:
        raise ValueError("A proposta foi atualizada. Revise a versão mais recente antes de aprovar.")
    if proposal_hash and proposal_hash != current_hash:
        raise ValueError("A proposta foi alterada. Gere ou revise novamente antes de aprovar.")
    unchanged = bool(
        current_hash
        and _hash(
            {
                "job_id": job.get("job_id"),
                "version": current_version,
                "store": store,
                "subject": subject_key,
                "answer": requested_answer,
            }
        ) == current_hash
    )
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
                verification={
                    "status": verification["status"],
                    "confirmed": verification["confirmed"],
                    "verified_at": verification["verified_at"],
                },
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=persistir_verificacao status=erro")
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
                verification={
                    "status": verification["status"],
                    "confirmed": verification["confirmed"],
                    "verified_at": verification["verified_at"],
                },
            )
        except Exception:
            logger.warning("[PPV CODEX] evento=registrar_rejeicao status=erro")
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
