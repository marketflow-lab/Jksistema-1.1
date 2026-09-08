"""Transactional repository for exact store/SKU knowledge generations."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.contracts import (
    ContextHubNotFoundError,
    ContextHubValidationError,
)
from backend.modules.context_hub.curation_records import (
    _ensure_curation_row,
    _public_curated_note,
    _read_curated_note,
    _validate_curated_content,
)
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.findings import _has_blocker
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.metadata import _dump_frontmatter
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import _new_id, _utc_now
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_contracts import (
    APPLICABLE_GUIDANCE_MAX_CHARS,
    StoreSkuBinding,
    StoreSkuScope,
    content_sha256,
    normalize_sku,
    store_directory_name,
    validate_integral_size,
)


from backend.modules.context_hub.store_sku_repository_support import (
    _approved_guidance_file,
    _assert_editorial_skus_publishable,
    _generation_knowledge,
    _guidance_body,
    _guidance_frontmatter,
    _materialized_files,
    _normalize_bindings,
    _not_found,
    _opaque_actor,
    _restore_files,
    _safe_json_load,
    _scope_for_paths,
    _sku_path_component,
    _source_hash,
    _validate_documents,
    _validate_no_dlp,
    _write_materialized_files,
)
from backend.modules.context_hub.store_sku_repository_db import (
    decode_generation_rows,
    load_exact_generation_rows,
    loaded_store_sku_result,
    publish_generation_transaction,
)

def publish_store_sku_generation(
    client_id: object,
    scope_value: Mapping[str, Any],
    *,
    canonical_documents: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    sku_guidance: Mapping[str, Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    migration_id: object = "",
    actor: object = "migration",
    preserve_curated_files: bool = False,
    expected_active_generation_id: str | None = None,
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Publish one immutable store generation and atomically switch its pointer."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    scope = _scope_for_paths(paths, scope_value)
    canonical, normalized_sku_guidance = _validate_documents(
        canonical_documents, store_guidance, sku_guidance,
    )
    normalized_bindings = _normalize_bindings(
        bindings, scope=scope, canonical_skus=set(canonical),
    )
    source_hash = _source_hash(
        scope, canonical, store_guidance, normalized_sku_guidance, normalized_bindings,
    )
    now = _utc_now()
    safe_actor = _opaque_actor(actor)
    safe_migration_id = re.sub(r"[^A-Za-z0-9._-]", "", str(migration_id or ""))[:128]
    generation_id = "store-sku-" + _new_id()
    stats = {
        "canonical_document_count": len(canonical),
        "store_guidance_count": int(bool(store_guidance)),
        "sku_guidance_count": len(normalized_sku_guidance),
        "binding_count": len(normalized_bindings),
    }
    files = _materialized_files(
        scope,
        generation_id=generation_id,
        canonical=canonical,
        store_guidance=store_guidance,
        sku_guidance=normalized_sku_guidance,
        actor=safe_actor,
        now=now,
        include_curated=not preserve_curated_files,
    )
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        active, idempotent = publish_generation_transaction(
            paths,
            scope,
            generation_id=generation_id,
            source_hash=source_hash,
            migration_id=safe_migration_id,
            stats=stats,
            canonical=canonical,
            store_guidance=store_guidance,
            sku_guidance=normalized_sku_guidance,
            bindings=normalized_bindings,
            files=files,
            actor=safe_actor,
            now=now,
            expected_active_generation_id=expected_active_generation_id,
        )
    active_generation_id = str(active["generation_id"]) if active is not None else ""
    if idempotent:
        return {
            "success": True,
            "changed": False,
            "idempotent": True,
            "generation_id": active_generation_id,
            "previous_generation_id": active_generation_id,
            "source_hash": source_hash,
            "stats": stats,
        }
    return {
        "success": True,
        "changed": True,
        "idempotent": False,
        "generation_id": generation_id,
        "previous_generation_id": active_generation_id,
        "source_hash": source_hash,
        "stats": stats,
    }


def load_store_sku_knowledge(
    client_id: object,
    identity: Mapping[str, Any],
    *,
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Read only the active canonical document bound to the exact listing identity."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    try:
        scope = _scope_for_paths(paths, identity)
        requested = StoreSkuBinding(
            item_id=str(identity.get("item_id") or "").strip().upper(),
            variation_id=str(identity.get("variation_id") or "").strip(),
            sku=normalize_sku(identity.get("sku")),
        )
        requested.validate(site_id=scope.site_id)
    except ContextHubValidationError as exc:
        reason = (
            "tenant_scope_mismatch"
            if "tenant_scope diverge" in str(exc)
            else "exact_identity_incomplete"
        )
        return _not_found(reason)
    active, binding, rows, reason = load_exact_generation_rows(paths, scope, requested)
    if reason:
        return _not_found(reason)
    if active is None or binding is None:
        return _not_found("store_generation_unavailable")
    by_role, decoded, reason = decode_generation_rows(rows)
    if reason:
        return _not_found(reason)
    return loaded_store_sku_result(
        scope, requested, active, binding, by_role, decoded,
    )


def load_store_guidance(
    client_id: object,
    scope_value: Mapping[str, Any],
    *,
    sku: object = "",
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Load active guidance for the editor without consulting legacy JSON."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    scope = _scope_for_paths(paths, scope_value)
    normalized_sku = normalize_sku(sku) if str(sku or "").strip() else ""
    with _connect(paths) as connection:
        active = connection.execute(
            """
            SELECT generation_id FROM context_hub_store_sku_active_generations
            WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?
            """,
            (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
        ).fetchone()
        if active is None:
            return {"found": False, "guidance": {"general": {}, "sku": {}}}
        if normalized_sku:
            rows = connection.execute(
                """
                SELECT knowledge_role, sku, content_json
                FROM context_hub_store_sku_documents
                WHERE generation_id=? AND (
                    (knowledge_role='store_guidance' AND sku='') OR
                    (knowledge_role='sku_guidance' AND sku=?)
                )
                """,
                (str(active["generation_id"]), normalized_sku),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT knowledge_role, sku, content_json
                FROM context_hub_store_sku_documents
                WHERE generation_id=? AND knowledge_role IN (
                    'store_guidance','sku_guidance'
                )
                """,
                (str(active["generation_id"]),),
            ).fetchall()
    general: dict[str, Any] = {}
    sku_values: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = _safe_json_load(row["content_json"], code="knowledge_json_invalid")
        if str(row["knowledge_role"]) == "store_guidance" and isinstance(value, Mapping):
            general = dict(value)
        elif str(row["knowledge_role"]) == "sku_guidance" and isinstance(value, Mapping):
            sku_values[str(row["sku"])] = dict(value)
    return {
        "found": bool(rows),
        "generation_id": str(active["generation_id"]),
        "guidance": {
            "general": general,
            "sku": sku_values.get(normalized_sku, {}) if normalized_sku else {},
        },
        "sku_guidance": sku_values,
    }


