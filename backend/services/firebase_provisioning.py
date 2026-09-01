"""Local, secret-free provisioning of the Firebase Admin credential.

The installer must never contain this credential.  This module accepts it only
from an authenticated local administrator, validates it against the configured
Firebase project, probes Firestore without writing data, and installs it at the
single persistent runtime location reserved for it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


CANONICAL_FILENAME = "firebase-service-account.json"
MAX_UPLOAD_BYTES = 64 * 1024
MAX_EXISTING_BYTES = 4 * MAX_UPLOAD_BYTES
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,29}$")
_CLIENT_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9.-]+\.gserviceaccount\.com$")
_REQUIRED_FIRESTORE_PERMISSIONS = frozenset({
    "datastore.entities.get",
    "datastore.entities.list",
    "datastore.entities.create",
    "datastore.entities.update",
    "datastore.entities.delete",
})
_PROVISIONING_LOCK = threading.RLock()
_RESTART_REQUIRED = False
_PROBE_SUCCESS_CACHE: dict[tuple[str, str], float] = {}
_STARTUP_BASE_KEY = ""
_STARTUP_CREDENTIAL_PATH_KEY = ""
_STARTUP_CREDENTIAL_FINGERPRINT = ""
_STARTUP_BASELINE_INITIALIZED = False


@dataclass(frozen=True)
class FirebaseProvisioningResult:
    status_code: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class _CredentialSelection:
    source: str
    path: Optional[Path]
    payload: Optional[dict[str, Any]]
    encoded: bytes
    canonical_occupied: bool


class FirebaseProvisioningError(Exception):
    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = str(code or "invalid_service_account")
        self.status_code = int(status_code or 400)


def _response(
    *,
    success: bool,
    configured: bool,
    ready: bool,
    source: str,
    code: str,
    restart_required: bool = False,
    can_migrate: bool = False,
    replacement_required: bool = False,
) -> dict[str, Any]:
    source_norm = source if source in {"canonical", "legacy", "none"} else "none"
    return {
        "success": bool(success),
        "configured": bool(configured),
        "ready": bool(ready),
        "source": source_norm,
        "code": str(code or "unknown"),
        "restart_required": bool(restart_required),
        "can_migrate": bool(can_migrate),
        "replacement_required": bool(replacement_required),
    }


def _result(status_code: int = 200, **payload: Any) -> FirebaseProvisioningResult:
    return FirebaseProvisioningResult(status_code=int(status_code), payload=_response(**payload))


def _canonical_path(info_dir: str | os.PathLike[str]) -> Path:
    return Path(info_dir).expanduser() / CANONICAL_FILENAME


def _strip_env_value(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _project_ids_from_env_file(candidate: Path) -> list[str]:
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_file():
        return []
    try:
        if candidate.stat().st_size > MAX_UPLOAD_BYTES:
            return []
        lines = candidate.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return []
    accepted = {"JK_FIREBASE_EXPECTED_PROJECT_ID", "FIREBASE_PROJECT_ID", "JK_FIREBASE_PROJECT_ID"}
    values: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        key = key.strip().removeprefix("export ").strip().upper()
        if key not in accepted:
            continue
        value = _strip_env_value(raw_value)
        if value:
            values.append(value)
    return values


def _project_ids_from_client_config(candidate: Path) -> list[str]:
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_file():
        return []
    try:
        if candidate.stat().st_size > MAX_UPLOAD_BYTES:
            return []
        data = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    remote_auth = data.get("remoteAuth") if isinstance(data, dict) else None
    value = str((remote_auth or {}).get("projectId") or "").strip() if isinstance(remote_auth, dict) else ""
    return [value] if value else []


def _expected_project_id(base_dir: str | os.PathLike[str], info_dir: str | os.PathLike[str]) -> str:
    authoritative: list[str] = []
    fallback: list[str] = []
    for key in (
        "JK_FIREBASE_EXPECTED_PROJECT_ID",
        "JK_REMOTE_AUTH_PROJECT_ID",
    ):
        value = str(os.getenv(key) or "").strip()
        if value:
            authoritative.append(value)

    for key in ("FIREBASE_PROJECT_ID", "JK_FIREBASE_PROJECT_ID"):
        value = str(os.getenv(key) or "").strip()
        if value:
            fallback.append(value)

    base = Path(base_dir).expanduser()
    info = Path(info_dir).expanduser()
    for candidate in (base / "firebase-presence.env", info / "firebase-presence.env"):
        authoritative.extend(_project_ids_from_env_file(candidate))
    for candidate in (base / "electron_app" / "client-config.json", base / "client-config.json"):
        authoritative.extend(_project_ids_from_client_config(candidate))

    normalized = []
    # FIREBASE_PROJECT_ID may be synthesized by Electron from the credential
    # currently on disk.  It is therefore only a fallback: a bad legacy file
    # must not be able to veto the trusted remote/client configuration that is
    # needed to replace it.
    for value in (authoritative if authoritative else fallback):
        value_norm = str(value or "").strip()
        if value_norm and value_norm not in normalized:
            normalized.append(value_norm)
    if not normalized:
        raise FirebaseProvisioningError("expected_project_unavailable", 503)
    if any(not _PROJECT_ID_RE.fullmatch(value) for value in normalized):
        raise FirebaseProvisioningError("project_configuration_invalid", 503)
    if len(normalized) != 1:
        raise FirebaseProvisioningError("project_configuration_conflict", 503)
    return normalized[0]


def _validate_private_key(value: str) -> None:
    key_text = str(value or "")
    if (
        not key_text.startswith("-----BEGIN PRIVATE KEY-----")
        or "-----END PRIVATE KEY-----" not in key_text
        or len(key_text.encode("utf-8")) > 32 * 1024
    ):
        raise FirebaseProvisioningError("invalid_service_account", 400)
    try:
        private_key = serialization.load_pem_private_key(key_text.encode("utf-8"), password=None)
    except Exception as exc:
        raise FirebaseProvisioningError("invalid_service_account", 400) from exc
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise FirebaseProvisioningError("invalid_service_account", 400)


def _service_account_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _validate_service_account(raw: bytes, expected_project_id: str) -> tuple[dict[str, Any], bytes]:
    if len(raw or b"") > MAX_UPLOAD_BYTES:
        raise FirebaseProvisioningError("oversize", 413)
    try:
        data = json.loads(bytes(raw or b"").decode("utf-8-sig"))
    except Exception as exc:
        raise FirebaseProvisioningError("invalid_json", 400) from exc
    if not isinstance(data, dict):
        raise FirebaseProvisioningError("invalid_service_account", 400)
    if str(data.get("type") or "").strip() != "service_account":
        raise FirebaseProvisioningError("invalid_service_account", 400)
    project_id = str(data.get("project_id") or "").strip()
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise FirebaseProvisioningError("invalid_service_account", 400)
    if project_id != expected_project_id:
        raise FirebaseProvisioningError("wrong_project", 400)
    client_email = str(data.get("client_email") or "").strip()
    if len(client_email) > 320 or not _CLIENT_EMAIL_RE.fullmatch(client_email):
        raise FirebaseProvisioningError("invalid_service_account", 400)
    if data.get("token_uri") != "https://oauth2.googleapis.com/token":
        raise FirebaseProvisioningError("invalid_service_account", 400)
    if "universe_domain" in data and data.get("universe_domain") != "googleapis.com":
        raise FirebaseProvisioningError("invalid_service_account", 400)
    _validate_private_key(str(data.get("private_key") or ""))
    encoded = _service_account_bytes(data)
    if len(encoded) > MAX_UPLOAD_BYTES:
        raise FirebaseProvisioningError("oversize", 413)
    return data, encoded


def _read_regular_file(candidate: Path, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    try:
        metadata = os.stat(candidate, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        raise FirebaseProvisioningError("not_found", 404)
    except Exception as exc:
        raise FirebaseProvisioningError("unsafe_destination", 409) from exc
    if not stat.S_ISREG(metadata.st_mode) or candidate.is_symlink():
        raise FirebaseProvisioningError("unsafe_destination", 409)
    if metadata.st_size > int(limit):
        raise FirebaseProvisioningError("oversize", 413)
    try:
        data = candidate.read_bytes()
    except Exception as exc:
        raise FirebaseProvisioningError("read_failed", 500) from exc
    if len(data) > int(limit):
        raise FirebaseProvisioningError("oversize", 413)
    return data


def _validated_candidate(candidate: Path, expected_project_id: str) -> Optional[tuple[dict[str, Any], bytes]]:
    try:
        return _validate_service_account(_read_regular_file(candidate), expected_project_id)
    except FirebaseProvisioningError:
        return None


def _legacy_candidates(base_dir: str | os.PathLike[str], info_dir: str | os.PathLike[str]) -> list[Path]:
    base = Path(base_dir).expanduser()
    info = Path(info_dir).expanduser()
    candidates = [
        info / "firebase_service_account.json",
        base / "firebase-service-account.json",
        base / "firebase_service_account.json",
    ]
    try:
        for entry in os.scandir(base):
            if not entry.is_file(follow_symlinks=False):
                continue
            lower = entry.name.lower()
            if (
                re.fullmatch(r"jkjkjk-.*\.json", lower)
                or re.search(r"service[-_ ]?account.*\.json$", lower)
                or (lower.startswith("firebase-") and lower.endswith(".json"))
            ):
                candidates.append(Path(entry.path))
    except Exception:
        pass
    canonical = _canonical_path(info)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key == os.path.normcase(os.path.abspath(canonical)) or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _select_credential(
    base_dir: str | os.PathLike[str],
    info_dir: str | os.PathLike[str],
    expected_project_id: str,
) -> _CredentialSelection:
    canonical = _canonical_path(info_dir)
    canonical_occupied = os.path.lexists(canonical)
    if canonical_occupied:
        validated = _validated_candidate(canonical, expected_project_id)
        if validated is not None:
            payload, encoded = validated
            return _CredentialSelection("canonical", canonical, payload, encoded, True)
    for candidate in _legacy_candidates(base_dir, info_dir):
        if not os.path.lexists(candidate):
            continue
        validated = _validated_candidate(candidate, expected_project_id)
        if validated is None:
            continue
        payload, encoded = validated
        return _CredentialSelection("legacy", candidate, payload, encoded, canonical_occupied)
    return _CredentialSelection("none", None, None, b"", canonical_occupied)


def _probe_timeout_seconds() -> float:
    try:
        value = float(os.getenv("JK_FIREBASE_PROVISIONING_PROBE_TIMEOUT_SECONDS", "5") or 5)
    except Exception:
        value = 5.0
    return max(2.0, min(value, 10.0))


def _probe_cache_seconds() -> float:
    try:
        value = float(os.getenv("JK_FIREBASE_PROVISIONING_PROBE_CACHE_SECONDS", "300") or 300)
    except Exception:
        value = 300.0
    return max(30.0, min(value, 600.0))


def _invalidate_probe_cache() -> None:
    _PROBE_SUCCESS_CACHE.clear()


def _probe_service_account_cached(
    payload: dict[str, Any],
    encoded: bytes,
    expected_project_id: str,
) -> None:
    now = time.monotonic()
    key = (expected_project_id, _fingerprint(encoded))
    expires_at = float(_PROBE_SUCCESS_CACHE.get(key) or 0.0)
    if expires_at > now:
        return
    _PROBE_SUCCESS_CACHE.pop(key, None)
    _probe_service_account(payload, expected_project_id)
    _PROBE_SUCCESS_CACHE[key] = now + _probe_cache_seconds()
    if len(_PROBE_SUCCESS_CACHE) > 8:
        for stale_key, stale_expiry in sorted(_PROBE_SUCCESS_CACHE.items(), key=lambda item: item[1])[:-8]:
            _PROBE_SUCCESS_CACHE.pop(stale_key, None)


def _probe_service_account(payload: dict[str, Any], expected_project_id: str) -> None:
    """Perform one bounded Firestore read using an isolated Firebase app."""
    try:
        import firebase_admin
        from firebase_admin import credentials as firebase_credentials
        from firebase_admin import firestore as firebase_firestore
    except Exception as exc:
        raise FirebaseProvisioningError("firebase_admin_unavailable", 503) from exc

    app = None
    app_name = f"jk_firebase_provisioning_{uuid.uuid4().hex}"
    try:
        credential = firebase_credentials.Certificate(payload)
        app = firebase_admin.initialize_app(
            credential,
            options={"projectId": expected_project_id},
            name=app_name,
        )
        database = firebase_firestore.client(app=app)
        query = database.collection("jk_sistema_shared_sync_keyrings").limit(1)
        list(query.stream(retry=None, timeout=_probe_timeout_seconds()))
        _test_required_iam_permissions(payload, expected_project_id)
    except FirebaseProvisioningError:
        raise
    except Exception as exc:
        raise FirebaseProvisioningError("firebase_test_failed", 503) from exc
    finally:
        if app is not None:
            try:
                firebase_admin.delete_app(app)
            except Exception:
                pass


def _test_required_iam_permissions(payload: dict[str, Any], expected_project_id: str) -> None:
    """Non-mutating check for every entity permission used by Shared Sync."""
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except Exception as exc:
        raise FirebaseProvisioningError("firebase_permissions_test_failed", 503) from exc

    session = None
    timeout = _probe_timeout_seconds()
    try:
        credentials = service_account.Credentials.from_service_account_info(
            payload,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        session = AuthorizedSession(
            credentials,
            refresh_timeout=timeout,
            max_refresh_attempts=1,
        )
        response = session.post(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{expected_project_id}:testIamPermissions",
            json={"permissions": sorted(_REQUIRED_FIRESTORE_PERMISSIONS)},
            timeout=timeout,
            allow_redirects=False,
        )
        if int(response.status_code or 0) != 200:
            raise FirebaseProvisioningError("firebase_permissions_test_failed", 503)
        if len(response.content or b"") > MAX_UPLOAD_BYTES:
            raise FirebaseProvisioningError("firebase_permissions_test_failed", 503)
        response_payload = response.json()
        granted_raw = response_payload.get("permissions") if isinstance(response_payload, dict) else None
        granted = {
            str(permission or "").strip()
            for permission in (granted_raw if isinstance(granted_raw, list) else [])
            if str(permission or "").strip()
        }
        if not _REQUIRED_FIRESTORE_PERMISSIONS.issubset(granted):
            raise FirebaseProvisioningError("firebase_permissions_insufficient", 403)
    except FirebaseProvisioningError:
        raise
    except Exception as exc:
        raise FirebaseProvisioningError("firebase_permissions_test_failed", 503) from exc
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def _fingerprint(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _path_key(candidate: Path | str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(Path(candidate).expanduser()))


def initialize_firebase_provisioning_runtime(
    *,
    base_dir: str | os.PathLike[str],
    info_dir: str | os.PathLike[str],
) -> None:
    """Capture the credential identity selected when this backend starts.

    The Firebase Admin app keeps its credential in memory after initialization.
    A later same-path file replacement therefore cannot be considered active
    until the backend restarts, even if an isolated probe of the new file works.
    """
    global _STARTUP_BASE_KEY
    global _STARTUP_CREDENTIAL_PATH_KEY
    global _STARTUP_CREDENTIAL_FINGERPRINT
    global _STARTUP_BASELINE_INITIALIZED

    with _PROVISIONING_LOCK:
        _STARTUP_BASE_KEY = _path_key(base_dir)
        _STARTUP_CREDENTIAL_PATH_KEY = ""
        _STARTUP_CREDENTIAL_FINGERPRINT = ""
        _STARTUP_BASELINE_INITIALIZED = True

        # Inline/base64 credentials take precedence in the live Firebase
        # runtime.  They never become equivalent to a provisioned file without
        # a restart, so no file baseline is recorded for them.
        if any(
            str(os.getenv(key) or "").strip()
            for key in (
                "FIREBASE_SERVICE_ACCOUNT_JSON",
                "FIREBASE_ADMIN_CREDENTIALS_JSON",
                "FIREBASE_SERVICE_ACCOUNT_BASE64",
                "FIREBASE_ADMIN_CREDENTIALS_BASE64",
            )
        ):
            return

        try:
            from backend.services import admin_usuarios_firebase

            selected_text = str(admin_usuarios_firebase._firebase_service_account_file() or "").strip()
        except Exception:
            selected_text = ""

        if not selected_text:
            return
        selected = Path(selected_text).expanduser()
        if not selected.is_absolute():
            selected = Path(base_dir).expanduser() / selected
        try:
            raw = _read_regular_file(selected, MAX_EXISTING_BYTES)
        except FirebaseProvisioningError:
            return

        _STARTUP_CREDENTIAL_PATH_KEY = _path_key(selected)
        try:
            expected_project_id = _expected_project_id(base_dir, info_dir)
            _payload, encoded = _validate_service_account(raw, expected_project_id)
        except FirebaseProvisioningError:
            # Preserve a non-secret identity for fail-closed change detection,
            # even if the startup file itself is not a usable credential.
            encoded = raw
        _STARTUP_CREDENTIAL_FINGERPRINT = _fingerprint(encoded)


def _stage_and_replace(target: Path, encoded: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".firebase-service-account-",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.chmod(temporary, 0o600)
        except Exception:
            pass
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except Exception:
            pass


def _atomic_install(info_dir: str | os.PathLike[str], encoded: bytes) -> None:
    info = Path(info_dir).expanduser()
    if info.exists() and (info.is_symlink() or not info.is_dir()):
        raise FirebaseProvisioningError("unsafe_destination", 409)
    try:
        info.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise FirebaseProvisioningError("write_failed", 500) from exc
    if info.is_symlink() or not info.is_dir():
        raise FirebaseProvisioningError("unsafe_destination", 409)

    target = _canonical_path(info)
    previous: Optional[bytes] = None
    if os.path.lexists(target):
        previous = _read_regular_file(target, MAX_EXISTING_BYTES)
    try:
        _stage_and_replace(target, encoded)
        installed = _read_regular_file(target, MAX_UPLOAD_BYTES)
        if installed != encoded:
            raise FirebaseProvisioningError("write_failed", 500)
    except Exception as exc:
        try:
            if previous is None:
                if os.path.lexists(target) and target.is_file() and not target.is_symlink():
                    target.unlink()
            else:
                _stage_and_replace(target, previous)
        except Exception:
            pass
        if isinstance(exc, FirebaseProvisioningError):
            raise exc
        raise FirebaseProvisioningError("write_failed", 500) from exc


def _runtime_requires_restart(
    base_dir: str | os.PathLike[str],
    selected_path: Optional[Path],
    selected_encoded: bytes,
) -> bool:
    if _RESTART_REQUIRED:
        return True
    if any(
        str(os.getenv(key) or "").strip()
        for key in (
            "FIREBASE_SERVICE_ACCOUNT_JSON",
            "FIREBASE_ADMIN_CREDENTIALS_JSON",
            "FIREBASE_SERVICE_ACCOUNT_BASE64",
            "FIREBASE_ADMIN_CREDENTIALS_BASE64",
        )
    ):
        return True
    configured_file = str(
        os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE")
        or os.getenv("FIREBASE_CREDENTIALS_FILE")
        or ""
    ).strip()
    if configured_file and selected_path is not None:
        configured_path = Path(configured_file).expanduser()
        if not configured_path.is_absolute():
            configured_path = Path(base_dir).expanduser() / configured_path
        if os.path.normcase(os.path.abspath(configured_path)) != os.path.normcase(os.path.abspath(selected_path)):
            return True
    if not _STARTUP_BASELINE_INITIALIZED or _STARTUP_BASE_KEY != _path_key(base_dir):
        return True
    selected_path_key = _path_key(selected_path) if selected_path is not None else ""
    selected_fingerprint = _fingerprint(selected_encoded) if selected_encoded else ""
    if selected_path_key != _STARTUP_CREDENTIAL_PATH_KEY:
        return True
    if selected_fingerprint != _STARTUP_CREDENTIAL_FINGERPRINT:
        return True
    try:
        from backend.services import admin_usuarios_firebase

        if not bool(admin_usuarios_firebase._firebase_deve_usar()):
            return True
        if not bool(admin_usuarios_firebase._firebase_live_features_ativas()):
            return True
    except Exception:
        return True
    return False


def _error_from_exception(
    error: FirebaseProvisioningError,
    *,
    selection: Optional[_CredentialSelection] = None,
) -> FirebaseProvisioningResult:
    current = selection or _CredentialSelection("none", None, None, b"", False)
    configured = current.source in {"canonical", "legacy"}
    return _result(
        error.status_code,
        success=False,
        configured=configured,
        ready=False,
        source=current.source,
        code=error.code,
        restart_required=False,
        can_migrate=current.source == "legacy" and not current.canonical_occupied,
        replacement_required=error.code in {"replacement_required", "unsafe_destination"},
    )


def firebase_provisioning_status(
    *,
    base_dir: str | os.PathLike[str],
    info_dir: str | os.PathLike[str],
) -> FirebaseProvisioningResult:
    with _PROVISIONING_LOCK:
        try:
            expected_project_id = _expected_project_id(base_dir, info_dir)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error)
        selection = _select_credential(base_dir, info_dir, expected_project_id)
        if selection.source == "none":
            return _result(
                200,
                success=True,
                configured=False,
                ready=False,
                source="none",
                code="invalid_canonical" if selection.canonical_occupied else "not_configured",
                restart_required=False,
                can_migrate=False,
                replacement_required=selection.canonical_occupied,
            )
        try:
            _probe_service_account_cached(
                selection.payload or {},
                selection.encoded,
                expected_project_id,
            )
        except FirebaseProvisioningError as error:
            return _error_from_exception(error, selection=selection)
        restart_required = _runtime_requires_restart(base_dir, selection.path, selection.encoded)
        return _result(
            200,
            success=True,
            configured=True,
            ready=not restart_required,
            source=selection.source,
            code=(
                "restart_required"
                if restart_required
                else ("ready" if selection.source == "canonical" else "legacy_detected")
            ),
            restart_required=restart_required,
            can_migrate=selection.source == "legacy" and not selection.canonical_occupied,
            replacement_required=selection.source == "legacy" and selection.canonical_occupied,
        )


def firebase_provisioning_import(
    raw: bytes,
    *,
    replace: bool,
    base_dir: str | os.PathLike[str],
    info_dir: str | os.PathLike[str],
) -> FirebaseProvisioningResult:
    global _RESTART_REQUIRED
    with _PROVISIONING_LOCK:
        _invalidate_probe_cache()
        try:
            expected_project_id = _expected_project_id(base_dir, info_dir)
            payload, encoded = _validate_service_account(raw, expected_project_id)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error)

        selection = _select_credential(base_dir, info_dir, expected_project_id)
        incoming_fingerprint = _fingerprint(encoded)
        same_active = bool(selection.encoded) and _fingerprint(selection.encoded) == incoming_fingerprint
        canonical = _canonical_path(info_dir)
        canonical_valid = _validated_candidate(canonical, expected_project_id) if os.path.lexists(canonical) else None
        same_canonical = bool(canonical_valid) and _fingerprint(canonical_valid[1]) == incoming_fingerprint

        replacement_needed = bool(
            (selection.canonical_occupied and not same_canonical)
            or (selection.source == "legacy" and not same_active)
        )
        if replacement_needed and not replace:
            return _result(
                409,
                success=False,
                configured=selection.source in {"canonical", "legacy"},
                ready=False,
                source=selection.source,
                code="replacement_required",
                restart_required=False,
                can_migrate=selection.source == "legacy" and not selection.canonical_occupied,
                replacement_required=True,
            )
        if same_canonical:
            try:
                _probe_service_account(payload, expected_project_id)
            except FirebaseProvisioningError as error:
                return _error_from_exception(error, selection=selection)
            restart_required = _runtime_requires_restart(base_dir, canonical, encoded)
            return _result(
                200,
                success=True,
                configured=True,
                ready=not restart_required,
                source="canonical",
                code="restart_required" if restart_required else "already_configured",
                restart_required=restart_required,
                can_migrate=False,
                replacement_required=False,
            )
        try:
            _probe_service_account(payload, expected_project_id)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error, selection=selection)
        try:
            _atomic_install(info_dir, encoded)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error, selection=selection)
        _RESTART_REQUIRED = True
        _invalidate_probe_cache()
        return _result(
            200,
            success=True,
            configured=True,
            ready=False,
            source="canonical",
            code="imported",
            restart_required=True,
            can_migrate=False,
            replacement_required=False,
        )


def firebase_provisioning_migrate_legacy(
    *,
    base_dir: str | os.PathLike[str],
    info_dir: str | os.PathLike[str],
) -> FirebaseProvisioningResult:
    global _RESTART_REQUIRED
    with _PROVISIONING_LOCK:
        _invalidate_probe_cache()
        try:
            expected_project_id = _expected_project_id(base_dir, info_dir)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error)
        selection = _select_credential(base_dir, info_dir, expected_project_id)
        if selection.source == "canonical":
            current = firebase_provisioning_status(base_dir=base_dir, info_dir=info_dir)
            if current.payload.get("ready"):
                payload_out = dict(current.payload)
                payload_out["code"] = "already_canonical"
                return FirebaseProvisioningResult(status_code=200, payload=payload_out)
            return current
        if selection.canonical_occupied:
            return _result(
                409,
                success=False,
                configured=selection.source == "legacy",
                ready=False,
                source=selection.source,
                code="replacement_required",
                restart_required=False,
                can_migrate=False,
                replacement_required=True,
            )
        if selection.source != "legacy" or selection.payload is None:
            return _result(
                404,
                success=False,
                configured=False,
                ready=False,
                source="none",
                code="legacy_not_found",
                restart_required=False,
                can_migrate=False,
                replacement_required=False,
            )
        try:
            _probe_service_account(selection.payload, expected_project_id)
            _atomic_install(info_dir, selection.encoded)
        except FirebaseProvisioningError as error:
            return _error_from_exception(error, selection=selection)
        _RESTART_REQUIRED = True
        _invalidate_probe_cache()
        return _result(
            200,
            success=True,
            configured=True,
            ready=False,
            source="canonical",
            code="migrated",
            restart_required=True,
            can_migrate=False,
            replacement_required=False,
        )


def reset_firebase_provisioning_runtime_state_for_tests() -> None:
    """Reset only the non-secret in-process restart marker used by unit tests."""
    global _RESTART_REQUIRED
    global _STARTUP_BASE_KEY
    global _STARTUP_CREDENTIAL_PATH_KEY
    global _STARTUP_CREDENTIAL_FINGERPRINT
    global _STARTUP_BASELINE_INITIALIZED
    _RESTART_REQUIRED = False
    _STARTUP_BASE_KEY = ""
    _STARTUP_CREDENTIAL_PATH_KEY = ""
    _STARTUP_CREDENTIAL_FINGERPRINT = ""
    _STARTUP_BASELINE_INITIALIZED = False
    _invalidate_probe_cache()


__all__ = [
    "CANONICAL_FILENAME",
    "MAX_UPLOAD_BYTES",
    "FirebaseProvisioningResult",
    "initialize_firebase_provisioning_runtime",
    "firebase_provisioning_status",
    "firebase_provisioning_import",
    "firebase_provisioning_migrate_legacy",
]
