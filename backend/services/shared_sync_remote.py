"""Shared Sync Firestore upload, metadata, and bundle download helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import Depends, Header, HTTPException
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from backend.schemas import (
    SharedSyncConfigRequest,
    SharedSyncMachineConfigRequest,
    SharedSyncRunRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserLinkRunRequest,
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


_SHARED_SYNC_POINTER_CAS_FALLBACK_LOCK = threading.RLock()
_SHARED_SYNC_V2_AUTHORITY_SUFFIX = "__v2_authority"


def configure_shared_sync_remote_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_firestore_required():
    db = _firebase_db()
    if db is None:
        detalhe = FIREBASE_AUTH_LAST_ERROR or "Firebase nao configurado para sincronizacao compartilhada."
        raise HTTPException(status_code=503, detail=detalhe)
    return db


def _shared_sync_encryption_secret() -> bytes:
    secret = str(os.getenv("JK_SHARED_SYNC_ENCRYPTION_SECRET") or "").strip()
    if not secret:
        getter = globals().get("_google_login_client_secret")
        if callable(getter):
            try:
                secret = str(getter() or "").strip()
            except Exception:
                secret = ""
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Chave de criptografia da sincronizacao nao configurada.",
        )
    return secret.encode("utf-8")


def _shared_sync_fernet(bundle_id: str, secret: Optional[bytes] = None) -> Fernet:
    salt = hashlib.sha256(("jk-shared-sync-v2|" + str(bundle_id or "")).encode("utf-8")).digest()
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=210_000,
    ).derive(secret if isinstance(secret, bytes) and secret else _shared_sync_encryption_secret())
    return Fernet(base64.urlsafe_b64encode(key))


def _shared_sync_encrypt_bundle(bundle_id: str, bundle: bytes, secret: Optional[bytes] = None) -> bytes:
    return _shared_sync_fernet(bundle_id, secret).encrypt(bundle or b"")


def _shared_sync_decrypt_bundle(bundle_id: str, encrypted: bytes, secret: Optional[bytes] = None) -> bytes:
    try:
        return _shared_sync_fernet(bundle_id, secret).decrypt(encrypted or b"")
    except InvalidToken:
        raise HTTPException(status_code=502, detail="Pacote criptografado invalido ou chave incompatível.")

def _shared_sync_delete_chunks(db, bundle_id: str) -> None:
    try:
        coll = db.collection(_firebase_shared_sync_chunks_collection_name())
        for snap in coll.where("bundle_id", "==", bundle_id).stream():
            snap.reference.delete()
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao limpar chunks antigos: %s", exc)


def _shared_sync_cleanup_old_snapshots(db, pointer_id: str, current_snapshot_id: str) -> None:
    """Retem os dois snapshots mais recentes e nunca remove o snapshot apontado."""
    try:
        coll = db.collection(_firebase_shared_sync_collection_name())
        authority_id = _shared_sync_v2_authority_id(pointer_id)

        def protected_snapshot_ids() -> set[str]:
            protected = {str(current_snapshot_id or "").strip()}
            for doc_id in (authority_id, pointer_id):
                snap = coll.document(doc_id).get()
                if not snap.exists:
                    continue
                snapshot_id = _shared_sync_pointer_snapshot_id(snap)
                if snapshot_id:
                    protected.add(snapshot_id)
            protected.discard("")
            return protected

        protected = protected_snapshot_ids()
        snapshots = []
        for snap in coll.where("pointer_id", "==", pointer_id).stream():
            data = snap.to_dict() or {}
            if str(data.get("status") or "") != "complete":
                continue
            snapshots.append((int(data.get("updated_ts") or 0), snap.id, data))
        snapshots.sort(reverse=True)
        cutoff = int(time.time()) - (7 * 24 * 60 * 60)
        for index, (_ts, snapshot_id, data) in enumerate(snapshots):
            if snapshot_id in protected or index < 2 or int(data.get("updated_ts") or 0) >= cutoff:
                continue
            # A autoridade pode mudar depois da listagem. Releia imediatamente
            # antes de excluir para nunca coletar o snapshot que venceu outro CAS.
            protected = protected_snapshot_ids()
            if snapshot_id in protected:
                continue
            _shared_sync_delete_chunks(db, snapshot_id)
            coll.document(snapshot_id).delete()
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao limpar snapshots antigos: %s", exc)


def _shared_sync_pointer_snapshot_id(snapshot) -> Optional[str]:
    if not snapshot.exists:
        return None
    data = snapshot.to_dict()
    if not isinstance(data, dict) or not data:
        raise HTTPException(
            status_code=409,
            detail="Metadados do snapshot remoto atual estao invalidos.",
        )
    return str(data.get("snapshot_id") or data.get("id") or snapshot.id).strip()


def _shared_sync_v2_authority_id(bundle_id: str) -> str:
    return f"{str(bundle_id or '').strip()}{_SHARED_SYNC_V2_AUTHORITY_SUFFIX}"


def _shared_sync_pointer_fail_closed_meta(meta: dict) -> dict:
    """Espelho legado sem chunks: clientes antigos param em vez de ler v1 stale."""
    pointer = dict(meta or {})
    pointer["chunk_count"] = 0
    pointer["bundle_b64_chars"] = 0
    pointer["legacy_reader_blocked"] = True
    return pointer


def _shared_sync_guard_expectation_from_meta(
    meta: Optional[dict],
    bundle_id: str,
) -> Optional[dict]:
    if not isinstance(meta, dict) or not meta:
        return None
    return {
        "pointer_id": str(
            meta.get("_guard_pointer_id") or bundle_id or ""
        ).strip(),
        "revision": _shared_sync_pointer_revision_from_meta(meta, bundle_id),
    }


def _shared_sync_pointer_revision_from_meta(
    meta: Optional[dict],
    fallback_id: str = "",
) -> Optional[str]:
    """Cria a precondicao CAS; v1 precisa da revisao, pois seu ID era mutavel."""
    if not isinstance(meta, dict) or not meta:
        return None
    try:
        schema = int(meta.get("schema") or 0)
    except (TypeError, ValueError):
        schema = 0
    if schema == 2 and bool(meta.get("encrypted")):
        snapshot_id = str(
            meta.get("snapshot_id") or meta.get("id") or fallback_id
        ).strip()
        return f"v2:{snapshot_id}"

    revision_fields = {
        key: str(meta.get(key) or "")
        for key in (
            "id",
            "schema",
            "encrypted",
            "scope",
            "snapshot_hash",
            "bundle_sha256",
            "updated_at",
            "updated_ts",
            "chunk_count",
            "bundle_bytes",
            "bundle_b64_chars",
            "file_count",
            "stores_count",
        )
    }
    revision_fields["id"] = revision_fields["id"] or str(fallback_id or "")
    encoded = json.dumps(
        revision_fields,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"legacy:{hashlib.sha256(encoded).hexdigest()}"


def _shared_sync_pointer_revision(snapshot) -> Optional[str]:
    if not snapshot.exists:
        return None
    data = snapshot.to_dict()
    if not isinstance(data, dict) or not data:
        raise HTTPException(
            status_code=409,
            detail="Metadados do snapshot remoto atual estao invalidos.",
        )
    return _shared_sync_pointer_revision_from_meta(data, snapshot.id)


def _shared_sync_publicar_pointer_cas(
    db,
    bundle_id: str,
    meta: dict,
    expected_revision: Any,
) -> None:
    """Publica autoridade v2 sem permitir que writers v1 a sobrescrevam."""
    collection = db.collection(_firebase_shared_sync_collection_name())
    pointer_ref = collection.document(bundle_id)
    authority_id = _shared_sync_v2_authority_id(bundle_id)
    authority_ref = collection.document(authority_id)
    pointer_meta = _shared_sync_pointer_fail_closed_meta(meta)

    def validar(source_snapshot, authority_snapshot) -> None:
        if isinstance(expected_revision, dict):
            expected_pointer_id = str(
                expected_revision.get("pointer_id") or ""
            ).strip()
            expected_value = expected_revision.get("revision")
            if expected_pointer_id not in {bundle_id, authority_id}:
                raise HTTPException(
                    status_code=409,
                    detail="A origem autoritativa do snapshot remoto mudou.",
                )
            if expected_pointer_id != authority_id and authority_snapshot.exists:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "O snapshot remoto mudou durante o envio. Importe os dados "
                        "mais recentes e tente novamente."
                    ),
                )
            atual_revision = _shared_sync_pointer_revision(source_snapshot)
        elif isinstance(expected_revision, str) and not expected_revision.startswith(
            ("v2:", "legacy:")
        ):
            # Compatibilidade interna com chamadas antigas/testes que usavam o ID.
            expected_value = expected_revision
            atual_snapshot = (
                authority_snapshot if authority_snapshot.exists else source_snapshot
            )
            atual_revision = _shared_sync_pointer_snapshot_id(atual_snapshot)
        else:
            expected_value = expected_revision
            atual_snapshot = (
                authority_snapshot if authority_snapshot.exists else source_snapshot
            )
            atual_revision = _shared_sync_pointer_revision(atual_snapshot)
        if atual_revision != expected_value:
            raise HTTPException(
                status_code=409,
                detail=(
                    "O snapshot remoto mudou durante o envio. Importe os dados "
                    "mais recentes e tente novamente."
                ),
            )

    transaction_factory = getattr(db, "transaction", None)
    if callable(transaction_factory):
        try:
            from google.cloud import firestore as google_firestore

            transaction = transaction_factory()

            @google_firestore.transactional
            def publish(transaction_obj):
                authority_snapshot = authority_ref.get(transaction=transaction_obj)
                if isinstance(expected_revision, dict):
                    expected_pointer_id = str(
                        expected_revision.get("pointer_id") or ""
                    ).strip()
                    source_ref = (
                        authority_ref
                        if expected_pointer_id == authority_id
                        else pointer_ref
                    )
                    source_snapshot = (
                        authority_snapshot
                        if source_ref is authority_ref
                        else source_ref.get(transaction=transaction_obj)
                    )
                elif authority_snapshot.exists:
                    source_snapshot = authority_snapshot
                else:
                    source_snapshot = pointer_ref.get(transaction=transaction_obj)
                validar(source_snapshot, authority_snapshot)
                transaction_obj.set(authority_ref, meta)
                transaction_obj.set(pointer_ref, pointer_meta)

            publish(transaction)
            return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("[SHARED-SYNC] Falha na troca transacional do ponteiro: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Nao foi possivel publicar o snapshot de forma atomica.",
            ) from exc

    # Implementacoes de teste/compatibilidade sem transacao ficam serializadas
    # no processo. O Firestore real sempre segue o caminho transacional acima.
    with _SHARED_SYNC_POINTER_CAS_FALLBACK_LOCK:
        authority_snapshot = authority_ref.get()
        if isinstance(expected_revision, dict):
            expected_pointer_id = str(
                expected_revision.get("pointer_id") or ""
            ).strip()
            source_snapshot = (
                authority_snapshot
                if expected_pointer_id == authority_id
                else pointer_ref.get()
            )
        elif authority_snapshot.exists:
            source_snapshot = authority_snapshot
        else:
            source_snapshot = pointer_ref.get()
        validar(source_snapshot, authority_snapshot)
        authority_ref.set(meta, merge=False)
        pointer_ref.set(pointer_meta, merge=False)

def _shared_sync_push_scope(
    client_id: str,
    scope: str,
    sessao: dict,
    machine_id: str = "",
    bundle_id: Optional[str] = None,
    extra_meta: Optional[dict] = None,
    user_only: bool = False,
    state_scope: Optional[str] = None,
    known_keys: Optional[set[str]] = None,
    allow_empty_delta: bool = False,
    sanitize_user_share_oauth: bool = False,
    skip_if_remote_hash_matches: bool = False,
    expected_snapshot_hash: str = "",
    key_context: Optional[dict] = None,
) -> dict:
    db = _shared_sync_firestore_required()
    bundle, manifest, warnings = _shared_sync_montar_pacote(
        client_id,
        scope,
        sessao.get("username"),
        machine_id,
        user_only=user_only,
        known_keys=known_keys,
        sanitize_user_share_oauth=sanitize_user_share_oauth,
    )
    stores_count = 0
    if scope == "lojas_integracoes":
        from backend.services.shared_sync_merge_integracoes import _shared_sync_lojas_config_from_bundle

        stores_count = len(_shared_sync_lojas_config_from_bundle(bundle))
    local_hash = str(manifest.get("snapshot_hash") or "")
    if expected_snapshot_hash and local_hash != str(expected_snapshot_hash).strip():
        raise HTTPException(
            status_code=409,
            detail="Os dados locais mudaram depois da previa; confira novamente.",
        )
    bundle_id = str(bundle_id or _shared_sync_doc_id(client_id, scope)).strip()
    encryption_secret: Optional[bytes] = None
    encryption_key_id = ""
    if isinstance(key_context, dict):
        encryption_secret, encryption_key_id = _shared_sync_keyring_key_for_push(
            key_context.get("sessao") if isinstance(key_context.get("sessao"), dict) else sessao,
            str(key_context.get("machine_id") or machine_id or ""),
        )
    if skip_if_remote_hash_matches:
        remote_meta = _shared_sync_remote_meta_by_id(bundle_id) or {}
        remote_hash = str(remote_meta.get("snapshot_hash") or "")
        remote_key_id = str(remote_meta.get("encryption_key_id") or "")
        same_key = remote_key_id == encryption_key_id if encryption_key_id else not remote_key_id
        authority_ready = (
            scope != "lojas_integracoes"
            or str(remote_meta.get("_guard_pointer_id") or "").strip()
            == _shared_sync_v2_authority_id(bundle_id)
        )
        if (
            remote_hash
            and local_hash
            and remote_hash == local_hash
            and same_key
            and authority_ready
        ):
            _shared_sync_state_update(
                client_id,
                sessao.get("username") or "",
                state_scope or scope,
                remote_meta,
                "push",
            )
            return {
                "scope": scope,
                "success": True,
                "direction": "push",
                "id": bundle_id,
                "skipped": True,
                "reason": "already_current",
                "file_count": manifest.get("file_count") or 0,
                "stores_count": stores_count,
                "item_count": manifest.get("item_count") or 0,
                "item_keys": manifest.get("item_keys") or [],
                "chunk_count": int(remote_meta.get("chunk_count") or 0),
                "bundle_bytes": int(remote_meta.get("bundle_bytes") or 0),
                "snapshot_hash": local_hash,
                "snapshot_id": str(
                    remote_meta.get("snapshot_id")
                    or remote_meta.get("id")
                    or ""
                ),
                "warnings": warnings,
                "updated_at": remote_meta.get("updated_at") or _shared_sync_now_iso(),
            }
    if known_keys is not None and not manifest.get("item_count") and not allow_empty_delta:
        return {
            "scope": scope,
            "success": True,
            "direction": "push",
            "id": bundle_id,
            "skipped": True,
            "reason": "already_shared",
            "file_count": 0,
            "item_count": 0,
            "item_keys": [],
            "chunk_count": 0,
            "bundle_bytes": 0,
            "snapshot_hash": manifest.get("snapshot_hash") or "",
            "warnings": warnings,
            "updated_at": _shared_sync_now_iso(),
        }
    expected_remote_revision: Any = None
    guard_pointer = scope == "lojas_integracoes" and known_keys is None
    if guard_pointer:
        base_snapshot_id = _shared_sync_state_snapshot_id(
            client_id,
            sessao.get("username") or "",
            state_scope or scope,
        )
        expected_remote_revision = _shared_sync_validar_push_lojas_integracoes(
            bundle_id,
            bundle,
            key_context=key_context,
            base_snapshot_id=base_snapshot_id,
            return_guard_revision=True,
            client_id=client_id,
        )
    encrypted_bundle = _shared_sync_encrypt_bundle(bundle_id, bundle, encryption_secret)
    bundle_sha256 = _shared_sync_bytes_sha256(encrypted_bundle)
    bundle_b64 = base64.b64encode(encrypted_bundle).decode("ascii")
    chunks = [bundle_b64[i:i + SHARED_SYNC_CHUNK_CHARS] for i in range(0, len(bundle_b64), SHARED_SYNC_CHUNK_CHARS)] or [""]
    snapshot_id = f"{bundle_id}__{uuid.uuid4().hex}"
    chunks_coll = db.collection(_firebase_shared_sync_chunks_collection_name())
    for idx, chunk in enumerate(chunks):
        chunks_coll.document(f"{snapshot_id}_{idx:05d}").set({
            "bundle_id": snapshot_id,
            "pointer_id": bundle_id,
            "client_id": str(client_id or "default").strip() or "default",
            "scope": scope,
            "index": idx,
            "data": chunk,
            "updated_at": manifest["created_at"],
        })
    # Fase 1: todos os chunks precisam ser legiveis e produzir o mesmo hash.
    validated_chunks = []
    for idx in range(len(chunks)):
        snap = chunks_coll.document(f"{snapshot_id}_{idx:05d}").get()
        if not snap.exists:
            _shared_sync_delete_chunks(db, snapshot_id)
            raise HTTPException(status_code=502, detail="Upload incompleto; o snapshot anterior foi preservado.")
        validated_chunks.append(str((snap.to_dict() or {}).get("data") or ""))
    try:
        validated_bundle = base64.b64decode("".join(validated_chunks).encode("ascii"))
    except Exception:
        _shared_sync_delete_chunks(db, snapshot_id)
        raise HTTPException(status_code=502, detail="Upload corrompido; o snapshot anterior foi preservado.")
    if _shared_sync_bytes_sha256(validated_bundle) != bundle_sha256:
        _shared_sync_delete_chunks(db, snapshot_id)
        raise HTTPException(status_code=502, detail="Hash do upload divergente; o snapshot anterior foi preservado.")
    meta = {
        "id": bundle_id,
        "pointer_id": bundle_id,
        "snapshot_id": snapshot_id,
        "schema": 2,
        "encrypted": True,
        "encryption": "fernet-pbkdf2-sha256",
        "encryption_key_id": encryption_key_id,
        "bundle_sha256": bundle_sha256,
        "client_id": str(client_id or "default").strip() or "default",
        "scope": scope,
        "updated_at": manifest["created_at"],
        "updated_ts": int(time.time()),
        "updated_by": sessao.get("username") or "",
        "machine_id": str(machine_id or "").strip(),
        "file_count": manifest.get("file_count") or 0,
        "stores_count": stores_count,
        "delta": bool(manifest.get("delta")),
        "item_count": manifest.get("item_count") or 0,
        "bundle_bytes": len(encrypted_bundle),
        "bundle_b64_chars": len(bundle_b64),
        "chunk_count": len(chunks),
        "snapshot_hash": manifest.get("snapshot_hash") or "",
        "files": (manifest.get("files") or [])[:250],
        "warnings": warnings,
    }
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    # Fase 2: publica o snapshot completo e so entao troca o ponteiro atual.
    snapshot_meta = dict(meta)
    snapshot_meta["id"] = snapshot_id
    # Um candidato ainda nao publicado nunca pode ser coletado como historico.
    snapshot_meta["status"] = "pending"
    db.collection(_firebase_shared_sync_collection_name()).document(snapshot_id).set(snapshot_meta, merge=False)
    try:
        if guard_pointer:
            _shared_sync_publicar_pointer_cas(
                db,
                bundle_id,
                meta,
                expected_remote_revision,
            )
        else:
            db.collection(_firebase_shared_sync_collection_name()).document(bundle_id).set(meta, merge=False)
    except HTTPException as exc:
        if exc.status_code == 409:
            # O CAS provou que nao houve commit; apenas neste caso e seguro
            # remover o snapshot candidato. Falhas 5xx sao ambiguas e ficam
            # para a coleta posterior, evitando apagar uma base ja publicada.
            _shared_sync_delete_chunks(db, snapshot_id)
            try:
                db.collection(_firebase_shared_sync_collection_name()).document(
                    snapshot_id
                ).delete()
            except Exception:
                pass
        raise
    db.collection(_firebase_shared_sync_collection_name()).document(
        snapshot_id
    ).set(
        {
            "status": "complete",
            "committed_at": _shared_sync_now_iso(),
        },
        merge=True,
    )
    # Chunks v1 usavam o proprio pointer_id. Depois do commit v2 eles precisam
    # desaparecer: um cliente v1 ignora schema/snapshot_id e poderia combinar
    # o ponteiro novo com esses chunks antigos, aplicando dados obsoletos de
    # forma destrutiva. Assim, clientes antigos falham fechado ate o upgrade.
    _shared_sync_delete_chunks(db, bundle_id)
    _shared_sync_cleanup_old_snapshots(db, bundle_id, snapshot_id)
    _shared_sync_state_update(client_id, sessao.get("username") or "", state_scope or scope, meta, "push")
    return {
        "scope": scope,
        "success": True,
        "direction": "push",
        "id": bundle_id,
        "file_count": meta["file_count"],
        "stores_count": meta["stores_count"],
        "item_count": meta["item_count"],
        "item_keys": list(manifest.get("item_keys") or []),
        "delta": meta["delta"],
        "chunk_count": meta["chunk_count"],
        "bundle_bytes": meta["bundle_bytes"],
        "snapshot_hash": meta["snapshot_hash"],
        "warnings": warnings,
        "updated_at": meta["updated_at"],
    }

def _shared_sync_remote_meta(client_id: str, scope: str) -> Optional[dict]:
    return _shared_sync_remote_meta_by_id(_shared_sync_doc_id(client_id, scope))

def _shared_sync_remote_meta_by_id(bundle_id: str) -> Optional[dict]:
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is None:
        return None
    try:
        coll = db.collection(_firebase_shared_sync_collection_name())
        logical_id = str(bundle_id or "").strip()
        authority_snap = coll.document(
            _shared_sync_v2_authority_id(logical_id)
        ).get()
        snap = (
            authority_snap
            if authority_snap.exists
            else coll.document(logical_id).get()
        )
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        data["id"] = data.get("id") or snap.id
        data["_guard_pointer_id"] = snap.id
        if int(data.get("schema") or 0) in {0, 1} and not bool(
            data.get("encrypted")
        ):
            # v1 usava o proprio documento mutavel como snapshot. Mesmo que um
            # campo snapshot_id apareca por migracao parcial, ele nao recebe
            # autoridade causal de um ID imutavel v2.
            data["snapshot_id"] = ""
        return data
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao ler metadados remotos: %s", exc)
        return None

def _shared_sync_obter_bundle_remoto(client_id: str, scope: str) -> tuple[bytes, dict]:
    return _shared_sync_obter_bundle_por_id(_shared_sync_doc_id(client_id, scope))


def _shared_sync_obter_bundle_remoto_para_guard(
    bundle_id: str,
    key_context: Optional[dict] = None,
) -> Optional[tuple[bytes, dict]]:
    """Distingue ausencia confirmada de indisponibilidade/corrupcao remota."""
    db = _shared_sync_firestore_required()
    try:
        coll = db.collection(_firebase_shared_sync_collection_name())
        logical_id = str(bundle_id or "").strip()
        authority_snap = coll.document(
            _shared_sync_v2_authority_id(logical_id)
        ).get()
        snap = (
            authority_snap
            if authority_snap.exists
            else coll.document(logical_id).get()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Nao foi possivel confirmar o snapshot remoto atual.",
        ) from exc
    if not snap.exists:
        return None
    meta = snap.to_dict()
    if not isinstance(meta, dict) or not meta:
        raise HTTPException(
            status_code=502,
            detail="Metadados do snapshot remoto estao invalidos.",
        )
    meta = dict(meta)
    meta["id"] = meta.get("id") or snap.id
    meta["_guard_pointer_id"] = snap.id
    schema = int(meta.get("schema") or 0)
    if schema in {0, 1} and not bool(meta.get("encrypted")):
        meta["snapshot_id"] = ""
        chunk_count = int(meta.get("chunk_count") or 0)
        if chunk_count <= 0:
            raise HTTPException(status_code=502, detail="Snapshot legado remoto sem chunks.")
        max_bundle_bytes = _shared_sync_max_bundle_bytes()
        max_b64_chars = ((max_bundle_bytes + 2) // 3) * 4
        expected_size = int(meta.get("bundle_bytes") or 0)
        expected_b64_chars = int(meta.get("bundle_b64_chars") or 0)
        max_chunk_count = max(
            4,
            ((max_b64_chars + max(1, SHARED_SYNC_CHUNK_CHARS) - 1)
             // max(1, SHARED_SYNC_CHUNK_CHARS)) * 4,
        )
        if expected_size > max_bundle_bytes:
            raise HTTPException(
                status_code=413,
                detail="Snapshot legado remoto maior que o limite permitido.",
            )
        if expected_b64_chars > max_b64_chars or chunk_count > max_chunk_count:
            raise HTTPException(
                status_code=413,
                detail="Snapshot legado remoto excede os limites de transferencia.",
            )
        chunks = []
        total_b64_chars = 0
        coll = db.collection(_firebase_shared_sync_chunks_collection_name())
        legacy_id = str(meta.get("id") or bundle_id)
        for idx in range(chunk_count):
            chunk = coll.document(f"{legacy_id}_{idx:05d}").get()
            if not chunk.exists:
                raise HTTPException(
                    status_code=502,
                    detail=f"Snapshot legado remoto incompleto: parte {idx + 1}/{chunk_count}.",
                )
            chunk_text = str((chunk.to_dict() or {}).get("data") or "")
            total_b64_chars += len(chunk_text)
            if total_b64_chars > max_b64_chars:
                raise HTTPException(
                    status_code=413,
                    detail="Snapshot legado remoto maior que o limite permitido.",
                )
            chunks.append(chunk_text)
        try:
            bundle = base64.b64decode(
                "".join(chunks).encode("ascii"),
                validate=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Snapshot legado remoto corrompido.",
            ) from exc
        if expected_size and len(bundle) != expected_size:
            raise HTTPException(
                status_code=502,
                detail="Tamanho do snapshot legado remoto divergente.",
            )
        if len(bundle) > max_bundle_bytes:
            raise HTTPException(
                status_code=413,
                detail="Snapshot legado remoto maior que o limite permitido.",
            )
        from backend.services.shared_sync_apply_scope import (
            _shared_sync_read_validated_bundle,
        )

        manifest, _ = _shared_sync_read_validated_bundle(
            bundle,
            str(meta.get("scope") or "lojas_integracoes"),
        )
        expected_snapshot_hash = str(meta.get("snapshot_hash") or "").strip().lower()
        manifest_snapshot_hash = str(manifest.get("snapshot_hash") or "").strip().lower()
        manifest_files = manifest.get("files")
        if not isinstance(manifest_files, list):
            raise HTTPException(
                status_code=502,
                detail="Lista de arquivos invalida no snapshot legado remoto.",
            )
        from backend.services.shared_sync_bundle import _shared_sync_snapshot_hash

        calculated_snapshot_hash = _shared_sync_snapshot_hash(manifest_files)
        if (
            not re.fullmatch(r"[a-f0-9]{64}", expected_snapshot_hash)
            or not re.fullmatch(r"[a-f0-9]{64}", manifest_snapshot_hash)
            or calculated_snapshot_hash != manifest_snapshot_hash
            or expected_snapshot_hash != manifest_snapshot_hash
        ):
            raise HTTPException(
                status_code=502,
                detail="Hash do snapshot legado remoto divergente.",
            )
        meta["legacy_plaintext"] = True
        return bundle, meta
    return _shared_sync_obter_bundle_por_id(
        bundle_id,
        meta=meta,
        key_context=key_context,
    )

def _shared_sync_obter_bundle_por_id(
    bundle_id: str,
    meta: Optional[dict] = None,
    key_context: Optional[dict] = None,
) -> tuple[bytes, dict]:
    db = _shared_sync_firestore_required()
    meta = meta if isinstance(meta, dict) and meta else _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    if int(meta.get("schema") or 0) != 2 or not bool(meta.get("encrypted")):
        raise HTTPException(status_code=409, detail="Pacote antigo nao pode ser importado; publique uma previa v2.")
    pointer_id = str(meta.get("pointer_id") or bundle_id)
    snapshot_id = str(meta.get("snapshot_id") or meta.get("id") or bundle_id)
    chunk_count = int(meta.get("chunk_count") or 0)
    if chunk_count <= 0:
        raise HTTPException(status_code=404, detail="Backup remoto sem chunks.")
    chunks = []
    coll = db.collection(_firebase_shared_sync_chunks_collection_name())
    for idx in range(chunk_count):
        snap = coll.document(f"{snapshot_id}_{idx:05d}").get()
        if not snap.exists:
            raise HTTPException(status_code=502, detail=f"Backup remoto incompleto: parte {idx + 1}/{chunk_count}.")
        data = snap.to_dict() or {}
        chunks.append(str(data.get("data") or ""))
    try:
        encrypted_bundle = base64.b64decode("".join(chunks).encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Backup remoto corrompido: {exc}")
    expected_hash = str(meta.get("bundle_sha256") or "")
    if not expected_hash or _shared_sync_bytes_sha256(encrypted_bundle) != expected_hash:
        raise HTTPException(status_code=502, detail="Hash do pacote remoto invalido.")
    encryption_secret: Optional[bytes] = None
    encryption_key_id_raw = str(meta.get("encryption_key_id") or "").strip().lower()
    encryption_key_id = (
        _shared_sync_keyring_normalize_key_id(encryption_key_id_raw)
        if encryption_key_id_raw else ""
    )
    if encryption_key_id:
        if not isinstance(key_context, dict):
            raise HTTPException(status_code=409, detail="Contexto autenticado ausente para abrir este snapshot.")
        sessao = key_context.get("sessao") if isinstance(key_context.get("sessao"), dict) else {}
        encryption_secret = _shared_sync_keyring_key_for_pull(
            sessao,
            str(key_context.get("machine_id") or ""),
            encryption_key_id,
        )
    return _shared_sync_decrypt_bundle(pointer_id, encrypted_bundle, encryption_secret), meta

def _shared_sync_manifest_from_bundle(bundle: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            raw = zf.read("manifest.json")
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao ler manifest do pacote: %s", exc)
        return {}

configure_shared_sync_remote_runtime()

__all__ = [
    "configure_shared_sync_remote_runtime",
    "_shared_sync_firestore_required",
    "_shared_sync_encrypt_bundle",
    "_shared_sync_decrypt_bundle",
    "_shared_sync_delete_chunks",
    "_shared_sync_publicar_pointer_cas",
    "_shared_sync_push_scope",
    "_shared_sync_remote_meta",
    "_shared_sync_remote_meta_by_id",
    "_shared_sync_obter_bundle_remoto",
    "_shared_sync_obter_bundle_remoto_para_guard",
    "_shared_sync_obter_bundle_por_id",
    "_shared_sync_manifest_from_bundle",
]
