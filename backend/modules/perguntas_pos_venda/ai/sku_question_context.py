"""Compact, SKU-bound Context Hub and live-data packet for public questions."""

from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


SKU_QUESTION_CONTEXT_SCHEMA = "jk_ml_sku_question_context_v1"
ADAPTIVE_PUBLIC_FLOW_POLICY = "adaptive-public-flow-v1"
SKU_QUESTION_CONTEXT_MAX_CHARS = 8_000
SKU_QUESTION_STABLE_FACTS_MAX_CHARS = 4_000
SKU_QUESTION_OPERATIONAL_FACTS_MAX_CHARS = 2_000
SKU_QUESTION_TEXT_MAX_CHARS = 1_500
SIMPLE_PUBLIC_PROMPT_MAX_CHARS = 12_000
HIGH_RISK_STAGE_PROMPT_MAX_CHARS = 24_000
GLOBAL_TRANSPORT_PROMPT_MAX_CHARS = 52_000
ROUTE_SIMPLE_OPERATIONAL = "simple_operational"
ROUTE_SIMPLE_FACTUAL = "simple_factual"
ROUTE_HIGH_RISK = "high_risk"
ROUTE_LEGACY_POLICY = "legacy_policy"
_OPERATIONAL_CATEGORIES = frozenset({"greeting", "price", "stock", "shipping", "invoice"})
_UNCHANGED_POLICY_CATEGORIES = frozenset({
    "post_sale", "regulated_product", "prohibited_contact", "unknown",
})
_HIGH_RISK_CATEGORIES = frozenset({"compatibility", "warranty_originality"})
_FACTUAL_TRUTH_CLASSES = frozenset({
    "canonical", "source", "generated_verified", "versioned_technical",
})
_ADVISORY_TRUTH_CLASSES = frozenset({"human_curated"})
_HIGH_RISK_MARKERS = (
    "compat", "serve", "encaix", "aplica", "instala", "funciona em",
    "original", "genuin", "autentic", "procedencia", "garantia", "falsific",
)
_ORIGINALITY_MARKERS = ("original", "genuin", "autentic", "procedencia", "falsific", "garantia")
_COMPATIBILITY_MARKERS = ("compat", "serve", "encaix", "aplica", "instala", "funciona em")
_STOP_WORDS = frozenset({
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "ela", "ele", "em", "esse", "esta", "este", "eu", "no", "nos", "o",
    "os", "ou", "para", "por", "qual", "que", "se", "sem", "tem", "uma", "um",
    "voces", "voce", "produto", "pergunta", "responder", "informado",
})
_IDENTITY_KEYS = (
    "store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id",
)
_IDENTITY_ITEM_KEYS = {
    "sku": ("seller_sku", "sku", "codigo", "codigo_produto"),
    "item_id": ("id", "item_id"),
    "variation_id": ("variation_id", "selected_variation_id"),
    "seller_id": ("seller_id",),
    "site_id": ("site_id",),
}
_OPERATIONAL_FIELDS = {
    "price": frozenset({"price", "base_price", "original_price", "item_price", "preco", "currency_id"}),
    "stock": frozenset({
        "available_quantity", "item_available_quantity", "saldo_loja", "saldo_full",
        "status", "situacao", "selected_variation", "variations",
    }),
    "shipping": frozenset({"shipping", "free_shipping", "store_pick_up", "logistic_type", "mode", "status"}),
    "invoice": frozenset({"ncm", "cest", "tipo", "situacao"}),
    "greeting": frozenset(),
}
_TECHNICAL_FIELDS = frozenset({
    "id", "title", "name", "nome", "seller_sku", "sku", "marca", "brand",
    "attributes", "attribute_combinations", "selected_variation", "variations",
    "description", "details", "category_id", "catalog_product_id", "condition",
    "warranty", "garantia_anuncio", "unidade", "formato", "tipo", "ncm", "cest",
})
def _plain(value: object, maximum: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[dado removido]", text)
    text = re.sub(r"(?<!\w)(?:\+?\d[\s().-]*){10,15}(?!\w)", "[dado removido]", text)
    text = re.sub(r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])", "[dado removido]", text, flags=re.I)
    text = re.sub(r"(?i)(?:[A-Z]:[/\\]|file://)[^\s]+", "[caminho removido]", text)
    return text[: max(0, int(maximum))]
