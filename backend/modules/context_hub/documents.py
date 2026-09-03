"""Context Hub documents component."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Optional,
    Sequence,
)



from backend.modules.context_hub.bundles import (
    _chunks_for_document,
)

from backend.modules.context_hub.contracts import (
    CONTEXT_BUNDLE_GRAPH_ANCHORS,
    CONTEXT_BUNDLE_OBSIDIAN_PATHS,
    ContextHubPaths,
    ContextHubValidationError,
    FRONTMATTER_REQUIRED,
)

from backend.modules.context_hub import curation_records
from backend.modules.context_hub.dlp import (
    scan_dlp,
)

from backend.modules.context_hub.dlp_core import (
    _dlp_document_text,
)

from backend.modules.context_hub.filesystem import (
    _replace_with_retry,
    _write_text_atomic,
)

from backend.modules.context_hub.findings import (
    _finding,
    _strip_volatile,
)

from backend.modules.context_hub.locking import (
    _safe_remove_tree,
)

from backend.modules.context_hub.metadata import (
    _dump_frontmatter,
    _normalize_generated_note,
    _normalize_source_refs,
    _obsidian_wikilink,
    _parse_frontmatter,
    _safe_relative_markdown_path,
)

from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
)

from backend.modules.context_hub.materialization import persist_generation_materialization

from backend.modules.context_hub.product_evidence_editorial import (
    PRODUCT_EVIDENCE_EDITORIAL_ROOT,
    render_product_evidence_editorial,
)

from backend.modules.context_hub.product_evidence_attestation import persist_product_evidence_attestation

from backend.modules.context_hub.runtime import (
    _json_canonical,
    _new_id,
    _sha256_text,
    _utc_now,
)

from backend.modules.context_hub.storage import (
    _connect,
)


def _public_generation(row: sqlite3.Row | Mapping[str, Any], *, include_details: bool = False) -> dict[str, Any]:
    data = dict(row)
    payload = {
        "generation_id": data.get("generation_id"),
        "status": data.get("status"),
        "source_hash": data.get("source_hash"),
        "source_version": data.get("source_version"),
        "surface": data.get("surface"),
        "reason": data.get("reason"),
        "base_active_generation_id": data.get("base_active_generation_id"),
        "rollback_of": data.get("rollback_of"),
        "created_at": data.get("created_at"),
        "validated_at": data.get("validated_at"),
        "published_at": data.get("published_at"),
        "superseded_at": data.get("superseded_at"),
    }
    if include_details:
        try:
            payload["findings"] = json.loads(str(data.get("findings_json") or "[]"))
        except json.JSONDecodeError:
            payload["findings"] = [_finding("stored_findings_invalid", category="storage")]
        try:
            payload["stats"] = json.loads(str(data.get("stats_json") or "{}"))
        except json.JSONDecodeError:
            payload["stats"] = {}
    return payload


def _insert_failed_generation(
    paths: ContextHubPaths,
    *,
    source_hash: str,
    source_version: str,
    surface: str,
    reason: str,
    findings: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    base_active_generation_id: Optional[str],
) -> dict[str, Any]:
    generation_id = _new_id()
    created_at = _utc_now()
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO context_hub_generations(
                generation_id, status, source_hash, source_version, surface, reason,
                base_active_generation_id, findings_json, stats_json, created_at, validated_at
            ) VALUES (?, 'failed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                source_hash,
                source_version,
                surface,
                reason,
                base_active_generation_id,
                _json_canonical(list(findings)),
                _json_canonical(dict(stats)),
                created_at,
                created_at,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
    return _public_generation(row, include_details=True) | {"success": False, "idempotent": False}


def _active_generation_id(connection: sqlite3.Connection) -> Optional[str]:
    row = connection.execute(
        "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
    ).fetchone()
    value = str(row["generation_id"] or "").strip() if row else ""
    return value or None


@dataclass
class _DocumentPreparation:
    documents: list[dict[str, Any]] = field(default_factory=list)
    managed_files: dict[str, str] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    seen_paths: set[str] = field(default_factory=set)
    reviewed_bundle_links: list[tuple[str, str, str]] = field(default_factory=list)


def _prepare_rendered_inventory(
    state: _DocumentPreparation,
    inventory: Mapping[str, Any],
    *,
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> bool:
    try:
        rendered = curation_records._render_inventory(inventory)
    except Exception:
        state.findings.append(_finding("inventory_render_failed", category="inventory"))
        return False
    for raw_path, raw_content in sorted(rendered.items(), key=lambda item: str(item[0]).lower()):
        try:
            relative = _safe_relative_markdown_path(raw_path)
            path_key = relative.as_posix().lower()
            if path_key in state.seen_paths:
                state.findings.append(_finding("generated_path_duplicate", category="inventory", source_ref=relative.as_posix()))
                continue
            metadata, content, body = _normalize_generated_note(
                relative,
                str(raw_content),
                inventory=inventory,
                client_id=client_id,
                surface=surface,
                source_version=source_version,
                generated_at=generated_at,
            )
        except ContextHubValidationError:
            state.findings.append(_finding("generated_path_invalid", category="inventory"))
            continue
        doc_id = str(metadata["id"])
        if doc_id in state.seen_ids:
            state.findings.append(_finding("generated_id_duplicate", category="inventory", source_ref=relative.as_posix()))
            continue
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative.as_posix())
        if dlp:
            state.findings.extend(dlp)
            continue
        state.seen_ids.add(doc_id)
        state.seen_paths.add(path_key)
        state.managed_files[relative.as_posix()] = content
        state.documents.append(
            {
                "metadata": metadata,
                "relative_path": relative.as_posix(),
                "content": content,
                "body": body,
            }
        )
    return True


def _prepare_sku_entities(
    state: _DocumentPreparation,
    inventory: Mapping[str, Any],
    *,
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> None:
    for entity in inventory.get("entities") or []:
        if not isinstance(entity, Mapping) or str(entity.get("kind") or "").lower() != "sku":
            continue
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id or entity_id in state.seen_ids:
            continue
        title = str(entity.get("title") or entity_id).strip()
        body_parts = [f"# {title}", "", str(entity.get("content") or "").strip()]
        relationships = entity.get("relationships")
        if isinstance(relationships, list) and relationships:
            body_parts.extend(["", "## Relacoes", ""])
            for relation in relationships:
                if isinstance(relation, Mapping):
                    relation_type = str(relation.get("type") or "related_to")
                    target_id = str(relation.get("target_id") or "")
                    body_parts.append(f"- {relation_type}: `{target_id}`")
        body = "\n".join(body_parts).strip() + "\n"
        source_hash = str(entity.get("source_hash") or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", source_hash):
            source_hash = _sha256_text(_json_canonical(_strip_volatile(entity)))
        metadata = {
            "id": entity_id,
            "type": "sku",
            "managed": True,
            "status": "published",
            "ai_usage": "allowed",
            "tenant_scope": f"tenant:{client_id}",
            "sensitivity": str(entity.get("sensitivity") or "internal"),
            "truth_class": str(entity.get("truth_class") or "canonical"),
            "required_permissions": ["full"],
            "surface": surface,
            "source_version": source_version,
            "source_refs": _normalize_source_refs(entity.get("source_refs")),
            "source_hash": source_hash,
            "generated_at": generated_at,
            "title": title,
            "module": str(entity.get("domain") or "cadastro"),
        }
        relative = f"@internal/sku/{_sha256_text(entity_id)[:20]}.md"
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative)
        if dlp:
            state.findings.extend(dlp)
            continue
        state.seen_ids.add(entity_id)
        state.seen_paths.add(relative.lower())
        state.documents.append(
            {
                "metadata": metadata,
                "relative_path": relative,
                "content": _dump_frontmatter(metadata, body),
                "body": body,
            }
        )


def _prepare_bundle_documents(
    state: _DocumentPreparation,
    bundle_documents: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    inventory_managed_paths = tuple(sorted(state.managed_files))
    for bundle in bundle_documents:
        metadata = dict(bundle.get("metadata") or {})
        relative = str(bundle.get("relative_path") or "")
        doc_id = str(metadata.get("id") or "")
        if not doc_id or doc_id in state.seen_ids:
            state.findings.append(_finding("bundle_id_duplicate", category="bundle", source_ref=relative))
            continue
        if relative.lower() in state.seen_paths:
            state.findings.append(_finding("bundle_path_duplicate", category="bundle", source_ref=relative))
            continue
        obsidian_target = CONTEXT_BUNDLE_OBSIDIAN_PATHS.get(relative)
        if obsidian_target:
            try:
                managed_relative = _safe_relative_markdown_path(obsidian_target)
            except ContextHubValidationError:
                state.findings.append(
                    _finding("bundle_obsidian_path_invalid", category="bundle", source_ref=relative)
                )
                continue
            if tuple(managed_relative.parts[:2]) != ("70_Gerado", "Contratos"):
                state.findings.append(
                    _finding("bundle_obsidian_path_invalid", category="bundle", source_ref=relative)
                )
                continue
            managed_path = managed_relative.as_posix()
            if any(path.casefold() == managed_path.casefold() for path in state.managed_files):
                state.findings.append(
                    _finding("bundle_obsidian_path_duplicate", category="bundle", source_ref=relative)
                )
                continue
            state.managed_files[managed_path] = str(bundle.get("content") or "")
            state.reviewed_bundle_links.append(
                (
                    managed_path,
                    str(metadata.get("title") or Path(managed_path).stem),
                    relative,
                )
            )
        state.seen_ids.add(doc_id)
        state.seen_paths.add(relative.lower())
        state.documents.append(dict(bundle))
    return inventory_managed_paths


def _link_reviewed_bundles(
    state: _DocumentPreparation,
    inventory_managed_paths: Sequence[str],
) -> None:
    for managed_path, title, source_ref in state.reviewed_bundle_links:
        preferred_anchors = CONTEXT_BUNDLE_GRAPH_ANCHORS.get(source_ref, ())
        anchor_path = next(
            (
                path
                for preferred in preferred_anchors
                for path in inventory_managed_paths
                if path.casefold() == preferred.casefold()
            ),
            "",
        )
        anchor_document = next(
            (
                document
                for document in state.documents
                if str(document.get("relative_path") or "").casefold() == anchor_path.casefold()
            ),
            None,
        )
        if not anchor_document:
            state.findings.append(
                _finding("bundle_graph_anchor_missing", category="bundle", source_ref=source_ref)
            )
            continue
        link = _obsidian_wikilink(managed_path, title)
        body = str(anchor_document.get("body") or "").rstrip()
        body += "\n\n## Documentacao revisada\n\n- " + link + "\n"
        metadata = dict(anchor_document.get("metadata") or {})
        metadata["source_refs"] = sorted(
            set(_normalize_source_refs(metadata.get("source_refs"))) | {source_ref}
        )
        metadata["source_hash"] = _sha256_text(
            _json_canonical(metadata["source_refs"]) + "\n" + body
        )
        content = _dump_frontmatter(metadata, body)
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=anchor_path)
        if dlp:
            state.findings.extend(dlp)
            continue
        anchor_document["metadata"] = metadata
        anchor_document["body"] = body
        anchor_document["content"] = content
        state.managed_files[anchor_path] = content


def _append_curated_documents(
    state: _DocumentPreparation,
    curated_documents: Sequence[Mapping[str, Any]],
) -> None:
    for curated in curated_documents:
        metadata = dict(curated.get("metadata") or {})
        relative = str(curated.get("relative_path") or "")
        doc_id = str(metadata.get("id") or "")
        if not doc_id or doc_id in state.seen_ids:
            state.findings.append(_finding("curated_id_duplicate", category="curation", source_ref=relative))
            continue
        path_key = relative.lower()
        if path_key in state.seen_paths:
            state.findings.append(_finding("curated_path_duplicate", category="curation", source_ref=relative))
            continue
        state.seen_ids.add(doc_id)
        state.seen_paths.add(path_key)
        state.documents.append(dict(curated))


def _append_product_evidence_documents(
    state: _DocumentPreparation,
    snapshot: Mapping[str, Any],
    *,
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> None:
    try:
        documents, managed_files, findings = render_product_evidence_editorial(
            snapshot,
            client_id=client_id,
            surface=surface,
            source_version=source_version,
            generated_at=generated_at,
        )
    except ContextHubValidationError:
        state.findings.append(
            _finding("product_evidence_render_failed", category="product_evidence")
        )
        return
    state.findings.extend(findings)
    projection_paths: set[str] = set()
    for raw_relative, raw_content in sorted(managed_files.items()):
        try:
            relative = _safe_relative_markdown_path(raw_relative).as_posix()
        except ContextHubValidationError:
            state.findings.append(
                _finding("product_evidence_path_invalid", category="product_evidence")
            )
            continue
        content = str(raw_content or "")
        path_key = relative.casefold()
        if (
            not relative.startswith(PRODUCT_EVIDENCE_EDITORIAL_ROOT + "/")
            or len(content.encode("utf-8")) > 1_000_000
            or path_key in state.seen_paths
        ):
            state.findings.append(
                _finding("product_evidence_file_invalid", category="product_evidence")
            )
            continue
        projection_paths.add(relative)
        state.seen_paths.add(path_key)
        state.managed_files[relative] = content
    for document in documents:
        metadata = dict(document.get("metadata") or {})
        relative = str(document.get("relative_path") or "")
        doc_id = str(metadata.get("id") or "")
        body = str(document.get("body") or "")
        if relative not in projection_paths or not doc_id or doc_id in state.seen_ids:
            state.findings.append(
                _finding("product_evidence_document_invalid", category="product_evidence")
            )
            continue
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative)
        if dlp:
            state.findings.extend(dlp)
            continue
        state.seen_ids.add(doc_id)
        state.documents.append(dict(document))


def _prepare_documents(
    inventory: Mapping[str, Any],
    bundle_documents: Sequence[Mapping[str, Any]],
    curated_documents: Sequence[Mapping[str, Any]],
    product_evidence_snapshot: Mapping[str, Any],
    *,
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    state = _DocumentPreparation()
    if not _prepare_rendered_inventory(
        state,
        inventory,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        generated_at=generated_at,
    ):
        return [], {}, state.findings
    _prepare_sku_entities(
        state,
        inventory,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        generated_at=generated_at,
    )
    inventory_managed_paths = _prepare_bundle_documents(state, bundle_documents)
    _link_reviewed_bundles(state, inventory_managed_paths)
    _append_product_evidence_documents(
        state,
        product_evidence_snapshot,
        client_id=client_id,
        surface=surface,
        source_version=source_version,
        generated_at=generated_at,
    )
    _append_curated_documents(state, curated_documents)
    return state.documents, state.managed_files, state.findings


def _reuse_unchanged_documents(
    paths: ContextHubPaths,
    base_generation_id: Optional[str],
    documents: list[dict[str, Any]],
    managed_files: dict[str, str],
) -> int:
    """Keep the previous generated_at/content for semantically unchanged docs.

    Immutable generations still receive a complete snapshot, but changing one
    SKU no longer changes every unrelated document merely because the rebuild
    timestamp changed.
    """

    if not base_generation_id or not documents:
        return 0
    with _connect(paths) as connection:
        previous = {
            str(row["doc_id"]): dict(row)
            for row in connection.execute(
                """
                SELECT doc_id, relative_path, source_hash, content
                FROM context_hub_documents WHERE generation_id=?
                """,
                (base_generation_id,),
            ).fetchall()
        }
    stable_metadata_keys = tuple(
        key for key in FRONTMATTER_REQUIRED if key != "generated_at"
    ) + ("title", "module")
    reused = 0
    for document in documents:
        metadata = dict(document.get("metadata") or {})
        doc_id = str(metadata.get("id") or "")
        old = previous.get(doc_id)
        if not old:
            continue
        relative_path = str(document.get("relative_path") or "")
        if (
            str(old.get("relative_path") or "") != relative_path
            or str(old.get("source_hash") or "") != str(metadata.get("source_hash") or "")
        ):
            continue
        old_content = str(old.get("content") or "")
        old_metadata, old_body = _parse_frontmatter(old_content)
        new_body = str(document.get("body") or "")
        if old_body.strip() != new_body.strip():
            continue
        if any(old_metadata.get(key) != metadata.get(key) for key in stable_metadata_keys):
            continue
        document["content"] = old_content
        document["body"] = old_body
        if relative_path in managed_files:
            managed_files[relative_path] = old_content
        reused += 1
    return reused


def _write_generation_snapshot(
    paths: ContextHubPaths,
    generation_id: str,
    managed_files: Mapping[str, str],
) -> Path:
    temporary_root = paths.staging_dir / generation_id
    final_root = paths.generations_dir / generation_id
    _safe_remove_tree(temporary_root, paths.internal_dir)
    _safe_remove_tree(final_root, paths.internal_dir)
    generated_root = temporary_root / "70_Gerado"
    generated_root.mkdir(parents=True, exist_ok=False)
    for relative in ("Mapas", "Dominios", "Fluxos", "Contratos", "Operacao", "Produtos"):
        (generated_root / relative).mkdir(parents=True, exist_ok=True)
    for relative, content in sorted(managed_files.items()):
        relative_path = Path(relative)
        target = temporary_root / relative_path
        _assert_path_chain_safe(target, temporary_root)
        _write_text_atomic(target, content)
    _replace_with_retry(temporary_root, final_root)
    return final_root


_DOCUMENT_INSERT_SQL = """
INSERT INTO context_hub_documents(
    generation_id, doc_id, entity_id, relative_path, title, kind, module,
    surface, truth_class, sensitivity, source_version, source_hash,
    content_hash, source_refs_json, store_ref, seller_id, site_id, sku,
    item_id, variation_id, tags_text, valid_from, valid_to, content, managed
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_staging_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    source_hash: str,
    source_version: str,
    surface: str,
    reason: str,
    base_active_generation_id: Optional[str],
    findings: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO context_hub_generations(
            generation_id, status, source_hash, source_version, surface, reason,
            base_active_generation_id, findings_json, stats_json, created_at, validated_at
        ) VALUES (?, 'staging', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id, source_hash, source_version, surface, reason,
            base_active_generation_id, _json_canonical(list(findings)),
            _json_canonical(dict(stats)), created_at, _utc_now(),
        ),
    )
    connection.execute(
        "UPDATE context_hub_generations SET status='validating' WHERE generation_id=?",
        (generation_id,),
    )


