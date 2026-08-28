"""Deterministic, read-only Obsidian projection for structured product evidence.

The operational product-evidence tables remain authoritative.  This module only
collects a canonical snapshot from an existing SQLite connection and renders an
in-memory projection suitable for a later Context Hub generation.  It never
writes to SQLite, the vault, a generation directory, or the publication state.
"""

from __future__ import annotations

import html
import re
import sqlite3
import unicodedata
from collections import defaultdict
from itertools import combinations
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.dlp_core import _dlp_document_text
from backend.modules.context_hub.metadata import _dump_frontmatter, _obsidian_wikilink
from backend.modules.context_hub.paths import _normalize_client_id
from backend.modules.context_hub.product_evidence_activation import (
    _claim_source_expiry,
    _support_for_claim,
    _source_supports_policy,
)
from backend.modules.context_hub.product_evidence_model import (
    PRODUCT_EVIDENCE_POLICY,
    PRODUCT_EVIDENCE_SCOPES,
    _SOURCE_AUTHORITIES,
    _canonicalize_url,
    _independence_origin,
    _iso,
    _normalize_field_name,
    _normalize_hash,
    _normalize_source_type,
    _normalize_text,
    _parse_timestamp,
    _registrable_domain,
    _reject_sensitive,
    _source_fingerprint,
)
from backend.modules.context_hub.runtime import (
    _json_canonical,
    _normalize_surface,
    _sha256_text,
)


