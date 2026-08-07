"""Context Hub curation component."""

from __future__ import annotations

import os
import re
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.bootstrap import (
    bootstrap_context_hub,
)

from backend.modules.context_hub.contracts import (
    CURATION_SCHEMA_VERSION,
    ContextHubConflictError,
    ContextHubValidationError,
)

from backend.modules.context_hub.curation_records import (
    _curation_row,
    _ensure_curation_row,
    _find_curated_note,
    _public_curated_note,
    _read_curated_note,
    _refresh_curation_dashboard_best_effort,
    _validate_curated_content,
)

from backend.modules.context_hub.dlp_core import (
    _dlp_categories,
)

from backend.modules.context_hub.filesystem import (
    _write_text_atomic,
)

from backend.modules.context_hub.findings import (
    _has_blocker,
)

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _tenant_thread_lock,
)

from backend.modules.context_hub.metadata import (
    _dump_frontmatter,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
)

from backend.modules.context_hub.paths import (
    _tenant_paths,
)

from backend.modules.context_hub.runtime import (
    _new_id,
    _sha256_text,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def list_curated_notes(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    _refresh_curation_dashboard_best_effort(paths)
    notes: list[dict[str, Any]] = []
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for candidate in sorted(paths.curated_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
                try:
                    record = _read_curated_note(paths, candidate)
                except ContextHubValidationError:
                    continue
                row = _ensure_curation_row(
                    connection,
                    record["relative_path"],
                    record["content_sha256"],
                    str(record.get("metadata", {}).get("id") or ""),
                )
                notes.append(_public_curated_note(record, row))
            connection.commit()
    return {"success": True, "client_id": paths.client_id, "notes": notes, "count": len(notes)}


def _safe_actor(actor: object) -> str:
    value = re.sub(r"[^A-Za-z0-9@._+-]+", "_", str(actor or "admin").strip())[:120]
    value = value or "admin"
    if _dlp_categories(value):
        return "actor-" + _sha256_text(value)[:16]
    return value


def create_curated_note(
    client_id: object,
    *,
    title: object,
    body: object,
    category: object = "Notas",
    actor: object = "admin",
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    safe_title = str(title or "").strip()
    safe_body = str(body or "").replace("\r\n", "\n").strip()
    safe_category = str(category or "Notas").strip()
    if safe_category not in {"ADRs", "Regras", "Notas", "Black-Jhon"}:
        raise ContextHubValidationError("Categoria de curadoria invalida.")
    if not 1 <= len(safe_title) <= 160 or len(safe_body.encode("utf-8")) > 900_000:
        raise ContextHubValidationError("Titulo ou corpo da nota curada invalido.")
    slug = re.sub(r"[^a-z0-9]+", "-", safe_title.lower()).strip("-")[:100]
    if not slug:
        raise ContextHubValidationError("Titulo nao gera um nome de arquivo seguro.")
    relative_path = f"{safe_category}/{slug}.md"
    target = paths.curated_dir / relative_path
    _assert_path_chain_safe(target, paths.curated_dir)
    now = _utc_now()
    metadata: dict[str, Any] = {
        "id": f"jk:curated:{_new_id()}",
        "type": "curated_note",
        "managed": False,
        "status": "draft",
        "ai_usage": "denied",
        "tenant_scope": f"tenant:{paths.client_id}",
        "sensitivity": "internal",
        "truth_class": "human_curated",
        "required_permissions": ["full"],
        "surface": "all",
        "source_version": "curation-v2",
        "source_refs": [f"80_Curadoria/{relative_path}"],
        "source_hash": _sha256_text(safe_body),
        "generated_at": now,
        "context_schema": CURATION_SCHEMA_VERSION,
        "title": safe_title,
        "module": "curadoria",
        "authority": "advisory",
        "created_by": _safe_actor(actor),
    }
    content = _dump_frontmatter(metadata, safe_body or f"# {safe_title}")
    findings = _validate_curated_content(metadata, safe_body or f"# {safe_title}", source_ref=relative_path)
    if _has_blocker(findings):
        raise ContextHubValidationError("Nota curada reprovada pela validacao/DLP.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        if target.exists():
            raise ContextHubConflictError("Ja existe uma nota curada com esse titulo.")
        _write_text_atomic(target, content)
        record = _read_curated_note(paths, target)
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _ensure_curation_row(
                connection,
                record["relative_path"],
                record["content_sha256"],
                str(record.get("metadata", {}).get("id") or ""),
            )
            connection.commit()
    _refresh_curation_dashboard_best_effort(paths)
    return {"success": True, "client_id": paths.client_id, "note": _public_curated_note(record, row)}


def _curation_transition(
    client_id: object,
    note_id: object,
    action: str,
    *,
    actor: object,
    reason: object = "",
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    safe_actor = _safe_actor(actor)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _, record = _find_curated_note(paths, note_id)
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _ensure_curation_row(
                connection,
                record["relative_path"],
                record["content_sha256"],
                str(record.get("metadata", {}).get("id") or ""),
            )
            state = str(row["state"])
            now = _utc_now()
            if action == "validate":
                if not record["valid"]:
                    connection.rollback()
                    raise ContextHubValidationError("Nota curada reprovada pela validacao/DLP.")
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals
                    SET validated_sha256=?, validated_at=?, updated_at=? WHERE relative_path=?
                    """,
                    (record["content_sha256"], now, now, record["relative_path"]),
                )
            elif action == "review":
                if not record["valid"] or str(row["validated_sha256"] or "") != record["content_sha256"]:
                    connection.rollback()
                    raise ContextHubValidationError("Valide a versao atual antes da revisao.")
                if state not in {"draft", "rejected"}:
                    connection.rollback()
                    raise ContextHubConflictError("Transicao de revisao invalida.")
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals SET state='reviewed', reviewed_by=?,
                    reviewed_at=?, rejected_by=NULL, rejected_at=NULL, rejection_reason=NULL,
                    updated_at=? WHERE relative_path=?
                    """,
                    (safe_actor, now, now, record["relative_path"]),
                )
            elif action == "approve":
                if state != "reviewed" or not record["valid"]:
                    connection.rollback()
                    raise ContextHubConflictError("Somente a versao revisada pode ser aprovada.")
                lifecycle = str(record.get("metadata", {}).get("lifecycle") or "").strip().casefold()
                if lifecycle == "superseded":
                    connection.rollback()
                    raise ContextHubValidationError("Nota historica substituida nao pode ser aprovada.")
                if str(row["validated_sha256"] or "") != record["content_sha256"]:
                    connection.rollback()
                    raise ContextHubConflictError("A nota mudou depois da validacao.")
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals SET state='approved', approved_by=?,
                    approved_at=?, content_sha256=?, updated_at=? WHERE relative_path=?
                    """,
                    (safe_actor, now, record["content_sha256"], now, record["relative_path"]),
                )
            elif action == "reject":
                safe_reason = str(reason or "").strip()[:500]
                if not safe_reason or _dlp_categories(safe_reason):
                    connection.rollback()
                    raise ContextHubValidationError("Motivo de rejeicao invalido.")
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals SET state='rejected', rejected_by=?,
                    rejected_at=?, rejection_reason=?, approved_by=NULL, approved_at=NULL,
                    updated_at=? WHERE relative_path=?
                    """,
                    (safe_actor, now, safe_reason, now, record["relative_path"]),
                )
            else:
                connection.rollback()
                raise ContextHubValidationError("Acao de curadoria invalida.")
            row = _curation_row(connection, record["relative_path"])
            connection.commit()
    _refresh_curation_dashboard_best_effort(paths)
    return {"success": True, "client_id": paths.client_id, "note": _public_curated_note(record, row)}


def validate_curated_note(client_id: object, note_id: object, *, actor: object = "admin", info_root: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    return _curation_transition(client_id, note_id, "validate", actor=actor, info_root=info_root)


def review_curated_note(client_id: object, note_id: object, *, actor: object = "admin", info_root: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    return _curation_transition(client_id, note_id, "review", actor=actor, info_root=info_root)


def approve_curated_note(client_id: object, note_id: object, *, actor: object = "admin", info_root: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    return _curation_transition(client_id, note_id, "approve", actor=actor, info_root=info_root)


def reject_curated_note(client_id: object, note_id: object, *, actor: object = "admin", reason: object, info_root: Optional[os.PathLike[str] | str] = None) -> dict[str, Any]:
    return _curation_transition(client_id, note_id, "reject", actor=actor, reason=reason, info_root=info_root)