def _document_values(
    generation_id: str,
    document: Mapping[str, Any],
    *,
    surface: str,
    source_version: str,
) -> tuple[tuple[Any, ...], dict[str, Any], str, str, str]:
    metadata = dict(document.get("metadata") or {})
    content = str(document.get("content") or "")
    body = str(document.get("body") or "")
    doc_id = str(metadata["id"])
    relative_path = str(document.get("relative_path") or "")
    source_refs = _normalize_source_refs(metadata.get("source_refs"))
    tags = sorted(
        {str(item).strip().casefold()[:80] for item in list(metadata.get("tags") or []) if str(item).strip()}
    )[:20]
    valid_from = str(metadata.get("valid_from") or metadata.get("valid_at") or "")[:40]
    valid_to = str(metadata.get("valid_to") or metadata.get("valid_at") or "")[:40]
    values = (
        generation_id, doc_id, doc_id, relative_path,
        str(metadata.get("title") or doc_id), str(metadata.get("type") or "document"),
        str(metadata.get("module") or ""), str(metadata.get("surface") or surface),
        str(metadata.get("truth_class") or "generated_verified"),
        str(metadata.get("sensitivity") or "internal"),
        str(metadata.get("source_version") or source_version),
        str(metadata.get("source_hash") or _sha256_text(body)), _sha256_text(content),
        _json_canonical(source_refs), str(metadata.get("store_ref") or "")[:180],
        str(metadata.get("seller_id") or "")[:180], str(metadata.get("site_id") or "")[:32],
        str(metadata.get("sku") or "")[:180], str(metadata.get("item_id") or "")[:180],
        str(metadata.get("variation_id") or "")[:180], "\n".join(tags), valid_from, valid_to, content,
        1 if metadata.get("managed") is True else 0,
    )
    return values, metadata, body, doc_id, relative_path


