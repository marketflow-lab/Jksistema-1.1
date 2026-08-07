"""Context Hub backups component."""

from __future__ import annotations

import os
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    ContextHubValidationError,
)

from backend.modules.context_hub.curation_records import (
    _refresh_curation_dashboard_best_effort,
    _validate_curated_content,
)

from backend.modules.context_hub.findings import (
    _has_blocker,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _tenant_thread_lock,
)

from backend.modules.context_hub.metadata import (
    _parse_frontmatter,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def create_curated_backup(
    client_id: object,
    *,
    passphrase: object,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    from backend.services import context_hub_backup

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    try:
        with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
            backup = context_hub_backup.create_encrypted_backup(
                paths.curated_dir,
                paths.backups_dir,
                client_id=paths.client_id,
                passphrase=passphrase,
            )
    except context_hub_backup.BackupValidationError as error:
        raise ContextHubValidationError(str(error)) from error
    return {"success": True, "client_id": paths.client_id, "backup": backup}


def list_curated_backups(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    from backend.services import context_hub_backup

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    backups = context_hub_backup.list_backups(paths.backups_dir, client_id=paths.client_id)
    return {"success": True, "client_id": paths.client_id, "backups": backups, "count": len(backups)}


def restore_curated_backup(
    client_id: object,
    backup_id: object,
    *,
    passphrase: object,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    from backend.services import context_hub_backup

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)

    def validate_restored_note(relative_path: str, content: str) -> None:
        metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        findings = _validate_curated_content(metadata, body, source_ref=relative_path)
        if _has_blocker(findings):
            raise context_hub_backup.BackupValidationError(
                "Backup contem nota reprovada pela validacao/DLP."
            )

    def invalidate_approvals_before_promotion() -> None:
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE context_hub_curated_approvals SET state='draft',
                    validated_sha256=NULL, validated_at=NULL,
                    reviewed_by=NULL, reviewed_at=NULL,
                    approved_by=NULL, approved_at=NULL,
                    rejected_by=NULL, rejected_at=NULL, rejection_reason=NULL,
                    updated_at=?
                """,
                (_utc_now(),),
            )
            connection.commit()

    try:
        with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
            restored = context_hub_backup.restore_encrypted_backup(
                backup_id,
                paths.curated_dir,
                paths.backups_dir,
                paths.restore_staging_dir,
                client_id=paths.client_id,
                passphrase=passphrase,
                validate_note=validate_restored_note,
                before_promote=invalidate_approvals_before_promotion,
            )
    except context_hub_backup.BackupValidationError as error:
        raise ContextHubValidationError(str(error)) from error
    _refresh_curation_dashboard_best_effort(paths)
    return {"success": True, "client_id": paths.client_id, "restore": restored}
