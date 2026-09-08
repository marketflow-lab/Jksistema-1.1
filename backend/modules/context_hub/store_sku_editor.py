"""Current Obsidian guidance and optimistic editing, independent of publication.

Callers must authorize the exact store and prove catalog membership before saving
a SKU. Drafts may precede that SKU's first published knowledge generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.contracts import ContextHubConflictError, ContextHubValidationError
from backend.modules.context_hub.curation_records import _ensure_curation_row, _read_curated_note, _validate_curated_content
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.findings import _has_blocker
from backend.modules.context_hub.guidance_examples import validate_guidance_examples
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.metadata import _dump_frontmatter, _parse_frontmatter
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import _utc_now
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_contracts import (
    APPLICABLE_GUIDANCE_MAX_CHARS, StoreSkuScope, content_sha256,
    normalize_sku, store_directory_name, validate_integral_size,
)
from backend.modules.context_hub.store_sku_repository_support import (
    _generation_knowledge, _guidance_body, _guidance_frontmatter,
    _opaque_actor, _scope_for_paths, _sku_path_component, _validate_no_dlp,
)

_JSON_BLOCK = re.compile(r"```json[^\S\n]*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


class StoreGuidanceEditorConflict(ContextHubConflictError):
    code = "editorial_revision_conflict"


def _scope_matches(metadata: Mapping[str, Any], scope: StoreSkuScope) -> bool:
    return all(str(metadata.get(key) or "").strip() == str(value) for key, value in {
        "tenant_scope": scope.tenant_scope, "store_ref": scope.store_ref,
        "seller_id": scope.seller_id, "site_id": scope.site_id, "surface": scope.surface,
    }.items())


def _path_slot(path: Path, paths: Any, scope: StoreSkuScope) -> str | None:
    """Recognize damaged files only in this store's identity-prefixed directory."""
    parts = path.relative_to(paths.curated_dir).parts
    if len(parts) < 3 or parts[0] != "Lojas" or not parts[1].startswith(scope.store_ref + "--"):
        return None
    if len(parts) == 3 and parts[2] == "Orientacoes-Gerais.md":
        return ""
    if len(parts) == 5 and parts[2] == "SKUs" and parts[4] == "Orientacoes.md":
        try:
            return normalize_sku(parts[3])
        except ContextHubValidationError:
            return None
    return None


def find_editor_note_paths(paths: Any, scope: StoreSkuScope) -> dict[str, list[Path]]:
    """Find exact identities regardless of display name or moved Markdown path.

Multiple current notes remain a conflict; never pick a winner by filename/date.
Damaged notes at canonical paths are included so they cannot be overwritten.
"""
    found: dict[str, list[Path]] = {}
    for path in sorted(paths.curated_dir.rglob("*.md")):
        slot = None
        try:
            record = _read_curated_note(paths, path)
            metadata = record["metadata"]
            if str(metadata.get("lifecycle") or "").casefold() == "superseded":
                continue
            if _scope_matches(metadata, scope):
                role = str(metadata.get("knowledge_role") or "")
                if role == "store_guidance" and not str(metadata.get("sku") or "").strip():
                    slot = ""
                elif role == "sku_guidance":
                    slot = normalize_sku(metadata.get("sku"))
            if slot is None:
                slot = _path_slot(path, paths, scope)
        except (ContextHubValidationError, OSError):
            slot = _path_slot(path, paths, scope)
        if slot is not None:
            found.setdefault(slot, []).append(path)
    return found


def _read_payload(body: str, sku: str) -> dict[str, Any]:
    blocks = list(_JSON_BLOCK.finditer(body))
    if blocks:
        if len(blocks) != 1:
            raise ContextHubValidationError("Nota possui mais de um bloco de orientacoes.")
        try:
            value = json.loads(blocks[0].group(1))
        except (ValueError, TypeError) as error:
            raise ContextHubValidationError("JSON de orientacoes invalido.") from error
        if not isinstance(value, dict):
            raise ContextHubValidationError("Orientacoes devem ser um objeto JSON.")
        field = "notas" if sku else "orientacoes_perguntas"
        aliases = ("texto", "orientacoes") if sku else ("orientacoes",)
        for alias in aliases:
            if field not in value and alias in value:
                value[field] = value[alias]
            value.pop(alias, None)
        return value
    if "```json" in body.casefold():
        raise ContextHubValidationError("Bloco JSON de orientacoes incompleto.")
    return {"notas" if sku else "orientacoes_perguntas": body} if body.strip() else {}