def _persist_curation_attestation(
    connection: sqlite3.Connection,
    generation_id: str,
    relative_path: str,
    document: Mapping[str, Any],
) -> None:
    if not relative_path.startswith("80_Curadoria/"):
        return
    digest = str(document.get("curation_content_sha256") or "").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ContextHubValidationError("Atestado de aprovacao da curadoria invalido.")
    connection.execute(
        "INSERT INTO context_hub_generation_curated_approvals("
        "generation_id, relative_path, content_sha256) VALUES (?, ?, ?)",
        (generation_id, relative_path, digest),
    )


def _persist_chunks(
    connection: sqlite3.Connection,
    generation_id: str,
    doc_id: str,
    body: str,
    metadata: Mapping[str, Any],
    surface: str,
) -> None:
    for chunk in _chunks_for_document(doc_id, body):
        connection.execute(
            "INSERT INTO context_hub_chunks("
            "generation_id, chunk_id, doc_id, ordinal, content, content_hash"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                generation_id, chunk["chunk_id"], doc_id, chunk["ordinal"],
                chunk["content"], chunk["content_hash"],
            ),
        )
        try:
            connection.execute(
                "INSERT INTO context_hub_chunks_fts("
                "generation_id, chunk_id, doc_id, title, content, module, kind, surface, truth_class"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generation_id, chunk["chunk_id"], doc_id,
                    str(metadata.get("title") or doc_id), chunk["content"],
                    str(metadata.get("module") or ""), str(metadata.get("type") or "document"),
                    str(metadata.get("surface") or surface),
                    str(metadata.get("truth_class") or "generated_verified"),
                ),
            )
        except sqlite3.OperationalError:
            pass


