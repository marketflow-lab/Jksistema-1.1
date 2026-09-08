"""Public-safe customer-reply activity queries."""

from __future__ import annotations

from typing import Any

from .common import _connection, _json_loads, _lock_for, codex_assistant_state_db_path
from .schema import _ensure_state_schema


def codex_assistant_customer_reply_solicitacoes_list(
    info_base: str,
    client_id: str,
    *,
    store_scopes: list[tuple[str, str, str]],
    status: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """List the durable journal only for exact authorized store identities."""

    clean_scopes = list(dict.fromkeys(
        (
            str(store_id or "").strip(),
            str(seller_id or "").strip(),
            str(site_id or "").strip(),
        )
        for store_id, seller_id, site_id in store_scopes
        if str(store_id or "").strip()
    ))
    bounded_limit = max(1, min(int(limit or 20), 100))
    bounded_offset = max(0, int(offset or 0))
    if not clean_scopes:
        return {
            "rows": [], "total": 0, "limit": bounded_limit,
            "offset": bounded_offset, "status_resumo": {},
        }

    scope_parts = ["(store_id = ? AND seller_id = ? AND site_id = ?)" for _ in clean_scopes]
    scope_params: list[Any] = [value for scope in clean_scopes for value in scope]
    scope_clause = "(" + " OR ".join(scope_parts) + ")"
    base_clause = "profile = 'mercado_livre_customer_reply' AND " + scope_clause
    filtered_clause = base_clause
    filtered_params = list(scope_params)
    clean_status = str(status or "").strip()
    if clean_status:
        filtered_clause += " AND status = ?"
        filtered_params.append(clean_status)

    db_path = codex_assistant_state_db_path(info_base, client_id)
    with _lock_for(db_path):
        with _connection(db_path) as conn:
            _ensure_state_schema(conn)
            total_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM assistant_customer_reply_jobs WHERE {filtered_clause}",
                filtered_params,
            ).fetchone()
            rows = conn.execute(
                "SELECT payload_json, store_id, seller_id, site_id "
                f"FROM assistant_customer_reply_jobs WHERE {filtered_clause} "
                "ORDER BY updated_at DESC, created_at DESC, job_id DESC LIMIT ? OFFSET ?",
                [*filtered_params, bounded_limit, bounded_offset],
            ).fetchall()
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM assistant_customer_reply_jobs "
                f"WHERE {base_clause} GROUP BY status",
                scope_params,
            ).fetchall()

    result_rows: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_loads(row["payload_json"], None)
        if not isinstance(payload, dict):
            continue
        durable = dict(payload)
        for identity_field in ("store_id", "seller_id", "site_id"):
            durable[identity_field] = str(row[identity_field] or durable.get(identity_field) or "")
        result_rows.append(durable)
    return {
        "rows": result_rows,
        "total": max(0, int(total_row["total"] or 0)),
        "limit": bounded_limit,
        "offset": bounded_offset,
        "status_resumo": {
            str(row["status"] or ""): max(0, int(row["total"] or 0))
            for row in status_rows if str(row["status"] or "")
        },
    }
