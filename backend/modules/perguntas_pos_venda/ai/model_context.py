"""Model-only projections for Mercado Livre customer replies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


_MODEL_INTERNAL_AGENT_KEYS = frozenset({
    # Response policy and seller method already live in the unified envelope.
    "prompt",
    "app_guidance",
    "app_guidance_source",
    "app_guidance_truth_class",
    "app_guidance_usage",
    "commercial_method_version",
    # Server/runtime identity and orchestration metadata are never answer facts.
    "tenant_id",
    "store",
    "store_id",
    "seller_id",
    "site_id",
    "order_id",
    "task",
    "orchestrator_profile",
    "locale",
    "_catalog_identity_proof",
    "_codex_thread_id",
    "_codex_job_id",
    "_codex_conversation_key",
    "_codex_active_turn_key",
    "_codex_on_thread_ready",
    "_codex_operational_failure_count",
    "_codex_prompt_version",
    "_codex_schema_version",
    "_research_session_key",
    # Legacy retirement telemetry stays available to the backend only.
    "legacy_guidance_available",
    "legacy_guidance_hash",
    "legacy_fallback_enabled",
    "legacy_fallback_used",
    "legacy_retirement_zero_use_days",
    # The backend owns research authorization and execution.
    "context_collection_pipeline",
    "allowed_tools",
    "tool_policy",
    "constraints",
    "use_web_search",
    "web_search_required",
    "force_external_research",
    "research_gap_only",
    "research_attempt",
    # This is the same exact product binding materialized once below.
    "product_evidence_identity",
})

_QUESTION_INTERNAL_KEYS = frozenset({
    "id", "item_id", "status", "buyer_id", "buyer_name",
    "history_count", "history_source",
})

_ITEM_INTERNAL_KEYS = frozenset({
    "id", "seller_sku", "sku", "variation_id", "thumbnail",
    "pictures_count", "site_id", "listing_type_id", "buying_mode",
})

_CONTEXT_INTERNAL_KEYS = frozenset({
    "tenant_id", "loja", "store_id", "seller_id", "site_id",
    "question_id", "item_id", "variation_id", "order_id", "sku",
})

_CONTEXT_HUB_CONTROL_KEYS = frozenset({
    "schema",
    "policy",
    "route",
    "route_reasons",
    "category",
    "question",
    "subquestions",
    "identity",
    "catalog_generation",
    "generation",
    "source_hashes",
    "integral_core_hash",
    "binding_hash",
    "content_role",
    "instruction_policy",
    "response_signature",
    "web",
})

_EMBEDDED_IDENTITY_KEYS = frozenset({
    "item_id", "mlb", "variation_id", "seller_id", "site_id",
    "store_id", "store_ref", "seller_sku", "sku",
})


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return str(left or "").strip() == str(right or "").strip()
    return left == right


def _remove_matching(payload: dict, key: str, expected: object) -> None:
    if key in payload and _same_value(payload.get(key), expected):
        payload.pop(key, None)


def compact_server_identity_for_model(server_identity: Mapping[str, Any]) -> dict[str, str]:
    """Expose the exact answer identity once, without tenant or seller scope."""

    return {
        key: str(server_identity.get(key) or "").strip()
        for key in ("sku", "item_id", "variation_id")
        if str(server_identity.get(key) or "").strip()
    }


def _remove_embedded_identity(value: object) -> object:
    """Strip binding IDs only from operational/listing projections.

    This helper must never be applied to canonical or catalog documents: their
    Obsidian content is transported exactly as published.
    """

    if isinstance(value, list):
        return [_remove_embedded_identity(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _remove_embedded_identity(item)
        for key, item in value.items()
        if key not in _EMBEDDED_IDENTITY_KEYS and key != "id"
    }


def context_hub_packet_for_model(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Remove Context Hub control metadata while preserving answer evidence."""

    projected = deepcopy(dict(packet))
    for key in _CONTEXT_HUB_CONTROL_KEYS:
        projected.pop(key, None)

    listing_facts = projected.get("listing_facts")
    if isinstance(listing_facts, dict):
        listing_facts.pop("identity", None)
        selected_variation = listing_facts.get("selected_variation")
        if isinstance(selected_variation, dict):
            listing_facts["selected_variation"] = _remove_embedded_identity(
                selected_variation
            )

    operational_data = projected.get("operational_data")
    if isinstance(operational_data, (dict, list)):
        projected["operational_data"] = _remove_embedded_identity(operational_data)

    return {
        key: value
        for key, value in projected.items()
        if value not in (None, "", [], {})
    }


def unified_initial_context_for_model(initial_context: Mapping[str, Any]) -> dict[str, Any]:
    """Project collected Context Hub data without mutating the server copy."""

    projected = deepcopy(dict(initial_context))
    packet = projected.get("sku_question_context")
    if isinstance(packet, dict):
        compact_packet = context_hub_packet_for_model(packet)
        if compact_packet:
            projected["sku_question_context"] = compact_packet
        else:
            projected.pop("sku_question_context", None)
    return projected


def unified_agent_input_for_model(
    agent_input: dict,
    *,
    server_identity: dict,
    response_signature: str,
) -> dict:
    """Return answer facts without server-only control and identity metadata.

    The complete input remains available to server-side authorization,
    research, diagnostics and evidence persistence. Context Hub/Obsidian
    documents are projected separately by ``unified_initial_context_for_model``.
    """

    projected = deepcopy(agent_input)
    for key in _MODEL_INTERNAL_AGENT_KEYS:
        projected.pop(key, None)

    question = projected.get("question") if isinstance(projected.get("question"), dict) else {}
    item = projected.get("item") if isinstance(projected.get("item"), dict) else {}
    input_context = projected.get("context") if isinstance(projected.get("context"), dict) else {}

    canonical_permalink = item.get("permalink") or item.get("url") or item.get("link")
    for alias in ("link", "url"):
        _remove_matching(item, alias, canonical_permalink)
    for key in _ITEM_INTERNAL_KEYS:
        item.pop(key, None)
    variations = item.get("variations")
    if isinstance(variations, list):
        item["variations"] = [
            {
                key: value
                for key, value in variation.items()
                if key not in {"id", "variation_id"}
            }
            if isinstance(variation, dict) else variation
            for variation in variations
        ]

    for key in _QUESTION_INTERNAL_KEYS:
        question.pop(key, None)

    duplicate_context_values = {
        "titulo": item.get("title"),
        "permalink": canonical_permalink,
        "descricao": item.get("description"),
        "pergunta": question.get("text"),
        "assinatura_obrigatoria": response_signature,
        "intencao_atendimento": projected.get("intent"),
    }
    for key in _CONTEXT_INTERNAL_KEYS:
        input_context.pop(key, None)
    for key, expected in duplicate_context_values.items():
        _remove_matching(input_context, key, expected)
    input_context.pop("descricao_chars", None)
    input_context.pop("descricao_disponivel", None)
    if input_context.get("busca_outra_peca") in (None, "", [], {}):
        input_context.pop("busca_outra_peca", None)

    if item:
        projected["item"] = item
    else:
        projected.pop("item", None)
    if question:
        projected["question"] = question
    else:
        projected.pop("question", None)
    if input_context:
        projected["context"] = input_context
    else:
        projected.pop("context", None)
    return projected


__all__ = [
    "compact_server_identity_for_model",
    "context_hub_packet_for_model",
    "unified_agent_input_for_model",
    "unified_initial_context_for_model",
]