PRODUCT_EVIDENCE_EDITORIAL_SCHEMA = "jk.context-hub.product-evidence-editorial.v1"
PRODUCT_EVIDENCE_EDITORIAL_ROOT = "70_Gerado/Produtos/Evidencias-Tecnicas"
_EXPORTED_STATES = frozenset({"verified", "conflict", "expired"})
_PRODUCT_EVIDENCE_TABLES = frozenset(
    {
        "product_evidence_batches",
        "product_evidence_sources",
        "product_evidence_claims",
        "product_evidence_claim_sources",
    }
)
_WINDOWS_RESERVED = frozenset(
    {"aux", "con", "nul", "prn", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
_FIELD_LABELS = {"electrical.voltage": "Tensão", "electrical.power": "Potência",
                 "physical.weight": "Peso do produto", "package.weight": "Peso da embalagem",
                 "performance.flow_rate": "Vazão", "performance.pressure": "Pressão",
                 "reference.oem_code": "Código OEM", "compatibility.vehicle": "Compatibilidade veicular"}
_SCOPE_LABELS = {
    "product": "produto",
    "package": "embalagem",
    "kit": "kit",
    "variation": "variação",
    "application": "aplicação",
}
_SOURCE_LABELS = {
    "official_manufacturer": "fabricante oficial",
    "official_oem": "catálogo OEM oficial",
    "official_listing": "anúncio oficial",
    "technical_distributor": "distribuidor técnico",
    "technical_independent": "fonte técnica independente",
    "marketplace": "marketplace",
    "forum": "fórum",
    "blog": "blog",
}


def _rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [str(column[0]) for column in cursor.description or ()]
    result: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        if isinstance(row, sqlite3.Row):
            result.append({column: row[column] for column in columns})
        else:
            result.append(dict(zip(columns, row)))
    return result


def _tables_available(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'product_evidence_%'"
    ).fetchall()
    names = {str(row[0]) for row in rows}
    return _PRODUCT_EVIDENCE_TABLES.issubset(names)


def _safe_short_text(value: object, *, label: str, maximum: int = 256) -> str:
    normalized = _normalize_text(value, label=label, maximum=maximum)
    _reject_sensitive(normalized, label=label)
    return normalized


def _normalized_timestamp(value: object, *, label: str) -> str:
    raw = _normalize_text(value, label=label, maximum=64)
    return _iso(_parse_timestamp(raw))


def _normalized_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id"):
        value = _normalize_text(
            row.get(key), label=key, maximum=180, required=key not in {"item_id", "variation_id"}
        )
        _reject_sensitive(value, label=key)
        values.append(value)
    return tuple(values)


def _normalized_source(row: Mapping[str, Any]) -> dict[str, str] | None:
    if row.get("source_id") is None:
        return None
    canonical_url, domain = _canonicalize_url(row.get("canonical_url"))
    source_type = _normalize_source_type(row.get("source_type"))
    authority = _SOURCE_AUTHORITIES[source_type]
    stored_authority = str(row.get("authority") or "").strip().lower()
    if stored_authority and stored_authority != authority:
        raise ContextHubValidationError("Autoridade divergente na evidencia editorial de produto.")
    section_ref = _normalize_text(
        row.get("section_ref"), label="Secao", maximum=160, required=False
    )
    _reject_sensitive(section_ref, label="Secao")
    return {
        "url": canonical_url,
        "domain": domain,
        "source_type": source_type,
        "authority": authority,
        "origin_key": _safe_short_text(
            row.get("origin_key") or domain, label="Origem", maximum=160
        ).casefold(),
        "section_ref": section_ref,
        "collected_at": _normalized_timestamp(row.get("source_collected_at"), label="Coleta"),
        "valid_until": _normalized_timestamp(row.get("source_valid_until"), label="Validade da fonte"),
        "content_hash": _normalize_hash(row.get("content_hash"), label="Hash de conteudo"),
        "copy_fingerprint": _normalize_hash(
            row.get("copy_fingerprint"), label="Fingerprint de copia", required=False
        ),
    }


def _snapshot_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _tables_available(connection):
        return []
    cursor = connection.execute(
        """
        SELECT b.store_ref, b.seller_id, b.site_id, b.sku, b.item_id, b.variation_id,
               c.claim_id, c.field_name, c.scope, c.normalized_value,
               c.normalized_key, c.unit, c.state, c.activation_policy,
               c.conflict_group, c.valid_from, c.valid_until,
               s.source_id, s.canonical_url, s.domain, s.source_type, s.authority,
               s.origin_key, s.section_ref, s.collected_at AS source_collected_at,
               s.valid_until AS source_valid_until, s.content_hash, s.copy_fingerprint
          FROM product_evidence_claims c
          JOIN product_evidence_batches b ON b.batch_id=c.batch_id
          LEFT JOIN product_evidence_claim_sources cs ON cs.claim_id=c.claim_id
          LEFT JOIN product_evidence_sources s ON s.source_id=cs.source_id
         WHERE b.status='completed' AND c.state!='rejected'
         ORDER BY b.store_ref, b.seller_id, b.site_id, b.sku, b.item_id,
                  b.variation_id, c.field_name, c.scope, c.normalized_key,
                  c.unit, c.state, s.canonical_url, s.content_hash
        """
    )
    return _rows_as_dicts(cursor)


def _fact_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    field_name = _normalize_field_name(row.get("field_name"))
    scope = str(row.get("scope") or "").strip().lower()
    if scope not in PRODUCT_EVIDENCE_SCOPES:
        raise ContextHubValidationError("Escopo invalido na evidencia editorial de produto.")
    normalized_key = _safe_short_text(
        row.get("normalized_key"), label="Chave normalizada", maximum=256
    )
    unit = _normalize_text(row.get("unit"), label="Unidade", maximum=16, required=False)
    _reject_sensitive(unit, label="Unidade")
    return field_name, scope, normalized_key, unit


def _new_fact(row: Mapping[str, Any], key: tuple[str, str, str, str]) -> dict[str, Any]:
    value = _safe_short_text(row.get("normalized_value"), label="Valor", maximum=256)
    return {
        "field_name": key[0],
        "scope": key[1],
        "value": value,
        "normalized_key": key[2],
        "unit": key[3],
        "sources": {},
    }


def _merge_fact_row(fact: dict[str, Any], row: Mapping[str, Any]) -> None:
    value = _safe_short_text(row.get("normalized_value"), label="Valor", maximum=256)
    if value != fact["value"]:
        raise ContextHubValidationError("Afirmacoes equivalentes divergiram na projecao editorial.")
    source = _normalized_source(row)
    if source is not None:
        source_key = (
            source["url"], source["source_type"], source["content_hash"],
            source["copy_fingerprint"], source["valid_until"], source["origin_key"],
        )
        fact["sources"][source_key] = source


def _policy_sources_at(
    fact: Mapping[str, Any], sources: Sequence[Mapping[str, Any]], policy: str, as_of: Any,
) -> tuple[list[Mapping[str, Any]], str]:
    active: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    eligible = [
        source for source in sources
        if _source_supports_policy(str(source["source_type"]), policy)
        and _parse_timestamp(source["collected_at"]) <= as_of < _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), source
        )
    ]
    for source in sorted(
        eligible,
        key=lambda item: _claim_source_expiry(
            str(fact["field_name"]), str(fact["scope"]), item
        ),
        reverse=True,
    ):
        fingerprint = _source_fingerprint(source)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        active.append(source)
    if policy != "two_independent_technical_v1":
        return active, min(str(source["collected_at"]) for source in active)
    pairs = [
        pair for pair in combinations(active, 2)
        if _independence_origin(pair[0]["origin_key"], pair[0]["domain"])
        != _independence_origin(pair[1]["origin_key"], pair[1]["domain"])
        and _registrable_domain(pair[0]["domain"]) != _registrable_domain(pair[1]["domain"])
    ]
    if not pairs:
        raise ContextHubValidationError("Suporte tecnico independente divergente no snapshot.")
    start = min(max(str(left["collected_at"]), str(right["collected_at"])) for left, right in pairs)
    return active, start


