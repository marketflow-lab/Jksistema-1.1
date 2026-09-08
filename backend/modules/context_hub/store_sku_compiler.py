"""Compile canonical SKU files and complete ML catalogues into store plans."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_contracts import (
    STORE_SKU_PUBLIC_SURFACE,
    StoreSkuBinding,
    StoreSkuScope,
    canonical_json,
    content_sha256,
    normalize_sku,
)
from backend.services.context_inventory.contracts import SKU_SCHEMA_VERSION, _SKU_REQUIRED_KEYS
from backend.services.context_inventory.normalization import _normal_key
from backend.services.context_inventory.sku_content import (
    _contains_forbidden_sku_key,
    _contains_sensitive_sku_value,
    _sku_field,
    _sku_section,
    _sku_top_keys,
)


_OPERATIONAL_KEYS = {
    "price", "preco", "preco_atual", "stock", "estoque", "saldo",
    "shipping", "frete", "delivery_time", "prazo_entrega", "promocao",
}


@dataclass(frozen=True, slots=True)
class CompiledStoreSkuKnowledge:
    scope: StoreSkuScope
    canonical_documents: dict[str, dict[str, Any]]
    store_guidance: dict[str, Any]
    sku_guidance: dict[str, dict[str, Any]]
    bindings: list[dict[str, str]]
    quarantined_legacy: dict[str, dict[str, Any]]
    report: dict[str, Any]
    source_hash: str


def _contains_operational_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key or "").strip().casefold()
            if normalized in _OPERATIONAL_KEYS or _contains_operational_key(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_operational_key(item) for item in value)
    return False


def load_canonical_sku_documents(
    client_id: object,
    *,
    info_root: str | Path | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Read every approved raw SKU JSON without generated Markdown expansion."""

    paths = _tenant_paths(client_id, info_root=info_root)
    sku_root = paths.tenant_dir / "SKU"
    _assert_path_chain_safe(sku_root, paths.info_root)
    if not sku_root.is_dir() or sku_root.is_symlink():
        raise ContextHubValidationError("Catalogo canonico SKU nao encontrado.")
    documents: dict[str, dict[str, Any]] = {}
    rejected: dict[str, int] = {}
    for path in sorted(sku_root.glob("*.json"), key=lambda value: value.name.casefold()):
        if path.name == "_INDICE.json":
            continue
        code = ""
        try:
            _assert_path_chain_safe(path, paths.info_root)
            if path.is_symlink() or path.stat().st_size > 1_000_000:
                code = "unsafe_or_oversized"
                raise ValueError(code)
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                code = "schema_invalid"
                raise ValueError(code)
            if _sku_top_keys(data) != _SKU_REQUIRED_KEYS or data.get("schema_version") != SKU_SCHEMA_VERSION:
                code = "schema_invalid"
                raise ValueError(code)
            if _contains_forbidden_sku_key(data) or _contains_sensitive_sku_value(data):
                code = "security_rejected"
                raise ValueError(code)
            sku = normalize_sku(data.get("sku"))
            if path.stem.casefold() != str(data.get("sku") or "").strip().casefold():
                code = "identity_mismatch"
                raise ValueError(code)
            revision = _sku_section(data, "revisao")
            status = _normal_key(_sku_field(revision, "status", ""))
            pending = _sku_field(revision, "pendencias", [])
            if status != "revisado" or not isinstance(pending, list) or pending:
                code = "not_approved"
                raise ValueError(code)
            if _contains_operational_key(data):
                code = "operational_data_forbidden"
                raise ValueError(code)
            if sku == "1599":
                code = "legacy_1599_quarantined"
                raise ValueError(code)
            if sku in documents and canonical_json(documents[sku]) != canonical_json(data):
                code = "duplicate_conflict"
                raise ValueError(code)
            documents[sku] = dict(data)
        except (OSError, UnicodeError, json.JSONDecodeError, ContextHubValidationError, ValueError):
            rejected[code or "unreadable"] = rejected.get(code or "unreadable", 0) + 1
    return documents, {
        "files_seen": sum(rejected.values()) + len(documents),
        "approved_documents": len(documents),
        "rejected_by_code": dict(sorted(rejected.items())),
    }


