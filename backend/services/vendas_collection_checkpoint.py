"""Durable, store/day-scoped preparation; no source response is persisted.

Incremental jobs share a pending generation. Explicit refresh jobs have their own
generation. A validated discovery manifest attests enumeration, never a business
commit or a completed day; the caller owns those two acknowledgements.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.services.sqlite_coordination import sqlite_lock_for_path


_VERSION = 1
_MANIFEST_VERSION = 1
_KINDS = {"pedido", "nfe_entrada", "nfe_saida"}
try:
    _DAY_TIMEZONE = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    _DAY_TIMEZONE = timezone(timedelta(hours=-3))

_MANIFEST_FIELDS = {
    "pedido": {"id", "data"},
    "nfe_saida": {
        "id", "numero", "data_emissao", "comprador", "numero_pedido_loja",
        "origem_codigo", "loja_id", "loja_descricao", "unidade_id",
    },
    "nfe_entrada": {
        "id", "numero", "data_emissao", "valor", "fornecedor", "natureza_id",
        "natureza_descricao", "finalidade_descricao", "origem_codigo", "loja_id",
        "loja_descricao", "unidade_id", "unidade_descricao",
        "intermediador_nome", "intermediador_cnpj",
    },
}
_FIELDS = {
    "sales": {
        "data", "loja_conta", "canal", "id_pedido", "numero", "situacao",
        "devolucao", "sku", "produto", "quantidade", "valor", "numero_nf",
        "comprador", "unidade_negocio", "nota_fiscal_id", "loja_id", "unidade_id",
        "intermediador_nome", "intermediador_cnpj", "origem_tipo", "origem_id", "item_id",
    },
    "notes": {
        "id", "numero", "data_emissao", "natureza_operacao", "finalidade_operacao",
        "devolucao", "valor", "fornecedor", "origem_codigo", "unidade_negocio",
        "unidade_negocio_virtual", "_detalhe_validado",
        "_return_reason_order_id", "_return_reason_bling_order_id",
        "_return_reason_original_invoice_id", "_return_reason_original_invoice_number",
        "_return_reason_bling_order_number", "_return_reason_reference_conflict",
    },
    "items": {
        "id_nota", "numero_nota", "origem_codigo", "data_emissao", "sku", "descricao",
        "quantidade", "valor_unitario", "valor_total", "natureza_operacao",
        "finalidade_operacao", "devolucao", "fornecedor", "unidade_negocio",
        "unidade_negocio_virtual", "item_id",
    },
}


@dataclass(frozen=True)
class _Scope:
    """Internal handle; yielded cycle_id is the durable generation, not job ID.

    started_at is UTC epoch seconds for the earliest discovery still in use.
    It advances when discovery is refreshed, while validated details survive.
    Empty generation_key denotes a lookup/cleanup reference, never a live worker.
    """

    client_id: str
    store_id: str
    cycle_id: str
    day: str
    path: str
    started_at: float = 0.0
    generation_key: str = ""
    connection_hash: str = ""
    on_write: Callable[[], Any] | None = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.client_id, self.store_id, self.cycle_id, self.day


_CURRENT: ContextVar[_Scope | None] = ContextVar("vendas_collection_checkpoint", default=None)


def _scope(client_id, store_id, cycle_id, day, tenant_path=None, *, on_write=None) -> _Scope:
    values = tuple(str(value or "").strip() for value in (client_id, store_id, cycle_id, day))
    if not all(values):
        raise ValueError("Checkpoint de coleta exige cliente, loja, ciclo e dia.")
    if date.fromisoformat(values[3]).isoformat() != values[3]:
        raise ValueError("Dia invalido para checkpoint de coleta.")
    if tenant_path is None:
        # Lazy dependency: bling_vendas also runs without loading the Vendas module.
        from backend.modules.vendas.dependencies import get_tenant_path

        tenant_path = get_tenant_path(values[0])
    return _Scope(*values, os.path.join(os.fspath(tenant_path), "vendas_collection_checkpoint.db"), on_write=on_write)


def _connect(scope: _Scope) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(scope.path), exist_ok=True)
    conn = sqlite3.connect(scope.path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS collected_documents (
                client_id TEXT NOT NULL, store_id TEXT NOT NULL, cycle_id TEXT NOT NULL,
                day TEXT NOT NULL, kind TEXT NOT NULL, document_id TEXT NOT NULL,
                listing_hash TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
                version INTEGER NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (client_id, store_id, cycle_id, day, kind, document_id)
            )"""
        )
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS collection_generations (
                client_id TEXT NOT NULL, store_id TEXT NOT NULL, day TEXT NOT NULL,
                generation_key TEXT NOT NULL, cycle_id TEXT NOT NULL,
                connection_hash TEXT NOT NULL, started_at REAL NOT NULL,
                last_job TEXT NOT NULL,
                PRIMARY KEY(client_id, store_id, day, generation_key)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS collection_generation_cycle
                ON collection_generations(client_id, store_id, cycle_id, day);
            CREATE TABLE IF NOT EXISTS collection_job_bindings (
                client_id TEXT NOT NULL, store_id TEXT NOT NULL, job_id TEXT NOT NULL,
                day TEXT NOT NULL, cycle_id TEXT NOT NULL,
                PRIMARY KEY(client_id, store_id, job_id, day)
            );
            CREATE TABLE IF NOT EXISTS collection_manifests (
                client_id TEXT NOT NULL, store_id TEXT NOT NULL, cycle_id TEXT NOT NULL,
                day TEXT NOT NULL, kind TEXT NOT NULL, entries TEXT NOT NULL,
                entries_hash TEXT NOT NULL, version INTEGER NOT NULL,
                started_at REAL NOT NULL, completed_at REAL NOT NULL,
                PRIMARY KEY(client_id, store_id, cycle_id, day, kind)
            );
            CREATE TABLE IF NOT EXISTS collection_store_connections (
                client_id TEXT NOT NULL, store_id TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                PRIMARY KEY(client_id, store_id)
            );
        """)
    except BaseException:
        conn.close()
        raise
    return conn


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_payload(payload: Any, kind: str, document_id: str, day: str) -> bool:
    if not isinstance(payload, dict) or set(payload) != set(_FIELDS):
        return False
    for group, allowed in _FIELDS.items():
        rows = payload[group]
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict) or not set(row).issubset(allowed):
                return False
            if any(isinstance(value, (dict, list)) for value in row.values()):
                return False
            for key, value in row.items():
                if key == "_return_reason_reference_conflict":
                    if value is not True:
                        return False
                elif key.startswith("_return_reason_"):
                    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,31}", value):
                        return False
            if any(isinstance(value, float) and not math.isfinite(value) for value in row.values()):
                return False
            if str(row.get("data" if group == "sales" else "data_emissao") or "")[:10] != day:
                return False
    if kind == "nfe_entrada":
        if payload["sales"] or len(payload["notes"]) != 1 or not payload["items"]:
            return False
        if str(payload["notes"][0].get("id")) != document_id:
            return False
        if payload["notes"][0].get("_detalhe_validado") is not True:
            return False
        return len({str(item.get("item_id")) for item in payload["items"]}) == len(payload["items"]) and all(
            str(item.get("id_nota")) == document_id
            and bool(str(item.get("item_id") or ""))
            and isinstance(item.get("quantidade"), (int, float))
            and isinstance(item.get("valor_total"), (int, float))
            and not isinstance(item.get("quantidade"), bool)
            and not isinstance(item.get("valor_total"), bool)
            for item in payload["items"]
        )
    if kind not in {"pedido", "nfe_saida"} or payload["notes"] or payload["items"] or not payload["sales"]:
        return False
    return len({str(item.get("item_id")) for item in payload["sales"]}) == len(payload["sales"]) and all(
        item.get("origem_tipo") == kind and str(item.get("origem_id")) == document_id
        and bool(str(item.get("item_id") or ""))
        and isinstance(item.get("quantidade"), (int, float))
        and isinstance(item.get("valor"), (int, float))
        and not isinstance(item.get("quantidade"), bool)
        and not isinstance(item.get("valor"), bool)
        for item in payload["sales"]
    )


def _now() -> float:
    return time.time()


def _callback(scope: _Scope) -> None:
    if scope.on_write is not None:
        scope.on_write()


def _validate_generation(conn, scope: _Scope) -> None:
    if not scope.generation_key:
        return
    row = conn.execute(
        "SELECT cycle_id, connection_hash, started_at FROM collection_generations "
        "WHERE client_id=? AND store_id=? AND day=? AND generation_key=?",
        (scope.client_id, scope.store_id, scope.day, scope.generation_key),
    ).fetchone()
    if row != (scope.cycle_id, scope.connection_hash, scope.started_at):
        raise RuntimeError("A preparacao desta coleta foi substituida; retome a sincronizacao.")


def current_collection_checkpoint() -> _Scope | None:
    return _CURRENT.get()


def validate_collection_context() -> None:
    """Validate the worker lease/connection and reject an obsolete generation."""
    scope = _CURRENT.get()
    if scope is None:
        return
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            _validate_generation(conn, scope)
        finally:
            conn.close()


def _delete_store(conn, client_id, store_id) -> None:
    for table in ("collected_documents", "collection_manifests", "collection_generations",
                  "collection_job_bindings", "collection_store_connections"):
        conn.execute(f"DELETE FROM {table} WHERE client_id=? AND store_id=?", (client_id, store_id))


def _delete_generation(conn, scope: _Scope) -> None:
    for table in ("collected_documents", "collection_manifests", "collection_generations",
                  "collection_job_bindings"):
        conn.execute(
            f"DELETE FROM {table} WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?", scope.key,
        )


def _resolved_scope(conn, scope: _Scope) -> _Scope:
    binding = conn.execute(
        "SELECT cycle_id FROM collection_job_bindings WHERE client_id=? AND store_id=? AND job_id=? AND day=?",
        scope.key,
    ).fetchone()
    if binding:
        return _Scope(scope.client_id, scope.store_id, binding[0], scope.day, scope.path,
                      on_write=scope.on_write)
    return scope


@contextmanager
def collection_checkpoint(client_id, store_id, cycle_id, day, *, tenant_path=None,
                          force=False, refresh_discovery=False, connection_identity="", on_write=None):
    """Bind a resumable generation, shared across ordinary jobs of one store/day.

    A discovery refresh preserves validated documents. Retrying the same open-day
    job preserves its manifest; a new job or the local day boundary refreshes it.
    """
    requested = _scope(client_id, store_id, cycle_id, day, tenant_path, on_write=on_write)
    _callback(requested)  # Must precede schema migration and directory creation.
    generation_key = "force:" + _hash(requested.cycle_id) if force else "incremental"
    identity_hash = _hash(str(connection_identity)) if connection_identity else ""
    with sqlite_lock_for_path(requested.path):
        conn = _connect(requested)
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_identity = conn.execute(
                "SELECT identity_hash FROM collection_store_connections WHERE client_id=? AND store_id=?",
                (requested.client_id, requested.store_id),
            ).fetchone()
            if existing_identity and identity_hash and existing_identity[0] != identity_hash:
                _delete_store(conn, requested.client_id, requested.store_id)
                existing_identity = None
            identity_hash = identity_hash or (existing_identity[0] if existing_identity else "")
            conn.execute("INSERT OR REPLACE INTO collection_store_connections VALUES (?,?,?)",
                         (requested.client_id, requested.store_id, identity_hash))
            row = conn.execute(
                "SELECT cycle_id, started_at, last_job FROM collection_generations "
                "WHERE client_id=? AND store_id=? AND day=? AND generation_key=?",
                (requested.client_id, requested.store_id, requested.day, generation_key),
            ).fetchone()
            now = _now()
            if force:
                # A normal job may have prepared this day while this forced job
                # was paused/retrying. Its stale payload must not overwrite the
                # resumed forced refresh, especially for today's provisional data.
                incremental = conn.execute(
                    "SELECT cycle_id FROM collection_generations "
                    "WHERE client_id=? AND store_id=? AND day=? AND generation_key='incremental'",
                    (requested.client_id, requested.store_id, requested.day),
                ).fetchone()
                if incremental:
                    _delete_generation(conn, _Scope(requested.client_id, requested.store_id,
                                                   incremental[0], requested.day, requested.path))
            if row is None:
                effective_cycle, started_at = uuid.uuid4().hex, now
                conn.execute("INSERT INTO collection_generations VALUES (?,?,?,?,?,?,?,?)",
                             (requested.client_id, requested.store_id, requested.day, generation_key,
                              effective_cycle, identity_hash, started_at, requested.cycle_id))
                # Old staging proves a document, never a complete enumeration.
                # Only the exact legacy job can migrate it; other generations
                # (especially explicit refreshes) are not adopted by inference.
                legacy_rows = conn.execute(
                    "SELECT kind,document_id,listing_hash,payload,payload_hash,version,updated_at "
                    "FROM collected_documents WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?",
                    requested.key,
                ).fetchall()
                for legacy in legacy_rows:
                    if _decode_document(legacy[2:6], legacy[0], legacy[1], requested.day) is not None:
                        conn.execute("INSERT OR IGNORE INTO collected_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                     (requested.client_id, requested.store_id, effective_cycle, requested.day, *legacy))
                conn.execute("DELETE FROM collected_documents WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?",
                             requested.key)
            else:
                effective_cycle, started_at, previous_job = row
                local_today = datetime.fromtimestamp(now, _DAY_TIMEZONE).date().isoformat()
                discovery_day = datetime.fromtimestamp(started_at, _DAY_TIMEZONE).date().isoformat()
                crossed_day = requested.day < local_today and discovery_day <= requested.day
                if crossed_day or (refresh_discovery and previous_job != requested.cycle_id):
                    started_at = now
                    conn.execute(
                        "DELETE FROM collection_manifests WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?",
                        (requested.client_id, requested.store_id, effective_cycle, requested.day),
                    )
                conn.execute(
                    "UPDATE collection_generations SET started_at=?, last_job=? "
                    "WHERE client_id=? AND store_id=? AND day=? AND generation_key=?",
                    (started_at, requested.cycle_id, requested.client_id, requested.store_id,
                     requested.day, generation_key),
                )
            conn.execute("INSERT OR REPLACE INTO collection_job_bindings VALUES (?,?,?,?,?)",
                         (requested.client_id, requested.store_id, requested.cycle_id, requested.day, effective_cycle))
            _callback(requested)
            conn.commit()
            scope = _Scope(requested.client_id, requested.store_id, effective_cycle, requested.day,
                           requested.path, started_at, generation_key, identity_hash, on_write)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    token = _CURRENT.set(scope)
    try:
        yield scope
    finally:
        _CURRENT.reset(token)


def collection_listing_hash(listing: dict) -> str:
    if not isinstance(listing, dict):
        raise ValueError("Resumo de documento invalido.")
    return _hash(_json(listing))


def _listing_digest(listing, listing_hash) -> str:
    if listing_hash is None:
        return collection_listing_hash(listing)
    if not isinstance(listing_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", listing_hash):
        raise ValueError("Hash de resumo invalido.")
    return listing_hash


def _decode_document(row, kind, document_id, day, expected_hash=None):
    if row is None:
        return None
    try:
        payload = json.loads(row[1])
        if (row[3] == _VERSION and isinstance(row[0], str) and re.fullmatch(r"[a-f0-9]{64}", row[0])
                and row[2] == _hash(row[1])
                and (expected_hash is None or row[0] == expected_hash)
                and _valid_payload(payload, kind, document_id, day)):
            return payload
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def load_collected_document(kind: str, document_id: Any, listing: dict | None = None,
                            *, listing_hash: str | None = None) -> dict | None:
    scope = _CURRENT.get()
    if scope is None:
        return None
    document_id = str(document_id)
    expected_hash = _listing_digest(listing, listing_hash)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            _validate_generation(conn, scope)
            row = conn.execute(
                "SELECT listing_hash,payload,payload_hash,version FROM collected_documents "
                "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=? AND document_id=?",
                (*scope.key, kind, document_id),
            ).fetchone()
            # Invalid or stale preparation is a cache miss. Keep reads read-only;
            # the next successfully validated detail atomically replaces it.
            return _decode_document(row, kind, document_id, scope.day, expected_hash)
        finally:
            conn.close()


def save_collected_document(kind: str, document_id: Any, listing: dict | None = None,
                            payload: dict | None = None, *, listing_hash: str | None = None) -> None:
    scope = _CURRENT.get()
    if scope is None:
        return
    document_id = str(document_id)
    if kind not in _KINDS or not _valid_payload(payload, kind, document_id, scope.day):
        raise ValueError("Documento incompleto ou fora do dia no checkpoint de coleta.")
    digest = _listing_digest(listing, listing_hash)
    serialized = _json(payload)
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _validate_generation(conn, scope)
                conn.execute(
                    "INSERT OR REPLACE INTO collected_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (*scope.key, kind, document_id, digest, serialized, _hash(serialized),
                     _VERSION, datetime.fromtimestamp(_now(), timezone.utc).isoformat()),
                )
                _callback(scope)
        finally:
            conn.close()


def save_collected_invoice_number(invoice_id: Any, number: Any) -> None:
    """Retain successful optional enrichment without changing source revisions."""
    scope = _CURRENT.get()
    if scope is None:
        return
    invoice_id, number = str(invoice_id or "").strip(), str(number or "").strip()
    if not invoice_id or not number:
        return
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _validate_generation(conn, scope)
                rows = conn.execute(
                    "SELECT kind,document_id,listing_hash,payload,payload_hash,version FROM collected_documents "
                    "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind IN ('pedido','nfe_saida')",
                    scope.key,
                ).fetchall()
                for row in rows:
                    payload = _decode_document(row[2:], row[0], row[1], scope.day)
                    if payload is None:
                        continue
                    changed = False
                    for item in payload["sales"]:
                        if str(item.get("nota_fiscal_id") or "").strip() == invoice_id and not str(item.get("numero_nf") or "").strip():
                            item["numero_nf"] = number
                            changed = True
                    if changed:
                        serialized = _json(payload)
                        conn.execute(
                            "UPDATE collected_documents SET payload=?, payload_hash=? "
                            "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=? AND document_id=?",
                            (serialized, _hash(serialized), *scope.key, row[0], row[1]),
                        )
                _callback(scope)
        finally:
            conn.close()


def _valid_entries(kind: str, entries) -> bool:
    if kind not in _KINDS or not isinstance(entries, list):
        return False
    identities = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"document_id", "listing_hash", "listing"}:
            return False
        identity, digest, listing = entry["document_id"], entry["listing_hash"], entry["listing"]
        if (not isinstance(identity, str) or not identity.strip() or len(identity) > 128
                or identity in identities or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            return False
        if (not isinstance(listing, dict) or not set(listing).issubset(_MANIFEST_FIELDS[kind])
                or listing.get("id") != identity):
            return False
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float)))
               for value in listing.values()):
            return False
        if any(isinstance(value, float) and not math.isfinite(value) for value in listing.values()):
            return False
        identities.add(identity)
    return True


def _manifest_hash(serialized, version, started_at, completed_at):
    return _hash(_json([serialized, version, float(started_at), float(completed_at)]))


def _decode_manifest(row, kind):
    if row is None:
        return None
    try:
        entries = json.loads(row[0])
        if (row[2] == _MANIFEST_VERSION and row[1] == _manifest_hash(row[0], row[2], row[3], row[4])
                and _valid_entries(kind, entries)
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in row[3:5])
                and 0 < row[3] <= row[4]):
            return {"entries": entries, "started_at": row[3], "completed_at": row[4]}
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def load_collection_manifest(kind: str) -> dict | None:
    scope = _CURRENT.get()
    if scope is None:
        return None
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            _validate_generation(conn, scope)
            row = conn.execute(
                "SELECT entries,entries_hash,version,started_at,completed_at FROM collection_manifests "
                "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=?",
                (*scope.key, kind),
            ).fetchone()
            manifest = _decode_manifest(row, kind)
            if manifest and manifest["started_at"] < scope.started_at:
                return None
            return manifest
        finally:
            conn.close()


def save_collection_manifest(kind: str, entries: list[dict], *, started_at: float | None = None) -> None:
    """Publish only after enumeration ends normally, including its empty last page."""
    scope = _CURRENT.get()
    if scope is None:
        return
    if not _valid_entries(kind, entries):
        raise ValueError("Manifesto incompleto ou com campos nao permitidos.")
    started_at = scope.started_at if started_at is None else started_at
    completed_at = _now()
    if (isinstance(started_at, bool) or not isinstance(started_at, (int, float))
            or not math.isfinite(started_at) or not 0 < scope.started_at <= started_at <= completed_at):
        raise ValueError("Inicio da enumeracao invalido.")
    serialized = _json(entries)
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _validate_generation(conn, scope)
                conn.execute("INSERT OR REPLACE INTO collection_manifests VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (*scope.key, kind, serialized,
                              _manifest_hash(serialized, _MANIFEST_VERSION, started_at, completed_at), _MANIFEST_VERSION,
                              started_at, completed_at))
                _callback(scope)
        finally:
            conn.close()


def load_prepared_source(kind: str) -> dict | None:
    scope = _CURRENT.get()
    if scope is None:
        return None
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            conn.execute("BEGIN")
            _validate_generation(conn, scope)
            row = conn.execute(
                "SELECT entries,entries_hash,version,started_at,completed_at FROM collection_manifests "
                "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=?",
                (*scope.key, kind),
            ).fetchone()
            manifest = _decode_manifest(row, kind)
            if manifest is None or manifest["started_at"] < scope.started_at:
                return None
            rows = conn.execute(
                "SELECT document_id,listing_hash,payload,payload_hash,version FROM collected_documents "
                "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=?", (*scope.key, kind),
            ).fetchall()
            documents = {row[0]: row[1:] for row in rows}
            result = {group: [] for group in _FIELDS}
            for entry in manifest["entries"]:
                payload = _decode_document(documents.get(entry["document_id"]), kind,
                                           entry["document_id"], scope.day, entry["listing_hash"])
                if payload is None:
                    return None
                for group in result:
                    result[group].extend(payload[group])
            return result
        finally:
            conn.close()


def collected_source_complete(kind: str) -> bool:
    return load_prepared_source(kind) is not None


def finish_collection_day(client_id, store_id, cycle_id, day, *, tenant_path=None, on_write=None) -> None:
    """Only after commercial commit and permanent closed-day acknowledgement."""
    scope = _scope(client_id, store_id, cycle_id, day, tenant_path, on_write=on_write)
    if not os.path.exists(scope.path):
        return
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _delete_generation(conn, _resolved_scope(conn, scope))
                _callback(scope)
        finally:
            conn.close()


def purge_store_collection(client_id, store_id, *, tenant_path=None, on_write=None) -> None:
    scope = _scope(client_id, store_id, "purge", "1970-01-01", tenant_path, on_write=on_write)
    if not os.path.exists(scope.path):
        return
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                _delete_store(conn, scope.client_id, scope.store_id)
                _callback(scope)
        finally:
            conn.close()


def purge_client_collection(client_id, *, tenant_path=None, on_write=None) -> None:
    """Clear this client's preparation even when no durable queue remains."""
    scope = _scope(client_id, "purge-client", "purge", "1970-01-01", tenant_path, on_write=on_write)
    if not os.path.exists(scope.path):
        return
    _callback(scope)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for table in ("collected_documents", "collection_manifests", "collection_generations",
                              "collection_job_bindings", "collection_store_connections"):
                    conn.execute(f"DELETE FROM {table} WHERE client_id=?", (scope.client_id,))
                _callback(scope)
        finally:
            conn.close()


def collection_day_summary(client_id, store_id, cycle_id, day, *, tenant_path=None) -> dict:
    scope = _scope(client_id, store_id, cycle_id, day, tenant_path)
    result = {"documentos_salvos": 0, "ultimo_tipo": None, "ultimo_id": None, "atualizado_em": None,
              "fontes_completas": [], "documentos_pendentes": 0}
    if not os.path.exists(scope.path):
        return result
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            scope = _resolved_scope(conn, scope)
            where = "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?"
            documents = {}
            for row in conn.execute(
                f"SELECT kind,document_id,listing_hash,payload,payload_hash,version,updated_at "
                f"FROM collected_documents {where} ORDER BY updated_at DESC", scope.key,
            ):
                if _decode_document(row[2:6], row[0], row[1], scope.day) is None:
                    continue
                documents[(row[0], row[1])] = row[2]
                if result["ultimo_id"] is None:
                    result.update(ultimo_tipo=row[0], ultimo_id=row[1], atualizado_em=row[6])
            result["documentos_salvos"] = len(documents)
            for row in conn.execute(
                f"SELECT kind,entries,entries_hash,version,started_at,completed_at "
                f"FROM collection_manifests {where} ORDER BY kind", scope.key,
            ):
                manifest = _decode_manifest(row[1:], row[0])
                if manifest is None:
                    continue
                pending = sum(documents.get((row[0], entry["document_id"])) != entry["listing_hash"]
                              for entry in manifest["entries"])
                result["documentos_pendentes"] += pending
                if not pending:
                    result["fontes_completas"].append(row[0])
            return result
        finally:
            conn.close()
