"""Shared Sync CSV and SQLite add-only merge helpers."""

from __future__ import annotations

import base64
import io
import json
import logging
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


_LOG = logging.getLogger(__name__)


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

def _shared_sync_sqlite_is_locked(exc: Exception) -> bool:
    texto = str(exc or "").lower()
    return "database is locked" in texto or "database table is locked" in texto or "database is busy" in texto

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
            globals().get("logger", _LOG).warning(
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

SHARED_SYNC_VENDAS_TABLES = ("vendas", "notas_entrada", "notas_entrada_itens")


def _shared_sync_sqlite_backup_to_path(source_abs: str, destination_abs: str, label: str) -> None:
    os.makedirs(os.path.dirname(destination_abs), exist_ok=True)
    src = sqlite3.connect(
        f"file:{os.path.abspath(source_abs)}?mode=ro",
        uri=True,
        timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000),
    )
    dst = sqlite3.connect(destination_abs, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
    try:
        _shared_sync_sqlite_configure(src)
        _shared_sync_sqlite_configure(dst)
        src.backup(dst)
        _shared_sync_sqlite_quick_check(dst, label)
    finally:
        dst.close()
        src.close()


def _shared_sync_vendas_merge_table(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
) -> dict:
    table_ident = _shared_sync_sql_ident(table)
    src_cur = src.cursor()
    dst_cur = dst.cursor()
    exists_src = src_cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists_src:
        return {"added": 0, "total": 0, "present": False, "schema_changed": False}

    src_cols_info = src_cur.execute(f"PRAGMA table_info({table_ident})").fetchall()
    src_cols = [str(row[1]) for row in src_cols_info]
    if "id_unico" not in src_cols:
        raise HTTPException(status_code=502, detail=f"Tabela {table} sem id_unico no banco recebido.")

    schema_changed = False
    exists_dst = dst_cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists_dst:
        create_sql = str(exists_src[0] or "").strip()
        if not create_sql:
            raise HTTPException(status_code=502, detail=f"Schema da tabela {table} ausente no banco recebido.")
        dst_cur.execute(create_sql)
        schema_changed = True

    dst_cols_info = dst_cur.execute(f"PRAGMA table_info({table_ident})").fetchall()
    dst_cols = [str(row[1]) for row in dst_cols_info]
    if "id_unico" not in dst_cols:
        raise HTTPException(status_code=502, detail=f"Tabela local {table} sem id_unico.")

    for col in src_cols_info:
        name = str(col[1])
        if name in dst_cols:
            continue
        col_type = str(col[2] or "TEXT")
        dst_cur.execute(f"ALTER TABLE {table_ident} ADD COLUMN {_shared_sync_sql_ident(name)} {col_type}")
        dst_cols.append(name)
        schema_changed = True

    common_cols = [col for col in src_cols if col in dst_cols]
    id_idx = common_cols.index("id_unico")
    existing_ids = {
        str(row[0] or "").strip()
        for row in dst_cur.execute(f"SELECT id_unico FROM {table_ident}").fetchall()
        if str(row[0] or "").strip()
    }
    select_sql = f"SELECT {', '.join(_shared_sync_sql_ident(col) for col in common_cols)} FROM {table_ident}"
    insert_sql = (
        f"INSERT OR IGNORE INTO {table_ident} "
        f"({', '.join(_shared_sync_sql_ident(col) for col in common_cols)}) "
        f"VALUES ({', '.join(['?'] * len(common_cols))})"
    )
    inserted = 0
    for row in src_cur.execute(select_sql):
        row_id = str(row[id_idx] or "").strip()
        if not row_id:
            raise HTTPException(status_code=502, detail=f"Tabela {table} contem id_unico vazio.")
        if row_id in existing_ids:
            continue
        dst_cur.execute(insert_sql, tuple(row))
        if dst_cur.rowcount > 0:
            inserted += 1
            existing_ids.add(row_id)

    total = int(dst_cur.execute(f"SELECT COUNT(*) FROM {table_ident}").fetchone()[0] or 0)
    return {
        "added": inserted,
        "total": total,
        "present": True,
        "schema_changed": schema_changed,
    }


def _shared_sync_stage_vendas_db_add_only(target_abs: str, remoto_bytes: bytes, rel: str) -> dict:
    if not (remoto_bytes or b"").startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}")
    remoto_tmp = _shared_sync_sqlite_temp_from_bytes(remoto_bytes, "shared_sync_vendas_remote_")
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    fd, stage_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target_abs)}.sharedsync_",
        suffix=".tmp",
        dir=os.path.dirname(target_abs),
    )
    os.close(fd)
    try:
        if os.path.exists(target_abs):
            _shared_sync_sqlite_backup_to_path(target_abs, stage_path, os.path.basename(target_abs))

        src = sqlite3.connect(remoto_tmp, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        dst = sqlite3.connect(stage_path, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        try:
            _shared_sync_sqlite_configure(src)
            _shared_sync_sqlite_configure(dst)
            _shared_sync_sqlite_quick_check(src, rel)
            dst.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    table: _shared_sync_vendas_merge_table(src, dst, table)
                    for table in SHARED_SYNC_VENDAS_TABLES
                }
                dst.commit()
            except BaseException:
                dst.rollback()
                raise
            _shared_sync_sqlite_quick_check(dst, rel)
        finally:
            dst.close()
            src.close()

        added = sum(int(info.get("added") or 0) for info in tables.values())
        changed = (
            not os.path.exists(target_abs)
            or added > 0
            or any(bool(info.get("schema_changed")) for info in tables.values())
        )
        return {
            "target": target_abs,
            "stage": stage_path,
            "rel": rel,
            "added": added,
            "changed": changed,
            "tables": tables,
        }
    except BaseException:
        try:
            os.remove(stage_path)
        except OSError:
            pass
        raise
    finally:
        try:
            os.remove(remoto_tmp)
        except OSError:
            pass


def _shared_sync_backup_staged_original(stage: dict, backup_dir: str) -> None:
    target_abs = str(stage["target"])
    if not os.path.exists(target_abs) or not stage.get("changed"):
        return
    backup_abs = os.path.abspath(os.path.join(backup_dir, str(stage["rel"])))
    backup_root = os.path.abspath(backup_dir)
    if not backup_abs.startswith(backup_root + os.sep):
        raise HTTPException(status_code=400, detail="Backup local contem caminho invalido.")
    os.makedirs(os.path.dirname(backup_abs), exist_ok=True)
    _shared_sync_sqlite_backup_to_path(target_abs, backup_abs, os.path.basename(target_abs))
    stage["backup"] = backup_abs


def _shared_sync_prepare_target_for_replace(target_abs: str, label: str) -> None:
    """Consolida WAL e remove sidecars antes de substituir o arquivo principal.

    Um ``os.replace`` apenas do ``.db`` com um WAL antigo ao lado pode fazer o
    SQLite reaplicar paginas do banco anterior sobre o arquivo novo. O lock por
    caminho impede novas conexoes coordenadas enquanto o checkpoint e a troca
    acontecem; se outro processo ainda estiver usando o banco, a operacao falha
    com 423 antes de qualquer arquivo principal ser promovido.
    """

    if not os.path.exists(target_abs):
        return
    conn = sqlite3.connect(
        target_abs,
        timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000),
    )
    try:
        _shared_sync_sqlite_configure(conn)
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0] or 0) != 0:
            raise sqlite3.OperationalError("database is locked during WAL checkpoint")
        _shared_sync_sqlite_quick_check(conn, label)
    finally:
        conn.close()

    for suffix in ("-shm", "-wal"):
        sidecar = f"{target_abs}{suffix}"
        if os.path.exists(sidecar):
            os.remove(sidecar)