def create_store_guidance_draft(
    client_id: object,
    scope_value: Mapping[str, Any],
    *,
    guidance: Mapping[str, Any],
    sku: object = "",
    actor: object = "admin",
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Write a store or store/SKU orientation as a schema-3 Obsidian draft."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    scope = _scope_for_paths(paths, scope_value)
    normalized_sku = normalize_sku(sku) if str(sku or "").strip() else ""
    if normalized_sku == "1599":
        raise ContextHubValidationError("SKU legado 1599 permanece em quarentena.")
    if normalized_sku:
        with _connect(paths) as connection:
            sku_row = connection.execute(
                """
                SELECT 1
                FROM context_hub_store_sku_active_generations a
                JOIN context_hub_store_sku_generations g
                  ON g.generation_id=a.generation_id AND g.status='active'
                JOIN context_hub_store_sku_documents d
                  ON d.generation_id=a.generation_id
                 AND d.knowledge_role='canonical_sku' AND d.sku=?
                WHERE a.store_ref=? AND a.seller_id=? AND a.site_id=? AND a.surface=?
                LIMIT 1
                """,
                (
                    normalized_sku,
                    scope.store_ref,
                    scope.seller_id,
                    scope.site_id,
                    scope.surface,
                ),
            ).fetchone()
        if sku_row is None:
            raise ContextHubValidationError(
                "SKU nao pertence a geracao ativa exata desta loja."
            )
    value = dict(guidance) if isinstance(guidance, Mapping) else {}
    _validate_no_dlp(value, source_ref="store_guidance_draft")
    validate_integral_size(
        value,
        APPLICABLE_GUIDANCE_MAX_CHARS,
        code="applicable_guidance_too_large",
    )
    now = _utc_now()
    safe_actor = _opaque_actor(actor)
    role = "sku_guidance" if normalized_sku else "store_guidance"
    title = f"Orientacoes do SKU {normalized_sku}" if normalized_sku else "Orientacoes gerais da loja"
    body = _guidance_body(title, value)
    metadata = _guidance_frontmatter(
        scope,
        sku=normalized_sku,
        role=role,
        state="draft",
        body=body,
        actor=safe_actor,
        now=now,
    )
    validation = _validate_curated_content(metadata, body, source_ref="store_guidance_draft")
    if _has_blocker(validation):
        raise ContextHubValidationError("Orientacao reprovada pela validacao/DLP.")
    store_dir = store_directory_name(scope.store_ref, scope.store_name)
    relative = (
        Path("Lojas") / store_dir / "SKUs" / _sku_path_component(normalized_sku) / "Orientacoes.md"
        if normalized_sku
        else Path("Lojas") / store_dir / "Orientacoes-Gerais.md"
    )
    target = paths.curated_dir / relative
    _assert_path_chain_safe(target, paths.info_root)
    content = _dump_frontmatter(metadata, body)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
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
    return {
        "success": True,
        "store_ref": scope.store_ref,
        "sku": normalized_sku,
        "note": _public_curated_note(record, row),
    }


def publish_approved_store_guidance(
    client_id: object,
    scope_value: Mapping[str, Any],
    *,
    actor: object = "curation-publication",
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Publish approved schema-3 Obsidian guidance over the current store generation."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    scope = _scope_for_paths(paths, scope_value)
    store_dir = store_directory_name(scope.store_ref, scope.store_name)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        # Resolve editorial files by immutable identity, including renamed stores
        # and notes moved inside the curated vault.
        from backend.modules.context_hub.store_sku_editor import find_editor_note_paths

        editorial_paths = find_editor_note_paths(paths, scope)
        if any(len(candidates) != 1 for candidates in editorial_paths.values()):
            raise ContextHubValidationError("Mais de uma nota para a mesma loja e SKU.")
        with _connect(paths) as connection:
            active = connection.execute(
                """
                SELECT a.generation_id, g.store_name
                FROM context_hub_store_sku_active_generations a
                JOIN context_hub_store_sku_generations g ON g.generation_id=a.generation_id
                WHERE a.store_ref=? AND a.seller_id=? AND a.site_id=? AND a.surface=?
                """,
                (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
            ).fetchone()
            if active is None:
                raise ContextHubNotFoundError("Geracao ativa por loja inexistente.")
            generation_id = str(active["generation_id"])
            rows = connection.execute(
                """
                SELECT knowledge_role, sku, content_json
                FROM context_hub_store_sku_documents WHERE generation_id=?
                """,
                (generation_id,),
            ).fetchall()
            bindings_rows = connection.execute(
                """
                SELECT item_id, variation_id, sku
                FROM context_hub_store_sku_bindings WHERE generation_id=?
                ORDER BY item_id, variation_id, sku
                """,
                (generation_id,),
            ).fetchall()
            canonical: dict[str, dict[str, Any]] = {}
            current_general: dict[str, Any] = {}
            current_sku: dict[str, dict[str, Any]] = {}
            for row in rows:
                role = str(row["knowledge_role"])
                value = _safe_json_load(row["content_json"], code="knowledge_json_invalid")
                if role == "canonical_sku" and isinstance(value, Mapping):
                    canonical[str(row["sku"])] = dict(value)
                elif role == "store_guidance" and isinstance(value, Mapping):
                    current_general = dict(value)
                elif role == "sku_guidance" and isinstance(value, Mapping):
                    current_sku[str(row["sku"])] = dict(value)
            general_file = paths.curated_dir / "Lojas" / store_dir / "Orientacoes-Gerais.md"
            general_file = next(iter(editorial_paths.get("", [])), general_file)
            approved_general = _approved_guidance_file(
                paths,
                connection,
                general_file,
                scope=scope,
                role="store_guidance",
            )
            next_general = approved_general if approved_general is not None else current_general
            next_sku = dict(current_sku)
            _assert_editorial_skus_publishable(paths, connection, scope, canonical, editorial_paths)
            for sku in canonical:
                target = (
                    paths.curated_dir / "Lojas" / store_dir / "SKUs"
                    / _sku_path_component(sku) / "Orientacoes.md"
                )
                target = next(iter(editorial_paths.get(sku, [])), target)
                approved = _approved_guidance_file(
                    paths,
                    connection,
                    target,
                    scope=scope,
                    role="sku_guidance",
                    sku=sku,
                )
                if approved is not None:
                    next_sku[sku] = approved
    effective_scope = {
        **scope.as_dict(),
        "store_name": str(active["store_name"] or scope.store_name),
    }
    return publish_store_sku_generation(
        paths.client_id,
        effective_scope,
        canonical_documents=canonical,
        store_guidance=next_general,
        sku_guidance=next_sku,
        bindings=[
            {
                "item_id": str(row["item_id"]),
                "variation_id": str(row["variation_id"]),
                "sku": str(row["sku"]),
            }
            for row in bindings_rows
        ],
        migration_id="curation-" + content_sha256({
            "general": next_general,
            "sku": next_sku,
        })[:24],
        actor=actor,
        preserve_curated_files=True,
        expected_active_generation_id=generation_id,
        info_root=paths.info_root,
    )


def rollback_store_sku_generation(
    client_id: object,
    scope_value: Mapping[str, Any],
    generation_id: object,
    *,
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Switch one store pointer back to a prior immutable generation."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    scope = _scope_for_paths(paths, scope_value)
    target = str(generation_id or "").strip()
    if not target:
        raise ContextHubNotFoundError("Geracao por loja nao encontrada.")
    now = _utc_now()
    safe_actor = _opaque_actor("store-sku-rollback")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        previous_files: dict[Path, str | None] = {}
        try:
            with _connect(paths) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT generation_id, store_name
                    FROM context_hub_store_sku_generations
                    WHERE generation_id=? AND store_ref=? AND seller_id=?
                      AND site_id=? AND surface=?
                    """,
                    (target, scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise ContextHubNotFoundError("Geracao por loja nao encontrada.")
                active = connection.execute(
                    """
                    SELECT generation_id FROM context_hub_store_sku_active_generations
                    WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?
                    """,
                    (scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
                ).fetchone()
                if active is not None and str(active["generation_id"]) == target:
                    connection.rollback()
                    return {"success": True, "changed": False, "generation_id": target}
                canonical, general, sku_guidance = _generation_knowledge(connection, target)
                effective_scope = StoreSkuScope.from_mapping(
                    {**scope.as_dict(), "store_name": str(row["store_name"] or scope.store_name)},
                    tenant_scope=scope.tenant_scope,
                )
                previous_files = _write_materialized_files(
                    paths,
                    connection,
                    _materialized_files(
                        effective_scope,
                        generation_id=target,
                        canonical=canonical,
                        store_guidance=general,
                        sku_guidance=sku_guidance,
                        actor=safe_actor,
                        now=now,
                        include_curated=False,
                    ),
                    scope=effective_scope,
                    actor=safe_actor,
                    now=now,
                )
                if active is not None:
                    connection.execute(
                        """
                        UPDATE context_hub_store_sku_generations
                        SET status='rolled_back', rolled_back_at=? WHERE generation_id=?
                        """,
                        (now, str(active["generation_id"])),
                    )
                connection.execute(
                    """
                    UPDATE context_hub_store_sku_generations
                    SET status='active', published_at=?, superseded_at=NULL, rolled_back_at=NULL
                    WHERE generation_id=?
                    """,
                    (now, target),
                )
                connection.execute(
                    """
                    UPDATE context_hub_store_sku_active_generations
                    SET generation_id=?, version=version+1, updated_at=?
                    WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?
                    """,
                    (target, now, scope.store_ref, scope.seller_id, scope.site_id, scope.surface),
                )
                connection.commit()
        except Exception:
            _restore_files(previous_files)
            raise
    return {"success": True, "changed": True, "generation_id": target}


__all__ = [
    "create_store_guidance_draft",
    "load_store_guidance",
    "load_store_sku_knowledge",
    "publish_approved_store_guidance",
    "publish_store_sku_generation",
    "rollback_store_sku_generation",
]
