"""Portable, identity-bound Context Hub snapshot for Cadastro machine sync.

The transport intentionally contains no SQLite database, WAL/SHM file,
Obsidian configuration, lock or generated search index.  It carries only the
effective curated documents from the active global publication and the active
store/SKU generations required to rebuild the destination projections.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from fastapi import HTTPException


AI_CONTEXT_SCHEMA = "jk.shared-sync.cadastro-ai-context.v1"
AI_CONTEXT_EXTENSION_KEY = "ai_context"
AI_CONTEXT_EXTENSION_PATH = "extensions/cadastro-ai-context-v1.json"
AI_CONTEXT_LEDGER_SCHEMA = "jk.shared-sync.cadastro-ai-context-ledger.v1"
AI_CONTEXT_LEDGER_NAME = "shared-sync-cadastro-ai-context-v1.json"
AI_CONTEXT_FINGERPRINT_PATH = "@extensions/cadastro-ai-context-v1.json"

_MAX_NOTES = 2_000
_MAX_STORE_GENERATIONS = 100
_MAX_NOTE_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 50_000_000
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$")
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{value}" for value in range(1, 10)),
    *(f"lpt{value}" for value in range(1, 10)),
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _username_hash(username: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(username or "").strip().casefold())
    if not normalized:
        raise HTTPException(403, "A sincronizacao do contexto exige um usuario autenticado.")
    return _sha256_bytes(normalized.encode("utf-8"))


def _root_material(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": payload.get("schema"),
        "source_client_id": payload.get("source_client_id"),
        "source_user_hash": payload.get("source_user_hash"),
        "global_publication": payload.get("global_publication"),
        "notes": payload.get("notes"),
        "store_generations": payload.get("store_generations"),
    }


def _root_hash(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(_root_material(payload)).encode("utf-8"))


def _scope_key(scope: Mapping[str, Any]) -> str:
    return "|".join(
        str(scope.get(key) or "").strip()
        for key in ("store_ref", "seller_id", "site_id", "surface")
    )


def _safe_note_relative(value: object) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "").replace("\\", "/"))
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith(("/", "//"))
        or path.is_absolute()
        or path.suffix.casefold() != ".md"
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HTTPException(400, "O contexto de IA contem um caminho invalido.")
    for part in path.parts:
        stem = part.split(".", 1)[0].casefold()
        if (
            ":" in part
            or part.rstrip(" .") != part
            or stem in _WINDOWS_RESERVED
            or any(ord(char) < 32 for char in part)
        ):
            raise HTTPException(400, "O contexto de IA contem um caminho invalido.")
    normalized = path.as_posix()
    if len(normalized) > 500:
        raise HTTPException(400, "O contexto de IA contem um caminho invalido.")
    return normalized


def _resolve_store_scope(client_id: str, store_id: str) -> dict[str, str]:
    from backend.services.training_read_service import resolve_read_scope

    return resolve_read_scope(client_id, store_id)


def _assert_authorized_scope(client_id: str, raw: Mapping[str, Any]) -> dict[str, str]:
    from backend.modules.context_hub.store_sku_contracts import (
        STORE_SKU_PUBLIC_SURFACE,
        StoreSkuScope,
    )

    expected_tenant = f"tenant:{client_id}"
    scope = StoreSkuScope.from_mapping(raw, tenant_scope=expected_tenant)
    if scope.tenant_scope != expected_tenant:
        raise HTTPException(403, "O contexto de IA pertence a outro cliente.")
    authorized = _resolve_store_scope(client_id, scope.store_ref)
    expected = {
        "store_ref": str(authorized.get("store_ref") or ""),
        "seller_id": str(authorized.get("seller_id") or ""),
        "site_id": str(authorized.get("site_id") or "").upper(),
        "surface": str(authorized.get("surface") or STORE_SKU_PUBLIC_SURFACE),
    }
    observed = {
        "store_ref": scope.store_ref,
        "seller_id": scope.seller_id,
        "site_id": scope.site_id,
        "surface": scope.surface,
    }
    if observed != expected:
        raise HTTPException(
            409,
            detail={
                "code": "shared_sync_ai_context_store_identity_mismatch",
                "message": "A identidade de uma loja mudou. Atualize as lojas antes de sincronizar o contexto.",
            },
        )
    # A rename is presentation metadata, not a change of store identity.  Keep
    # the source name here because it participates in the already-published
    # generation hash; the immutable store/seller/site/surface tuple above is
    # what authorizes the transfer.
    return scope.as_dict()


def _active_global_snapshot(
    connection: Any,
    client_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from backend.modules.context_hub.contracts import CURATION_SCHEMA_VERSION
    from backend.modules.context_hub.metadata import _parse_frontmatter
    from backend.modules.context_hub.store_sku_contracts import STORE_SKU_PUBLIC_SURFACE

    active = connection.execute(
        """
        SELECT g.generation_id, g.source_hash
        FROM context_hub_active_generation a
        JOIN context_hub_generations g ON g.generation_id=a.generation_id
        WHERE a.singleton_id=1 AND g.status='active'
        """
    ).fetchone()
    if active is None:
        return {"active": False, "source_hash": "", "curated_count": 0}, []
    rows = connection.execute(
        """
        SELECT d.relative_path, d.content, d.doc_id,
               a.content_sha256 AS source_attestation_sha256
        FROM context_hub_documents d
        JOIN context_hub_generation_curated_approvals a
          ON a.generation_id=d.generation_id AND a.relative_path=d.relative_path
        WHERE d.generation_id=? AND d.relative_path LIKE '80_Curadoria/%'
        ORDER BY d.relative_path
        """,
        (str(active["generation_id"]),),
    ).fetchall()
    notes: list[dict[str, Any]] = []
    for row in rows:
        relative = _safe_note_relative(str(row["relative_path"])[len("80_Curadoria/") :])
        content = str(row["content"] or "").replace("\r\n", "\n")
        metadata, _body = _parse_frontmatter(content)
        try:
            schema = int(metadata.get("context_schema") or 0)
        except (TypeError, ValueError):
            schema = 0
        if schema == CURATION_SCHEMA_VERSION and str(
            metadata.get("scope_kind") or ""
        ) in {"store", "store_sku"}:
            try:
                _assert_authorized_scope(
                    client_id,
                    {
                        "tenant_scope": metadata.get("tenant_scope"),
                        "store_ref": metadata.get("store_ref"),
                        "store_name": metadata.get("store_name"),
                        "seller_id": metadata.get("seller_id"),
                        "site_id": metadata.get("site_id"),
                        "surface": metadata.get("surface") or STORE_SKU_PUBLIC_SURFACE,
                    },
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if exc.status_code == 403 and detail.get("code") == "store_access_denied":
                    continue
                raise
        data = content.encode("utf-8", errors="strict")
        notes.append(
            {
                "path": relative,
                "document_id": str(row["doc_id"] or ""),
                "content": content,
                "sha256": _sha256_bytes(data),
                "source_attestation_sha256": str(row["source_attestation_sha256"] or ""),
            }
        )
    return {
        "active": True,
        "source_hash": str(active["source_hash"] or ""),
        "curated_count": len(notes),
    }, notes


def _active_store_snapshots(connection: Any, client_id: str) -> list[dict[str, Any]]:
    from backend.modules.context_hub.store_sku_repository_support import (
        _generation_knowledge,
        _normalize_bindings,
        _source_hash,
    )
    from backend.modules.context_hub.store_sku_contracts import StoreSkuScope

    rows = connection.execute(
        """
        SELECT g.generation_id, g.store_ref, g.store_name, g.seller_id,
               g.site_id, g.surface, g.source_hash
        FROM context_hub_store_sku_active_generations a
        JOIN context_hub_store_sku_generations g ON g.generation_id=a.generation_id
        WHERE g.status='active'
        ORDER BY g.store_ref, g.seller_id, g.site_id, g.surface
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            scope = _assert_authorized_scope(
                client_id,
                {
                    "tenant_scope": f"tenant:{client_id}",
                    "store_ref": row["store_ref"],
                    "store_name": row["store_name"],
                    "seller_id": row["seller_id"],
                    "site_id": row["site_id"],
                    "surface": row["surface"],
                },
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code == 403 and detail.get("code") == "store_access_denied":
                # Cadastro no longer contains this store.  Omitting it creates
                # a causal tombstone on peers that imported it previously.
                continue
            raise
        exact = StoreSkuScope.from_mapping(scope, tenant_scope=f"tenant:{client_id}")
        generation_id = str(row["generation_id"])
        canonical, general, sku_guidance = _generation_knowledge(connection, generation_id)
        bindings = [
            {
                "item_id": str(binding["item_id"]),
                "variation_id": str(binding["variation_id"]),
                "sku": str(binding["sku"]),
            }
            for binding in connection.execute(
                """
                SELECT item_id, variation_id, sku
                FROM context_hub_store_sku_bindings
                WHERE generation_id=?
                ORDER BY item_id, variation_id, sku
                """,
                (generation_id,),
            ).fetchall()
        ]
        normalized_bindings = _normalize_bindings(
            bindings,
            scope=exact,
            canonical_skus=set(canonical),
        )
        source_hash = _source_hash(
            exact, canonical, general, sku_guidance, normalized_bindings,
        )
        if source_hash != str(row["source_hash"] or ""):
            raise HTTPException(409, "A geracao ativa do contexto de IA esta inconsistente.")
        result.append(
            {
                "scope": scope,
                "source_hash": source_hash,
                "canonical_documents": canonical,
                "store_guidance": general,
                "sku_guidance": sku_guidance,
                "bindings": [binding.as_dict() for binding in normalized_bindings],
            }
        )
    return result


def build_snapshot_bytes(
    client_id: str,
    username: str,
    *,
    info_root: os.PathLike[str] | str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Return deterministic portable bytes and their validated payload."""

    from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.storage import _connect

    paths = _tenant_paths(client_id, info_root=info_root)
    global_publication = {"active": False, "source_hash": "", "curated_count": 0}
    notes: list[dict[str, Any]] = []
    store_generations: list[dict[str, Any]] = []
    if paths.db_path.is_file():
        with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
            with _connect(paths) as connection:
                global_publication, notes = _active_global_snapshot(connection, paths.client_id)
                store_generations = _active_store_snapshots(connection, paths.client_id)
    payload: dict[str, Any] = {
        "schema": AI_CONTEXT_SCHEMA,
        "source_client_id": paths.client_id,
        "source_user_hash": _username_hash(username),
        "global_publication": global_publication,
        "notes": notes,
        "store_generations": store_generations,
        "root_hash": "",
    }
    payload["root_hash"] = _root_hash(payload)
    data = _canonical_json(payload).encode("utf-8")
    if len(data) > _MAX_TOTAL_BYTES:
        raise HTTPException(413, "O contexto de IA excede o limite de sincronizacao.")
    # Run the same closed-contract validator used by the receiver.
    validate_snapshot_bytes(
        data,
        expected_client_id=paths.client_id,
        expected_username=username,
        authorize_stores=True,
    )
    return data, payload


def extension_descriptor(data: bytes, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AI_CONTEXT_SCHEMA,
        "path": AI_CONTEXT_EXTENSION_PATH,
        "size": len(data),
        "sha256": _sha256_bytes(data),
        "root_hash": str(payload.get("root_hash") or ""),
        "note_count": len(payload.get("notes") or []),
        "store_generation_count": len(payload.get("store_generations") or []),
        "source_user_hash": str(payload.get("source_user_hash") or ""),
    }


def fingerprint_entry(data: bytes, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "relative_path": AI_CONTEXT_FINGERPRINT_PATH,
        "size": len(data),
        "sha256": _sha256_bytes(data),
        "mtime": 0,
    }


def validate_extension_descriptor(descriptor: object, data: bytes) -> dict[str, Any]:
    expected_keys = {
        "schema", "path", "size", "sha256", "root_hash", "note_count",
        "store_generation_count", "source_user_hash",
    }
    if not isinstance(descriptor, dict) or set(descriptor) != expected_keys:
        raise HTTPException(502, "O descritor do contexto de IA e invalido.")
    if descriptor.get("schema") != AI_CONTEXT_SCHEMA or descriptor.get("path") != AI_CONTEXT_EXTENSION_PATH:
        raise HTTPException(400, "A versao do contexto de IA nao e suportada.")
    if any(
        not isinstance(descriptor.get(key), int)
        or isinstance(descriptor.get(key), bool)
        for key in ("size", "note_count", "store_generation_count")
    ):
        raise HTTPException(502, "O descritor do contexto de IA e invalido.")
    size = descriptor["size"]
    note_count = descriptor["note_count"]
    generation_count = descriptor["store_generation_count"]
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "O descritor do contexto de IA e invalido.") from exc
    if (
        size != len(data)
        or size > _MAX_TOTAL_BYTES
        or not 0 <= note_count <= _MAX_NOTES
        or not 0 <= generation_count <= _MAX_STORE_GENERATIONS
        or _sha256_bytes(data) != str(descriptor.get("sha256") or "")
        or not _HASH_RE.fullmatch(str(descriptor.get("root_hash") or ""))
        or not _HASH_RE.fullmatch(str(descriptor.get("source_user_hash") or ""))
        or not isinstance(payload, dict)
        or descriptor["root_hash"] != payload.get("root_hash")
        or descriptor["source_user_hash"] != payload.get("source_user_hash")
        or not isinstance(payload.get("notes"), list)
        or not isinstance(payload.get("store_generations"), list)
        or note_count != len(payload["notes"])
        or generation_count != len(payload["store_generations"])
    ):
        raise HTTPException(502, "O descritor do contexto de IA diverge do pacote.")
    return descriptor


def validate_snapshot_bytes(
    data: bytes,
    *,
    expected_client_id: str,
    expected_username: str,
    authorize_stores: bool = True,
) -> dict[str, Any]:
    """Validate every byte and identity before Cadastro performs its first write."""

    from backend.modules.context_hub.curation_records import _validate_curated_content
    from backend.modules.context_hub.findings import _has_blocker
    from backend.modules.context_hub.metadata import _parse_frontmatter
    from backend.modules.context_hub.contracts import CURATION_SCHEMA_VERSION
    from backend.modules.context_hub.store_sku_contracts import (
        STORE_SKU_PUBLIC_SURFACE,
        StoreSkuScope,
        normalize_sku,
    )
    from backend.modules.context_hub.store_sku_repository_support import (
        _normalize_bindings,
        _source_hash,
        _validate_documents,
    )

    if not isinstance(data, (bytes, bytearray)) or len(data) > _MAX_TOTAL_BYTES:
        raise HTTPException(413, "O contexto de IA excede o limite de sincronizacao.")
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(502, "O contexto de IA recebido e invalido.") from exc
    expected_keys = {
        "schema", "source_client_id", "source_user_hash", "global_publication",
        "notes", "store_generations", "root_hash",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise HTTPException(502, "O contrato do contexto de IA recebido e invalido.")
    client_id = str(expected_client_id or "").strip()
    if payload.get("schema") != AI_CONTEXT_SCHEMA:
        raise HTTPException(400, "A versao do contexto de IA nao e suportada.")
    if str(payload.get("source_client_id") or "") != client_id:
        raise HTTPException(403, "O contexto de IA pertence a outro cliente.")
    if str(payload.get("source_user_hash") or "") != _username_hash(expected_username):
        raise HTTPException(403, "O contexto de IA pertence a outro usuario.")
    observed_root = str(payload.get("root_hash") or "")
    if not _HASH_RE.fullmatch(observed_root) or observed_root != _root_hash(payload):
        raise HTTPException(502, "O hash raiz do contexto de IA e invalido.")

    publication = payload.get("global_publication")
    if (
        not isinstance(publication, dict)
        or set(publication) != {"active", "source_hash", "curated_count"}
        or not isinstance(publication.get("active"), bool)
        or not isinstance(publication.get("curated_count"), int)
        or publication.get("curated_count", -1) < 0
        or (
            publication.get("active")
            and not _HASH_RE.fullmatch(str(publication.get("source_hash") or ""))
        )
        or (
            not publication.get("active")
            and str(publication.get("source_hash") or "")
        )
    ):
        raise HTTPException(502, "A publicacao global do contexto de IA e invalida.")

    notes = payload.get("notes")
    if not isinstance(notes, list) or len(notes) > _MAX_NOTES or publication["curated_count"] != len(notes):
        raise HTTPException(502, "A lista de notas do contexto de IA e invalida.")
    if notes and not publication["active"]:
        raise HTTPException(502, "Notas ativas sem publicacao global sao invalidas.")
    seen_paths: set[str] = set()
    seen_documents: set[str] = set()
    normalized_notes: list[dict[str, Any]] = []
    total_bytes = 0
    for note in notes:
        if not isinstance(note, dict) or set(note) != {
            "path", "document_id", "content", "sha256", "source_attestation_sha256",
        }:
            raise HTTPException(502, "Uma nota do contexto de IA e invalida.")
        relative = _safe_note_relative(note.get("path"))
        collision_key = unicodedata.normalize("NFKC", relative).casefold()
        document_id = str(note.get("document_id") or "")
        if collision_key in seen_paths or document_id in seen_documents or not _DOCUMENT_ID_RE.fullmatch(document_id):
            raise HTTPException(502, "O contexto de IA contem notas duplicadas ou invalidas.")
        seen_paths.add(collision_key)
        seen_documents.add(document_id)
        content = note.get("content")
        if not isinstance(content, str):
            raise HTTPException(502, "O conteudo de uma nota de IA e invalido.")
        try:
            raw = content.replace("\r\n", "\n").encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise HTTPException(502, "Uma nota do contexto de IA nao esta em UTF-8.") from exc
        total_bytes += len(raw)
        if len(raw) > _MAX_NOTE_BYTES or total_bytes > _MAX_TOTAL_BYTES:
            raise HTTPException(413, "O contexto de IA excede o limite de sincronizacao.")
        digest = str(note.get("sha256") or "")
        attestation = str(note.get("source_attestation_sha256") or "")
        if digest != _sha256_bytes(raw) or not _HASH_RE.fullmatch(attestation):
            raise HTTPException(502, "O hash de uma nota do contexto de IA e invalido.")
        metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        if str(metadata.get("id") or "") != document_id:
            raise HTTPException(502, "A identidade de uma nota do contexto de IA diverge.")
        findings = _validate_curated_content(metadata, body, source_ref=relative)
        if _has_blocker(findings):
            raise HTTPException(400, "Uma nota do contexto de IA foi bloqueada pela validacao.")
        tenant_scope = str(metadata.get("tenant_scope") or "")
        if tenant_scope != f"tenant:{client_id}":
            raise HTTPException(403, "Uma nota do contexto de IA pertence a outro cliente.")
        try:
            schema = int(metadata.get("context_schema") or 0)
        except (TypeError, ValueError):
            schema = 0
        scope: dict[str, str] | None = None
        if schema == CURATION_SCHEMA_VERSION:
            if str(metadata.get("scope_kind") or "") == "store_sku":
                normalize_sku(metadata.get("sku"))
            raw_scope = {
                "tenant_scope": tenant_scope,
                "store_ref": metadata.get("store_ref"),
                "store_name": metadata.get("store_name"),
                "seller_id": metadata.get("seller_id"),
                "site_id": metadata.get("site_id"),
                "surface": metadata.get("surface") or STORE_SKU_PUBLIC_SURFACE,
            }
            scope = (
                _assert_authorized_scope(client_id, raw_scope)
                if authorize_stores
                else StoreSkuScope.from_mapping(raw_scope, tenant_scope=tenant_scope).as_dict()
            )
        normalized_notes.append({**note, "path": relative, "content": content.replace("\r\n", "\n"), "scope": scope})

    generations = payload.get("store_generations")
    if not isinstance(generations, list) or len(generations) > _MAX_STORE_GENERATIONS:
        raise HTTPException(502, "As geracoes por loja do contexto de IA sao invalidas.")
    seen_scopes: set[str] = set()
    normalized_generations: list[dict[str, Any]] = []
    for generation in generations:
        if not isinstance(generation, dict) or set(generation) != {
            "scope", "source_hash", "canonical_documents", "store_guidance",
            "sku_guidance", "bindings",
        }:
            raise HTTPException(502, "Uma geracao por loja do contexto de IA e invalida.")
        raw_scope = generation.get("scope")
        if not isinstance(raw_scope, dict):
            raise HTTPException(502, "A identidade de uma geracao por loja e invalida.")
        scope_dict = (
            _assert_authorized_scope(client_id, raw_scope)
            if authorize_stores
            else StoreSkuScope.from_mapping(raw_scope, tenant_scope=f"tenant:{client_id}").as_dict()
        )
        exact = StoreSkuScope.from_mapping(scope_dict, tenant_scope=f"tenant:{client_id}")
        key = _scope_key(scope_dict)
        if key in seen_scopes:
            raise HTTPException(502, "O contexto de IA contem geracoes duplicadas para uma loja.")
        seen_scopes.add(key)
        canonical_raw = generation.get("canonical_documents")
        general_raw = generation.get("store_guidance")
        sku_raw = generation.get("sku_guidance")
        bindings_raw = generation.get("bindings")
        if not all(isinstance(value, dict) for value in (canonical_raw, general_raw, sku_raw)) or not isinstance(bindings_raw, list):
            raise HTTPException(502, "O conteudo de uma geracao por loja e invalido.")
        try:
            canonical, sku_guidance = _validate_documents(canonical_raw, general_raw, sku_raw)
            bindings = _normalize_bindings(bindings_raw, scope=exact, canonical_skus=set(canonical))
            source_hash = _source_hash(exact, canonical, general_raw, sku_guidance, bindings)
        except Exception as exc:
            raise HTTPException(400, "Uma geracao por loja do contexto de IA foi rejeitada.") from exc
        if source_hash != str(generation.get("source_hash") or ""):
            raise HTTPException(502, "O hash de uma geracao por loja do contexto de IA e invalido.")
        normalized_generations.append(
            {
                "scope": scope_dict,
                "source_hash": source_hash,
                "canonical_documents": canonical,
                "store_guidance": dict(general_raw),
                "sku_guidance": sku_guidance,
                "bindings": [binding.as_dict() for binding in bindings],
            }
        )
    return {
        "payload": payload,
        "client_id": client_id,
        "source_user_hash": str(payload["source_user_hash"]),
        "root_hash": observed_root,
        "global_publication": dict(publication),
        "notes": normalized_notes,
        "store_generations": normalized_generations,
    }


def _ledger_path(paths: Any) -> Path:
    return paths.internal_dir / AI_CONTEXT_LEDGER_NAME


def _read_ledger(paths: Any, plan: Mapping[str, Any]) -> dict[str, Any]:
    path = _ledger_path(paths)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "schema": AI_CONTEXT_LEDGER_SCHEMA,
            "source_client_id": plan["client_id"],
            "source_user_hash": plan["source_user_hash"],
            "root_hash": "",
            "global_publication_active": False,
            "notes": {},
            "store_generations": {},
            "finalized": False,
        }
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "O controle local do contexto sincronizado esta invalido.") from exc
    expected = {
        "schema", "source_client_id", "source_user_hash", "root_hash",
        "global_publication_active", "notes", "store_generations", "finalized",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema") != AI_CONTEXT_LEDGER_SCHEMA
        or value.get("source_client_id") != plan["client_id"]
        or value.get("source_user_hash") != plan["source_user_hash"]
        or not isinstance(value.get("global_publication_active"), bool)
        or not isinstance(value.get("notes"), dict)
        or not isinstance(value.get("store_generations"), dict)
        or not isinstance(value.get("finalized"), bool)
    ):
        raise HTTPException(409, "O controle local do contexto sincronizado diverge do usuario atual.")
    return value


def _active_store_hashes(paths: Any) -> dict[str, str]:
    if not paths.db_path.is_file():
        return {}
    from backend.modules.context_hub.storage import _connect

    with _connect(paths) as connection:
        rows = connection.execute(
            """
            SELECT g.store_ref, g.seller_id, g.site_id, g.surface, g.source_hash
            FROM context_hub_store_sku_active_generations a
            JOIN context_hub_store_sku_generations g ON g.generation_id=a.generation_id
            WHERE g.status='active'
            """
        ).fetchall()
    return {
        "|".join(str(row[key] or "") for key in ("store_ref", "seller_id", "site_id", "surface")):
        str(row["source_hash"] or "")
        for row in rows
    }


def stage_snapshot(plan: dict[str, Any], tenant_abs: str) -> dict[str, Any]:
    """Stage curated files and a causal ledger inside the Cadastro rollback boundary."""

    from backend.modules.context_hub.curation_records import _read_curated_note
    from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
    from backend.modules.context_hub.path_safety import _assert_path_chain_safe
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.storage import _connect
    from backend.modules.context_hub.filesystem import _write_text_atomic
    from backend.services.shared_sync_cadastro_transaction import enlist_context_paths

    info_root = os.path.dirname(os.path.abspath(tenant_abs))
    paths = _tenant_paths(plan["client_id"], info_root=info_root)
    if os.path.normcase(str(paths.tenant_dir)) != os.path.normcase(os.path.abspath(tenant_abs)):
        raise HTTPException(409, "O destino do contexto de IA diverge do cliente do Cadastro.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        ledger = _read_ledger(paths, plan)
        previously_synchronized = bool(str(ledger.get("root_hash") or ""))
        # A first empty snapshot means that this producer has never prepared
        # Context Hub.  After a prior import, the same empty state is a causal
        # tombstone and must retire only content tracked by this ledger.
        if (
            not plan["global_publication"]["active"]
            and not plan["store_generations"]
            and not previously_synchronized
        ):
            return {
                **plan,
                "staged": False,
                "preserved": True,
                "deleted_paths": [],
                "removed_store_generations": [],
                "previous_global_publication_active": False,
            }
        previous_notes = {str(key): str(value) for key, value in ledger["notes"].items()}
        incoming_notes = {note["path"]: note for note in plan["notes"]}
        targets = [paths.curated_dir / relative for relative in sorted(set(previous_notes) | set(incoming_notes))]
        for target in targets:
            _assert_path_chain_safe(target, paths.info_root)

        conflicts: list[str] = []
        for relative, note in incoming_notes.items():
            target = paths.curated_dir / relative
            if not target.exists():
                if ledger["finalized"] and relative in previous_notes:
                    conflicts.append(relative)
                continue
            current_hash = _sha256_bytes(target.read_bytes())
            if current_hash == note["sha256"]:
                continue
            if previous_notes.get(relative) != current_hash:
                conflicts.append(relative)
        deleted_paths: list[str] = []
        for relative, previous_hash in previous_notes.items():
            if relative in incoming_notes:
                continue
            target = paths.curated_dir / relative
            if target.exists() and _sha256_bytes(target.read_bytes()) != previous_hash:
                conflicts.append(relative)
            elif target.exists():
                deleted_paths.append(relative)

        # A local approved note outside the synchronized snapshot would enter a
        # rebuilt global generation and silently change the effective context.
        if plan["global_publication"]["active"] and paths.db_path.is_file():
            with _connect(paths) as connection:
                approved = connection.execute(
                    """
                    SELECT relative_path, content_sha256
                    FROM context_hub_curated_approvals
                    WHERE present=1 AND state='approved'
                    """
                ).fetchall()
            for row in approved:
                relative = str(row["relative_path"] or "")
                if relative not in incoming_notes and relative not in previous_notes:
                    conflicts.append(relative)

        current_store_hashes = _active_store_hashes(paths)
        previous_store_hashes = {
            str(key): str(value) for key, value in ledger["store_generations"].items()
        }
        incoming_store_hashes = {
            _scope_key(item["scope"]): item["source_hash"]
            for item in plan["store_generations"]
        }
        for key, incoming_hash in incoming_store_hashes.items():
            current_hash = current_store_hashes.get(key, "")
            if ledger["finalized"] and key in previous_store_hashes and not current_hash:
                conflicts.append("store:" + key)
                continue
            if current_hash and current_hash != incoming_hash and previous_store_hashes.get(key) != current_hash:
                conflicts.append("store:" + key)
        removed_store_generations: list[dict[str, str]] = []
        for key, previous_hash in previous_store_hashes.items():
            if key in incoming_store_hashes:
                continue
            current_hash = current_store_hashes.get(key, "")
            if current_hash and current_hash != previous_hash:
                conflicts.append("store:" + key)
                continue
            parts = key.split("|")
            if len(parts) != 4 or not all(parts):
                raise HTTPException(409, "O controle local de uma loja sincronizada esta invalido.")
            removed_store_generations.append(
                {
                    "store_ref": parts[0],
                    "seller_id": parts[1],
                    "site_id": parts[2],
                    "surface": parts[3],
                    "source_hash": previous_hash,
                }
            )
        if conflicts:
            raise HTTPException(
                409,
                detail={
                    "code": "shared_sync_ai_context_conflict",
                    "message": "O contexto de IA foi alterado nesta maquina. Sincronize ou revise o conflito antes de continuar.",
                    "count": len(set(conflicts)),
                },
            )

        ledger_path = _ledger_path(paths)
        enlist_context_paths(
            tenant_abs,
            [*(str(target) for target in targets), str(ledger_path)],
        )
        paths.curated_dir.mkdir(parents=True, exist_ok=True)
        for relative in deleted_paths:
            try:
                (paths.curated_dir / relative).unlink()
            except FileNotFoundError:
                pass
        for relative, note in incoming_notes.items():
            target = paths.curated_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or _sha256_bytes(target.read_bytes()) != note["sha256"]:
                _write_text_atomic(target, note["content"])
            record = _read_curated_note(paths, target)
            if record["content_sha256"] != note["sha256"] or not record["valid"]:
                raise HTTPException(409, "Uma nota do contexto de IA mudou durante a aplicacao.")

        ledger_value = {
            "schema": AI_CONTEXT_LEDGER_SCHEMA,
            "source_client_id": plan["client_id"],
            "source_user_hash": plan["source_user_hash"],
            "root_hash": plan["root_hash"],
            "global_publication_active": bool(plan["global_publication"]["active"]),
            "notes": {relative: note["sha256"] for relative, note in sorted(incoming_notes.items())},
            "store_generations": dict(sorted(incoming_store_hashes.items())),
            "finalized": False,
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(ledger_path, _canonical_json(ledger_value))
    return {
        **plan,
        "staged": True,
        "preserved": False,
        "deleted_paths": deleted_paths,
        "removed_store_generations": removed_store_generations,
        "previous_global_publication_active": bool(
            ledger.get("global_publication_active")
        ),
    }


def finalize_snapshot(staged: dict[str, Any], tenant_abs: str) -> dict[str, Any]:
    """Rebuild local publications after Cadastro and curated files committed."""

    if not staged.get("staged"):
        return {
            "status": "preserved",
            "root_hash": staged.get("root_hash") or "",
            "note_count": 0,
            "store_generation_count": 0,
        }
    from backend.modules.context_hub.curation_records import _ensure_curation_row, _read_curated_note
    from backend.modules.context_hub.bootstrap import bootstrap_context_hub
    from backend.modules.context_hub.locking import _exclusive_file_lock, _tenant_thread_lock
    from backend.modules.context_hub.paths import _tenant_paths
    from backend.modules.context_hub.runtime import _utc_now
    from backend.modules.context_hub.storage import _connect
    from backend.modules.context_hub.publication import publish_curated_context
    from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation

    info_root = os.path.dirname(os.path.abspath(tenant_abs))
    bootstrap_context_hub(staged["client_id"], info_root=info_root)
    paths = _tenant_paths(staged["client_id"], info_root=info_root)
    actor = "actor-" + staged["source_user_hash"][:16]
    now = _utc_now()
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        records: list[dict[str, Any]] = []
        for note in staged["notes"]:
            target = paths.curated_dir / note["path"]
            record = _read_curated_note(paths, target)
            if record["content_sha256"] != note["sha256"] or not record["valid"]:
                raise HTTPException(409, "O contexto de IA mudou antes da publicacao local.")
            records.append(record)
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for record in records:
                _ensure_curation_row(
                    connection,
                    record["relative_path"],
                    record["content_sha256"],
                    str(record.get("metadata", {}).get("id") or ""),
                )
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals SET
                        state='approved', present=1, missing_at=NULL,
                        validated_sha256=?, validated_at=?, reviewed_by=?, reviewed_at=?,
                        approved_by=?, approved_at=?, rejected_by=NULL, rejected_at=NULL,
                        rejection_reason=NULL, updated_at=?
                    WHERE relative_path=?
                    """,
                    (
                        record["content_sha256"], now, actor, now, actor, now, now,
                        record["relative_path"],
                    ),
                )
            for relative in staged.get("deleted_paths") or []:
                connection.execute(
                    """
                    UPDATE context_hub_curated_approvals SET
                        state='draft', present=0, missing_at=?, validated_sha256=NULL,
                        validated_at=NULL, reviewed_by=NULL, reviewed_at=NULL,
                        approved_by=NULL, approved_at=NULL, updated_at=?
                    WHERE relative_path=?
                    """,
                    (now, now, relative),
                )
            connection.commit()

    global_result: dict[str, Any] = {"status": "absent"}
    if (
        staged["global_publication"]["active"]
        or staged.get("previous_global_publication_active")
    ):
        global_result = publish_curated_context(
            staged["client_id"],
            reason="shared_sync_cadastro_context",
            force=True,
            info_root=paths.info_root,
        )
        if not global_result.get("success") or global_result.get("status") != "active":
            raise HTTPException(503, "O Cadastro foi aplicado, mas a publicacao local do contexto de IA precisa ser repetida.")

    store_results: list[dict[str, Any]] = []
    for generation in staged["store_generations"]:
        result = publish_store_sku_generation(
            staged["client_id"],
            generation["scope"],
            canonical_documents=generation["canonical_documents"],
            store_guidance=generation["store_guidance"],
            sku_guidance=generation["sku_guidance"],
            bindings=generation["bindings"],
            migration_id="shared-sync-" + staged["root_hash"][:24],
            actor=actor,
            preserve_curated_files=True,
            info_root=paths.info_root,
        )
        store_results.append(result)
        try:
            from backend.modules.context_hub.training_index_worker import request_refresh

            request_refresh(
                staged["client_id"],
                generation["scope"],
                info_root=paths.info_root,
                immediate=True,
            )
        except Exception:
            # The published source remains authoritative; the worker retries on
            # first read and during its periodic reconciliation.
            pass

    removed_store_generations = list(staged.get("removed_store_generations") or [])
    removed_count = 0
    if removed_store_generations:
        with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
            with _connect(paths) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for removed in removed_store_generations:
                        row = connection.execute(
                            """
                            SELECT a.generation_id, g.source_hash
                            FROM context_hub_store_sku_active_generations a
                            JOIN context_hub_store_sku_generations g
                              ON g.generation_id=a.generation_id
                            WHERE a.store_ref=? AND a.seller_id=?
                              AND a.site_id=? AND a.surface=?
                            """,
                            tuple(
                                removed[key]
                                for key in ("store_ref", "seller_id", "site_id", "surface")
                            ),
                        ).fetchone()
                        if row is None:
                            continue
                        if str(row["source_hash"] or "") != removed["source_hash"]:
                            raise HTTPException(
                                409,
                                "O contexto de uma loja mudou durante a finalizacao.",
                            )
                        connection.execute(
                            """
                            DELETE FROM context_hub_store_sku_active_generations
                            WHERE store_ref=? AND seller_id=? AND site_id=? AND surface=?
                            """,
                            tuple(
                                removed[key]
                                for key in ("store_ref", "seller_id", "site_id", "surface")
                            ),
                        )
                        connection.execute(
                            """
                            UPDATE context_hub_store_sku_generations
                            SET status='superseded', superseded_at=?
                            WHERE generation_id=? AND status='active'
                            """,
                            (now, str(row["generation_id"])),
                        )
                        removed_count += 1
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        for removed in removed_store_generations:
            try:
                from backend.modules.context_hub.training_index_worker import request_refresh

                request_refresh(
                    staged["client_id"],
                    {
                        "tenant_scope": f"tenant:{staged['client_id']}",
                        "store_ref": removed["store_ref"],
                        "store_name": "",
                        "seller_id": removed["seller_id"],
                        "site_id": removed["site_id"],
                        "surface": removed["surface"],
                    },
                    info_root=paths.info_root,
                    immediate=True,
                )
            except Exception:
                pass

    ledger_path = _ledger_path(paths)
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        ledger = _read_ledger(paths, staged)
        if ledger.get("root_hash") != staged["root_hash"]:
            raise HTTPException(409, "O contexto de IA mudou durante a finalizacao.")
        ledger["finalized"] = True
        from backend.modules.context_hub.filesystem import _write_text_atomic

        _write_text_atomic(ledger_path, _canonical_json(ledger))
    return {
        "status": "applied",
        "root_hash": staged["root_hash"],
        "note_count": len(staged["notes"]),
        "store_generation_count": len(store_results),
        "removed_store_generation_count": removed_count,
        "global_generation_id": str(global_result.get("generation_id") or ""),
    }


__all__ = [
    "AI_CONTEXT_EXTENSION_KEY",
    "AI_CONTEXT_EXTENSION_PATH",
    "AI_CONTEXT_FINGERPRINT_PATH",
    "AI_CONTEXT_SCHEMA",
    "build_snapshot_bytes",
    "extension_descriptor",
    "finalize_snapshot",
    "fingerprint_entry",
    "stage_snapshot",
    "validate_extension_descriptor",
    "validate_snapshot_bytes",
]