def _shared_sync_rollback_promotions(promotions: list[dict]) -> list[str]:
    failures: list[str] = []
    for item in reversed(promotions):
        target = item["target"]
        rollback = item.get("rollback") or ""
        backup = item.get("backup") or ""
        try:
            if item.get("promoted") and os.path.exists(target):
                os.remove(target)
            restored = not item.get("moved")
            if item.get("moved") and rollback and os.path.exists(rollback):
                try:
                    os.replace(rollback, target)
                    restored = True
                except OSError:
                    restored = False
            if not restored and backup and os.path.exists(backup):
                _shared_sync_sqlite_backup_to_path(backup, target, os.path.basename(target))
                restored = True
            if item.get("moved") and not os.path.exists(target):
                raise OSError("arquivo original nao foi restaurado")
        except Exception as exc:
            failures.append(f"{os.path.basename(target)}: {exc}")
    return failures


def _shared_sync_apply_vendas_dbs_add_only(
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> dict:
    prepared_sources = []
    for rel, data in fontes:
        safe_rel = _shared_sync_relativo_seguro(rel)
        if not safe_rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
            continue
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, safe_rel)
        prepared_sources.append((safe_rel, data, target_abs))
    if not prepared_sources:
        return {"file_count": 0, "files": [], "added": 0, "details": []}

    targets = [item[2] for item in prepared_sources]
    stages: list[dict] = []
    promotions: list[dict] = []
    try:
        with _shared_sync_sqlite_locks_for_paths(targets):
            for rel, data, target_abs in prepared_sources:
                stage = _shared_sync_sqlite_retry_locked(
                    lambda target_abs=target_abs, data=data, rel=rel: _shared_sync_stage_vendas_db_add_only(
                        target_abs, data, rel,
                    ),
                    rel,
                )
                stages.append(stage)

            for stage in stages:
                _shared_sync_backup_staged_original(stage, backup_dir)

            # Prepare todos os destinos antes da primeira promocao. Assim, um
            # banco ocupado nunca deixa apenas parte do conjunto substituida.
            for stage in stages:
                if not stage.get("changed"):
                    continue
                _shared_sync_sqlite_retry_locked(
                    lambda stage=stage: _shared_sync_prepare_target_for_replace(
                        str(stage["target"]), str(stage["rel"]),
                    ),
                    str(stage["rel"]),
                )

            for stage in stages:
                if not stage.get("changed"):
                    continue
                target = str(stage["target"])
                rollback = f"{target}.sharedsync_{uuid.uuid4().hex}.rollback"
                record = {
                    "target": target,
                    "rollback": rollback,
                    "backup": str(stage.get("backup") or ""),
                    "moved": False,
                    "promoted": False,
                }
                promotions.append(record)
                if os.path.exists(target):
                    os.replace(target, rollback)
                    record["moved"] = True
                os.replace(str(stage["stage"]), target)
                record["promoted"] = True

            for item in promotions:
                rollback = item.get("rollback") or ""
                if rollback and os.path.exists(rollback):
                    try:
                        os.remove(rollback)
                    except OSError:
                        # O arquivo e apenas a copia transitoria; o backup
                        # recuperavel em _shared_sync_backups ja foi criado.
                        pass
    except BaseException as exc:
        with _shared_sync_sqlite_locks_for_paths(targets):
            rollback_failures = _shared_sync_rollback_promotions(promotions)
        if rollback_failures:
            globals().get("logger", _LOG).critical(
                "[SHARED-SYNC] Falha ao reverter promocao de bancos: %s",
                "; ".join(rollback_failures[:5]),
            )
            raise HTTPException(
                status_code=500,
                detail="Falha critica ao reverter bancos de vendas; backups recuperaveis foram preservados.",
            ) from exc
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {32, 33}:
            raise HTTPException(
                status_code=423,
                detail="Banco de vendas em uso no momento. Tente sincronizar novamente em instantes.",
            ) from exc
        raise
    finally:
        for stage in stages:
            try:
                if os.path.exists(str(stage.get("stage") or "")):
                    os.remove(str(stage["stage"]))
            except OSError:
                pass

    changed = [stage for stage in stages if stage.get("changed")]
    return {
        "file_count": len(changed),
        "files": [str(stage["rel"]) for stage in changed][:250],
        "added": sum(int(stage.get("added") or 0) for stage in stages),
        "details": [
            {
                "file": stage["rel"],
                "added": stage["added"],
                "changed": bool(stage["changed"]),
                "tables": stage["tables"],
            }
            for stage in stages
        ][:250],
    }


