"""Preview or apply the approved, exact-store correction of SKU 232-2.

Run with ``python -m scripts.repair_sku_232_2``. Reports contain only status,
counts and hashes; dossier bodies remain in their canonical stores. Preview
opens an existing SQLite database read-only and never bootstraps Context Hub.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import requests

from backend.modules.context_hub.catalog_product_sync import _stores
from backend.modules.context_hub.contracts import ContextHubConflictError, ContextHubValidationError
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_contracts import StoreSkuBinding, content_sha256
from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation
from backend.modules.context_hub.store_sku_repository_support import (
    _generation_knowledge, _normalize_bindings, _scope_for_paths, _source_hash, _validate_documents,
)
from scripts.review_sku_dossiers_ptbr import repair_sku_232_2_dossier

SKU = "232-2"
ITEM_ID = "MLB2781314798"
_SAFE_ERROR_CODES = frozenset({
    "canonical_sku_missing", "canonical_sku_invalid", "canonical_sku_identity_mismatch",
    "store_database_missing", "exact_store_generation_missing",
    "generation_document_hash_mismatch", "generation_binding_hash_mismatch",
    "exact_listing_sku_binding_mismatch", "exact_listing_sku_binding_missing",
    "generation_source_hash_mismatch", "binding_store_config_unavailable",
    "binding_store_config_identity_mismatch", "binding_access_token_missing",
    "binding_live_lookup_failed", "binding_live_identity_mismatch",
    "binding_live_sku_unavailable", "binding_live_sku_mismatch",
    "canonical_sku_revision_conflict", "store_identity_alias_mismatch",
    "repair_listing_out_of_scope", "store_generation_revision_conflict",
    "repair_would_change_unrelated_guidance",
    "projection_publication_state_changed_canonical_preserved",
    "projection_publication_failed_canonical_restored",
    "canonical_sku_changed_after_publication", "store_generation_changed_after_publication",
})


def _safe_error_code(error):
    message = str(error)
    if message in _SAFE_ERROR_CODES:
        return message
    if isinstance(error, ContextHubConflictError) and message == "Outra operacao do Context Hub esta em andamento.":
        return "context_hub_operation_locked"
    return "repair_failed"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_canonical(paths):
    target = paths.tenant_dir / "SKU" / f"{SKU}.json"
    _assert_path_chain_safe(target, paths.info_root)
    if not target.is_file():
        raise ContextHubValidationError("canonical_sku_missing")
    raw = target.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ContextHubValidationError("canonical_sku_invalid") from exc
    if not isinstance(payload, dict) or payload.get("sku") != SKU:
        raise ContextHubValidationError("canonical_sku_identity_mismatch")
    return target, raw, payload


def _snapshot(paths, scope, item_id, *, proven_missing_binding=False):
    """One consistent, read-only DB transaction; no schema initialization."""
    _assert_path_chain_safe(paths.db_path, paths.info_root)
    if not paths.db_path.is_file():
        raise ContextHubValidationError("store_database_missing")
    # Read WAL-backed committed state as well. SQLite may maintain its own
    # -shm bookkeeping, but this connection cannot mutate the logical database.
    with closing(sqlite3.connect(paths.db_path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        row = connection.execute(
            """SELECT a.generation_id, a.version, g.store_name, g.source_hash
               FROM context_hub_store_sku_active_generations a
               JOIN context_hub_store_sku_generations g ON g.generation_id=a.generation_id
                 AND g.store_ref=a.store_ref AND g.seller_id=a.seller_id
                 AND g.site_id=a.site_id AND g.surface=a.surface AND g.status='active'
               WHERE a.store_ref=? AND a.seller_id=? AND a.site_id=? AND a.surface=?""",
            (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
        ).fetchone()
        if row is None:
            raise ContextHubValidationError("exact_store_generation_missing")
        generation_id = str(row["generation_id"])
        scope = replace(scope, store_name=str(row["store_name"]))
        canonical, general, guidance = _generation_knowledge(connection, generation_id)
        documents = connection.execute(
            "SELECT content_json, content_hash, source_hash FROM context_hub_store_sku_documents "
            "WHERE generation_id=?", (generation_id,),
        ).fetchall()
        if any(content_sha256(entry["content_json"]) != entry["content_hash"]
               or entry["source_hash"] != entry["content_hash"] for entry in documents):
            raise ContextHubValidationError("generation_document_hash_mismatch")
        rows = connection.execute(
            "SELECT item_id, variation_id, sku, binding_hash FROM context_hub_store_sku_bindings "
            "WHERE generation_id=? ORDER BY item_id, variation_id, sku", (generation_id,),
        ).fetchall()
        bindings = [{key: entry[key] for key in ("item_id", "variation_id", "sku")} for entry in rows]
        for entry in rows:
            binding = StoreSkuBinding.from_mapping(dict(entry), site_id=scope.site_id)
            if content_sha256(binding.as_dict()) != entry["binding_hash"]:
                raise ContextHubValidationError("generation_binding_hash_mismatch")
        exact = [entry for entry in bindings if entry["item_id"] == item_id]
        if any(entry["sku"] != SKU or entry["variation_id"] for entry in exact) or SKU not in canonical:
            raise ContextHubValidationError("exact_listing_sku_binding_mismatch")
        if not exact and not proven_missing_binding:
            raise ContextHubValidationError("exact_listing_sku_binding_missing")
        normalized = _normalize_bindings(bindings, scope=scope, canonical_skus=set(canonical))
        actual_hash = _source_hash(scope, canonical, general, guidance, normalized)
        if actual_hash != row["source_hash"]:
            raise ContextHubValidationError("generation_source_hash_mismatch")
    return {
        "scope": scope, "generation_id": generation_id, "version": int(row["version"]),
        "source_hash": actual_hash, "canonical": canonical, "general": general,
        "guidance": guidance, "bindings": bindings,
    }


def _prove_live_binding(paths, scope, item_id):
    """Authenticate one fixed GET from the exact local integration, without refresh.

    External response bodies and access tokens are never returned or persisted.
    Every supplied SKU identifier must agree; a listing with variations requires
    a different maintenance workflow and cannot be inferred by this repair.
    """
    try:
        stores = [entry for entry in _stores(paths) if entry.get("store_id") == scope.store_ref]
    except (OSError, ValueError, ContextHubValidationError):
        raise ContextHubValidationError("binding_store_config_unavailable") from None
    if len(stores) != 1:
        raise ContextHubValidationError("binding_store_config_identity_mismatch")
    integrations = stores[0].get("integracoes")
    ml = integrations.get("mercadolivre") if isinstance(integrations, dict) else None
    if not isinstance(ml, dict):
        raise ContextHubValidationError("binding_store_config_identity_mismatch")
    seller_values = [str(ml[key]).strip() for key in ("user_id", "seller_id") if ml.get(key)]
    if (not seller_values or any(value != scope.seller_id for value in seller_values)
            or str(ml.get("site_id") or "").strip().upper() != scope.site_id):
        raise ContextHubValidationError("binding_store_config_identity_mismatch")
    token = ml.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise ContextHubValidationError("binding_access_token_missing")
    response = None
    try:
        response = requests.get(
            f"https://api.mercadolibre.com/items/{ITEM_ID}",
            headers={"Authorization": f"Bearer {token.strip()}"},
            timeout=25, allow_redirects=False,
        )
        if response.status_code != 200:
            raise ContextHubValidationError("binding_live_lookup_failed")
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise ContextHubValidationError("binding_live_lookup_failed") from None
    finally:
        if response is not None:
            response.close()
    if (not isinstance(payload, dict) or payload.get("id") != item_id
            or str(payload.get("seller_id") or "") != scope.seller_id
            or payload.get("site_id") != scope.site_id or payload.get("status") != "active"
            or payload.get("variations") != []):
        raise ContextHubValidationError("binding_live_identity_mismatch")
    attributes = payload.get("attributes", [])
    if not isinstance(attributes, list) or any(not isinstance(value, dict) for value in attributes):
        raise ContextHubValidationError("binding_live_sku_unavailable")
    values = []
    for attribute in attributes:
        if attribute.get("id") == "SELLER_SKU":
            value = attribute.get("value_name")
            if not isinstance(value, str) or not value.strip():
                raise ContextHubValidationError("binding_live_sku_unavailable")
            values.append(value.strip())
    custom = payload.get("seller_custom_field")
    if custom is not None and custom != "":
        if not isinstance(custom, str) or not custom.strip():
            raise ContextHubValidationError("binding_live_sku_unavailable")
        values.append(custom.strip())
    if not values or any(value != SKU for value in values):
        raise ContextHubValidationError("binding_live_sku_mismatch")


def _revision(snapshot):
    return content_sha256({key: snapshot[key] for key in ("generation_id", "version", "source_hash")})


def _replace_if_unchanged(paths, target, expected: bytes, replacement: bytes):
    """CAS under the tenant's publication lock, including exact byte changes."""
    _assert_path_chain_safe(target, paths.info_root)
    if not target.is_file() or target.read_bytes() != expected:
        raise ContextHubConflictError("canonical_sku_revision_conflict")
    staged = target.with_name(f".{target.name}.{uuid4().hex}.repair")
    try:
        # utf-8 (not utf-8-sig) preserves an original BOM during compensation.
        _write_text_atomic(staged, replacement.decode("utf-8"))
        # Recheck after staging; an edit during the write must survive too.
        if not target.is_file() or target.read_bytes() != expected:
            raise ContextHubConflictError("canonical_sku_revision_conflict")
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def _publish(paths, snapshot, canonical, bindings):
    return publish_store_sku_generation(
        paths.client_id, snapshot["scope"].as_dict(), canonical_documents=canonical,
        store_guidance=snapshot["general"], sku_guidance=snapshot["guidance"],
        bindings=bindings, preserve_curated_files=True,
        expected_active_generation_id=snapshot["generation_id"],
        actor="approved-sku-232-2-repair", info_root=paths.info_root,
    )


