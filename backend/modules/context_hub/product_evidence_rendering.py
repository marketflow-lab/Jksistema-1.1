"""Internal renderer for the structured product-evidence editorial projection.

The public facade remains :mod:`product_evidence_editorial`.  Keeping rendering
here isolates Markdown/Obsidian concerns from operational evidence collection
without changing the snapshot or document contracts.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.dlp_core import _dlp_document_text
from backend.modules.context_hub.metadata import _dump_frontmatter, _obsidian_wikilink
from backend.modules.context_hub.paths import _normalize_client_id
from backend.modules.context_hub.product_evidence_model import (
    PRODUCT_EVIDENCE_POLICY,
    _iso,
    _normalize_text,
    _parse_timestamp,
)
from backend.modules.context_hub.runtime import (
    _json_canonical,
    _normalize_surface,
    _sha256_text,
)


PRODUCT_EVIDENCE_EDITORIAL_SCHEMA = "jk.context-hub.product-evidence-editorial.v1"
PRODUCT_EVIDENCE_EDITORIAL_ROOT = "70_Gerado/Produtos/Evidencias-Tecnicas"
_EXPORTED_STATES = frozenset({"candidate", "verified", "conflict", "expired"})
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


def _canonical_snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": str(snapshot.get("schema_version") or ""),
        "policy_version": str(snapshot.get("policy_version") or ""),
        "client_id": str(snapshot.get("client_id") or ""),
        "identities": list(snapshot.get("identities") or []),
    }


def _canonical_projection_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    canonical = _canonical_snapshot_payload(snapshot)
    return canonical | {
        "editorial_identities": list(
            snapshot.get("editorial_identities") or canonical["identities"]
        )
    }


def _assert_dlp_safe(value: object, *, source_ref: str) -> None:
    if scan_dlp(value, source_ref=source_ref):
        raise ContextHubValidationError("DLP bloqueou a projecao editorial de evidencias.")


def _normalized_timestamp(value: object, *, label: str) -> str:
    raw = _normalize_text(value, label=label, maximum=64)
    return _iso(_parse_timestamp(raw))


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
    selected = (
        fact["supporting_sources"]
        if indexed or fact.get("state") == "candidate"
        else fact["sources"]
    )
    heading = (
        "Fontes que ativaram o fato"
        if indexed
        else "Fontes candidatas registradas"
        if fact.get("state") == "candidate"
        else "Fontes registradas"
    )
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
        "candidate": "Candidatas",
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
        status=(
            "review_required"
            if state in {"candidate", "conflict"}
            else "archived"
            if state == "expired"
            else "published"
        ),
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
    projection = _canonical_projection_payload(snapshot)
    expected_hash = _sha256_text(_json_canonical(canonical))
    expected_projection_hash = _sha256_text(_json_canonical(projection))
    stored_projection_hash = str(snapshot.get("projection_hash") or "")
    legacy_projection_is_equivalent = bool(
        not stored_projection_hash
        and "editorial_identities" not in snapshot
        and projection["editorial_identities"] == canonical["identities"]
    )
    if (
        canonical["schema_version"] != PRODUCT_EVIDENCE_EDITORIAL_SCHEMA
        or canonical["policy_version"] != PRODUCT_EVIDENCE_POLICY
        or str(snapshot.get("snapshot_hash") or "") != expected_hash
        or (
            stored_projection_hash != expected_projection_hash
            and not legacy_projection_is_equivalent
        )
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
    _assert_dlp_safe(
        _json_canonical(projection), source_ref="product_evidence_editorial_render"
    )
    if not projection["editorial_identities"]:
        return [], {}, []

    fact_documents: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    documents_by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for identity in projection["editorial_identities"]:
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
        projection["editorial_identities"],
        summaries,
        snapshot_hash=expected_projection_hash,
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
    "render_product_evidence_editorial",
]