def _finish_fact(fact: dict[str, Any], as_of: Any) -> dict[str, Any] | None:
    sources = sorted(
        fact["sources"].values(),
        key=lambda source: (
            source["domain"], source["url"], source["source_type"],
            source["content_hash"], source["valid_until"],
        ),
    )
    if not sources:
        return None
    current = _support_for_claim(fact["field_name"], fact["scope"], sources, as_of)
    history = []
    for event in sorted({_parse_timestamp(source["collected_at"]) for source in sources}):
        result = _support_for_claim(fact["field_name"], fact["scope"], sources, event)
        if result[0]:
            history.append((event, result))
    if not current[0] and not history:
        return None
    if current[0]:
        supporting_sources, valid_from = _policy_sources_at(fact, sources, current[1], as_of)
        state, policy, valid_until = "verified", current[1], _iso(current[2])
    else:
        supporting_sources = []
        valid_from = _iso(min(item[0] for item in history))
        valid_until = _iso(max(item[1][2] for item in history if item[1][2] is not None))
        state, policy = "expired", history[-1][1][1]
    return {
        "field_name": fact["field_name"],
        "scope": fact["scope"],
        "value": fact["value"],
        "normalized_key": fact["normalized_key"],
        "unit": fact["unit"],
        "state": state,
        "activation_policy": policy,
        "conflict_group": "",
        "valid_from": valid_from,
        "valid_until": valid_until,
        "sources": sources,
        "supporting_sources": supporting_sources,
    }


def _identity_digest(client_id: str, identity: Sequence[str]) -> str:
    return _sha256_text(_json_canonical([client_id, *identity]))


def _canonical_snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": str(snapshot.get("schema_version") or ""),
        "policy_version": str(snapshot.get("policy_version") or ""),
        "client_id": str(snapshot.get("client_id") or ""),
        "identities": list(snapshot.get("identities") or []),
    }


def _assert_dlp_safe(value: object, *, source_ref: str) -> None:
    if scan_dlp(value, source_ref=source_ref):
        raise ContextHubValidationError("DLP bloqueou a projecao editorial de evidencias.")


