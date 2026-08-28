"""Context Hub status component."""

from __future__ import annotations

import os
import sqlite3
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    ContextHubNotFoundError,
)

from backend.modules.context_hub.documents import (
    _active_generation_id,
    _public_generation,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _runtime_config,
)

from backend.modules.context_hub.settings import (
    _settings_from_row,
)

from backend.modules.context_hub.state import CONTEXT_HUB_STATE

from backend.modules.context_hub.storage import (
    _connect,
)


def list_generations(
    client_id: object,
    *,
    limit: int = 50,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    safe_limit = max(1, min(int(limit or 50), 200))
    with _connect(paths) as connection:
        rows = connection.execute(
            "SELECT * FROM context_hub_generations ORDER BY created_at DESC, rowid DESC LIMIT ?", (safe_limit,)
        ).fetchall()
    return {"success": True, "client_id": paths.client_id, "generations": [_public_generation(row) for row in rows]}


def get_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (normalized_id,)
        ).fetchone()
        if row is None:
            raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
        counts = connection.execute(
            """
            SELECT
                COUNT(DISTINCT d.doc_id) AS documents,
                COUNT(c.chunk_id) AS chunks
            FROM context_hub_documents d
            LEFT JOIN context_hub_chunks c
              ON c.generation_id=d.generation_id AND c.doc_id=d.doc_id
            WHERE d.generation_id=?
            """,
            (normalized_id,),
        ).fetchone()
    payload = _public_generation(row, include_details=True)
    payload["counts"] = {"documents": int(counts["documents"] or 0), "chunks": int(counts["chunks"] or 0)}
    return {"success": True, "client_id": paths.client_id, "generation": payload}


def _generation_document_hashes(
    connection: sqlite3.Connection,
    generation_id: Optional[str],
) -> dict[str, str]:
    if not generation_id:
        return {}
    return {
        str(row["doc_id"]): str(row["content_hash"])
        for row in connection.execute(
            "SELECT doc_id, content_hash FROM context_hub_documents WHERE generation_id=?",
            (generation_id,),
        ).fetchall()
    }


def _product_evidence_sync_snapshot(
    connection: sqlite3.Connection,
    active_id: Optional[str],
) -> tuple[sqlite3.Row, int, Optional[sqlite3.Row]]:
    row = connection.execute(
        "SELECT requested_revision, completed_revision, not_before, "
        "attempt_count, last_attempt_at, last_success_at, last_error_code, "
        "completed_generation_id, updated_at "
        "FROM context_hub_product_evidence_outbox WHERE singleton_id=1"
    ).fetchone()
    count = int(connection.execute(
        "SELECT COUNT(*) FROM context_hub_documents "
        "WHERE generation_id=? AND kind='product_evidence_fact'",
        (active_id,),
    ).fetchone()[0]) if active_id else 0
    attestation = connection.execute(
        "SELECT next_transition_at FROM context_hub_generation_product_evidence "
        "WHERE generation_id=?",
        (active_id,),
    ).fetchone() if active_id else None
    return row, count, attestation


def _public_product_evidence_sync(
    paths: Any,
    active_id: Optional[str],
    snapshot: tuple[sqlite3.Row, int, Optional[sqlite3.Row]],
) -> dict[str, Any]:
    row, active_count, attestation = snapshot
    key = str(paths.internal_dir).casefold()
    with CONTEXT_HUB_STATE.product_evidence_sync_guard:
        worker = CONTEXT_HUB_STATE.product_evidence_sync_workers.get(key)
        worker_running = bool(worker and worker[0].is_alive())
        transition_scheduled = key in CONTEXT_HUB_STATE.product_evidence_transition_schedule
        scheduler = CONTEXT_HUB_STATE.product_evidence_transition_scheduler
        scheduler_running = bool(scheduler and scheduler[0].is_alive())
    requested = int(row["requested_revision"] or 0)
    completed = int(row["completed_revision"] or 0)
    completed_generation = str(row["completed_generation_id"] or "")
    current = bool(
        (requested == 0 and completed == 0 and not completed_generation and not active_count)
        or (
            requested == completed
            and completed_generation == str(active_id or "")
        )
    )
    return {
        "pending": requested > completed,
        "requested_revision": requested,
        "completed_revision": completed,
        "attempt_count": int(row["attempt_count"] or 0),
        "last_error_code": str(row["last_error_code"] or ""),
        "not_before": str(row["not_before"] or ""),
        "last_attempt_at": row["last_attempt_at"],
        "last_success_at": row["last_success_at"],
        "updated_at": str(row["updated_at"] or ""),
        "worker_running": worker_running,
        "transition_scheduled": transition_scheduled,
        "scheduler_running": scheduler_running,
        "active_evidence_current": current,
        "active_verified_facts": active_count,
        "next_transition_at": attestation["next_transition_at"] if attestation else None,
    }


