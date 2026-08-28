"""Context Hub generations component."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from typing import (
    Any,
    Mapping,
    Optional,
    Sequence,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.bundles import (
    _load_context_bundle,
)

from backend.modules.context_hub.contracts import (
    ContextHubPaths,
    ContextHubRuntimeConfig,
    ContextHubValidationError,
    _SAFE_REASON_RE,
)

from backend.modules.context_hub.curated_collection import (
    _collect_curated_notes,
)

from backend.modules.context_hub.curation_records import (
    _refresh_curation_dashboard_best_effort,
)

from backend.modules.context_hub.documents import (
    _active_generation_id,
    _insert_failed_generation,
    _persist_ready_generation,
    _prepare_documents,
    _public_generation,
    _reuse_unchanged_documents,
    _write_generation_snapshot,
)

from backend.modules.context_hub.findings import (
    _finding,
    _has_blocker,
)

from backend.modules.context_hub.inventory import (
    _build_inventory,
    _inventory_source_hash,
    _runtime_source_version,
    _sanitize_inventory_findings,
)

from backend.modules.context_hub.journal import (
    _recover_publish_journal,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _safe_remove_tree,
    _tenant_thread_lock,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.materialization import (
    capture_generation_materialization,
)

from backend.modules.context_hub.product_evidence_editorial import (
    PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
    collect_product_evidence_editorial_snapshot,
)

from backend.modules.context_hub.runtime import (
    _new_id,
    _runtime_config,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _idempotent_generation(
    connection: sqlite3.Connection,
    source_hash: str,
) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM context_hub_generations
        WHERE source_hash=? AND status IN ('active','ready')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC, rowid DESC
        LIMIT 1
        """,
        (source_hash,),
    ).fetchone()


@dataclass
class _RebuildSource:
    inventory: Mapping[str, Any]
    curated_documents: Sequence[Mapping[str, Any]]
    bundle_documents: Sequence[Mapping[str, Any]]
    product_evidence_snapshot: Mapping[str, Any]
    product_evidence_revision: int
    findings: list[dict[str, Any]]
    source_version: str
    source_hash: str
    stats: dict[str, Any]


def _collect_rebuild_source(
    config: ContextHubRuntimeConfig,
    paths: ContextHubPaths,
) -> _RebuildSource:
    inventory, adapter_findings = _build_inventory(config, paths.client_id)
    source_version = str(inventory.get("source_version") or "unknown").strip()[:120]
    if not re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", source_version):
        source_version = "unknown"
    curated_documents, curated_findings, curated_hashes = _collect_curated_notes(paths)
    bundle_documents, bundle_findings, bundle_hashes = _load_context_bundle(
        config,
        client_id=paths.client_id,
        expected_source_version=source_version,
    )
    findings = (
        adapter_findings
        + _sanitize_inventory_findings(inventory.get("findings"))
        + curated_findings
        + bundle_findings
    )
    captured_at = _utc_now()
    product_evidence_revision = 0
    try:
        with _connect(paths) as connection:
            connection.execute("BEGIN")
            product_evidence_snapshot = collect_product_evidence_editorial_snapshot(
                connection,
                client_id=paths.client_id,
                as_of=captured_at,
            )
            outbox = connection.execute(
                "SELECT requested_revision FROM context_hub_product_evidence_outbox "
                "WHERE singleton_id=1"
            ).fetchone()
            product_evidence_revision = int(outbox["requested_revision"] or 0) if outbox else 0
            connection.commit()
    except Exception:
        product_evidence_snapshot = {
            "schema_version": PRODUCT_EVIDENCE_EDITORIAL_SCHEMA,
            "policy_version": "",
            "snapshot_hash": "0" * 64,
            "captured_at": captured_at,
            "next_transition_at": "",
            "identities": [],
            "stats": {},
        }
        findings.append(
            _finding("product_evidence_snapshot_failed", category="product_evidence")
        )
    runtime_version = _runtime_source_version(config)
    if runtime_version != "unknown" and source_version != runtime_version:
        findings.append(_finding("inventory_source_version_mismatch", category="inventory"))
    raw_stats = dict(inventory.get("stats") or {}) if isinstance(inventory.get("stats"), Mapping) else {}
    stats = {
        str(key): value
        for key, value in raw_stats.items()
        if isinstance(value, (int, float, bool, type(None))) and len(str(key)) <= 80
    }
    for key, value in dict(product_evidence_snapshot.get("stats") or {}).items():
        if isinstance(value, (int, float, bool, type(None))):
            stats[f"product_evidence_{str(key)[:60]}"] = value
    return _RebuildSource(
        inventory=inventory,
        curated_documents=curated_documents,
        bundle_documents=bundle_documents,
        product_evidence_snapshot=product_evidence_snapshot,
        product_evidence_revision=product_evidence_revision,
        findings=findings,
        source_version=source_version,
        source_hash=_inventory_source_hash(
            inventory,
            curated_hashes,
            bundle_hashes,
            str(product_evidence_snapshot.get("snapshot_hash") or ""),
        ),
        stats=stats,
    )