def collect_product_evidence_editorial_snapshot(
    connection: sqlite3.Connection,
    *,
    client_id: object,
    as_of: object,
) -> dict[str, Any]:
    """Read and canonicalize exportable evidence without changing the database."""

    normalized_client = _normalize_client_id(client_id)
    captured_datetime = _parse_timestamp(as_of)
    captured_at = _iso(captured_datetime)
    identities: dict[tuple[str, ...], dict[tuple[str, str, str, str], dict[str, Any]]] = defaultdict(dict)
    for row in _snapshot_rows(connection):
        identity = _normalized_identity(row)
        key = _fact_key(row)
        fact = identities[identity].get(key)
        if fact is None:
            fact = _new_fact(row, key)
            identities[identity][key] = fact
        _merge_fact_row(fact, row)

    rendered_identities: list[dict[str, Any]] = []
    for identity in sorted(identities):
        facts = [
            finished for key in sorted(identities[identity])
            if (finished := _finish_fact(identities[identity][key], captured_datetime)) is not None
        ]
        by_field: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for fact in facts:
            if fact["state"] == "verified":
                by_field[(fact["field_name"], fact["scope"])].append(fact)
        for (field_name, scope), active in by_field.items():
            if len(active) < 2:
                continue
            group = _sha256_text(_json_canonical([*identity, field_name, scope]))[:24]
            for fact in active:
                fact.update(state="conflict", activation_policy="conflict_v1", conflict_group=group)
        if not facts:
            continue
        rendered_identities.append(
            {
                "identity_key": _identity_digest(normalized_client, identity),
                "store_ref": identity[0],
                "seller_id": identity[1],
                "site_id": identity[2],
                "sku": identity[3],
                "item_id": identity[4],
                "variation_id": identity[5],
                "facts": facts,
            }
        )
    transitions = sorted(
        _iso(_claim_source_expiry(fact["field_name"], fact["scope"], source))
        for identity in rendered_identities
        for fact in identity["facts"]
        for source in fact["supporting_sources"]
        if _claim_source_expiry(fact["field_name"], fact["scope"], source) > captured_datetime
    )
    payload = {
        "schema_version": PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
        "policy_version": PRODUCT_EVIDENCE_POLICY,
        "client_id": normalized_client,
        "identities": rendered_identities,
    }
    _assert_dlp_safe(_json_canonical(payload), source_ref="product_evidence_editorial_snapshot")
    counts = defaultdict(int)
    for identity in rendered_identities:
        for fact in identity["facts"]:
            counts[str(fact["state"])] += 1
    return payload | {
        "snapshot_hash": _sha256_text(_json_canonical(payload)),
        "captured_at": captured_at,
        "next_transition_at": transitions[0] if transitions else "",
        "stats": {
            "identities": len(rendered_identities),
            "facts": sum(counts.values()),
            "verified": counts["verified"],
            "conflict": counts["conflict"],
            "expired": counts["expired"],
        },
    }


def _slug(value: object, *, fallback: str, maximum: int) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-.")[:maximum]
    if not slug or slug.split(".", 1)[0] in _WINDOWS_RESERVED:
        return fallback
    return slug


def _markdown_text(value: object) -> str:
    text = html.escape(str(value or ""), quote=False).replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "|"):
        text = text.replace(character, "\\" + character)
    return text.replace("\r", " ").replace("\n", " ").strip()


def _markdown_url(value: str) -> str:
    return value.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")


def _field_label(field_name: str) -> str:
    return _FIELD_LABELS.get(field_name, field_name.replace(".", " / "))


def _identity_directory(identity: Mapping[str, Any]) -> str:
    site = _slug(identity.get("site_id"), fallback="site", maximum=24)
    sku = _slug(identity.get("sku"), fallback="sku", maximum=48)
    return f"{PRODUCT_EVIDENCE_EDITORIAL_ROOT}/{site}-{sku}-{identity['identity_key'][:12]}"


def _fact_digest(identity: Mapping[str, Any], fact: Mapping[str, Any]) -> str:
    material = {
        "identity_key": identity["identity_key"],
        "field_name": fact["field_name"],
        "scope": fact["scope"],
        "normalized_key": fact["normalized_key"],
        "unit": fact["unit"],
        "state": fact["state"],
    }
    return _sha256_text(_json_canonical(material))