def _shared_sync_merge_vendas_db_add_only(target_abs: str, remoto_bytes: bytes, rel: str) -> dict:
    safe_rel = _shared_sync_relativo_seguro(rel or os.path.basename(target_abs))
    tenant_abs = os.path.abspath(target_abs)
    for _part in safe_rel.split("/"):
        tenant_abs = os.path.dirname(tenant_abs)
    backup_dir = os.path.join(tenant_abs, "_shared_sync_backups", f"vendas_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
    result = _shared_sync_apply_vendas_dbs_add_only(
        [(safe_rel, remoto_bytes)], tenant_abs, backup_dir,
    )
    details = result.get("details") or []
    info = details[0] if details else {"added": 0, "tables": {}}
    total = sum(int(table.get("total") or 0) for table in (info.get("tables") or {}).values())
    return {"added": int(info.get("added") or 0), "total": total, "tables": info.get("tables") or {}}

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
    vendas_dbs = [
        (rel, data)
        for rel, data in fontes
        if scope == "vendas" and _shared_sync_vendas_history_db(rel)
    ]
    if vendas_dbs:
        merged_dbs = _shared_sync_apply_vendas_dbs_add_only(vendas_dbs, tenant_abs, backup_dir)
        escritos.extend(merged_dbs.get("files") or [])
        added += int(merged_dbs.get("added") or 0)
        details.extend(merged_dbs.get("details") or [])

    for rel, data in fontes:
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
        lower = rel.lower()
        if scope == "vendas" and _shared_sync_vendas_history_db(rel):
            continue
        if scope == "cadastro" and lower.endswith(".csv"):
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            info = _shared_sync_merge_csv_add_only(target_abs, data, scope, rel)
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
    "SHARED_SYNC_VENDAS_TABLES",
    "_shared_sync_sqlite_backup_to_path",
    "_shared_sync_vendas_merge_table",
    "_shared_sync_stage_vendas_db_add_only",
    "_shared_sync_apply_vendas_dbs_add_only",
    "_shared_sync_merge_vendas_db_add_only",
    "_shared_sync_write_missing_file",
    "_shared_sync_aplicar_user_share_add_only",
]
