"""Current, exactly scoped Obsidian product information for the training editor."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from backend.modules.context_hub.contracts import ContextHubConflictError, ContextHubValidationError
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.metadata import _parse_frontmatter
from backend.modules.context_hub.obsidian_store_sku_index import (
    checked_store_sku_document_path,
    load_store_sku_index_entry,
)
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence_research_repository import list_product_research_evidence
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_contracts import content_sha256, normalize_sku, store_directory_name
from backend.modules.context_hub.store_sku_repository_support import (
    _scope_for_paths,
    _sku_path_component,
)

_KEY = re.compile(r"^(?:canonical|evidence|catalog):[0-9a-f]{32}$")
_METADATA_FIELDS = {"sku", "schema_version", "status", "source_refs", "sources", "fontes", "source_hash", "content_hash"}


def _field_identity(label: str) -> str:
    # Match declared field names only; never derive attributes from source prose.
    leaf = re.split(r"[/\.]", label)[-1]
    token = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", leaf.casefold()).encode("ascii", "ignore").decode())
    return {"nomeproduto": "nome", "name": "nome", "title": "nome", "titulo": "nome",
            "description": "descricao", "brand": "marca", "category": "categoria", "model": "modelo"}.get(token, token)


def validate_characteristic_edits(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 500:
        raise ContextHubValidationError("Caracteristicas devem ser um mapa de ate 500 campos.")
    if any(not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(text, str) for key, text in value.items()):
        raise ContextHubValidationError("Caracteristica ou valor invalido.")
    return dict(value)


def _characteristic(source: str, identity: object, label: str, value: object) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"key": source + ":" + content_sha256(identity)[:32], "label": label,
            "value": text, "original_value": text, "source": source, "edited": False}


def _canonical_characteristics(value: object, path: tuple[str, ...] = (), contexts: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [row for key, child in value.items() if key not in _METADATA_FIELDS
                for row in _canonical_characteristics(child, (*path, str(key)), contexts)]
    if isinstance(value, list):
        return [row for index, child in enumerate(value)
                for row in _canonical_characteristics(child, (*path, str(index + 1)), (*contexts, content_sha256(child)))]
    if value is None or value == "" or not path:
        return []
    return [_characteristic("canonical", {"path": path, "value": value, "contexts": contexts}, " / ".join(path).replace("_", " "), value)]


def _read_document(
    paths: Any,
    path: Path,
    scope: Any,
    sku: str,
    *,
    generation_id: str = "",
    file_hash: str = "",
    content_hash: str = "",
    knowledge_role: str = "",
) -> dict[str, Any] | None:
    expected = {"tenant_scope": scope.tenant_scope, "store_ref": scope.store_ref,
                "seller_id": scope.seller_id, "site_id": scope.site_id, "sku": sku,
                "surface": scope.surface}
    try:
        _assert_path_chain_safe(path, paths.info_root)
        raw = path.read_text(encoding="utf-8")
        if file_hash and content_sha256(raw) != file_hash:
            return None
        metadata, body = _parse_frontmatter(raw)
    except (FileNotFoundError, OSError, UnicodeError, ContextHubValidationError):
        return None
    if not all(str(metadata.get(key) or "").strip() == value for key, value in expected.items()):
        return None
    if generation_id and str(metadata.get("generation_id") or "") != generation_id:
        return None
    if content_hash and str(metadata.get("content_hash") or "") != content_hash:
        return None
    if knowledge_role and str(metadata.get("knowledge_role") or "") != knowledge_role:
        return None
    if str(metadata.get("lifecycle") or "").lower() == "superseded":
        return None
    if scan_dlp(body, source_ref="sku_details_document"):
        return None
    return {"title": str(metadata.get("title") or path.stem),
            "relative_path": path.relative_to(paths.vault_dir).as_posix(),
            "body": body, "status": str(metadata.get("status") or ""),
            "managed": metadata.get("managed") is True}


def _documents(
    paths: Any,
    scope: Any,
    sku: str,
    *,
    generation_id: str,
    generated_store_name: str,
    canonical_hash: str,
    editorial_entry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    indexed = False
    try:
        index_entry = load_store_sku_index_entry(
            paths, scope, generation_id=generation_id, sku=sku,
        ) if generation_id else None
        if index_entry is not None:
            indexed = True
            path = checked_store_sku_document_path(paths, index_entry)
            document = _read_document(
                paths, path, scope, sku,
                generation_id=generation_id,
                file_hash=str(index_entry.get("file_hash") or ""),
                content_hash=str(index_entry.get("content_hash") or canonical_hash),
                knowledge_role="canonical_sku",
            )
            if document:
                result.append(document)
    except ContextHubValidationError:
        indexed = True
    if generation_id and not indexed:
        # Compatibility for generations created before the JSON index existed.
        relative = (Path("Lojas") / store_directory_name(scope.store_ref, generated_store_name)
                    / "SKUs" / _sku_path_component(sku) / "Contexto.md")
        document = _read_document(
            paths, paths.generated_dir / relative, scope, sku,
            generation_id=generation_id, content_hash=canonical_hash,
            knowledge_role="canonical_sku",
        )
        if document:
            result.append(document)
    relative = str(editorial_entry.get("relative_path") or "")
    if relative:
        value = Path(relative)
        if not value.is_absolute() and ".." not in value.parts:
            document = _read_document(
                paths, paths.curated_dir / value, scope, sku,
                knowledge_role="sku_guidance",
            )
            if document:
                result.append(document)
    return result


def load_store_sku_details(client_id: object, scope_value: Mapping[str, Any], sku: str,
                           editor: Mapping[str, Any], *, info_root=None) -> dict[str, Any]:
    """Caller proves current catalog membership; no global or other-store fallback."""
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    sku = normalize_sku(sku)
    canonical: dict[str, Any] = {}
    canonical_hash = ""
    bindings: set[tuple[str, str]] = {("", "")}
    generation = str(editor.get("generation_id") or "")
    generated_store_name = scope.store_name
    with _connect(paths) as connection:
        active = connection.execute(
            "SELECT a.generation_id, g.store_name FROM context_hub_store_sku_active_generations a "
            "JOIN context_hub_store_sku_generations g ON g.generation_id=a.generation_id "
            "WHERE a.store_ref=? AND a.seller_id=? AND a.site_id=? AND a.surface=?",
            (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
        ).fetchone()
        if active and str(active["generation_id"]) == generation:
            generated_store_name = str(active["store_name"] or scope.store_name)
            rows = connection.execute(
                "SELECT item_id, variation_id FROM context_hub_store_sku_bindings WHERE generation_id=? AND sku=?",
                (generation, sku),
            ).fetchall()
            bindings.update((str(row["item_id"]), str(row["variation_id"])) for row in rows)
            row = connection.execute(
                "SELECT content_json, content_hash FROM context_hub_store_sku_documents "
                "WHERE generation_id=? AND sku=? AND knowledge_role='canonical_sku'",
                (generation, sku),
            ).fetchone()
            if row:
                canonical = json.loads(row["content_json"])
                canonical_hash = str(row["content_hash"] or "")
    evidence = []
    for item_id, variation_id in sorted(bindings):
        evidence.extend({**fact, "item_id": item_id, "variation_id": variation_id} for fact in list_product_research_evidence(
            client_id, store_ref=scope.store_ref, seller_id=scope.seller_id, site_id=scope.site_id,
            sku=sku, item_id=item_id, variation_id=variation_id, info_root=info_root, recalculate=False,
        ))
    from backend.modules.context_hub.catalog_product_repository import load_catalog_product, catalog_snapshot_status
    from backend.modules.context_hub.catalog_product_sync import get_catalog_sync_status
    catalog_error = False
    try:
        catalog = load_catalog_product(client_id, scope_value, sku, info_root=info_root)
    except (ContextHubValidationError, ContextHubConflictError, OSError, sqlite3.Error, ValueError):
        # A failed generated file must not hide the existing editorial content.
        catalog, catalog_error = {}, True
    try:
        synchronization = get_catalog_sync_status(client_id, scope.store_ref, info_root=info_root)
        if synchronization.get("status") == "not_synced" and catalog.get("found"):
            synchronization = catalog_snapshot_status(client_id, scope_value, info_root=info_root)
    except (ContextHubValidationError, ContextHubConflictError, OSError, sqlite3.Error, ValueError):
        synchronization = {"status": "error", "last_error_code": "catalog_sync_read_failed"}
    if catalog_error:
        synchronization = {"status": "error", "last_error_code": "catalog_source_read_failed"}
    characteristics = [dict(row) for row in catalog.get("characteristics", [])]
    characteristics.extend(_canonical_characteristics(canonical))
    for fact in evidence:
        identity = {key: fact.get(key) for key in ("item_id", "variation_id", "field_name", "scope", "value", "unit", "state")}
        characteristics.append(_characteristic("evidence", identity, str(fact.get("field_name") or "Caracteristica").replace("_", " "),
                                              str(fact.get("value") or "") + (" " + str(fact["unit"]) if fact.get("unit") else "")))
    guidance = dict((editor.get("sku_guidance") or {}).get(sku) or {})
    edits = validate_characteristic_edits(guidance.get("caracteristicas") or {})
    known = {row["key"] for row in characteristics}
    provenance = guidance.get("caracteristicas_fontes") or {}
    for key in edits.keys() - known:
        previous = provenance.get(key, {}) if isinstance(provenance, dict) else {}
        characteristics.append({"key": key, "label": str(previous.get("label") or "Caracteristica editada"),
                                "value": "", "original_value": str(previous.get("original_value") or ""),
                                "source": key.split(":", 1)[0], "edited": False, "source_missing": True})
    for row in characteristics:
        if row["key"] in edits:
            previous = provenance.get(row["key"], {}) if isinstance(provenance, dict) else {}
            row.update(value=edits[row["key"]], edited=True,
                       source_changed=previous.get("original_value") != row.get("original_value"))
    # Keep both sources visible when their labels overlap but values differ.
    labels: dict[str, list[dict]] = {}
    for row in characteristics:
        labels.setdefault(_field_identity(str(row["label"])), []).append(row)
    for rows in labels.values():
        if len({row["source"] for row in rows}) > 1 and len({row.get("original_value") for row in rows}) > 1:
            for row in rows:
                row["source_conflict"] = True
    entry = ((editor.get("editorial") or {}).get("skus") or {}).get(sku) or {}
    return {"sku": sku, "canonical_document": canonical, "evidence": evidence, "guidance": guidance,
            "catalog_document": catalog.get("document") or {}, "catalog_revision": catalog.get("revision") or "",
            "catalog_found": bool(catalog.get("found")), "synchronization": synchronization,
            "source_body": str(entry.get("source_body") or ""), "characteristics": characteristics,
            "documents": _documents(
                paths, scope, sku, generation_id=generation if canonical else "",
                generated_store_name=generated_store_name, canonical_hash=canonical_hash,
                editorial_entry=entry,
            ),
            "revision": str((editor.get("editorial") or {}).get("revision") or "")}


def characteristic_edit_payload(edits: dict[str, str], details: Mapping[str, Any]) -> dict[str, Any]:
    edits = validate_characteristic_edits(edits)
    available = {row["key"]: row for row in details["characteristics"]}
    if any(key not in available for key in edits):
        raise ContextHubValidationError("Caracteristica nao pertence ao conhecimento deste SKU; recarregue a ficha.")
    guidance = details.get("guidance") or {}
    previous_edits = guidance.get("caracteristicas") or {}
    previous_sources = guidance.get("caracteristicas_fontes") or {}
    return {"caracteristicas": edits, "caracteristicas_fontes": {
        # Saving an unrelated note must not acknowledge a changed technical source.
        key: (dict(previous_sources[key]) if key in previous_sources and edits[key] == previous_edits.get(key)
              else {field: available[key].get(field) for field in ("label", "source", "original_value", "source_revision")})
        for key in edits
    }}
