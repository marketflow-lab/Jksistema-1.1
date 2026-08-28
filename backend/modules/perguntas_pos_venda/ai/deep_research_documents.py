"""Research documents, verified-evidence persistence and coverage decisions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.product_evidence import (
    PRODUCT_EVIDENCE_SOURCE_TYPES,
    add_product_evidence_claim,
    add_product_evidence_source,
    complete_product_evidence_batch,
    create_product_evidence_batch,
    list_verified_product_evidence,
)

from .deep_research_analysis import (
    _variation_selection_state,
    classify_research_source,
    document_matches_product,
    expected_fields_for_research,
    extract_guarded_commercial_claims,
    extract_technical_claims,
    scope_research_text_to_product,
)
from .deep_research_fingerprints import (
    _deduplicate_research_documents,
    _technical_content_hash,
    _technical_copy_fingerprint,
)
from .deep_research_contracts import (
    PUBLIC_RESEARCH_POLICY,
    ResearchDocumentV1,
    TechnicalClaimV1,
    _domain,
    _registrable_domain,
    _safe_page_text,
    _safe_text,
    canonical_research_url,
    sanitize_public_research_text,
)

def make_research_document(
    *,
    item: Mapping[str, Any],
    url: str,
    query: str,
    query_type: str,
    text: str,
    agent_input: Mapping[str, Any],
) -> ResearchDocumentV1 | None:
    canonical = canonical_research_url(url)
    body = _safe_page_text(text)
    source_type = classify_research_source(item, canonical, agent_input, query_type=query_type)
    scoped_body = scope_research_text_to_product(
        body,
        agent_input,
        source_type=source_type,
    )
    if not canonical or not scoped_body or not document_matches_product(
        scoped_body, agent_input, source_type=source_type,
    ):
        return None
    if source_type not in PRODUCT_EVIDENCE_SOURCE_TYPES:
        source_type = "technical_independent"
    content_hash = _technical_content_hash(scoped_body)
    return ResearchDocumentV1(
        url=canonical,
        title=_safe_text(sanitize_public_research_text(item.get("title"), 240), 240),
        query_type=_safe_text(query_type or "web", 80),
        query=_safe_text(query, 260),
        text=scoped_body,
        content_hash=content_hash,
        source_type=source_type,
        origin_key=_registrable_domain(_domain(canonical)),
        copy_fingerprint=_technical_copy_fingerprint(scoped_body),
    )


def _listing_attribute_rows(values: object) -> list[dict[str, str]]:
    return [
        {
            "id": _safe_text(value.get("id"), 96),
            "name": _safe_text(value.get("name") or value.get("id"), 160),
            "value": _safe_text(
                value.get("value_name")
                or value.get("value_id")
                or value.get("value"),
                500,
            ),
        }
        for value in (values or [])
        if isinstance(value, Mapping)
        and str(
            value.get("value_name")
            or value.get("value_id")
            or value.get("value")
            or ""
        ).strip()
    ]


def _listing_variation_projection(
    agent_input: Mapping[str, Any],
    item: Mapping[str, Any],
) -> tuple[list[dict[str, str]], bool]:
    """Return exact-variation attributes and whether global prose is safe."""

    variations = [
        value for value in (item.get("variations") or []) if isinstance(value, Mapping)
    ]
    state = _variation_selection_state(agent_input)
    if not variations:
        return [], state == "global"
    if state != "selected":
        return [], False
    identity = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    variation_id = str(identity.get("variation_id") or "").strip()
    selected = next(
        (
            value
            for value in variations
            if str(value.get("id") or "").strip() == variation_id
        ),
        None,
    )
    if selected is None:
        return [], False
    rows = [
        *_listing_attribute_rows(selected.get("attribute_combinations")),
        *_listing_attribute_rows(selected.get("attributes")),
    ]
    for field, label in (
        ("seller_sku", "SKU da variacao"),
        ("seller_custom_field", "SKU da variacao"),
        ("sku", "SKU da variacao"),
    ):
        if str(selected.get(field) or "").strip():
            rows.append({"id": field, "name": label, "value": _safe_text(selected[field], 500)})
            break
    return rows, False


def listing_document(agent_input: Mapping[str, Any]) -> ResearchDocumentV1 | None:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    if item.get("official_current_listing") is not True:
        return None
    url = canonical_research_url(item.get("permalink") or item.get("url") or item.get("link"))
    if not url:
        return None
    variation_rows, include_global_prose = _listing_variation_projection(
        agent_input,
        item,
    )
    if not include_global_prose and not variation_rows:
        return None
    item_attribute_rows = (
        _listing_attribute_rows(item.get("attributes")) if include_global_prose else []
    )
    facts = {
        "title": (item.get("title") or "") if include_global_prose else "",
        "description": (item.get("description") or "") if include_global_prose else "",
        "condition": item.get("condition") or "",
        "attributes": [*item_attribute_rows, *variation_rows],
        "sale_terms": _listing_attribute_rows(item.get("sale_terms")),
    }
    body = " ".join(
        _safe_text(value, 20_000)
        for value in (
            facts["title"],
            facts["description"],
            f"Condicao: {facts['condition']}" if facts["condition"] else "",
            " ".join(f"{row['name']}: {row['value']}" for row in facts["attributes"]),
            " ".join(f"{row['name']}: {row['value']}" for row in facts["sale_terms"]),
        )
        if value
    )
    body = sanitize_public_research_text(body)
    if not body:
        return None
    return ResearchDocumentV1(
        url=url,
        title=_safe_text(sanitize_public_research_text(item.get("title"), 240), 240),
        query_type="official_current_listing",
        query="",
        text=body,
        content_hash=_technical_content_hash(body),
        source_type="official_listing",
        origin_key=_registrable_domain(_domain(url)),
        copy_fingerprint=_technical_copy_fingerprint(body),
    )


def evidence_identity(agent_input: Mapping[str, Any]) -> dict[str, str]:
    raw = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    return {
        "store_ref": _safe_text(raw.get("store_ref") or agent_input.get("store"), 128),
        "seller_id": _safe_text(raw.get("seller_id"), 128),
        "site_id": _safe_text(raw.get("site_id"), 32),
        "sku": _safe_text(raw.get("sku") or item.get("seller_sku"), 128),
        "item_id": _safe_text(raw.get("item_id") or item.get("id"), 128),
        "variation_id": _safe_text(raw.get("variation_id"), 128),
    }


def _identity_complete(identity: Mapping[str, str]) -> bool:
    return all(
        identity.get(key)
        for key in ("store_ref", "seller_id", "site_id", "sku", "item_id")
    )


def compact_verified_evidence(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Expose only activated facts to generation; source text and coordinates stay out."""

    compact: list[dict[str, Any]] = []
    for value in values[:120]:
        compact.append(
            {
                "field_name": _safe_text(value.get("field_name"), 96),
                "scope": _safe_text(value.get("scope"), 24),
                "value": _safe_text(value.get("value"), 256),
                "unit": _safe_text(value.get("unit"), 16),
                "activation_policy": _safe_text(value.get("activation_policy"), 64),
                "source_authorities": sorted(
                    {
                        _safe_text(source.get("authority"), 24)
                        for source in value.get("sources") or []
                        if isinstance(source, Mapping) and source.get("authority")
                    }
                ),
            }
        )
    return compact