def load_legacy_public_guidance(
    client_id: object,
    *,
    store_ref: str,
    info_root: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Read the legacy JSON only as migration input, never as runtime fallback."""

    paths = _tenant_paths(client_id, info_root=info_root)
    legacy_path = paths.tenant_dir / "ia_treinamento_perguntas_pos_venda.json"
    _assert_path_chain_safe(legacy_path, paths.info_root)
    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContextHubValidationError("JSON legado de orientacoes invalido.") from exc
    if not isinstance(payload, dict):
        raise ContextHubValidationError("JSON legado de orientacoes invalido.")
    store_layer: dict[str, Any] = {}
    per_store = payload.get("por_loja") if isinstance(payload.get("por_loja"), Mapping) else {}
    exact = per_store.get(f"store_id:{store_ref}") if isinstance(per_store, Mapping) else None
    if isinstance(exact, Mapping):
        store_layer = dict(exact)

    def choose(field: str, fallback: str = "") -> Any:
        if field in store_layer:
            return store_layer.get(field)
        return payload.get(field, fallback)

    examples = choose("exemplos", {})
    public_examples = (
        list(examples.get("perguntas_anuncio") or [])
        if isinstance(examples, Mapping)
        else []
    )
    guidance = {
        "orientacoes_perguntas": str(
            choose("orientacoes_perguntas", choose("orientacoes", "")) or ""
        ).strip(),
        "contexto_loja": str(choose("contexto_loja", "") or "").strip(),
        "compatibilidade_autopecas": str(
            choose("compatibilidade_autopecas", "") or ""
        ).strip(),
        "proibicoes": str(choose("proibicoes", "") or "").strip(),
        "exemplos_perguntas": public_examples,
    }
    guidance = {key: value for key, value in guidance.items() if value not in ("", [], {})}
    raw_notes: dict[str, Any] = {}
    for source in (payload.get("notas_sku"), store_layer.get("notas_sku")):
        if not isinstance(source, Mapping):
            continue
        for raw_sku, raw_note in source.items():
            try:
                raw_notes[normalize_sku(raw_sku)] = raw_note
            except ContextHubValidationError:
                continue
    notes: dict[str, dict[str, Any]] = {}
    quarantine: dict[str, dict[str, Any]] = {}
    for sku, raw_note in raw_notes.items():
        if isinstance(raw_note, Mapping):
            note = {
                key: value
                for key, value in dict(raw_note).items()
                if key != "updated_at" and value not in ("", [], {})
            }
        else:
            note = {"notas": str(raw_note or "").strip()}
        note = {key: value for key, value in note.items() if value not in ("", [], {})}
        if not note:
            continue
        if sku == "1599":
            quarantine[sku] = note
        else:
            notes[sku] = note
    return guidance, notes, quarantine


def _select_catalog_knowledge(
    scope: StoreSkuScope,
    catalog: Mapping[str, Any],
    source_by_sku: Mapping[str, dict[str, Any]],
    legacy_sku_guidance: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[StoreSkuBinding],
    set[str],
    dict[str, int],
]:
    selected: dict[str, dict[str, Any]] = {}
    selected_guidance: dict[str, dict[str, Any]] = {}
    binding_objects: list[StoreSkuBinding] = []
    skipped: dict[str, int] = {}
    catalog_skus: set[str] = set()
    for raw_item in catalog.get("items") if isinstance(catalog.get("items"), list) else []:
        if not isinstance(raw_item, Mapping):
            continue
        try:
            sku = normalize_sku(raw_item.get("sku"))
        except ContextHubValidationError:
            skipped["sku_missing"] = skipped.get("sku_missing", 0) + 1
            continue
        catalog_skus.add(sku)
        # Price, stock, status or title can legitimately differ between two
        # listings of the same SKU. Binding safety comes from each exact
        # item/variation pair and the catalogue-wide duplicate check below;
        # non-identity conflicts remain visible in the migration report.
        if raw_item.get("conflicts"):
            skipped["non_binding_conflict_reported"] = (
                skipped.get("non_binding_conflict_reported", 0) + 1
            )
        fields = raw_item.get("fields") if isinstance(raw_item.get("fields"), Mapping) else {}
        site_id = str(fields.get("site_id_ml") or "").strip().upper()
        if not site_id:
            skipped["site_missing"] = skipped.get("site_missing", 0) + 1
            continue
        if site_id != scope.site_id:
            skipped["site_mismatch"] = skipped.get("site_mismatch", 0) + 1
            continue
        canonical = source_by_sku.get(sku)
        if canonical is None:
            skipped["canonical_missing"] = skipped.get("canonical_missing", 0) + 1
            continue
        item_bindings: list[StoreSkuBinding] = []
        listings = raw_item.get("listings") if isinstance(raw_item.get("listings"), list) else []
        for listing in listings:
            if not isinstance(listing, Mapping):
                continue
            try:
                item_bindings.append(StoreSkuBinding.from_mapping(
                    {
                        "item_id": listing.get("mlb"),
                        "variation_id": listing.get("variation_id"),
                        "sku": sku,
                    },
                    site_id=scope.site_id,
                ))
            except ContextHubValidationError:
                continue
        if not item_bindings:
            skipped["binding_missing"] = skipped.get("binding_missing", 0) + 1
            continue
        selected[sku] = canonical
        if sku in legacy_sku_guidance:
            selected_guidance[sku] = dict(legacy_sku_guidance[sku])
        binding_objects.extend(item_bindings)
    return selected, selected_guidance, binding_objects, catalog_skus, skipped


def _deduplicate_catalog_bindings(
    binding_objects: Sequence[StoreSkuBinding],
) -> list[dict[str, str]]:
    unique: dict[tuple[str, str], StoreSkuBinding] = {}
    for binding in binding_objects:
        key = (binding.item_id, binding.variation_id)
        previous = unique.get(key)
        if previous is not None and previous.sku != binding.sku:
            raise ContextHubValidationError("Catalogo associa o mesmo item/variacao a SKUs distintos.")
        unique[key] = binding
    return [
        value.as_dict()
        for value in sorted(
            unique.values(),
            key=lambda item: (item.item_id, item.variation_id, item.sku),
        )
    ]


def compile_store_sku_knowledge(
    client_id: object,
    scope_value: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    canonical_documents: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    legacy_sku_guidance: Mapping[str, Mapping[str, Any]],
    quarantined_legacy: Mapping[str, Mapping[str, Any]] | None = None,
    allow_partial_catalog: bool = False,
) -> CompiledStoreSkuKnowledge:
    scope = StoreSkuScope.from_mapping(scope_value, tenant_scope=f"tenant:{client_id}")
    if scope.tenant_scope != f"tenant:{client_id}":
        raise ContextHubValidationError("tenant_scope diverge do tenant ligado pelo servidor.")
    if str(catalog.get("store_id") or "").strip() != scope.store_ref:
        raise ContextHubValidationError("Catalogo Mercado Livre diverge do store_id solicitado.")
    if str(catalog.get("seller_id") or "").strip() != scope.seller_id:
        raise ContextHubValidationError("Catalogo Mercado Livre diverge do seller_id solicitado.")
    catalog_site_id = str(catalog.get("site_id") or "").strip().upper()
    if catalog_site_id and catalog_site_id != scope.site_id:
        raise ContextHubValidationError("Catalogo Mercado Livre diverge do site_id solicitado.")
    coverage_complete = catalog.get("coverage_complete") is True
    if catalog.get("cancelled") is True or (
        not coverage_complete and not allow_partial_catalog
    ):
        raise ContextHubValidationError("Catalogo Mercado Livre incompleto; publicacao bloqueada.")
    source_by_sku = {
        normalize_sku(key): dict(value) for key, value in canonical_documents.items()
    }
    selected, selected_guidance, binding_objects, catalog_skus, skipped = (
        _select_catalog_knowledge(
            scope,
            catalog,
            source_by_sku,
            legacy_sku_guidance,
        )
    )

    if not selected:
        raise ContextHubValidationError("Nenhum SKU teve identidade completa no catalogo atual.")
    bindings = _deduplicate_catalog_bindings(binding_objects)
    report = {
        "coverage_complete": coverage_complete,
        "partial_catalog": not coverage_complete,
        "catalog_warning_count": len(catalog.get("warnings") or []),
        "catalog_skipped_count": len(catalog.get("skipped") or []),
        "catalog_sku_count": len(catalog_skus),
        "published_sku_count": len(selected),
        "binding_count": len(bindings),
        "store_guidance_count": int(bool(store_guidance)),
        "sku_guidance_count": len(selected_guidance),
        "quarantined_legacy_count": len(quarantined_legacy or {}),
        "skipped_by_code": dict(sorted(skipped.items())),
    }
    source_hash = content_sha256({
        "scope": scope.as_dict(),
        "canonical_hashes": {
            sku: content_sha256(document) for sku, document in sorted(selected.items())
        },
        "store_guidance": store_guidance,
        "sku_guidance": selected_guidance,
        "bindings": bindings,
    })
    return CompiledStoreSkuKnowledge(
        scope=scope,
        canonical_documents=selected,
        store_guidance=dict(store_guidance),
        sku_guidance=selected_guidance,
        bindings=bindings,
        quarantined_legacy={
            normalize_sku(key): dict(value)
            for key, value in (quarantined_legacy or {}).items()
            if isinstance(value, Mapping)
        },
        report=report,
        source_hash=source_hash,
    )


__all__ = [
    "CompiledStoreSkuKnowledge",
    "compile_store_sku_knowledge",
    "load_canonical_sku_documents",
    "load_legacy_public_guidance",
]
