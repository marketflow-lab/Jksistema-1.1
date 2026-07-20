"""Per-user Shared Sync key provisioning for trusted machines.

The data-encryption key never leaves a machine in plaintext.  Each installation
keeps an X25519 private key in Windows Credential Manager and publishes only its
public key.  A sending machine wraps the per-user data key independently for
every registered machine using X25519 + HKDF + AES-GCM.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException

try:
    from google.cloud.firestore_v1 import ArrayUnion as _FirestoreArrayUnion
except Exception:  # Firebase is optional in local-only installations.
    _FirestoreArrayUnion = None

from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.admin_usuarios_firebase import (
    _firebase_collection,
    _firebase_db,
    _firebase_deve_usar,
    _firebase_doc_id,
)
from backend.services.admin_usuarios_presence_core import (
    _authenticated_session_machine_id,
)
from backend.services.secure_credentials import read_scoped_secret, write_scoped_secret
from backend.services.shared_sync_common import *
from backend.services.shared_sync_config import _firebase_shared_sync_keyrings_collection_name
from backend.services.shared_sync_context import configure_shared_sync_context


_SHARED_SYNC_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def configure_shared_sync_keyring_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_keyring_hash(*parts: str, length: int = 32) -> str:
    raw = "|".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _shared_sync_keyring_normalize_key_id(value: str, *, allow_empty: bool = False) -> str:
    key_id = str(value or "").strip().lower()
    if not key_id and allow_empty:
        return ""
    if not _SHARED_SYNC_KEY_ID_RE.fullmatch(key_id):
        raise HTTPException(status_code=409, detail="Identificador da chave de sincronizacao invalido.")
    return key_id


def _shared_sync_keyring_identity(sessao: dict, machine_id: str) -> dict:
    client_id = _shared_sync_normalizar_client_id((sessao or {}).get("client_id"))
    username = _shared_sync_normalizar_username((sessao or {}).get("username"))
    machine = str(machine_id or "").strip()
    if not client_id or not username:
        raise HTTPException(status_code=401, detail="Sessao invalida para provisionar a criptografia da sincronizacao.")
    if not machine:
        raise HTTPException(status_code=400, detail="Identificacao desta maquina ausente para a sincronizacao.")
    keyring_id = _shared_sync_safe_doc_id("shared-sync-keyring", client_id, username)
    member_id = _shared_sync_safe_doc_id("shared-sync-keyring-member", client_id, username, machine)
    return {
        "client_id": client_id,
        "username": username,
        "machine_id": machine,
        "keyring_id": keyring_id,
        "member_id": member_id,
        "owner_client_hash": _shared_sync_keyring_hash("client", client_id, length=16),
        "owner_user_hash": _shared_sync_keyring_hash("user", client_id, username, length=16),
    }


def _shared_sync_keyring_private_target(identity: dict) -> str:
    return "/".join([
        "shared-sync", "x25519-private", identity["keyring_id"], identity["member_id"],
    ])


def _shared_sync_keyring_data_target(identity: dict, key_id: str) -> str:
    return "/".join([
        "shared-sync", "data-key", identity["keyring_id"], str(key_id or "").strip().lower(),
    ])


def _shared_sync_keyring_approved_machine_ids(sessao: dict) -> set[str]:
    username = _shared_sync_normalizar_username((sessao or {}).get("username"))
    client_id = _shared_sync_normalizar_client_id((sessao or {}).get("client_id"))
    try:
        collection = _firebase_collection()
        snap = collection.document(_firebase_doc_id(username)).get() if collection is not None else None
        remote = snap.to_dict() or {} if snap is not None and snap.exists else {}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Nao foi possivel validar as maquinas autorizadas no Firebase.") from exc
    if not remote or _shared_sync_normalizar_client_id(remote.get("client_id")) != client_id:
        raise HTTPException(status_code=403, detail="Cadastro remoto deste usuario nao autoriza a sincronizacao.")
    machines = remote.get("machine_ids") if isinstance(remote.get("machine_ids"), list) else []
    first = str(remote.get("machine_id") or "").strip()
    return {
        str(machine or "").strip()
        for machine in [*machines, first]
        if str(machine or "").strip()
    }


def _shared_sync_keyring_persist_unrestricted_machine(sessao: dict, machine_id: str) -> None:
    """Persist the authenticated admin/full machine before it can join a keyring."""
    permissions = (sessao or {}).get("permissions") if isinstance((sessao or {}).get("permissions"), dict) else {}
    username = _shared_sync_normalizar_username((sessao or {}).get("username"))
    unrestricted = bool(permissions.get("full") is True or username in {"admin", "administrador"})
    if not unrestricted:
        return
    client_id = _shared_sync_normalizar_client_id((sessao or {}).get("client_id"))
    machine = _authenticated_session_machine_id(sessao, machine_id)
    collection = _firebase_collection()
    ref = collection.document(_firebase_doc_id(username)) if collection is not None else None
    snap = ref.get() if ref is not None else None
    remote = snap.to_dict() or {} if snap is not None and snap.exists else {}
    if not remote or _shared_sync_normalizar_client_id(remote.get("client_id")) != client_id:
        raise HTTPException(status_code=403, detail="Cadastro remoto deste usuario nao autoriza esta maquina.")
    remote_machine_ids = remote.get("machine_ids") if isinstance(remote.get("machine_ids"), list) else []
    approved = {
        str(value or "").strip()
        for value in [*remote_machine_ids, remote.get("machine_id")]
        if str(value or "").strip()
    }
    if machine in approved:
        return
    if _FirestoreArrayUnion is None:
        raise HTTPException(status_code=503, detail="Firebase indisponivel para autorizar esta maquina.")
    ref.update({
        "machine_ids": _FirestoreArrayUnion([machine]),
        "updated_at": _shared_sync_now_iso(),
    })


def _shared_sync_keyring_authorized_machine_ids(sessao: dict, current_machine_id: str = "") -> set[str]:
    approved = _shared_sync_keyring_approved_machine_ids(sessao)
    return approved


def _shared_sync_keyring_authorized_member_ids(sessao: dict, current_machine_id: str = "") -> set[str]:
    client_id = _shared_sync_normalizar_client_id((sessao or {}).get("client_id"))
    username = _shared_sync_normalizar_username((sessao or {}).get("username"))
    return {
        _shared_sync_safe_doc_id("shared-sync-keyring-member", client_id, username, machine_id)
        for machine_id in _shared_sync_keyring_authorized_machine_ids(sessao, current_machine_id)
    }


def _shared_sync_keyring_read_secret(target: str) -> str:
    return str(read_scoped_secret(target) or "").strip()


def _shared_sync_keyring_write_secret(target: str, value: str) -> None:
    try:
        saved = bool(write_scoped_secret(target, value))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Cofre seguro local indisponivel para a sincronizacao.") from exc
    if not saved:
        raise HTTPException(status_code=503, detail="Cofre seguro local indisponivel para a sincronizacao.")


def _shared_sync_keyring_decode(value: str, *, expected_bytes: int, label: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(str(value or "").encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{label} local da sincronizacao esta corrompida.") from exc
    if len(decoded) != expected_bytes:
        raise HTTPException(status_code=409, detail=f"{label} local da sincronizacao esta corrompida.")
    return decoded


def _shared_sync_keyring_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _shared_sync_keyring_private_key(identity: dict) -> X25519PrivateKey:
    target = _shared_sync_keyring_private_target(identity)
    stored = _shared_sync_keyring_read_secret(target)
    if stored:
        raw = _shared_sync_keyring_decode(stored, expected_bytes=32, label="Chave privada")
        try:
            return X25519PrivateKey.from_private_bytes(raw)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Chave privada local da sincronizacao esta corrompida.") from exc
    private_key = X25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _shared_sync_keyring_write_secret(target, _shared_sync_keyring_encode(raw))
    return private_key


def _shared_sync_keyring_public_b64(private_key: X25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _shared_sync_keyring_encode(raw)


def _shared_sync_keyring_collection(db):
    return db.collection(_firebase_shared_sync_keyrings_collection_name())


def _shared_sync_keyring_firestore_required():
    db = _firebase_db() if _firebase_deve_usar() else None
    if db is None:
        raise HTTPException(status_code=503, detail="Firebase indisponivel para provisionar a criptografia da sincronizacao.")
    return db


def _shared_sync_keyring_validate_root(identity: dict, root: dict) -> dict:
    if not root:
        return {}
    valid = (
        root.get("id") == identity["keyring_id"]
        and root.get("kind") == "keyring"
        and int(root.get("schema") or 0) == 1
        and root.get("owner_client_hash") == identity["owner_client_hash"]
        and root.get("owner_user_hash") == identity["owner_user_hash"]
    )
    if not valid:
        raise HTTPException(status_code=409, detail="Registro remoto da chave nao pertence a este usuario.")
    _shared_sync_keyring_normalize_key_id(root.get("active_key_id"), allow_empty=True)
    return root


def _shared_sync_keyring_validate_member(identity: dict, member: dict, expected_member_id: str) -> dict:
    valid = (
        member.get("id") == expected_member_id
        and member.get("kind") == "member"
        and int(member.get("schema") or 0) == 1
        and member.get("keyring_id") == identity["keyring_id"]
        and member.get("owner_client_hash") == identity["owner_client_hash"]
        and member.get("owner_user_hash") == identity["owner_user_hash"]
        and bool(member.get("public_key"))
    )
    if not valid:
        raise HTTPException(status_code=409, detail="Registro remoto de maquina invalido para este usuario.")
    return member


def _shared_sync_keyring_register(sessao: dict, machine_id: str) -> tuple[dict, X25519PrivateKey, dict, object]:
    identity = _shared_sync_keyring_identity(sessao, machine_id)
    _authenticated_session_machine_id(sessao, identity["machine_id"])
    _shared_sync_keyring_persist_unrestricted_machine(sessao, identity["machine_id"])
    authorized = _shared_sync_keyring_authorized_machine_ids(sessao, identity["machine_id"])
    if identity["machine_id"] not in authorized:
        raise HTTPException(status_code=403, detail="Esta maquina nao esta autorizada no cadastro deste usuario.")
    private_key = _shared_sync_keyring_private_key(identity)
    public_key = _shared_sync_keyring_public_b64(private_key)
    db = _shared_sync_keyring_firestore_required()
    collection = _shared_sync_keyring_collection(db)
    root_ref = collection.document(identity["keyring_id"])
    root_snap = root_ref.get()
    root = root_snap.to_dict() or {} if root_snap.exists else {}
    _shared_sync_keyring_validate_root(identity, root)
    member_ref = collection.document(identity["member_id"])
    previous_snap = member_ref.get()
    previous = previous_snap.to_dict() or {} if previous_snap.exists else {}
    if previous:
        _shared_sync_keyring_validate_member(identity, previous, identity["member_id"])
    member = {
        "id": identity["member_id"],
        "kind": "member",
        "schema": 1,
        "keyring_id": identity["keyring_id"],
        "owner_client_hash": identity["owner_client_hash"],
        "owner_user_hash": identity["owner_user_hash"],
        "public_key": public_key,
        "registered_at": str(previous.get("registered_at") or _shared_sync_now_iso()),
        "last_seen_at": _shared_sync_now_iso(),
    }
    member_ref.set(member, merge=True)
    return identity, private_key, root, db


def _shared_sync_keyring_members(db, identity: dict, sessao: dict) -> list[dict]:
    members = []
    collection = _shared_sync_keyring_collection(db)
    allowed_member_ids = _shared_sync_keyring_authorized_member_ids(sessao, identity["machine_id"])
    for member_id in sorted(allowed_member_ids):
        snap = collection.document(member_id).get()
        if not snap.exists:
            continue
        item = snap.to_dict() or {}
        if item.get("id") and item.get("id") != snap.id:
            continue
        item["id"] = snap.id
        if item.get("kind") != "member":
            continue
        members.append(_shared_sync_keyring_validate_member(identity, item, item["id"]))
    members.sort(key=lambda item: str(item.get("id") or ""))
    return members


def _shared_sync_keyring_envelope_id(identity: dict, member_id: str, key_id: str) -> str:
    return _shared_sync_safe_doc_id(
        "shared-sync-keyring-envelope", identity["keyring_id"], member_id, key_id,
    )


def _shared_sync_keyring_envelope_record(identity: dict, member_id: str, key_id: str, envelope: dict) -> dict:
    return {
        "id": _shared_sync_keyring_envelope_id(identity, member_id, key_id),
        "kind": "envelope",
        "schema": 1,
        "keyring_id": identity["keyring_id"],
        "member_id": member_id,
        "owner_client_hash": identity["owner_client_hash"],
        "owner_user_hash": identity["owner_user_hash"],
        "encryption_key_id": key_id,
        "envelope": envelope,
        "updated_at": _shared_sync_now_iso(),
    }


def _shared_sync_keyring_read_envelope(db, identity: dict, member_id: str, key_id: str) -> dict:
    key_id = _shared_sync_keyring_normalize_key_id(key_id)
    envelope_id = _shared_sync_keyring_envelope_id(identity, member_id, key_id)
    snap = _shared_sync_keyring_collection(db).document(envelope_id).get()
    record = snap.to_dict() or {} if snap.exists else {}
    if not record:
        return {}
    valid = (
        record.get("id") == envelope_id
        and record.get("kind") == "envelope"
        and int(record.get("schema") or 0) == 1
        and record.get("keyring_id") == identity["keyring_id"]
        and record.get("member_id") == member_id
        and record.get("owner_client_hash") == identity["owner_client_hash"]
        and record.get("owner_user_hash") == identity["owner_user_hash"]
        and record.get("encryption_key_id") == key_id
        and isinstance(record.get("envelope"), dict)
    )
    if not valid:
        raise HTTPException(status_code=409, detail="Envelope remoto nao pertence a este usuario ou maquina.")
    return record


def _shared_sync_keyring_aad(identity: dict, member_id: str, key_id: str) -> bytes:
    return "|".join([
        "jk-shared-sync-key-envelope-v1",
        identity["keyring_id"],
        identity["owner_client_hash"],
        identity["owner_user_hash"],
        str(member_id or ""),
        str(key_id or "").strip().lower(),
    ]).encode("ascii")


def _shared_sync_keyring_wrap_key(identity: dict, member: dict, key_id: str, data_key: bytes) -> dict:
    try:
        recipient_raw = _shared_sync_keyring_decode(
            str(member.get("public_key") or ""), expected_bytes=32, label="Chave publica remota",
        )
        recipient = X25519PublicKey.from_public_bytes(recipient_raw)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Chave publica remota da sincronizacao esta corrompida.") from exc
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(recipient)
    member_id = str(member.get("id") or "")
    salt = hashlib.sha256(("jk-shared-sync-wrap-v1|" + identity["keyring_id"] + "|" + member_id).encode("ascii")).digest()
    wrap_key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        info=("jk-shared-sync-wrap-v1|" + str(key_id)).encode("ascii"),
    ).derive(shared)
    nonce = os.urandom(12)
    ciphertext = AESGCM(wrap_key).encrypt(nonce, data_key, _shared_sync_keyring_aad(identity, member_id, key_id))
    ephemeral_public = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "schema": 1,
        "algorithm": "x25519-hkdf-sha256-aesgcm",
        "key_id": key_id,
        "ephemeral_public_key": _shared_sync_keyring_encode(ephemeral_public),
        "nonce": _shared_sync_keyring_encode(nonce),
        "ciphertext": _shared_sync_keyring_encode(ciphertext),
        "wrapped_at": _shared_sync_now_iso(),
    }


def _shared_sync_keyring_unwrap_key(identity: dict, private_key: X25519PrivateKey, envelope_record: dict, key_id: str) -> bytes:
    envelope = envelope_record.get("envelope") if isinstance(envelope_record.get("envelope"), dict) else {}
    if str(envelope.get("key_id") or "") != str(key_id or ""):
        raise HTTPException(
            status_code=409,
            detail="Esta maquina ainda nao recebeu a chave. Na maquina de origem, clique em Enviar agora novamente.",
        )
    ephemeral_raw = _shared_sync_keyring_decode(
        str(envelope.get("ephemeral_public_key") or ""), expected_bytes=32, label="Envelope",
    )
    nonce = _shared_sync_keyring_decode(str(envelope.get("nonce") or ""), expected_bytes=12, label="Envelope")
    try:
        ciphertext = base64.urlsafe_b64decode(str(envelope.get("ciphertext") or "").encode("ascii"))
        ephemeral_public = X25519PublicKey.from_public_bytes(ephemeral_raw)
        shared = private_key.exchange(ephemeral_public)
        member_id = identity["member_id"]
        salt = hashlib.sha256(("jk-shared-sync-wrap-v1|" + identity["keyring_id"] + "|" + member_id).encode("ascii")).digest()
        wrap_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=salt,
            info=("jk-shared-sync-wrap-v1|" + str(key_id)).encode("ascii"),
        ).derive(shared)
        data_key = AESGCM(wrap_key).decrypt(
            nonce, ciphertext, _shared_sync_keyring_aad(identity, member_id, key_id),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Envelope da chave incompativel com esta maquina.") from exc
    if len(data_key) != 32 or _shared_sync_keyring_key_id(data_key) != str(key_id or ""):
        raise HTTPException(status_code=409, detail="Chave recebida nao corresponde ao snapshot remoto.")
    return data_key


def _shared_sync_keyring_key_id(data_key: bytes) -> str:
    return hashlib.sha256(data_key).hexdigest()[:16]


def _shared_sync_keyring_local_data_key(identity: dict, key_id: str) -> bytes:
    key_id = _shared_sync_keyring_normalize_key_id(key_id)
    stored = _shared_sync_keyring_read_secret(_shared_sync_keyring_data_target(identity, key_id))
    if not stored:
        return b""
    data_key = _shared_sync_keyring_decode(stored, expected_bytes=32, label="Chave de dados")
    if _shared_sync_keyring_key_id(data_key) != str(key_id or ""):
        raise HTTPException(status_code=409, detail="Chave de dados local nao corresponde ao identificador remoto.")
    return data_key


def _shared_sync_keyring_store_data_key(identity: dict, key_id: str, data_key: bytes) -> None:
    key_id = _shared_sync_keyring_normalize_key_id(key_id)
    if len(data_key) != 32 or _shared_sync_keyring_key_id(data_key) != str(key_id or ""):
        raise HTTPException(status_code=409, detail="Chave de dados invalida para a sincronizacao.")
    _shared_sync_keyring_write_secret(
        _shared_sync_keyring_data_target(identity, key_id), _shared_sync_keyring_encode(data_key),
    )


def _shared_sync_keyring_current_member(db, identity: dict) -> dict:
    snap = _shared_sync_keyring_collection(db).document(identity["member_id"]).get()
    member = (snap.to_dict() or {}) if snap.exists else {}
    return _shared_sync_keyring_validate_member(identity, member, identity["member_id"]) if member else {}


def _shared_sync_keyring_create_root(db, identity: dict, key_id: str) -> tuple[dict, bool]:
    key_id = _shared_sync_keyring_normalize_key_id(key_id)
    root = {
        "id": identity["keyring_id"],
        "kind": "keyring",
        "schema": 1,
        "owner_client_hash": identity["owner_client_hash"],
        "owner_user_hash": identity["owner_user_hash"],
        "active_key_id": key_id,
        "created_at": _shared_sync_now_iso(),
        "updated_at": _shared_sync_now_iso(),
    }
    ref = _shared_sync_keyring_collection(db).document(identity["keyring_id"])
    try:
        ref.create(root)
        return root, True
    except Exception as exc:
        snap = ref.get()
        existing = snap.to_dict() or {} if snap.exists else {}
        if not existing:
            raise HTTPException(status_code=503, detail="Nao foi possivel eleger a chave da sincronizacao.") from exc
        return _shared_sync_keyring_validate_root(identity, existing), False


def _shared_sync_keyring_key_for_push(sessao: dict, machine_id: str) -> tuple[bytes, str]:
    identity, private_key, root, db = _shared_sync_keyring_register(sessao, machine_id)
    key_id = _shared_sync_keyring_normalize_key_id(root.get("active_key_id"), allow_empty=True)
    if key_id:
        data_key = _shared_sync_keyring_local_data_key(identity, key_id)
        if not data_key:
            envelope_record = _shared_sync_keyring_read_envelope(
                db, identity, identity["member_id"], key_id,
            )
            data_key = _shared_sync_keyring_unwrap_key(identity, private_key, envelope_record, key_id)
            _shared_sync_keyring_store_data_key(identity, key_id, data_key)
    else:
        candidate = os.urandom(32)
        candidate_key_id = _shared_sync_keyring_key_id(candidate)
        elected_root, won = _shared_sync_keyring_create_root(db, identity, candidate_key_id)
        key_id = _shared_sync_keyring_normalize_key_id(elected_root.get("active_key_id"))
        if won:
            data_key = candidate
            _shared_sync_keyring_store_data_key(identity, key_id, data_key)
        else:
            data_key = _shared_sync_keyring_local_data_key(identity, key_id)
            if not data_key:
                envelope_record = _shared_sync_keyring_read_envelope(
                    db, identity, identity["member_id"], key_id,
                )
                if not envelope_record:
                    raise HTTPException(
                        status_code=409,
                        detail="Outra maquina criou a chave agora. Aguarde o envio dela e tente novamente.",
                    )
                data_key = _shared_sync_keyring_unwrap_key(identity, private_key, envelope_record, key_id)
                _shared_sync_keyring_store_data_key(identity, key_id, data_key)

    members = _shared_sync_keyring_members(db, identity, sessao)
    if not members:
        raise HTTPException(status_code=503, detail="Nenhuma maquina registrada para receber a chave da sincronizacao.")
    collection = _shared_sync_keyring_collection(db)
    for member in members:
        envelope = _shared_sync_keyring_wrap_key(identity, member, key_id, data_key)
        record = _shared_sync_keyring_envelope_record(
            identity, str(member.get("id") or ""), key_id, envelope,
        )
        collection.document(record["id"]).set(record, merge=False)
    return data_key, key_id


def _shared_sync_keyring_key_for_pull(sessao: dict, machine_id: str, key_id: str) -> bytes:
    key_id = _shared_sync_keyring_normalize_key_id(key_id)
    identity, private_key, _root, db = _shared_sync_keyring_register(sessao, machine_id)
    data_key = _shared_sync_keyring_local_data_key(identity, key_id)
    if data_key:
        return data_key
    envelope_record = _shared_sync_keyring_read_envelope(
        db, identity, identity["member_id"], key_id,
    )
    data_key = _shared_sync_keyring_unwrap_key(identity, private_key, envelope_record, key_id)
    _shared_sync_keyring_store_data_key(identity, key_id, data_key)
    return data_key


def _shared_sync_keyring_status(sessao: dict, machine_id: str) -> dict:
    if not str(machine_id or "").strip():
        return {"ready": False, "registered": False, "key_id": "", "needs_send": True, "reason": "machine_id_missing"}
    try:
        identity, private_key, root, db = _shared_sync_keyring_register(sessao, machine_id)
        key_id = _shared_sync_keyring_normalize_key_id(root.get("active_key_id"), allow_empty=True)
        if not key_id:
            return {"ready": False, "registered": True, "key_id": "", "needs_send": True, "reason": "not_created"}
        data_key = _shared_sync_keyring_local_data_key(identity, key_id)
        if not data_key:
            envelope_record = _shared_sync_keyring_read_envelope(
                db, identity, identity["member_id"], key_id,
            )
            try:
                data_key = _shared_sync_keyring_unwrap_key(identity, private_key, envelope_record, key_id)
                _shared_sync_keyring_store_data_key(identity, key_id, data_key)
            except HTTPException:
                reason = "envelope_invalid" if envelope_record else "envelope_missing"
                return {"ready": False, "registered": True, "key_id": key_id, "needs_send": True, "reason": reason}
        return {"ready": True, "registered": True, "key_id": key_id, "needs_send": False, "reason": ""}
    except HTTPException as exc:
        return {
            "ready": False,
            "registered": False,
            "key_id": "",
            "needs_send": True,
            "reason": "secure_store_unavailable" if int(exc.status_code or 500) == 503 else "registration_failed",
        }


configure_shared_sync_keyring_runtime()


__all__ = [
    "configure_shared_sync_keyring_runtime",
    "_shared_sync_keyring_key_for_push",
    "_shared_sync_keyring_key_for_pull",
    "_shared_sync_keyring_status",
    "_shared_sync_keyring_normalize_key_id",
]
