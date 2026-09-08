"""Shared validation and materialization helpers for store/SKU knowledge."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.modules.context_hub.contracts import (
    CURATION_SCHEMA_VERSION,
    ContextHubConflictError,
    ContextHubValidationError,
)
from backend.modules.context_hub.curation_records import (
    _ensure_curation_row,
    _public_curated_note,
    _read_curated_note,
    _validate_curated_content,
)
from backend.modules.context_hub.dlp import scan_dlp
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.findings import _has_blocker
from backend.modules.context_hub.metadata import _dump_frontmatter
from backend.modules.context_hub.path_safety import _assert_path_chain_safe
from backend.modules.context_hub.store_sku_contracts import (
    APPLICABLE_GUIDANCE_MAX_CHARS,
    CANONICAL_DOCUMENT_MAX_CHARS,
    STORE_SKU_MIGRATION_SCHEMA,
    StoreSkuBinding,
    StoreSkuScope,
    canonical_json,
    content_sha256,
    normalize_sku,
    store_directory_name,
    validate_integral_size,
)


_KNOWLEDGE_ROLES = {"canonical_sku", "store_guidance", "sku_guidance"}


def _opaque_actor(actor: object) -> str:
    return "actor-" + content_sha256(str(actor or "migration"))[:16]


def _scope_for_paths(paths: Any, value: Mapping[str, Any]) -> StoreSkuScope:
    expected_tenant = f"tenant:{paths.client_id}"
    scope = StoreSkuScope.from_mapping(value, tenant_scope=expected_tenant)
    if scope.tenant_scope != expected_tenant:
        raise ContextHubValidationError("tenant_scope diverge do tenant ligado pelo servidor.")
    return scope


def _sku_path_component(sku: object) -> str:
    normalized = normalize_sku(sku)
    safe = re.sub(r"[^A-Z0-9._-]+", "-", normalized).strip(".-")[:100]
    if not safe or safe in {".", ".."}:
        raise ContextHubValidationError("SKU nao gera caminho seguro no vault.")
    return safe


def _safe_json_load(raw: object, *, code: str) -> Any:
    try:
        return json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContextHubValidationError(code) from exc


def _validate_no_dlp(value: object, *, source_ref: str) -> None:
    if scan_dlp(canonical_json(value), source_ref=source_ref):
        raise ContextHubValidationError("Conteudo bloqueado pelo DLP do Context Hub.")


def _validate_documents(
    canonical_documents: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    sku_guidance: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    canonical: dict[str, dict[str, Any]] = {}
    for raw_sku, raw_document in canonical_documents.items():
        sku = normalize_sku(raw_sku)
        document = dict(raw_document) if isinstance(raw_document, Mapping) else {}
        if not document or normalize_sku(document.get("sku")) != sku:
            raise ContextHubValidationError("Documento canonico diverge do SKU associado.")
        if sku == "1599":
            raise ContextHubValidationError("SKU legado 1599 nao pode ser publicado.")
        validate_integral_size(
            document,
            CANONICAL_DOCUMENT_MAX_CHARS,
            code="canonical_document_too_large",
        )
        _validate_no_dlp(document, source_ref="canonical_store_sku")
        canonical[sku] = document
    if not canonical:
        raise ContextHubValidationError("Nenhum documento canonico elegivel para a loja.")
    from .guidance_examples import validate_guidance_examples
    general = dict(store_guidance) if isinstance(store_guidance, Mapping) else {}
    validate_guidance_examples(general)
    normalized_sku_guidance: dict[str, dict[str, Any]] = {}
    for raw_sku, raw_guidance in sku_guidance.items():
        sku = normalize_sku(raw_sku)
        if sku == "1599" or sku not in canonical:
            continue
        normalized_sku_guidance[sku] = (
            dict(raw_guidance) if isinstance(raw_guidance, Mapping) else {}
        )
    _validate_no_dlp(general, source_ref="store_guidance")
    for sku, guidance in normalized_sku_guidance.items():
        validate_guidance_examples(guidance, sku)
        _validate_no_dlp(guidance, source_ref=f"sku_guidance:{content_sha256(sku)[:12]}")
    for sku in canonical:
        validate_integral_size(
            {"general": general, "sku": normalized_sku_guidance.get(sku, {})},
            APPLICABLE_GUIDANCE_MAX_CHARS,
            code="applicable_guidance_too_large",
        )
    return canonical, normalized_sku_guidance


def _normalize_bindings(
    bindings: Sequence[Mapping[str, Any]],
    *,
    scope: StoreSkuScope,
    canonical_skus: set[str],
) -> list[StoreSkuBinding]:
    normalized: list[StoreSkuBinding] = []
    seen: dict[tuple[str, str], str] = {}
    for raw in bindings:
        binding = StoreSkuBinding.from_mapping(raw, site_id=scope.site_id)
        if binding.sku not in canonical_skus:
            raise ContextHubValidationError("Binding aponta para SKU sem documento canonico.")
        key = (binding.item_id, binding.variation_id)
        previous = seen.get(key)
        if previous and previous != binding.sku:
            raise ContextHubConflictError("Item/variacao associado a mais de um SKU.")
        if previous:
            continue
        seen[key] = binding.sku
        normalized.append(binding)
    if not normalized:
        raise ContextHubValidationError("Nenhum binding exato de item/SKU foi comprovado.")
    bound_skus = {binding.sku for binding in normalized}
    if bound_skus != canonical_skus:
        raise ContextHubValidationError("Documento canonico sem binding de catalogo exato.")
    return sorted(normalized, key=lambda value: (value.item_id, value.variation_id, value.sku))


def _source_hash(
    scope: StoreSkuScope,
    canonical: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    sku_guidance: Mapping[str, Mapping[str, Any]],
    bindings: Sequence[StoreSkuBinding],
) -> str:
    return content_sha256({
        "contract": STORE_SKU_MIGRATION_SCHEMA,
        "scope": scope.as_dict(),
        "canonical": canonical,
        "store_guidance": store_guidance,
        "sku_guidance": sku_guidance,
        "bindings": [binding.as_dict() for binding in bindings],
    })


def _canonical_frontmatter(
    scope: StoreSkuScope,
    *,
    sku: str,
    generation_id: str,
    document: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    document_hash = content_sha256(document)
    return {
        "id": f"jk:store-sku:{scope.store_ref}:{content_sha256(sku)[:20]}",
        "type": "store_sku_canonical",
        "managed": True,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": scope.tenant_scope,
        "sensitivity": "internal_catalog",
        "truth_class": "canonical",
        "required_permissions": ["full"],
        "surface": scope.surface,
        "source_version": "store-sku-knowledge-v1",
        "source_refs": [f"canonical-sku:{content_sha256(sku)[:20]}"],
        "source_hash": document_hash,
        "content_hash": document_hash,
        "generated_at": now,
        "scope_kind": "store_sku",
        "store_ref": scope.store_ref,
        "seller_id": scope.seller_id,
        "site_id": scope.site_id,
        "sku": sku,
        "knowledge_role": "canonical_sku",
        "generation_id": generation_id,
    }


def _guidance_frontmatter(
    scope: StoreSkuScope,
    *,
    sku: str,
    role: str,
    state: str,
    body: str,
    actor: str,
    now: str,
) -> dict[str, Any]:
    if role not in {"store_guidance", "sku_guidance"}:
        raise ContextHubValidationError("knowledge_role invalido para orientacao.")
    body_hash = content_sha256(body)
    identity_key = f"{scope.store_ref}:{role}:{sku}"
    return {
        "id": f"jk:curated:store-sku:{content_sha256(identity_key)[:24]}",
        "type": "curated_note",
        "managed": False,
        "status": state,
        "ai_usage": "allowed" if state == "approved" else "denied",
        "tenant_scope": scope.tenant_scope,
        "sensitivity": "internal",
        "truth_class": "human_curated",
        "required_permissions": ["full"],
        "surface": scope.surface,
        "source_version": "curation-v3",
        "source_refs": [f"store-guidance:{content_sha256(identity_key)[:24]}"],
        "source_hash": body_hash,
        "content_hash": body_hash,
        "generated_at": now,
        "context_schema": CURATION_SCHEMA_VERSION,
        "scope_kind": "store_sku" if sku else "store",
        "store_ref": scope.store_ref,
        "seller_id": scope.seller_id,
        "site_id": scope.site_id,
        "sku": sku,
        "knowledge_role": role,
        "authority": "advisory",
        "created_by": actor,
    }


def _guidance_body(title: str, value: Mapping[str, Any]) -> str:
    return (
        f"# {title}\n\n"
        "Os valores abaixo sao orientacoes editoriais; nao substituem evidencia tecnica "
        "nem dados operacionais atuais.\n\n"
        "```json\n"
        + json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n```"
    )


def _materialized_files(
    scope: StoreSkuScope,
    *,
    generation_id: str,
    canonical: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    sku_guidance: Mapping[str, Mapping[str, Any]],
    actor: str,
    now: str,
    include_curated: bool = True,
) -> list[tuple[Path, str, str, bool]]:
    store_dir = store_directory_name(scope.store_ref, scope.store_name)
    files: list[tuple[Path, str, str, bool]] = []
    for sku, document in sorted(canonical.items()):
        relative = Path("Lojas") / store_dir / "SKUs" / _sku_path_component(sku) / "Contexto.md"
        body = (
            f"# Contexto canonico do SKU {sku}\n\n```json\n"
            + json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```"
        )
        files.append((
            relative,
            _dump_frontmatter(
                _canonical_frontmatter(
                    scope,
                    sku=sku,
                    generation_id=generation_id,
                    document=document,
                    now=now,
                ),
                body,
            ),
            "generated",
            False,
        ))
    if not include_curated:
        return files
    general_body = _guidance_body("Orientacoes gerais da loja", store_guidance)
    general_metadata = _guidance_frontmatter(
        scope,
        sku="",
        role="store_guidance",
        state="approved",
        body=general_body,
        actor=actor,
        now=now,
    )
    files.append((
        Path("Lojas") / store_dir / "Orientacoes-Gerais.md",
        _dump_frontmatter(general_metadata, general_body),
        "curated",
        True,
    ))
    for sku, guidance in sorted(sku_guidance.items()):
        sku_body = _guidance_body(f"Orientacoes do SKU {sku}", guidance)
        sku_metadata = _guidance_frontmatter(
            scope,
            sku=sku,
            role="sku_guidance",
            state="approved",
            body=sku_body,
            actor=actor,
            now=now,
        )
        files.append((
            Path("Lojas") / store_dir / "SKUs" / _sku_path_component(sku) / "Orientacoes.md",
            _dump_frontmatter(sku_metadata, sku_body),
            "curated",
            True,
        ))
    return files


def _prune_stale_generated_notes(
    paths: Any,
    files: Sequence[tuple[Path, str, str, bool]],
    previous: dict[Path, str | None],
    *,
    scope: StoreSkuScope,
) -> None:
    expected: set[Path] = set()
    store_roots: set[Path] = set()
    for relative, _content, root_kind, _approved in files:
        if root_kind != "generated":
            continue
        target = paths.generated_dir / relative
        expected.add(target)
        if len(relative.parts) >= 2 and relative.parts[0] == "Lojas":
            store_roots.add(paths.generated_dir / relative.parts[0] / relative.parts[1])
    stores_root = paths.generated_dir / "Lojas"
    _assert_path_chain_safe(stores_root, paths.info_root)
    if stores_root.is_dir() and not stores_root.is_symlink():
        store_prefix = f"{scope.store_ref}--"
        for candidate in stores_root.iterdir():
            if (
                candidate.name.startswith(store_prefix)
                and candidate.is_dir()
                and not candidate.is_symlink()
            ):
                _assert_path_chain_safe(candidate, paths.info_root)
                store_roots.add(candidate)
    for store_root in store_roots:
        _assert_path_chain_safe(store_root, paths.info_root)
        if not store_root.exists():
            continue
        for candidate in store_root.rglob("Contexto.md"):
            _assert_path_chain_safe(candidate, paths.info_root)
            if candidate in expected:
                continue
            previous[candidate] = candidate.read_text(encoding="utf-8")
            candidate.unlink()


def _write_materialized_files(
    paths: Any,
    connection: sqlite3.Connection,
    files: Sequence[tuple[Path, str, str, bool]],
    *,
    scope: StoreSkuScope,
    actor: str,
    now: str,
) -> dict[Path, str | None]:
    previous: dict[Path, str | None] = {}
    try:
        _prune_stale_generated_notes(paths, files, previous, scope=scope)
        for relative, content, root_kind, approved in files:
            root = paths.generated_dir if root_kind == "generated" else paths.curated_dir
            target = root / relative
            _assert_path_chain_safe(target, paths.info_root)
            previous[target] = target.read_text(encoding="utf-8") if target.exists() else None
            _write_text_atomic(target, content)
            if approved:
                record = _read_curated_note(paths, target)
                if not record["valid"]:
                    raise ContextHubValidationError(
                        "Orientacao migrada falhou na validacao de curadoria."
                    )
                _ensure_curation_row(
                    connection,
                    record["relative_path"],
                    record["content_sha256"],
                    str(record.get("metadata", {}).get("id") or ""),
                )
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals
                    SET state='approved', validated_sha256=?, validated_at=?,
                        reviewed_by=?, reviewed_at=?, approved_by=?, approved_at=?, updated_at=?
                    WHERE relative_path=?
                    """,
                    (
                        record["content_sha256"], now, actor, now, actor, now, now,
                        record["relative_path"],
                    ),
                )
    except Exception:
        _restore_files(previous)
        raise
    return previous


