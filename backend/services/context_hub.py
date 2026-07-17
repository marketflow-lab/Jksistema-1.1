"""Generation-aware, tenant-isolated storage for the JK Sistema Context Hub.

The module deliberately does not depend on ``backend_api`` at import time.  A
runtime may configure the checkout and info roots explicitly, while tests and
small tools can use the conservative defaults.  The only tenant selected by
the HTTP layer is the one returned by the authenticated session.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

import yaml


CONTEXT_HUB_SCHEMA_VERSION = 1
GENERATION_STATES = {
    "staging",
    "validating",
    "ready",
    "active",
    "superseded",
    "failed",
}
FRONTMATTER_REQUIRED = (
    "id",
    "type",
    "managed",
    "status",
    "ai_usage",
    "tenant_scope",
    "sensitivity",
    "truth_class",
    "required_permissions",
    "surface",
    "source_version",
    "source_refs",
    "source_hash",
    "generated_at",
)
VAULT_DIRECTORIES = (
    "00_Inicio",
    "70_Gerado/Mapas",
    "70_Gerado/Dominios",
    "70_Gerado/Fluxos",
    "70_Gerado/Contratos",
    "70_Gerado/Operacao",
    "70_Gerado/Produtos",
    "80_Curadoria/ADRs",
    "80_Curadoria/Regras",
    "80_Curadoria/Notas",
    "90_Arquivo",
)

_SAFE_CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_SAFE_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
CONTEXT_BUNDLE_REQUIRED_PATHS = frozenset(
    {
        "docs/knowledge/README.md",
        "docs/knowledge/security-policy.md",
        "docs/knowledge/vault-structure.md",
        "docs/knowledge/templates/curated-note.md",
        "docs/knowledge/templates/generated-note.md",
    }
)
_VOLATILE_INVENTORY_KEYS = {
    "generated_at",
    "generation_time",
    "duration_ms",
    "elapsed_ms",
    "scanned_at",
    "updated_at",
}


class ContextHubError(RuntimeError):
    """Base error safe to translate at the HTTP boundary."""


class ContextHubValidationError(ContextHubError):
    """The requested operation did not pass validation."""


class ContextHubConflictError(ContextHubError):
    """The active generation changed or another operation is running."""


class ContextHubNotFoundError(ContextHubError):
    """The requested generation was not found for this tenant."""


@dataclass(frozen=True)
class ContextHubRuntimeConfig:
    base_dir: Path
    info_root: Path
    surface: str


@dataclass(frozen=True)
class ContextHubPaths:
    client_id: str
    info_root: Path
    tenant_dir: Path
    vault_dir: Path
    generated_dir: Path
    curated_dir: Path
    internal_dir: Path
    db_path: Path
    staging_dir: Path
    generations_dir: Path
    lock_path: Path
    journal_path: Path


@dataclass(frozen=True)
class _InfoRootSnapshot:
    absolute: Path
    resolved: Path
    identity: Optional[tuple[int, int, int]]


_CONFIG_LOCK = threading.RLock()
_RUNTIME_CONFIG: Optional[ContextHubRuntimeConfig] = None
_TENANT_LOCKS_GUARD = threading.Lock()
_TENANT_LOCKS: dict[str, threading.RLock] = {}
_WATCHERS_GUARD = threading.Lock()
_WATCHERS: dict[str, tuple[threading.Thread, threading.Event]] = {}
_WATCH_FINGERPRINTS: dict[str, str] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _json_canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _normalize_surface(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"installed", "packaged", "package", "development", "checkout", "test"}:
        return "development" if normalized == "checkout" else normalized
    return "development"


def configure_context_hub(
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> ContextHubRuntimeConfig:
    """Configure roots without creating files or starting background work."""

    global _RUNTIME_CONFIG
    default_base = Path(__file__).resolve().parents[2]
    base = Path(base_dir or default_base).expanduser().resolve()
    raw_info = Path(info_root or os.getenv("JK_INFO_DIR") or (base / "info")).expanduser()
    if not raw_info.is_absolute():
        raw_info = base / raw_info
    raw_info = raw_info.absolute()
    info_snapshot = _snapshot_info_root(raw_info)
    configured = ContextHubRuntimeConfig(
        base_dir=base,
        info_root=info_snapshot.resolved,
        surface=_normalize_surface(surface or os.getenv("JK_CONTEXT_HUB_SURFACE") or "development"),
    )
    with _CONFIG_LOCK:
        _RUNTIME_CONFIG = configured
    return configured


def _runtime_config(
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> ContextHubRuntimeConfig:
    if base_dir is not None or info_root is not None or surface is not None:
        current = _RUNTIME_CONFIG or configure_context_hub()
        base = Path(base_dir).resolve() if base_dir is not None else current.base_dir
        info = Path(info_root).expanduser().absolute() if info_root is not None else current.info_root
        return ContextHubRuntimeConfig(base, info, _normalize_surface(surface or current.surface))
    with _CONFIG_LOCK:
        current = _RUNTIME_CONFIG
    return current or configure_context_hub()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(path))
    except OSError:
        return True


def _snapshot_info_root(root: Path) -> _InfoRootSnapshot:
    """Capture a stable, non-redirected info root for one path operation."""

    absolute = root.expanduser().absolute()

    def capture() -> tuple[Path, Optional[tuple[int, int, int]]]:
        try:
            resolved = absolute.resolve(strict=False)
            if _is_link_or_junction(absolute):
                raise ContextHubValidationError(
                    "Links simbolicos ou junctions nao sao aceitos como raiz info do Context Hub."
                )
            try:
                stat = os.lstat(absolute)
            except FileNotFoundError:
                identity = None
            else:
                identity = (int(stat.st_dev), int(stat.st_ino), int(stat.st_mode))
            return resolved, identity
        except ContextHubValidationError:
            raise
        except OSError as error:
            raise ContextHubValidationError(
                "Nao foi possivel validar a raiz info do Context Hub."
            ) from error

    resolved_before, identity_before = capture()
    resolved_after, identity_after = capture()
    if resolved_before != resolved_after or identity_before != identity_after:
        raise ContextHubValidationError(
            "A raiz info do Context Hub foi alterada durante a validacao."
        )
    if resolved_after != absolute:
        raise ContextHubValidationError(
            "A raiz info do Context Hub nao pode ser redirecionada."
        )
    return _InfoRootSnapshot(absolute=absolute, resolved=resolved_after, identity=identity_after)


def _assert_path_chain_safe(path: Path, root: Path) -> None:
    root_resolved = root.resolve()
    candidate = path.absolute()
    if not _is_relative_to(candidate, root.absolute()) and candidate != root.absolute():
        raise ContextHubValidationError("Caminho fora da raiz autorizada do Context Hub.")
    relative = candidate.relative_to(root.absolute()) if candidate != root.absolute() else Path()
    cursor = root.absolute()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() and _is_link_or_junction(cursor):
            raise ContextHubValidationError("Links simbolicos ou junctions nao sao aceitos no Context Hub.")
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root_resolved) and resolved != root_resolved:
        raise ContextHubValidationError("Caminho resolvido fora da raiz autorizada do Context Hub.")


def _normalize_client_id(client_id: object) -> str:
    raw = str(client_id or "")
    normalized = raw.strip()
    reserved_stem = normalized.split(".", 1)[0].lower()
    if (
        not normalized
        or raw != normalized
        or normalized.endswith((".", " "))
        or normalized != normalized.lower()
        or normalized in {".", ".."}
        or reserved_stem in _WINDOWS_RESERVED_NAMES
        or not _SAFE_CLIENT_ID_RE.fullmatch(normalized)
    ):
        raise ContextHubValidationError("Identificador de cliente invalido para o Context Hub.")
    return normalized


def _tenant_paths(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> ContextHubPaths:
    client = _normalize_client_id(client_id)
    config = _runtime_config(info_root=info_root)
    root_snapshot = _snapshot_info_root(config.info_root)
    root = root_snapshot.absolute
    tenant = root / client
    _assert_path_chain_safe(tenant, root)
    vault = tenant / "ContextVault"
    internal = tenant / "context_hub"
    for candidate in (vault, internal):
        _assert_path_chain_safe(candidate, root)
    final_root_snapshot = _snapshot_info_root(config.info_root)
    if final_root_snapshot != root_snapshot:
        raise ContextHubValidationError(
            "A raiz info do Context Hub foi alterada durante a operacao."
        )
    return ContextHubPaths(
        client_id=client,
        info_root=root,
        tenant_dir=tenant,
        vault_dir=vault,
        generated_dir=vault / "70_Gerado",
        curated_dir=vault / "80_Curadoria",
        internal_dir=internal,
        db_path=internal / "context_hub.db",
        staging_dir=internal / "staging",
        generations_dir=internal / "generations",
        lock_path=internal / "operation.lock",
        journal_path=internal / "publish-journal.json",
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        _replace_with_retry(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _replace_with_retry(source: Path | str, target: Path | str, *, attempts: int = 12) -> None:
    """Preserve atomic replacement while tolerating short Windows file locks."""

    maximum_attempts = max(1, int(attempts))
    for attempt in range(maximum_attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 >= maximum_attempts:
                raise
            time.sleep(min(0.25, 0.02 * (2 ** min(attempt, 4))))


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


@contextlib.contextmanager
def _connect(paths: ContextHubPaths) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(paths.db_path, timeout=20, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=20000")
    try:
        yield connection
    finally:
        connection.close()


def _initialize_database(paths: ContextHubPaths, surface: str) -> None:
    with _connect(paths) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_hub_generations (
                generation_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('staging','validating','ready','active','superseded','failed')),
                source_hash TEXT NOT NULL,
                source_version TEXT NOT NULL DEFAULT '',
                surface TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'manual_admin',
                base_active_generation_id TEXT,
                rollback_of TEXT,
                findings_json TEXT NOT NULL DEFAULT '[]',
                stats_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                validated_at TEXT,
                published_at TEXT,
                superseded_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_context_hub_generations_status_created
                ON context_hub_generations(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_context_hub_generations_source_hash
                ON context_hub_generations(source_hash);

            CREATE TABLE IF NOT EXISTS context_hub_documents (
                generation_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                module TEXT NOT NULL DEFAULT '',
                surface TEXT NOT NULL,
                truth_class TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                source_version TEXT NOT NULL DEFAULT '',
                source_hash TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source_refs_json TEXT NOT NULL DEFAULT '[]',
                content TEXT NOT NULL,
                managed INTEGER NOT NULL CHECK(managed IN (0,1)),
                PRIMARY KEY (generation_id, doc_id),
                UNIQUE (generation_id, relative_path),
                FOREIGN KEY (generation_id) REFERENCES context_hub_generations(generation_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_context_hub_documents_generation_kind
                ON context_hub_documents(generation_id, kind, module);

            CREATE TABLE IF NOT EXISTS context_hub_chunks (
                generation_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (generation_id, chunk_id),
                UNIQUE (generation_id, doc_id, ordinal),
                FOREIGN KEY (generation_id, doc_id)
                    REFERENCES context_hub_documents(generation_id, doc_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_context_hub_chunks_generation_doc
                ON context_hub_chunks(generation_id, doc_id, ordinal);

            CREATE TABLE IF NOT EXISTS context_hub_active_generation (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                generation_id TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (generation_id) REFERENCES context_hub_generations(generation_id)
            );

            CREATE TABLE IF NOT EXISTS context_hub_settings (
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
                auto_publish_enabled INTEGER NOT NULL CHECK(auto_publish_enabled IN (0,1)),
                watch_enabled INTEGER NOT NULL CHECK(watch_enabled IN (0,1)),
                paused INTEGER NOT NULL CHECK(paused IN (0,1)),
                debounce_seconds INTEGER NOT NULL,
                retention_generations INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        now = _utc_now()
        connection.execute(
            """
            INSERT OR IGNORE INTO context_hub_active_generation(singleton_id, generation_id, version, updated_at)
            VALUES (1, NULL, 0, ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO context_hub_settings(
                singleton_id, auto_publish_enabled, watch_enabled, paused,
                debounce_seconds, retention_generations, updated_at
            ) VALUES (1, 1, ?, 0, 10, 5, ?)
            """,
            (1 if surface == "development" else 0, now),
        )


def bootstrap_context_hub(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    """Create the persistent vault skeleton and private state for one tenant."""

    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    paths.info_root.mkdir(parents=True, exist_ok=True)
    _assert_path_chain_safe(paths.tenant_dir, paths.info_root)
    paths.tenant_dir.mkdir(parents=True, exist_ok=True)
    for relative in VAULT_DIRECTORIES:
        target = paths.vault_dir / relative
        _assert_path_chain_safe(target, paths.info_root)
        target.mkdir(parents=True, exist_ok=True)
    obsidian = paths.vault_dir / ".obsidian"
    if not obsidian.exists():
        obsidian.mkdir(parents=True, exist_ok=False)
        _write_json_atomic(obsidian / "app.json", {"alwaysUpdateLinks": True})
        _write_json_atomic(obsidian / "community-plugins.json", [])
    elif not obsidian.is_dir():
        raise ContextHubValidationError("Configuracao .obsidian invalida para o Context Hub.")
    _assert_path_chain_safe(obsidian, paths.info_root)
    paths.staging_dir.mkdir(parents=True, exist_ok=True)
    paths.generations_dir.mkdir(parents=True, exist_ok=True)
    _initialize_database(paths, config.surface)
    # Recovery mutates only swap artefacts.  Never race it with a live publish.
    if paths.journal_path.exists() and not paths.lock_path.exists():
        with _tenant_thread_lock(paths):
            try:
                with _exclusive_file_lock(paths, timeout=0.0):
                    _recover_publish_journal(paths)
            except ContextHubConflictError:
                pass
    return {
        "success": True,
        "client_id": paths.client_id,
        "surface": config.surface,
        "initialized": True,
    }


def _tenant_thread_lock(paths: ContextHubPaths) -> threading.RLock:
    key = str(paths.internal_dir).lower()
    with _TENANT_LOCKS_GUARD:
        return _TENANT_LOCKS.setdefault(key, threading.RLock())


def _read_lock_owner(lock_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _pid_is_running(pid: object) -> bool:
    try:
        normalized = int(pid)
    except (TypeError, ValueError):
        return False
    if normalized <= 0:
        return False
    if normalized == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            synchronize = 0x00100000
            wait_timeout = 0x00000102
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, normalized)
            if not handle:
                return ctypes.get_last_error() == 5
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(normalized, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_owned_lock(lock_path: Path, token: str) -> bool:
    owner = _read_lock_owner(lock_path)
    if not token or str(owner.get("token") or "") != token:
        return False
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextlib.contextmanager
def _exclusive_file_lock(paths: ContextHubPaths, *, timeout: float = 8.0) -> Iterator[None]:
    deadline = time.monotonic() + max(0.0, timeout)
    owner_token = uuid.uuid4().hex
    paths.internal_dir.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(paths.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(
                    descriptor,
                    _json_canonical(
                        {"pid": os.getpid(), "token": owner_token, "created_at": _utc_now()}
                    ).encode("utf-8"),
                )
            finally:
                os.close(descriptor)
            break
        except FileExistsError:
            owner = _read_lock_owner(paths.lock_path)
            try:
                stale = (time.time() - paths.lock_path.stat().st_mtime) > 300
            except OSError:
                stale = False
            stale_token = str(owner.get("token") or "")
            if stale and stale_token and not _pid_is_running(owner.get("pid")):
                _remove_owned_lock(paths.lock_path, stale_token)
                continue
            if time.monotonic() >= deadline:
                raise ContextHubConflictError("Outra operacao do Context Hub esta em andamento.")
            time.sleep(0.05)
    try:
        yield
    finally:
        _remove_owned_lock(paths.lock_path, owner_token)


def _safe_remove_tree(path: Path, root: Path) -> None:
    if not path.exists():
        return
    _assert_path_chain_safe(path, root)
    shutil.rmtree(path)


def _recover_publish_journal(paths: ContextHubPaths) -> None:
    """Recover a directory swap interrupted before the database CAS completed."""

    if not paths.journal_path.is_file():
        return
    try:
        journal = json.loads(paths.journal_path.read_text(encoding="utf-8"))
    except Exception:
        # An unreadable journal must fail closed; leave it for an administrator.
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    generation_id = str(journal.get("generation_id") or "")
    if not re.fullmatch(r"[a-f0-9]{32}", generation_id):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    temporary = paths.vault_dir / f".context_hub_publish_{generation_id}"
    backup = paths.vault_dir / f".context_hub_backup_{generation_id}"
    state = str(journal.get("state") or "")
    if state not in {"prepared", "old_moved", "new_active"}:
        raise ContextHubValidationError("Estado do journal de publicacao invalido; publicacao bloqueada.")
    previous_generation_id = str(journal.get("previous_generation_id") or "")
    if previous_generation_id and not re.fullmatch(r"[a-f0-9]{32}", previous_generation_id):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    raw_had_previous = journal.get("had_previous")
    if raw_had_previous is not None and not isinstance(raw_had_previous, bool):
        raise ContextHubValidationError("Journal de publicacao invalido; publicacao bloqueada.")
    active_id = None
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT generation_id FROM context_hub_active_generation WHERE singleton_id=1"
        ).fetchone()
        active_id = str(row["generation_id"] or "") if row else ""
    if previous_generation_id and active_id not in {previous_generation_id, generation_id}:
        raise ContextHubValidationError("Geracao ativa divergiu do journal; publicacao bloqueada.")
    had_previous = (
        raw_had_previous
        if isinstance(raw_had_previous, bool)
        else bool(previous_generation_id or (active_id and active_id != generation_id) or backup.exists())
    )
    if state == "new_active" and active_id == generation_id:
        _safe_remove_tree(backup, paths.vault_dir)
        _safe_remove_tree(temporary, paths.vault_dir)
        paths.journal_path.unlink(missing_ok=True)
        return

    # Antes do CAS do banco, qualquer arvore nova deve ser abortada e a
    # geracao indicada pelo ponteiro ativo deve continuar visivel.  O estado
    # `prepared` tambem pode ter backup: existe uma janela entre mover a arvore
    # antiga e persistir `old_moved`.
    if backup.exists():
        if paths.generated_dir.exists():
            _safe_remove_tree(paths.generated_dir, paths.vault_dir)
        _replace_with_retry(backup, paths.generated_dir)
    elif had_previous:
        if state != "prepared" or not paths.generated_dir.exists():
            raise ContextHubValidationError(
                "Backup da geracao ativa indisponivel; recuperacao bloqueada."
            )
        # prepared + arvore presente + sem backup: o movimento ainda nao
        # aconteceu; a arvore atual ja e a anterior e deve ser preservada.
    elif paths.generated_dir.exists():
        # Primeira publicacao interrompida antes do CAS: nao existe geracao
        # anterior no banco, portanto uma arvore nova parcial nao pode ficar
        # exposta como ativa.
        _safe_remove_tree(paths.generated_dir, paths.vault_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)


def _finding(
    code: str,
    *,
    severity: str = "blocker",
    category: str = "validation",
    source_ref: str = "",
    count: int = 1,
) -> dict[str, Any]:
    # Never accept a message or match value here: findings are safe metadata only.
    payload: dict[str, Any] = {
        "code": re.sub(r"[^a-z0-9_.-]+", "_", str(code or "finding").lower())[:80],
        "severity": str(severity or "blocker").lower()[:20],
        "category": re.sub(r"[^a-z0-9_.-]+", "_", str(category or "validation").lower())[:80],
        "count": max(1, int(count or 1)),
    }
    if source_ref:
        safe_ref = str(source_ref).replace("\\", "/")
        # A source reference is useful for remediation, but it is untrusted
        # metadata too.  Never copy a filename/path containing PII or a secret
        # into a finding: findings are persisted and may also reach logs/UI.
        if (
            len(safe_ref) <= 300
            and not any(character in safe_ref for character in "\r\n\x00")
            and ".." not in Path(safe_ref).parts
            and not Path(safe_ref).is_absolute()
            and not _dlp_categories(safe_ref)
        ):
            payload["source_ref"] = safe_ref
    return payload


def _sanitize_inventory_findings(findings: object) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    if not isinstance(findings, list):
        return sanitized
    for item in findings:
        if not isinstance(item, Mapping):
            continue
        sanitized.append(
            _finding(
                str(item.get("code") or item.get("category") or "inventory_finding"),
                severity="blocker" if item.get("blocking") is True else str(item.get("severity") or "warning"),
                category=str(item.get("category") or "inventory"),
                source_ref=str(item.get("source_ref") or item.get("path") or ""),
                count=int(item.get("count") or 1),
            )
        )
    return sanitized


def _has_blocker(findings: Sequence[Mapping[str, Any]]) -> bool:
    return any(str(item.get("severity") or "").lower() in {"blocker", "critical", "error"} for item in findings)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key).lower() not in _VOLATILE_INVENTORY_KEYS
        }
    if isinstance(value, (list, tuple)):
        normalized = [_strip_volatile(item) for item in value]
        if all(isinstance(item, Mapping) and item.get("id") for item in normalized):
            normalized.sort(key=lambda item: str(item.get("id")))
        return normalized
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _inventory_source_hash(
    inventory: Mapping[str, Any],
    curated_hashes: Sequence[tuple[str, str]],
    bundle_hashes: Sequence[tuple[str, str]],
) -> str:
    payload = {
        "schema_version": CONTEXT_HUB_SCHEMA_VERSION,
        "source_version": inventory.get("source_version") or "",
        "entities": _strip_volatile(inventory.get("entities") or []),
        "findings": _strip_volatile(_sanitize_inventory_findings(inventory.get("findings"))),
        "curated": sorted(curated_hashes),
        "bundle": sorted(bundle_hashes),
    }
    return _sha256_text(_json_canonical(payload))


def _load_inventory_adapter() -> Any:
    """Import inventory lazily so a partial deployment still imports safely."""

    return importlib.import_module("backend.services.context_hub_inventory")


def _build_inventory(config: ContextHubRuntimeConfig, client_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        adapter = _load_inventory_adapter()
        builder = getattr(adapter, "build_context_inventory", None)
        if not callable(builder):
            raise AttributeError("build_context_inventory")
    except (ImportError, AttributeError):
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_adapter_unavailable", category="inventory")
        ])
    try:
        inventory = builder(
            config.base_dir,
            config.info_root,
            client_id,
            config.surface,
        )
    except Exception:
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_build_failed", category="inventory")
        ])
    if not isinstance(inventory, Mapping) or not isinstance(inventory.get("entities"), list):
        return ({"entities": [], "findings": [], "source_version": "", "stats": {}}, [
            _finding("inventory_contract_invalid", category="inventory")
        ])
    return dict(inventory), []


def _runtime_source_version(config: ContextHubRuntimeConfig) -> str:
    environment_version = str(os.getenv("JK_APP_VERSION") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", environment_version):
        return environment_version
    package_path = config.base_dir / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        package_version = str(package.get("version") or "").strip() if isinstance(package, dict) else ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        package_version = ""
    return package_version if re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", package_version) else "unknown"


_DLP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("email", re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)),
    (
        "credential",
        re.compile(
            r"(?ix)\b(?:"
            r"access[_ -]?token|refresh[_ -]?token|oauth[_ -]?token|auth[_ -]?token|"
            r"token|client[_ -]?secret|api[_ -]?key|password|senha"
            r")\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?!redacted\b|redigido\b|masked\b|none\b|null\b|false\b|\*{3,}|<[^>]+>)"
            r"[^\s\"'<>]{6,}"
            r"|\b(?:authorization\b[\"']?\s*(?:=|:)\s*[\"']?)?"
            r"bearer\s+(?!redacted\b|redigido\b|masked\b|\*{3,}|<[^>]+>)"
            r"[A-Za-z0-9._~+/=-]{12,}"
            r"|\boauth\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?!redacted\b|redigido\b|masked\b|none\b|null\b|false\b|\*{3,}|<[^>]+>)"
            r"[^\s\"'<>]{6,}"
        ),
    ),
    ("buyer_data", re.compile(r"(?i)\b(?:comprador|buyer|recipient|destinatario)\b\s*(?:=|:)\s*[^\s#][^\n]{2,}")),
    (
        "address",
        re.compile(
            r"(?ix)\b(?:"
            r"(?:endereco|logradouro)\s*(?:=|:)\s*[^\s#][^\n]{3,}"
            r"|(?:rua|avenida|av\.)\s+[A-Z0-9À-ÿ .'-]{2,60}(?:,\s*|\s+)\d{1,6}\b"
            r"|cep\s*(?:=|:)?\s*\d{5}-?\d{3}\b"
            r")"
        ),
    ),
    (
        "phone",
        # Require an explicit DDD/format separator.  Plain 10/11 digit values
        # are common SKU/OEM identifiers and must not be classified as phones.
        re.compile(
            r"(?<!\d)(?:(?:\+?55[\s.-]+)?\([1-9]\d\)[\s.-]*9?\d{4}[\s.-]+\d{4}"
            r"|(?:\+?55[\s.-]+)?[1-9]\d[\s.-]+9?\d{4}[\s.-]+\d{4}"
            # Compact numbers are ambiguous with SKU/OEM identifiers, so they
            # are PII only when an explicit phone field labels the value.
            r"|(?i:\b(?:telefone|phone|celular|whatsapp|fone)\b[\"']?\s*(?:=|:)\s*[\"']?"
            r"(?:\+?55)?[1-9]\d9?\d{8}))(?!\d)"
        ),
    ),
)


def _valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for position in (9, 10):
        total = sum(int(digits[index]) * ((position + 1) - index) for index in range(position))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if check != int(digits[position]):
            return False
    return True


def _valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for position, weights in (
        (12, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
        (13, (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)),
    ):
        total = sum(int(digits[index]) * weights[index] for index in range(position))
        remainder = total % 11
        check = 0 if remainder < 2 else 11 - remainder
        if check != int(digits[position]):
            return False
    return True


def _dlp_categories(content: str) -> set[str]:
    categories = {category for category, pattern in _DLP_PATTERNS if pattern.search(content)}
    # A bare 11/14 digit value can legitimately be an OEM/SKU.  Accept only a
    # labelled compact document number or the conventional punctuated form.
    document_numbers = re.findall(
        r"(?ix)(?:"
        r"\b(?:cpf|cnpj)\b[\"']?\s*(?:=|:)\s*[\"']?(\d{14}|\d{11})(?!\d)"
        r"|(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})(?!\d)"
        r")",
        content,
    )
    for compact, formatted in document_numbers:
        raw = compact or formatted
        digits = re.sub(r"\D", "", raw)
        if _valid_cpf(digits):
            categories.add("cpf")
        elif _valid_cnpj(digits):
            categories.add("cnpj")
    return categories


def scan_dlp(content: object, *, source_ref: str = "") -> list[dict[str, Any]]:
    """Return category-only blockers; never return a match or excerpt."""

    text = str(content or "")
    return [
        _finding("dlp_blocked", category=category, source_ref=source_ref)
        for category in sorted(_dlp_categories(text))
    ]


def _dlp_document_text(metadata: Mapping[str, Any], body: str) -> str:
    """Build a complete, deterministic DLP surface for body and metadata.

    IDs, source references and nested/custom frontmatter are untrusted inputs,
    just like the Markdown body.  Flattening labelled values also lets the DLP
    distinguish a compact phone field from a numeric SKU/OEM identifier.
    """

    surface = [str(body or "")]
    visited: set[int] = set()

    def append_value(value: Any, label: str = "") -> None:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for raw_key in sorted(value, key=lambda item: str(item)):
                key = str(raw_key)
                surface.append(key)
                append_value(value[raw_key], key)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            items = value
            if isinstance(value, (set, frozenset)):
                items = sorted(value, key=lambda item: str(item))
            for item in items:
                append_value(item, label)
            return
        text = str(value or "")
        surface.append(f"{label}: {text}" if label else text)

    append_value(metadata)
    return "\n".join(surface)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n") and not content.startswith("---\r\n"):
        return {}, content
    lines = content.splitlines()
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return {}, content
    raw = "\n".join(lines[1:closing])
    try:
        metadata = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, content
    if not isinstance(metadata, dict):
        return {}, content
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return {str(key): value for key, value in metadata.items()}, body


def _dump_frontmatter(metadata: Mapping[str, Any], body: str) -> str:
    ordered = {key: metadata[key] for key in FRONTMATTER_REQUIRED}
    extras = {str(key): value for key, value in metadata.items() if str(key) not in ordered}
    ordered.update(dict(sorted(extras.items())))
    yaml_text = yaml.safe_dump(
        ordered,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n{body.rstrip()}\n"


def _safe_relative_markdown_path(raw_path: object) -> Path:
    value = str(raw_path or "").strip().replace("\\", "/").lstrip("/")
    relative = Path(value)
    if relative.suffix.lower() != ".md" or relative.is_absolute() or ".." in relative.parts:
        raise ContextHubValidationError("O inventario produziu um caminho Markdown invalido.")
    if not relative.parts or relative.parts[0].lower() != "70_gerado":
        relative = Path("70_Gerado") / relative
    if relative.parts[0] != "70_Gerado":
        relative = Path("70_Gerado") / Path(*relative.parts[1:])
    return relative


def _safe_identifier(value: object, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= 300 and not any(character in normalized for character in "\r\n\x00"):
        return normalized
    return fallback


def _normalize_source_refs(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    refs: list[str] = []
    for item in values:
        ref = str(item or "").strip().replace("\\", "/")
        if ref and len(ref) <= 300 and ".." not in Path(ref).parts and not Path(ref).is_absolute():
            refs.append(ref)
    return sorted(set(refs))


def _entity_index(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for entity in inventory.get("entities") or []:
        if isinstance(entity, Mapping) and entity.get("id"):
            result[str(entity["id"])] = entity
    return result


def _normalize_generated_note(
    relative_path: Path,
    raw_content: str,
    *,
    inventory: Mapping[str, Any],
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> tuple[dict[str, Any], str, str]:
    raw_metadata, body = _parse_frontmatter(raw_content.replace("\r\n", "\n"))
    entities = _entity_index(inventory)
    entity_id = str(raw_metadata.get("id") or "")
    entity = entities.get(entity_id, {})
    fallback_id = "jk:generated:" + re.sub(r"[^a-z0-9]+", "-", relative_path.with_suffix("").as_posix().lower()).strip("-")
    entity_id = _safe_identifier(entity_id or entity.get("id"), fallback=fallback_id)
    title = _safe_identifier(raw_metadata.get("title") or entity.get("title"), fallback=relative_path.stem.replace("_", " "))
    source_refs = _normalize_source_refs(raw_metadata.get("source_refs") or entity.get("source_refs"))
    body = body.strip() or f"# {title}\n"
    source_hash = str(raw_metadata.get("source_hash") or entity.get("source_hash") or _sha256_text(body))
    if not re.fullmatch(r"[a-fA-F0-9]{64}", source_hash):
        source_hash = _sha256_text(_json_canonical(_strip_volatile(entity)) + "\n" + body)
    metadata: dict[str, Any] = {
        "id": entity_id,
        "type": _safe_identifier(raw_metadata.get("type") or entity.get("kind"), fallback="generated"),
        "managed": True,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": f"tenant:{client_id}",
        "sensitivity": _safe_identifier(raw_metadata.get("sensitivity") or entity.get("sensitivity"), fallback="internal"),
        "truth_class": _safe_identifier(raw_metadata.get("truth_class") or entity.get("truth_class"), fallback="generated_verified"),
        "required_permissions": ["full"],
        "surface": surface,
        "source_version": source_version,
        "source_refs": source_refs,
        "source_hash": source_hash.lower(),
        "generated_at": generated_at,
        "title": title,
        "module": _safe_identifier(raw_metadata.get("module") or entity.get("domain"), fallback=""),
    }
    return metadata, _dump_frontmatter(metadata, body), body


def _fallback_render_inventory(inventory: Mapping[str, Any]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for entity in inventory.get("entities") or []:
        if not isinstance(entity, Mapping):
            continue
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            continue
        kind = re.sub(r"[^a-z0-9]+", "-", str(entity.get("kind") or "entity").lower()).strip("-") or "entity"
        slug = re.sub(r"[^a-z0-9]+", "-", entity_id.lower()).strip("-")[:120]
        title = str(entity.get("title") or entity_id)
        body = [f"# {title}", "", f"Identificador: `{entity_id}`."]
        rendered[f"Contratos/{kind}/{slug}.md"] = "\n".join(body) + "\n"
    return rendered


def _render_inventory(inventory: Mapping[str, Any]) -> dict[str, str]:
    try:
        adapter = _load_inventory_adapter()
        renderer = getattr(adapter, "render_context_inventory_markdown", None)
    except ImportError:
        renderer = None
    if callable(renderer):
        rendered = renderer(dict(inventory))
        if isinstance(rendered, Mapping):
            return {str(path): str(content) for path, content in rendered.items()}
    return _fallback_render_inventory(inventory)


def _collect_curated_notes(paths: ContextHubPaths) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    documents: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    hashes: list[tuple[str, str]] = []
    if not paths.curated_dir.exists():
        return documents, findings, hashes
    try:
        _assert_path_chain_safe(paths.curated_dir, paths.info_root)
    except ContextHubValidationError:
        return documents, [_finding("unsafe_curated_root", category="path")], hashes
    for candidate in sorted(paths.curated_dir.rglob("*.md"), key=lambda item: item.as_posix().lower()):
        try:
            _assert_path_chain_safe(candidate, paths.info_root)
            resolved = candidate.resolve()
            if not _is_relative_to(resolved, paths.curated_dir.resolve()):
                raise ContextHubValidationError("Nota fora de 80_Curadoria.")
            if candidate.stat().st_size > 1_000_000:
                findings.append(_finding("curated_note_too_large", category="curation", source_ref=candidate.name))
                continue
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ContextHubValidationError):
            findings.append(_finding("curated_note_unreadable", category="curation", source_ref=candidate.name))
            continue
        relative = candidate.relative_to(paths.vault_dir).as_posix()
        metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        status = str(metadata.get("status") or "draft").strip().lower()
        ai_usage = str(metadata.get("ai_usage") or "denied").strip().lower()
        if status != "published" or ai_usage != "allowed":
            continue
        hashes.append((relative, _sha256_text(content)))
        missing = [field for field in FRONTMATTER_REQUIRED if field not in metadata]
        if missing:
            findings.append(_finding("curated_frontmatter_incomplete", category="curation", source_ref=relative, count=len(missing)))
            continue
        if metadata.get("managed") not in {False, "false", 0}:
            findings.append(_finding("curated_note_managed_invalid", category="curation", source_ref=relative))
            continue
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative)
        if dlp:
            findings.extend(dlp)
            continue
        doc_id = _safe_identifier(metadata.get("id"), fallback="")
        if not isinstance(metadata.get("id"), str) or not doc_id:
            findings.append(_finding("curated_id_invalid", category="curation", source_ref=relative))
            continue
        metadata = dict(metadata)
        metadata["required_permissions"] = ["full"]
        metadata["tenant_scope"] = f"tenant:{paths.client_id}"
        metadata["source_refs"] = _normalize_source_refs(metadata.get("source_refs"))
        metadata["source_hash"] = _sha256_text(body)
        documents.append(
            {
                "metadata": metadata,
                "relative_path": relative,
                "content": _dump_frontmatter({key: metadata[key] for key in FRONTMATTER_REQUIRED} | {
                    "title": str(metadata.get("title") or candidate.stem),
                    "module": str(metadata.get("module") or "curadoria"),
                }, body),
                "body": body,
            }
        )
    return documents, findings, hashes


def _load_context_bundle(
    config: ContextHubRuntimeConfig,
    *,
    client_id: str,
    expected_source_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str]]]:
    """Load only Markdown files authorized by the root bundle manifest."""

    manifest_path = config.base_dir / "context-bundle-manifest.json"
    knowledge_root = config.base_dir / "docs" / "knowledge"
    if not manifest_path.is_file():
        return [], [_finding("context_bundle_manifest_missing", category="bundle")], []
    try:
        _assert_path_chain_safe(manifest_path, config.base_dir)
        if manifest_path.stat().st_size > 1_000_000:
            raise ValueError("manifest_size")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ContextHubValidationError):
        return [], [_finding("context_bundle_manifest_invalid", category="bundle")], []
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "source_version", "files"}:
        return [], [_finding("context_bundle_manifest_contract_invalid", category="bundle")], []
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        return [], [_finding("context_bundle_manifest_schema_unsupported", category="bundle")], []
    if not manifest["files"]:
        return [], [_finding("context_bundle_manifest_empty", category="bundle")], []
    manifest_version = str(manifest.get("source_version") or "").strip()
    if manifest_version != expected_source_version:
        return [], [_finding("context_bundle_version_mismatch", category="bundle")], []
    declared_paths = {
        str(entry.get("path") or "").replace("\\", "/")
        for entry in manifest["files"]
        if isinstance(entry, dict)
    }
    if not CONTEXT_BUNDLE_REQUIRED_PATHS.issubset(declared_paths):
        return [], [_finding("context_bundle_required_entries_missing", category="bundle")], []
    try:
        knowledge_resolved = knowledge_root.resolve(strict=True)
        _assert_path_chain_safe(knowledge_resolved, config.base_dir)
    except (OSError, ContextHubValidationError):
        return [], [_finding("context_bundle_root_missing", category="bundle")], []

    documents: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    hashes: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    total_size = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            findings.append(_finding("context_bundle_entry_invalid", category="bundle"))
            continue
        raw_path = str(entry.get("path") or "").replace("\\", "/")
        relative = Path(raw_path)
        expected_hash = str(entry.get("sha256") or "").lower()
        expected_size = entry.get("size")
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or len(relative.parts) < 3
            or tuple(part.lower() for part in relative.parts[:2]) != ("docs", "knowledge")
            or relative.suffix.lower() != ".md"
            or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
            or not isinstance(expected_size, int)
            or not 0 <= expected_size <= 1_000_000
        ):
            findings.append(_finding("context_bundle_entry_invalid", category="bundle"))
            continue
        path_key = relative.as_posix().lower()
        if path_key in seen_paths:
            findings.append(_finding("context_bundle_path_duplicate", category="bundle"))
            continue
        seen_paths.add(path_key)
        total_size += expected_size
        if total_size > 10_000_000:
            findings.append(_finding("context_bundle_size_exceeded", category="bundle"))
            break
        candidate = config.base_dir / relative
        try:
            _assert_path_chain_safe(candidate, config.base_dir)
            resolved = candidate.resolve(strict=True)
            if not _is_relative_to(resolved, knowledge_resolved):
                raise ContextHubValidationError("Arquivo fora do bundle.")
            stat = resolved.stat()
            if stat.st_size != expected_size:
                findings.append(_finding("context_bundle_size_mismatch", category="bundle", source_ref=relative.as_posix()))
                continue
            data = resolved.read_bytes()
            if _sha256_bytes(data) != expected_hash:
                findings.append(_finding("context_bundle_hash_mismatch", category="bundle", source_ref=relative.as_posix()))
                continue
            content = data.decode("utf-8")
        except (OSError, UnicodeError, ContextHubValidationError):
            findings.append(_finding("context_bundle_file_unreadable", category="bundle", source_ref=relative.as_posix()))
            continue
        raw_metadata, body = _parse_frontmatter(content.replace("\r\n", "\n"))
        title_match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
        title = title_match.group(1).strip() if title_match else relative.stem.replace("-", " ")
        doc_id = "jk:bundle:" + re.sub(
            r"[^a-z0-9]+", "-", "/".join(relative.parts[2:]).lower()
        ).strip("-")
        metadata = {
            "id": doc_id,
            "type": "technical_knowledge",
            "managed": True,
            "status": "published",
            "ai_usage": "allowed",
            "tenant_scope": f"tenant:{client_id}",
            "sensitivity": "internal",
            "truth_class": str(raw_metadata.get("truth_class") or "versioned_technical"),
            "required_permissions": ["full"],
            "surface": config.surface,
            "source_version": expected_source_version,
            "source_refs": [relative.as_posix()],
            "source_hash": expected_hash,
            "generated_at": "1970-01-01T00:00:00+00:00",
            "title": title,
            "module": "knowledge",
        }
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative.as_posix())
        if dlp:
            findings.extend(dlp)
            continue
        documents.append(
            {
                "metadata": metadata,
                "relative_path": "@bundle/" + "/".join(relative.parts[2:]),
                "content": _dump_frontmatter(metadata, body),
                "body": body,
            }
        )
        hashes.append((relative.as_posix(), expected_hash))
    return documents, findings, hashes


def _chunks_for_document(doc_id: str, body: str, *, maximum: int = 1800, overlap: int = 180) -> list[dict[str, Any]]:
    normalized = re.sub(r"\n{3,}", "\n\n", body.strip())
    if not normalized:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(normalized):
        end = min(len(normalized), cursor + maximum)
        if end < len(normalized):
            split = normalized.rfind("\n\n", cursor + maximum // 2, end)
            if split > cursor:
                end = split
        chunk = normalized[cursor:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        cursor = max(cursor + 1, end - overlap)
    result: list[dict[str, Any]] = []
    for ordinal, content in enumerate(chunks):
        content_hash = _sha256_text(content)
        result.append(
            {
                "chunk_id": _sha256_text(f"{doc_id}:{ordinal}:{content_hash}")[:32],
                "ordinal": ordinal,
                "content": content,
                "content_hash": content_hash,
            }
        )
    return result


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
    generation_id = uuid.uuid4().hex
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


def _prepare_documents(
    inventory: Mapping[str, Any],
    bundle_documents: Sequence[Mapping[str, Any]],
    curated_documents: Sequence[Mapping[str, Any]],
    *,
    client_id: str,
    surface: str,
    source_version: str,
    generated_at: str,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    managed_files: dict[str, str] = {}
    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    try:
        rendered = _render_inventory(inventory)
    except Exception:
        return [], {}, [_finding("inventory_render_failed", category="inventory")]

    for raw_path, raw_content in sorted(rendered.items(), key=lambda item: str(item[0]).lower()):
        try:
            relative = _safe_relative_markdown_path(raw_path)
            path_key = relative.as_posix().lower()
            if path_key in seen_paths:
                findings.append(_finding("generated_path_duplicate", category="inventory", source_ref=relative.as_posix()))
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
            findings.append(_finding("generated_path_invalid", category="inventory"))
            continue
        doc_id = str(metadata["id"])
        if doc_id in seen_ids:
            findings.append(_finding("generated_id_duplicate", category="inventory", source_ref=relative.as_posix()))
            continue
        dlp = scan_dlp(_dlp_document_text(metadata, body), source_ref=relative.as_posix())
        if dlp:
            findings.extend(dlp)
            continue
        seen_ids.add(doc_id)
        seen_paths.add(path_key)
        managed_files[relative.as_posix()] = content
        documents.append(
            {
                "metadata": metadata,
                "relative_path": relative.as_posix(),
                "content": content,
                "body": body,
            }
        )

    # SKU dossiers remain canonical JSON.  Their semantic entities are stored
    # only inside the generation DB so they are searchable without producing
    # hundreds of duplicate Markdown files in the Obsidian vault.
    for entity in inventory.get("entities") or []:
        if not isinstance(entity, Mapping) or str(entity.get("kind") or "").lower() != "sku":
            continue
        entity_id = str(entity.get("id") or "").strip()
        if not entity_id or entity_id in seen_ids:
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
            findings.extend(dlp)
            continue
        seen_ids.add(entity_id)
        seen_paths.add(relative.lower())
        documents.append(
            {
                "metadata": metadata,
                "relative_path": relative,
                "content": _dump_frontmatter(metadata, body),
                "body": body,
            }
        )

    for bundle in bundle_documents:
        metadata = dict(bundle.get("metadata") or {})
        relative = str(bundle.get("relative_path") or "")
        doc_id = str(metadata.get("id") or "")
        if not doc_id or doc_id in seen_ids:
            findings.append(_finding("bundle_id_duplicate", category="bundle", source_ref=relative))
            continue
        if relative.lower() in seen_paths:
            findings.append(_finding("bundle_path_duplicate", category="bundle", source_ref=relative))
            continue
        seen_ids.add(doc_id)
        seen_paths.add(relative.lower())
        documents.append(dict(bundle))

    for curated in curated_documents:
        metadata = dict(curated.get("metadata") or {})
        relative = str(curated.get("relative_path") or "")
        doc_id = str(metadata.get("id") or "")
        if not doc_id or doc_id in seen_ids:
            findings.append(_finding("curated_id_duplicate", category="curation", source_ref=relative))
            continue
        path_key = relative.lower()
        if path_key in seen_paths:
            findings.append(_finding("curated_path_duplicate", category="curation", source_ref=relative))
            continue
        seen_ids.add(doc_id)
        seen_paths.add(path_key)
        documents.append(dict(curated))
    return documents, managed_files, findings


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
) -> None:
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO context_hub_generations(
                    generation_id, status, source_hash, source_version, surface, reason,
                    base_active_generation_id, findings_json, stats_json, created_at, validated_at
                ) VALUES (?, 'staging', ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _utc_now(),
                ),
            )
            connection.execute(
                "UPDATE context_hub_generations SET status='validating' WHERE generation_id=?",
                (generation_id,),
            )
            for document in documents:
                metadata = dict(document.get("metadata") or {})
                content = str(document.get("content") or "")
                body = str(document.get("body") or "")
                doc_id = str(metadata["id"])
                relative_path = str(document.get("relative_path") or "")
                source_refs = _normalize_source_refs(metadata.get("source_refs"))
                connection.execute(
                    """
                    INSERT INTO context_hub_documents(
                        generation_id, doc_id, entity_id, relative_path, title, kind, module,
                        surface, truth_class, sensitivity, source_version, source_hash,
                        content_hash, source_refs_json, content, managed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation_id,
                        doc_id,
                        doc_id,
                        relative_path,
                        str(metadata.get("title") or doc_id),
                        str(metadata.get("type") or "document"),
                        str(metadata.get("module") or ""),
                        str(metadata.get("surface") or surface),
                        str(metadata.get("truth_class") or "generated_verified"),
                        str(metadata.get("sensitivity") or "internal"),
                        str(metadata.get("source_version") or source_version),
                        str(metadata.get("source_hash") or _sha256_text(body)),
                        _sha256_text(content),
                        _json_canonical(source_refs),
                        content,
                        1 if metadata.get("managed") is True else 0,
                    ),
                )
                for chunk in _chunks_for_document(doc_id, body):
                    connection.execute(
                        """
                        INSERT INTO context_hub_chunks(
                            generation_id, chunk_id, doc_id, ordinal, content, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            generation_id,
                            chunk["chunk_id"],
                            doc_id,
                            chunk["ordinal"],
                            chunk["content"],
                            chunk["content_hash"],
                        ),
                    )
            connection.execute(
                "UPDATE context_hub_generations SET status='ready', validated_at=? WHERE generation_id=?",
                (_utc_now(), generation_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _idempotent_generation(
    connection: sqlite3.Connection,
    source_hash: str,
) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM context_hub_generations
        WHERE source_hash=? AND status IN ('active','ready')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC, rowid DESC
        LIMIT 1
        """,
        (source_hash,),
    ).fetchone()