def _base_metadata(
    *,
    doc_id: str,
    title: str,
    source_hash: str,
    generated_at: str,
    client_id: str,
    surface: str,
    source_version: str,
    indexed: bool,
    status: str = "published",
) -> dict[str, Any]:
    return {
        "id": doc_id,
        "type": "product_evidence_fact" if indexed else "product_evidence_editorial",
        "managed": True,
        "status": status,
        "ai_usage": "allowed" if indexed else "denied",
        "tenant_scope": f"tenant:{client_id}",
        "sensitivity": "internal",
        "truth_class": "generated_verified" if indexed else "generated_secondary",
        "required_permissions": ["full"],
        "surface": surface,
        "source_version": source_version,
        "source_refs": [],
        "source_hash": source_hash,
        "generated_at": generated_at,
        "title": title,
        "module": "produto",
        "valid_from": "",
        "valid_to": "",
    }


def _identity_metadata(metadata: dict[str, Any], identity: Mapping[str, Any]) -> None:
    metadata.update(
        {
            "store_ref": identity["store_ref"],
            "seller_id": identity["seller_id"],
            "site_id": identity["site_id"],
            "sku": identity["sku"],
            "item_id": identity["item_id"],
            "variation_id": identity["variation_id"],
            "evidence_policy": PRODUCT_EVIDENCE_POLICY,
            "editorial_schema": PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
            "tags": ["evidencia-tecnica", "produto", f"sku-{_slug(identity['sku'], fallback='sku', maximum=48)}"],
        }
    )


def _sources_body(sources: Sequence[Mapping[str, Any]], heading: str) -> list[str]:
    lines = [f"## {heading}", ""]
    for source in sources:
        line = (
            f"- <{_markdown_url(str(source['url']))}> — "
            f"{_markdown_text(_SOURCE_LABELS.get(str(source['source_type']), source['source_type']))}"
        )
        if source.get("section_ref"):
            line += f", seção “{_markdown_text(source['section_ref'])}”"
        line += (
            f"; coletada em {_markdown_text(source['collected_at'])}; "
            f"válida até {_markdown_text(source['valid_until'])}."
        )
        lines.append(line)
    return lines


def _fact_body(identity: Mapping[str, Any], fact: Mapping[str, Any], *, indexed: bool) -> str:
    label = _field_label(str(fact["field_name"]))
    value = f"{fact['value']} {fact['unit']}".strip()
    lines = [
        f"# SKU {_markdown_text(identity['sku'])} — {_markdown_text(label)}",
        "",
        f"- Especificação: **{_markdown_text(value)}**",
        f"- Escopo: {_markdown_text(_SCOPE_LABELS[str(fact['scope'])])}",
        f"- Estado: {_markdown_text(fact['state'])}",
    ]
    if fact.get("valid_until"):
        lines.append(f"- Válida até: {_markdown_text(fact['valid_until'])}")
    selected = fact["supporting_sources"] if indexed else fact["sources"]
    heading = "Fontes que ativaram o fato" if indexed else "Fontes registradas"
    lines.extend(["", *_sources_body(selected, heading)])
    return "\n".join(lines).strip() + "\n"


def _safe_document(
    relative_path: str,
    metadata: Mapping[str, Any],
    body: str,
) -> dict[str, Any]:
    if scan_dlp(_dlp_document_text(metadata, body), source_ref=relative_path):
        raise ContextHubValidationError("DLP bloqueou uma nota editorial de evidencias.")
    content = _dump_frontmatter(metadata, body)
    return {
        "metadata": dict(metadata),
        "relative_path": relative_path,
        "content": content,
        "body": body,
    }


