"""Optional JSON snapshots for Vendas; operational sources remain authoritative.

Revision UUIDs live outside the snapshots and survive process restarts. Pending
invalidation receipts fail closed if a process crashes before replacing a UUID.
No credentials, user sessions, photo bytes or Python objects are serialized.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Callable
import uuid


SCHEMA = 1
CALCULATION_VERSION = "vendas-json-v1"
CACHE_DIRECTORY = ".vendas_read_cache"
MAX_ENTRY_BYTES = 12 * 1024 * 1024
MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 128
LOCK_TIMEOUT_SECONDS = 0.25
NAMESPACES = frozenset({"resumo", "grafico"})
_UUID = re.compile(r"[0-9a-f]{32}\Z")
_SNAPSHOT = re.compile(r"[0-9a-f]{64}\.json\Z")
_LOCKS = tuple(threading.RLock() for _ in range(32))
_DISABLED: set[str] = set()


class CacheUnavailable(Exception):
    pass


def _canonical(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _directory(root: str) -> Path:
    directory = Path(root) / CACHE_DIRECTORY
    if _linked(directory):
        raise CacheUnavailable()
    directory.mkdir(parents=True, exist_ok=True)
    if _linked(directory) or _canonical(directory.parent) != root:
        raise CacheUnavailable()
    return directory


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _atomic_write(path: Path, content: bytes, validate: Callable[[], bool] = lambda: True) -> bool:
    descriptor, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if not validate():
            return False
        os.replace(temporary, path)
        return True
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _pending(directory: Path) -> list[Path]:
    return [entry for entry in directory.iterdir() if entry.name.startswith(".pending-")]


def _revision(directory: Path, name: str, *, create: bool) -> str:
    if str(directory) in _DISABLED or _pending(directory):
        raise CacheUnavailable()
    path = directory / name
    if _linked(path):
        raise CacheUnavailable()
    try:
        with path.open("rb") as stream:
            value = stream.read(33).decode("ascii")
    except FileNotFoundError:
        value = ""
    if not _UUID.fullmatch(value):
        if not create:
            raise CacheUnavailable()
        # Concurrent creators may conservatively invalidate each other. Every
        # reader/writer checks the final UUID again before publishing a hit.
        _atomic_write(path, uuid.uuid4().hex.encode("ascii"))
        return _revision(directory, name, create=False)
    return value


@dataclass(frozen=True)
class Generation:
    info_root: str
    tenant_root: str
    global_revision: str
    tenant_revision: str


def capture(info_root: str, tenant_root: str, *, create: bool = True) -> Generation | None:
    try:
        info = _canonical(info_root)
        tenant = _canonical(tenant_root)
        if os.path.commonpath([info, tenant]) != info or tenant == info:
            return None
        global_directory = _directory(info)
        tenant_directory = _directory(tenant)
        return Generation(info, tenant, _revision(global_directory, "global.revision", create=create),
                          _revision(tenant_directory, "tenant.revision", create=create))
    except (OSError, ValueError, UnicodeError, CacheUnavailable):
        return None


def current(generation: Generation) -> bool:
    return capture(generation.info_root, generation.tenant_root, create=False) == generation


def invalidate(root: str, *, global_scope: bool = False) -> bool:
    """A fresh UUID invalidates even snapshots belonging to an unseen tenant."""
    directory = None
    try:
        directory = _directory(_canonical(root))
        receipt = directory / (".pending-" + uuid.uuid4().hex)
        _atomic_write(receipt, b"invalidating")
        pending = _pending(directory)
        _atomic_write(directory / ("global.revision" if global_scope else "tenant.revision"), uuid.uuid4().hex.encode("ascii"))
        # These receipts all preceded this successful invalidation. A later
        # invalidation retains its own receipt or replaces the UUID again.
        for path in pending:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _DISABLED.discard(str(directory))
        return True
    except (OSError, ValueError, CacheUnavailable):
        if directory is not None:
            _DISABLED.add(str(directory))
        return False


@lru_cache(maxsize=1)
def calculation_revision() -> str:
    """Invalidate saved calculations after a deployed change in their engine."""
    here = Path(__file__).parent
    services = here.parents[1] / "services"
    paths = [here / name for name in (
        "performance.py", "persistent_cache.py", "repository.py", "analytics.py", "compositions.py", "legacy_duplicates.py",
    )]
    paths += [services / name for name in (
        "bling_vendas.py", "estoque_historico.py", "cadastro_compatibilidade.py", "cadastro_lojas_produtos.py",
    )]
    digest = hashlib.sha256(CALCULATION_VERSION.encode("ascii"))
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def identity(generation: Generation, namespace: str, client_id: str, filters: Any, sources: Any, day: str = "") -> str:
    return _digest([SCHEMA, CALCULATION_VERSION, calculation_revision(), generation.tenant_root,
                    namespace, client_id, filters, sources, day])


def valid_payload(namespace: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if namespace == "resumo":
        meta = payload.get("meta")
        composition = meta.get("composicao") if isinstance(meta, dict) else None
        return (payload.get("success") is True and isinstance(payload.get("rows"), list)
                and all(isinstance(row, dict) for row in payload["rows"])
                and isinstance(payload.get("totals"), dict) and isinstance(meta, dict)
                and (composition is None or (isinstance(composition, dict) and not composition.get("incomplete", False))))
    if namespace == "grafico":
        labels = payload.get("labels")
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            return False
        for name in ("valores_vendas", "quantidades_vendas", "valores_devolucoes", "quantidades_devolucoes"):
            if not isinstance(payload.get(name), list) or len(payload[name]) != len(labels):
                return False
        for name in ("estoque_geral", "estoque_sku", "estoque_skus_com_saldo", "estoque_skus_pareto_com_saldo"):
            if name in payload and (not isinstance(payload[name], list) or len(payload[name]) not in (0, len(labels))):
                return False
        stock = payload.get("estoque_meta")
        return stock is None or (isinstance(stock, dict) and stock.get("success", True) is not False)
    return False


def load(generation: Generation, namespace: str, key: str) -> dict | None:
    try:
        if namespace not in NAMESPACES or not re.fullmatch(r"[0-9a-f]{64}", key) or not current(generation):
            return None
        path = _directory(generation.tenant_root) / (key + ".json")
        if _linked(path) or path.stat().st_size > MAX_ENTRY_BYTES:
            return None
        with path.open("rb") as stream:
            content = stream.read(MAX_ENTRY_BYTES + 1)
        if len(content) > MAX_ENTRY_BYTES:
            return None
        envelope = json.loads(content)
        if (not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA or envelope.get("key") != key
                or envelope.get("namespace") != namespace
                or envelope.get("generation") != [generation.global_revision, generation.tenant_revision]
                or not isinstance(envelope.get("created_at"), (float, int))
                or not math.isfinite(envelope["created_at"]) or envelope["created_at"] > time.time() + 60):
            return None
        payload = envelope.get("payload")
        if (not valid_payload(namespace, payload) or _digest(payload) != envelope.get("checksum")
                or not current(generation)):
            return None
        return payload
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError, CacheUnavailable):
        return None


@contextmanager
def _writer_lock(directory: Path):
    """Bound the cache work and serialize pruning across server processes."""
    lock_path = directory / ".writer.lock"
    if _linked(lock_path):
        raise CacheUnavailable()
    with _LOCKS[hash(str(directory)) % len(_LOCKS)]:
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            locked = False
            try:
                while not locked:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise CacheUnavailable() from None
                        time.sleep(0.01)
                yield
            finally:
                if locked:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _prune(directory: Path) -> None:
    entries = []
    for path in directory.iterdir():
        if not _SNAPSHOT.fullmatch(path.name) or _linked(path):
            continue
        try:
            entry = path.stat()
            entries.append((entry.st_mtime_ns, entry.st_size, path))
        except FileNotFoundError:
            pass
    entries.sort(key=lambda entry: (entry[0], entry[2].name))
    total = sum(entry[1] for entry in entries)
    count = len(entries)
    for _, size, path in entries:
        if count <= MAX_ENTRIES and total <= MAX_BYTES:
            break
        try:
            path.unlink()
            total -= size
            count -= 1
        except FileNotFoundError:
            pass


def save(generation: Generation, namespace: str, key: str, payload: Any, validate: Callable[[], bool]) -> bool:
    try:
        if namespace not in NAMESPACES or not re.fullmatch(r"[0-9a-f]{64}", key) or not valid_payload(namespace, payload):
            return False
        if not current(generation) or not validate():
            return False
        content = _json_bytes({
            "schema": SCHEMA, "key": key, "namespace": namespace,
            "generation": [generation.global_revision, generation.tenant_revision],
            "created_at": time.time(), "checksum": _digest(payload), "payload": payload,
        })
        if len(content) > MAX_ENTRY_BYTES:
            return False
        directory = _directory(generation.tenant_root)
        with _writer_lock(directory):
            if not _atomic_write(directory / (key + ".json"), content, lambda: current(generation) and validate()):
                return False
            _prune(directory)
        return current(generation) and validate()
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, CacheUnavailable):
        return False