def rebuild_context(
    client_id: object,
    *,
    reason: str = "manual_admin",
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build, validate and optionally publish one isolated generation."""

    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, base_dir=config.base_dir, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    safe_reason = str(reason or "manual_admin").strip().lower()
    if not _SAFE_REASON_RE.fullmatch(safe_reason):
        raise ContextHubValidationError("Motivo de reconstrucao invalido.")
    thread_lock = _tenant_thread_lock(paths)
    with thread_lock, _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        with _connect(paths) as connection:
            base_active = _active_generation_id(connection)
            settings = _settings_from_row(connection.execute(
                "SELECT * FROM context_hub_settings WHERE singleton_id=1"
            ).fetchone(), config.surface)

        inventory, adapter_findings = _build_inventory(config, paths.client_id)
        source_version = str(inventory.get("source_version") or "unknown").strip()[:120]
        if not re.fullmatch(r"[A-Za-z0-9._+/-]{1,120}", source_version):
            source_version = "unknown"
        curated_documents, curated_findings, curated_hashes = _collect_curated_notes(paths)
        bundle_documents, bundle_findings, bundle_hashes = _load_context_bundle(
            config,
            client_id=paths.client_id,
            expected_source_version=source_version,
        )
        findings = (
            adapter_findings
            + _sanitize_inventory_findings(inventory.get("findings"))
            + curated_findings
            + bundle_findings
        )
        runtime_version = _runtime_source_version(config)
        if runtime_version != "unknown" and source_version != runtime_version:
            findings.append(_finding("inventory_source_version_mismatch", category="inventory"))
        source_hash = _inventory_source_hash(inventory, curated_hashes, bundle_hashes)
        stats = dict(inventory.get("stats") or {}) if isinstance(inventory.get("stats"), Mapping) else {}
        stats = {
            str(key): value
            for key, value in stats.items()
            if isinstance(value, (int, float, bool, type(None))) and len(str(key)) <= 80
        }

        # Non-blocking inventory warnings are expected (for example dynamic or
        # orphan frontend calls) and must not create duplicate generations when
        # the canonical source hash is unchanged.
        if not force and not _has_blocker(findings):
            with _connect(paths) as connection:
                existing = _idempotent_generation(connection, source_hash)
            if existing is not None:
                result = _public_generation(existing, include_details=True) | {
                    "success": True,
                    "idempotent": True,
                }
                if existing["status"] == "ready" and settings["auto_publish_enabled"] and not settings["paused"]:
                    published = _publish_generation_locked(paths, str(existing["generation_id"]))
                    published["idempotent"] = True
                    return published
                return result

        if _has_blocker(findings):
            return _insert_failed_generation(
                paths,
                source_hash=source_hash,
                source_version=source_version,
                surface=config.surface,
                reason=safe_reason,
                findings=findings,
                stats=stats,
                base_active_generation_id=base_active,
            )

        created_at = _utc_now()
        documents, managed_files, preparation_findings = _prepare_documents(
            inventory,
            bundle_documents,
            curated_documents,
            client_id=paths.client_id,
            surface=config.surface,
            source_version=source_version,
            generated_at=created_at,
        )
        reused_documents = _reuse_unchanged_documents(
            paths,
            base_active,
            documents,
            managed_files,
        )
        findings.extend(preparation_findings)
        if _has_blocker(findings):
            return _insert_failed_generation(
                paths,
                source_hash=source_hash,
                source_version=source_version,
                surface=config.surface,
                reason=safe_reason,
                findings=findings,
                stats=stats,
                base_active_generation_id=base_active,
            )
        if not documents:
            findings.append(_finding("generation_empty", category="inventory"))
            return _insert_failed_generation(
                paths,
                source_hash=source_hash,
                source_version=source_version,
                surface=config.surface,
                reason=safe_reason,
                findings=findings,
                stats=stats,
                base_active_generation_id=base_active,
            )

        generation_id = uuid.uuid4().hex
        try:
            _write_generation_snapshot(paths, generation_id, managed_files)
            stats.update(
                {
                    "documents": len(documents),
                    "managed_documents": len(managed_files),
                    "internal_documents": sum(
                        1 for item in documents if str(item.get("relative_path") or "").startswith(("@internal/", "@bundle/"))
                    ),
                    "curated_documents": sum(
                        1 for item in documents if str(item.get("relative_path") or "").startswith("80_Curadoria/")
                    ),
                    "reused_documents": reused_documents,
                }
            )
            _persist_ready_generation(
                paths,
                generation_id,
                source_hash=source_hash,
                source_version=source_version,
                surface=config.surface,
                reason=safe_reason,
                findings=findings,
                stats=stats,
                base_active_generation_id=base_active,
                created_at=created_at,
                documents=documents,
            )
        except Exception:
            _safe_remove_tree(paths.generations_dir / generation_id, paths.internal_dir)
            return _insert_failed_generation(
                paths,
                source_hash=source_hash,
                source_version=source_version,
                surface=config.surface,
                reason=safe_reason,
                findings=findings + [_finding("generation_persist_failed", category="storage")],
                stats=stats,
                base_active_generation_id=base_active,
            )

        if settings["auto_publish_enabled"] and not settings["paused"]:
            return _publish_generation_locked(paths, generation_id)
        with _connect(paths) as connection:
            row = connection.execute(
                "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
            ).fetchone()
        return _public_generation(row, include_details=True) | {"success": True, "idempotent": False}


def _copy_publish_candidate(paths: ContextHubPaths, generation_id: str) -> tuple[Path, Path]:
    snapshot = paths.generations_dir / generation_id / "70_Gerado"
    _assert_path_chain_safe(snapshot, paths.internal_dir)
    if not snapshot.is_dir():
        raise ContextHubValidationError("Snapshot gerenciado indisponivel para publicacao.")
    temporary = paths.vault_dir / f".context_hub_publish_{generation_id}"
    backup = paths.vault_dir / f".context_hub_backup_{generation_id}"
    _safe_remove_tree(temporary, paths.vault_dir)
    _safe_remove_tree(backup, paths.vault_dir)
    shutil.copytree(snapshot, temporary, copy_function=shutil.copy2)
    return temporary, backup


def _swap_generated_directory(
    paths: ContextHubPaths,
    generation_id: str,
    *,
    previous_generation_id: Optional[str],
) -> tuple[Path, Path]:
    temporary, backup = _copy_publish_candidate(paths, generation_id)
    had_previous = paths.generated_dir.exists()
    journal_base = {
        "generation_id": generation_id,
        "previous_generation_id": str(previous_generation_id or ""),
        "had_previous": had_previous,
        "created_at": _utc_now(),
    }
    _write_json_atomic(
        paths.journal_path,
        journal_base | {"state": "prepared"},
    )
    try:
        if paths.generated_dir.exists():
            _replace_with_retry(paths.generated_dir, backup)
        _write_json_atomic(
            paths.journal_path,
            journal_base | {"state": "old_moved"},
        )
        _replace_with_retry(temporary, paths.generated_dir)
        _write_json_atomic(
            paths.journal_path,
            journal_base | {"state": "new_active"},
        )
        return temporary, backup
    except Exception:
        if backup.exists():
            _safe_remove_tree(paths.generated_dir, paths.vault_dir)
            _replace_with_retry(backup, paths.generated_dir)
        _safe_remove_tree(temporary, paths.vault_dir)
        paths.journal_path.unlink(missing_ok=True)
        raise


def _restore_swapped_directory(paths: ContextHubPaths, temporary: Path, backup: Path) -> None:
    if backup.exists():
        _safe_remove_tree(paths.generated_dir, paths.vault_dir)
        _replace_with_retry(backup, paths.generated_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)


def _finalize_swapped_directory(paths: ContextHubPaths, temporary: Path, backup: Path) -> None:
    _safe_remove_tree(backup, paths.vault_dir)
    _safe_remove_tree(temporary, paths.vault_dir)
    paths.journal_path.unlink(missing_ok=True)


def _publish_generation_locked(
    paths: ContextHubPaths,
    generation_id: str,
    *,
    allow_superseded: bool = False,
) -> dict[str, Any]:
    with _connect(paths) as connection:
        target = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if target is None:
            raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
        current = _active_generation_id(connection)
    if current == generation_id:
        return _public_generation(target, include_details=True) | {"success": True, "idempotent": True}
    allowed = {"ready"} | ({"superseded"} if allow_superseded else set())
    if str(target["status"]) not in allowed:
        raise ContextHubValidationError("Somente uma geracao pronta pode ser publicada.")
    expected = current if allow_superseded else (str(target["base_active_generation_id"] or "") or None)
    if expected != current:
        raise ContextHubConflictError("A geracao ativa mudou; reconstrua antes de publicar.")

    temporary, backup = _swap_generated_directory(
        paths,
        generation_id,
        previous_generation_id=current,
    )
    now = _utc_now()
    try:
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            actual = _active_generation_id(connection)
            if actual != current:
                connection.rollback()
                raise ContextHubConflictError("A geracao ativa mudou durante a publicacao.")
            if current:
                connection.execute(
                    """
                    UPDATE context_hub_generations
                    SET status='superseded', superseded_at=?
                    WHERE generation_id=? AND status='active'
                    """,
                    (now, current),
                )
            connection.execute(
                """
                UPDATE context_hub_generations
                SET status='active', published_at=?, superseded_at=NULL
                WHERE generation_id=?
                """,
                (now, generation_id),
            )
            cursor = connection.execute(
                """
                UPDATE context_hub_active_generation
                SET generation_id=?, version=version+1, updated_at=?
                WHERE singleton_id=1 AND (generation_id IS ? OR generation_id=?)
                """,
                (generation_id, now, current, current),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise ContextHubConflictError("Falha no compare-and-swap da geracao ativa.")
            connection.commit()
    except Exception:
        _restore_swapped_directory(paths, temporary, backup)
        raise
    _finalize_swapped_directory(paths, temporary, backup)
    _prune_generations(paths)
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
    return _public_generation(row, include_details=True) | {"success": True, "idempotent": False}


def publish_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_id):
        raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        return _publish_generation_locked(paths, normalized_id)


def rollback_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_id):
        raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        _recover_publish_journal(paths)
        result = _publish_generation_locked(paths, normalized_id, allow_superseded=True)
        result["rollback"] = True
        return result


def _prune_generations(paths: ContextHubPaths) -> None:
    with _connect(paths) as connection:
        settings = _settings_from_row(connection.execute(
            "SELECT * FROM context_hub_settings WHERE singleton_id=1"
        ).fetchone(), "development")
        active = _active_generation_id(connection)
        published = connection.execute(
            """
            SELECT generation_id FROM context_hub_generations
            WHERE status IN ('active','superseded')
            ORDER BY COALESCE(published_at, created_at) DESC, rowid DESC
            """
        ).fetchall()
        protected: set[str] = {active} if active else set()
        published_ids = [str(row["generation_id"]) for row in published]
        if len(published_ids) > 1:
            protected.add(published_ids[1])  # explicit previous generation
        protected.update(published_ids[2 : 2 + int(settings["retention_generations"])])
        removable = [generation_id for generation_id in published_ids if generation_id not in protected]
        failed_or_ready = connection.execute(
            """
            SELECT generation_id FROM context_hub_generations
            WHERE status IN ('failed','ready') ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
        removable.extend(str(row["generation_id"]) for row in failed_or_ready[int(settings["retention_generations"]) :])
        if not removable:
            return
        connection.execute("BEGIN IMMEDIATE")
        for generation_id in sorted(set(removable)):
            connection.execute("DELETE FROM context_hub_generations WHERE generation_id=?", (generation_id,))
        connection.commit()
    for generation_id in sorted(set(removable)):
        _safe_remove_tree(paths.generations_dir / generation_id, paths.internal_dir)


def _settings_from_row(row: Optional[sqlite3.Row], surface: str) -> dict[str, Any]:
    if row is None:
        return {
            "auto_publish_enabled": True,
            "watch_enabled": surface == "development",
            "paused": False,
            "debounce_seconds": 10,
            "retention_generations": 5,
            "watcher_supported": surface == "development",
        }
    return {
        "auto_publish_enabled": bool(row["auto_publish_enabled"]),
        "watch_enabled": bool(row["watch_enabled"]),
        "paused": bool(row["paused"]),
        "debounce_seconds": int(row["debounce_seconds"]),
        "retention_generations": int(row["retention_generations"]),
        "watcher_supported": surface == "development",
        "updated_at": row["updated_at"],
    }


def get_settings(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    with _connect(paths) as connection:
        row = connection.execute("SELECT * FROM context_hub_settings WHERE singleton_id=1").fetchone()
    return _settings_from_row(row, config.surface)


def update_settings(
    client_id: object,
    *,
    auto_publish_enabled: Optional[bool] = None,
    paused: Optional[bool] = None,
    watch_enabled: Optional[bool] = None,
    debounce_seconds: Optional[int] = None,
    retention_generations: Optional[int] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    current = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
    if debounce_seconds is not None and not 1 <= int(debounce_seconds) <= 300:
        raise ContextHubValidationError("Debounce deve ficar entre 1 e 300 segundos.")
    if retention_generations is not None and not 1 <= int(retention_generations) <= 20:
        raise ContextHubValidationError("Retencao deve ficar entre 1 e 20 geracoes.")
    if watch_enabled is True and config.surface != "development":
        raise ContextHubValidationError("Watcher permanente so e aceito no checkout de desenvolvimento.")
    updated = {
        "auto_publish_enabled": current["auto_publish_enabled"] if auto_publish_enabled is None else bool(auto_publish_enabled),
        "watch_enabled": current["watch_enabled"] if watch_enabled is None else bool(watch_enabled),
        "paused": current["paused"] if paused is None else bool(paused),
        "debounce_seconds": current["debounce_seconds"] if debounce_seconds is None else int(debounce_seconds),
        "retention_generations": current["retention_generations"] if retention_generations is None else int(retention_generations),
    }
    with _tenant_thread_lock(paths), _exclusive_file_lock(paths):
        with _connect(paths) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE context_hub_settings SET
                    auto_publish_enabled=?, watch_enabled=?, paused=?, debounce_seconds=?,
                    retention_generations=?, updated_at=? WHERE singleton_id=1
                """,
                (
                    int(updated["auto_publish_enabled"]),
                    int(updated["watch_enabled"]),
                    int(updated["paused"]),
                    updated["debounce_seconds"],
                    updated["retention_generations"],
                    _utc_now(),
                ),
            )
            connection.commit()
    return get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)


def list_generations(
    client_id: object,
    *,
    limit: int = 50,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    safe_limit = max(1, min(int(limit or 50), 200))
    with _connect(paths) as connection:
        rows = connection.execute(
            "SELECT * FROM context_hub_generations ORDER BY created_at DESC, rowid DESC LIMIT ?", (safe_limit,)
        ).fetchall()
    return {"success": True, "client_id": paths.client_id, "generations": [_public_generation(row) for row in rows]}


def get_generation(
    client_id: object,
    generation_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    normalized_id = str(generation_id or "").strip().lower()
    with _connect(paths) as connection:
        row = connection.execute(
            "SELECT * FROM context_hub_generations WHERE generation_id=?", (normalized_id,)
        ).fetchone()
        if row is None:
            raise ContextHubNotFoundError("Geracao do Context Hub nao encontrada.")
        counts = connection.execute(
            """
            SELECT
                COUNT(DISTINCT d.doc_id) AS documents,
                COUNT(c.chunk_id) AS chunks
            FROM context_hub_documents d
            LEFT JOIN context_hub_chunks c
              ON c.generation_id=d.generation_id AND c.doc_id=d.doc_id
            WHERE d.generation_id=?
            """,
            (normalized_id,),
        ).fetchone()
    payload = _public_generation(row, include_details=True)
    payload["counts"] = {"documents": int(counts["documents"] or 0), "chunks": int(counts["chunks"] or 0)}
    return {"success": True, "client_id": paths.client_id, "generation": payload}


def _generation_document_hashes(
    connection: sqlite3.Connection,
    generation_id: Optional[str],
) -> dict[str, str]:
    if not generation_id:
        return {}
    return {
        str(row["doc_id"]): str(row["content_hash"])
        for row in connection.execute(
            "SELECT doc_id, content_hash FROM context_hub_documents WHERE generation_id=?",
            (generation_id,),
        ).fetchall()
    }


def get_status(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(info_root=info_root, surface=surface)
    bootstrap_context_hub(client_id, info_root=config.info_root, surface=config.surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    with _connect(paths) as connection:
        connection.execute("BEGIN")
        try:
            active_id = _active_generation_id(connection)
            active = connection.execute(
                "SELECT * FROM context_hub_generations WHERE generation_id=?", (active_id,)
            ).fetchone() if active_id else None
            latest = connection.execute(
                "SELECT * FROM context_hub_generations ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            diff_target_id = (
                str(latest["generation_id"])
                if latest and str(latest["status"]) in {"ready", "active", "superseded"}
                else active_id
            )
            active_hashes = _generation_document_hashes(connection, active_id)
            target_hashes = _generation_document_hashes(connection, diff_target_id)
            counts = connection.execute(
                """
                SELECT COUNT(DISTINCT d.doc_id) AS documents, COUNT(c.chunk_id) AS chunks
                FROM context_hub_documents d
                LEFT JOIN context_hub_chunks c
                  ON c.generation_id=d.generation_id AND c.doc_id=d.doc_id
                WHERE d.generation_id=?
                """,
                (active_id,),
            ).fetchone() if active_id else {"documents": 0, "chunks": 0}
            settings_row = connection.execute(
                "SELECT * FROM context_hub_settings WHERE singleton_id=1"
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    settings = _settings_from_row(settings_row, config.surface)
    watcher_key = str(paths.internal_dir).lower()
    with _WATCHERS_GUARD:
        watcher_running = watcher_key in _WATCHERS and _WATCHERS[watcher_key][0].is_alive()
    latest_public = _public_generation(latest, include_details=True) if latest else None
    active_ids = set(active_hashes)
    target_ids = set(target_hashes)
    diff_added = len(target_ids - active_ids)
    diff_removed = len(active_ids - target_ids)
    diff_updated = sum(
        1
        for doc_id in active_ids & target_ids
        if active_hashes[doc_id] != target_hashes[doc_id]
    )
    return {
        "success": True,
        "client_id": paths.client_id,
        "initialized": True,
        "surface": config.surface,
        "active_generation": _public_generation(active) if active else None,
        "latest_generation": latest_public,
        "source_version": str(active["source_version"] or "") if active else "",
        "counts": {"documents": int(counts["documents"] or 0), "chunks": int(counts["chunks"] or 0)},
        "settings": settings,
        "watcher_running": watcher_running,
        "blocked": bool(latest and latest["status"] == "failed"),
        "blockers": [
            {key: value for key, value in finding.items() if key != "source_ref"}
            for finding in (latest_public or {}).get("findings", [])
        ] if latest and latest["status"] == "failed" else [],
        "diff": {
            "has_changes": bool(latest and (not active or latest["source_hash"] != active["source_hash"])),
            "added": diff_added,
            "updated": diff_updated,
            "removed": diff_removed,
            "latest_generation_id": str(latest["generation_id"]) if latest else None,
            "latest_status": str(latest["status"]) if latest else None,
        },
    }


def _search_score(query_terms: Sequence[str], title: str, content: str, doc_id: str) -> float:
    title_lower = title.lower()
    content_lower = content.lower()
    id_lower = doc_id.lower()
    if not query_terms:
        return 1.0
    score = 0.0
    for term in query_terms:
        score += title_lower.count(term) * 5.0
        score += id_lower.count(term) * 4.0
        score += min(content_lower.count(term), 20) * 1.0
    return score


def _search_snippet(content: str, query_terms: Sequence[str], *, maximum: int = 320) -> str:
    normalized = re.sub(r"\s+", " ", content).strip()
    if len(normalized) <= maximum:
        return normalized
    lower = normalized.lower()
    positions = [lower.find(term) for term in query_terms if lower.find(term) >= 0]
    start = max(0, min(positions) - maximum // 3) if positions else 0
    end = min(len(normalized), start + maximum)
    snippet = normalized[start:end].strip()
    return ("..." if start else "") + snippet + ("..." if end < len(normalized) else "")


def search_context(
    client_id: object,
    query: object,
    filters: Optional[Mapping[str, Any]] = None,
    limit: int = 12,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    """Search only the generation selected by the tenant's active pointer."""

    paths = _tenant_paths(client_id, info_root=info_root)
    bootstrap_context_hub(paths.client_id, info_root=paths.info_root)
    safe_query = str(query or "").strip()[:500]
    safe_limit = max(1, min(int(limit or 12), 12))
    raw_filters = dict(filters or {}) if isinstance(filters, Mapping) else {}
    module_filter = str(raw_filters.get("module") or raw_filters.get("domain") or "").strip().lower()[:100]
    kind_filter = str(raw_filters.get("source_type") or raw_filters.get("kind") or "").strip().lower()[:100]
    surface_filter = str(raw_filters.get("environment") or raw_filters.get("surface") or "").strip().lower()[:100]
    ids_value = raw_filters.get("ids") or raw_filters.get("entity_ids") or []
    ids = {str(value).strip() for value in ids_value if str(value).strip()} if isinstance(ids_value, list) else set()
    query_terms = [term for term in re.findall(r"[\w:/.-]+", safe_query.lower(), re.UNICODE) if len(term) >= 2][:20]
    with _connect(paths) as connection:
        connection.execute("BEGIN")
        active_id = _active_generation_id(connection)
        if not active_id:
            connection.commit()
            return {
                "success": True,
                "query": safe_query,
                "generation_id": None,
                "generation": None,
                "results": [],
                "count": 0,
            }
        generation = connection.execute(
            "SELECT source_version FROM context_hub_generations WHERE generation_id=? AND status='active'",
            (active_id,),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT
                d.doc_id, d.relative_path, d.title, d.kind, d.module, d.surface,
                d.truth_class, d.source_version, d.source_hash, d.content_hash,
                d.source_refs_json, c.chunk_id, c.content
            FROM context_hub_chunks c
            JOIN context_hub_documents d
              ON d.generation_id=c.generation_id AND d.doc_id=c.doc_id
            WHERE c.generation_id=?
            """,
            (active_id,),
        ).fetchall()
        connection.commit()
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        if module_filter and module_filter not in str(row["module"] or "").lower():
            continue
        if kind_filter and kind_filter != str(row["kind"] or "").lower():
            continue
        if surface_filter and surface_filter != str(row["surface"] or "").lower():
            continue
        if ids and str(row["doc_id"]) not in ids:
            continue
        score = _search_score(query_terms, str(row["title"] or ""), str(row["content"] or ""), str(row["doc_id"] or ""))
        if query_terms and score <= 0:
            continue
        try:
            refs = json.loads(str(row["source_refs_json"] or "[]"))
        except json.JSONDecodeError:
            refs = []
        reference = str(refs[0]) if isinstance(refs, list) and refs else str(row["relative_path"] or "")
        result = {
            "doc_id": row["doc_id"],
            "chunk_id": row["chunk_id"],
            "snippet": _search_snippet(str(row["content"] or ""), query_terms),
            "score": round(score, 4),
            "reference": reference,
            "truth_class": row["truth_class"],
            "source_version": row["source_version"],
            "source_hash": row["source_hash"],
            "content_hash": row["content_hash"],
            "generation_id": active_id,
            # Compatibility aliases for the first UI/MCP draft.
            "version": row["source_version"],
            "hash": row["source_hash"],
            "generation": active_id,
            "type": row["kind"],
            "module": row["module"],
            "surface": row["surface"],
        }
        ranked.append((score, f"{row['doc_id']}:{row['chunk_id']}", result))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    results = [item[2] for item in ranked[:safe_limit]]
    return {
        "success": True,
        "query": safe_query,
        "generation_id": active_id,
        "generation": active_id,
        "source_version": str(generation["source_version"] or "") if generation else "",
        "results": results,
        "count": len(results),
    }


def _watch_roots(config: ContextHubRuntimeConfig, paths: ContextHubPaths) -> list[Path]:
    candidates = [
        config.base_dir / "backend",
        config.base_dir / "static",
        config.base_dir / "tests",
        config.base_dir / "electron_app" / "main",
        config.base_dir / "electron_app" / "scripts",
        config.base_dir / "electron_app" / "package.json",
        config.base_dir / "electron_app" / "installer-required-resources.json",
        config.base_dir / "docs" / "knowledge",
        config.base_dir / "package.json",
        config.base_dir / "context-bundle-manifest.json",
        paths.tenant_dir / "SKU",
        paths.curated_dir,
    ]
    allowed: list[Path] = []
    for candidate in candidates:
        authority = paths.info_root if _is_relative_to(candidate.absolute(), paths.info_root.absolute()) else config.base_dir
        try:
            _assert_path_chain_safe(candidate, authority)
        except ContextHubValidationError:
            continue
        if candidate.exists():
            allowed.append(candidate)
    return allowed


_WATCH_EXCLUDED_DIRS = {
    ".git",
    ".obsidian",
    "__pycache__",
    "build",
    "context_hub",
    "contextvault",
    "dist",
    "dist-client-setup",
    "logs",
    "node_modules",
    "test-results",
}


def _iter_watch_files(root: Path) -> Iterator[Path]:
    if root.is_file():
        yield root
        return
    for directory, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
        dir_names[:] = [
            name
            for name in dir_names
            if name.lower() not in _WATCH_EXCLUDED_DIRS
            and not name.lower().startswith(".venv")
            and not name.lower().startswith("dist-")
            and not name.lower().startswith(".context-hub")
        ]
        current = Path(directory)
        for file_name in file_names:
            yield current / file_name


def _watch_fingerprint(config: ContextHubRuntimeConfig, paths: ContextHubPaths) -> str:
    records: list[tuple[str, int, int]] = []
    accepted_suffixes = {".py", ".js", ".html", ".json", ".md", ".toml", ".yml", ".yaml"}
    for root in _watch_roots(config, paths):
        for candidate in _iter_watch_files(root):
            if not candidate.is_file() or candidate.suffix.lower() not in accepted_suffixes:
                continue
            authority = paths.info_root if _is_relative_to(candidate.absolute(), paths.info_root.absolute()) else config.base_dir
            try:
                _assert_path_chain_safe(candidate, authority)
                stat = candidate.stat()
            except (OSError, ContextHubValidationError):
                continue
            try:
                relative = candidate.relative_to(authority).as_posix()
            except ValueError:
                continue
            records.append((relative, int(stat.st_mtime_ns), int(stat.st_size)))
    return _sha256_text(_json_canonical(sorted(records)))


def scan_context_hub_changes(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    fingerprint = _watch_fingerprint(config, paths)
    key = str(paths.internal_dir).lower()
    with _WATCHERS_GUARD:
        previous = _WATCH_FINGERPRINTS.get(key)
        _WATCH_FINGERPRINTS[key] = fingerprint
    return {
        "success": True,
        "client_id": paths.client_id,
        "changed": previous is not None and previous != fingerprint,
        "initialized": previous is not None,
        "fingerprint": fingerprint,
    }


def _watch_loop(config: ContextHubRuntimeConfig, paths: ContextHubPaths, stop_event: threading.Event) -> None:
    last_change: Optional[float] = None
    while not stop_event.wait(1.0):
        try:
            settings = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
            if settings["paused"] or not settings["watch_enabled"]:
                last_change = None
                continue
            scan = scan_context_hub_changes(
                paths.client_id,
                base_dir=config.base_dir,
                info_root=config.info_root,
                surface=config.surface,
            )
            if scan["changed"]:
                last_change = time.monotonic()
            if last_change is not None and time.monotonic() - last_change >= settings["debounce_seconds"]:
                rebuild_context(
                    paths.client_id,
                    reason="watcher_change",
                    base_dir=config.base_dir,
                    info_root=config.info_root,
                    surface=config.surface,
                )
                last_change = None
        except ContextHubError:
            # Fail closed and try again; no source content or exception text is logged.
            last_change = None
        except Exception:
            last_change = None


def start_context_hub_watcher(
    client_id: object,
    *,
    base_dir: Optional[os.PathLike[str] | str] = None,
    info_root: Optional[os.PathLike[str] | str] = None,
    surface: Optional[str] = None,
) -> dict[str, Any]:
    config = _runtime_config(base_dir=base_dir, info_root=info_root, surface=surface)
    paths = _tenant_paths(client_id, info_root=config.info_root)
    bootstrap_context_hub(paths.client_id, base_dir=config.base_dir, info_root=config.info_root, surface=config.surface)
    if config.surface != "development":
        return {"success": False, "client_id": paths.client_id, "started": False, "reason": "watcher_not_supported"}
    settings = get_settings(paths.client_id, info_root=config.info_root, surface=config.surface)
    if not settings["watch_enabled"] or settings["paused"]:
        return {"success": False, "client_id": paths.client_id, "started": False, "reason": "watcher_disabled"}
    key = str(paths.internal_dir).lower()
    with _WATCHERS_GUARD:
        existing = _WATCHERS.get(key)
        if existing and existing[0].is_alive():
            return {"success": True, "client_id": paths.client_id, "started": False, "already_running": True}
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_watch_loop,
            args=(config, paths, stop_event),
            name=f"context-hub-{paths.client_id}",
            daemon=True,
        )
        _WATCHERS[key] = (thread, stop_event)
        _WATCH_FINGERPRINTS[key] = _watch_fingerprint(config, paths)
        thread.start()
    return {"success": True, "client_id": paths.client_id, "started": True}


def stop_context_hub_watcher(
    client_id: object,
    *,
    info_root: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    paths = _tenant_paths(client_id, info_root=info_root)
    key = str(paths.internal_dir).lower()
    with _WATCHERS_GUARD:
        watcher = _WATCHERS.pop(key, None)
        _WATCH_FINGERPRINTS.pop(key, None)
    if watcher:
        thread, event = watcher
        event.set()
        thread.join(timeout=3.0)
    return {"success": True, "client_id": paths.client_id, "stopped": bool(watcher)}


def stop_all_context_hub_watchers() -> None:
    with _WATCHERS_GUARD:
        watchers = list(_WATCHERS.values())
        _WATCHERS.clear()
        _WATCH_FINGERPRINTS.clear()
    for thread, event in watchers:
        event.set()
        thread.join(timeout=3.0)


__all__ = [
    "CONTEXT_HUB_SCHEMA_VERSION",
    "ContextHubConflictError",
    "ContextHubError",
    "ContextHubNotFoundError",
    "ContextHubValidationError",
    "bootstrap_context_hub",
    "configure_context_hub",
    "get_generation",
    "get_settings",
    "get_status",
    "list_generations",
    "publish_generation",
    "rebuild_context",
    "rollback_generation",
    "scan_context_hub_changes",
    "scan_dlp",
    "search_context",
    "start_context_hub_watcher",
    "stop_all_context_hub_watchers",
    "stop_context_hub_watcher",
    "update_settings",
]
