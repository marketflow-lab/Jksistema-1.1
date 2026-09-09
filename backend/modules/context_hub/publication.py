"""Context Hub publication component."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    ContextHubConflictError,
    ContextHubNotFoundError,
    ContextHubPaths,
    ContextHubValidationError,
)

from backend.modules.context_hub.documents import (
    _active_generation_id,
    _public_generation,
)

from backend.modules.context_hub.filesystem import (
    _replace_with_retry,
    _write_json_atomic,
)

from backend.modules.context_hub.generations import (
    rebuild_context,
)

from backend.modules.context_hub.journal import (
    _recover_publish_journal,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _safe_remove_tree,
    _tenant_thread_lock,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.materialization import (
    assert_generation_materialization_current,
)

from backend.modules.context_hub.product_evidence_attestation import (
    align_product_evidence_outbox_generation,
    assert_product_evidence_attestation_current,
)

from backend.modules.context_hub.runtime import (
    _sha256_text,
    _utc_now,
)

from backend.modules.context_hub.settings import (
    _settings_from_row,
)

from backend.modules.context_hub.storage import (
    _connect,
)
from backend.modules.context_hub.store_sku_repository_db import (
    materialize_active_store_sku_generated,
)


def _copy_publish_candidate(paths: ContextHubPaths, generation_id: str) -> tuple[Path, Path]:
    snapshot = paths.generations_dir / generation_id / "70_Gerado"
    _assert_path_chain_safe(snapshot, paths.internal_dir)
    if not snapshot.is_dir():
        raise ContextHubValidationError("Snapshot gerenciado indisponivel para publicacao.")
    temporary = paths.vault_dir / f".context_hub_publish_{generation_id}"
    backup = paths.vault_dir / f".context_hub_backup_{generation_id}"
    _safe_remove_tree(temporary, paths.vault_dir)
    _safe_remove_tree(backup, paths.vault_dir)
    shutil.copytree(snapshot, temporary, copy_function=shutil.copy2)
    materialize_active_store_sku_generated(paths, temporary)
    _preserve_catalog_products(paths, temporary)
    return temporary, backup


def _preserve_catalog_products(paths: ContextHubPaths, target_root: Path) -> None:
    """Keep independently versioned catalog notes across global vault swaps.

    Include historical source objects and manual edits exactly as stored. The
    catalog repository owns their checksums and recovery, not this generation.
    """
    source = paths.generated_dir / "CadastroPorLoja"
    target = target_root / "CadastroPorLoja"
    _assert_path_chain_safe(source, paths.info_root)
    _assert_path_chain_safe(target, paths.info_root)
    _safe_remove_tree(target, paths.vault_dir)
    if not source.exists():
        return
    if not source.is_dir():
        raise ContextHubValidationError("catalog_source_directory_invalid")

    def copy_directory(original: Path, destination: Path) -> None:
        _assert_path_chain_safe(original, paths.info_root)
        _assert_path_chain_safe(destination, paths.info_root)
        destination.mkdir(parents=True, exist_ok=True)
        for entry in original.iterdir():
            _assert_path_chain_safe(entry, paths.info_root)
            output = destination / entry.name
            _assert_path_chain_safe(output, paths.info_root)
            if entry.is_dir():
                copy_directory(entry, output)
            elif entry.is_file():
                shutil.copy2(entry, output)
            else:
                raise ContextHubValidationError("catalog_source_file_invalid")

    copy_directory(source, target)


def _swap_generated_directory(
    paths: ContextHubPaths,
    generation_id: str,
    *,
    previous_generation_id: Optional[str],
) -> tuple[Path, Path]:
    temporary, backup = _copy_publish_candidate(paths, generation_id)
    had_previous = paths.generated_dir.exists()
    journal_base = {
        "generation_id": generation_id,
        "previous_generation_id": str(previous_generation_id or ""),
        "had_previous": had_previous,
        "created_at": _utc_now(),
    }
    _write_json_atomic(
        paths.journal_path,
        journal_base | {"state": "prepared"},
    )
    try:
        if paths.generated_dir.exists():
            _replace_with_retry(paths.generated_dir, backup)
        _write_json_atomic(
            paths.journal_path,
            journal_base | {"state": "old_moved"},
        )
        _replace_with_retry(temporary, paths.generated_dir)
        _write_json_atomic(
            paths.journal_path,
            journal_base | {"state": "new_active"},
        )
        return temporary, backup
    except Exception:
        if backup.exists():
            _safe_remove_tree(paths.generated_dir, paths.vault_dir)
            _replace_with_retry(backup, paths.generated_dir)
        _safe_remove_tree(temporary, paths.vault_dir)
        paths.journal_path.unlink(missing_ok=True)
        raise


def _restore_swapped_directory(paths: ContextHubPaths, temporary: Path, backup: Path) -> None:
    if backup.exists():
        _safe_remove_tree(paths.generated_dir, paths.vault_dir)
        _replace_with_retry(backup, paths.generated_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)


def _finalize_swapped_directory(paths: ContextHubPaths, temporary: Path, backup: Path) -> None:
    _safe_remove_tree(backup, paths.vault_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)


def _assert_ready_curation_attestation(
    paths: ContextHubPaths,
    connection: sqlite3.Connection,
    generation_id: str,
) -> None:
    """Require every curated document to retain its exact current approval."""

    document_count = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM context_hub_documents
            WHERE generation_id=? AND relative_path LIKE '80_Curadoria/%'
            """,
            (generation_id,),
        ).fetchone()[0]
    )
    attestations = connection.execute(
        """
        SELECT relative_path, content_sha256
        FROM context_hub_generation_curated_approvals
        WHERE generation_id=?
        ORDER BY relative_path
        """,
        (generation_id,),
    ).fetchall()
    if len(attestations) != document_count:
        raise ContextHubConflictError(
            "A aprovacao da curadoria mudou; reconstrua antes de publicar."
        )

    prefix = "80_Curadoria/"
    for attestation in attestations:
        vault_relative = str(attestation["relative_path"] or "")
        expected_hash = str(attestation["content_sha256"] or "").lower()
        if not vault_relative.startswith(prefix) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise ContextHubConflictError(
                "A aprovacao da curadoria mudou; reconstrua antes de publicar."
            )
        curated_relative = vault_relative[len(prefix):]
        approval = connection.execute(
            """
            SELECT state, content_sha256 FROM context_hub_curated_approvals
            WHERE relative_path=?
            """,
            (curated_relative,),
        ).fetchone()
        if (
            approval is None
            or str(approval["state"] or "") != "approved"
            or str(approval["content_sha256"] or "").lower() != expected_hash
        ):
            raise ContextHubConflictError(
                "A aprovacao da curadoria mudou; reconstrua antes de publicar."
            )

        source = paths.curated_dir / Path(curated_relative)
        try:
            _assert_path_chain_safe(source, paths.curated_dir)
            if not source.is_file() or source.stat().st_size > 1_000_000:
                raise OSError("curation_source_unavailable")
            actual_hash = _sha256_text(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ContextHubValidationError) as error:
            raise ContextHubConflictError(
                "A nota aprovada mudou ou ficou indisponivel; reconstrua antes de publicar."
            ) from error
        if actual_hash != expected_hash:
            raise ContextHubConflictError(
                "A nota aprovada mudou; reconstrua antes de publicar."
            )


