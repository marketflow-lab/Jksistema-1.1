"""Machine-readable Obsidian index for exact store/SKU documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.store_sku_contracts import content_sha256


STORE_SKU_INDEX_SCHEMA = "jk_obsidian_store_sku_index_v1"


def _identity(scope: Any) -> dict[str, str]:
    return {
        key: str(getattr(scope, key))
        for key in ("tenant_scope", "store_ref", "seller_id", "site_id", "surface")
    }


def store_sku_index_relative_path(scope: Any) -> Path:
    key = content_sha256(_identity(scope))[:32]
    return Path("Lojas") / "_JK_Sistema" / "IndicesStoreSku" / key / "Indice.json"


def store_sku_index_text(
    scope: Any,
    *,
    generation_id: str,
    entries: Mapping[str, Mapping[str, str]],
) -> str:
    payload = {
        "schema": STORE_SKU_INDEX_SCHEMA,
        "identity": _identity(scope),
        "generation_id": str(generation_id),
        "skus": {str(sku): dict(entry) for sku, entry in sorted(entries.items())},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_store_sku_index_entry(
    paths: Any,
    scope: Any,
    *,
    generation_id: str,
    sku: str,
) -> dict[str, str] | None:
    target = paths.generated_dir / store_sku_index_relative_path(scope)
    _assert_path_chain_safe(target, paths.info_root)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ContextHubValidationError("store_sku_index_invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != STORE_SKU_INDEX_SCHEMA:
        raise ContextHubValidationError("store_sku_index_invalid")
    if value.get("identity") != _identity(scope) or str(value.get("generation_id") or "") != generation_id:
        raise ContextHubValidationError("store_sku_index_stale")
    skus = value.get("skus")
    entry = skus.get(sku) if isinstance(skus, dict) else None
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ContextHubValidationError("store_sku_index_invalid")
    allowed = {"path", "file_hash", "content_hash"}
    if set(entry) - allowed or not isinstance(entry.get("path"), str):
        raise ContextHubValidationError("store_sku_index_invalid")
    return {key: str(item) for key, item in entry.items()}


def checked_store_sku_document_path(paths: Any, entry: Mapping[str, str]) -> Path:
    relative = Path(str(entry.get("path") or ""))
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:2] != ("70_Gerado", "Lojas")
        or relative.name != "Contexto.md"
    ):
        raise ContextHubValidationError("store_sku_document_path_invalid")
    target = paths.vault_dir / relative
    _assert_path_chain_safe(target, paths.info_root)
    return target


__all__ = [
    "checked_store_sku_document_path",
    "load_store_sku_index_entry",
    "store_sku_index_relative_path",
    "store_sku_index_text",
]
