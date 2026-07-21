"""Normalization helpers and taxonomies for Black Jhon V3 contracts."""

from __future__ import annotations

from typing import Any, Callable, Mapping


CONVERSATION_DECISION_V3 = "jk.whatsapp.conversation-decision.v3"
RETRIEVAL_RESULT_V3 = "jk.context-hub.retrieval.v3"

INTENT_REGISTRY_V1 = (
    "general.conversation",
    "general.knowledge",
    "general.current_information",
    "commerce.product",
    "commerce.stock",
    "commerce.sales",
    "commerce.report",
    "mercado_livre.listing",
    "mercado_livre.order",
    "mercado_livre.question",
    "mercado_livre.claim",
    "mercado_livre.return",
    "mercado_livre.shipment",
    "mercado_livre.payment",
    "mercado_livre.catalog",
    "system.task_status",
    "system.task_control",
    "system.mutation_request",
    "clarification.required",
    "other",
)

ENTITY_TYPES_V1 = (
    "store",
    "sku",
    "mlb",
    "order_id",
    "pack_id",
    "claim_id",
    "return_id",
    "shipment_id",
    "payment_id",
    "question_id",
    "category_id",
    "product_id",
    "user_product_id",
    "period",
    "metric",
    "aggregation",
    "subject",
)

OPERATION_CLASSES_V1 = (
    "general_reply",
    "read",
    "sensitive_read",
    "mutation_request",
)


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[: max(0, int(limit))]