def _render_fact_document(
    identity: Mapping[str, Any],
    fact: Mapping[str, Any],
    *,
    client_id: str,
    generated_at: str,
    surface: str,
    source_version: str,
) -> dict[str, Any]:
    digest = _fact_digest(identity, fact)
    state = str(fact["state"])
    directory = {
        "verified": "Fatos-Verificados",
        "conflict": "Conflitos",
        "expired": "Expiradas",
    }[state]
    field_slug = _slug(fact["field_name"], fallback="campo", maximum=64)
    scope_slug = _slug(fact["scope"], fallback="escopo", maximum=24)
    relative_path = f"{_identity_directory(identity)}/{directory}/{field_slug}-{scope_slug}-{digest[:16]}.md"
    indexed = state == "verified"
    fact_hash = _sha256_text(_json_canonical({"identity": dict(identity), "fact": dict(fact)}))
    title = f"SKU {identity['sku']} — {_field_label(str(fact['field_name']))}"
    metadata = _base_metadata(
        doc_id=f"jk:product-evidence:{identity['identity_key'][:24]}:{digest[:24]}",
        title=title,
        source_hash=fact_hash,
        generated_at=generated_at,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        indexed=indexed,
        status="review_required" if state == "conflict" else "archived" if state == "expired" else "published",
    )
    _identity_metadata(metadata, identity)
    metadata.update(
        {
            "source_refs": [f"product_evidence/{identity['identity_key'][:24]}/{digest[:24]}"],
            "evidence_state": state,
            "field_name": fact["field_name"],
            "evidence_scope": fact["scope"],
            "activation_policy": fact["activation_policy"],
            "valid_from": fact["valid_from"],
            "valid_to": fact["valid_until"],
        }
    )
    return _safe_document(relative_path, metadata, _fact_body(identity, fact, indexed=indexed))


def _summary_document(
    identity: Mapping[str, Any],
    fact_documents: Sequence[Mapping[str, Any]],
    *,
    client_id: str,
    generated_at: str,
    surface: str,
    source_version: str,
) -> dict[str, Any]:
    relative_path = f"{_identity_directory(identity)}/Resumo.md"
    identity_hash = _sha256_text(_json_canonical(dict(identity)))
    metadata = _base_metadata(
        doc_id=f"jk:product-evidence-summary:{identity['identity_key'][:24]}",
        title=f"Evidências técnicas — SKU {identity['sku']}",
        source_hash=identity_hash,
        generated_at=generated_at,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        indexed=False,
    )
    _identity_metadata(metadata, identity)
    counts = defaultdict(int)
    lines = [
        f"# Evidências técnicas — SKU {_markdown_text(identity['sku'])}",
        "",
        f"- Loja: {_markdown_text(identity['store_ref'])}",
        f"- Seller: {_markdown_text(identity['seller_id'])}",
        f"- Site: {_markdown_text(identity['site_id'])}",
        f"- Item: {_markdown_text(identity['item_id']) or '—'}",
        f"- Variação: {_markdown_text(identity['variation_id']) or '—'}",
        "",
        "## Fatos materializados",
        "",
        "| Estado | Campo | Valor | Nota |",
        "|---|---|---|---|",
    ]
    facts_by_path = {str(document["relative_path"]): document for document in fact_documents}
    for fact in identity["facts"]:
        digest = _fact_digest(identity, fact)
        matching = next(
            document for path, document in facts_by_path.items() if digest[:16] in path
        )
        counts[str(fact["state"])] += 1
        value = f"{fact['value']} {fact['unit']}".strip()
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_text(fact["state"]),
                    _markdown_text(_field_label(str(fact["field_name"]))),
                    _markdown_text(value),
                    _obsidian_wikilink(matching["relative_path"], "abrir", table_cell=True),
                )
            )
            + " |"
        )
    metadata["evidence_counts"] = dict(sorted(counts.items()))
    return _safe_document(relative_path, metadata, "\n".join(lines).strip() + "\n")


def _index_document(
    identities: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    *,
    snapshot_hash: str,
    client_id: str,
    generated_at: str,
    surface: str,
    source_version: str,
) -> dict[str, Any]:
    relative_path = f"{PRODUCT_EVIDENCE_EDITORIAL_ROOT}/Indice.md"
    metadata = _base_metadata(
        doc_id="jk:product-evidence-editorial:index",
        title="Índice de evidências técnicas de produtos",
        source_hash=snapshot_hash,
        generated_at=generated_at,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        indexed=False,
    )
    metadata.update(
        {
            "evidence_policy": PRODUCT_EVIDENCE_POLICY,
            "editorial_schema": PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
            "tags": ["evidencia-tecnica", "produto", "indice"],
        }
    )
    lines = [
        "# Índice de evidências técnicas de produtos",
        "",
        "| Site | SKU | Loja | Item | Variação | Dossiê |",
        "|---|---|---|---|---|---|",
    ]
    if not identities:
        lines.extend(["", "Nenhuma evidência verificada, conflitante ou expirada foi materializada."])
    for identity, summary in zip(identities, summaries):
        lines.append(
            "| "
            + " | ".join(
                (
                    _markdown_text(identity["site_id"]),
                    _markdown_text(identity["sku"]),
                    _markdown_text(identity["store_ref"]),
                    _markdown_text(identity["item_id"]) or "—",
                    _markdown_text(identity["variation_id"]) or "—",
                    _obsidian_wikilink(summary["relative_path"], "abrir", table_cell=True),
                )
            )
            + " |"
        )
    return _safe_document(relative_path, metadata, "\n".join(lines).strip() + "\n")


