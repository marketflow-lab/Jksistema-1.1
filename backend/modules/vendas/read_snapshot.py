"""Read one store's commercial tables and account evidence at one SQLite version."""

from __future__ import annotations

import sqlite3


def begin_vendas_snapshot(conn: sqlite3.Connection) -> set[tuple[str, str]] | None:
    """Start a read transaction; the caller closes it before projecting rows.

    Account evidence must share the version used by sales and returns, including
    evidence outside the requested dates. Closing the read-only connection rolls
    this transaction back without writing business data.
    """
    conn.execute("BEGIN")
    accounts = set()
    try:
        for table in ("vendas", "notas_entrada", "notas_entrada_itens"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if {"loja_conta", "store_id"} <= columns:
                accounts.update(
                    (row[0], row[1]) for row in conn.execute(
                        f"SELECT DISTINCT loja_conta, trim(store_id) FROM {table} "
                        "WHERE loja_conta IS NOT NULL AND trim(coalesce(store_id,'')) != ''"
                    ).fetchall()
                )
    except (OSError, sqlite3.Error):
        # Unknown account evidence cannot authorize suppression of legacy rows.
        # Keep live data readable, but never publish this read as a full cache.
        from .performance import mark_vendas_read_incomplete
        mark_vendas_read_incomplete()
        return None
    return accounts