def load_verified_product_evidence(
    client_id: str,
    identity: Mapping[str, str],
) -> list[dict[str, Any]]:
    if not _identity_complete(identity):
        return []
    try:
        values = list_verified_product_evidence(client_id, **dict(identity))
    except Exception:
        return []
    return compact_verified_evidence(values)


def _field_is_covered(expected: str, verified_fields: set[str]) -> bool:
    if expected == "compatibility":
        return any(value.startswith("compatibility.") for value in verified_fields)
    if expected == "reference.oem_code":
        return any(value.startswith("reference.oem_code.") for value in verified_fields)
    if expected == "electrical.voltage":
        return any(
            value in verified_fields
            for value in (
                "electrical.voltage",
                "electrical.input_voltage",
                "electrical.output_voltage",
            )
        )
    if expected == "interface.connector":
        return any(
            value == expected
            or value.startswith(expected + ".")
            or value.startswith(expected + "_")
            for value in verified_fields
        )
    if expected in {"kit.content", "installation.requirement"}:
        return any(value.startswith(expected + ".") for value in verified_fields)
    return expected in verified_fields


def _aggregate_research_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy": PUBLIC_RESEARCH_POLICY,
        "pages_discovered": max(0, int(metrics.get("pages_discovered") or 0)),
        "pages_read": max(0, int(metrics.get("pages_read") or 0)),
        "duration_ms": max(0, int(metrics.get("duration_ms") or 0)),
        "stop_reason": _safe_text(metrics.get("stop_reason") or "no_new_facts", 40),
        "coverage_complete": False,
        "fields_confirmed": 0,
        "fields_missing": 0,
        "conflicts": 0,
    }


