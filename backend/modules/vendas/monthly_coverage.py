"""Read closed sales months from the durable, tenant-scoped daily receipts."""

import calendar
import datetime as dt
import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from functools import cache
from pathlib import Path

from backend.services.bling_vendas import _slug_loja_para_arquivo

from .sync_queue import SYNC_TIMEZONE, SyncQueue, _time


def _historicos(root):
    return {
        path.name.casefold(): path for path in root.glob("vendas_historico*.db")
        if (path.name == "vendas_historico.db" or path.name.startswith("vendas_historico_"))
        and ".backup_" not in path.name and path.is_file() and not path.is_symlink()
    }


def _historico_legado(paths, store_id, loja):
    # A receipt predating database_name may belong to a renamed store or to
    # the shared legacy database. An empty day still has a valid receipt.
    if loja:
        expected = f"vendas_historico_{_slug_loja_para_arquivo(loja)}.db"
        if expected in paths:
            return expected
    for name, path in sorted(paths.items()):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            for table in ("vendas", "notas_entrada"):
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if "store_id" in columns and conn.execute(
                    f"SELECT 1 FROM {table} WHERE store_id = ? LIMIT 1", (store_id,),
                ).fetchone():
                    return name
    return None


def meses_confirmados(tenant_path, client_id, store_id=None, *, now=None) -> dict[tuple[str, str], str]:
    """Map (stable store ID, closed YYYY-MM) to a version of its daily receipts.

    Missing, foreign or unreadable evidence yields no certification. Passing
    None selects every store in this tenant. A caller asking for one store
    must pass its ID, including when the visible store name has changed.
    """
    root = Path(tenant_path).resolve()
    queue = root / "vendas_sync_queue.db"
    if not client_id or store_id == "" or not queue.is_file() or queue.is_symlink():
        return {}
    instant = _time(now)
    current_month = dt.datetime.fromtimestamp(instant, SYNC_TIMEZONE).date().replace(day=1)
    try:
        paths = _historicos(root)
        if not paths:
            return {}
        with closing(sqlite3.connect(queue.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            if [row[0] for row in conn.execute("SELECT client_id FROM tenant")] != [client_id]:
                return {}
            columns = {row[1] for row in conn.execute("PRAGMA table_info(days)")}
            started = "COALESCE(d.collection_started_at, j.created_at)" if "collection_started_at" in columns else "j.created_at"
            database = "d.database_name" if "database_name" in columns else "NULL"
            sources = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='store_sources'").fetchone():
                sources = dict(conn.execute("SELECT store_id, connection_identity FROM store_sources"))
            store_filter = "" if store_id is None else " AND j.store_id = ? COLLATE BINARY"
            params = (str(current_month), instant) + (() if store_id is None else (store_id,))
            rows = conn.execute(f"""
                SELECT j.store_id, j.loja, d.day, d.completed_at, {started} AS collection_started_at,
                       {database} AS database_name
                FROM days d JOIN jobs j ON j.id = d.job_id
                WHERE d.day < ? AND d.completed_at <= ? {store_filter}
                ORDER BY j.store_id, d.day, d.completed_at DESC, j.created_at ASC
            """, params)
            return _agrupar_meses(rows, client_id, sources, paths, instant)
    except (sqlite3.Error, OSError):
        return {}


def _agrupar_meses(rows, client_id, sources, paths, instant):
    @cache
    def historico_legado(store, loja):
        return _historico_legado(paths, store, loja)

    meses = defaultdict(dict)
    ultimo_dia = None
    for row in rows:
        store, dia = row["store_id"], row["day"]
        if not store or (store, dia) == ultimo_dia or not SyncQueue._closed_day(row, instant):
            continue
        ultimo_dia = (store, dia)
        try:
            date = dt.date.fromisoformat(dia)
        except (ValueError, TypeError):
            continue
        if dia != date.isoformat():
            continue
        database = row["database_name"]
        if database is None:
            database = historico_legado(store, row["loja"])
        elif isinstance(database, str):
            database = database.casefold()
        # A later collection whose database was lost invalidates the day.
        # Falling through to a previous receipt would certify stale data.
        if database not in paths:
            continue
        meses[store, date.replace(day=1)][date.day] = (
            row["collection_started_at"], row["completed_at"], database,
        )

    result = {}
    for (store, first), receipts in meses.items():
        if len(receipts) != calendar.monthrange(first.year, first.month)[1]:
            continue
        month = first.isoformat()[:7]
        proof = (client_id, store, sources.get(store), month, sorted(receipts.items()))
        result[store, month] = hashlib.sha256(json.dumps(proof, separators=(",", ":")).encode()).hexdigest()
    return result
