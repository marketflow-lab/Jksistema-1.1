"""Durable preparation of validated Bling documents, scoped to one sync cycle.

Only normalized commercial rows are stored here. HTTP responses, credentials and
request arguments never belong in this database. A cached document is not a
completed day: the caller still lists every page and commits the commercial DB.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from backend.services.sqlite_coordination import sqlite_lock_for_path


_VERSION = 1
_KINDS = {"pedido", "nfe_entrada", "nfe_saida"}
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
    client_id: str
    store_id: str
    cycle_id: str
    day: str
    path: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.client_id, self.store_id, self.cycle_id, self.day


_CURRENT: ContextVar[_Scope | None] = ContextVar("vendas_collection_checkpoint", default=None)


def _scope(client_id, store_id, cycle_id, day, tenant_path=None) -> _Scope:
    values = tuple(str(value or "").strip() for value in (client_id, store_id, cycle_id, day))
    if not all(values):
        raise ValueError("Checkpoint de coleta exige cliente, loja, ciclo e dia.")
    if date.fromisoformat(values[3]).isoformat() != values[3]:
        raise ValueError("Dia invalido para checkpoint de coleta.")
    if tenant_path is None:
        # Lazy dependency: bling_vendas also runs without loading the Vendas module.
        from backend.modules.vendas.dependencies import get_tenant_path

        tenant_path = get_tenant_path(values[0])
    return _Scope(*values, os.path.join(os.fspath(tenant_path), "vendas_collection_checkpoint.db"))


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
        return all(
            str(item.get("id_nota")) == document_id
            and bool(str(item.get("item_id") or ""))
            and isinstance(item.get("quantidade"), (int, float))
            and isinstance(item.get("valor_total"), (int, float))
            for item in payload["items"]
        )
    if kind not in {"pedido", "nfe_saida"} or payload["notes"] or payload["items"] or not payload["sales"]:
        return False
    return all(
        item.get("origem_tipo") == kind and str(item.get("origem_id")) == document_id
        and bool(str(item.get("item_id") or ""))
        and isinstance(item.get("quantidade"), (int, float))
        and isinstance(item.get("valor"), (int, float))
        for item in payload["sales"]
    )


@contextmanager
def collection_checkpoint(client_id, store_id, cycle_id, day, *, tenant_path=None):
    """Enable preparation for this execution context; signatures stay unchanged."""
    scope = _scope(client_id, store_id, cycle_id, day, tenant_path)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            conn.commit()
        finally:
            conn.close()
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def load_collected_document(kind: str, document_id: Any, listing: dict) -> dict | None:
    scope = _CURRENT.get()
    if scope is None:
        return None
    document_id = str(document_id)
    key = (*scope.key, kind, document_id)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            row = conn.execute(
                "SELECT listing_hash, payload, payload_hash, version FROM collected_documents "
                "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=? AND kind=? AND document_id=?", key,
            ).fetchone()
            if row is None:
                return None
            try:
                payload = json.loads(row[1])
                valid = (
                    row[0] == _hash(_json(listing)) and row[2] == _hash(row[1]) and row[3] == _VERSION
                    and _valid_payload(payload, kind, document_id, scope.day)
                )
            except (ValueError, TypeError):
                valid = False
            if valid:
                return payload
            conn.execute(
                "DELETE FROM collected_documents WHERE client_id=? AND store_id=? AND cycle_id=? "
                "AND day=? AND kind=? AND document_id=?", key,
            )
            conn.commit()
            return None
        finally:
            conn.close()


def save_collected_document(kind: str, document_id: Any, listing: dict, payload: dict) -> None:
    scope = _CURRENT.get()
    if scope is None:
        return
    document_id = str(document_id)
    if kind not in _KINDS or not _valid_payload(payload, kind, document_id, scope.day):
        raise ValueError("Documento incompleto ou fora do dia no checkpoint de coleta.")
    serialized = _json(payload)
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO collected_documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (*scope.key, kind, document_id, _hash(_json(listing)), serialized, _hash(serialized),
                     _VERSION, datetime.now(timezone.utc).isoformat()),
                )
        finally:
            conn.close()


def finish_collection_day(client_id, store_id, cycle_id, day, *, tenant_path=None) -> None:
    """Call only after commercial commit AND the durable day-completion checkpoint."""
    scope = _scope(client_id, store_id, cycle_id, day, tenant_path)
    if not os.path.exists(scope.path):
        return
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            with conn:
                conn.execute(
                    "DELETE FROM collected_documents WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?",
                    scope.key,
                )
        finally:
            conn.close()


def collection_day_summary(client_id, store_id, cycle_id, day, *, tenant_path=None) -> dict:
    scope = _scope(client_id, store_id, cycle_id, day, tenant_path)
    result = {"documentos_salvos": 0, "ultimo_tipo": None, "ultimo_id": None, "atualizado_em": None}
    if not os.path.exists(scope.path):
        return result
    with sqlite_lock_for_path(scope.path):
        conn = _connect(scope)
        try:
            where = "WHERE client_id=? AND store_id=? AND cycle_id=? AND day=?"
            result["documentos_salvos"] = conn.execute(
                f"SELECT count(*) FROM collected_documents {where}", scope.key,
            ).fetchone()[0]
            row = conn.execute(
                f"SELECT kind, document_id, updated_at FROM collected_documents {where} ORDER BY updated_at DESC LIMIT 1",
                scope.key,
            ).fetchone()
            if row:
                result.update(ultimo_tipo=row[0], ultimo_id=row[1], atualizado_em=row[2])
            return result
        finally:
            conn.close()