def repair(tenant, *, info_root, seller_id, site_id, item_id, store_ref=None,
           store_id=None, apply=False, expected_canonical_sha256=None,
           expected_generation_id=None, refresh_binding=False):
    """Repair the canonical file and one exact active projection, with CAS.

    A publication failure compensates the canonical write only while its bytes
    still belong to this attempt. A lost post-commit acknowledgement is detected
    by the active source hash, preventing reversal of a successful publication.
    Concurrent edits are retained and reported as conflicts, never overwritten.
    """
    if store_ref and store_id and str(store_ref) != str(store_id):
        raise ContextHubValidationError("store_identity_alias_mismatch")
    paths = _tenant_paths(tenant, info_root=info_root)
    scope = _scope_for_paths(paths, {"store_ref": store_ref or store_id,
        "seller_id": seller_id, "site_id": site_id})
    item_id = str(item_id or "").strip().upper()
    if item_id != ITEM_ID:
        raise ContextHubValidationError("repair_listing_out_of_scope")
    proven_missing_binding = False
    try:
        snapshot = _snapshot(paths, scope, item_id)
    except ContextHubValidationError as error:
        if not refresh_binding or str(error) != "exact_listing_sku_binding_missing":
            raise
        _prove_live_binding(paths, scope, item_id)
        proven_missing_binding = True
        snapshot = _snapshot(paths, scope, item_id, proven_missing_binding=True)
    bindings = deepcopy(snapshot["bindings"])
    added_bindings = int(not any(entry["item_id"] == item_id for entry in bindings))
    if added_bindings:
        bindings.append({"item_id": item_id, "variation_id": "", "sku": SKU})
    target, original_bytes, original = _read_canonical(paths)
    if expected_canonical_sha256 and expected_canonical_sha256 != _digest(original_bytes):
        raise ContextHubConflictError("canonical_sku_revision_conflict")
    if expected_generation_id and expected_generation_id != snapshot["generation_id"]:
        raise ContextHubConflictError("store_generation_revision_conflict")
    corrected = repair_sku_232_2_dossier(original)
    canonical = deepcopy(snapshot["canonical"])
    canonical[SKU] = repair_sku_232_2_dossier(canonical[SKU])
    canonical, guidance = _validate_documents(canonical, snapshot["general"], snapshot["guidance"])
    if guidance != snapshot["guidance"]:
        raise ContextHubValidationError("repair_would_change_unrelated_guidance")
    new_bytes = (json.dumps(corrected, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    canonical_changed = corrected != original
    projection_changed = canonical != snapshot["canonical"] or bool(added_bindings)
    normalized_bindings = _normalize_bindings(bindings, scope=snapshot["scope"],
                                               canonical_skus=set(canonical))
    next_source_hash = _source_hash(snapshot["scope"], canonical, snapshot["general"],
                                   snapshot["guidance"], normalized_bindings)
    report = {
        "applied": False, "changed": canonical_changed or projection_changed,
        "canonical_changed_count": int(canonical_changed),
        "projection_changed_count": int(projection_changed),
        "canonical_document_count": len(canonical), "binding_count": len(bindings),
        "added_bindings": added_bindings,
        "canonical_before_sha256": _digest(original_bytes),
        "canonical_after_sha256": _digest(new_bytes if canonical_changed else original_bytes),
        "projection_before_sha256": snapshot["source_hash"],
        "projection_after_sha256": next_source_hash,
        "generation_before_sha256": content_sha256(snapshot["generation_id"]),
        "generation_after_sha256": content_sha256(snapshot["generation_id"]),
        "revision_sha256": _revision(snapshot), "publication_acknowledgement_recovered": False,
    }
    if not apply or not report["changed"]:
        report["applied"] = bool(apply)
        return report

    repair_paths = replace(paths, lock_path=paths.internal_dir / "sku-232-2-repair.lock")
    with _tenant_thread_lock(paths), _exclusive_file_lock(repair_paths):
        with _exclusive_file_lock(paths):
            current = _snapshot(paths, scope, item_id, proven_missing_binding=proven_missing_binding)
            if _revision(current) != _revision(snapshot):
                raise ContextHubConflictError("store_generation_revision_conflict")
            if canonical_changed:
                _replace_if_unchanged(paths, target, original_bytes, new_bytes)
            elif target.read_bytes() != original_bytes:
                raise ContextHubConflictError("canonical_sku_revision_conflict")
        try:
            published = _publish(paths, snapshot, canonical, bindings) if projection_changed else {
                "generation_id": snapshot["generation_id"],
            }
        except Exception as error:
            with _exclusive_file_lock(paths):
                current = _snapshot(paths, scope, item_id, proven_missing_binding=proven_missing_binding)
                if current["source_hash"] == next_source_hash:
                    # Commit succeeded but a notification or response failed.
                    published = {"generation_id": current["generation_id"]}
                    report["publication_acknowledgement_recovered"] = True
                else:
                    if current["canonical"][SKU] != snapshot["canonical"][SKU]:
                        # A competing publisher may have already consumed our
                        # repaired file. Reverting it would break that new state.
                        raise ContextHubConflictError(
                            "projection_publication_state_changed_canonical_preserved"
                        ) from error
                    if canonical_changed:
                        _replace_if_unchanged(paths, target, new_bytes, original_bytes)
                    raise ContextHubConflictError("projection_publication_failed_canonical_restored") from error
        expected_bytes = new_bytes if canonical_changed else original_bytes
        current = _snapshot(paths, scope, item_id)
        if target.read_bytes() != expected_bytes:
            raise ContextHubConflictError("canonical_sku_changed_after_publication")
        if (current["generation_id"] != published["generation_id"]
                or current["source_hash"] != next_source_hash):
            raise ContextHubConflictError("store_generation_changed_after_publication")
        report["generation_after_sha256"] = content_sha256(current["generation_id"])
        report["applied"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--info-root", type=Path, required=True)
    parser.add_argument("--store-ref", "--store-id", dest="store_ref", required=True)
    parser.add_argument("--seller-id", required=True)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--expected-canonical-sha256")
    parser.add_argument("--expected-generation-id")
    parser.add_argument("--refresh-binding", action="store_true",
                        help="Prove a missing binding with the exact store's authenticated read-only GET")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = repair(**vars(args))
    except Exception as error:
        # Do not print exception payloads: dependencies may include paths/content.
        failure = {"applied": False, "error_type": type(error).__name__,
                   "error_code": _safe_error_code(error)}
        if error.__cause__ is not None:
            failure.update(cause_error_type=type(error.__cause__).__name__,
                           cause_error_code=_safe_error_code(error.__cause__))
        print(json.dumps(failure))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
