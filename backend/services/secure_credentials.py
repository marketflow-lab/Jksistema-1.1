"""Local secure storage for sensitive runtime secrets.

On Windows this module stores values in Windows Credential Manager. Other
platforms deliberately report the store as unavailable so callers can keep using
their existing environment/file fallback paths.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import os
from typing import Any


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168

SERVICE_NAME = "JK Sistema"
TARGET_PREFIX = "JK Sistema/IA"
META_TARGET = f"{TARGET_PREFIX}/__meta__"
SCOPED_TARGET_PREFIX = "JK Sistema/Scoped"
SCOPED_TARGET_MAX_CHARS = 2048

SUPPORTED_SECRET_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_AGENT_API_KEY",
    "GROQ_API_KEY",
)

SECRET_KEY_ALIASES = {
    "OPENAI": "OPENAI_API_KEY",
    "IA_OPENAI_API_KEY": "OPENAI_API_KEY",
    "DEEPSEEK": "DEEPSEEK_API_KEY",
    "IA_DEEPSEEK_API_KEY": "DEEPSEEK_API_KEY",
    "GEMINI": "GEMINI_API_KEY",
    "GOOGLE_API_KEY": "GEMINI_API_KEY",
    "GOOGLE_GENAI_API_KEY": "GEMINI_AGENT_API_KEY",
    "VERTEX_AGENT_API_KEY": "GEMINI_AGENT_API_KEY",
    "VERTEX_AI_API_KEY": "GEMINI_AGENT_API_KEY",
    "IA_AGENT_API_KEY": "GEMINI_AGENT_API_KEY",
    "JK_AGENT_ENDPOINT_API_KEY": "GEMINI_AGENT_API_KEY",
    "GROQ": "GROQ_API_KEY",
    "IA_GROQ_API_KEY": "GROQ_API_KEY",
}


def _available() -> bool:
    return os.name == "nt" and hasattr(ctypes, "windll")


if _available():
    LPBYTE = ctypes.POINTER(wintypes.BYTE)

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", LPBYTE),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", wintypes.LPVOID),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
    _advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
    _advapi32.CredWriteW.argtypes = [PCREDENTIALW, wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL
    _advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(PCREDENTIALW),
    ]
    _advapi32.CredReadW.restype = wintypes.BOOL
    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL
    _advapi32.CredFree.argtypes = [wintypes.LPVOID]
    _advapi32.CredFree.restype = None
else:
    CREDENTIALW = None
    PCREDENTIALW = None
    _advapi32 = None


def secure_store_available() -> bool:
    return _available()


def normalize_secret_key(key: str) -> str:
    key_norm = str(key or "").replace("\ufeff", "").strip().upper()
    return SECRET_KEY_ALIASES.get(key_norm, key_norm)


def _target_name(key: str) -> str:
    return f"{TARGET_PREFIX}/{normalize_secret_key(key)}"


def _scoped_target_name(target: str) -> str:
    """Map an app-owned logical target to a non-identifying credential name."""
    target_norm = str(target or "").strip()
    if not target_norm:
        raise ValueError("Destino seguro vazio.")
    if "\x00" in target_norm or len(target_norm) > SCOPED_TARGET_MAX_CHARS:
        raise ValueError("Destino seguro invalido.")
    target_hash = hashlib.sha256(target_norm.encode("utf-8")).hexdigest()
    return f"{SCOPED_TARGET_PREFIX}/{target_hash}"


def _write_target(target: str, value: str) -> None:
    if not _available() or CREDENTIALW is None:
        raise RuntimeError("Windows Credential Manager indisponivel neste ambiente.")
    payload = str(value or "").encode("utf-8")
    if not payload:
        raise ValueError("Valor vazio nao deve ser gravado no cofre.")
    buffer = ctypes.create_string_buffer(payload)
    credential = CREDENTIALW()
    credential.Flags = 0
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "Credencial local do JK Sistema"
    credential.CredentialBlobSize = len(payload)
    credential.CredentialBlob = ctypes.cast(buffer, LPBYTE)
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.AttributeCount = 0
    credential.Attributes = None
    credential.TargetAlias = None
    credential.UserName = SERVICE_NAME
    if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def _read_target(target: str) -> str:
    if not _available() or PCREDENTIALW is None:
        return ""
    credential_ptr = PCREDENTIALW()
    if not _advapi32.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(credential_ptr)):
        if ctypes.get_last_error() == ERROR_NOT_FOUND:
            return ""
        return ""
    try:
        credential = credential_ptr.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-8", errors="ignore").strip()
    finally:
        _advapi32.CredFree(credential_ptr)


def _delete_target(target: str) -> bool:
    if not _available():
        return False
    if _advapi32.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        return True
    return ctypes.get_last_error() == ERROR_NOT_FOUND


def write_secret(key: str, value: str) -> bool:
    key_norm = normalize_secret_key(key)
    if key_norm not in SUPPORTED_SECRET_KEYS:
        return False
    value_norm = str(value or "").strip()
    if not value_norm:
        return delete_secret(key_norm)
    _write_target(_target_name(key_norm), value_norm)
    return True


def read_secret(key: str) -> str:
    key_norm = normalize_secret_key(key)
    if key_norm not in SUPPORTED_SECRET_KEYS:
        return ""
    return _read_target(_target_name(key_norm))


def read_any_secret(keys: tuple[str, ...] | list[str]) -> str:
    for key in keys or ():
        value = read_secret(str(key or ""))
        if value:
            return value
    return ""


def delete_secret(key: str) -> bool:
    key_norm = normalize_secret_key(key)
    if key_norm not in SUPPORTED_SECRET_KEYS:
        return False
    return _delete_target(_target_name(key_norm))


def write_scoped_secret(target: str, value: str) -> bool:
    """Persist an app-scoped secret without exposing its logical target or value."""
    value_norm = str(value or "").strip()
    if not value_norm:
        return delete_scoped_secret(target)
    _write_target(_scoped_target_name(target), value_norm)
    return True


def read_scoped_secret(target: str) -> str:
    """Read an app-scoped secret from the current Windows user's secure store."""
    return _read_target(_scoped_target_name(target))


