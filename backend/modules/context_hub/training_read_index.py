"""Private read projection for training UI; never an authority for AI publication."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import uuid

from .contracts import ContextHubValidationError
from .path_safety import _assert_path_chain_safe
from .paths import _tenant_paths
from .runtime import _utc_now
from .store_sku_contracts import canonical_json, normalize_sku
from .store_sku_repository_support import _scope_for_paths


class TrainingIndexUnavailable(RuntimeError):
    def __init__(self, code="training_index_unavailable"):
        super().__init__(code)
        self.code = code


def _identity(client_id, scope, info_root):
    paths = _tenant_paths(client_id, info_root=info_root)
    exact = _scope_for_paths(paths, scope)
    key = tuple(getattr(exact, field) for field in (
        "tenant_scope", "store_ref", "seller_id", "site_id", "surface"))
    return paths, exact, key


def index_path(client_id, *, info_root=None):
    paths = _tenant_paths(client_id, info_root=info_root)
    target = paths.internal_dir / "training_read_index.sqlite"
    for path in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
        _assert_path_chain_safe(path, paths.info_root)
    return target


@contextmanager
def _db(client_id, info_root=None, *, write=False):
    path = index_path(client_id, info_root=info_root)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=2, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS training_scopes (
                tenant TEXT, store TEXT, seller TEXT, site TEXT, surface TEXT,
                generation TEXT NOT NULL, published_at TEXT NOT NULL, status TEXT NOT NULL,
                revision TEXT NOT NULL, editor_json TEXT NOT NULL, checked_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(tenant,store,seller,site,surface));
            CREATE TABLE IF NOT EXISTS training_failures (
                tenant TEXT, store TEXT, seller TEXT, site TEXT, surface TEXT,
                checked_at TEXT NOT NULL, PRIMARY KEY(tenant,store,seller,site,surface));
            CREATE TABLE IF NOT EXISTS training_fichas (
                tenant TEXT, store TEXT, seller TEXT, site TEXT, surface TEXT, sku TEXT,
                generation TEXT NOT NULL, payload_json TEXT NOT NULL,
                PRIMARY KEY(tenant,store,seller,site,surface,sku));
        """)
        if "checked_at" not in {row[1] for row in connection.execute("PRAGMA table_info(training_scopes)")}:
            connection.execute("ALTER TABLE training_scopes ADD COLUMN checked_at TEXT NOT NULL DEFAULT ''")
    else:
        if not path.exists():
            raise TrainingIndexUnavailable("training_index_initializing")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.05, isolation_level=None)
        connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


_WHERE = "tenant=? AND store=? AND seller=? AND site=? AND surface=?"


def _meta(row):
    return {"generation": row["generation"], "published_at": row["published_at"],
            "checked_at": row["checked_at"] or row["published_at"], "status": row["status"], "state": row["status"]}


def _empty_details(sku, revision):
    return {"sku": sku, "canonical_document": {}, "evidence": [], "guidance": {},
            "catalog_document": {}, "catalog_revision": "", "catalog_found": False,
            "synchronization": {"status": "not_synced"}, "source_body": "",
            "characteristics": [], "documents": [], "revision": revision, "source_revision": ""}


def _ficha(exact, sku, editor, details, *, known):
    guidance = (editor.get("sku_guidance") or {}).get(sku) or {}
    revision = str((editor.get("editorial") or {}).get("revision") or "")
    entry = ((editor.get("editorial") or {}).get("skus") or {}).get(sku) or {"status": "missing", "hash": ""}
    product = dict(details or _empty_details(sku, revision))
    product.update(revision=revision, guidance=guidance, source_body=str(entry.get("source_body") or ""))
    editorial_status = str(entry.get("status") or "missing")
    product["editorial_state"] = (editorial_status if editorial_status in {"missing", "invalid", "conflict"}
                                  else "missing" if editorial_status == "deleted" else "ready")
    sync_status = str((product.get("synchronization") or {}).get("status") or "")
    product["catalog_state"] = ("unavailable" if sync_status == "error" or (sync_status == "not_synced"
                                and not (product.get("catalog_found") or product.get("canonical_document"))) else
                                "updating" if sync_status in {"running", "queued", "pending"} else
                                "ready" if product.get("catalog_found") or product.get("canonical_document") else "missing")
    return {"success": True, "sku": sku, "sku_known": bool(known),
            "loja": exact.store_name, "store_id": exact.store_ref,
            "seller_id": exact.seller_id, "site_id": exact.site_id,
            "notas_sku": {sku: str(guidance.get("notas") or "")},
            "caracteristicas_sku": {sku: guidance.get("caracteristicas") or {}},
            "exemplos": {"perguntas_anuncio": [{**row, "sku": sku} for row in guidance.get("exemplos_perguntas", [])
                                               if isinstance(row, dict) and str(row.get("sku") or "") in {"", sku}]},
            "editorial": {"revision": revision, "skus": {sku: entry}},
            "expected_revision": revision, "sku_details": product}


