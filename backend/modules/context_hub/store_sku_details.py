"""Current, exactly scoped Obsidian product information for the training editor."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.metadata import _parse_frontmatter
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.product_evidence_research_repository import list_product_research_evidence
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_contracts import content_sha256, normalize_sku
from backend.modules.context_hub.store_sku_repository_support import _scope_for_paths

_KEY = re.compile(r"^(?:canonical|evidence):[0-9a-f]{32}$")
_METADATA_FIELDS = {"sku", "schema_version", "status", "source_refs", "sources", "fontes", "source_hash", "content_hash"}


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


def _documents(paths: Any, scope: Any, sku: str) -> list[dict[str, Any]]:
    result = []
    expected = {"tenant_scope": scope.tenant_scope, "store_ref": scope.store_ref,
                "seller_id": scope.seller_id, "site_id": scope.site_id, "sku": sku,
                "surface": scope.surface}
    for root in (paths.generated_dir, paths.curated_dir):
        for path in sorted(root.rglob("*.md")):
            try:
                _assert_path_chain_safe(path, paths.info_root)
                metadata, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, UnicodeError, ContextHubValidationError):
                continue
            if not all(str(metadata.get(key) or "").strip() == value for key, value in expected.items()):
                continue
            if str(metadata.get("lifecycle") or "").lower() == "superseded":
                continue
            if scan_dlp(body, source_ref="sku_details_document"):
                continue
            result.append({"title": str(metadata.get("title") or path.stem),
                           "relative_path": path.relative_to(paths.vault_dir).as_posix(),
                           "body": body, "status": str(metadata.get("status") or ""),
                           "managed": metadata.get("managed") is True})
    return result


def load_store_sku_details(client_id: object, scope_value: Mapping[str, Any], sku: str,
                           editor: Mapping[str, Any], *, info_root=None) -> dict[str, Any]:
    """Caller proves current catalog membership; no global or other-store fallback."""
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    sku = normalize_sku(sku)
    canonical: dict[str, Any] = {}
    bindings: set[tuple[str, str]] = {("", "")}
    generation = str(editor.get("generation_id") or "")
    with _connect(paths) as connection:
        active = connection.execute(
            "SELECT generation_id FROM context_hub_store_sku_active_generations "
            "WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?",
            (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
        ).fetchone()
        if active and str(active["generation_id"]) == generation:
            rows = connection.execute(
                "SELECT item_id, variation_id FROM context_hub_store_sku_bindings WHERE generation_id=? AND sku=?",
                (generation, sku),
            ).fetchall()
            bindings.update((str(row["item_id"]), str(row["variation_id"])) for row in rows)
            row = connection.execute(
                "SELECT content_json FROM context_hub_store_sku_documents "
                "WHERE generation_id=? AND sku=? AND knowledge_role='canonical_sku'",
                (generation, sku),
            ).fetchone()
            if row:
                canonical = json.loads(row["content_json"])
    evidence = []
    for item_id, variation_id in sorted(bindings):
        evidence.extend({**fact, "item_id": item_id, "variation_id": variation_id} for fact in list_product_research_evidence(
            client_id, store_ref=scope.store_ref, seller_id=scope.seller_id, site_id=scope.site_id,
            sku=sku, item_id=item_id, variation_id=variation_id, info_root=info_root, recalculate=False,
        ))
    characteristics = _canonical_characteristics(canonical)
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
            row.update(value=edits[row["key"]], edited=True)
    entry = ((editor.get("editorial") or {}).get("skus") or {}).get(sku) or {}
    return {"sku": sku, "canonical_document": canonical, "evidence": evidence, "guidance": guidance,
            "source_body": str(entry.get("source_body") or ""), "characteristics": characteristics,
            "documents": _documents(paths, scope, sku),
            "revision": str((editor.get("editorial") or {}).get("revision") or "")}


def characteristic_edit_payload(edits: dict[str, str], details: Mapping[str, Any]) -> dict[str, Any]:
    edits = validate_characteristic_edits(edits)
    available = {row["key"]: row for row in details["characteristics"]}
    if any(key not in available for key in edits):
        raise ContextHubValidationError("Caracteristica nao pertence ao conhecimento deste SKU; recarregue a ficha.")
    return {"caracteristicas": edits, "caracteristicas_fontes": {
        key: {field: available[key].get(field) for field in ("label", "source", "original_value")}
        for key in edits
    }}