def _file_hash(path: Path, paths: Any) -> str:
    try:
        # Hash bytes so an external newline/encoding edit also invalidates CAS.
        _assert_path_chain_safe(path, paths.info_root)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ContextHubValidationError):
        return "unavailable"


def _active(paths: Any, scope: StoreSkuScope) -> dict[str, Any]:
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT generation_id FROM context_hub_store_sku_active_generations "
            "WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?",
            (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
        ).fetchone()
        generation = str(row["generation_id"]) if row else ""
        canonical, general, skus = _generation_knowledge(connection, generation) if row else ({}, {}, {})
    return {
        "generation_id": generation, "guidance": {"general": general},
        "sku_guidance": skus, "canonical_skus": sorted(canonical),
    }


def _snapshot(paths: Any, scope: StoreSkuScope) -> dict[str, Any]:
    published = _active(paths, scope)
    files = find_editor_note_paths(paths, scope)
    slots = set(files) | set(published["sku_guidance"]) | {""}
    general: dict[str, Any] = {}
    skus: dict[str, dict[str, Any]] = {}
    statuses: dict[str, dict[str, Any]] = {}
    with _connect(paths) as connection:
        for sku in sorted(slots):
            candidates = files.get(sku, [])
            active_value = published["sku_guidance"].get(sku, {}) if sku else published["guidance"]["general"]
            entry: dict[str, Any] = {"status": "deleted" if active_value else "missing", "hash": ""}
            if candidates:
                fingerprints = [(p.relative_to(paths.curated_dir).as_posix(), _file_hash(p, paths)) for p in candidates]
                entry["hash"] = fingerprints[0][1] if len(candidates) == 1 else content_sha256(fingerprints)
                entry["relative_path"] = fingerprints[0][0] if len(candidates) == 1 else ""
                entry["status"] = "conflict" if len(candidates) > 1 else "invalid"
                if len(candidates) == 1:
                    try:
                        record = _read_curated_note(paths, candidates[0])
                        entry["note_id"] = record["note_id"]
                        metadata = record["metadata"]
                        slot_matches = (
                            str(metadata.get("knowledge_role") or "") == ("sku_guidance" if sku else "store_guidance")
                            and str(metadata.get("scope_kind") or "") == ("store_sku" if sku else "store")
                            and str(metadata.get("sku") or "") == sku
                        )
                        if not record["valid"] or not slot_matches or not _scope_matches(metadata, scope):
                            raise ContextHubValidationError("Nota invalida ou com identidade divergente.")
                        value = _read_payload(record["body"], sku)
                        entry["source_body"] = record["body"]
                        entry["requires_catalog_sync"] = (
                            sku not in published["canonical_skus"] if sku else not bool(published["generation_id"])
                        )
                        row = connection.execute(
                            "SELECT state, content_sha256 FROM context_hub_curated_approvals WHERE relative_path=? AND present=1",
                            (record["relative_path"],),
                        ).fetchone()
                        state = str(row["state"]) if row and row["content_sha256"] == record["content_sha256"] else "draft"
                        entry["status"] = "published" if state == "approved" and value == active_value else state
                        if sku:
                            skus[sku] = value
                        else:
                            general = value
                    except (ContextHubValidationError, OSError, UnicodeError):
                        entry["error_code"] = "editorial_note_invalid"
            statuses[sku] = entry
    revision_notes = {sku: {key: value for key, value in entry.items() if key != "source_body"} for sku, entry in statuses.items()}
    revision = content_sha256({"scope": scope.as_dict(), "generation": published["generation_id"], "notes": revision_notes})
    return {
        "guidance": {"general": general}, "sku_guidance": skus,
        "generation_id": published["generation_id"], "published_guidance": published,
        "editorial": {"revision": revision, "general": statuses.pop(""), "skus": statuses},
    }


def load_store_guidance_editor(client_id: object, scope: Mapping[str, Any], *, info_root=None) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    exact_scope = _scope_for_paths(paths, scope)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        return _snapshot(paths, exact_scope)


