"""Restartable migration from legacy guidance and live ML catalogues."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping, Sequence

from backend.modules.context_hub.bootstrap import bootstrap_context_hub
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
from backend.modules.context_hub.metadata import _dump_frontmatter
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.runtime import _utc_now
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_compiler import (
    CompiledStoreSkuKnowledge,
    compile_store_sku_knowledge,
    load_canonical_sku_documents,
    load_legacy_public_guidance,
)
from backend.modules.context_hub.store_sku_contracts import (
    STORE_SKU_MIGRATION_SCHEMA,
    STORE_SKU_PUBLIC_SURFACE,
    canonical_json,
    content_sha256,
)
from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation


INITIAL_STORE_SKU_MIGRATION_TARGETS: tuple[dict[str, str], ...] = (
    {
        "store_ref": "b1e5a6efb16c0db69bba1836",
        "store_name": "JK Peças",
        "seller_id": "588182191",
        "site_id": "MLB",
    },
    {
        "store_ref": "666955b171470f4fb6f6a4a8",
        "store_name": "Carlos José",
        "seller_id": "1275608482",
        "site_id": "MLB",
    },
    {
        "store_ref": "22bb88ce93b504a26dcef2b5",
        "store_name": "Uai Mineirinho",
        "seller_id": "375033834",
        "site_id": "MLB",
    },
)


def _migration_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "catalog_timeout"
    message = str(exc).casefold()
    known_codes = (
        ("store_id solicitado", "catalog_store_mismatch"),
        ("seller_id solicitado", "catalog_seller_mismatch"),
        ("site_id solicitado", "catalog_site_mismatch"),
        ("catalogo mercado livre incompleto", "catalog_incomplete"),
        ("item/variacao a skus distintos", "ambiguous_binding"),
        ("nenhum sku teve identidade completa", "no_publishable_sku"),
        ("json legado de orientacoes invalido", "legacy_guidance_invalid"),
    )
    for fragment, code in known_codes:
        if fragment in message:
            return code
    if isinstance(exc, ContextHubValidationError):
        return "validation_failed"
    if isinstance(exc, OSError):
        return "catalog_unavailable"
    return "unexpected_error"


def _catalog_collector(client_id: str, store_ref: str) -> dict[str, Any]:
    from backend.services.cadastro_catalogo_mercadolivre import coletar_catalogo_mercadolivre

    return coletar_catalogo_mercadolivre(client_id, store_ref)


def _migration_report(plan: CompiledStoreSkuKnowledge) -> dict[str, Any]:
    return {
        "store_ref": plan.scope.store_ref,
        "seller_id": plan.scope.seller_id,
        "site_id": plan.scope.site_id,
        "source_hash": plan.source_hash,
        **plan.report,
    }


def _record_migration(
    client_id: object,
    plan: CompiledStoreSkuKnowledge,
    *,
    migration_id: str,
    status: str,
    generation_id: str = "",
    info_root: os.PathLike[str] | str | None = None,
) -> None:
    paths = _tenant_paths(client_id, info_root=info_root)
    now = _utc_now()
    report = _migration_report(plan)
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO context_hub_store_sku_migrations(
                migration_id, contract_version, store_ref, seller_id, site_id,
                source_hash, status, generation_id, report_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
                status=excluded.status,
                generation_id=excluded.generation_id,
                report_json=excluded.report_json,
                updated_at=excluded.updated_at
            """,
            (
                migration_id,
                STORE_SKU_MIGRATION_SCHEMA,
                plan.scope.store_ref,
                plan.scope.seller_id,
                plan.scope.site_id,
                plan.source_hash,
                status,
                generation_id,
                canonical_json(report),
                now,
                now,
            ),
        )
        connection.commit()


def _validate_quarantined_legacy(
    quarantined: Mapping[str, Mapping[str, Any]],
) -> None:
    for sku, value in quarantined.items():
        if scan_dlp(
            canonical_json(value),
            source_ref=f"legacy_quarantine:{content_sha256(sku)[:12]}",
        ):
            raise ContextHubValidationError(
                "Conteudo legado de quarentena bloqueado pelo DLP."
            )


