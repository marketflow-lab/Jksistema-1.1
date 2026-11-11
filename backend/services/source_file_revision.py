"""Content revisions for source fences, independent of filesystem clock granularity.

SQLite tokens use its documented database/WAL commit records, not sampled rows.
See sqlite.org/fileformat.html and sqlite.org/walformat.html.
"""

from __future__ import annotations

import ctypes
import hashlib
import mmap
import os
from pathlib import Path
import stat
import struct
import threading


_SQLITE_MAGIC = b"SQLite format 3\0"
_WAL_VERSION = 3007000
_SHM_BARRIER = threading.Lock()


class _RevisionBusy(OSError):
    def __init__(self):
        super().__init__("source_revision_changed")


def _open_read(path):
    """Allow an atomic publisher to replace files while a revision is read."""
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        return os.fdopen(descriptor, "rb", buffering=0)
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    handle = create(str(path), 0x80000000, 7, None, 3, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb", buffering=0)


def _info(path):
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise _RevisionBusy()
    return value


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size


def _read(path, offset, size):
    before = _info(path)
    with _open_read(path) as source:
        opened = os.fstat(source.fileno())
        if before.st_ino and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise _RevisionBusy()
        source.seek(offset)
        value = source.read(size)
    if _identity(_info(path)) != _identity(before):
        raise _RevisionBusy()
    return value, _identity(before)


def _checksum(data, endian):
    words = struct.unpack(endian + str(len(data) // 4) + "I", data)
    first = second = 0
    for index in range(0, len(words), 2):
        first = (first + words[index] + second) & 0xFFFFFFFF
        second = (second + words[index + 1] + first) & 0xFFFFFFFF
    return first, second


def _index_header(path):
    before = _info(path)
    if before.st_size < 96:
        raise _RevisionBusy()
    with _open_read(path) as source:
        opened = os.fstat(source.fileno())
        if before.st_ino and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise _RevisionBusy()
        with mmap.mmap(source.fileno(), 96, access=mmap.ACCESS_READ) as shared:
            first = shared[:48]
            # An acquire/release barrier separates the two shared-memory samples.
            with _SHM_BARRIER:
                second = shared[48:96]
    if first != second or _identity(_info(path)) != _identity(before):
        raise _RevisionBusy()
    if struct.unpack("=I", first[:4])[0] != _WAL_VERSION or first[12] != 1:
        return None
    if _checksum(first[:40], "=") != struct.unpack("=2I", first[40:48]):
        raise _RevisionBusy()
    return first, _identity(before)


def _sqlite_capture(path):
    header, database_identity = _read(path, 0, 100)
    if (len(header) != 100 or header[:16] != _SQLITE_MAGIC
            or header[18] not in (1, 2) or header[19] not in (1, 2)):
        return None
    page_size = struct.unpack(">H", header[16:18])[0]
    page_size = 65536 if page_size == 1 else page_size
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        return None
    wal = Path(str(path) + "-wal")
    try:
        wal_info = _info(wal)
    except FileNotFoundError:
        return None
    if wal_info.st_size == 0:
        return None
    wal_header, wal_identity = _read(wal, 0, 32)
    if len(wal_header) != 32:
        return None
    magic, version, wal_page_size = struct.unpack(">3I", wal_header[:12])
    if magic not in (0x377F0682, 0x377F0683) or version != _WAL_VERSION or wal_page_size != page_size:
        return None
    endian = ">" if magic & 1 else "<"
    if _checksum(wal_header[:24], endian) != struct.unpack(">2I", wal_header[24:32]):
        return None
    try:
        indexed = _index_header(Path(str(path) + "-shm"))
    except FileNotFoundError:
        return None
    if indexed is None:
        return None
    index_header, shm_identity = indexed
    indexed_page = struct.unpack("=H", index_header[14:16])[0]
    indexed_page = 65536 if indexed_page == 1 else indexed_page
    if (indexed_page != page_size or index_header[13] != magic & 1
            or index_header[32:40] != wal_header[16:24]):
        raise _RevisionBusy()
    frame, pages = struct.unpack("=2I", index_header[16:24])
    if frame == 0:
        return None
    if pages == 0:
        raise _RevisionBusy()
    offset = 32 + (frame - 1) * (24 + page_size)
    if offset + 24 + page_size > wal_info.st_size:
        raise _RevisionBusy()
    frame_header, frame_identity = _read(wal, offset, 24)
    if (len(frame_header) != 24 or struct.unpack(">I", frame_header[:4])[0] == 0
            or frame_header[8:16] != wal_header[16:24]
            or struct.unpack(">I", frame_header[4:8])[0] != pages
            or struct.unpack(">2I", frame_header[16:24]) != struct.unpack("=2I", index_header[24:32])
            or frame_identity != wal_identity):
        raise _RevisionBusy()
    # iChange is connection-era state; read marks/locks/checkpoint progress are
    # transient. The WAL generation, committed end and chained checksum persist.
    committed = (frame, pages, *struct.unpack("=2I", index_header[24:32]), index_header[32:40].hex())
    token = ("sqlite-v1", database_identity[:2], header.hex(), wal_identity[:2],
             wal_header.hex(), committed, frame_header.hex())
    return token, (database_identity, wal_identity, shm_identity)


def _full_digest(path):
    before = _info(path)
    with _open_read(path) as source:
        opened = os.fstat(source.fileno())
        if before.st_ino and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise _RevisionBusy()
        digest = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    after = _info(path)
    if (_identity(before), before.st_mtime_ns, before.st_ctime_ns) != (
            _identity(after), after.st_mtime_ns, after.st_ctime_ns):
        raise _RevisionBusy()
    return "sha256", digest.hexdigest()


def content_stamp(path, *, strict=False):
    """Return a portable content token; no source contents or paths are logged.

    A reliable SQLite identity allows bounded commit-header reads. Unknown
    identities/formats use an integral streaming hash, without a stat cache.
    Continuously changing headers fail closed rather than naming a false read.
    """
    path = Path(path)
    initial = _info(path)
    database = path if path.suffix.casefold() == ".db" else (
        Path(str(path)[:-4]) if str(path).casefold().endswith(".db-wal") else None)
    for _attempt in range(3):
        try:
            if not strict and initial.st_ino and database is not None:
                try:
                    captured = _sqlite_capture(database)
                except FileNotFoundError:
                    if database == path:
                        raise
                    captured = None
                if captured is not None:
                    if captured != _sqlite_capture(database):
                        raise _RevisionBusy()
                    return captured[0]
            return _full_digest(path)
        except _RevisionBusy:
            continue
        except FileNotFoundError:
            # A sidecar can disappear during checkpoint/rotation while the
            # requested source still exists. Do not report the DB as removed.
            _info(path)
            continue
    raise _RevisionBusy()
