"""Context Hub curated collection component."""

from __future__ import annotations

from typing import Any



from backend.modules.context_hub.contracts import (
    CURATION_SCHEMA_VERSION,
    ContextHubPaths,
    ContextHubValidationError,
    FRONTMATTER_REQUIRED,
)

from backend.modules.context_hub.curation_records import (
    _ensure_curation_row,
    _validate_curated_content,
)

from backend.modules.context_hub.findings import (
    _finding,
)

from backend.modules.context_hub.metadata import (
    _dump_frontmatter,
    _normalize_source_refs,
    _parse_frontmatter,
    _safe_identifier,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_relative_to,
)

from backend.modules.context_hub.runtime import (
    _sha256_text,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _collect_curated_notes(paths: ContextHubPaths) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    documents: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    hashes: list[tuple[str, str]] = []
    if not paths.curated_dir.exists():
        return documents, findings, hashes
    try:
        _assert_path_chain_safe(paths.curated_dir, paths.info_root)
    except ContextHubValidationError:
        return documents, [_finding("unsafe_curated_root", category="path")], hashes
    for candidate in sorted(paths.curated_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        try:
            _assert_path_chain_safe(candidate, paths.info_root)
            resolved = candidate.resolve()
            if not _is_relative_to(resolved, paths.curated_dir.resolve()):
                raise ContextHubValidationError("Nota fora de 80_Curadoria.")
            if candidate.stat().st_size > 1_000_000:
                findings.append(_finding("curated_note_too_large", category="curation", source_ref=candidate.name))
                continue
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ContextHubValidationError):
            findings.append(_finding("curated_note_unreadable", category="curation", source_ref=candidate.name))
            continue
        relative = candidate.relative_to(paths.vault_dir).as_posix()
        metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        content_hash = _sha256_text(content)
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = _ensure_curation_row(
                connection,
                candidate.relative_to(paths.curated_dir).as_posix(),
                content_hash,
                str(metadata.get("id") or ""),
            )
            connection.commit()
        if str(metadata.get("lifecycle") or "").strip().casefold() == "superseded":
            continue
        if str(row["state"]) != "approved" or str(row["content_sha256"]) != content_hash:
            continue
        hashes.append((relative, content_hash))
        validation = _validate_curated_content(metadata, body, source_ref=relative)
        if validation:
            findings.extend(validation)
            continue
        doc_id = _safe_identifier(metadata.get("id"), fallback="")
        metadata = dict(metadata)
        metadata["required_permissions"] = ["full"]
        metadata["tenant_scope"] = f"tenant:{paths.client_id}"
        metadata["truth_class"] = "human_curated"
        metadata["authority"] = "advisory"
        metadata["status"] = "approved"
        metadata["ai_usage"] = "allowed"
        metadata["source_refs"] = _normalize_source_refs(metadata.get("source_refs"))
        metadata["source_hash"] = _sha256_text(body)
        documents.append(
            {
                "metadata": metadata,
                "relative_path": relative,
                "content": _dump_frontmatter({key: metadata[key] for key in FRONTMATTER_REQUIRED} | {
                    "title": str(metadata.get("title") or candidate.stem),
                    "module": str(metadata.get("module") or "curadoria"),
                    "context_schema": int(metadata.get("context_schema") or 2),
                    "authority": "advisory",
                    **{
                        key: metadata[key]
                        for key in (
                            "scope_kind", "store_ref", "seller_id", "site_id", "sku",
                            "knowledge_role", "content_hash", "created_by", "reviewed_by",
                            "approved_by",
                        )
                        if key in metadata
                    },
                }, body),
                "body": body,
                # Internal publication attestation.  This value is persisted
                # outside the searchable document so a ready generation can
                # be rejected if its approved source changes before publish.
                "curation_content_sha256": content_hash,
            }
        )
    return documents, findings, hashes