def _publish_generation_locked(
    paths: ContextHubPaths,
    generation_id: str,
    *,
    allow_superseded: bool = False,
) -> dict[str, Any]:
    with _connect(paths) as connection:
        target = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if target is None:
            raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
        current = _active_generation_id(connection)
    if current == generation_id:
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            assert_generation_materialization_current(connection, generation_id, paths.generated_dir)
            assert_product_evidence_attestation_current(
                connection,
                generation_id,
                client_id=paths.client_id,
                as_of=now,
            )
            align_product_evidence_outbox_generation(
                connection,
                generation_id,
                completed_at=now,
            )
            connection.commit()
        return _public_generation(target, include_details=True) | {"success": True, "idempotent": True}
    allowed = {"ready"} | ({"superseded"} if allow_superseded else set())
    if str(target["status"]) not in allowed:
        raise ContextHubValidationError("Somente uma geracao pronta pode ser publicada.")
    expected = current if allow_superseded else (str(target["base_active_generation_id"] or "") or None)
    if expected != current:
        raise ContextHubConflictError("A geracao ativa mudou; reconstrua antes de publicar.")
    with _connect(paths) as connection:
        assert_generation_materialization_current(
            connection,
            generation_id,
            paths.generations_dir / generation_id / "70_Gerado",
        )
        assert_product_evidence_attestation_current(
            connection,
            generation_id,
            client_id=paths.client_id,
            as_of=_utc_now(),
        )
        if str(target["status"]) == "ready":
            _assert_ready_curation_attestation(paths, connection, generation_id)

    temporary, backup = _swap_generated_directory(paths, generation_id, previous_generation_id=current)
    now = _utc_now()
    try:
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            actual = _active_generation_id(connection)
            if actual != current:
                connection.rollback()
                raise ContextHubConflictError("A geracao ativa mudou durante a publicacao.")
            assert_generation_materialization_current(
                connection, generation_id, paths.generated_dir
            )
            if str(target["status"]) == "ready":
                _assert_ready_curation_attestation(paths, connection, generation_id)
            assert_product_evidence_attestation_current(
                connection,
                generation_id,
                client_id=paths.client_id,
                as_of=_utc_now(),
            )
            align_product_evidence_outbox_generation(
                connection,
                generation_id,
                completed_at=now,
            )
            if current:
                connection.execute(
                    """
                    UPDATE context_hub_generations
                    SET status='superseded', superseded_at=?
                    WHERE generation_id=? AND status='active'
                    """,
                    (now, current),
                )
            connection.execute(
                """
                UPDATE context_hub_generations
                SET status='active', published_at=?, superseded_at=NULL
                WHERE generation_id=?
                """,
                (now, generation_id),
            )
            cursor = connection.execute(
                """
                UPDATE context_hub_active_generation
                SET generation_id=?, version=version+1, updated_at=?
                WHERE singleton_id=1 AND (generation_id IS ? OR generation_id=?)
                """,
                (generation_id, now, current, current),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ContextHubConflictError("Falha no compare-and-swap da geracao ativa.")
            connection.commit()
    except Exception:
        _restore_swapped_directory(paths, temporary, backup)
        raise
    _finalize_swapped_directory(paths, temporary, backup)
    _prune_generations(paths)
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
    return _public_generation(row, include_details=True) | {"success": True, "idempotent": False}


def publish_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_id):
        raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        return _publish_generation_locked(paths, normalized_id)


