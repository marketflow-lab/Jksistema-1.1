"""Durable store catalog outbox; operational CSVs remain authoritative.

Only committed store snapshots are projected. Reconciliation closes the crash
window between the operational commit and the best-effort outbox notification.
No absent row is interpreted as a deletion: only explicit tombstones retire it.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.locking import _exclusive_file_lock
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import _runtime_config
from backend.modules.context_hub.store_sku_contracts import StoreSkuScope

DEBOUNCE_SECONDS = 5.0
RECONCILE_SECONDS = 15 * 60
_guard = threading.RLock()
_workers: dict[str, tuple[threading.Thread, threading.Event, threading.Event]] = {}


def _stores(paths):
    target = paths.tenant_dir / "lojas_config.json"
    _assert_path_chain_safe(target, paths.info_root)
    # Read the local canonical snapshot; never invoke migration, credential
    # recovery or central-account networking while publishing a projection.
    value = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ContextHubValidationError("store_config_invalid")
    return value


def resolve_catalog_scope(client_id, store_id, *, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    matches = [item for item in _stores(paths) if item.get("store_id") == store_id]
    if len(matches) != 1:
        raise ContextHubValidationError("store_scope_unresolved")
    store = matches[0]
    ml = (store.get("integracoes") or {}).get("mercadolivre") or {}
    scope = StoreSkuScope.from_mapping({
        "tenant_scope": f"tenant:{paths.client_id}", "store_ref": store_id,
        "store_name": store.get("nome", ""),
        "seller_id": str(ml.get("user_id") or ml.get("seller_id") or ""),
        "site_id": str(ml.get("site_id") or "").upper(),
    })
    if scope.site_id != "MLB":
        raise ContextHubValidationError("store_marketplace_identity_incomplete")
    return scope.as_dict()


def _key(scope):
    identity = [scope[k] for k in ("tenant_scope", "store_ref", "seller_id", "site_id")]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


@contextmanager
def _db(paths, *, readonly=False):
    target = paths.internal_dir / "catalog-sync.db"
    _assert_path_chain_safe(target, paths.info_root)
    if readonly:
        con = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)
    else:
        paths.internal_dir.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(target, timeout=10)
        con.execute("""CREATE TABLE IF NOT EXISTS catalog_outbox (
            scope_key TEXT PRIMARY KEY, store_id TEXT NOT NULL,
            requested_revision INTEGER NOT NULL DEFAULT 0,
            completed_revision INTEGER NOT NULL DEFAULT 0,
            not_before REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            last_error_code TEXT NOT NULL DEFAULT '', report TEXT NOT NULL DEFAULT '{}')""")
        con.commit()
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def get_catalog_sync_status(client_id, store_id, *, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    default = {"status": "not_synced", "requested_revision": 0,
               "completed_revision": 0, "last_error_code": "", "pending": 0}
    if not (paths.internal_dir / "catalog-sync.db").exists():
        return default
    scope = resolve_catalog_scope(client_id, store_id, info_root=paths.info_root)
    with _db(paths, readonly=True) as con:
        row = con.execute("SELECT * FROM catalog_outbox WHERE scope_key=?", (_key(scope),)).fetchone()
    if row is None:
        return default
    report = json.loads(row["report"])
    return {**report, **{key: row[key] for key in default if key != "pending"},
            "pending": int(row["requested_revision"] > row["completed_revision"])}


def _enqueue(paths, scope):
    with _db(paths) as con:
        con.execute("""INSERT INTO catalog_outbox(scope_key,store_id,requested_revision,not_before)
            VALUES(?,?,1,?) ON CONFLICT(scope_key) DO UPDATE SET
            requested_revision=requested_revision+1, not_before=excluded.not_before,
            status='pending',last_error_code=''""",
            (_key(scope), scope["store_ref"], time.time() + DEBOUNCE_SECONDS))
        con.commit()


def request_catalog_sync(client_id, store_id, *, sku="", info_root=None):
    # A SKU request deliberately coalesces into the complete store snapshot.
    # This cannot lose other dirty SKUs when imports and manual requests overlap.
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = resolve_catalog_scope(client_id, store_id, info_root=paths.info_root)
    _enqueue(paths, scope)
    _ensure_worker(paths.info_root)
    return get_catalog_sync_status(client_id, store_id, info_root=paths.info_root)


def notify_catalog_committed(client_id, store_id="", *, info_root=None):
    """Best effort after the caller has released all commit/rollback locks."""
    try:
        paths = _tenant_paths(client_id, info_root=info_root)
        ids = [store_id] if store_id else [str(item.get("store_id") or "") for item in _stores(paths)]
        for current in ids:
            try:
                request_catalog_sync(client_id, current, info_root=paths.info_root)
            except Exception:
                # Never turn an already committed save into a reported failure.
                # Startup/periodic reconciliation retries from canonical data.
                continue
    except Exception:
        pass


def load_catalog_source_snapshot(client_id, store_id, *, info_root=None):
    """Same store/legacy resolution as Cadastro, without runtime-global rebinding."""
    from backend.services import cadastro_lojas_produtos as cadastro, integracoes
    from backend.services.path_coordination import path_lock_for, path_locks_for
    paths = _tenant_paths(client_id, info_root=info_root)
    files = ["lojas_config.json", cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO,
             *cadastro._ARQUIVOS_LEGADOS.values()]
    candidates = [paths.tenant_dir / name for name in files]
    for target in candidates:
        _assert_path_chain_safe(target, paths.info_root)
    # Match Cadastro/Shared Sync writer lock order; lexicographic locking alone
    # would take costs before products and deadlock against a product commit.
    with (integracoes._LOJAS_CONFIG_LOCK,
          path_lock_for(paths.tenant_dir / cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO),
          path_lock_for(paths.tenant_dir / "cadastro_custos_lojas.csv"),
          path_locks_for(str(target) for target in candidates)):
        scope = resolve_catalog_scope(client_id, store_id, info_root=paths.info_root)
        stores = _stores(paths)
        store = {"store_id": store_id, "nome": scope["store_name"]}
        context = cadastro._contexto_legado_de_tenant(
            client_id, store, paths.tenant_dir, stores, fotos={})
        rows, _ = cadastro._ler_registros_persistidos_caminho(
            paths.tenant_dir / cadastro.CADASTRO_PRODUTOS_LOJAS_ARQUIVO)
        # Explicit rows override their own store shadows, including tombstones.
        products = dict(context["sombras"])
        deleted = []
        for row in rows:
            if row["store_id"] != store_id:
                continue
            sku = row["sku_normalizado"]
            if str(row.get("deleted_at_utc") or "").strip():
                products.pop(sku, None)
                deleted.append(sku)
                continue
            item = dict(row)
            cadastro._aplicar_compilado(item, context.get("compilado", {}).get(sku, {}))
            item["scope_source"] = "store_file"
            products[sku] = item
        _attach_store_images(paths, store_id, products)
        return {"scope": scope, "products": list(products.values()),
                "deleted_skus": deleted, "ambiguous_count": len(context["nao_mapeados"]),
                "ambiguous_records": [
                    {"sku": str(item.get("sku") or "")[:100],
                     "row": str(item.get("linha") or ""),
                     "record_id": hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest(),
                     "reason": str(item.get("motivo") or "association_unresolved"),
                     "source": str(item.get("fonte") or "")}
                    for item in context["nao_mapeados"]]}


def _attach_store_images(paths, store_id, products):
    from backend.services import cadastro_fotos as photos
    segment = photos._cadastro_store_id_foto_segmento(store_id)
    folder = paths.tenant_dir / "cadastro_fotos" / "lojas" / segment
    _assert_path_chain_safe(folder, paths.info_root)
    local = {}
    if folder.is_dir():
        for target in sorted(folder.iterdir(), key=lambda path: (path.suffix != ".png", path.name)):
            _assert_path_chain_safe(target, paths.info_root)
            if target.is_file() and target.suffix.lower() in photos.CADASTRO_FOTOS_EXTENSOES:
                local.setdefault(target.stem.upper(), target.relative_to(paths.tenant_dir).as_posix())
    for sku, product in products.items():
        for field, value in photos._cadastro_foto_referencias_candidatas(product):
            if not photos._cadastro_foto_referencia_local_cadastro(value):
                continue
            parts = photos._cadastro_foto_partes_loja_referencia_local(str(value), paths.client_id)
            if not parts or len(parts) != 3 or parts[1] != segment or parts[2] in {".", ".."}:
                product[field] = ""
        if sku.upper() in local:
            product["foto"] = local[sku.upper()]


def run_catalog_sync_now(client_id, store_id, *, sku="", info_root=None):
    from backend.modules.context_hub.catalog_product_repository import publish_catalog_snapshot
    paths = _tenant_paths(client_id, info_root=info_root)
    scope = resolve_catalog_scope(client_id, store_id, info_root=paths.info_root)
    key = _key(scope)
    # An OS-backed lock prevents two processes from projecting this store at once.
    lock_paths = replace(paths, lock_path=paths.internal_dir / f"catalog-sync-{key}.lock")
    with _exclusive_file_lock(lock_paths):
        with _db(paths) as con:
            row = con.execute("SELECT * FROM catalog_outbox WHERE scope_key=?", (key,)).fetchone()
        if row is None or row["requested_revision"] == row["completed_revision"]:
            _enqueue(paths, scope)
        with _db(paths) as con:
            revision = con.execute("SELECT requested_revision FROM catalog_outbox WHERE scope_key=?", (key,)).fetchone()[0]
            con.execute("UPDATE catalog_outbox SET status='running' WHERE scope_key=?", (key,))
            con.commit()
        try:
            snapshot = load_catalog_source_snapshot(client_id, store_id, info_root=paths.info_root)
            if _key(snapshot["scope"]) != key:
                raise ContextHubValidationError("store_scope_changed")
            report = publish_catalog_snapshot(client_id, snapshot["scope"], snapshot["products"],
                deleted_skus=snapshot["deleted_skus"], complete=True, info_root=paths.info_root)
            if report.get("status") in {"error", "failed"}:
                raise ContextHubValidationError("catalog_publish_failed")
            report = {**report, "ambiguous_count": snapshot["ambiguous_count"]}
            with _db(paths) as con:
                con.execute("""UPDATE catalog_outbox SET completed_revision=?,
                    status=CASE WHEN requested_revision=? THEN 'completed' ELSE 'pending' END,
                    last_error_code='',report=? WHERE scope_key=?""",
                    (revision, revision, json.dumps(report, ensure_ascii=False), key))
                con.commit()
            return report
        except Exception:
            with _db(paths) as con:
                con.execute("UPDATE catalog_outbox SET status='error',last_error_code='snapshot_unavailable',not_before=? WHERE scope_key=?",
                            (time.time() + 30, key))
                con.commit()
            raise


def _reconcile(root):
    if not root.exists():
        return
    for tenant in tuple(root.iterdir()):
        try:
            paths = _tenant_paths(tenant.name, info_root=root)
            if not tenant.is_dir() or not (tenant / "lojas_config.json").is_file():
                continue
            for store in _stores(paths):
                try:
                    scope = resolve_catalog_scope(paths.client_id, store.get("store_id"), info_root=root)
                    _enqueue(paths, scope)
                except Exception:
                    continue
        except Exception:
            continue


def _drain(root, stop):
    if not root.exists():
        return
    for tenant in tuple(root.iterdir()):
        if stop.is_set():
            return
        try:
            paths = _tenant_paths(tenant.name, info_root=root)
            if not (paths.internal_dir / "catalog-sync.db").is_file():
                continue
            with _db(paths, readonly=True) as con:
                pending = con.execute("SELECT scope_key,store_id FROM catalog_outbox WHERE requested_revision>completed_revision AND not_before<=?", (time.time(),)).fetchall()
            for row in pending:
                if stop.is_set():
                    return
                try:
                    current = resolve_catalog_scope(paths.client_id, row["store_id"], info_root=root)
                    if _key(current) != row["scope_key"]:
                        continue  # Old seller/site outboxes can never publish into a new scope.
                    run_catalog_sync_now(paths.client_id, row["store_id"], info_root=root)
                except Exception:
                    continue
        except Exception:
            continue


def _worker(root, stop, wake):
    next_reconcile = time.monotonic() + RECONCILE_SECONDS
    while not stop.is_set():
        try:
            if time.monotonic() >= next_reconcile:
                _reconcile(root)
                next_reconcile = time.monotonic() + RECONCILE_SECONDS
            _drain(root, stop)
        except Exception:
            pass  # A transient missing root must not permanently kill recovery.
        wake.wait(1.0)
        wake.clear()


def _ensure_worker(root):
    root = Path(root).absolute()
    key = str(root).casefold()
    with _guard:
        current = _workers.get(key)
        if current and current[0].is_alive() and not current[1].is_set():
            current[2].set()
            return
        stop, wake = threading.Event(), threading.Event()
        thread = threading.Thread(target=_worker, args=(root, stop, wake), daemon=True,
                                  name="catalog-product-sync")
        _workers[key] = (thread, stop, wake)
        thread.start()


def start_catalog_sync_workers(*, info_root=None):
    root = Path(_runtime_config(info_root=info_root).info_root)
    _reconcile(root)
    _ensure_worker(root)


def stop_catalog_sync_workers():
    with _guard:
        current = list(_workers.values())
        for thread, stop, wake in current:
            stop.set()
            wake.set()
    for thread, _, _ in current:
        thread.join(timeout=5)
    with _guard:
        for key, value in list(_workers.items()):
            if not value[0].is_alive():
                del _workers[key]