def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", _plain(value, 4_000).casefold())
    return "".join(char for char in text if not unicodedata.combining(char))
def _terms(*values: object) -> set[str]:
    words: set[str] = set()
    for value in values:
        for word in re.findall(r"[a-z0-9][a-z0-9._+-]{2,}", _normalized(value)):
            if word not in _STOP_WORDS:
                words.add(word)
    return words
def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
def _bounded_value(value: object, maximum: int = 320, *, depth: int = 0) -> object:
    if depth >= 4:
        return "[compactado]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _plain(value, maximum)
    if isinstance(value, Mapping):
        return {
            _plain(key, 80): _bounded_value(item, maximum, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_bounded_value(item, maximum, depth=depth + 1) for item in list(value)[:12]]
    return _plain(value, maximum)


def _classification(agent_input: Mapping[str, Any]) -> dict[str, Any]:
    intent = agent_input.get("intent") if isinstance(agent_input.get("intent"), Mapping) else {}
    if not intent:
        context = agent_input.get("context") if isinstance(agent_input.get("context"), Mapping) else {}
        intent = context.get("intencao_atendimento") if isinstance(context.get("intencao_atendimento"), Mapping) else {}
    return dict(intent)


def _category(agent_input: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    classification = _classification(agent_input)
    return _plain(
        classification.get("categoria") or agent_input.get("category") or metadata.get("category"), 80,
    ).casefold().replace("-", "_").replace(" ", "_")


def _subquestions(agent_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    classification = _classification(agent_input)
    raw = agent_input.get("subquestions")
    if not isinstance(raw, list):
        raw = classification.get("subperguntas")
    result: list[dict[str, Any]] = []
    for value in list(raw or [])[:8]:
        if not isinstance(value, Mapping):
            continue
        projected = {
            "intent": _plain(value.get("intent") or value.get("category"), 80),
            "question": _plain(value.get("question") or value.get("pergunta"), 320),
            "required_evidence": _plain(value.get("required_evidence") or value.get("evidencia_necessaria"), 240),
        }
        projected = {key: item for key, item in projected.items() if item}
        if projected:
            result.append(projected)
    return result


def _question_projection(agent_input: Mapping[str, Any]) -> dict[str, Any]:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), Mapping) else {}
    projected: dict[str, Any] = {"text": _plain(question.get("text"), 1_100)}
    history: list[dict[str, str]] = []
    for item in list(question.get("history") or [])[-2:]:
        if not isinstance(item, Mapping):
            continue
        text = _plain(item.get("text") or item.get("question") or item.get("answer"), 180)
        if text:
            history.append({
                "role": "seller" if _plain(item.get("role") or item.get("from_role"), 20).casefold() in {"seller", "loja", "store"} else "buyer",
                "text": text,
            })
    if history:
        projected["history"] = history
    while len(_json_text(projected)) > SKU_QUESTION_TEXT_MAX_CHARS and projected.get("history"):
        projected["history"].pop(0)
    return projected


def _identity_projection(agent_input: Mapping[str, Any], metadata: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), Mapping) else {}
    supplied = agent_input.get("product_evidence_identity")
    supplied = supplied if isinstance(supplied, Mapping) else {}
    identity = {key: _plain(supplied.get(key), 160) for key in _IDENTITY_KEYS}
    identity["store_ref"] = identity["store_ref"] or _plain(agent_input.get("store"), 160)
    observed: dict[str, str] = {}
    for target, candidates in _IDENTITY_ITEM_KEYS.items():
        for source in (item, context, metadata):
            for candidate in candidates:
                value = _plain(source.get(candidate), 160)
                if value:
                    observed[target] = value
                    break
            if observed.get(target):
                break
        identity[target] = identity.get(target) or observed.get(target, "")
    mismatch = any(
        supplied.get(key) and observed.get(key)
        and _normalized(supplied.get(key)) != _normalized(observed.get(key))
        for key in ("sku", "item_id", "variation_id", "seller_id", "site_id")
    )
    return {key: value for key, value in identity.items() if value}, mismatch