def _restore_files(previous: Mapping[Path, str | None]) -> None:
    for target, content in reversed(list(previous.items())):
        if content is None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            try:
                _write_text_atomic(target, content)
            except OSError:
                pass


def _document_rows(
    generation_id: str,
    canonical: Mapping[str, Mapping[str, Any]],
    store_guidance: Mapping[str, Any],
    sku_guidance: Mapping[str, Mapping[str, Any]],
    *,
    actor: str,
    now: str,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for sku, document in sorted(canonical.items()):
        raw = canonical_json(document)
        digest = content_sha256(raw)
        rows.append((
            generation_id,
            f"canonical:{content_sha256(sku)[:24]}",
            "canonical_sku",
            sku,
            raw,
            digest,
            digest,
            "approved",
            actor,
            now,
        ))
    raw_general = canonical_json(store_guidance)
    general_hash = content_sha256(raw_general)
    rows.append((
        generation_id,
        "guidance:store",
        "store_guidance",
        "",
        raw_general,
        general_hash,
        general_hash,
        "approved",
        actor,
        now,
    ))
    for sku, guidance in sorted(sku_guidance.items()):
        raw = canonical_json(guidance)
        digest = content_sha256(raw)
        rows.append((
            generation_id,
            f"guidance:sku:{content_sha256(sku)[:24]}",
            "sku_guidance",
            sku,
            raw,
            digest,
            digest,
            "approved",
            actor,
            now,
        ))
    return rows


def _not_found(reason: str, *, unavailable: bool = False) -> dict[str, Any]:
    return {
        "found": False,
        "reason_code": reason,
        "unavailable": unavailable,
        "validity": {
            "status": "unavailable",
            "identity_verified": False,
            "hashes_verified": False,
            "approval_state": "unknown",
        },
        "canonical_document": {},
        "guidance": {"general": {}, "sku": {}},
        "conflicts": [],
        "gaps": [reason],
        "read_only": True,
    }


def _guidance_value_from_body(body: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*\})\s*```", body, flags=re.DOTALL | re.IGNORECASE)
    if match:
        decoded = _safe_json_load(match.group(1), code="store_guidance_json_invalid")
        if isinstance(decoded, Mapping):
            return dict(decoded)
    text = str(body or "")
    return {"orientacoes": text} if text.strip() else {}


def _approved_guidance_file(
    paths: Any,
    connection: sqlite3.Connection,
    target: Path,
    *,
    scope: StoreSkuScope,
    role: str,
    sku: str = "",
) -> dict[str, Any] | None:
    if not target.is_file():
        return None
    record = _read_curated_note(paths, target)
    metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    expected = {
        "tenant_scope": scope.tenant_scope,
        "store_ref": scope.store_ref,
        "seller_id": scope.seller_id,
        "site_id": scope.site_id,
        "surface": scope.surface,
        "scope_kind": "store_sku" if sku else "store",
        "knowledge_role": role,
        "sku": sku,
    }
    try:
        schema_version = int(metadata.get("context_schema") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != CURATION_SCHEMA_VERSION or any(
        str(metadata.get(key) or "").strip() != str(value or "").strip()
        for key, value in expected.items()
    ):
        raise ContextHubValidationError(
            "Orientacao aprovada diverge do tenant, loja, seller, site ou SKU solicitado."
        )
    row = connection.execute(
        """
        SELECT state, content_sha256 FROM context_hub_curated_approvals
        WHERE relative_path=? AND present=1
        """,
        (record["relative_path"],),
    ).fetchone()
    if (
        row is None
        or str(row["state"]) != "approved"
        or str(row["content_sha256"]) != str(record["content_sha256"])
        or not record["valid"]
    ):
        return None
    value = _guidance_value_from_body(str(record.get("body") or ""))
    # Plain Markdown and the legacy editorial key must reach the same fields
    # used by both the editor and the active store generation.
    if "orientacoes" in value:
        field = "notas" if role == "sku_guidance" else "orientacoes_perguntas"
        value.setdefault(field, value.pop("orientacoes"))
    if role == "sku_guidance" and "texto" in value:
        value.setdefault("notas", value.pop("texto"))
    return value


def _assert_editorial_skus_publishable(
    paths: Any,
    connection: sqlite3.Connection,
    scope: StoreSkuScope,
    canonical: Mapping[str, Any],
    editorial_paths: Mapping[str, list[Path]],
) -> None:
    for sku in set(editorial_paths) - set(canonical) - {""}:
        if _approved_guidance_file(
            paths, connection, editorial_paths[sku][0],
            scope=scope, role="sku_guidance", sku=sku,
        ) is not None:
            raise ContextHubValidationError(
                "Sincronize o cadastro na base de conhecimento desta loja "
                "antes de publicar orientacoes de novos SKUs."
            )


def _generation_knowledge(
    connection: sqlite3.Connection,
    generation_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT knowledge_role, sku, content_json
        FROM context_hub_store_sku_documents
        WHERE generation_id=?
        ORDER BY knowledge_role, sku
        """,
        (generation_id,),
    ).fetchall()
    canonical: dict[str, dict[str, Any]] = {}
    general: dict[str, Any] = {}
    sku_guidance: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = _safe_json_load(row["content_json"], code="knowledge_json_invalid")
        role = str(row["knowledge_role"])
        if role == "canonical_sku" and isinstance(value, Mapping):
            canonical[str(row["sku"])] = dict(value)
        elif role == "store_guidance" and isinstance(value, Mapping):
            general = dict(value)
        elif role == "sku_guidance" and isinstance(value, Mapping):
            sku_guidance[str(row["sku"])] = dict(value)
    if not canonical:
        raise ContextHubValidationError("Geracao por loja sem documento canonico.")
    return canonical, general, sku_guidance