def get_status(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    with _connect(paths) as connection:
        connection.execute("BEGIN")
        try:
            active_id = _active_generation_id(connection)
            active = connection.execute(
                "SELECT * FROM context_hub_generations WHERE generation_id=?", (active_id,)
            ).fetchone() if active_id else None
            latest = connection.execute(
                "SELECT * FROM context_hub_generations ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            diff_target_id = (
                str(latest["generation_id"])
                if latest and str(latest["status"]) in {"ready", "active", "superseded"}
                else active_id
            )
            active_hashes = _generation_document_hashes(connection, active_id)
            target_hashes = _generation_document_hashes(connection, diff_target_id)
            counts = connection.execute(
                """
                SELECT COUNT(DISTINCT d.doc_id) AS documents, COUNT(c.chunk_id) AS chunks
                FROM context_hub_documents d
                LEFT JOIN context_hub_chunks c
                  ON c.generation_id=d.generation_id AND c.doc_id=d.doc_id
                WHERE d.generation_id=?
                """,
                (active_id,),
            ).fetchone() if active_id else {"documents": 0, "chunks": 0}
            settings_row = connection.execute(
                "SELECT * FROM context_hub_settings WHERE singleton_id=1"
            ).fetchone()
            curation_rows = connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM context_hub_curated_approvals WHERE present=1 GROUP BY state
                """
            ).fetchall()
            evidence_sync_snapshot = _product_evidence_sync_snapshot(connection, active_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    settings = _settings_from_row(settings_row, config.surface)
    watcher_key = str(paths.internal_dir).lower()
    with CONTEXT_HUB_STATE.watchers_guard:
        watcher = CONTEXT_HUB_STATE.watchers.get(watcher_key)
        watcher_running = bool(watcher and watcher[0].is_alive())
    evidence_sync = _public_product_evidence_sync(
        paths,
        active_id,
        evidence_sync_snapshot,
    )
    latest_public = _public_generation(latest, include_details=True) if latest else None
    active_ids = set(active_hashes)
    target_ids = set(target_hashes)
    diff_added = len(target_ids - active_ids)
    diff_removed = len(active_ids - target_ids)
    diff_updated = sum(
        1
        for doc_id in active_ids & target_ids
        if active_hashes[doc_id] != target_hashes[doc_id]
    )
    return {
        "success": True,
        "client_id": paths.client_id,
        "initialized": True,
        "surface": config.surface,
        "active_generation": _public_generation(active) if active else None,
        "latest_generation": latest_public,
        "source_version": str(active["source_version"] or "") if active else "",
        "counts": {"documents": int(counts["documents"] or 0), "chunks": int(counts["chunks"] or 0)},
        "settings": settings,
        "watcher_running": watcher_running,
        "product_evidence_sync": evidence_sync,
        "curation": {
            "states": {str(row["state"]): int(row["count"] or 0) for row in curation_rows},
            "manual_publication_only": True,
            "embeddings_enabled": False,
            "search_engine": "fts5_bm25",
        },
        "blocked": bool(latest and latest["status"] == "failed"),
        "blockers": [
            {key: value for key, value in finding.items() if key != "source_ref"}
            for finding in (latest_public or {}).get("findings", [])
        ] if latest and latest["status"] == "failed" else [],
        "diff": {
            "has_changes": bool(latest and (not active or latest["source_hash"] != active["source_hash"])),
            "added": diff_added,
            "updated": diff_updated,
            "removed": diff_removed,
            "latest_generation_id": str(latest["generation_id"]) if latest else None,
            "latest_status": str(latest["status"]) if latest else None,
        },
    }