def publish_curated_context(
    client_id: object,
    *,
    reason: str = "manual_curation_publish",
    force: bool = False,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    """Build a ready generation and explicitly activate it in one admin action."""

    ready = rebuild_context(
        client_id,
        reason=reason,
        force=force,
        base_dir=base_dir,
        info_root=info_root,
        surface=surface,
    )
    if not ready.get("success") or ready.get("status") != "ready":
        return ready
    generation_id = str(ready.get("generation_id") or "")
    published = publish_generation(client_id, generation_id, info_root=info_root)
    published["curation_publication"] = True
    return published


def rollback_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_id):
        raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        result = _publish_generation_locked(paths, normalized_id, allow_superseded=True)
        result["rollback"] = True
        return result


def _prune_generations(paths: ContextHubPaths) -> None:
    with _connect(paths) as connection:
        settings = _settings_from_row(connection.execute(
            "SELECT * FROM context_hub_settings WHERE singleton_id=1"
        ).fetchone(), "development")
        active = _active_generation_id(connection)
        published = connection.execute(
            """
            SELECT generation_id FROM context_hub_generations
            WHERE status IN ('active','superseded')
            ORDER BY COALESCE(published_at, created_at) DESC, rowid DESC
            """
        ).fetchall()
        protected: set[str] = {active} if active else set()
        published_ids = [str(row["generation_id"]) for row in published]
        if len(published_ids) > 1:
            protected.add(published_ids[1])  # explicit previous generation
        protected.update(published_ids[2 : 2 + int(settings["retention_generations"])])
        removable = [generation_id for generation_id in published_ids if generation_id not in protected]
        failed_or_ready = connection.execute(
            """
            SELECT generation_id FROM context_hub_generations
            WHERE status IN ('failed','ready') ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
        removable.extend(str(row["generation_id"]) for row in failed_or_ready[int(settings["retention_generations"]) :])
        if not removable:
            return
        connection.execute("BEGIN IMMEDIATE")
        for generation_id in sorted(set(removable)):
            try:
                connection.execute(
                    "DELETE FROM context_hub_chunks_fts WHERE generation_id=?",
                    (generation_id,),
                )
            except sqlite3.OperationalError:
                pass
            connection.execute("DELETE FROM context_hub_generations WHERE generation_id=?", (generation_id,))
        connection.commit()
    for generation_id in sorted(set(removable)):
        _safe_remove_tree(paths.generations_dir / generation_id, paths.internal_dir)