def delete_scoped_secret(target: str) -> bool:
    """Delete an app-scoped secret without allowing arbitrary credential targets."""
    return _delete_target(_scoped_target_name(target))


def write_metadata(version: str | None = None, source: str | None = None) -> None:
    payload = {
        "version": str(version or "").strip(),
        "source": str(source or "").strip(),
    }
    _write_target(META_TARGET, json.dumps(payload, ensure_ascii=False))


def read_metadata() -> dict[str, Any]:
    raw = _read_target(META_TARGET)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_secrets_bundle(payload: dict[str, Any], version: str | None = None, source: str | None = None) -> dict[str, Any]:
    saved: list[str] = []
    skipped: list[str] = []
    for raw_key, raw_value in (payload or {}).items():
        key = normalize_secret_key(str(raw_key or ""))
        value = str(raw_value or "").strip()
        if key not in SUPPORTED_SECRET_KEYS:
            skipped.append(str(raw_key or ""))
            continue
        if not value:
            delete_secret(key)
            continue
        write_secret(key, value)
        saved.append(key)
    if saved:
        write_metadata(version=version, source=source)
    return {
        "saved": saved,
        "skipped": skipped,
        "available": secure_store_available(),
        "version": str(version or "").strip(),
    }


def secrets_status() -> dict[str, Any]:
    configured = {key: bool(read_secret(key)) for key in SUPPORTED_SECRET_KEYS}
    meta = read_metadata()
    return {
        "available": secure_store_available(),
        "configured": configured,
        "version": str(meta.get("version") or "").strip(),
        "source": str(meta.get("source") or "").strip(),
    }