def publish_generation(client_id, scope, editor, products, *, info_root=None, product_status="ready"):
    """Replace one exact scope in a single transaction; readers see all-or-nothing."""
    _, exact, key = _identity(client_id, scope, info_root)
    revision = str((editor.get("editorial") or {}).get("revision") or "")
    if not revision or not isinstance(products, dict):
        raise ContextHubValidationError("training_projection_invalid")
    generation, published_at = uuid.uuid4().hex, _utc_now()
    skus = set(products) | set(editor.get("sku_guidance") or {}) | set((editor.get("editorial") or {}).get("skus") or {})
    payloads = []
    for sku in sorted(skus):
        if normalize_sku(sku) != sku:
            raise ContextHubValidationError("training_projection_sku_invalid")
        payload = _ficha(exact, sku, editor, products.get(sku), known=sku in products)
        payloads.append((*key, sku, generation, canonical_json(payload)))
    with _db(client_id, info_root, write=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM training_failures WHERE " + _WHERE, key)
            connection.execute("DELETE FROM training_fichas WHERE " + _WHERE, key)
            connection.executemany("INSERT INTO training_fichas VALUES (?,?,?,?,?,?,?,?)", payloads)
            connection.execute("INSERT OR REPLACE INTO training_scopes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (*key, generation, published_at, product_status, revision, canonical_json(editor), published_at))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"generation": generation, "published_at": published_at, "status": product_status}


def _read(client_id, scope, *, sku=None, info_root=None):
    _, exact, key = _identity(client_id, scope, info_root)
    try:
        with _db(client_id, info_root) as connection:
            connection.execute("BEGIN")
            columns = "*" if sku is None else "generation,published_at,status,revision,checked_at"
            current = connection.execute("SELECT " + columns + " FROM training_scopes WHERE " + _WHERE, key).fetchone()
            if current is None:
                failed = connection.execute("SELECT 1 FROM training_failures WHERE " + _WHERE, key).fetchone()
                raise TrainingIndexUnavailable("training_index_unavailable" if failed else "training_index_initializing")
            if sku is None:
                payload = json.loads(current["editor_json"])
            else:
                row = connection.execute("SELECT payload_json FROM training_fichas WHERE " + _WHERE
                                         + " AND sku=? AND generation=?", (*key, sku, current["generation"])).fetchone()
                payload = json.loads(row["payload_json"]) if row else _ficha(exact, sku, {
                    "editorial": {"revision": current["revision"]}}, {}, known=False)
            payload["snapshot"] = _meta(current)
            return payload
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise TrainingIndexUnavailable() from None


def read_ficha(client_id, scope, sku, *, info_root=None):
    return _read(client_id, scope, sku=normalize_sku(sku), info_root=info_root)


def read_editor(client_id, scope, *, info_root=None):
    return _read(client_id, scope, info_root=info_root)


def index_status(client_id, scope, *, info_root=None):
    _, _, key = _identity(client_id, scope, info_root)
    try:
        with _db(client_id, info_root) as connection:
            row = connection.execute("SELECT generation,published_at,status,checked_at FROM training_scopes WHERE " + _WHERE, key).fetchone()
        return _meta(row) if row else {"status": "initializing", "generation": "", "published_at": ""}
    except (TrainingIndexUnavailable, sqlite3.Error, OSError):
        return {"status": "initializing", "generation": "", "published_at": ""}


def mark_updating(client_id, scope, *, info_root=None, status="updating"):
    """Worker-only status mutation; never initialize a database on the GET path."""
    _, _, key = _identity(client_id, scope, info_root)
    with _db(client_id, info_root, write=True) as connection:
        connection.execute("UPDATE training_scopes SET status=? WHERE " + _WHERE, (status, *key))


def mark_unavailable(client_id, scope, *, info_root=None):
    _, _, key = _identity(client_id, scope, info_root)
    with _db(client_id, info_root, write=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR REPLACE INTO training_failures VALUES (?,?,?,?,?,?)", (*key, _utc_now()))
        connection.execute("UPDATE training_scopes SET status='degraded' WHERE " + _WHERE, key)
        connection.commit()


def mark_checked(client_id, scope, *, info_root=None, status="ready"):
    _, _, key = _identity(client_id, scope, info_root)
    with _db(client_id, info_root, write=True) as connection:
        connection.execute("UPDATE training_scopes SET checked_at=?,status=? WHERE " + _WHERE, (_utc_now(), status, *key))
