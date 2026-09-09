"""Atomic catalog projection independent of listing-bound knowledge generations.

Immutable source objects live under 70_Gerado. SQLite switches the complete
store snapshot in one transaction; neither editorial files nor the original
store-SKU generation/pointers are modified. Missing input is never a tombstone.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import re
import sqlite3

import yaml

from backend.modules.context_hub.catalog_product_projection import catalog_characteristics, project_catalog_product
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import _new_id, _utc_now
from backend.modules.context_hub.store_sku_contracts import canonical_json, content_sha256, normalize_sku
from backend.modules.context_hub.store_sku_repository_support import _scope_for_paths


def _scope_key(scope):
    return content_sha256([getattr(scope, k) for k in ("tenant_scope", "store_ref", "seller_id", "site_id")])


@contextmanager
def _db(paths, *, readonly=False):
    _assert_path_chain_safe(paths.db_path, paths.info_root)
    if readonly:
        con = sqlite3.connect(paths.db_path.as_uri() + "?mode=ro", uri=True, timeout=20)
    else:
        paths.internal_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(paths.db_path, timeout=20, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS catalog_product_generations (
                generation_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                revision TEXT NOT NULL, previous_id TEXT NOT NULL, created_at TEXT NOT NULL,
                manifest_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS catalog_product_scope ON catalog_product_generations(scope_key);
            CREATE TABLE IF NOT EXISTS catalog_product_active (
                scope_key TEXT PRIMARY KEY, generation_id TEXT NOT NULL);
        """)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _active(con, key):
    try:
        row = con.execute("""SELECT g.* FROM catalog_product_active a
            JOIN catalog_product_generations g ON g.generation_id=a.generation_id
            WHERE a.scope_key=? AND g.scope_key=?""", (key, key)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return None
        raise
    return dict(row) if row else None


def _manifest(active):
    return json.loads(active["manifest_json"]) if active else {}


def _directory(paths, scope):
    # Store names are display metadata, never identity or filesystem keys.
    return paths.generated_dir / "CadastroPorLoja" / scope.store_ref / _scope_key(scope)[:16]


def _object_text(scope, document):
    revision = content_sha256(document)
    meta = {**scope.as_dict(), "surface": "catalog_product_reference", "sku": document["sku"],
            "knowledge_role": "catalog_product", "managed": True,
            "source": "store_catalog", "source_revision": revision,
            "trust": "untrusted_reference_data", "status": "source_snapshot"}
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    longest = max([len(item) for item in re.findall(r"`+", payload)] or [0])
    fence = "`" * max(3, longest + 1)
    title = str(document["fields"].get("name") or document["sku"]).replace("\n", " ")
    return ("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=True) + "---\n\n"
            + f"# SKU {document['sku']} — {title}\n\n"
            + "Cadastro original sincronizado. As edições e orientações permanecem em 80_Curadoria.\n\n"
            + f"{fence}json\n{payload}\n{fence}\n")


def _checked_file(paths, entry):
    relative = entry.get("path", "")
    from pathlib import Path
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or value.parts[:2] != ("70_Gerado", "CadastroPorLoja"):
        raise ContextHubValidationError("catalog_document_path_invalid")
    target = paths.vault_dir / value
    _assert_path_chain_safe(target, paths.info_root)
    raw = target.read_text(encoding="utf-8")
    if content_sha256(raw) != entry["file_hash"]:
        raise ContextHubValidationError("catalog_source_file_changed")
    return target


def _write_index(paths, scope, active, manifest):
    folder = _directory(paths, scope)
    target = folder / "Indice.md"
    _assert_path_chain_safe(target, paths.info_root)
    lines = [f"# Fichas do cadastro — {scope.store_name}", "",
             f"Revisão: `{active['revision']}`", "", f"Produtos ativos: {len(manifest)}", ""]
    for sku, entry in sorted(manifest.items()):
        # Labels are escaped to prevent source prose becoming Markdown links.
        label = sku.replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [[{entry['path'][:-3]}|SKU {label}]]")
    text = "\n".join(lines) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != text:
        _write_text_atomic(target, text)


def publish_catalog_snapshot(client_id, scope_value, products, *, deleted_skus=(), complete=True, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    key = _scope_key(scope)
    if not complete:
        raise ContextHubValidationError("catalog_snapshot_incomplete")
    if not isinstance(products, (list, tuple)):
        raise ContextHubValidationError("catalog_snapshot_invalid")
    documents = {}
    for row in products:
        document = project_catalog_product(scope, row)
        sku = document["sku"]
        if sku in documents:
            raise ContextHubValidationError("catalog_duplicate_sku")
        documents[sku] = document
    deleted = {normalize_sku(sku) for sku in deleted_skus}
    if deleted.intersection(documents):
        raise ContextHubValidationError("catalog_tombstone_conflict")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _db(paths) as con:
            active = _active(con, key)
            previous = _manifest(active)
            manifest = dict(previous)
            changed = 0
            for sku, document in documents.items():
                revision = content_sha256(document)
                if sku in previous and previous[sku]["revision"] == revision:
                    try:
                        _checked_file(paths, previous[sku])
                    except FileNotFoundError:
                        # Recover a missing managed object from the verified
                        # manifest after interrupted/older vault publication.
                        # A present edited file is never overwritten here.
                        body = _object_text(scope, document)
                        if content_sha256(body) != previous[sku]["file_hash"]:
                            raise ContextHubValidationError("catalog_source_recovery_mismatch")
                        target = paths.vault_dir / previous[sku]["path"]
                        _assert_path_chain_safe(target, paths.info_root)
                        _write_text_atomic(target, body)
                    continue
                body = _object_text(scope, document)
                target = _directory(paths, scope) / "Objetos" / (content_sha256(sku)[:16] + "-" + revision + ".md")
                _assert_path_chain_safe(target, paths.info_root)
                if target.exists():
                    if target.read_text(encoding="utf-8") != body:
                        raise ContextHubValidationError("catalog_source_file_changed")
                else:
                    _write_text_atomic(target, body)
                manifest[sku] = {"revision": revision, "document": document,
                                 "path": target.relative_to(paths.vault_dir).as_posix(),
                                 "file_hash": content_sha256(body)}
                changed += 1
            removed = sum(1 for sku in deleted if sku in manifest)
            for sku in deleted:
                manifest.pop(sku, None)
            revision = content_sha256({sku: entry["revision"] for sku, entry in manifest.items()})
            if not active or revision != active["revision"]:
                generation_id = "catalog-" + _new_id()
                con.execute("BEGIN IMMEDIATE")
                try:
                    con.execute("INSERT INTO catalog_product_generations VALUES (?,?,?,?,?,?)",
                                (generation_id, key, revision, active["generation_id"] if active else "",
                                 _utc_now(), canonical_json(manifest)))
                    con.execute("INSERT INTO catalog_product_active VALUES (?,?) ON CONFLICT(scope_key) DO UPDATE SET generation_id=excluded.generation_id",
                                (key, generation_id))
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
                active = {"generation_id": generation_id, "revision": revision}
            _write_index(paths, scope, active, manifest)
            return {"status": "completed", "generation_id": active["generation_id"], "revision": revision,
                    "total": len(manifest), "changed": changed, "removed": removed,
                    "pending": 0, "idempotent": not changed and not removed}


def catalog_snapshot_status(client_id, scope_value, *, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    if not paths.db_path.is_file():
        return {"status": "not_synced", "total": 0, "generation_id": "", "revision": ""}
    with _db(paths, readonly=True) as con:
        active = _active(con, _scope_key(scope))
    return {"status": "completed" if active else "not_synced", "total": len(_manifest(active)),
            "generation_id": active["generation_id"] if active else "", "revision": active["revision"] if active else ""}


def load_catalog_product(client_id, scope_value, sku, *, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    sku = normalize_sku(sku)
    missing = {"found": False, "document": {}, "characteristics": [], "generation_id": "", "revision": "", "source_revision": ""}
    if not paths.db_path.is_file():
        return missing
    with _db(paths, readonly=True) as con:
        active = _active(con, _scope_key(scope))
    if not active:
        return missing
    entry = _manifest(active).get(sku)
    if not entry:
        return {**missing, "generation_id": active["generation_id"]}
    _checked_file(paths, entry)
    document = entry["document"]
    if document.get("sku") != sku or document.get("identity") != {k: getattr(scope, k) for k in ("tenant_scope", "store_ref", "seller_id", "site_id")} or content_sha256(document) != entry["revision"]:
        raise ContextHubValidationError("catalog_identity_or_revision_mismatch")
    return {"found": True, "document": document, "characteristics": catalog_characteristics(document),
            "generation_id": active["generation_id"], "revision": entry["revision"],
            "source_revision": entry["revision"], "path": entry["path"]}


def rollback_catalog_snapshot(client_id, scope_value, *, generation_id=None, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = _scope_for_paths(paths, scope_value)
    key = _scope_key(scope)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths), _db(paths) as con:
        active = _active(con, key)
        target_id = generation_id or (active or {}).get("previous_id")
        target = con.execute("SELECT * FROM catalog_product_generations WHERE scope_key=? AND generation_id=?", (key, target_id)).fetchone()
        if not target:
            raise ContextHubValidationError("catalog_rollback_generation_missing")
        target = dict(target)
        manifest = _manifest(target)
        for entry in manifest.values():
            _checked_file(paths, entry)
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("UPDATE catalog_product_active SET generation_id=? WHERE scope_key=?", (target_id, key))
            con.commit()
        except BaseException:
            con.rollback()
            raise
        _write_index(paths, scope, target, manifest)
        return {"status": "completed", "generation_id": target_id, "revision": target["revision"], "total": len(manifest)}
