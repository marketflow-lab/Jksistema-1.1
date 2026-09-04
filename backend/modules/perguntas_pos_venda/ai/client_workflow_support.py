"""Shared contracts and bounded helpers for provider-client workflows."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ml_questions_gemini.prompt_builder import _untrusted_json_block

from .deep_research_contracts import research_session
from .document_vision import (
    MAX_DOCUMENT_VISION_IMAGES,
    PRODUCT_DOCUMENT_VISION_POLICY,
    collect_document_vision_batch,
    document_vision_page_key,
    technical_focus_terms,
)
from .evidence import _perguntas_ia_v2_fontes_web
from .inputs import _perguntas_codex_compact_json, _perguntas_ia_research_input
from .runtime import logger


ToolCallback = Callable[[], Optional[dict]]
STORE_BOUND_PUBLIC_CATEGORIES = frozenset({
    "greeting", "price", "stock", "shipping", "invoice",
    "warranty_originality", "prohibited_contact", "other_product",
})


@dataclass(frozen=True, slots=True)
class CompatibilityBindings:
    listing_tool: Callable[..., dict]
    product_tool: Callable[..., dict]
    bling_tool: Callable[..., dict]
    context_hub_tool: Callable[..., dict]
    memory_prompt: Callable[..., str]
    legacy_reader_enabled: Callable[[], bool]
    legacy_fallback: Callable[..., str]
    product_identity_tool: Callable[..., dict]
    web_tool: Callable[..., dict]
    alternative_tool: Optional[Callable[..., dict]] = None


@dataclass(frozen=True, slots=True)
class GeneralBindings:
    context_hub_tool: Callable[..., dict]
    web_tool: Callable[..., dict]
    alternative_tool: Optional[Callable[..., dict]] = None
    listing_tool: Optional[Callable[..., dict]] = None
    product_tool: Optional[Callable[..., dict]] = None
    bling_tool: Optional[Callable[..., dict]] = None


@dataclass(frozen=True, slots=True)
class CompatibilityWorkflowHooks:
    classified_tool: Callable[..., dict]
    classified_web_tool: Callable[..., dict]
    next_pipeline_step: Callable[..., int]
    prepare_document_vision: Callable[..., list[dict[str, Any]]]
    preserve_technical_state: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class GeneralWorkflowHooks:
    classified_tool: Callable[..., dict]
    mandatory_web_tool: Callable[..., dict]
    next_pipeline_step: Callable[..., int]
    prepare_document_vision: Callable[..., list[dict[str, Any]]]
    preserve_technical_state: Callable[..., Any]
    context_hub_should_search: Callable[..., bool]


def untrusted_compact_block(tag: str, value: object, max_chars: int) -> str:
    """Bound dynamic prompt data without allowing it to close a trusted delimiter."""

    compacted = _perguntas_codex_compact_json(value, max_chars)
    try:
        payload = json.loads(compacted)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = compacted
    return _untrusted_json_block(tag, payload)


def next_pipeline_step(client, default: int = 0) -> int:
    return max(
        [
            int(step.get("step") or 0)
            for step in getattr(client, "context_pipeline", [])
            if isinstance(step, dict)
        ],
        default=default,
    ) + 1


def _document_vision_budget(client) -> tuple[set[str], int, int]:
    seen = set(getattr(client, "_document_vision_seen_page_keys", set()) or set())
    used = max(0, min(
        int(getattr(client, "_document_vision_images_used", 0) or 0),
        MAX_DOCUMENT_VISION_IMAGES,
    ))
    return seen, used, MAX_DOCUMENT_VISION_IMAGES - used


def _record_exhausted_document_vision(client, phase: str, images_used: int) -> None:
    client._document_vision_attachments = []
    client._document_vision_page_refs = []
    client.context_pipeline.append({
        "step": next_pipeline_step(client, 0),
        "name": "product_document_vision",
        "status": "not_applicable",
        "phase": phase,
        "policy": PRODUCT_DOCUMENT_VISION_POLICY,
        "documents_attempted": 0,
        "documents_processed": 0,
        "documents_unprocessed": 0,
        "images_created": 0,
        "images_used_total": images_used,
        "images_remaining": 0,
        "stop_reason": "global_image_limit",
    })


def _combined_document_vision_research(
    research_results: list[dict[str, Any]],
) -> dict[str, Any]:
    combined: dict[str, Any] = {
        "research_sources": [], "document_sources": [],
        "product_research_evidence": [], "verified_product_evidence": [],
        "verified_target_evidence": [],
    }
    for result in research_results:
        if not isinstance(result, dict):
            continue
        data = result.get("result") if isinstance(result.get("result"), dict) else {}
        combined["research_sources"].extend(_perguntas_ia_v2_fontes_web(result))
        for key in ("research_passages", "document_sources", "documents", "sources"):
            combined["document_sources"].extend(
                value for value in list(data.get(key) or [])[:160]
                if isinstance(value, dict)
            )
        for key in (
            "product_research_evidence", "verified_product_evidence",
            "verified_target_evidence",
        ):
            combined[key].extend(
                value for value in list(data.get(key) or [])[:160]
                if isinstance(value, dict)
            )
    return combined


def _document_vision_focus(client, metadata: dict[str, Any]) -> tuple[str, ...]:
    plan = client.agent_input.get("question_plan")
    if not isinstance(plan, dict):
        plan = client.agent_input.get("technical_question_plan")
    plan = plan if isinstance(plan, dict) else {}
    identifiers: list[object] = []
    for requirement in list(plan.get("requirements") or [])[:8]:
        if not isinstance(requirement, dict):
            continue
        identifiers.extend(requirement.get("search_terms") or [])
        for endpoint in (requirement.get("subject"), requirement.get("target")):
            if isinstance(endpoint, dict):
                identifiers.extend(endpoint.get("identifiers") or [])
    question = client.agent_input.get("question")
    question = question if isinstance(question, dict) else {}
    item = client.agent_input.get("item")
    item = item if isinstance(item, dict) else {}
    return technical_focus_terms(
        question.get("text") or metadata.get("question_text") or "",
        listing_title=item.get("title") or metadata.get("listing_title") or "",
        identifiers=identifiers,
    )


def _accept_document_vision_batch(client, batch, seen: set[str], used: int, phase: str):
    attachments: list[Any] = []
    page_refs: list[dict[str, Any]] = []
    for attachment, raw_ref in zip(batch.attachments, batch.page_refs):
        ref = dict(raw_ref)
        key = document_vision_page_key(ref)
        if not key or key in seen:
            continue
        seen.add(key)
        attachments.append(attachment)
        page_refs.append(ref)
    used = min(MAX_DOCUMENT_VISION_IMAGES, used + len(attachments))
    client._document_vision_seen_page_keys = seen
    client._document_vision_images_used = used
    client._document_vision_attachments = attachments
    client._document_vision_page_refs = page_refs
    diagnostics = batch.diagnostics()
    diagnostics.update({
        "images_created": len(attachments), "images_used_total": used,
        "images_remaining": MAX_DOCUMENT_VISION_IMAGES - used,
    })
    client.context_pipeline.append({
        "step": next_pipeline_step(client, 0), "name": "product_document_vision",
        "status": "completed" if attachments else (
            "unprocessed" if batch.documents_attempted else "not_applicable"
        ),
        "phase": phase, **diagnostics,
    })
    return [dict(value) for value in page_refs]


def prepare_document_vision(
    client,
    metadata: dict[str, Any],
    research_results: list[dict[str, Any]],
    *,
    phase: str,
) -> list[dict[str, Any]]:
    """Prepare at most eight ephemeral PDF page images for the next graph turn."""

    seen_page_keys, images_used, remaining_images = _document_vision_budget(client)
    if remaining_images <= 0:
        _record_exhausted_document_vision(client, phase, images_used)
        return []
    combined = _combined_document_vision_research(research_results)
    focus = _document_vision_focus(client, metadata)
    try:
        session = research_session(_perguntas_ia_research_input(client.agent_input))
        deadline = session.phase_deadline_monotonic(
            "gap" if phase == "gap" else "initial"
        )
        batch = collect_document_vision_batch(
            {"result": combined},
            focus_terms=focus,
            deadline_monotonic=deadline,
            maximum_images=remaining_images,
            excluded_page_keys=seen_page_keys,
        )
    except Exception as exc:
        logger.warning(
            "[PERGUNTAS V2] Leitura visual tecnica indisponivel: %s",
            type(exc).__name__,
        )
        client._document_vision_attachments = []
        client._document_vision_page_refs = []
        return []
    return _accept_document_vision_batch(
        client, batch, seen_page_keys, images_used, phase,
    )


def with_committed_technical_state_preserved(
    client,
    callback: Callable[[], Any],
) -> Any:
    """Keep the public writer from becoming a second technical decision boundary."""

    committed_analysis = deepcopy(getattr(client, "compatibility_analysis", {}) or {})
    committed_state = str(getattr(client, "commercial_state", "") or "")
    try:
        return callback()
    finally:
        client.compatibility_analysis = committed_analysis
        client.commercial_state = committed_state


__all__ = [
    "CompatibilityBindings",
    "CompatibilityWorkflowHooks",
    "GeneralBindings",
    "GeneralWorkflowHooks",
    "STORE_BOUND_PUBLIC_CATEGORIES",
    "ToolCallback",
    "next_pipeline_step",
    "prepare_document_vision",
    "untrusted_compact_block",
    "with_committed_technical_state_preserved",
]