def _failed_generation(
    paths: ContextHubPaths,
    source: _RebuildSource,
    *,
    surface: str,
    reason: str,
    base_active: Optional[str],
) -> dict[str, Any]:
    return _insert_failed_generation(
        paths,
        source_hash=source.source_hash,
        source_version=source.source_version,
        surface=surface,
        reason=reason,
        findings=source.findings,
        stats=source.stats,
        base_active_generation_id=base_active,
    )


def _existing_generation(
    paths: ContextHubPaths,
    source: _RebuildSource,
    *,
    force: bool,
) -> Optional[dict[str, Any]]:
    if force or _has_blocker(source.findings):
        return None
    with _connect(paths) as connection:
        existing = _idempotent_generation(connection, source.source_hash)
    if existing is None:
        return None
    return _public_generation(existing, include_details=True) | {
        "success": True,
        "idempotent": True,
    }


def _generation_stats(
    source: _RebuildSource,
    documents: Sequence[Mapping[str, Any]],
    managed_files: Mapping[str, str],
    reused_documents: int,
) -> None:
    source.stats.update(
        {
            "documents": len(documents),
            "managed_documents": len(managed_files),
            "internal_documents": sum(
                1
                for item in documents
                if str(item.get("relative_path") or "").startswith(("@internal/", "@bundle/"))
            ),
            "curated_documents": sum(
                1
                for item in documents
                if str(item.get("relative_path") or "").startswith("80_Curadoria/")
            ),
            "reused_documents": reused_documents,
        }
    )


def _persist_generation(
    paths: ContextHubPaths,
    source: _RebuildSource,
    documents: Sequence[Mapping[str, Any]],
    managed_files: Mapping[str, str],
    *,
    surface: str,
    reason: str,
    base_active: Optional[str],
    created_at: str,
) -> dict[str, Any]:
    generation_id = _new_id()
    try:
        snapshot_root = _write_generation_snapshot(paths, generation_id, managed_files)
        materialization = capture_generation_materialization(snapshot_root / "70_Gerado")
        _persist_ready_generation(
            paths,
            generation_id,
            source_hash=source.source_hash,
            source_version=source.source_version,
            surface=surface,
            reason=reason,
            findings=source.findings,
            stats=source.stats,
            base_active_generation_id=base_active,
            created_at=created_at,
            documents=documents,
            product_evidence_attestation={
                "evidence_revision": source.product_evidence_revision,
                "snapshot_hash": source.product_evidence_snapshot.get("snapshot_hash"),
                "policy_version": source.product_evidence_snapshot.get("policy_version"),
                "captured_at": source.product_evidence_snapshot.get("captured_at"),
                "next_transition_at": source.product_evidence_snapshot.get("next_transition_at"),
            },
            generation_materialization=materialization,
        )
    except Exception:
        _safe_remove_tree(paths.generations_dir / generation_id, paths.internal_dir)
        source.findings.append(_finding("generation_persist_failed", category="storage"))
        return _failed_generation(
            paths,
            source,
            surface=surface,
            reason=reason,
            base_active=base_active,
        )
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?",
            (generation_id,),
        ).fetchone()
    return _public_generation(row, include_details=True) | {"success": True, "idempotent": False}


def rebuild_context(
    client_id: object,
    *,
    reason: str = "manual_admin",
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build, validate and optionally publish one isolated generation."""

    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, base_dir=config.base_dir, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    _refresh_curation_dashboard_best_effort(paths)
    safe_reason = str(reason or "manual_admin").strip().lower()
    if not _SAFE_REASON_RE.fullmatch(safe_reason):
        raise ContextHubValidationError("Motivo de reconstrucao invalido.")
    thread_lock = _tenant_thread_lock(paths)
    with thread_lock, _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        with _connect(paths) as connection:
            base_active = _active_generation_id(connection)
        source = _collect_rebuild_source(config, paths)
        existing = _existing_generation(paths, source, force=force)
        if existing is not None:
            return existing
        if _has_blocker(source.findings):
            return _failed_generation(
                paths, source, surface=config.surface, reason=safe_reason, base_active=base_active
            )

        created_at = _utc_now()
        documents, managed_files, preparation_findings = _prepare_documents(
            source.inventory,
            source.bundle_documents,
            source.curated_documents,
            source.product_evidence_snapshot,
            client_id=paths.client_id,
            surface=config.surface,
            source_version=source.source_version,
            generated_at=created_at,
        )
        reused_documents = _reuse_unchanged_documents(
            paths,
            base_active,
            documents,
            managed_files,
        )
        source.findings.extend(preparation_findings)
        if _has_blocker(source.findings):
            return _failed_generation(
                paths, source, surface=config.surface, reason=safe_reason, base_active=base_active
            )
        if not documents:
            source.findings.append(_finding("generation_empty", category="inventory"))
            return _failed_generation(
                paths, source, surface=config.surface, reason=safe_reason, base_active=base_active
            )
        _generation_stats(source, documents, managed_files, reused_documents)
        return _persist_generation(
            paths,
            source,
            documents,
            managed_files,
            surface=config.surface,
            reason=safe_reason,
            base_active=base_active,
            created_at=created_at,
        )