def render_product_evidence_editorial(
    snapshot: Mapping[str, Any],
    *,
    client_id: object,
    generated_at: object,
    surface: object,
    source_version: object,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    """Render a validated snapshot without persisting or publishing anything."""

    canonical = _canonical_snapshot_payload(snapshot)
    expected_hash = _sha256_text(_json_canonical(canonical))
    if (
        canonical["schema_version"] != PRODUCT_EVIDENCE_EDITORIAL_SCHEMA
        or canonical["policy_version"] != PRODUCT_EVIDENCE_POLICY
        or str(snapshot.get("snapshot_hash") or "") != expected_hash
    ):
        raise ContextHubValidationError("Snapshot editorial de evidencias invalido ou adulterado.")
    normalized_client = _normalize_client_id(client_id)
    if normalized_client != _normalize_client_id(canonical["client_id"]):
        raise ContextHubValidationError("Snapshot editorial pertence a outro tenant.")
    normalized_generated_at = _normalized_timestamp(generated_at, label="Data da geracao")
    normalized_surface = _normalize_surface(surface)
    normalized_source_version = _normalize_text(
        source_version, label="Versao da fonte", maximum=120
    )
    _assert_dlp_safe(_json_canonical(canonical), source_ref="product_evidence_editorial_render")
    if not canonical["identities"]:
        return [], {}, []

    fact_documents: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    documents_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for identity in canonical["identities"]:
        if not isinstance(identity, Mapping):
            raise ContextHubValidationError("Identidade invalida no snapshot editorial.")
        for fact in identity.get("facts") or []:
            if not isinstance(fact, Mapping) or fact.get("state") not in _EXPORTED_STATES:
                raise ContextHubValidationError("Fato invalido no snapshot editorial.")
            document = _render_fact_document(
                identity,
                fact,
                client_id=normalized_client,
                generated_at=normalized_generated_at,
                surface=normalized_surface,
                source_version=normalized_source_version,
            )
            fact_documents.append(document)
            documents_by_identity[str(identity["identity_key"])].append(document)
        summaries.append(
            _summary_document(
                identity,
                documents_by_identity[str(identity["identity_key"])],
                client_id=normalized_client,
                generated_at=normalized_generated_at,
                surface=normalized_surface,
                source_version=normalized_source_version,
            )
        )
    index = _index_document(
        canonical["identities"],
        summaries,
        snapshot_hash=expected_hash,
        client_id=normalized_client,
        generated_at=normalized_generated_at,
        surface=normalized_surface,
        source_version=normalized_source_version,
    )
    all_documents = [index, *summaries, *fact_documents]
    managed_files = {
        str(document["relative_path"]): str(document["content"])
        for document in sorted(all_documents, key=lambda item: str(item["relative_path"]))
    }
    if len(managed_files) != len(all_documents):
        raise ContextHubValidationError("Colisao de caminho na projecao editorial de evidencias.")
    indexed_documents = [
        document for document in sorted(fact_documents, key=lambda item: str(item["relative_path"]))
        if document["metadata"]["ai_usage"] == "allowed"
        and document["metadata"].get("evidence_state") == "verified"
    ]
    return indexed_documents, managed_files, []


__all__ = [
    "PRODUCT_EVIDENCE_EDITORIAL_ROOT",
    "PRODUCT_EVIDENCE_EDITORIAL_SCHEMA",
    "collect_product_evidence_editorial_snapshot",
    "render_product_evidence_editorial",
]