def _clean_list(value: Any, *, item_limit: int, max_items: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    output: list[str] = []
    for raw in list(value)[:max_items]:
        item = _clean_text(raw, item_limit)
        if item and item not in output:
            output.append(item)
    return output


def _normalize_intent_id(value: Any, fallback: Any = "") -> str:
    candidate = _clean_text(value, 120).casefold().replace(" ", "_")
    if candidate in INTENT_REGISTRY_V1:
        return candidate
    aliases = {
        "conversation_reply": "general.conversation",
        "task_delegation": "other",
        "task_queue": "other",
        "task_update": "other",
        "task_cancel": "system.task_control",
        "status_wait": "system.task_status",
        "clarification": "clarification.required",
        "sku_stock": "commerce.stock",
        "current_store_stock": "commerce.stock",
        "mutation_candidate": "system.mutation_request",
    }
    return aliases.get(_clean_text(fallback, 120).casefold(), "other")


def _normalize_entity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    entity_type = _clean_text(value.get("type"), 80).casefold()
    entity_value = _clean_text(value.get("value"), 500)
    if entity_type not in ENTITY_TYPES_V1 or not entity_value:
        return None
    source = _clean_text(value.get("source"), 20).casefold()
    if source not in {"message", "memory", "tool", "inference"}:
        source = "inference"
    confidence = _clean_text(value.get("confidence"), 20).casefold()
    if confidence not in {"high", "medium", "low", "unknown"}:
        confidence = "unknown"
    return {
        "type": entity_type,
        "value": entity_value,
        "source": source,
        "confidence": confidence,
        "confirmed": value.get("confirmed") is True,
    }


def normalize_conversation_decision_v3(value: Any) -> dict[str, Any]:
    """Normalize native V3 or enrich a V1/V2 decision without authorizing it."""

    source = dict(value) if isinstance(value, Mapping) else {}
    source_schema = _clean_text(source.get("schema_version"), 120) or "legacy-v1"
    resolved = source.get("resolved_context") if isinstance(source.get("resolved_context"), Mapping) else {}
    raw_entities = source.get("entities") if isinstance(source.get("entities"), (list, tuple)) else []
    entities = [item for item in (_normalize_entity(raw) for raw in raw_entities[:24]) if item]
    existing_types = {item["type"] for item in entities}
    for entity_type, key in (("store", "store"), ("sku", "sku"), ("mlb", "mlb"), ("period", "period")):
        entity_value = _clean_text(resolved.get(key), 500)
        if entity_value and entity_type not in existing_types:
            entities.append({
                "type": entity_type,
                "value": entity_value,
                "source": "memory",
                "confidence": "high",
                "confirmed": True,
            })
    raw_scope = source.get("scope") if isinstance(source.get("scope"), Mapping) else {}
    store_mode = _clean_text(raw_scope.get("store_mode") or resolved.get("store_mode"), 20).casefold()
    if store_mode not in {"none", "single", "all"}:
        store_mode = "none"
    store_refs = _clean_list(raw_scope.get("store_refs"), item_limit=200, max_items=20)
    if not store_refs and store_mode == "single" and _clean_text(resolved.get("store"), 200):
        store_refs = [_clean_text(resolved.get("store"), 200)]
    raw_risk = source.get("risk") if isinstance(source.get("risk"), Mapping) else {}
    operation_class = _clean_text(raw_risk.get("operation_class"), 40).casefold()
    action = _clean_text(source.get("action"), 40).casefold()
    intent_id = _normalize_intent_id(source.get("intent_id"), source.get("intent"))
    if operation_class not in OPERATION_CLASSES_V1:
        operation_class = (
            "mutation_request" if intent_id == "system.mutation_request"
            else "read" if action in {"delegate", "queue", "steer"}
            else "general_reply"
        )
    level = _clean_text(raw_risk.get("level"), 20).casefold()
    if level not in {"low", "medium", "high", "critical"}:
        level = "high" if operation_class == "mutation_request" else "medium" if operation_class == "sensitive_read" else "low"
    ambiguities: list[dict[str, Any]] = []
    for raw in list(source.get("ambiguities") or [])[:4]:
        if not isinstance(raw, Mapping):
            continue
        interpretations = _clean_list(raw.get("interpretations"), item_limit=300, max_items=4)
        if len(interpretations) < 2:
            continue
        ambiguities.append({
            "field": _clean_text(raw.get("field"), 120),
            "interpretations": interpretations,
            "question": _clean_text(raw.get("question"), 420),
            "material": raw.get("material") is True,
        })
    compatibility_keys = {
        "action", "confidence", "intent", "job_prompt", "job_title", "missing_fields",
        "needs_user_input", "related_job_id", "reply_text", "requires_web", "resolved_context",
        "response_mode", "subtasks", "task",
    }
    result = {key: source[key] for key in compatibility_keys if key in source}
    result.update({
        "schema_version": CONVERSATION_DECISION_V3,
        "source_schema_version": source_schema,
        "intent_id": intent_id,
        "intent_path": _clean_list(source.get("intent_path"), item_limit=120, max_items=6) or intent_id.split("."),
        "entities": entities[:24],
        "scope": {
            "tenant_source": "server",
            "store_mode": store_mode,
            "store_refs": store_refs if store_mode == "single" else [],
            "period": _clean_text(raw_scope.get("period") or resolved.get("period"), 240),
            "subject": _clean_text(raw_scope.get("subject"), 240),
            "module": _clean_text(raw_scope.get("module"), 120),
        },
        "risk": {
            "operation_class": operation_class,
            "level": level,
            "requires_human_review": raw_risk.get("requires_human_review") is True or operation_class == "mutation_request",
            "reason_codes": _clean_list(raw_risk.get("reason_codes"), item_limit=80, max_items=10),
        },
        "ambiguities": ambiguities,
    })
    return result


def normalize_retrieval_result_v3(
    value: Any,
    *,
    normalize_v2: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Normalize Context Hub V3 while retaining the V2 evidence fields."""

    source = dict(value) if isinstance(value, Mapping) else {}
    legacy = normalize_v2(source)
    raw_citations = source.get("citations")
    if not isinstance(raw_citations, (list, tuple)):
        raw_citations = source.get("results") if isinstance(source.get("results"), (list, tuple)) else []
    citations: list[dict[str, Any]] = []
    for raw in list(raw_citations)[:40]:
        if not isinstance(raw, Mapping):
            continue
        authority = _clean_text(raw.get("authority"), 40).casefold()
        if authority not in {"authoritative", "verified_technical", "advisory", "unverified"}:
            authority = "unverified"
        validity = _clean_text(raw.get("validity") or raw.get("validity_status"), 40).casefold()
        if validity not in {"active_generation", "unverified", "expired", "unknown"}:
            validity = "unknown"
        try:
            normalized_score = float(raw.get("normalized_score") or 0.0)
        except (TypeError, ValueError):
            normalized_score = 0.0
        citations.append({
            "citation_id": _clean_text(raw.get("citation_id"), 120),
            "doc_id": _clean_text(raw.get("doc_id"), 240),
            "chunk_id": _clean_text(raw.get("chunk_id"), 240),
            "reference": _clean_text(raw.get("reference"), 1000),
            "snippet": _clean_text(raw.get("snippet"), 4000),
            "generation_id": _clean_text(raw.get("generation_id") or source.get("generation_id"), 160),
            "source_version": _clean_text(raw.get("source_version") or source.get("source_version"), 160),
            "truth_class": _clean_text(raw.get("truth_class"), 100),
            "authority": authority,
            "validity": validity,
            "normalized_score": max(0.0, min(1.0, normalized_score)),
            "conflict": raw.get("conflict") is True,
            "conflict_with": _clean_list(raw.get("conflict_with"), item_limit=120, max_items=12),
        })
    records = list(legacy["records"])
    if citations and not isinstance(source.get("records"), (list, tuple)):
        records = [{
            "field": "context",
            "value": item["snippet"],
            "store": "",
            "period": "",
            "source": item["citation_id"] or item["reference"],
        } for item in citations if item["snippet"]][:40]
    sources = list(legacy["sources"])
    for item in citations:
        reference = item["citation_id"] or item["reference"]
        if reference and reference not in sources and len(sources) < 30:
            sources.append(reference)
    return {
        "schema_version": RETRIEVAL_RESULT_V3,
        "source_schema_version": _clean_text(source.get("schema_version"), 120) or legacy["source_schema_version"],
        "query": legacy["query"],
        "generation_id": _clean_text(source.get("generation_id") or source.get("generation"), 160),
        "source_version": _clean_text(source.get("source_version"), 160),
        "records": records,
        "citations": citations,
        "sources": sources,
        "gaps": legacy["gaps"],
        "coverage_complete": source.get("coverage_complete") is True,
        "count": len(records),
        "conflict_detected": source.get("conflict_detected") is True or any(item["conflict"] for item in citations),
        "operational_data_source": False,
        "embeddings_enabled": False,
    }


__all__ = [
    "CONVERSATION_DECISION_V3",
    "ENTITY_TYPES_V1",
    "INTENT_REGISTRY_V1",
    "OPERATION_CLASSES_V1",
    "RETRIEVAL_RESULT_V3",
    "normalize_conversation_decision_v3",
    "normalize_retrieval_result_v3",
]