def _source_result(source: object) -> tuple[str, dict[str, Any]]:
    payload = source if isinstance(source, Mapping) else {}
    function = _plain(payload.get("function"), 80)
    result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
    return function, dict(result)


def _selected_internal_facts(
    internal_sources: Sequence[Mapping[str, Any]], *, category: str,
    focus_terms: set[str], high_risk: bool,
) -> list[dict[str, Any]]:
    operational_requested = set(_OPERATIONAL_FIELDS.get(category, frozenset()))
    requested = set(operational_requested)
    if high_risk or category in {"product_feature", "other_product", "warranty_originality", "compatibility"}:
        requested.update(_TECHNICAL_FIELDS)
    records: list[dict[str, Any]] = []
    for raw_source in list(internal_sources or [])[:6]:
        function, result = _source_result(raw_source)
        authority = _plain(result.get("source_authority"), 80)
        for match in list(result.get("matches") or [])[:3]:
            if not isinstance(match, Mapping):
                continue
            fields: dict[str, Any] = {}
            for key, value in match.items():
                normalized_key = _plain(key, 80)
                if normalized_key not in requested:
                    continue
                if function == "get_product_data" and normalized_key in operational_requested:
                    continue
                if normalized_key in {"description", "details"} and focus_terms:
                    if not _terms(value).intersection(focus_terms):
                        continue
                if normalized_key in {"attributes", "attribute_combinations"} and focus_terms:
                    value = [
                        item for item in list(value or [])[:20]
                        if isinstance(item, Mapping) and _terms(_json_text(item)).intersection(focus_terms)
                    ]
                    if not value:
                        continue
                fields[normalized_key] = _bounded_value(value, 300)
            if fields:
                records.append({
                    "source": function,
                    "authority": authority,
                    "current_operational": function in {"get_mercado_livre_listing", "get_bling_product"},
                    "fields": fields,
                })
    selected: list[dict[str, Any]] = []
    for record in records:
        if len(_json_text([*selected, record])) <= SKU_QUESTION_OPERATIONAL_FACTS_MAX_CHARS:
            selected.append(record)
    return selected


def _is_stale(valid_to: object) -> bool:
    raw = _plain(valid_to, 64)
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _compact_coverage(coverage: object, focus_terms: set[str]) -> dict[str, Any]:
    payload = coverage if isinstance(coverage, Mapping) else {}
    binding = payload.get("source_binding") if isinstance(payload.get("source_binding"), Mapping) else {}
    rules: list[tuple[int, dict[str, Any]]] = []
    for raw_rule in list(payload.get("rules") or [])[:40]:
        if not isinstance(raw_rule, Mapping):
            continue
        projected = {
            key: _bounded_value(raw_rule.get(key), 260)
            for key in ("rule_id", "coverage_mode", "target_kind", "scope", "target_expression", "conditions")
            if raw_rule.get(key) not in (None, "", [], {})
        }
        overlap = len(_terms(_json_text(projected)).intersection(focus_terms))
        if focus_terms and overlap == 0:
            continue
        rules.append((overlap, projected))
    rules.sort(key=lambda item: (-item[0], _json_text(item[1])))
    selected = [item for _score, item in rules[:2]]
    if not selected:
        return {}
    return {
        "rules": selected,
        "source": {
            "doc_id": _plain(binding.get("doc_id"), 160),
            "source_hash": _plain(binding.get("source_hash"), 128),
            "generation_id": _plain(binding.get("generation_id"), 160),
        },
    }


