"""Authenticated encrypted backups for Context Hub human curation only."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


MAGIC = b"JKCHB2\x00"
KDF_ITERATIONS = 600_000
MAX_FILES = 2_000
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 50_000_000
_BACKUP_ID_RE = re.compile(r"^[a-f0-9]{32}$")


class BackupError(RuntimeError):
    pass


class BackupValidationError(BackupError):
    pass


class BackupCryptoError(BackupValidationError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _safe_passphrase(passphrase: object) -> bytes:
    value = str(passphrase or "")
    if not 12 <= len(value) <= 1024:
        raise BackupValidationError("A senha do backup deve ter entre 12 e 1024 caracteres.")
    return value.encode("utf-8")


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    ).derive(passphrase)


def _safe_relative(raw: object) -> Path:
    value = str(raw or "").replace("\\", "/")
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.lower() != ".md"
        or any(part in {"", "."} for part in relative.parts)
    ):
        raise BackupValidationError("Backup contem caminho invalido.")
    return relative


def _is_link(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(is_junction and is_junction(path))


def _assert_safe_directory(path: Path, *, create: bool, label: str) -> None:
    absolute = Path(os.path.abspath(path))
    for component in reversed((absolute, *absolute.parents)):
        if component.exists() and _is_link(component):
            raise BackupValidationError(f"{label} nao pode usar links simbolicos ou junctions.")
    if not absolute.exists():
        if not create:
            raise BackupValidationError(f"{label} indisponivel.")
        absolute.mkdir(parents=True, exist_ok=False)
    if _is_link(absolute) or not absolute.is_dir():
        raise BackupValidationError(f"{label} deve ser um diretorio regular.")


def _collect(curated_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    total = 0
    _assert_safe_directory(curated_dir, create=False, label="A raiz de curadoria")
    for candidate in sorted(curated_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        relative = _safe_relative(candidate.relative_to(curated_dir).as_posix())
        cursor = curated_dir
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists() and _is_link(cursor):
                raise BackupValidationError("Backup nao aceita links simbolicos ou junctions.")
        data = candidate.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise BackupValidationError("Uma nota excede o limite do backup.")
        total += len(data)
        if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
            raise BackupValidationError("O conjunto de curadoria excede o limite do backup.")
        files.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
    return files


def create_encrypted_backup(
    curated_dir: Path,
    backups_dir: Path,
    *,
    client_id: str,
    passphrase: object,
) -> dict[str, Any]:
    secret = _safe_passphrase(passphrase)
    files = _collect(curated_dir)
    _assert_safe_directory(backups_dir, create=True, label="O diretorio de backups")
    backup_id = uuid.uuid4().hex
    created_at = _utc_now()
    archive = {
        "schema_version": 2,
        "backup_id": backup_id,
        "client_id": client_id,
        "created_at": created_at,
        "scope": "80_Curadoria",
        "files": files,
    }
    plaintext = zlib.compress(
        json.dumps(archive, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        level=9,
    )
    salt = os.urandom(16)
    nonce = os.urandom(12)
    aad = MAGIC + client_id.encode("utf-8")
    ciphertext = AESGCM(_derive_key(secret, salt)).encrypt(nonce, plaintext, aad)
    envelope = {
        "schema_version": 2,
        "backup_id": backup_id,
        "client_id": client_id,
        "created_at": created_at,
        "scope": "80_Curadoria",
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": KDF_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    target = backups_dir / f"{backup_id}.jkhub"
    temporary = backups_dir / f".{backup_id}.{uuid.uuid4().hex}.tmp"
    temporary.write_bytes(MAGIC + json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    os.replace(temporary, target)
    return {
        "backup_id": backup_id,
        "created_at": created_at,
        "scope": "80_Curadoria",
        "file_count": len(files),
        "encrypted": True,
    }


def _read_envelope(path: Path, client_id: str) -> Mapping[str, Any]:
    if not path.is_file() or _is_link(path) or path.stat().st_size > 100_000_000:
        raise BackupValidationError("Backup indisponivel ou invalido.")
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise BackupValidationError("Formato de backup invalido.")
    try:
        envelope = json.loads(data[len(MAGIC) :].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BackupValidationError("Formato de backup invalido.") from error
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema_version") != 2
        or envelope.get("client_id") != client_id
        or envelope.get("scope") != "80_Curadoria"
        or envelope.get("iterations") != KDF_ITERATIONS
    ):
        raise BackupValidationError("Contrato do backup invalido para este cliente.")
    return envelope


def list_backups(backups_dir: Path, *, client_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        _assert_safe_directory(backups_dir, create=False, label="O diretorio de backups")
    except BackupValidationError:
        return result
    for candidate in sorted(backups_dir.glob("*.jkhub"), key=lambda item: item.name, reverse=True):
        try:
            envelope = _read_envelope(candidate, client_id)
        except BackupValidationError:
            continue
        result.append(
            {
                "backup_id": str(envelope.get("backup_id") or ""),
                "created_at": str(envelope.get("created_at") or ""),
                "scope": "80_Curadoria",
                "encrypted": True,
            }
        )
    return result


def restore_encrypted_backup(
    backup_id: object,
    curated_dir: Path,
    backups_dir: Path,
    restore_staging_dir: Path,
    *,
    client_id: str,
    passphrase: object,
    validate_note: Optional[Callable[[str, str], None]] = None,
    before_promote: Optional[Callable[[], None]] = None,
) -> dict[str, Any]:
    normalized_id = str(backup_id or "").strip().lower()
    if not _BACKUP_ID_RE.fullmatch(normalized_id):
        raise BackupValidationError("Backup nao encontrado.")
    secret = _safe_passphrase(passphrase)
    _assert_safe_directory(curated_dir, create=False, label="A raiz de curadoria")
    _assert_safe_directory(backups_dir, create=False, label="O diretorio de backups")
    _assert_safe_directory(restore_staging_dir, create=True, label="O staging de restauracao")
    path = backups_dir / f"{normalized_id}.jkhub"
    envelope = _read_envelope(path, client_id)
    try:
        salt = base64.b64decode(str(envelope["salt"]), validate=True)
        nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
        ciphertext = base64.b64decode(str(envelope["ciphertext"]), validate=True)
        plaintext = AESGCM(_derive_key(secret, salt)).decrypt(
            nonce,
            ciphertext,
            MAGIC + client_id.encode("utf-8"),
        )
        archive = json.loads(zlib.decompress(plaintext).decode("utf-8"))
    except (InvalidTag, ValueError, KeyError, UnicodeError, json.JSONDecodeError, zlib.error, binascii.Error) as error:
        raise BackupCryptoError("Senha incorreta ou backup adulterado.") from error
    if (
        not isinstance(archive, dict)
        or archive.get("schema_version") != 2
        or archive.get("backup_id") != normalized_id
        or archive.get("client_id") != client_id
        or archive.get("scope") != "80_Curadoria"
        or not isinstance(archive.get("files"), list)
    ):
        raise BackupValidationError("Conteudo do backup invalido.")
    files = archive["files"]
    if len(files) > MAX_FILES:
        raise BackupValidationError("Backup excede o limite de arquivos.")
    stage = restore_staging_dir / f"{normalized_id}-{uuid.uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    total = 0
    seen: set[str] = set()
    try:
        for entry in files:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "data"}:
                raise BackupValidationError("Entrada invalida no backup.")
            relative = _safe_relative(entry["path"])
            key = relative.as_posix().lower()
            if key in seen:
                raise BackupValidationError("Caminho duplicado no backup.")
            seen.add(key)
            try:
                data = base64.b64decode(str(entry["data"]), validate=True)
            except (ValueError, binascii.Error) as error:
                raise BackupValidationError("Conteudo invalido no backup.") from error
            if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise BackupValidationError("Hash ou tamanho de nota invalido no backup.")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise BackupValidationError("Backup excede o limite total.")
            try:
                content = data.decode("utf-8")
            except UnicodeError as error:
                raise BackupValidationError("Nota do backup nao esta em UTF-8.") from error
            if validate_note is not None:
                validate_note(relative.as_posix(), content)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for directory in ("ADRs", "Regras", "Notas"):
            (stage / directory).mkdir(parents=True, exist_ok=True)
        # Preserve the pre-restore curation as another encrypted backup using
        # the already verified passphrase. No plaintext restore-history is
        # retained after the atomic swap succeeds.
        pre_restore_backup = create_encrypted_backup(
            curated_dir,
            backups_dir,
            client_id=client_id,
            passphrase=passphrase,
        )
        if before_promote is not None:
            before_promote()
        history_root = restore_staging_dir.parent / "restore-history"
        _assert_safe_directory(history_root, create=True, label="O historico de restauracao")
        history = history_root / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        if curated_dir.exists():
            os.replace(curated_dir, history)
        try:
            os.replace(stage, curated_dir)
        except Exception:
            if history.exists() and not curated_dir.exists():
                os.replace(history, curated_dir)
            raise
        if history.exists():
            shutil.rmtree(history)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {
        "backup_id": normalized_id,
        "restored": True,
        "scope": "80_Curadoria",
        "file_count": len(files),
        "active_generation_changed": False,
        "pre_restore_backup_id": pre_restore_backup["backup_id"],
    }


__all__ = [
    "BackupCryptoError",
    "BackupError",
    "BackupValidationError",
    "create_encrypted_backup",
    "list_backups",
    "restore_encrypted_backup",
]
