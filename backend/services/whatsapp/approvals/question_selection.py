"""Selection and identity helpers for WhatsApp question approvals."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Optional

from backend.services import perguntas_pos_venda_codex


def question_template_excerpt(value: Any, fallback: str, limit: int = 480) -> str:
    text = " ".join(str(value or "").split()).strip() or fallback
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def question_notification_key(client_id: str, subject_id: str, approval: dict[str, Any]) -> str:
    approval_id = str(approval.get("id") or "").strip()
    draft = str(approval.get("resposta_sugerida") or "").strip()
    draft_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()[:16]
    return hashlib.sha256(
        f"{client_id}|{subject_id}|{approval_id}|{draft_hash}".encode("utf-8")
    ).hexdigest()


def approved_utility_template_names(
    config: dict[str, Any],
    worker_health: Callable[[dict[str, Any]], dict[str, Any]],
) -> set[str]:
    try:
        worker = worker_health(config)
        templates = worker.get("templates") if isinstance(worker.get("templates"), list) else []
        return {
            str(item.get("name") or "").strip()
            for item in templates
            if isinstance(item, dict)
            and str(item.get("category") or "").strip().upper() == "UTILITY"
            and str(item.get("status") or "").strip().upper() == "APPROVED"
        }
    except Exception:
        return set()


def question_research_delivery_state(approval: dict[str, Any], job_id: str) -> str:
    delivery_state = str(approval.get("research_delivery_state") or "")
    if not delivery_state and approval.get("data_sufficient") is False and job_id:
        approval.update(
            {
                "research_job_id": job_id,
                "research_delivery_state": "waiting_evidence",
                "research_status": "legacy_incomplete",
            }
        )
        return "waiting_evidence"
    return delivery_state


def question_approval_marker(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_post_sale_question_approval(item: dict[str, Any]) -> bool:
    tipo = question_approval_marker(item.get("tipo") or item.get("approval_type"))
    origens = (
        item.get("origem"),
        item.get("ia_origem"),
        item.get("ia_finalidade"),
    )
    return tipo == "pos_venda" or any(
        "pos_venda" in question_approval_marker(origem)
        for origem in origens
    )


def pending_question_approval(
    ppv_state: Any,
    configs: Any,
    approvals: list[dict[str, Any]],
    *,
    client_id: str = "",
) -> Optional[dict[str, Any]]:
    return next(
        (
            item for item in approvals
            if isinstance(item, dict)
            and not is_post_sale_question_approval(item)
            and perguntas_pos_venda_codex.approval_job_current(
                client_id,
                str(item.get("proposal_id") or item.get("codex_job_id") or item.get("research_job_id") or ""),
            )
            and str(item.get("status") or "pending") == "pending"
            and str(item.get("id") or "").strip()
            and str(item.get("resposta_sugerida") or "").strip()
            and ppv_state._perguntas_loja_config_normalizar(
                ppv_state._perguntas_loja_config_obter(configs, str(item.get("loja") or "").strip())
            ).get("notificar_whatsapp_aprovacoes") is True
        ),
        None,
    )


def question_has_active_research(approval: dict[str, Any]) -> bool:
    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    if not job_id or str(approval.get("research_delivered_job_id") or "").strip() == job_id:
        return False
    return question_research_delivery_state(approval, job_id) in {"waiting_evidence", "ready"}


def question_active_token_invalid_reason(
    state: dict[str, Any],
    approval: dict[str, Any],
    token: str,
    *,
    subject_id: str,
    client_id: str,
    username: str,
    token_ttl_seconds: float,
    token_approval_matches: Callable[[dict[str, Any], dict[str, Any]], bool],
) -> str:
    if is_post_sale_question_approval(approval):
        return "ineligible_post_sale"
    tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
    token_item = tokens.get(str(token or "").strip().upper())
    if not isinstance(token_item, dict):
        return "missing_token"
    if (
        not token_approval_matches(token_item, approval)
        or str(token_item.get("subject_id") or "").strip() != str(subject_id or "").strip()
        or str(token_item.get("client_id") or "").strip() != str(client_id or "").strip()
        or str(token_item.get("username") or "").strip().lower() != str(username or "").strip().lower()
    ):
        return "invalid_token_scope"
    try:
        token_age = time.time() - float(token_item.get("created_at") or 0)
    except (TypeError, ValueError):
        token_age = token_ttl_seconds + 1
    if token_age > token_ttl_seconds:
        return "expired_token"
    decision = question_approval_marker(token_item.get("decision"))
    if token_item.get("used") is True or "superseded" in decision:
        if question_has_active_research(approval):
            return ""
        return "consumed_token"
    return ""
