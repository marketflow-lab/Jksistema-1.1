"""Context Hub metadata component."""

from __future__ import annotations

import re
from pathlib import Path
from typing import (
    Any,
    Mapping,
)

import yaml


from backend.modules.context_hub.contracts import (
    ContextHubValidationError,
    FRONTMATTER_REQUIRED,
)

from backend.modules.context_hub.findings import (
    _strip_volatile,
)

from backend.modules.context_hub.runtime import (
    _json_canonical,
    _sha256_text,
)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n") and not content.startswith("---\r\n"):
        return {}, content
    lines = content.splitlines()
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return {}, content
    raw = "\n".join(lines[1:closing])
    try:
        metadata = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, content
    if not isinstance(metadata, dict):
        return {}, content
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return {str(key): value for key, value in metadata.items()}, body


def _dump_frontmatter(metadata: Mapping[str, Any], body: str) -> str:
    ordered = {key: metadata[key] for key in FRONTMATTER_REQUIRED}
    extras = {str(key): value for key, value in metadata.items() if str(key) not in ordered}
    ordered.update(dict(sorted(extras.items())))
    yaml_text = yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{body.rstrip()}\n"


def _safe_relative_markdown_path(raw_path: object) -> Path:
    value = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    relative = Path(value)
    if relative.suffix.lower() != ".md" or relative.is_absolute() or ".." in relative.parts:
        raise ContextHubValidationError("O inventario produziu um caminho Markdown invalido.")
    if not relative.parts or relative.parts[0].lower() != "70_gerado":
        relative = Path("70_Gerado") / relative
    if relative.parts[0] != "70_Gerado":
        relative = Path("70_Gerado") / Path(*relative.parts[1:])
    return relative


def _obsidian_wikilink(
    relative_path: object,
    label: object,
    *,
    table_cell: bool = False,
) -> str:
    value = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
    relative = Path(value)
    if relative.suffix.lower() != ".md" or relative.is_absolute() or ".." in relative.parts:
        raise ContextHubValidationError("Caminho Markdown invalido para navegacao.")
    target = relative.as_posix()[:-3]
    if any(character in target for character in "[]|"):
        raise ContextHubValidationError("Caminho Markdown invalido para navegacao.")
    safe_label = re.sub(r"[\\\[\]|\r\n]+", " ", str(label or relative.stem)).strip()[:160]
    # Markdown tables use an unescaped pipe as a cell delimiter. Obsidian's
    # wikilink parser recognizes the escaped alias separator inside a table.
    separator = r"\|" if table_cell else "|"
    return f"[[{target}{separator}{safe_label or relative.stem}]]"


def _safe_identifier(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= 300 and not any(character in normalized for character in "\r\n\x00"):
        return normalized
    return fallback


def _normalize_source_refs(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    refs: list[str] = []
    for item in values:
        ref = str(item or "").strip().replace("\\", "/")
        if ref and len(ref) <= 300 and ".." not in Path(ref).parts and not Path(ref).is_absolute():
            refs.append(ref)
    return sorted(set(refs))


def _entity_index(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for entity in inventory.get("entities") or []:
        if isinstance(entity, Mapping) and entity.get("id"):
            result[str(entity["id"])] = entity
    return result


def _normalize_generated_note(
    relative_path: Path,
    raw_content: str,
    *,
    inventory: Mapping[str, Any],
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> tuple[dict[str, Any], str, str]:
    raw_metadata, body = _parse_frontmatter(raw_content.replace("\r\n", "\n"))
    entities = _entity_index(inventory)
    entity_id = str(raw_metadata.get("id") or "")
    entity = entities.get(entity_id, {})
    fallback_id = "jk:generated:" + re.sub(r"[^a-z0-9]+", "-", relative_path.with_suffix("").as_posix().lower()).strip("-")
    entity_id = _safe_identifier(entity_id or entity.get("id"), fallback=fallback_id)
    title = _safe_identifier(raw_metadata.get("title") or entity.get("title"), fallback=relative_path.stem.replace("_", " "))
    source_refs = _normalize_source_refs(raw_metadata.get("source_refs") or entity.get("source_refs"))
    body = body.strip() or f"# {title}\n"
    source_hash = str(raw_metadata.get("source_hash") or entity.get("source_hash") or _sha256_text(body))
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_hash):
        source_hash = _sha256_text(_json_canonical(_strip_volatile(entity)) + "\n" + body)
    metadata: dict[str, Any] = {
        "id": entity_id,
        "type": _safe_identifier(raw_metadata.get("type") or entity.get("kind"), fallback="generated"),
        "managed": True,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": f"tenant:{client_id}",
        "sensitivity": _safe_identifier(raw_metadata.get("sensitivity") or entity.get("sensitivity"), fallback="internal"),
        "truth_class": _safe_identifier(raw_metadata.get("truth_class") or entity.get("truth_class"), fallback="generated_verified"),
        "required_permissions": ["full"],
        "surface": surface,
        "source_version": source_version,
        "source_refs": source_refs,
        "source_hash": source_hash.lower(),
        "generated_at": generated_at,
        "title": title,
        "module": _safe_identifier(raw_metadata.get("module") or entity.get("domain"), fallback=""),
        "store_ref": _safe_identifier(raw_metadata.get("store_ref") or entity.get("store_ref"), fallback=""),
        "tags": sorted(
            {
                str(item).strip()[:80]
                for item in (
                    raw_metadata.get("tags")
                    if isinstance(raw_metadata.get("tags"), list)
                    else entity.get("tags")
                    if isinstance(entity.get("tags"), list)
                    else []
                )
                if str(item).strip()
            }
        )[:20],
        "valid_from": str(raw_metadata.get("valid_from") or entity.get("valid_from") or raw_metadata.get("valid_at") or "")[:40],
        "valid_to": str(raw_metadata.get("valid_to") or entity.get("valid_to") or raw_metadata.get("valid_at") or "")[:40],
    }
    return metadata, _dump_frontmatter(metadata, body), body
