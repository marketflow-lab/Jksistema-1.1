"""Isolated final AI review for buyer-facing Mercado Livre drafts."""

from __future__ import annotations

import copy
import time
from typing import Any

from .factual_critic import (
    FACTUAL_REVIEW_VERSION,
    factual_review_prompt,
    factual_revision_prompt,
    normalize_factual_review,
)
from .runtime import AIAnswer, ValidationResult, _PERGUNTAS_IA_RESPONSE_POLICY
from .sku_question_context import _listing_plain, _response_signature


def _failure_code(exc: Exception, *, revision: bool = False) -> str:
    phase = "ai_revision" if revision else "ai_review"
    error_name = type(exc).__name__.lower()
    if "timeout" in error_name:
        return f"{phase}_timeout"
    if isinstance(exc, ValueError):
        return f"{phase}_invalid_json"
    return f"{phase}_provider_error"


def _review_context(client: Any) -> dict[str, Any]:
    agent_input = client.agent_input if isinstance(client.agent_input, dict) else {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    store_data = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    behavior_profile = agent_input.get("seller_behavior_profile")
    if not isinstance(behavior_profile, dict):
        behavior_profile = agent_input.get("seller_behavior_profile_v2")
    if not isinstance(behavior_profile, dict):
        behavior_profile = {}
    sku_context = client.sku_question_context if isinstance(client.sku_question_context, dict) else {}
    signature = str(sku_context.get("response_signature") or "").strip() or _response_signature({
        **agent_input,
        "store": str(agent_input.get("store") or client.loja or ""),
    })
    flow_key = "pos_venda" if client._is_post_sale else "perguntas_anuncio"
    flow_policy = str(_PERGUNTAS_IA_RESPONSE_POLICY[flow_key])
    subquestions = agent_input.get("subquestions")
    if not isinstance(subquestions, list):
        subquestions = []
    listing_facts = (
        sku_context.get("listing_facts")
        if isinstance(sku_context.get("listing_facts"), dict)
        else {}
    )
    safe_item = {
        key: copy.deepcopy(item.get(key))
        for key in ("id", "seller_sku", "variation_id", "catalog_product_id")
        if item.get(key) not in (None, "")
    }
    for key in ("title", "description"):
        value = listing_facts.get(key) or _listing_plain(item.get(key))
        if value:
            safe_item[key] = value
    return {
        "flow_policy": flow_policy,
        "buyer_context": {
            "question": {
                "text": str(question.get("text") or ""),
                "history": list(question.get("history") or []),
            },
            "subquestions": copy.deepcopy(subquestions),
        },
        "store_context": {
            "tenant_id": str(agent_input.get("tenant_id") or client.client_id or ""),
            "store": str(agent_input.get("store") or client.loja or ""),
            "product_evidence_identity": copy.deepcopy(
                agent_input.get("product_evidence_identity")
                if isinstance(agent_input.get("product_evidence_identity"), dict)
                else {}
            ),
            "item": safe_item,
            "listing_facts": copy.deepcopy(listing_facts),
            "official_store_context": copy.deepcopy(store_data),
            "sku_question_context": copy.deepcopy(sku_context),
            "seller_behavior_profile": copy.deepcopy(behavior_profile),
            "commercial_state": str(client.commercial_state or ""),
        },
        "technical_resolution": copy.deepcopy(
            client._technical_resolution_final
            if isinstance(client._technical_resolution_final, dict) and client._technical_resolution_final
            else client.compatibility_analysis
        ),
        "research": {
            "technical_research_context": copy.deepcopy(client._technical_research_context),
            "technical_evidence_graph": copy.deepcopy(client._technical_evidence_graph),
            "compatibility_analysis": copy.deepcopy(client.compatibility_analysis),
        },
        "internal_sources": copy.deepcopy(client.evidence_records[:20]),
        "subquestions": copy.deepcopy(subquestions),
        "official_marketplace_policy": copy.deepcopy(
            client._official_marketplace_policy
            if isinstance(client._official_marketplace_policy, dict)
            else {}
        ),
        "response_signature": signature,
        "post_sale": client._is_post_sale,
    }


def _record(
    client: Any,
    *,
    status: str,
    codes: list[str],
    duration_ms: int,
    revision_count: int,
) -> None:
    client.context_pipeline.append({
        "step": max(
            [
                int(item.get("step") or 0)
                for item in client.context_pipeline
                if isinstance(item, dict)
            ],
            default=0,
        ) + 1,
        "name": "factual_critic",
        "status": str(status or "insufficient"),
        "codes": list(dict.fromkeys(
            str(code or "other") for code in codes if str(code or "").strip()
        ))[:20],
        "duration_ms": max(0, int(duration_ms)),
        "revision_count": max(0, int(revision_count)),
    })


def _issue_codes(review: dict[str, Any], fallback: str) -> list[str]:
    codes = [
        str(issue.get("code") or "other")
        for issue in list(review.get("issues") or [])
        if isinstance(issue, dict)
    ]
    return list(dict.fromkeys(codes)) or [fallback]


def _block(
    client: Any,
    candidate: AIAnswer,
    *,
    codes: list[str],
    confidence: float = 0.0,
) -> AIAnswer:
    candidate.requires_human_review = True
    candidate.validation = ValidationResult(False, list(dict.fromkeys(codes)), confidence)
    client.manual_review_required = True
    return candidate


def _review_once(client: Any, answer: AIAnswer, review_metadata: dict[str, Any]) -> dict[str, Any]:
    raw_review = client._call_structured_model(
        factual_review_prompt(
            candidate_body=str(answer.answer or ""),
            **_review_context(client),
        ),
        review_metadata,
        stage="factual_critic",
        isolated=True,
    )
    if (
        raw_review.get("schema") != FACTUAL_REVIEW_VERSION
        or raw_review.get("verdict") not in {"pass", "revise", "insufficient"}
        or not isinstance(raw_review.get("issues"), list)
        or not isinstance(raw_review.get("revision_instructions"), list)
        or isinstance(raw_review.get("confidence"), bool)
        or not isinstance(raw_review.get("confidence"), (int, float))
    ):
        raise ValueError("invalid_factual_review_contract")
    return normalize_factual_review(raw_review)


def _accept(
    client: Any,
    candidate: AIAnswer,
    review: dict[str, Any],
    *,
    original_requires_review: bool,
    started: float,
    revision_count: int,
    codes: list[str],
) -> AIAnswer:
    candidate.validation = ValidationResult(
        True, [], float(review.get("confidence") or candidate.confidence or 0.0),
    )
    candidate.requires_human_review = bool(
        original_requires_review or candidate.requires_human_review
    )
    _record(
        client,
        status="pass",
        codes=codes,
        duration_ms=round((time.perf_counter() - started) * 1000),
        revision_count=revision_count,
    )
    return candidate


def review_public_answer(client: Any, candidate: AIAnswer, metadata: dict[str, Any]) -> AIAnswer:
    """Run one isolated critique and, when requested, one rewrite plus final critique."""
    started = time.perf_counter()
    revision_count = 0
    aggregate_codes: list[str] = []
    original_requires_review = bool(candidate.requires_human_review or client.manual_review_required)
    review_metadata = {
        **metadata,
        "category": "factual_critic",
        "review_target_category": str(candidate.category or metadata.get("category") or ""),
        "review_flow": "post_sale" if client._is_post_sale else "pre_sale",
    }
    try:
        first_review = _review_once(client, candidate, review_metadata)
    except Exception as exc:
        code = _failure_code(exc)
        _record(
            client,
            status="incomplete",
            codes=[code],
            duration_ms=round((time.perf_counter() - started) * 1000),
            revision_count=0,
        )
        return _block(client, candidate, codes=[code])
    first_verdict = str(first_review.get("verdict") or "insufficient")
    if first_verdict == "pass":
        return _accept(
            client,
            candidate,
            first_review,
            original_requires_review=original_requires_review,
            started=started,
            revision_count=0,
            codes=[],
        )

    aggregate_codes.extend(_issue_codes(first_review, f"ai_review_{first_verdict}"))
    if first_verdict != "revise":
        _record(
            client,
            status=first_verdict,
            codes=aggregate_codes,
            duration_ms=round((time.perf_counter() - started) * 1000),
            revision_count=0,
        )
        return _block(
            client,
            candidate,
            codes=aggregate_codes,
            confidence=float(first_review.get("confidence") or 0.0),
        )

    revision_count = 1
    try:
        revised = client._call_model(
            factual_revision_prompt(
                preserved_candidate_body=str(candidate.answer or ""),
                review=first_review,
                **_review_context(client),
            ),
            {**review_metadata, "category": "factual_revision"},
            stage="factual_revision",
            isolated=True,
        )
        if not str(getattr(revised, "answer", "") or "").strip():
            raise ValueError("empty_factual_revision")
        revised.category = str(candidate.category or revised.category or "")
        revised.confidence = min(float(candidate.confidence or 0.0), float(revised.confidence or 0.0))
    except Exception as exc:
        code = _failure_code(exc, revision=True)
        aggregate_codes.append(code)
        _record(
            client,
            status="incomplete",
            codes=aggregate_codes,
            duration_ms=round((time.perf_counter() - started) * 1000),
            revision_count=revision_count,
        )
        return _block(client, candidate, codes=aggregate_codes)

    try:
        final_review = _review_once(client, revised, review_metadata)
    except Exception as exc:
        code = _failure_code(exc)
        aggregate_codes.append(code)
        _record(
            client,
            status="incomplete",
            codes=aggregate_codes,
            duration_ms=round((time.perf_counter() - started) * 1000),
            revision_count=revision_count,
        )
        return _block(client, revised, codes=aggregate_codes)

    final_verdict = str(final_review.get("verdict") or "insufficient")
    if final_verdict == "pass":
        return _accept(
            client,
            revised,
            final_review,
            original_requires_review=original_requires_review,
            started=started,
            revision_count=revision_count,
            codes=aggregate_codes,
        )

    aggregate_codes.extend(_issue_codes(final_review, f"ai_review_{final_verdict}"))
    _record(
        client,
        status=final_verdict,
        codes=aggregate_codes,
        duration_ms=round((time.perf_counter() - started) * 1000),
        revision_count=revision_count,
    )
    return _block(
        client,
        revised,
        codes=aggregate_codes,
        confidence=float(final_review.get("confidence") or 0.0),
    )


__all__ = ["review_public_answer"]
