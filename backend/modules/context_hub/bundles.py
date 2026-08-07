"""Context Hub bundles component."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import (
    Any,
    Optional,
)



from backend.modules.context_hub.contracts import (
    CONTEXT_BUNDLE_REQUIRED_PATHS,
    ContextHubRuntimeConfig,
    ContextHubValidationError,
)

from backend.modules.context_hub.dlp import (
    scan_dlp,
)

from backend.modules.context_hub.dlp_core import (
    _dlp_document_text,
)

from backend.modules.context_hub.findings import (
    _finding,
)

from backend.modules.context_hub.metadata import (
    _dump_frontmatter,
    _parse_frontmatter,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_relative_to,
)

from backend.modules.context_hub.runtime import (
    _sha256_bytes,
    _sha256_text,
)


def _validated_bundle_manifest(
    config: ContextHubRuntimeConfig,
    expected_source_version: str,
) -> tuple[Optional[dict[str, Any]], Optional[Path], list[dict[str, Any]]]:
    manifest_path = config.base_dir / "context-bundle-manifest.json"
    knowledge_root = config.base_dir / "docs" / "knowledge"
    if not manifest_path.is_file():
        return None, None, [_finding("context_bundle_manifest_missing", category="bundle")]
    try:
        _assert_path_chain_safe(manifest_path, config.base_dir)
        if manifest_path.stat().st_size > 1_000_000:
            raise ValueError("manifest_size")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ContextHubValidationError):
        return None, None, [_finding("context_bundle_manifest_invalid", category="bundle")]
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "source_version", "files"}:
        return None, None, [_finding("context_bundle_manifest_contract_invalid", category="bundle")]
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        return None, None, [_finding("context_bundle_manifest_schema_unsupported", category="bundle")]
    if not manifest["files"]:
        return None, None, [_finding("context_bundle_manifest_empty", category="bundle")]
    manifest_version = str(manifest.get("source_version") or "").strip()
    if manifest_version != expected_source_version:
        return None, None, [_finding("context_bundle_version_mismatch", category="bundle")]
    declared_paths = {
        str(entry.get("path") or "").replace("\\", "/")
        for entry in manifest["files"]
        if isinstance(entry, dict)
    }
    if not CONTEXT_BUNDLE_REQUIRED_PATHS.issubset(declared_paths):
        return None, None, [_finding("context_bundle_required_entries_missing", category="bundle")]
    try:
        knowledge_resolved = knowledge_root.resolve(strict=True)
        _assert_path_chain_safe(knowledge_resolved, config.base_dir)
    except (OSError, ContextHubValidationError):
        return None, None, [_finding("context_bundle_root_missing", category="bundle")]
    return manifest, knowledge_resolved, []


def _load_context_bundle(
    config: ContextHubRuntimeConfig,
    *,
    client_id: str,
    expected_source_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    """Load only Markdown files authorized by the root bundle manifest."""

    manifest, knowledge_resolved, manifest_findings = _validated_bundle_manifest(
        config,
        expected_source_version,
    )
    if manifest is None or knowledge_resolved is None:
        return [], manifest_findings, []

    documents: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    hashes: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    total_size = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            findings.append(_finding("context_bundle_entry_invalid", category="bundle"))
            continue
        raw_path = str(entry.get("path") or "").replace("\\", "/")
        relative = Path(raw_path)
        expected_hash = str(entry.get("sha256") or "").lower()
        expected_size = entry.get("size")
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) < 3
            or tuple(part.lower() for part in relative.parts[:2]) != ("docs", "knowledge")
            or relative.suffix.lower() != ".md"
            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
            or not isinstance(expected_size, int)
            or not 0 <= expected_size <= 1_000_000
        ):
            findings.append(_finding("context_bundle_entry_invalid", category="bundle"))
            continue
        path_key = relative.as_posix().lower()
        if path_key in seen_paths:
            findings.append(_finding("context_bundle_path_duplicate", category="bundle"))
            continue
        seen_paths.add(path_key)
        total_size += expected_size
        if total_size > 10_000_000:
            findings.append(_finding("context_bundle_size_exceeded", category="bundle"))
            break
        candidate = config.base_dir / relative
        try:
            _assert_path_chain_safe(candidate, config.base_dir)
            resolved = candidate.resolve(strict=True)
            if not _is_relative_to(resolved, knowledge_resolved):
                raise ContextHubValidationError("Arquivo fora do bundle.")
            stat = resolved.stat()
            if stat.st_size != expected_size:
                findings.append(_finding("context_bundle_size_mismatch", category="bundle", source_ref=relative.as_posix()))
                continue
            data = resolved.read_bytes()
            if _sha256_bytes(data) != expected_hash:
                findings.append(_finding("context_bundle_hash_mismatch", category="bundle", source_ref=relative.as_posix()))
                continue
            content = data.decode("utf-8")
        except (OSError, UnicodeError, ContextHubValidationError):
            findings.append(_finding("context_bundle_file_unreadable", category="bundle", source_ref=relative.as_posix()))
            continue
        raw_metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        title_match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
        title = title_match.group(1).strip() if title_match else relative.stem.replace("-", " ")
        doc_id = "jk:bundle:" + re.sub(
            r"[^a-z0-9]+", "-", "/".join(relative.parts[2:]).lower()
        ).strip("-")
        metadata = {
            "id": doc_id,
            "type": "technical_knowledge",
            "managed": True,
            "status": "published",
            "ai_usage": "allowed",
            "tenant_scope": f"tenant:{client_id}",
            "sensitivity": "internal",
            "truth_class": str(raw_metadata.get("truth_class") or "versioned_technical"),
            "required_permissions": ["full"],
            "surface": config.surface,
            "source_version": expected_source_version,
            "source_refs": [relative.as_posix()],
            "source_hash": expected_hash,
            "generated_at": "1970-01-01T00:00:00+00:00",
            "title": title,
            "module": "knowledge",
        }
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative.as_posix())
        if dlp:
            findings.extend(dlp)
            continue
        documents.append(
            {
                "metadata": metadata,
                "relative_path": "@bundle/" + "/".join(relative.parts[2:]),
                "content": _dump_frontmatter(metadata, body),
                "body": body,
            }
        )
        hashes.append((relative.as_posix(), expected_hash))
    return documents, findings, hashes


def _semantic_markdown_blocks(body: str) -> list[tuple[str, str]]:
    """Split Markdown without cutting headings, paragraphs, tables or fields."""

    lines = body.replace("\r\n", "\n").splitlines()
    headings: dict[int, str] = {}
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            level = len(heading.group(1))
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = line
            blocks.append(("heading", line))
            index += 1
            continue
        context = "\n".join(headings[key] for key in sorted(headings))
        if line.lstrip().startswith("|"):
            table_lines = [line]
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index].rstrip())
                index += 1
            value = "\n".join(table_lines)
            blocks.append(("table", f"{context}\n\n{value}".strip()))
            continue
        if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|[A-Za-zÀ-ÿ0-9_. -]{1,80}:\s+)", line):
            field_lines = [line]
            index += 1
            while index < len(lines):
                current = lines[index].rstrip()
                if not current.strip() or re.match(r"^#{1,6}\s+", current) or current.lstrip().startswith("|"):
                    break
                if not re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|[A-Za-zÀ-ÿ0-9_. -]{1,80}:\s+)", current):
                    break
                field_lines.append(current)
                index += 1
            blocks.append(("fields", f"{context}\n\n" + "\n".join(field_lines) if context else "\n".join(field_lines)))
            continue
        paragraph = [line]
        index += 1
        while index < len(lines):
            current = lines[index].rstrip()
            if not current.strip():
                index += 1
                break
            if re.match(r"^#{1,6}\s+", current) or current.lstrip().startswith("|"):
                break
            paragraph.append(current)
            index += 1
        value = "\n".join(paragraph)
        blocks.append(("paragraph", f"{context}\n\n{value}".strip()))
    return blocks


def _split_semantic_block(value: str, maximum: int) -> list[str]:
    if len(value) <= maximum:
        return [value]
    lines = value.splitlines()
    parts: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and len("\n".join(current + [line])) > maximum:
            parts.append("\n".join(current).strip())
            current = []
        if len(line) > maximum:
            if current:
                parts.append("\n".join(current).strip())
                current = []
            parts.extend(line[offset : offset + maximum] for offset in range(0, len(line), maximum))
        else:
            current.append(line)
    if current:
        parts.append("\n".join(current).strip())
    return [part for part in parts if part]


def _chunks_for_document(doc_id: str, body: str, *, maximum: int = 1800, overlap: int = 0) -> list[dict[str, Any]]:
    del overlap  # v2 chunks on semantic boundaries; it never overlaps text.
    normalized = re.sub(r"\n{3,}", "\n\n", body.strip())
    if not normalized:
        return []
    chunks: list[tuple[str, str]] = []
    for semantic_type, block in _semantic_markdown_blocks(normalized):
        for part in _split_semantic_block(block, maximum):
            chunks.append((semantic_type, part))
    result: list[dict[str, Any]] = []
    for ordinal, (semantic_type, content) in enumerate(chunks):
        content_hash = _sha256_text(content)
        result.append(
            {
                "chunk_id": _sha256_text(f"{doc_id}:{ordinal}:{content_hash}")[:32],
                "ordinal": ordinal,
                "content": content,
                "content_hash": content_hash,
                "semantic_type": semantic_type,
            }
        )
    return result