def _research_persistence_plan(
    agent_input: Mapping[str, Any],
    documents: Sequence[ResearchDocumentV1],
    aggregate: Mapping[str, Any],
) -> tuple[list[ResearchDocumentV1], set[str], bool, str]:
    current_listing = listing_document(agent_input)
    candidates = ([current_listing] if current_listing is not None else []) + list(documents)
    deduplicated = _deduplicate_research_documents(candidates)
    expected = expected_fields_for_research(agent_input)
    externally_limited = aggregate["stop_reason"] in {
        "deadline",
        "page_limit",
        "provider_unavailable",
        "failed",
    }
    estimated_coverage = bool(
        expected
        and not externally_limited
        and apparent_coverage_complete(agent_input, documents)
    )
    requested_stop_reason = (
        "coverage_complete" if estimated_coverage else str(aggregate["stop_reason"])
    )
    allowed_stop_reasons = {
        "coverage_complete",
        "no_new_facts",
        "page_limit",
        "deadline",
        "provider_unavailable",
        "failed",
    }
    if requested_stop_reason not in allowed_stop_reasons:
        requested_stop_reason = "no_new_facts"
    return deduplicated, expected, externally_limited, requested_stop_reason


def _persist_document_claims(
    client_id: str,
    batch_id: str,
    documents: Sequence[ResearchDocumentV1],
    agent_input: Mapping[str, Any],
) -> None:
    claims_by_signature: dict[
        tuple[str, str, str, str], tuple[TechnicalClaimV1, list[str]]
    ] = {}
    for document in documents:
        scoped_text = scope_research_text_to_product(
            document.text,
            agent_input,
            source_type=document.source_type,
        )
        if not scoped_text:
            continue
        scoped_document = replace(
            document,
            text=scoped_text,
            content_hash=_technical_content_hash(scoped_text),
            copy_fingerprint=_technical_copy_fingerprint(scoped_text),
        )
        source = add_product_evidence_source(
            client_id,
            batch_id,
            url=scoped_document.url,
            source_type=scoped_document.source_type,
            content_hash=scoped_document.content_hash,
            copy_fingerprint=scoped_document.copy_fingerprint,
            origin_key=scoped_document.origin_key,
            section_ref=scoped_document.query_type,
        )
        source_id = str(source.get("source_id") or "")
        if not source_id:
            continue
        for claim in [
            *extract_technical_claims(scoped_text),
            *extract_guarded_commercial_claims(scoped_document, agent_input),
        ]:
            entry = claims_by_signature.setdefault(claim.signature, (claim, []))
            if source_id not in entry[1]:
                entry[1].append(source_id)
    for claim, source_ids in claims_by_signature.values():
        add_product_evidence_claim(
            client_id,
            batch_id,
            field_name=claim.field_name,
            scope=claim.scope,
            value=claim.value,
            unit=claim.unit or None,
            source_ids=source_ids,
        )