def _quarantine_legacy_1599(
    client_id: object,
    quarantined: Mapping[str, Mapping[str, Any]],
    *,
    info_root: os.PathLike[str] | str | None = None,
) -> bool:
    value = quarantined.get("1599")
    if not isinstance(value, Mapping) or not value:
        return False
    _validate_quarantined_legacy({"1599": value})
    paths = _tenant_paths(client_id, info_root=info_root)
    now = _utc_now()
    body = (
        "# Registro legado 1599 em quarentena\n\n"
        "Identidade de SKU nao comprovada. Este conteudo e apenas historico e nunca "
        "pode ser consultado pelo fluxo de perguntas publicas.\n\n```json\n"
        + json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```"
    )
    digest = content_sha256(body)
    metadata = {
        "id": "jk:quarantine:legacy-1599",
        "type": "quarantined_legacy_knowledge",
        "managed": True,
        "status": "quarantined",
        "ai_usage": "denied",
        "tenant_scope": f"tenant:{paths.client_id}",
        "sensitivity": "internal",
        "truth_class": "legacy_unverified",
        "required_permissions": ["full"],
        "surface": STORE_SKU_PUBLIC_SURFACE,
        "source_version": "store-sku-migration-v1",
        "source_refs": ["legacy-guidance:1599"],
        "source_hash": digest,
        "content_hash": digest,
        "generated_at": now,
        "scope_kind": "unresolved",
        "sku": "1599",
        "knowledge_role": "quarantine",
    }
    target = (
        paths.vault_dir
        / "90_Arquivo"
        / "Quarentena"
        / "SKUs-sem-identidade"
        / "1599.md"
    )
    _assert_path_chain_safe(target, paths.info_root)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _write_text_atomic(target, _dump_frontmatter(metadata, body))
    return True


def prepare_store_sku_migration(
    client_id: object,
    *,
    targets: Sequence[Mapping[str, Any]] = INITIAL_STORE_SKU_MIGRATION_TARGETS,
    catalog_collector: Callable[[str, str], Mapping[str, Any]] | None = None,
    info_root: os.PathLike[str] | str | None = None,
) -> tuple[list[CompiledStoreSkuKnowledge], dict[str, Any]]:
    """Build a read-only preview. No vault, database pointer, or catalog is changed."""

    paths = _tenant_paths(client_id, info_root=info_root)
    canonical, canonical_report = load_canonical_sku_documents(
        paths.client_id,
        info_root=paths.info_root,
    )
    collect = catalog_collector or _catalog_collector
    plans: list[CompiledStoreSkuKnowledge] = []
    errors: list[dict[str, str]] = []
    for raw_target in targets:
        store_ref = str(raw_target.get("store_ref") or raw_target.get("store_id") or "").strip()
        try:
            catalog = collect(paths.client_id, store_ref)
            general, sku_notes, quarantine = load_legacy_public_guidance(
                paths.client_id,
                store_ref=store_ref,
                info_root=paths.info_root,
            )
            plan = compile_store_sku_knowledge(
                paths.client_id,
                {
                    **dict(raw_target),
                    "tenant_scope": f"tenant:{paths.client_id}",
                    "surface": STORE_SKU_PUBLIC_SURFACE,
                },
                catalog,
                canonical_documents=canonical,
                store_guidance=general,
                legacy_sku_guidance=sku_notes,
                quarantined_legacy=quarantine,
            )
            _validate_quarantined_legacy(plan.quarantined_legacy)
            plans.append(plan)
        except Exception as exc:
            errors.append({
                "store_ref_hash": content_sha256(store_ref)[:16],
                "error_type": type(exc).__name__,
                "reason_code": _migration_error_code(exc),
            })
    preview = {
        "contract": STORE_SKU_MIGRATION_SCHEMA,
        "client_id_hash": content_sha256(paths.client_id)[:16],
        "canonical": canonical_report,
        "target_count": len(targets),
        "ready_count": len(plans),
        "blocked_count": len(errors),
        "stores": [_migration_report(plan) for plan in plans],
        "errors": errors,
        "apply": False,
    }
    return plans, preview


def migrate_store_sku_knowledge(
    client_id: object,
    *,
    targets: Sequence[Mapping[str, Any]] = INITIAL_STORE_SKU_MIGRATION_TARGETS,
    catalog_collector: Callable[[str, str], Mapping[str, Any]] | None = None,
    actor: object = "migration-v18",
    apply: bool = False,
    info_root: os.PathLike[str] | str | None = None,
) -> dict[str, Any]:
    """Preview or apply each store independently; reruns are content-idempotent."""

    plans, preview = prepare_store_sku_migration(
        client_id,
        targets=targets,
        catalog_collector=catalog_collector,
        info_root=info_root,
    )
    if not apply:
        return preview
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    results: list[dict[str, Any]] = []
    quarantined: dict[str, dict[str, Any]] = {}
    for plan in plans:
        migration_id = "store-sku-migration-" + plan.source_hash[:24]
        _record_migration(
            paths.client_id,
            plan,
            migration_id=migration_id,
            status="applying",
            info_root=paths.info_root,
        )
        try:
            result = publish_store_sku_generation(
                paths.client_id,
                plan.scope.as_dict(),
                canonical_documents=plan.canonical_documents,
                store_guidance=plan.store_guidance,
                sku_guidance=plan.sku_guidance,
                bindings=plan.bindings,
                migration_id=migration_id,
                actor=actor,
                info_root=paths.info_root,
            )
            _record_migration(
                paths.client_id,
                plan,
                migration_id=migration_id,
                status="applied",
                generation_id=str(result.get("generation_id") or ""),
                info_root=paths.info_root,
            )
            results.append({
                "store_ref": plan.scope.store_ref,
                "migration_id": migration_id,
                "changed": bool(result.get("changed")),
                "idempotent": bool(result.get("idempotent")),
                "generation_id": str(result.get("generation_id") or ""),
                "rollback_generation_id": str(result.get("previous_generation_id") or ""),
                "stats": result.get("stats") or {},
            })
            quarantined.update(plan.quarantined_legacy)
        except Exception:
            _record_migration(
                paths.client_id,
                plan,
                migration_id=migration_id,
                status="failed",
                info_root=paths.info_root,
            )
            raise
    quarantine_written = _quarantine_legacy_1599(
        paths.client_id,
        quarantined,
        info_root=paths.info_root,
    )
    return {
        **preview,
        "apply": True,
        "applied_count": len(results),
        "results": results,
        "legacy_1599_quarantined": quarantine_written,
    }


__all__ = [
    "INITIAL_STORE_SKU_MIGRATION_TARGETS",
    "migrate_store_sku_knowledge",
    "prepare_store_sku_migration",
]
