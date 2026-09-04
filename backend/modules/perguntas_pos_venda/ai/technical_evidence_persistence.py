"""Persist normalized Sol evidence-graph facts without retaining source bodies."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from backend.modules.context_hub.dlp_core import _dlp_categories
from backend.modules.context_hub.product_evidence import (
    add_product_evidence_claim,
    add_product_evidence_source,
    complete_product_evidence_batch,
    create_product_evidence_batch,
)
from backend.services.vin_transient import contains_vin_like_identifier


_SOURCE_TYPES = frozenset({
    "official_manufacturer", "official_oem", "official_listing",
    "technical_distributor", "technical_independent", "marketplace", "forum", "blog",
})
_SCOPES = frozenset({"product", "package", "kit", "variation", "application"})
_RELATIONS = frozenset({
    "mounted_on", "installed_in", "part_of", "applies_to", "compatible_with",
    "replaces", "superseded_by",
})
_TECHNICAL_FIELD_ROOTS = frozenset({
    "application", "brand", "capacity", "certification", "code", "compatibility",
    "condition", "connector", "current", "diameter", "dimension", "dimensions",
    "electrical", "engine", "fitment", "fixation", "flow", "frequency", "function",
    "installation", "installation_location", "interface", "kit", "manufacturer",
    "material", "mounting", "oem", "package", "performance", "physical", "power",
    "pressure", "product", "reference", "relation", "replacement", "specification",
    "speed", "standard", "technical", "temperature", "thread", "torque", "variation",
    "vehicle", "voltage", "weight", "years",
})
_FORBIDDEN_ENTITY_KINDS = frozenset({
    "buyer", "comprador", "customer", "cliente", "person", "pessoa", "recipient",
    "destinatario", "user", "usuario",
})
_UNLABELLED_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?55[ .-]*)?(?:\([1-9]\d\)|[1-9]\d[ .-]+)"
    r"(?:9\d{4}|\d{4})[ .-]+\d{4}(?!\d)|"
    r"(?<!\d)(?:11|12|13|14|15|16|17|18|19|21|22|24|27|28|31|32|33|34|35|37|38|"
    r"41|42|43|44|45|46|47|48|49|51|53|54|55|61|62|63|64|65|66|67|68|69|71|73|"
    r"74|75|77|79|81|82|83|84|85|86|87|88|89|91|92|93|94|95|96|97|98|99)"
    r"(?:9\d{8}|[2-5]\d{7})(?!\d)"
)
_ADDRESS_RE = re.compile(
    r"(?i)\b(?:rua|r\.|avenida|av\.|alameda|travessa|estrada|rodovia|pra[cç]a)\s+"
    r"[a-z0-9à-ÿ .'-]{2,80}(?:,\s*|\s+)\d{1,6}\b"
)
_PERSON_LABEL_RE = re.compile(
    r"(?i)\b(?:nome(?:\s+completo)?|cliente|pessoa|destinat[aá]rio)\s*(?:=|:)\s*"
    r"[a-zà-ÿ][a-zà-ÿ .'-]{2,80}"
)


def _text(value: object, maximum: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def _sha(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def _source_type(value: object) -> str:
    normalized = _text(value, 40).lower()
    return normalized if normalized in _SOURCE_TYPES else ""


def _identity(agent_input: Mapping[str, Any]) -> dict[str, str]:
    raw = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    return {
        key: _text(raw.get(key), 128)
        for key in ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id")
    }


def _operational_fingerprint(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


def _operational_fragments(agent_input: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    """Return bounded fingerprints that must never become dossier facts.

    Short values such as ``12 V`` are deliberately ignored so a buyer asking a
    concise technical question cannot suppress the same legitimate normalized
    specification.  Longer question/history/draft copies are operational
    conversation data, not reusable product evidence.
    """

    candidates: list[tuple[object, int]] = []
    question = agent_input.get("question") if isinstance(agent_input.get("question"), Mapping) else {}
    candidates.extend((question.get(key), 24) for key in (
        "text", "current_draft_to_avoid", "current_draft", "answer", "response",
    ))
    history = question.get("history") if isinstance(question.get("history"), list) else []
    for entry in history[:20]:
        if not isinstance(entry, Mapping):
            continue
        candidates.extend((entry.get(key), 24) for key in (
            "text", "question", "answer", "message", "content", "response",
        ))
    subquestions = agent_input.get("subquestions")
    if isinstance(subquestions, list):
        for entry in subquestions[:8]:
            if isinstance(entry, Mapping):
                candidates.append((entry.get("question") or entry.get("text"), 24))
            else:
                candidates.append((entry, 24))
    # A full prompt can legitimately contain listing facts.  Only a substantial
    # copied span is rejected, avoiding false positives for ordinary specs.
    candidates.append((agent_input.get("prompt"), 80))

    result: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for raw, minimum in candidates:
        fingerprint = _operational_fingerprint(raw)
        if len(fingerprint) < minimum or len(fingerprint.split()) < 4:
            continue
        key = (fingerprint[:4000], minimum)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return tuple(result)


def _contains_operational_copy(
    value: object,
    fragments: Iterable[tuple[str, int]],
) -> bool:
    candidate = _operational_fingerprint(value)
    if not candidate:
        return False
    for fragment, minimum in fragments:
        if fragment in candidate:
            return True
        if len(candidate) >= minimum and len(candidate.split()) >= 4 and candidate in fragment:
            return True
    return False


def _identity_matches_active_product(
    identity: Mapping[str, str],
    agent_input: Mapping[str, Any],
) -> bool:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    question = (
        agent_input.get("question")
        if isinstance(agent_input.get("question"), Mapping)
        else {}
    )
    identity_item = _text(identity.get("item_id"), 128)
    active_item_ids = {
        value.casefold()
        for value in (
            _text(item.get("id"), 128),
            _text(question.get("item_id"), 128),
        )
        if value
    }
    if not identity_item or not active_item_ids or active_item_ids != {identity_item.casefold()}:
        return False

    active_sku = _text(item.get("seller_sku"), 128)
    if active_sku and active_sku.casefold() != _text(identity.get("sku"), 128).casefold():
        return False

    variations = item.get("variations") if isinstance(item.get("variations"), list) else []
    variation_ids = {
        _text(value.get("id"), 128).casefold()
        for value in variations[:40]
        if isinstance(value, Mapping) and _text(value.get("id"), 128)
    }
    identity_variation = _text(identity.get("variation_id"), 128).casefold()
    if variation_ids and (not identity_variation or identity_variation not in variation_ids):
        return False
    if identity_variation and not variation_ids:
        return False
    return True


def _walk_selected_context(value: object, *, depth: int = 0) -> Iterable[Mapping[str, Any]]:
    if depth >= 7:
        return
    if isinstance(value, Mapping):
        for key, item in list(value.items())[:160]:
            normalized = str(key or "").strip().lower()
            if normalized in {
                "research_passages", "product_research_evidence",
                "verified_product_evidence", "verified_target_evidence",
                "document_vision_page_refs", "sources",
            } and isinstance(item, list):
                for candidate in item[:160]:
                    if isinstance(candidate, Mapping):
                        yield candidate
            if isinstance(item, (Mapping, list, tuple)):
                yield from _walk_selected_context(item, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in list(value)[:160]:
            if isinstance(item, (Mapping, list, tuple)):
                yield from _walk_selected_context(item, depth=depth + 1)


def _source_url(raw: Mapping[str, Any]) -> str:
    url = _text(raw.get("url") or raw.get("source_url") or raw.get("source_ref"), 800)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    decoded_url = unquote(url)
    if (
        contains_vin_like_identifier(decoded_url)
        or _dlp_categories(decoded_url)
        or _UNLABELLED_PHONE_RE.search(decoded_url)
        or _ADDRESS_RE.search(decoded_url)
    ):
        return ""
    return url


def _source_entry(
    raw: Mapping[str, Any],
    *,
    operational_fragments: Iterable[tuple[str, int]] = (),
) -> tuple[str, dict[str, str]] | None:
    url = _source_url(raw)
    source_type = _source_type(raw.get("source_type"))
    if not url or not source_type:
        return None
    content_hash = _text(raw.get("content_hash"), 64).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        passage = _text(raw.get("text") or raw.get("excerpt"), 2400)
        content_hash = _sha(passage) if passage else ""
    if not content_hash:
        return None
    section = _text(raw.get("section_ref"), 160)
    if (
        _contains_forbidden_personal_data(section)
        or _contains_operational_copy(section, operational_fragments)
    ):
        section = ""
    page = raw.get("page")
    if not section and isinstance(page, int) and page > 0:
        section = f"p. {page}"
    return url, {
        "url": url,
        "source_type": source_type,
        "content_hash": content_hash,
        "section_ref": section,
    }


def _source_catalog(
    graph: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    operational_fragments: Iterable[tuple[str, int]] = (),
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    by_url: dict[str, dict[str, str]] = {}
    ref_to_url: dict[str, str] = {}
    candidates = [*_walk_selected_context(context)]
    for raw in candidates:
        entry = _source_entry(raw, operational_fragments=operational_fragments)
        if entry is None:
            continue
        url, metadata = entry
        existing = by_url.get(url)
        if existing is None:
            by_url[url] = metadata
        else:
            merged = dict(existing)
            if raw.get("content_hash"):
                merged["content_hash"] = metadata["content_hash"]
            if metadata["section_ref"] and not merged.get("section_ref"):
                merged["section_ref"] = metadata["section_ref"]
            by_url[url] = merged
    # Vision metadata may refine a hash/page only for a URL already admitted by
    # a real crawler/listing record.  Model-emitted graph passages are never a
    # source authority on their own.
    for raw in candidates:
        url = _source_url(raw)
        if not url or url not in by_url:
            continue
        content_hash = _text(raw.get("content_hash"), 64).lower()
        if re.fullmatch(r"[0-9a-f]{64}", content_hash):
            by_url[url]["content_hash"] = content_hash
        section = _text(raw.get("section_ref"), 160)
        page = raw.get("page")
        if not section and isinstance(page, int) and page > 0:
            section = f"p. {page}"
        if (
            section
            and not _contains_forbidden_personal_data(section)
            and not _contains_operational_copy(section, operational_fragments)
            and not by_url[url].get("section_ref")
        ):
            by_url[url]["section_ref"] = section
        reference = _text(raw.get("id") or raw.get("passage_id"), 128)
        if reference:
            ref_to_url[reference] = url
        ref_to_url[url] = url
    for raw in list(graph.get("passages") or [])[:80]:
        if not isinstance(raw, Mapping):
            continue
        url = _source_url(raw)
        if not url or url not in by_url:
            continue
        reference = _text(raw.get("id") or raw.get("passage_id"), 128)
        if reference:
            ref_to_url[reference] = url
        ref_to_url[url] = url
    return by_url, ref_to_url


def _scope(entity: Mapping[str, Any]) -> str:
    kind = _text(entity.get("kind") or entity.get("type"), 40).lower()
    if any(token in kind for token in ("vehicle", "application", "veiculo", "aplicacao")):
        return "application"
    for candidate in _SCOPES:
        if candidate in kind:
            return candidate
    return "product"


def _field(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9_.]", "_", _text(value, 96).lower()).strip("._")
    if not normalized or not normalized[0].isalpha():
        normalized = f"fact.{normalized}" if normalized else "fact.technical"
    return normalized[:96]


def _is_allowed_technical_field(value: str) -> bool:
    root = str(value or "").split(".", 1)[0]
    return root in _TECHNICAL_FIELD_ROOTS


def _contains_forbidden_personal_data(value: object) -> bool:
    text = _text(value, 512)
    return bool(
        not text
        or contains_vin_like_identifier(text)
        or _dlp_categories(text)
        or _UNLABELLED_PHONE_RE.search(text)
        or _ADDRESS_RE.search(text)
        or _PERSON_LABEL_RE.search(text)
    )


def _entity_is_technical(entity: Mapping[str, Any]) -> bool:
    kind = _text(entity.get("kind") or entity.get("type"), 48).lower()
    tokens = set(re.split(r"[^a-z0-9]+", kind))
    return not bool(tokens & _FORBIDDEN_ENTITY_KINDS)


def _claim_rows(
    graph: Mapping[str, Any],
    *,
    operational_fragments: Iterable[tuple[str, int]] = (),
) -> list[dict[str, Any]]:
    entities = {
        _text(item.get("id"), 128): item
        for item in list(graph.get("entities") or [])[:40]
        if isinstance(item, Mapping) and _text(item.get("id"), 128)
    }
    rows: list[dict[str, Any]] = []
    claims_by_id: dict[str, Mapping[str, Any]] = {}
    for claim in list(graph.get("claims") or [])[:120]:
        if not isinstance(claim, Mapping) or not _text(claim.get("value"), 256):
            continue
        entity = entities.get(_text(claim.get("entity_id"), 128), {})
        if not entity or not _entity_is_technical(entity):
            continue
        support = _text(claim.get("support"), 24).lower()
        raw_field = _text(claim.get("field_name"), 256)
        original_field = _field(raw_field)
        if support == "refutes":
            field_name = _field(f"refutation.{original_field}")
        elif support == "context":
            continue
        elif support == "supports":
            field_name = original_field
        else:
            continue
        value = _text(claim.get("value"), 256)
        if (
            not _is_allowed_technical_field(original_field)
            or _contains_operational_copy(raw_field, operational_fragments)
            or _contains_forbidden_personal_data(value)
            or _contains_operational_copy(value, operational_fragments)
        ):
            continue
        claim_id = _text(claim.get("id"), 128)
        if claim_id:
            claims_by_id[claim_id] = claim
        rows.append({
            "field_name": field_name,
            "scope": _scope(entity),
            "value": value,
            "unit": _text(claim.get("unit"), 16),
            "source_refs": list(claim.get("source_refs") or [])[:16],
        })
    for relation in list(graph.get("relations") or [])[:80]:
        if not isinstance(relation, Mapping):
            continue
        predicate = _text(relation.get("relation"), 48).lower()
        source = entities.get(_text(relation.get("from_entity_id"), 128), {})
        target = entities.get(_text(relation.get("to_entity_id"), 128), {})
        claim_ids = [
            _text(value, 128) for value in list(relation.get("claim_ids") or [])[:16]
        ]
        supporting_claims = [
            claims_by_id[claim_id]
            for claim_id in claim_ids
            if claim_id in claims_by_id
            and _text(claims_by_id[claim_id].get("support"), 24).lower() == "supports"
        ]
        if (
            predicate not in _RELATIONS
            or not source
            or not target
            or not _entity_is_technical(source)
            or not _entity_is_technical(target)
            or not supporting_claims
        ):
            continue
        subject_name = _text(source.get("name") or source.get("id"), 256)
        if (
            _contains_forbidden_personal_data(subject_name)
            or _contains_operational_copy(subject_name, operational_fragments)
        ):
            continue
        subject = re.sub(
            r"[^a-z0-9_]", "_", subject_name[:40].lower(),
        ).strip("_") or "entity"
        target_value = _text(target.get("name") or target.get("id"), 256)
        if (
            _contains_forbidden_personal_data(target_value)
            or _contains_operational_copy(target_value, operational_fragments)
        ):
            continue
        source_refs = list(relation.get("source_refs") or [])[:16]
        for claim in supporting_claims:
            source_refs.extend(list(claim.get("source_refs") or [])[:16])
        rows.append({
            "field_name": _field(f"relation.{predicate}.{subject}"),
            "scope": _scope(source),
            "value": target_value,
            "unit": "",
            "source_refs": source_refs[:16],
        })
    return rows


def persist_technical_evidence_graph(
    client_id: str,
    agent_input: Mapping[str, Any],
    graph: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    expected_store: str = "",
) -> dict[str, Any]:
    """Write graph facts/relations with provenance; never write passages or prompts."""

    identity = _identity(agent_input)
    operational_fragments = _operational_fragments(agent_input)
    tenant_id = _text(agent_input.get("tenant_id"), 128)
    active_store = _text(agent_input.get("store") or agent_input.get("loja"), 128)
    store_values = [
        value.casefold()
        for value in (identity.get("store_ref", ""), active_store, _text(expected_store, 128))
        if value
    ]
    if (
        not tenant_id
        or tenant_id != _text(client_id, 128)
        or len(set(store_values)) > 1
        or not active_store
        or any(not identity[key] for key in ("store_ref", "seller_id", "site_id", "sku", "item_id"))
        or not _identity_matches_active_product(identity, agent_input)
        or not isinstance(graph, Mapping)
    ):
        return {"status": "skipped", "reason": "identity_unavailable"}
    by_url, ref_to_url = _source_catalog(
        graph,
        context,
        operational_fragments=operational_fragments,
    )
    rows = _claim_rows(graph, operational_fragments=operational_fragments)
    if not rows or not by_url:
        return {"status": "skipped", "reason": "no_sourced_graph_facts"}
    batch_id = ""
    try:
        batch = create_product_evidence_batch(client_id, **identity)
        batch_id = _text(batch.get("batch_id"), 128)
        source_ids: dict[str, str] = {}
        for url, source in by_url.items():
            added = add_product_evidence_source(
                client_id,
                batch_id,
                url=url,
                source_type=source["source_type"],
                content_hash=source["content_hash"],
                origin_key=str(urlsplit(url).hostname or ""),
                section_ref=source["section_ref"],
            )
            if added.get("source_id"):
                source_ids[url] = _text(added.get("source_id"), 128)
        written = 0
        for row in rows:
            refs = {
                ref_to_url.get(_text(value, 800), _text(value, 800))
                for value in row.pop("source_refs", [])
            }
            linked = [source_ids[url] for url in refs if url in source_ids]
            if not linked:
                continue
            try:
                add_product_evidence_claim(
                    client_id,
                    batch_id,
                    source_ids=linked,
                    **row,
                )
                written += 1
            except Exception:
                continue
        complete_product_evidence_batch(
            client_id,
            batch_id,
            coverage_complete=False,
            stop_reason="no_new_facts",
            pages_discovered=len(source_ids),
            pages_read=len(source_ids),
            fields_missing=0,
            expected_fields=[],
        )
        return {"status": "completed", "facts_written": written, "sources": len(source_ids)}
    except Exception:
        if batch_id:
            try:
                complete_product_evidence_batch(
                    client_id,
                    batch_id,
                    coverage_complete=False,
                    stop_reason="failed",
                    pages_discovered=0,
                    pages_read=0,
                    fields_missing=0,
                    failed=True,
                )
            except Exception:
                pass
        return {"status": "unavailable", "reason": "repository_error"}


__all__ = ["persist_technical_evidence_graph"]
