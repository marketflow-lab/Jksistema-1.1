"""Delivery of completed Mercado Livre question research."""

from __future__ import annotations

from typing import Any

from backend.services.whatsapp.approvals.question_tokens import _question_card_context
from backend.services.whatsapp.composition import BridgeDependencies, bind_component_namespace


def _question_research_delivery_state(approval: dict[str, Any], job_id: str) -> str:
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


def _load_research_job(client_id: str, job_id: str) -> tuple[Any, Any]:
    try:
        from backend.services import perguntas_pos_venda_codex as ppv_codex
        from backend.services import perguntas_pos_venda_state as ppv_state

        return ppv_codex.get_job(client_id, job_id), ppv_state
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"question_research_status: {str(exc)[:800]}"
        return None, None


def _research_draft(job: dict[str, Any]) -> tuple[dict[str, Any], str, bool, bool]:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    response = str(result.get("resposta") or "").strip()[:1200]
    safe_partial = bool(
        job.get("data_sufficient") is False
        and (job.get("completed_with_partial") or result.get("completed_with_partial"))
        and result.get("requires_approval") is not False
        and response
    )
    unusable = bool(
        job.get("blocked_without_draft")
        or result.get("blocked_without_draft")
        or not response
        or (job.get("data_sufficient") is not True and not safe_partial)
    )
    return result, response, safe_partial, unusable


def _mark_research_ready(
    approval: dict[str, Any],
    job: dict[str, Any],
    result: dict[str, Any],
    response: str,
    safe_partial: bool,
    job_id: str,
) -> None:
    approval.update(
        {
            "resposta_sugerida": response,
            "regenerated_at": _now(),
            "regenerated_via": "whatsapp_research_loop",
            "research_status": "completed",
            "research_delivery_state": "ready",
            "data_sufficient": bool(job.get("data_sufficient")),
            "completed_with_partial": safe_partial,
            "review_required": bool(result.get("review_required") or job.get("review_required")),
            "completion_reason": str(result.get("completion_reason") or job.get("completion_reason") or ""),
            "proposal_id": job_id,
            "codex_job_id": job_id,
            "proposal_version": int(result.get("proposal_version") or job.get("proposal_version") or 1),
            "proposal_hash": str(result.get("proposal_hash") or job.get("proposal_hash") or ""),
            "warnings": list(result.get("warnings") or job.get("warnings") or [])[:8],
        }
    )


def _supersede_approval_tokens(state: dict[str, Any], approval_id: str) -> None:
    tokens = state.get("question_approval_tokens")
    for item in tokens.values() if isinstance(tokens, dict) else ():
        if isinstance(item, dict) and str(item.get("approval_id") or "") == approval_id:
            item.update({"used": True, "decision": "superseded_by_verified_research"})


def deliver_completed_question_research(
    config: dict[str, Any],
    state: dict[str, Any],
    approvals: list[dict[str, Any]],
    approval: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    username: str,
) -> bool:
    job_id = str(approval.get("research_job_id") or approval.get("codex_job_id") or "").strip()
    delivery_state = _question_research_delivery_state(approval, job_id)
    if not job_id or delivery_state not in {"waiting_evidence", "ready"}:
        return False
    if str(approval.get("research_delivered_job_id") or "") == job_id:
        return False
    job, ppv_state = _load_research_job(client_id, job_id)
    if not isinstance(job, dict) or ppv_state is None:
        return False
    job_status = str(job.get("status") or "").strip().lower()
    approval["research_status"] = job_status
    approval["research_retry_count"] = max(0, int(job.get("retry_count") or 0))
    approval["research_next_retry_at_epoch"] = float(job.get("next_retry_at_epoch") or 0.0)
    if job_status != "completed":
        if job_status == "cancelled":
            approval["research_delivery_state"] = "cancelled"
        return False
    result, response, safe_partial, unusable = _research_draft(job)
    if unusable:
        approval.update(
            {
                "research_delivery_state": "review_required",
                "review_required": True,
                "completion_reason": str(
                    result.get("completion_reason")
                    or job.get("completion_reason")
                    or "research_without_safe_draft"
                ),
            }
        )
        ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
        return False
    _mark_research_ready(approval, job, result, response, safe_partial, job_id)
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    approval_id = str(approval.get("id") or "").strip()
    _supersede_approval_tokens(state, approval_id)
    token, token_item = _question_approval_token(
        state,
        approval=approval,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        force_new=True,
        user_guidance=str(approval.get("whatsapp_user_guidance") or ""),
    )
    delivery = _post_interactive_approval(
        config,
        subject_id=subject_id,
        fingerprint=f"ppv-research:{client_id}:{subject_id}:{job_id}:{result.get('proposal_hash') or ''}",
        token=token,
        body=_question_approval_body(approval, response),
        state=state,
    )
    if str(delivery.get("status") or "") not in {"sent", "duplicate"}:
        return False
    approval.update(
        {
            "research_delivery_state": "delivered",
            "research_delivered_job_id": job_id,
            "research_delivered_at": _now(),
        }
    )
    ppv_state._perguntas_ia_aprovacoes_salvar(client_id, approvals)
    _question_set_active_thread(
        state,
        approval_id=approval_id,
        token=token,
        subject_id=subject_id,
        client_id=client_id,
        username=username,
        card_context=_question_card_context(approval, token_item),
    )
    _save_state(state)
    return True


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), {}, dependencies)


__all__ = ["bind_bridge_dependencies", "deliver_completed_question_research"]