def _persist_document(
    connection: sqlite3.Connection,
    generation_id: str,
    document: Mapping[str, Any],
    *,
    surface: str,
    source_version: str,
) -> None:
    values, metadata, body, doc_id, relative_path = _document_values(
        generation_id,
        document,
        surface=surface,
        source_version=source_version,
    )
    connection.execute(_DOCUMENT_INSERT_SQL, values)
    _persist_curation_attestation(connection, generation_id, relative_path, document)
    _persist_chunks(connection, generation_id, doc_id, body, metadata, surface)


def _persist_ready_generation(
    paths: ContextHubPaths,
    generation_id: str,
    *,
    source_hash: str,
    source_version: str,
    surface: str,
    reason: str,
    findings: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any],
    base_active_generation_id: Optional[str],
    created_at: str,
    documents: Sequence[Mapping[str, Any]],
    product_evidence_attestation: Mapping[str, Any],
    generation_materialization: Mapping[str, Any],
) -> None:
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _insert_staging_generation(
                connection,
                generation_id,
                source_hash=source_hash,
                source_version=source_version,
                surface=surface,
                reason=reason,
                base_active_generation_id=base_active_generation_id,
                findings=findings,
                stats=stats,
                created_at=created_at,
            )
            for document in documents:
                _persist_document(
                    connection,
                    generation_id,
                    document,
                    surface=surface,
                    source_version=source_version,
                )
            persist_product_evidence_attestation(
                connection,
                generation_id,
                product_evidence_attestation,
            )
            persist_generation_materialization(connection, generation_id, generation_materialization)
            connection.execute(
                "UPDATE context_hub_generations SET status='ready', validated_at=? WHERE generation_id=?",
                (_utc_now(), generation_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
