"""Durable, bounded JSON snapshots for the monthly Medias view only.

Sources and access policy remain authoritative. Filenames and metadata contain
hashes, never request headers, tokens, raw queries or filesystem paths.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time


SCHEMA = 1
CALCULATION_VERSION = "medias-visao-json-v1"
DIRECTORY = ".medias_visao_cache"
MAX_ENTRY_BYTES = 34 * 1024 * 1024
MAX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 64
MAX_AGE_SECONDS = 90 * 24 * 60 * 60
LOCK_TIMEOUT_SECONDS = 0.25
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(r"[0-9a-f]{64}\.json\Z")
_TEMP_FILE = re.compile(r"\.write-[a-z0-9_]{8}\.tmp\Z")
_LOCKS = tuple(threading.RLock() for _ in range(32))


class CacheUnavailable(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _canonical(root):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(root))))


def _linked(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _directory(root, *, create=False):
    canonical = _canonical(root)
    directory = Path(canonical) / DIRECTORY
    if _linked(directory):
        raise CacheUnavailable()
    if create:
        directory.mkdir(exist_ok=True)
    if _linked(directory) or _canonical(directory.parent) != canonical:
        raise CacheUnavailable()
    return directory


@lru_cache(maxsize=1)
def calculation_revision():
    services = Path(__file__).parent
    root = services.parents[1]
    paths = set(services.glob("medias_compras*.py")) | set(services.glob("cadastro*.py"))
    paths.update((services / "bling_vendas.py", root / "medias_compras.py"))
    digest = hashlib.sha256(CALCULATION_VERSION.encode("ascii"))
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _identity(root, key):
    return _digest([SCHEMA, calculation_revision(), _canonical(root), key])


def _generated(record):
    value = datetime.fromisoformat(record["gerado_em"])
    if value.tzinfo is None:
        raise ValueError("Missing timezone")
    return value.timestamp()


def _valid(record):
    if not isinstance(record, dict) or set(record) != {
        "payload", "assinatura", "autoridade", "alvo", "politica_fotos", "gerado_em", "versao",
    }:
        return False
    if any(not isinstance(record[name], str) or not _HASH.fullmatch(record[name])
           for name in ("assinatura", "autoridade", "alvo", "politica_fotos")):
        return False
    if not isinstance(record["versao"], str) or not re.fullmatch(r"[0-9a-f]{24}", record["versao"]):
        return False
    age = time.time() - _generated(record)
    if not -300 <= age <= MAX_AGE_SECONDS:
        return False
    payload = record["payload"]
    return (isinstance(payload, dict) and payload.get("success") is True
            and payload.get("periodo_meses") in (3, 6, 12)
            and isinstance(payload.get("itens"), list)
            and all(isinstance(item, dict) for item in payload["itens"])
            and isinstance(payload.get("colunas_meses"), list)
            and all(isinstance(item, dict) for item in payload["colunas_meses"])
            and isinstance(payload.get("resumo"), dict)
            and "_cache" not in payload)


def _read(path, identity):
    if _linked(path):
        return None
    with path.open("rb") as stream:
        content = stream.read(MAX_ENTRY_BYTES + 1)
    if len(content) > MAX_ENTRY_BYTES:
        return None
    envelope = json.loads(content)
    if (not isinstance(envelope, dict) or set(envelope) != {"schema", "identity", "record", "checksum"}
            or envelope["schema"] != SCHEMA or envelope["identity"] != identity
            or envelope["checksum"] != _digest([SCHEMA, identity, envelope["record"]])):
        return None
    return envelope["record"] if _valid(envelope["record"]) else None


def ler(root, key):
    try:
        identity = _identity(root, key)
        return _read(_directory(root) / (identity + ".json"), identity)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, CacheUnavailable):
        return None


@contextmanager
def _writer(directory):
    # Serialize pruning and replace across processes as well as threads.
    with _LOCKS[hash(str(directory)) % len(_LOCKS)]:
        lock = directory / ".lock"
        if _linked(lock):
            raise CacheUnavailable()
        with lock.open("a+b") as stream:
            if not lock.stat().st_size:
                stream.write(b"0")
                stream.flush()
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            acquired = False
            while not acquired:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise CacheUnavailable()
                    time.sleep(.01)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _clear_orphans(directory):
    # The directory writer lock excludes every active writer. Only our own
    # mkstemp names can be leftover files from an interrupted publication.
    for path in directory.iterdir():
        if _TEMP_FILE.fullmatch(path.name) and not _linked(path) and path.is_file():
            path.unlink()


def _prune(directory):
    entries = []
    for path in directory.iterdir():
        if not _FILE.fullmatch(path.name) or _linked(path):
            continue
        info = path.stat()
        if time.time() - info.st_mtime > MAX_AGE_SECONDS:
            path.unlink()
        else:
            entries.append((info.st_mtime_ns, path, info.st_size))
    entries.sort()
    total = sum(size for _, _, size in entries)
    while entries and (len(entries) > MAX_ENTRIES or total > MAX_BYTES):
        _, path, size = entries.pop(0)
        path.unlink()
        total -= size


def gravar(root, key, record):
    temporary = None
    try:
        if not _valid(record):
            return False
        identity = _identity(root, key)
        content = _json({"schema": SCHEMA, "identity": identity, "record": record,
                         "checksum": _digest([SCHEMA, identity, record])})
        if len(content) > min(MAX_ENTRY_BYTES, MAX_BYTES):
            return False
        directory = _directory(root, create=True)
        path = directory / (identity + ".json")
        with _writer(directory):
            _clear_orphans(directory)
            if _linked(path):
                return False
            previous = ler(root, key)
            if previous and _generated(previous) > _generated(record):
                return False
            descriptor, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=directory)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            _prune(directory)
        return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, CacheUnavailable):
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
