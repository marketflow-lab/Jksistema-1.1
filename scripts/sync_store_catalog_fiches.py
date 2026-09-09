"""Inventory and optional idempotent initial load; does not start the application.

Usage: python scripts/sync_store_catalog_fiches.py --info-root ... --client ...
       --store-id ... [--store-id ...] --report ... [--apply]
The report contains only counts, SKU references and machine-readable reasons.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.context_hub.catalog_product_projection import project_catalog_product
from backend.modules.context_hub import catalog_product_repository as repository
from backend.modules.context_hub.catalog_product_sync import load_catalog_source_snapshot, run_catalog_sync_now
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_contracts import StoreSkuScope, content_sha256


def curated_digest(paths):
    # Only the combined digest leaves this function; no curated content/path is logged.
    return content_sha256({p.relative_to(paths.curated_dir).as_posix(): content_sha256(p.read_bytes().hex())
                           for p in sorted(paths.curated_dir.rglob("*")) if p.is_file()})


def bound_inventory(paths, scope):
    if not paths.db_path.exists():
        return {"generation_id": "", "skus": set(), "bindings": 0}
    with sqlite3.connect(paths.db_path.as_uri() + "?mode=ro", uri=True) as con:
        try:
            row = con.execute("""SELECT generation_id FROM context_hub_store_sku_active_generations
                WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?""",
                tuple(scope[key] for key in ("store_ref", "seller_id", "site_id", "surface"))).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return {"generation_id": "", "skus": set(), "bindings": 0}
            raise
        if not row:
            return {"generation_id": "", "skus": set(), "bindings": 0}
        skus = {item[0] for item in con.execute("SELECT sku FROM context_hub_store_sku_documents WHERE generation_id=? AND knowledge_role='canonical_sku'", (row[0],))}
        bindings = con.execute("SELECT COUNT(*) FROM context_hub_store_sku_bindings WHERE generation_id=?", (row[0],)).fetchone()[0]
    return {"generation_id": row[0], "skus": skus, "bindings": bindings}


def inventory_and_sync(client, store_ids, *, info_root, apply=False):
    paths = _tenant_paths(client, info_root=info_root)
    curated_before = curated_digest(paths)
    report = {"schema": "jk_catalog_initial_load_report_v1", "applied": apply, "stores": [], "legacy_pending": []}
    pending = {}
    for store_id in store_ids:
        snapshot = load_catalog_source_snapshot(client, store_id, info_root=info_root)
        scope_value = snapshot["scope"]
        scope = StoreSkuScope.from_mapping(scope_value)
        projected = {doc["sku"]: doc for doc in (project_catalog_product(scope, row) for row in snapshot["products"])}
        bound = bound_inventory(paths, scope_value)
        current = repository.catalog_snapshot_status(client, scope_value, info_root=info_root)
        entry = {"store_name": scope.store_name, "store_id": store_id,
                 "catalog_skus": len(projected), "with_technical_data": sum(bool(doc["fields"]) for doc in projected.values()),
                 "without_technical_data": sum(not doc["fields"] for doc in projected.values()),
                 "with_description": sum(bool(doc["fields"].get("description")) for doc in projected.values()),
                 "existing_bound_fiches": len(bound["skus"]), "existing_bindings": bound["bindings"],
                 "missing_from_bound_fiches": len(set(projected) - bound["skus"]),
                 "catalog_projection_before": current["total"], "explicit_tombstones": len(snapshot["deleted_skus"])}
        for item in snapshot.get("ambiguous_records", []):
            safe = {key: str(item.get(key) or "") for key in ("record_id", "sku", "source", "reason", "row")}
            pending[content_sha256(safe)] = safe
        entry["legacy_pending_tenant_count"] = snapshot["ambiguous_count"]
        if apply:
            first = run_catalog_sync_now(client, store_id, info_root=info_root)
            repeat = run_catalog_sync_now(client, store_id, info_root=info_root)
            if repeat["changed"] or repeat["removed"] or first["generation_id"] != repeat["generation_id"]:
                raise RuntimeError("initial_load_not_idempotent_or_source_changed")
            with repository._db(paths, readonly=True) as con:
                active = repository._active(con, repository._scope_key(scope))
            manifest = repository._manifest(active)
            for sku, doc in projected.items():
                if sku not in manifest or manifest[sku]["revision"] != content_sha256(doc):
                    raise RuntimeError("catalog_projection_does_not_match_source")
                repository._checked_file(paths, manifest[sku])
            if bound_inventory(paths, scope_value) != bound:
                raise RuntimeError("bound_knowledge_changed_during_load")
            entry.update({"result": first, "idempotent_repeat": True, "verified_fiches": len(projected),
                          "existing_bound_knowledge_preserved": True})
            if "001" in projected:
                acceptance = repository.load_catalog_product(client, scope_value, "001", info_root=info_root)
                entry["sku_001"] = {"found": acceptance["found"], "sku": acceptance["document"].get("sku"),
                                    "fields": sorted(acceptance["document"].get("fields", {})),
                                    "revision": acceptance["revision"]}
        report["stores"].append(entry)
    report["legacy_pending"] = sorted(pending.values(), key=lambda row: (row["source"], row["sku"], row["reason"]))
    report["legacy_pending_reason_counts"] = dict(Counter(row["reason"] for row in pending.values()))
    report["curated_preserved"] = curated_digest(paths) == curated_before
    if not report["curated_preserved"]:
        raise RuntimeError("curated_tree_changed_during_inventory")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--info-root", required=True, type=Path)
    parser.add_argument("--client", required=True)
    parser.add_argument("--store-id", required=True, action="append")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = inventory_and_sync(args.client, args.store_id, info_root=args.info_root, apply=args.apply)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "legacy_pending"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