def _updated_content(raw: str, value: Mapping[str, Any], sku: str) -> str:
    """Preserve unrelated Markdown and frontmatter, including custom comments."""
    metadata, body = _parse_frontmatter(raw)
    blocks = list(_JSON_BLOCK.finditer(body))
    if blocks:
        merged = {**_read_payload(body, sku), **value}
        validate_guidance_examples(merged, sku)
        block = blocks[0]
        body = body[:block.start(1)] + json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=2) + body[block.end(1):]
    else:
        field = "notas" if sku else "orientacoes_perguntas"
        extras = {key: item for key, item in value.items() if key != field and item not in ("", [], {}, None)}
        edited_text = str(value.get(field, body))
        body = edited_text if not extras and edited_text.strip() else _guidance_body("Orientacoes", value)
    updates = {"status": "draft", "ai_usage": "denied", "source_hash": content_sha256(body), "content_hash": content_sha256(body)}
    # Retain the original YAML instead of reserializing unrelated metadata.
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n)*", raw, re.DOTALL)
    if not match:
        raise ContextHubValidationError("Frontmatter da nota invalido.")
    frontmatter = match.group(1)
    for key, item in updates.items():
        encoded = yaml.safe_dump({key: item}, allow_unicode=True, sort_keys=False).strip()
        pattern = rf"(?m)^{re.escape(key)}:[^\n]*(?:\n[ \t]+[^\n]*)*"
        frontmatter, count = re.subn(pattern, lambda _: encoded, frontmatter)
        if not count:
            frontmatter += "\n" + encoded
    metadata.update(updates)
    if _has_blocker(_validate_curated_content(metadata, body, source_ref="store_guidance_editor")):
        raise ContextHubValidationError("Orientacao reprovada pela validacao.")
    return "---\n" + frontmatter + "\n---\n\n" + body + "\n"


def save_store_guidance_editor(
    client_id: object, scope: Mapping[str, Any], *, guidance: Mapping[str, Any],
    sku: str = "", expected_revision: str, actor: object = "admin", info_root=None,
) -> dict[str, Any]:
    """Save only the requested note; caller verifies SKU catalog membership."""
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    exact_scope = _scope_for_paths(paths, scope)
    normalized_sku = normalize_sku(sku) if sku else ""
    if normalized_sku == "1599":
        raise ContextHubValidationError("SKU legado 1599 permanece em quarentena.")
    if not isinstance(guidance, Mapping):
        raise ContextHubValidationError("Orientacoes devem ser um objeto.")
    value = dict(guidance)
    validate_guidance_examples(value, normalized_sku)
    _validate_no_dlp(value, source_ref="store_guidance_editor")
    validate_integral_size(value, APPLICABLE_GUIDANCE_MAX_CHARS, code="applicable_guidance_too_large")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        before = _snapshot(paths, exact_scope)
        if not expected_revision or expected_revision != before["editorial"]["revision"]:
            raise StoreGuidanceEditorConflict("As orientacoes mudaram. Recarregue antes de salvar.")
        entry = before["editorial"]["skus"].get(normalized_sku, {}) if normalized_sku else before["editorial"]["general"]
        if entry.get("status") in {"invalid", "conflict"}:
            raise ContextHubValidationError("Resolva a nota invalida ou duplicada no Obsidian antes de salvar.")
        candidates = find_editor_note_paths(paths, exact_scope).get(normalized_sku, [])
        if candidates:
            target = candidates[0]
            raw = target.read_text(encoding="utf-8")
            content = _updated_content(raw, value, normalized_sku)
        else:
            relative = Path("Lojas") / store_directory_name(exact_scope.store_ref, exact_scope.store_name)
            relative /= Path("SKUs") / _sku_path_component(normalized_sku) / "Orientacoes.md" if normalized_sku else Path("Orientacoes-Gerais.md")
            target = paths.curated_dir / relative
            body = _guidance_body("Orientacoes do SKU " + normalized_sku if normalized_sku else "Orientacoes gerais da loja", value)
            metadata = _guidance_frontmatter(exact_scope, sku=normalized_sku, role="sku_guidance" if normalized_sku else "store_guidance", state="draft", body=body, actor=_opaque_actor(actor), now=_utc_now())
            content = _dump_frontmatter(metadata, body)
        _assert_path_chain_safe(target, paths.info_root)
        # Recheck after preparation: Obsidian does not participate in our lock.
        if _snapshot(paths, exact_scope)["editorial"]["revision"] != expected_revision:
            raise StoreGuidanceEditorConflict("O Obsidian alterou a nota durante o salvamento.")
        _write_text_atomic(target, content)
        record = _read_curated_note(paths, target)
        with _connect(paths) as connection:
            _ensure_curation_row(connection, record["relative_path"], record["content_sha256"], str(record["metadata"].get("id") or ""))
            connection.commit()
        return _snapshot(paths, exact_scope)


__all__ = ["StoreGuidanceEditorConflict", "find_editor_note_paths", "load_store_guidance_editor", "save_store_guidance_editor"]
