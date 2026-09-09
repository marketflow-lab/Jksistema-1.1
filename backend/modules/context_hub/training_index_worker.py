"""Background reconciliation of current Obsidian notes into the private UI index."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import copy
import json
import os
import logging
import sqlite3
import threading
import time

from backend.services.path_coordination import canonical_path_key

from . import training_read_index as index
from .bootstrap import bootstrap_context_hub
from .contracts import ContextHubValidationError, ContextHubConflictError
from .curation_records import _read_curated_note
from .locking import _exclusive_file_lock, _tenant_thread_lock
from .path_safety import _assert_path_chain_safe
from .store_sku_contracts import content_sha256, normalize_sku
from . import store_sku_editor as editor_module
from .store_sku_details import load_store_sku_details, validate_characteristic_edits
from .obsidian_store_sku_index import STORE_SKU_INDEX_SCHEMA, store_sku_index_relative_path

_METADATA_INTERVAL = 2.0
_DEBOUNCE = 1.0
_FULL_INTERVAL = 60.0
_IDLE_LEASE = 120.0
_LOGGER = logging.getLogger(__name__)
_JOBS = {}
_GUARD = threading.Lock()


def _stat(path):
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns


def _walk_files(root, paths, suffixes):
    if not root.is_dir():
        raise OSError("training_source_directory_missing")
    def failed(error):
        raise error
    found = []
    for folder, directories, files in os.walk(root, onerror=failed, followlinks=False):
        for name in directories:
            _assert_path_chain_safe(type(root)(folder) / name, paths.info_root)
        for name in files:
            target = type(root)(folder) / name
            if target.suffix in suffixes:
                found.append(target)
    return sorted(found)


class NoteCache:
    """Cache validated records; incomplete I/O never becomes a deleted note."""
    def __init__(self):
        self.notes = {}
        self.products = {}
        self.product_tokens = {}
        self.catalog_checks = {}
        self.last_source = None
        self.last_status = "ready"
        self.last_full = 0.0
        self.initialized = False

    def scan(self, paths, *, force=False):
        found = {}
        for path in _walk_files(paths.curated_dir, paths, {".md"}):
            _assert_path_chain_safe(path, paths.info_root)
            signature = _stat(path)
            previous = self.notes.get(path)
            if previous is not None and previous["signature"] == signature and not force:
                found[path] = previous
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if previous is not None and previous["hash"] == digest:
                found[path] = {**previous, "signature": signature}
                continue
            try:
                record = _read_curated_note(paths, path)
            except (ContextHubValidationError, UnicodeError):
                record = None
            if _stat(path) != signature or path.read_bytes() != raw:
                raise TrainingSourceChanged("editorial_write_in_progress")
            found[path] = {"signature": signature, "record": record, "hash": digest}
        self.notes = found
        return content_sha256([(str(p.relative_to(paths.curated_dir)), v["hash"]) for p, v in found.items()])

    def scoped_paths(self, paths, scope):
        found = {}
        for path, cached in self.notes.items():
            record = cached["record"]
            metadata = record["metadata"] if record else {}
            if str(metadata.get("lifecycle") or "").casefold() == "superseded":
                continue
            slot = None
            if editor_module._scope_matches(metadata, scope):
                role = str(metadata.get("knowledge_role") or "")
                if role == "store_guidance" and not str(metadata.get("sku") or "").strip():
                    slot = ""
                elif role == "sku_guidance":
                    try:
                        slot = normalize_sku(metadata.get("sku"))
                    except ContextHubValidationError:
                        pass
            if slot is None:
                slot = editor_module._path_slot(path, paths, scope)
            if slot is not None:
                found.setdefault(slot, []).append(path)
        return found

    def read_note(self, paths, path):
        value = self.notes[path]["record"]
        if value is None:
            raise ContextHubValidationError("editorial_note_invalid")
        return value

    def file_hash(self, path, paths):
        return self.notes[path]["hash"]


class TrainingSourceChanged(RuntimeError):
    pass


def _catalog_batch(paths, scope, cache, *, force=False):
    from . import catalog_product_repository as catalog
    from .catalog_product_projection import catalog_characteristics
    from .catalog_product_sync import get_catalog_sync_status_for_scope
    synchronization = get_catalog_sync_status_for_scope(paths.client_id, scope.as_dict(), info_root=paths.info_root)
    with catalog._db(paths, readonly=True) as connection:
        active = catalog._active(connection, catalog._scope_key(scope))
    if not active:
        return {}, synchronization, ""
    manifest = catalog._manifest(active)  # Exactly one decode per scope, never per SKU.
    result = {}
    for sku, entry in manifest.items():
        document = entry["document"]
        expected = {key: getattr(scope, key) for key in ("tenant_scope", "store_ref", "seller_id", "site_id")}
        if (document.get("sku") != sku or document.get("identity") != expected
                or content_sha256(document) != entry["revision"]):
            raise ContextHubValidationError("catalog_identity_or_revision_mismatch")
        target = paths.vault_dir / entry["path"]
        _assert_path_chain_safe(target, paths.info_root)
        token = (_stat(target), entry["file_hash"])
        if force or cache.catalog_checks.get(target) != token:
            catalog._checked_file(paths, entry)
            cache.catalog_checks[target] = token
        result[sku] = {"found": True, "document": document, "characteristics": catalog_characteristics(document),
                       "generation_id": active["generation_id"], "revision": entry["revision"],
                       "source_revision": entry["revision"], "path": entry["path"]}
    if synchronization.get("status") == "not_synced":
        synchronization = {"status": "completed", "generation_id": active["generation_id"],
                           "revision": active["revision"], "total": len(result)}
    return result, synchronization, active["generation_id"]


def _index_lookup(paths, scope, generation):
    target = paths.generated_dir / store_sku_index_relative_path(scope)
    _assert_path_chain_safe(target, paths.info_root)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        value = None
    expected = {key: getattr(scope, key) for key in ("tenant_scope", "store_ref", "seller_id", "site_id", "surface")}
    invalid = value is not None and (not isinstance(value, dict) or value.get("schema") != STORE_SKU_INDEX_SCHEMA
        or value.get("identity") != expected or value.get("generation_id") != generation or not isinstance(value.get("skus"), dict))
    def lookup(_paths, _scope, *, generation_id, sku):
        if invalid:
            raise ContextHubValidationError("store_sku_index_invalid")
        entry = (value or {}).get("skus", {}).get(sku)
        if entry is not None and (not isinstance(entry, dict) or set(entry) - {"path", "file_hash", "content_hash"}
                                  or not isinstance(entry.get("path"), str)):
            raise ContextHubValidationError("store_sku_index_invalid")
        return entry
    return lookup


def _previous_products(client_id, scope, info_root):
    _, _, key = index._identity(client_id, scope, info_root)
    try:
        with index._db(client_id, info_root) as connection:
            rows = connection.execute("SELECT sku,payload_json FROM training_fichas WHERE " + index._WHERE, key).fetchall()
        return {row["sku"]: json.loads(row["payload_json"])["sku_details"] for row in rows}
    except Exception:
        return {}


def _with_current_editor(previous, sku, editor):
    product = copy.deepcopy(previous)
    guidance = (editor.get("sku_guidance") or {}).get(sku) or {}
    entry = (editor.get("editorial") or {}).get("skus", {}).get(sku, {})
    edits = validate_characteristic_edits(guidance.get("caracteristicas") or {})
    sources = guidance.get("caracteristicas_fontes") or {}
    rows = [{**row, "value": row.get("original_value", ""), "edited": False}
            for row in product.get("characteristics", []) if not row.get("source_missing") or row["key"] in edits]
    known = {row["key"] for row in rows}
    for key in edits.keys() - known:
        original = sources.get(key, {})
        rows.append({"key": key, "label": original.get("label", "Caracteristica editada"),
                     "source": key.split(":", 1)[0], "original_value": original.get("original_value", ""),
                     "value": "", "source_missing": True, "edited": False})
    for row in rows:
        row.pop("source_changed", None)
        if row["key"] in edits:
            row.update(value=edits[row["key"]], edited=True,
                       source_changed=sources.get(row["key"], {}).get("original_value") != row.get("original_value"))
    documents = [row for row in product.get("documents", []) if not str(row.get("relative_path", "")).startswith("80_Curadoria/")]
    if entry.get("source_body"):
        documents.append({"title": "Orientacoes do SKU " + sku,
                          "relative_path": "80_Curadoria/" + str(entry.get("relative_path") or ""),
                          "body": entry["source_body"], "status": entry.get("status", "draft"), "managed": False})
    product.update(guidance=guidance, source_body=entry.get("source_body", ""), documents=documents,
                   characteristics=rows, revision=editor["editorial"]["revision"],
                   synchronization={"status": "error", "last_error_code": "technical_source_unavailable"})
    product["source_revision"] = content_sha256([{key: row.get(key) for key in (
        "key", "original_value", "source_revision", "source_missing")} for row in rows])
    return product


def _products(client_id, scope, paths, exact, editor, cache, *, info_root, force, prioritized_sku, source_signature):
    if not cache.products:
        cache.products = _previous_products(client_id, scope, info_root)
    try:
        catalog, synchronization, catalog_generation = _catalog_batch(paths, exact, cache, force=force)
        product_status = "ready"
    except (OSError, sqlite3.Error, ValueError, ContextHubValidationError, ContextHubConflictError):
        catalog, synchronization, catalog_generation = {}, {"status": "error", "last_error_code": "catalog_source_read_failed"}, "unavailable"
        product_status = "degraded"
    generation = str(editor.get("generation_id") or "")
    from .storage import _connect
    with _connect(paths) as connection:
        evidence_skus = {str(row[0]) for row in connection.execute(
            "SELECT DISTINCT sku FROM product_evidence_batches WHERE store_ref=? AND seller_id=? AND site_id=? AND status='completed'",
            (exact.store_ref, exact.seller_id, exact.site_id))}
    try:
        lookup = _index_lookup(paths, exact, generation)
    except (OSError, ValueError):
        def lookup(*args, **kwargs):
            raise ContextHubValidationError("store_sku_index_invalid")
    from .product_evidence_research_repository import list_product_research_evidence
    def evidence_loader(*args, **kwargs):
        if str(kwargs.get("sku") or "") not in evidence_skus:
            return []
        return list_product_research_evidence(*args, **kwargs, _prepared_paths=paths)
    skus = set(catalog) | set(editor.get("sku_guidance") or {}) | set(editor["editorial"]["skus"])
    skus.update((editor.get("published_guidance") or {}).get("canonical_skus") or [])
    if product_status == "degraded":
        skus.update(cache.products)
    result = {}
    for sku in sorted(skus, key=lambda value: (value != prioritized_sku, value)):
        guidance = (editor.get("sku_guidance") or {}).get(sku) or {}
        token = content_sha256([source_signature, generation, catalog_generation, catalog.get(sku, {}).get("revision"), guidance,
                                editor["editorial"]["skus"].get(sku, {}).get("hash")])
        if cache.product_tokens.get(sku) == token and sku in cache.products and not force:
            result[sku] = cache.products[sku]
            continue
        try:
            if product_status == "degraded" and sku in cache.products and not force:
                # Preserve the last validated technical source independently of editorial freshness.
                old = cache.products[sku]
                product = {"found": old.get("catalog_found", False), "document": old.get("catalog_document") or {},
                           "revision": old.get("catalog_revision") or "", "characteristics": [
                               {**row, "value": row.get("original_value", ""), "edited": False}
                               for row in old.get("characteristics", []) if row.get("source") == "catalog"]}
            else:
                product = catalog.get(sku, {"found": False})
            result[sku] = load_store_sku_details(client_id, scope, sku, editor, info_root=info_root,
                catalog_snapshot=product, catalog_sync=synchronization, index_lookup=lookup,
                evidence_loader=evidence_loader)
            if result[sku].get("canonical_document") and not any(
                    str(doc.get("relative_path") or "").startswith("70_Gerado/") for doc in result[sku].get("documents", [])):
                raise ContextHubValidationError("canonical_source_unavailable")
            cache.product_tokens[sku] = token
        except Exception:
            product_status = "degraded"
            result[sku] = _with_current_editor(cache.products.get(
                sku, index._empty_details(sku, editor["editorial"]["revision"])), sku, editor)
    cache.products = result
    return result, product_status


def _database_signature(paths):
    return [(_stat(path) if path.exists() else None) for path in (
        paths.db_path, paths.db_path.with_name(paths.db_path.name + "-wal"),
        paths.internal_dir / "catalog-sync.db", paths.internal_dir / "catalog-sync.db-wal")]


def _source_fingerprint(paths, notes_hash, scope=None):
    files = []
    for path in _walk_files(paths.generated_dir, paths, {".json", ".md"}):
        _assert_path_chain_safe(path, paths.info_root)
        files.append((path.relative_to(paths.generated_dir).as_posix(), _stat(path)))
    return content_sha256([notes_hash, scope.as_dict() if scope else {}, _database_signature(paths), files])


def _recover_source_journal(paths):
    # Caller holds the existing Hub locks. Recovery only completes/rolls back the
    # canonical publication journal; it never approves or republishes guidance.
    from .journal import _recover_publish_journal
    try:
        _recover_publish_journal(paths)
    except ContextHubValidationError:
        raise ContextHubConflictError("training_source_publication_pending") from None
    if paths.journal_path.exists():
        raise ContextHubConflictError("training_source_publication_pending")


def _rebuild_batch(client_id, entries, *, info_root=None, notes=None, stability_delay=0.0):
    """One tenant scan and validation boundary for all active store scopes."""
    paths, _, _ = index._identity(client_id, entries[0]["scope"], info_root)
    notes = notes or NoteCache()
    worker_paths = replace(paths, lock_path=paths.internal_dir / "training-index-worker.lock")
    with _exclusive_file_lock(worker_paths, timeout=0.05):
        if not notes.initialized:
            bootstrap_context_hub(client_id, info_root=paths.info_root)
            notes.initialized = True
        if paths.journal_path.exists():
            with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
                _recover_source_journal(paths)
        before = notes.scan(paths, force=any(entry["force"] for entry in entries))
        technical = _source_fingerprint(paths, "")
        pending, results = [], {}
        for entry in entries:
            _, exact, identity = index._identity(client_id, entry["scope"], info_root)
            source = content_sha256([before, technical, exact.as_dict()])
            if not entry["force"] and entry["cache"].last_source == source:
                index.mark_checked(client_id, entry["scope"], info_root=info_root, status=entry["cache"].last_status)
                results[identity] = index.index_status(client_id, entry["scope"], info_root=info_root)
            else:
                pending.append((entry, exact, identity, source))
        if not pending:
            return results
        if stability_delay > 0 and any(entry["cache"].last_source != source for entry, _, _, source in pending):
            time.sleep(stability_delay)
            if notes.scan(paths) != before or _source_fingerprint(paths, "") != technical:
                raise TrainingSourceChanged("source_not_stable")
        staged = []
        with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
            if paths.journal_path.exists():
                _recover_source_journal(paths)
            for entry, exact, identity, source in pending:
                index.mark_updating(client_id, entry["scope"], info_root=info_root)
                editor = editor_module._snapshot(paths, exact, note_paths=notes.scoped_paths(paths, exact),
                                                 read_note=notes.read_note, file_hash=notes.file_hash)
                products, status = _products(client_id, entry["scope"], paths, exact, editor, entry["cache"],
                    info_root=info_root, force=entry["force"], prioritized_sku=entry["sku"], source_signature=technical)
                staged.append((entry, identity, source, editor, products, status))
            if notes.scan(paths) != before or _source_fingerprint(paths, "") != technical:
                raise TrainingSourceChanged("source_changed_during_projection")
            for entry, identity, source, editor, products, status in staged:
                results[identity] = index.publish_generation(client_id, entry["scope"], editor, products,
                                                             info_root=info_root, product_status=status)
                entry["cache"].last_source, entry["cache"].last_status = source, status
        return results


def rebuild_now(client_id, scope, *, info_root=None, cache=None, force=False, sku="", stability_delay=0.0):
    """Synchronous worker/test entrypoint; never called by an HTTP reader."""
    cache = cache or NoteCache()
    _, _, identity = index._identity(client_id, scope, info_root)
    results = _rebuild_batch(client_id, [{"scope": scope, "cache": cache, "force": force, "sku": sku}],
                             info_root=info_root, notes=cache, stability_delay=stability_delay)
    return results[identity]


def _active_entries(job, now, full):
    with _GUARD:
        for identity in list(job["scopes"]):
            if now - job["scopes"][identity]["visited"] > _IDLE_LEASE:
                job["scopes"].pop(identity)
        return [{"scope": dict(scope["scope"]), "cache": scope["cache"], "sku": scope["sku"],
                 "force": full or scope["revision"] != scope["handled"], "revision": scope["revision"],
                 "changed": scope["changed"], "ref": scope}
                for scope in job["scopes"].values()]


def _mark_batch(job, entries, *, unavailable):
    mark = index.mark_unavailable if unavailable else index.mark_updating
    for entry in entries:
        try:
            mark(job["client_id"], entry["scope"], info_root=job["info_root"])
        except Exception:
            pass


def _run(key, job):
    notes = NoteCache()
    full_at = 0.0
    try:
        while not job["stop"].is_set():
            now = time.monotonic()
            full = now - full_at >= _FULL_INTERVAL
            entries = _active_entries(job, now, full)
            if not entries:
                return
            job["event"].clear()
            explicit = [entry for entry in entries if entry["revision"] != entry["ref"]["handled"] and entry["revision"] > 0]
            if explicit:
                wait = max(0.0, _DEBOUNCE - (now - max(entry["changed"] for entry in explicit)))
                if job["stop"].wait(wait):
                    return
            try:
                _rebuild_batch(job["client_id"], entries, info_root=job["info_root"], notes=notes,
                               stability_delay=0.0 if explicit else _DEBOUNCE)
                with _GUARD:
                    for entry in entries:
                        entry["ref"]["handled"] = entry["revision"]
                if full:
                    full_at = now
            except (ContextHubConflictError, TrainingSourceChanged):
                _LOGGER.info("training_index stage=refresh code=source_busy")
                _mark_batch(job, entries, unavailable=False)
            except Exception:
                _LOGGER.warning("training_index stage=refresh code=refresh_failed")
                _mark_batch(job, entries, unavailable=True)
            _LOGGER.info("training_index stage=refresh duration_ms=%d count=%d",
                         int((time.monotonic() - now) * 1000), sum(len(entry["cache"].products) for entry in entries))
            job["event"].wait(_METADATA_INTERVAL)
    finally:
        with _GUARD:
            if _JOBS.get(key) is job:
                _JOBS.pop(key, None)


def request_refresh(client_id, scope, *, info_root=None, immediate=False, sku=""):
    paths, exact, identity = index._identity(client_id, scope, info_root)
    normalized_sku = normalize_sku(sku) if sku else ""
    key, now = canonical_path_key(paths.internal_dir), time.monotonic()
    with _GUARD:
        job = _JOBS.get(key)
        created = job is None
        if created:
            job = {"client_id": paths.client_id, "info_root": paths.info_root, "scopes": {},
                   "event": threading.Event(), "stop": threading.Event()}
            _JOBS[key] = job
        active = job["scopes"].get(identity)
        if active is None:
            active = {"scope": exact.as_dict(), "visited": now, "changed": now,
                      "revision": 0, "handled": -1, "cache": NoteCache(), "sku": ""}
            job["scopes"][identity] = active
            job["event"].set()
        elif active["scope"] != exact.as_dict():
            active["scope"] = exact.as_dict()
            immediate = True
        active["visited"] = now
        if normalized_sku:
            active["sku"] = normalized_sku
        if immediate:
            active.update(revision=active["revision"] + 1, changed=now)
            job["event"].set()
        if created:
            job["thread"] = threading.Thread(target=_run, args=(key, job), name="training-read-index", daemon=True)
            try:
                job["thread"].start()
            except BaseException:
                job["stop"].set()
                job["event"].set()
                if _JOBS.get(key) is job:
                    _JOBS.pop(key, None)
                raise


def invalidate(client_id, scope, *, info_root=None, sku=""):
    request_refresh(client_id, scope, info_root=info_root, immediate=True, sku=sku)


def stop_workers(*, timeout=2.0):
    with _GUARD:
        jobs = list(_JOBS.values())
        for job in jobs:
            job["stop"].set()
            job["event"].set()
    deadline = time.monotonic() + timeout
    for job in jobs:
        job["thread"].join(timeout=max(0.0, deadline - time.monotonic()))
