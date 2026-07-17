"""Shared Sync CSV and SQLite add-only merge helpers."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import Depends, Header, HTTPException

from backend.schemas import (
    SharedSyncConfigRequest,
    SharedSyncMachineConfigRequest,
    SharedSyncRunRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserLinkRunRequest,
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_merge_sqlite_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_merge_csv_add_only(target_abs: str, remoto_bytes: bytes, scope: str, rel: str) -> dict:
    remoto_df = _shared_sync_csv_read_bytes(remoto_bytes)
    if remoto_df.empty:
        return {"added": 0, "total": 0}
    if os.path.exists(target_abs):
        with open(target_abs, "rb") as f:
            atual_df = _shared_sync_csv_read_bytes(f.read())
    else:
        atual_df = pd.DataFrame(columns=list(remoto_df.columns))

    colunas = []
    for col in list(atual_df.columns) + list(remoto_df.columns):
        if col not in colunas:
            colunas.append(col)
    for col in colunas:
        if col not in atual_df.columns:
            atual_df[col] = ""
        if col not in remoto_df.columns:
            remoto_df[col] = ""
    atual_df = atual_df[colunas].fillna("")
    remoto_df = remoto_df[colunas].fillna("")

    existentes = {
        _shared_sync_csv_row_key(scope, rel, row, colunas)
        for _idx, row in atual_df.iterrows()
    }
    novas = []
    vistos_lote = set()
    for _idx, row in remoto_df.iterrows():
        chave = _shared_sync_csv_row_key(scope, rel, row, colunas)
        if chave in existentes or chave in vistos_lote:
            continue
        vistos_lote.add(chave)
        novas.append(row.to_dict())

    if not novas and os.path.exists(target_abs):
        return {"added": 0, "total": len(atual_df)}

    merged = pd.concat([atual_df, pd.DataFrame(novas, columns=colunas)], ignore_index=True) if novas else atual_df
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    merged.to_csv(target_abs, index=False, encoding="utf-8-sig")
    return {"added": len(novas), "total": len(merged)}

def _shared_sync_sql_ident(nome: str) -> str:
    return '"' + str(nome or "").replace('"', '""') + '"'

def _shared_sync_sqlite_temp_from_bytes(data: bytes, prefix: str = "shared_sync_") -> str:
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=".db")
    os.close(fd)
    with open(tmp_path, "wb") as f:
        f.write(data or b"")
    return tmp_path

def _shared_sync_sqlite_lock_for_path(path: str):
    key = os.path.abspath(str(path or ""))
    with SHARED_SYNC_SQLITE_FILE_LOCKS_LOCK:
        lock = SHARED_SYNC_SQLITE_FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            SHARED_SYNC_SQLITE_FILE_LOCKS[key] = lock
        return lock

def _shared_sync_sqlite_is_locked(exc: Exception) -> bool:
    texto = str(exc or "").lower()
    return "database is locked" in texto or "database table is locked" in texto or "database is busy" in texto

def _shared_sync_sqlite_configure(conn: sqlite3.Connection, *, writable: bool = False) -> None:
    try:
        conn.execute(f"PRAGMA busy_timeout={SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS}")
    except Exception:
        pass
    if writable:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            if _shared_sync_sqlite_is_locked(exc):
                raise
        except Exception:
            pass
        try:
            conn.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass

def _shared_sync_sqlite_retry_locked(operation: Callable[[], Any], label: str) -> Any:
    delay = 0.35
    last_exc: Optional[Exception] = None
    for tentativa in range(1, SHARED_SYNC_SQLITE_LOCK_RETRIES + 1):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not _shared_sync_sqlite_is_locked(exc):
                raise
            last_exc = exc
            logger.warning(
                "[SHARED-SYNC] Banco SQLite ocupado em %s; tentativa %s/%s.",
                label,
                tentativa,
                SHARED_SYNC_SQLITE_LOCK_RETRIES,
            )
            time.sleep(delay)
            delay = min(delay * 1.8, 4.0)
    raise HTTPException(
        status_code=423,
        detail=f"Banco de vendas em uso no momento ({label}). Tente sincronizar novamente em instantes.",
    ) from last_exc

def _shared_sync_merge_vendas_db_add_only(target_abs: str, remoto_bytes: bytes, rel: str) -> dict:
    remoto_tmp = _shared_sync_sqlite_temp_from_bytes(remoto_bytes, "shared_sync_vendas_remote_")
    try:
        def _merge() -> dict:
            inserted = 0
            src = sqlite3.connect(remoto_tmp, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
            try:
                _shared_sync_sqlite_configure(src)
                src_cur = src.cursor()
                tabela = src_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vendas'").fetchone()
                if not tabela:
                    return {"added": 0, "total": 0}
                src_cols_info = src_cur.execute("PRAGMA table_info(vendas)").fetchall()
                src_cols = [row[1] for row in src_cols_info]
                if not src_cols:
                    return {"added": 0, "total": 0}
                create_row = src_cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='vendas'").fetchone()
                create_sql = create_row[0] if create_row and create_row[0] else ""
                os.makedirs(os.path.dirname(target_abs), exist_ok=True)
                with _shared_sync_sqlite_lock_for_path(target_abs):
                    dst = sqlite3.connect(target_abs, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
                    try:
                        _shared_sync_sqlite_configure(dst, writable=True)
                        dst_cur = dst.cursor()
                        exists = dst_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vendas'").fetchone()
                        if not exists:
                            if create_sql:
                                dst_cur.execute(create_sql)
                            else:
                                col_defs = []
                                for col in src_cols_info:
                                    nome = str(col[1])
                                    tipo = str(col[2] or "TEXT")
                                    pk = " PRIMARY KEY" if int(col[5] or 0) else ""
                                    col_defs.append(f"{_shared_sync_sql_ident(nome)} {tipo}{pk}")
                                dst_cur.execute(f"CREATE TABLE vendas ({', '.join(col_defs)})")
                        dst_cols_info = dst_cur.execute("PRAGMA table_info(vendas)").fetchall()
                        dst_cols = [row[1] for row in dst_cols_info]
                        for col in src_cols_info:
                            nome = str(col[1])
                            if nome not in dst_cols:
                                tipo = str(col[2] or "TEXT")
                                dst_cur.execute(f"ALTER TABLE vendas ADD COLUMN {_shared_sync_sql_ident(nome)} {tipo}")
                                dst_cols.append(nome)

                        comuns = [col for col in src_cols if col in dst_cols]
                        if not comuns:
                            return {"added": 0, "total": 0}
                        existing_ids = set()
                        if "id_unico" in dst_cols:
                            try:
                                existing_ids = {
                                    str(row[0] or "").strip()
                                    for row in dst_cur.execute("SELECT id_unico FROM vendas").fetchall()
                                    if str(row[0] or "").strip()
                                }
                            except Exception:
                                existing_ids = set()

                        select_sql = f"SELECT {', '.join(_shared_sync_sql_ident(col) for col in comuns)} FROM vendas"
                        rows = src_cur.execute(select_sql).fetchall()
                        id_idx = comuns.index("id_unico") if "id_unico" in comuns else -1
                        insert_cols = ", ".join(_shared_sync_sql_ident(col) for col in comuns)
                        placeholders = ", ".join(["?"] * len(comuns))
                        for row in rows:
                            if id_idx >= 0:
                                id_unico = str(row[id_idx] or "").strip()
                                if id_unico and id_unico in existing_ids:
                                    continue
                            try:
                                dst_cur.execute(f"INSERT OR IGNORE INTO vendas ({insert_cols}) VALUES ({placeholders})", row)
                                if dst_cur.rowcount > 0:
                                    inserted += 1
                                    if id_idx >= 0 and str(row[id_idx] or "").strip():
                                        existing_ids.add(str(row[id_idx] or "").strip())
                            except sqlite3.IntegrityError:
                                continue
                        dst.commit()
                        total = 0
                        try:
                            total = int(dst_cur.execute("SELECT COUNT(*) FROM vendas").fetchone()[0] or 0)
                        except Exception:
                            pass
                        return {"added": inserted, "total": total}
                    finally:
                        dst.close()
            finally:
                src.close()

        return _shared_sync_sqlite_retry_locked(_merge, rel or os.path.basename(target_abs))
    finally:
        try:
            os.remove(remoto_tmp)
        except Exception:
            pass

def _shared_sync_write_missing_file(target_abs: str, data: bytes) -> dict:
    if os.path.exists(target_abs):
        return {"added": 0, "skipped_existing": True}
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    with open(target_abs, "wb") as f:
        f.write(data or b"")
    return {"added": 1, "skipped_existing": False}

def _shared_sync_aplicar_user_share_add_only(
    client_id: str,
    scope: str,
    username: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> dict:
    if not fontes:
        return {"file_count": 0, "files": [], "added": 0}
    if bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped")):
        return _shared_sync_aplicar_user_scoped_share(client_id, scope, username, fontes, tenant_abs, backup_dir)

    escritos = []
    added = 0
    details = []
    for rel, data in fontes:
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
        lower = rel.lower()
        if scope == "cadastro" and lower.endswith(".csv"):
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            info = _shared_sync_merge_csv_add_only(target_abs, data, scope, rel)
        elif scope == "vendas" and lower.endswith((".db", ".sqlite")):
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            info = _shared_sync_merge_vendas_db_add_only(target_abs, data, rel)
        elif scope == "lojas_integracoes" and lower == "lojas_config.json":
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            merged = _shared_sync_merge_lojas_integracoes_bytes(target_abs, data, add_only=True)
            os.makedirs(os.path.dirname(target_abs), exist_ok=True)
            with open(target_abs, "wb") as f:
                f.write(merged)
            info = {"added": 1, "merged": True}
        elif scope == "vendas" and lower.endswith(".json"):
            info = {"added": 0, "skipped_state": True}
        else:
            info = _shared_sync_write_missing_file(target_abs, data)
        if int(info.get("added") or 0) > 0 or info.get("merged"):
            escritos.append(rel)
            added += int(info.get("added") or 0)
        details.append({"file": rel, **info})
    return {
        "file_count": len(escritos),
        "files": escritos[:250],
        "added": added,
        "details": details[:250],
    }

configure_shared_sync_merge_sqlite_runtime()

__all__ = [
    "configure_shared_sync_merge_sqlite_runtime",
    "_shared_sync_merge_csv_add_only",
    "_shared_sync_sql_ident",
    "_shared_sync_sqlite_temp_from_bytes",
    "_shared_sync_sqlite_lock_for_path",
    "_shared_sync_sqlite_is_locked",
    "_shared_sync_sqlite_configure",
    "_shared_sync_sqlite_retry_locked",
    "_shared_sync_merge_vendas_db_add_only",
    "_shared_sync_write_missing_file",
    "_shared_sync_aplicar_user_share_add_only",
]