def _hub_projection(
    hub: Mapping[str, Any], *, focus_terms: set[str], max_facts: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _function, result = _source_result(hub)
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    scored: list[tuple[int, float, dict[str, Any]]] = []
    coverages: list[dict[str, Any]] = []
    seen_coverage: set[tuple[str, str, str]] = set()
    stale_count = conflict_count = advisory_count = discarded_count = 0
    coverage_candidates = 0
    for row in rows[:12]:
        if not isinstance(row, Mapping):
            continue
        truth_class = _plain(row.get("truth_class"), 80).casefold()
        factual = bool(row.get("eligible_as_factual_evidence") or truth_class in _FACTUAL_TRUTH_CLASSES)
        advisory = truth_class in _ADVISORY_TRUTH_CLASSES
        snippet = _plain(row.get("snippet"), 500)
        overlap = len(_terms(row.get("title"), snippet).intersection(focus_terms))
        if not factual and not advisory:
            discarded_count += 1
            conflict_count += int("conflict" in truth_class)
            continue
        if focus_terms and overlap == 0:
            discarded_count += 1
            continue
        stale = _is_stale(row.get("valid_to"))
        stale_count += int(stale)
        conflict = "conflict" in truth_class or bool(row.get("conflict"))
        conflict_count += int(conflict)
        advisory_count += int(advisory)
        fact = {
            "fact_id": _plain(row.get("doc_id"), 200),
            "snippet": snippet,
            "truth_class": truth_class,
            "authority": "advisory" if advisory else "canonical",
            "source_hash": _plain(row.get("source_hash"), 128),
            "generation_id": _plain(row.get("generation_id") or result.get("generation_id"), 160),
            "valid_to": _plain(row.get("valid_to"), 64),
            "stale": stale,
            "conflict": conflict,
        }
        scored.append((overlap, float(row.get("score") or 0.0), {
            key: value for key, value in fact.items() if value not in ("", False)
        }))
        compact_coverage = _compact_coverage(row.get("compatibility_coverage"), focus_terms)
        if compact_coverage:
            coverage_candidates += 1
        binding = compact_coverage.get("source") if isinstance(compact_coverage.get("source"), Mapping) else {}
        coverage_key = (
            _plain(binding.get("doc_id"), 160), _plain(binding.get("source_hash"), 128),
            _plain(binding.get("generation_id"), 160),
        )
        if compact_coverage and coverage_key not in seen_coverage:
            seen_coverage.add(coverage_key)
            coverages.append(compact_coverage)
    scored.sort(key=lambda item: (-item[0], -item[1], _json_text(item[2])))
    facts: list[dict[str, Any]] = []
    for _overlap, _score, fact in scored:
        if len(facts) >= max_facts:
            break
        if len(_json_text([*facts, fact])) > SKU_QUESTION_STABLE_FACTS_MAX_CHARS:
            discarded_count += 1
            continue
        facts.append(fact)
    return facts, coverages[:1], {
        "hub_found": bool(result.get("found")),
        "hub_unavailable": bool(result.get("unavailable")),
        "stale_count": stale_count,
        "conflict_count": conflict_count,
        "advisory_count": advisory_count,
        "discarded_count": discarded_count,
        "deduplicated_coverage_count": max(0, coverage_candidates - len(coverages[:1])),
        "generation_id": _plain(result.get("generation_id"), 160),
    }


def _verified_fact_projection(values: Iterable[Mapping[str, Any]], focus_terms: set[str]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for raw in list(values)[:24]:
        if not isinstance(raw, Mapping):
            continue
        field = _plain(raw.get("field_name"), 96).casefold()
        value = _plain(raw.get("value"), 280)
        if not field or not value:
            continue
        if focus_terms and not _terms(field, value).intersection(focus_terms):
            continue
        fact = {
            "field_name": field,
            "scope": _plain(raw.get("scope"), 32),
            "value": value,
            "unit": _plain(raw.get("unit"), 24),
            "state": _plain(raw.get("state"), 32),
            "activation_policy": _plain(raw.get("activation_policy"), 64),
            "source_authorities": [_plain(item, 40) for item in list(raw.get("source_authorities") or [])[:4]],
        }
        facts.append({key: item for key, item in fact.items() if item not in ("", [])})
    return facts


def _external_projection(
    external_sources: Sequence[Mapping[str, Any]], focus_terms: set[str], *, max_facts: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    remaining = max(0, int(max_facts))
    for source in list(external_sources or [])[:6]:
        if remaining <= 0:
            break
        function, result = _source_result(source)
        verified: list[dict[str, Any]] = []
        for key in ("verified_product_evidence", "verified_target_evidence", "product_research_evidence"):
            raw = result.get(key) if isinstance(result.get(key), list) else []
            verified.extend(_verified_fact_projection(raw, focus_terms))
        context = _plain(result.get("context"), 500)
        if verified:
            # Prefer structured evidence. The narrative research dossier is a
            # fallback only and must not be repeated beside equivalent facts.
            context = ""
        if context and focus_terms and not _terms(context).intersection(focus_terms):
            context = ""
        verified = verified[:remaining]
        record = {"source": function, "verified_facts": verified, "context": context,
                  "found": bool(result.get("found") or verified or context)}
        if record["found"]:
            selected.append({key: value for key, value in record.items() if value not in ("", [])})
            remaining -= len(verified) if verified else int(bool(context))
    return selected[:4]


def _canonical_reference_projection(reference: Mapping[str, Any] | None) -> dict[str, Any]:
    source = reference if isinstance(reference, Mapping) else {}
    projected = {
        key: _bounded_value(source.get(key), 240)
        for key in (
            "schema", "decision", "commercial_state", "reason", "confidence",
            "product_interface", "target_interface", "missing_fields", "doc_id",
            "source_hash", "generation_id",
        )
        if source.get(key) not in (None, "", [], {})
    }
    binding = source.get("source_binding") if isinstance(source.get("source_binding"), Mapping) else {}
    if binding:
        projected["source"] = {
            key: _plain(binding.get(key), 160)
            for key in ("doc_id", "source_hash", "generation_id") if binding.get(key)
        }
    return projected


def _route_and_reasons(
    *, category: str, question_text: str, subquestions: Sequence[Mapping[str, Any]],
    authoritative_facts: int, metrics: Mapping[str, Any], classification: Mapping[str, Any],
    identity_mismatch: bool, force_high_risk: bool,
) -> tuple[str, list[str], bool]:
    if category in _UNCHANGED_POLICY_CATEGORIES:
        return ROUTE_LEGACY_POLICY, ["unchanged_existing_policy"], False
    combined = _normalized(" ".join([
        question_text,
        *(_plain(value.get("intent"), 80) + " " + _plain(value.get("question"), 320) for value in subquestions),
    ]))
    compatibility = category == "compatibility" or any(marker in combined for marker in _COMPATIBILITY_MARKERS)
    originality = category == "warranty_originality" or any(marker in combined for marker in _ORIGINALITY_MARKERS)
    try:
        confidence = float(classification.get("confianca") or classification.get("confidence") or 1.0)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    reasons: list[str] = []
    if compatibility:
        reasons.append("compatibility_required")
    if originality:
        reasons.append("originality_required")
    if identity_mismatch:
        reasons.append("identity_mismatch")
    if int(metrics.get("conflict_count") or 0):
        reasons.append("evidence_conflict")
    if int(metrics.get("stale_count") or 0):
        reasons.append("stale_evidence")
    if confidence < 0.75:
        reasons.append("low_classification_confidence")
    if category == "product_feature" and authoritative_facts <= 0:
        reasons.append("decisive_fact_missing")
    if force_high_risk and not reasons:
        reasons.append("factual_critic_escalation")
    if reasons:
        web_required = any(reason in reasons for reason in {
            "compatibility_required", "originality_required", "evidence_conflict",
            "stale_evidence", "decisive_fact_missing", "identity_mismatch",
            "factual_critic_escalation",
        })
        return ROUTE_HIGH_RISK, list(dict.fromkeys(reasons)), web_required
    if category == "product_feature":
        return ROUTE_SIMPLE_FACTUAL, ["canonical_evidence_sufficient"], False
    if category in _OPERATIONAL_CATEGORIES or category == "other_product":
        reason = "no_research_needed" if category == "greeting" else "operational_live_data_sufficient"
        return ROUTE_SIMPLE_OPERATIONAL, [reason], False
    return ROUTE_HIGH_RISK, ["decisive_fact_missing"], True


def _fit_packet_to_budget(packet: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    compacted = deepcopy(packet)
    dropped_decisive = False
    removable = (
        ("question", "history"), ("external_facts", None),
        ("operational_facts", None), ("stable_facts", None), ("subquestions", None),
    )
    while len(_json_text(compacted)) > SKU_QUESTION_CONTEXT_MAX_CHARS:
        removed = False
        for key, nested in removable:
            target = compacted.get(key)
            if nested and isinstance(target, dict) and target.get(nested):
                target.pop(nested, None)
                removed = True
                break
            if isinstance(target, list) and target:
                target.pop()
                dropped_decisive = dropped_decisive or key in {"stable_facts", "external_facts"}
                removed = True
                break
        if not removed:
            break
    if len(_json_text(compacted)) > SKU_QUESTION_CONTEXT_MAX_CHARS:
        compacted = {
            "schema": SKU_QUESTION_CONTEXT_SCHEMA,
            "policy": ADAPTIVE_PUBLIC_FLOW_POLICY,
            "route": ROUTE_HIGH_RISK,
            "route_reasons": ["context_budget_exceeded"],
            "web": {"required": True, "reason": "decisive_fact_missing"},
            "question": {"text": _plain((packet.get("question") or {}).get("text"), 1_000)},
            "identity": dict(packet.get("identity") or {}),
            "gaps": ["context_budget_exceeded"],
        }
        dropped_decisive = True
    return compacted, dropped_decisive


def build_sku_question_context(
    agent_input: Mapping[str, Any] | None, metadata: Mapping[str, Any] | None, *,
    internal_sources: Sequence[Mapping[str, Any]] = (), context_hub: Mapping[str, Any] | None = None,
    external_sources: Sequence[Mapping[str, Any]] = (), canonical_reference: Mapping[str, Any] | None = None,
    force_high_risk: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a closed, bounded public-question evidence packet and safe metrics."""

    source = agent_input if isinstance(agent_input, Mapping) else {}
    metadata_dict = metadata if isinstance(metadata, Mapping) else {}
    classification = _classification(source)
    category = _category(source, metadata_dict)
    question = _question_projection(source)
    subquestions = _subquestions(source)
    compatibility = classification.get("compatibilidade")
    compatibility = compatibility if isinstance(compatibility, Mapping) else {}
    relevance_terms = _terms(
        question.get("text"), *(_json_text(value) for value in subquestions),
        compatibility.get("technical_focus"), compatibility.get("target_item"),
        compatibility.get("required_evidence"), compatibility.get("decisive_fields"),
    )
    identity, identity_mismatch = _identity_projection(source, metadata_dict)
    preliminary_high_risk = bool(
        category in _HIGH_RISK_CATEGORIES or force_high_risk
        or any(marker in _normalized(question.get("text")) for marker in _HIGH_RISK_MARKERS)
    )
    stable_fact_limit = 4 if external_sources else (8 if preliminary_high_risk else 4)
    stable_facts, coverages, hub_metrics = _hub_projection(
        context_hub or {}, focus_terms=relevance_terms,
        max_facts=stable_fact_limit,
    )
    verified = _verified_fact_projection(
        source.get("verified_product_evidence")
        if isinstance(source.get("verified_product_evidence"), list) else [],
        relevance_terms,
    )
    for fact in verified:
        if len(stable_facts) >= stable_fact_limit:
            break
        stable_facts.append(fact)
    authoritative_facts = sum(
        1 for fact in stable_facts
        if fact.get("truth_class") in _FACTUAL_TRUTH_CLASSES or fact.get("activation_policy")
    )
    route, reasons, web_required = _route_and_reasons(
        category=category, question_text=str(question.get("text") or ""),
        subquestions=subquestions, authoritative_facts=authoritative_facts,
        metrics=hub_metrics, classification=classification,
        identity_mismatch=identity_mismatch, force_high_risk=force_high_risk,
    )
    operational = _selected_internal_facts(
        internal_sources, category=category, focus_terms=relevance_terms,
        high_risk=route == ROUTE_HIGH_RISK,
    )
    fact_limit = 8 if route == ROUTE_HIGH_RISK else 4
    external = _external_projection(
        external_sources, relevance_terms,
        max_facts=max(0, fact_limit - len(stable_facts)),
    )
    if web_required:
        web_reason = next((reason for reason in (
            "compatibility_required", "originality_required", "evidence_conflict",
            "decisive_fact_missing", "stale_evidence",
        ) if reason in reasons), "decisive_fact_missing")
    elif "canonical_evidence_sufficient" in reasons:
        web_reason = "canonical_evidence_sufficient"
    elif "operational_live_data_sufficient" in reasons:
        web_reason = "operational_live_data_sufficient"
    else:
        web_reason = "no_research_needed"
    packet = {
        "schema": SKU_QUESTION_CONTEXT_SCHEMA,
        "policy": ADAPTIVE_PUBLIC_FLOW_POLICY,
        "route": route,
        "route_reasons": reasons,
        "category": category,
        "question": question,
        "subquestions": subquestions,
        "identity": identity,
        "required_fields": [_plain(value, 160) for value in list(compatibility.get("decisive_fields") or [])[:8] if _plain(value, 160)],
        "stable_facts": stable_facts,
        "operational_facts": operational,
        "compatibility_coverage": coverages,
        "external_facts": external,
        "canonical_reference": _canonical_reference_projection(canonical_reference),
        "conflicts": [reason for reason in reasons if reason in {"evidence_conflict", "identity_mismatch"}],
        "gaps": [reason for reason in reasons if reason in {
            "decisive_fact_missing", "stale_evidence", "identity_mismatch", "low_classification_confidence",
        }],
        "web": {
            "required": web_required,
            "reason": web_reason,
        },
        "source_generation": hub_metrics.get("generation_id") or "",
        "content_role": "untrusted_reference_data",
    }
    packet = {key: value for key, value in packet.items() if value not in ("", [], {})}
    packet, dropped_decisive = _fit_packet_to_budget(packet)
    if dropped_decisive:
        packet["route"] = ROUTE_HIGH_RISK
        packet["route_reasons"] = list(dict.fromkeys([
            *list(packet.get("route_reasons") or []), "context_budget_exceeded",
        ]))
        packet["web"] = {"required": True, "reason": "decisive_fact_missing"}
        packet["gaps"] = list(dict.fromkeys([*list(packet.get("gaps") or []), "context_budget_exceeded"]))
    metrics = {
        "schema": SKU_QUESTION_CONTEXT_SCHEMA,
        "route": packet.get("route"),
        "route_reasons": list(packet.get("route_reasons") or []),
        "packet_chars": len(_json_text(packet)),
        "selected_stable_fact_count": len(list(packet.get("stable_facts") or [])),
        "selected_operational_source_count": len(list(packet.get("operational_facts") or [])),
        "selected_external_source_count": len(list(packet.get("external_facts") or [])),
        "discarded_fact_count": int(hub_metrics.get("discarded_count") or 0),
        "deduplicated_coverage_count": int(hub_metrics.get("deduplicated_coverage_count") or 0),
        "web_required": bool((packet.get("web") or {}).get("required")),
        "web_reason": _plain((packet.get("web") or {}).get("reason"), 80),
        "context_budget_exceeded": dropped_decisive,
    }
    return packet, metrics


def packet_tool_result(packet: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "function": "sku_question_context",
        "arguments": {"schema": SKU_QUESTION_CONTEXT_SCHEMA},
        "result": deepcopy(dict(packet)),
    }


def with_document_references(
    packet: Mapping[str, Any], references: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Attach bounded, path-free document identifiers without breaking the packet budget."""

    projected = deepcopy(dict(packet))
    compact_refs: list[dict[str, Any]] = []
    for raw in list(references or [])[:8]:
        if not isinstance(raw, Mapping):
            continue
        content_hash = _plain(raw.get("content_hash") or raw.get("hash"), 128)
        try:
            page = max(0, int(raw.get("page") or 0))
        except (TypeError, ValueError, OverflowError):
            page = 0
        generation = _plain(raw.get("generation_id") or raw.get("generation"), 120)
        reference = {
            "reference_id": f"{content_hash[:16]}:p{page}" if content_hash else f"document:p{page}",
            "content_hash": content_hash,
            "page": page,
            "generation": generation,
        }
        reference = {key: value for key, value in reference.items() if value not in ("", None)}
        candidate = deepcopy(projected)
        candidate["technical_references"] = [*compact_refs, reference]
        if len(_json_text(candidate)) > SKU_QUESTION_CONTEXT_MAX_CHARS:
            break
        compact_refs.append(reference)
    if compact_refs:
        projected["technical_references"] = compact_refs
    return projected


def bind_client_sku_question_context(
    client: Any,
    metadata: Mapping[str, Any] | None,
    *,
    internal_sources: Sequence[Mapping[str, Any]] = (),
    context_hub: Mapping[str, Any] | None = None,
    external_sources: Sequence[Mapping[str, Any]] = (),
    canonical_reference: Mapping[str, Any] | None = None,
    force_high_risk: bool = False,
) -> dict[str, Any]:
    if internal_sources:
        client._sku_context_internal_sources = list(internal_sources)
    if context_hub is not None:
        client._sku_context_hub = dict(context_hub)
    if external_sources:
        existing_external = list(getattr(client, "_sku_context_external_sources", []) or [])
        client._sku_context_external_sources = [*existing_external, *list(external_sources)][-8:]
    if canonical_reference is not None:
        client._sku_context_canonical_reference = dict(canonical_reference)
    effective_internal = list(getattr(client, "_sku_context_internal_sources", []) or [])
    effective_hub = dict(getattr(client, "_sku_context_hub", {}) or {})
    effective_external = list(getattr(client, "_sku_context_external_sources", []) or [])
    effective_canonical = dict(getattr(client, "_sku_context_canonical_reference", {}) or {})
    packet, metrics = build_sku_question_context(
        getattr(client, "agent_input", {}), metadata,
        internal_sources=effective_internal, context_hub=effective_hub,
        external_sources=effective_external, canonical_reference=effective_canonical,
        force_high_risk=force_high_risk,
    )
    client.sku_question_context = packet
    client.sku_question_context_metrics = {
        **dict(getattr(client, "sku_question_context_metrics", {}) or {}),
        **metrics,
        "model_call_count": int(
            (getattr(client, "sku_question_context_metrics", {}) or {}).get("model_call_count") or 0
        ),
        "max_prompt_chars": int(
            (getattr(client, "sku_question_context_metrics", {}) or {}).get("max_prompt_chars") or 0
        ),
    }
    client.adaptive_route = str(packet.get("route") or ROUTE_HIGH_RISK)
    client.agent_input["sku_question_context"] = deepcopy(packet)
    client.agent_input["adaptive_route"] = client.adaptive_route
    client.agent_input["web_research_reason"] = str((packet.get("web") or {}).get("reason") or "")
    web_required = bool((packet.get("web") or {}).get("required"))
    client.agent_input["use_web_search"] = web_required
    client.agent_input["web_search_required"] = web_required
    allowed = [
        str(value) for value in list(client.agent_input.get("allowed_tools") or [])
        if not str(value).startswith("web_search")
    ]
    if web_required:
        allowed.extend(["web_search", "web_search_product_identity", "web_search_question_context"])
    client.agent_input["allowed_tools"] = list(dict.fromkeys(allowed))
    for stage in reversed(list(getattr(client, "context_pipeline", []) or [])):
        if not isinstance(stage, dict) or stage.get("name") not in {
            "context_hub_sku_reference", "listing_product_analysis",
            "buyer_question_history_and_listing_snapshot",
        }:
            continue
        stage["sku_question_context"] = deepcopy(client.sku_question_context_metrics)
        break
    return packet


__all__ = [
    "ADAPTIVE_PUBLIC_FLOW_POLICY", "GLOBAL_TRANSPORT_PROMPT_MAX_CHARS",
    "HIGH_RISK_STAGE_PROMPT_MAX_CHARS", "ROUTE_HIGH_RISK", "ROUTE_LEGACY_POLICY",
    "ROUTE_SIMPLE_FACTUAL", "ROUTE_SIMPLE_OPERATIONAL", "SIMPLE_PUBLIC_PROMPT_MAX_CHARS",
    "SKU_QUESTION_CONTEXT_MAX_CHARS", "SKU_QUESTION_CONTEXT_SCHEMA",
    "bind_client_sku_question_context", "build_sku_question_context", "packet_tool_result",
    "with_document_references",
]
