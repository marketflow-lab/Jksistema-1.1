"""Context Hub curation records component."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Optional,
    Sequence,
)



from backend.modules.context_hub.contracts import (
    CURATION_SCHEMA_VERSION,
    CURATION_DASHBOARD_RELATIVE_PATH,
    ContextHubError,
    ContextHubNotFoundError,
    ContextHubPaths,
    ContextHubValidationError,
    FRONTMATTER_REQUIRED,
)

from backend.modules.context_hub.store_sku_contracts import (
    STORE_SKU_PUBLIC_SURFACE,
    StoreSkuScope,
    normalize_sku,
)

from backend.modules.context_hub.dlp import (
    scan_dlp,
)

from backend.modules.context_hub.dlp_core import (
    _dlp_document_text,
)

from backend.modules.context_hub.filesystem import (
    _write_text_atomic,
)

from backend.modules.context_hub.findings import (
    _finding,
    _has_blocker,
)

from backend.services.context_inventory import api as context_inventory_api

from backend.modules.context_hub.locking import (
    _exclusive_file_lock,
    _tenant_thread_lock,
)

from backend.modules.context_hub.metadata import (
    _obsidian_wikilink,
    _parse_frontmatter,
    _safe_identifier,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_link_or_junction,
    _is_relative_to,
)

from backend.modules.context_hub.runtime import (
    _sha256_text,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _fallback_render_inventory(inventory: Mapping[str, Any]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for entity in inventory.get("entities") or []:
        if not isinstance(entity, Mapping):
            continue
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            continue
        kind = re.sub(r"[^a-z0-9]+", "-", str(entity.get("kind") or "entity").lower()).strip("-") or "entity"
        slug = re.sub(r"[^a-z0-9]+", "-", entity_id.lower()).strip("-")[:120]
        title = str(entity.get("title") or entity_id)
        body = [f"# {title}", "", f"Identificador: `{entity_id}`."]
        rendered[f"Contratos/{kind}/{slug}.md"] = "\n".join(body) + "\n"
    return rendered


def _render_inventory(inventory: Mapping[str, Any]) -> dict[str, str]:
    renderer = context_inventory_api.render_context_inventory_markdown
    if callable(renderer):
        rendered = renderer(dict(inventory))
        if isinstance(rendered, Mapping):
            return {str(path): str(content) for path, content in rendered.items()}
    return _fallback_render_inventory(inventory)


def _curated_note_id(relative_path: str) -> str:
    return _sha256_text(relative_path.replace("\\", "/").lower())[:24]


def _curated_relative(candidate: Path, paths: ContextHubPaths) -> str:
    _assert_path_chain_safe(candidate, paths.info_root)
    resolved = candidate.resolve(strict=True)
    curated_root = paths.curated_dir.resolve(strict=True)
    if not _is_relative_to(resolved, curated_root):
        raise ContextHubValidationError("Nota fora de 80_Curadoria.")
    relative = candidate.relative_to(paths.curated_dir).as_posix()
    if candidate.suffix.lower() != ".md" or ".." in Path(relative).parts:
        raise ContextHubValidationError("Caminho de nota curada invalido.")
    return relative


def _validate_curated_content(
    metadata: Mapping[str, Any],
    body: str,
    *,
    source_ref: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    schema = metadata.get("context_schema", metadata.get("schema_version", 1))
    if schema not in {1, 2, 3, "1", "2", "3"}:
        findings.append(_finding("curated_schema_unsupported", category="curation", source_ref=source_ref))
    missing = [field for field in FRONTMATTER_REQUIRED if field not in metadata]
    if missing:
        findings.append(
            _finding(
                "curated_frontmatter_incomplete",
                category="curation",
                source_ref=source_ref,
                count=len(missing),
            )
        )
    if metadata.get("managed") not in {False, "false", 0}:
        findings.append(_finding("curated_note_managed_invalid", category="curation", source_ref=source_ref))
    try:
        schema_number = int(schema)
    except (TypeError, ValueError):
        schema_number = 0
    if schema_number == CURATION_SCHEMA_VERSION:
        scoped_required = (
            "scope_kind", "store_ref", "seller_id", "site_id", "sku",
            "knowledge_role", "content_hash",
        )
        if any(field not in metadata for field in scoped_required):
            findings.append(
                _finding("curated_store_scope_incomplete", category="curation", source_ref=source_ref)
            )
        else:
            scope_kind = str(metadata.get("scope_kind") or "").strip()
            role = str(metadata.get("knowledge_role") or "").strip()
            sku = str(metadata.get("sku") or "").strip()
            try:
                StoreSkuScope.from_mapping(
                    metadata,
                    tenant_scope=metadata.get("tenant_scope"),
                )
                if scope_kind not in {"store", "store_sku"}:
                    raise ValueError("scope_kind")
                if role not in {"store_guidance", "sku_guidance"}:
                    raise ValueError("knowledge_role")
                if scope_kind == "store_sku":
                    normalize_sku(sku)
                    if role != "sku_guidance":
                        raise ValueError("knowledge_role")
                elif sku or role != "store_guidance":
                    raise ValueError("store_guidance")
                if str(metadata.get("surface") or "") != STORE_SKU_PUBLIC_SURFACE:
                    raise ValueError("surface")
                if not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("content_hash") or "")):
                    raise ValueError("content_hash")
            except (ContextHubValidationError, ValueError):
                findings.append(
                    _finding("curated_store_scope_invalid", category="curation", source_ref=source_ref)
                )
    doc_id = _safe_identifier(metadata.get("id"), fallback="")
    if not isinstance(metadata.get("id"), str) or not doc_id:
        findings.append(_finding("curated_id_invalid", category="curation", source_ref=source_ref))
    lifecycle = str(metadata.get("lifecycle") or "").strip().casefold()
    if lifecycle and lifecycle not in {"current", "superseded"}:
        findings.append(
            _finding("curated_lifecycle_invalid", category="curation", source_ref=source_ref)
        )
    superseded_by = metadata.get("superseded_by")
    if lifecycle == "superseded" and (
        not isinstance(superseded_by, str)
        or not _safe_identifier(superseded_by, fallback="")
    ):
        findings.append(
            _finding("curated_superseded_target_missing", category="curation", source_ref=source_ref)
        )
    valid_dates: dict[str, str] = {}
    for field in ("valid_from", "valid_to"):
        raw_date = metadata.get(field)
        if raw_date is None or raw_date == "":
            continue
        value = str(raw_date).strip()
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            findings.append(
                _finding("curated_validity_date_invalid", category="curation", source_ref=source_ref)
            )
        else:
            valid_dates[field] = value
    if valid_dates.get("valid_from", "") > valid_dates.get("valid_to", "9999-12-31"):
        findings.append(
            _finding("curated_validity_range_invalid", category="curation", source_ref=source_ref)
        )
    if not body.strip():
        findings.append(_finding("curated_body_empty", category="curation", source_ref=source_ref))
    findings.extend(scan_dlp(_dlp_document_text(metadata, body), source_ref=source_ref))
    return findings


def _curation_row(connection: sqlite3.Connection, relative_path: str) -> Optional[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM context_hub_curated_approvals WHERE relative_path=?",
        (relative_path,),
    ).fetchone()


def _ensure_curation_row(
    connection: sqlite3.Connection,
    relative_path: str,
    content_sha256: str,
    document_id: str = "",
) -> sqlite3.Row:
    now = _utc_now()
    safe_document_id = _safe_identifier(document_id, fallback="")
    if safe_document_id:
        connection.execute(
            """
            UPDATE context_hub_curated_approvals
            SET present=0, missing_at=COALESCE(missing_at, ?), updated_at=?
            WHERE document_id=? AND relative_path<>? AND present=1
            """,
            (now, now, safe_document_id, relative_path),
        )
    row = _curation_row(connection, relative_path)
    if row is None:
        connection.execute(
            """
            INSERT INTO context_hub_curated_approvals(
                relative_path, document_id, content_sha256, state, present, missing_at, updated_at
            ) VALUES (?, ?, ?, 'draft', 1, NULL, ?)
            """,
            (relative_path, safe_document_id, content_sha256, now),
        )
    elif str(row["content_sha256"]) != content_sha256 or not bool(row["present"]):
        # Any edit, including frontmatter-only edits in Obsidian, invalidates
        # validation/review/approval atomically. Moving a note back to a path
        # used in the past is also a new draft, even when its bytes match.
        connection.execute(
            """
            UPDATE context_hub_curated_approvals SET
                document_id=?, content_sha256=?, state='draft', present=1, missing_at=NULL,
                validated_sha256=NULL,
                validated_at=NULL, reviewed_by=NULL, reviewed_at=NULL,
                approved_by=NULL, approved_at=NULL, rejected_by=NULL,
                rejected_at=NULL, rejection_reason=NULL, updated_at=?
            WHERE relative_path=?
            """,
            (safe_document_id, content_sha256, now, relative_path),
        )
    else:
        connection.execute(
            """
            UPDATE context_hub_curated_approvals
            SET document_id=?, present=1, missing_at=NULL
            WHERE relative_path=?
            """,
            (safe_document_id, relative_path),
        )
    return _curation_row(connection, relative_path)  # type: ignore[return-value]


def _read_curated_note(paths: ContextHubPaths, candidate: Path) -> dict[str, Any]:
    relative_path = _curated_relative(candidate, paths)
    try:
        if candidate.stat().st_size > 1_000_000:
            raise ContextHubValidationError("Nota curada excede 1 MB.")
        content = candidate.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError) as error:
        raise ContextHubValidationError("Nota curada indisponivel para leitura.") from error
    metadata, body = _parse_frontmatter(content)
    content_sha256 = _sha256_text(content)
    findings = _validate_curated_content(metadata, body, source_ref=relative_path)
    return {
        "note_id": _curated_note_id(relative_path),
        "relative_path": relative_path,
        "content_sha256": content_sha256,
        "metadata": metadata,
        "body": body,
        "findings": findings,
        "valid": not _has_blocker(findings),
    }


def _find_curated_note(paths: ContextHubPaths, note_id: object) -> tuple[Path, dict[str, Any]]:
    normalized_id = str(note_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{24}", normalized_id):
        raise ContextHubNotFoundError("Nota curada nao encontrada.")
    for candidate in sorted(paths.curated_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        try:
            record = _read_curated_note(paths, candidate)
        except ContextHubValidationError:
            continue
        if record["note_id"] == normalized_id:
            return candidate, record
    raise ContextHubNotFoundError("Nota curada nao encontrada.")


def _public_curated_note(record: Mapping[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    metadata = dict(record.get("metadata") or {})
    try:
        schema_version = int(metadata.get("context_schema") or metadata.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 0
    return {
        "note_id": record["note_id"],
        "relative_path": record["relative_path"],
        "title": str(metadata.get("title") or Path(str(record["relative_path"])).stem),
        "document_id": str(metadata.get("id") or ""),
        "schema_version": schema_version,
        "state": str(row["state"]),
        "content_sha256": record["content_sha256"],
        "valid": bool(record["valid"]),
        "findings": record["findings"],
        "validated_at": row["validated_at"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "rejected_by": row["rejected_by"],
        "rejected_at": row["rejected_at"],
        "rejection_reason": row["rejection_reason"],
    }


def _curation_dashboard_lifecycle(metadata: Mapping[str, Any]) -> str:
    lifecycle = str(metadata.get("lifecycle") or "").strip().casefold()
    if lifecycle == "superseded":
        return "Substituida"
    valid_from = str(metadata.get("valid_from") or "").strip()
    valid_to = str(metadata.get("valid_to") or "").strip()
    if valid_from and valid_to:
        return "Vigencia definida"
    if valid_from:
        return "Vigente desde data definida"
    if valid_to:
        return "Valida ate data definida"
    return "Sem prazo"


def _render_curation_dashboard(paths: ContextHubPaths, notes: Sequence[Mapping[str, Any]]) -> str:
    state_labels = {
        "draft": "Rascunho",
        "reviewed": "Revisada",
        "approved": "Aprovada",
        "rejected": "Rejeitada",
        "unavailable": "Indisponivel",
    }
    counts = {state: 0 for state in state_labels}
    superseded_count = 0
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for note in notes:
        state = str(note.get("state") or "unavailable")
        if bool(note.get("superseded")):
            superseded_count += 1
        else:
            counts[state if state in counts else "unavailable"] += 1
        grouped.setdefault(str(note.get("category") or "Outras"), []).append(note)

    lines = [
        "---",
        "id: jk:navigation:curation-dashboard",
        "type: navigation",
        "managed: true",
        "ai_usage: denied",
        f"tenant_scope: tenant:{paths.client_id}",
        "---",
        "",
        "# Painel de Curadoria",
        "",
        "> Painel automatico. O estado vem do fluxo interno; este arquivo nao aprova nem publica notas.",
        "",
        "## Resumo",
        "",
        f"- Total: {len(notes)}",
        f"- Rascunhos ativos: {counts['draft']}",
        f"- Revisadas: {counts['reviewed']}",
        f"- Aprovadas: {counts['approved']}",
        f"- Rejeitadas: {counts['rejected']}",
        f"- Substituidas: {superseded_count}",
    ]
    if counts["unavailable"]:
        lines.append(f"- Indisponiveis: {counts['unavailable']}")
    home = paths.vault_dir / "00_Inicio" / "Inicio.md"
    if home.is_file() and not _is_link_or_junction(home):
        lines.extend(["", "- " + _obsidian_wikilink("00_Inicio/Inicio.md", "Voltar ao Inicio")])

    for category in sorted(grouped, key=str.casefold):
        safe_category = re.sub(r"[\[\]|\r\n]+", " ", category).strip()[:120] or "Outras"
        if scan_dlp(safe_category, source_ref="curation-dashboard"):
            safe_category = "Outras"
        lines.extend(
            [
                "",
                f"## {safe_category}",
                "",
                "| Nota | Estado | Contrato | Vigencia | Autoridade |",
                "|---|---|---|---|---|",
            ]
        )
        for note in sorted(grouped[category], key=lambda item: str(item.get("sort_key") or "")):
            lines.append(
                "| {label} | {state} | {contract} | {lifecycle} | Consultiva |".format(
                    label=note["label"],
                    state=state_labels.get(str(note.get("state") or ""), "Indisponivel"),
                    contract=note["contract"],
                    lifecycle=note["lifecycle"],
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def _refresh_curation_dashboard(paths: ContextHubPaths) -> bool:
    dashboard_path = paths.vault_dir / CURATION_DASHBOARD_RELATIVE_PATH
    _assert_path_chain_safe(dashboard_path, paths.info_root)
    notes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for candidate in sorted(
                paths.curated_dir.rglob("*.md"), key=lambda item: item.as_posix().casefold()
            ):
                relative = candidate.relative_to(paths.curated_dir).as_posix()
                seen_paths.add(relative)
                try:
                    record = _read_curated_note(paths, candidate)
                except ContextHubValidationError:
                    notes.append(
                        {
                            "category": "Outras",
                            "sort_key": relative.casefold(),
                            "label": "Nota retida",
                            "state": "unavailable",
                            "contract": "Bloqueada",
                            "lifecycle": "Indisponivel",
                            "superseded": False,
                        }
                    )
                    continue
                row = _ensure_curation_row(
                    connection,
                    record["relative_path"],
                    record["content_sha256"],
                    str(record.get("metadata", {}).get("id") or ""),
                )
                metadata = dict(record.get("metadata") or {})
                relative = str(record["relative_path"])
                title = str(metadata.get("title") or Path(relative).stem)
                safe_surface = title + "\n" + relative
                if scan_dlp(safe_surface, source_ref="curation-dashboard"):
                    label = "Nota retida"
                    category = "Outras"
                else:
                    try:
                        label = _obsidian_wikilink(
                            f"80_Curadoria/{relative}",
                            title,
                            table_cell=True,
                        )
                        category = Path(relative).parts[0] if Path(relative).parts else "Outras"
                    except ContextHubValidationError:
                        label = "Nota retida"
                        category = "Outras"
                lifecycle = _curation_dashboard_lifecycle(metadata)
                contract = "Valida" if record["valid"] else "Bloqueada"
                is_superseded = (
                    str(metadata.get("lifecycle") or "").strip().casefold() == "superseded"
                )
                if is_superseded:
                    contract = "Somente historico"
                notes.append(
                    {
                        "category": category,
                        "sort_key": relative.casefold(),
                        "label": label,
                        "state": str(row["state"]),
                        "contract": contract,
                        "lifecycle": lifecycle,
                        "superseded": is_superseded,
                    }
                )
            now = _utc_now()
            for row in connection.execute(
                "SELECT relative_path FROM context_hub_curated_approvals WHERE present=1"
            ).fetchall():
                relative_path = str(row["relative_path"])
                if relative_path not in seen_paths:
                    connection.execute(
                        """
                        UPDATE context_hub_curated_approvals
                        SET present=0, missing_at=COALESCE(missing_at, ?), updated_at=?
                        WHERE relative_path=?
                        """,
                        (now, now, relative_path),
                    )
            connection.commit()
        content = _render_curation_dashboard(paths, notes)
        try:
            current = dashboard_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except FileNotFoundError:
            current = ""
        if current == content:
            return False
        _write_text_atomic(dashboard_path, content)
    return True


def _refresh_curation_dashboard_best_effort(paths: ContextHubPaths) -> Optional[bool]:
    try:
        return _refresh_curation_dashboard(paths)
    except (ContextHubError, OSError, UnicodeError, sqlite3.Error):
        # The dashboard is derived navigation. A temporary Obsidian lock or an
        # unsafe path must never roll back a completed curation transition.
        return None