def _complete_research_batch(
    client_id: str,
    batch_id: str,
    identity: Mapping[str, str],
    expected: set[str],
    externally_limited: bool,
    requested_stop_reason: str,
    aggregate: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    completed = complete_product_evidence_batch(
        client_id,
        batch_id,
        coverage_complete=bool(expected) and not externally_limited,
        stop_reason=requested_stop_reason,
        pages_discovered=aggregate["pages_discovered"],
        pages_read=min(aggregate["pages_read"], aggregate["pages_discovered"]),
        fields_missing=len(expected),
        expected_fields=sorted(expected),
    )
    verified_raw = list_verified_product_evidence(client_id, **dict(identity))
    verified = compact_verified_evidence(verified_raw)
    verified_fields = {str(value.get("field_name") or "") for value in verified}
    missing = {value for value in expected if not _field_is_covered(value, verified_fields)}
    aggregate.update(
        {
            "stop_reason": str(completed.get("stop_reason") or requested_stop_reason),
            "coverage_complete": bool(completed.get("coverage_complete")),
            "fields_confirmed": int(
                completed.get("fields_confirmed") or len(verified_fields)
            ),
            "fields_missing": int(completed.get("fields_missing") or len(missing)),
            "conflicts": int(completed.get("conflicts_count") or 0),
            "repository_status": "completed",
        }
    )
    return verified, aggregate


def _fail_research_batch(
    client_id: str,
    batch_id: str,
    expected: set[str],
    aggregate: dict[str, Any],
) -> None:
    if not batch_id:
        return
    try:
        complete_product_evidence_batch(
            client_id,
            batch_id,
            coverage_complete=False,
            stop_reason="failed",
            pages_discovered=aggregate["pages_discovered"],
            pages_read=min(aggregate["pages_read"], aggregate["pages_discovered"]),
            fields_missing=len(expected),
            failed=True,
        )
    except Exception:
        pass


def persist_research_documents(
    client_id: str,
    agent_input: Mapping[str, Any],
    documents: Sequence[ResearchDocumentV1],
    *,
    metrics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Persist a batch without retaining question, page body, snippets or model text."""

    identity = evidence_identity(agent_input)
    aggregate = _aggregate_research_metrics(metrics)
    tenant_id = _safe_text(agent_input.get("tenant_id"), 128)
    if tenant_id and tenant_id != _safe_text(client_id, 128):
        aggregate["stop_reason"] = "provider_unavailable"
        aggregate["repository_status"] = "tenant_mismatch"
        return [], aggregate
    if _variation_selection_state(agent_input) not in {"global", "selected"}:
        aggregate["stop_reason"] = "no_new_facts"
        aggregate["repository_status"] = "variation_unresolved"
        return [], aggregate
    if not _identity_complete(identity):
        aggregate["stop_reason"] = "provider_unavailable"
        aggregate["repository_status"] = "identity_incomplete"
        return [], aggregate
    deduplicated, expected, externally_limited, requested_stop_reason = (
        _research_persistence_plan(agent_input, documents, aggregate)
    )
    batch_id = ""
    try:
        batch = create_product_evidence_batch(client_id, **identity)
        batch_id = str(batch.get("batch_id") or "")
        _persist_document_claims(
            client_id,
            batch_id,
            deduplicated,
            agent_input,
        )
        return _complete_research_batch(
            client_id,
            batch_id,
            identity,
            expected,
            externally_limited,
            requested_stop_reason,
            aggregate,
        )
    except Exception:
        _fail_research_batch(client_id, batch_id, expected, aggregate)
        aggregate["coverage_complete"] = False
        aggregate["repository_status"] = "unavailable"
        return load_verified_product_evidence(client_id, identity), aggregate


def apparent_coverage_complete(
    agent_input: Mapping[str, Any],
    documents: Sequence[ResearchDocumentV1],
) -> bool:
    if _variation_selection_state(agent_input) not in {"global", "selected"}:
        return False
    expected = expected_fields_for_research(agent_input)
    if not expected:
        return False
    support: dict[tuple[str, str, str, str], set[tuple[str, str]]] = {}
    official: set[tuple[str, str, str, str]] = set()
    for document in _deduplicate_research_documents(documents):
        if document.source_type not in {
            "official_manufacturer",
            "official_oem",
            "technical_distributor",
            "technical_independent",
        }:
            continue
        scoped_text = scope_research_text_to_product(
            document.text,
            agent_input,
            source_type=document.source_type,
        )
        if not scoped_text:
            continue
        scoped_document = replace(
            document,
            text=scoped_text,
            content_hash=_technical_content_hash(scoped_text),
            copy_fingerprint=_technical_copy_fingerprint(scoped_text),
        )
        for claim in [
            *extract_technical_claims(scoped_text),
            *extract_guarded_commercial_claims(scoped_document, agent_input),
        ]:
            support.setdefault(claim.signature, set()).add(
                (scoped_document.origin_key, scoped_document.copy_fingerprint)
            )
            if scoped_document.source_type in {"official_manufacturer", "official_oem"}:
                official.add(claim.signature)
    qualified = {
        signature
        for signature, origins in support.items()
        if signature in official
        or (
            len({origin for origin, _fingerprint in origins}) >= 2
            and len({fingerprint for _origin, fingerprint in origins}) >= 2
        )
    }
    values_by_field: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for signature in qualified:
        values_by_field.setdefault((signature[0], signature[1]), set()).add((signature[2], signature[3]))
    supported_fields = {
        field_name
        for (field_name, _scope), values in values_by_field.items()
        if len(values) == 1
    }
    return all(_field_is_covered(value, supported_fields) for value in expected if value != "compatibility") and "compatibility" not in expected
