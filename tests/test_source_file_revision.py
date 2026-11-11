"""Independent content-fence checks using synthetic SQLite databases only."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import time

import pytest

from backend.services import source_file_revision as revision


ROOT = Path(__file__).resolve().parents[1]


class _StatView:
    def __init__(self, original, *, anonymous=False):
        self.original = original
        self.st_ino = 0 if anonymous else original.st_ino
        self.st_mtime_ns = 1_000_000_000
        self.st_ctime_ns = 1_000_000_000

    def __getattr__(self, name):
        return getattr(self.original, name)


class _CountedFile:
    def __init__(self, source, calls):
        self.source = source
        self.calls = calls

    def __enter__(self):
        self.source.__enter__()
        return self

    def __exit__(self, *args):
        return self.source.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def read(self, size=-1):
        chunk = self.source.read(size)
        self.calls.append((size, len(chunk)))
        return chunk


def _count_file_reads(monkeypatch):
    calls = []
    original = revision._open_read
    monkeypatch.setattr(revision, "_open_read", lambda path: _CountedFile(original(path), calls))
    return calls


def _freeze_metadata(monkeypatch, *, anonymous=False):
    original = revision._info
    monkeypatch.setattr(revision, "_info", lambda path: _StatView(original(path), anonymous=anonymous))


def _digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _read_at(path, offset, length):
    with path.open("rb") as source:
        source.seek(offset)
        return source.read(length)


@pytest.fixture
def wal_database(tmp_path):
    path = tmp_path / "source.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA page_size=1024")
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE fixture(id INTEGER PRIMARY KEY, payload BLOB)")
        connection.executemany("INSERT INTO fixture VALUES (?, ?)", ((i, b"a" * 900) for i in range(1, 97)))
        connection.commit()
        yield path, connection
    finally:
        connection.close()


def _copy_wal_database(path, destination):
    for suffix in ("", "-wal", "-shm"):
        Path(str(destination) + suffix).write_bytes(Path(str(path) + suffix).read_bytes())
    return destination


def test_same_size_regular_file_changes_with_identical_metadata(tmp_path, monkeypatch):
    path = tmp_path / "source.csv"
    path.write_bytes(b"sku,amount\nA,10\n")
    _freeze_metadata(monkeypatch)
    before_info = revision._identity(revision._info(path))
    before = revision.content_stamp(path)
    path.write_bytes(b"sku,amount\nA,20\n")
    assert revision._identity(revision._info(path)) == before_info
    assert revision.content_stamp(path) != before


def test_sqlite_commit_changes_revision_when_size_and_clock_collide(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fixture(amount INTEGER)")
        connection.execute("INSERT INTO fixture VALUES (10)")
        connection.commit()
        _freeze_metadata(monkeypatch)
        metadata = revision._identity(revision._info(path))
        before = revision.content_stamp(path)
        connection.execute("UPDATE fixture SET amount=20")
        connection.commit()
        assert revision._identity(revision._info(path)) == metadata
        assert revision.content_stamp(path) != before
    connection.close()


def test_wal_restart_detects_two_commits_before_the_old_physical_tail(wal_database, monkeypatch):
    path, connection = wal_database
    wal = Path(str(path) + "-wal")
    page_size = 1024
    checkpoint = connection.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
    assert checkpoint[0] == 0 and checkpoint[1] == checkpoint[2]
    physical_size = wal.stat().st_size
    tail_offset = physical_size - page_size - 24
    old_tail = _read_at(wal, tail_offset, 24)
    _freeze_metadata(monkeypatch)
    connection.execute("UPDATE fixture SET payload=? WHERE id=1", (b"b" * 900,))
    connection.commit()
    first = revision.content_stamp(path)
    header = _read_at(wal, 0, 32)
    connection.execute("UPDATE fixture SET payload=? WHERE id=2", (b"c" * 900,))
    connection.commit()
    second = revision.content_stamp(path)
    committed_frames = struct.unpack("=I", _read_at(Path(str(path) + "-shm"), 16, 4))[0]
    assert 32 + committed_frames * (24 + page_size) < physical_size
    assert wal.stat().st_size == physical_size
    assert _read_at(wal, 0, 32) == header
    assert _read_at(wal, tail_offset, 24) == old_tail
    assert first != second, "A new commit must invalidate even when WAL header, size and physical tail stay unchanged"
    assert revision.content_stamp(wal) == second


def test_checkpoint_truncate_and_next_commit_are_fenced(wal_database):
    path, connection = wal_database
    before = revision.content_stamp(path)
    assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    assert Path(str(path) + "-wal").stat().st_size == 0
    checkpointed = revision.content_stamp(path)
    assert checkpointed != before
    assert revision.content_stamp(path) == checkpointed
    assert connection.execute("SELECT COUNT(*) FROM fixture").fetchone()[0] == 96
    connection.execute("UPDATE fixture SET payload=? WHERE id=1", (b"z" * 900,))
    connection.commit()
    assert revision.content_stamp(path) != checkpointed


def test_same_header_after_wal_commit_and_truncate_uses_changed_content(wal_database, monkeypatch):
    path, connection = wal_database
    assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    _freeze_metadata(monkeypatch)
    header = _read_at(path, 0, 100)
    before = revision.content_stamp(path)
    connection.execute("UPDATE fixture SET payload=? WHERE id=1", (b"t" * 900,))
    connection.commit()
    assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    assert _read_at(path, 0, 100) == header, "The fixture must expose the unchanged database-header case"
    assert revision.content_stamp(path) != before


def test_delete_exclusive_commits_with_same_header_are_fenced(tmp_path, monkeypatch):
    path = tmp_path / "exclusive.db"
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        assert connection.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone()[0] == "exclusive"
        connection.execute("CREATE TABLE fixture(amount INTEGER)")
        connection.execute("INSERT INTO fixture VALUES (10)")
        connection.commit()
        _freeze_metadata(monkeypatch)
        header = _read_at(path, 0, 100)
        before = revision.content_stamp(path)
        connection.execute("UPDATE fixture SET amount=20")
        connection.commit()
        assert _read_at(path, 0, 100) == header, "Exclusive locking must keep the file-change counter unchanged"
        assert revision.content_stamp(path) != before
    finally:
        connection.close()


def test_absent_and_empty_wal_have_the_same_database_stamp(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fixture(amount INTEGER)")
    connection.close()
    before = revision.content_stamp(path)
    wal = Path(str(path) + "-wal")
    wal.touch()
    assert revision.content_stamp(path) == before
    wal.unlink()
    assert revision.content_stamp(path) == before


@pytest.mark.parametrize("strict,anonymous", [(False, False), (True, False), (False, True)])
def test_large_sqlite_fallback_hashes_every_byte(tmp_path, monkeypatch, strict, anonymous):
    path = tmp_path / "large.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fixture(payload BLOB)")
        connection.execute("INSERT INTO fixture VALUES (zeroblob(?))", (9 * 1024 * 1024,))
    connection.close()
    _freeze_metadata(monkeypatch, anonymous=anonymous)
    calls = _count_file_reads(monkeypatch)
    before = revision.content_stamp(path, strict=strict)
    assert before == ("sha256", _digest(path))
    assert path.stat().st_size <= sum(length for _, length in calls) < path.stat().st_size + 4096
    assert all(0 < requested <= 1024 * 1024 for requested, _ in calls)
    header = _read_at(path, 0, 100)
    with path.open("r+b") as source:
        source.seek(path.stat().st_size // 2)
        source.write(b"different payload")
    assert _read_at(path, 0, 100) == header
    calls.clear()
    after = revision.content_stamp(path, strict=strict)
    assert after == ("sha256", _digest(path)) and after != before
    assert path.stat().st_size <= sum(length for _, length in calls) < path.stat().st_size + 4096


def test_unknown_database_and_wal_formats_use_integral_digests(tmp_path):
    database = tmp_path / "unknown.db"
    wal = Path(str(database) + "-wal")
    database.write_bytes(b"not a SQLite database" * 400)
    wal.write_bytes(b"not a valid WAL header" * 400)
    for path in (database, wal):
        assert revision.content_stamp(path) == ("sha256", _digest(path))


@pytest.mark.parametrize("corruption", ["copies_differ", "checksum", "truncated_wal"])
def test_inconsistent_wal_headers_fail_closed(wal_database, tmp_path, corruption):
    original, _connection = wal_database
    path = _copy_wal_database(original, tmp_path / "corrupt.db")
    if corruption == "truncated_wal":
        with Path(str(path) + "-wal").open("r+b") as source:
            source.truncate(32 + 100)
    else:
        shm = Path(str(path) + "-shm")
        contents = bytearray(shm.read_bytes())
        contents[8] ^= 1
        if corruption == "checksum":
            contents[56] ^= 1
        shm.write_bytes(contents)
    with pytest.raises(OSError, match="source_revision_changed"):
        revision.content_stamp(path)


def test_missing_wal_during_capture_retries_while_database_remains(wal_database, monkeypatch):
    path, connection = wal_database
    wal = Path(str(path) + "-wal")
    expected = revision.content_stamp(path)
    database_digest = _digest(path)
    original = revision._read
    wal_reads = 0

    def rotating_wal_read(file, offset, size):
        nonlocal wal_reads
        if Path(file) == wal:
            wal_reads += 1
            if wal_reads == 1:
                raise FileNotFoundError("synthetic WAL rotation after stat")
        return original(file, offset, size)

    monkeypatch.setattr(revision, "_read", rotating_wal_read)
    assert revision.content_stamp(path) == expected
    assert wal_reads >= 2
    assert path.is_file() and _digest(path) == database_digest
    assert connection.execute("SELECT COUNT(*) FROM fixture").fetchone()[0] == 96


def test_actual_database_removal_during_capture_propagates_not_found(tmp_path, monkeypatch):
    path = tmp_path / "removed.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE fixture(amount INTEGER)")
    connection.close()
    original = revision._open_read
    removed = False

    def remove_before_open(file):
        nonlocal removed
        if Path(file) == path and not removed:
            path.unlink()
            removed = True
        return original(file)

    monkeypatch.setattr(revision, "_open_read", remove_before_open)
    with pytest.raises(FileNotFoundError):
        revision.content_stamp(path)
    assert removed and not path.exists()


def test_continuously_changing_headers_fail_closed(wal_database, monkeypatch):
    path, _connection = wal_database
    original = revision._read
    calls = []

    def racing_read(file, offset, size):
        value, identity = original(file, offset, size)
        if Path(file) == path and offset == 0 and size == 100:
            calls.append(None)
            value = value[:24] + len(calls).to_bytes(4, "big") + value[28:]
        return value, identity

    monkeypatch.setattr(revision, "_read", racing_read)
    with pytest.raises(OSError, match="source_revision_changed"):
        revision.content_stamp(path)
    assert 2 <= len(calls) <= 16, "An unstable source must use a bounded retry budget"


def test_atomic_replacement_during_capture_retries_the_current_file(tmp_path, monkeypatch):
    path = tmp_path / "source.db"
    replacement = tmp_path / "replacement.db"
    for file, version in ((path, 1), (replacement, 2)):
        with sqlite3.connect(file) as connection:
            connection.execute("CREATE TABLE fixture(amount INTEGER)")
            connection.execute(f"PRAGMA user_version={version}")
        connection.close()
    expected = revision.content_stamp(replacement)
    previous = revision.content_stamp(path)
    original = revision._read
    replaced = False

    def replace_after_read(file, offset, size):
        nonlocal replaced
        result = original(file, offset, size)
        if not replaced and Path(file) == path:
            os.replace(replacement, path)
            replaced = True
        return result

    monkeypatch.setattr(revision, "_read", replace_after_read)
    assert revision.content_stamp(path) == expected != previous
    assert replaced


def test_canonical_wal_stamp_is_stable_across_processes_and_readers(wal_database):
    path, _connection = wal_database
    before = revision.content_stamp(path)
    child = """
import json, sqlite3, sys
from pathlib import Path
from backend.services.source_file_revision import content_stamp
path = Path(sys.argv[1])
connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
connection.execute('BEGIN')
connection.execute('SELECT COUNT(*) FROM fixture').fetchone()
print(json.dumps(content_stamp(path)))
connection.close()
"""
    output = subprocess.check_output([sys.executable, "-B", "-c", child, str(path)], cwd=ROOT, timeout=20)
    assert json.loads(output) == json.loads(json.dumps(before))
    assert revision.content_stamp(path) == before


def test_large_active_wal_polling_reads_bounded_commit_headers(wal_database, monkeypatch):
    path, connection = wal_database
    connection.execute("INSERT INTO fixture VALUES (1000, zeroblob(?))", (16 * 1024 * 1024,))
    connection.commit()
    assert connection.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()[0] == 0
    connection.execute("UPDATE fixture SET payload=? WHERE id=1", (b"q" * 900,))
    connection.commit()
    assert path.stat().st_size > 16 * 1024 * 1024
    read = revision._read
    index_header = revision._index_header
    full_digest = revision._full_digest
    requests = []
    mappings = []
    file_reads = _count_file_reads(monkeypatch)

    def counted_read(file, offset, size):
        requests.append(size)
        return read(file, offset, size)

    def counted_index(file):
        mappings.append(96)
        return index_header(file)

    def forbidden_hash(_file):
        pytest.fail("Polling a recognized SQLite database must not hash its full contents")

    monkeypatch.setattr(revision, "_read", counted_read)
    monkeypatch.setattr(revision, "_index_header", counted_index)
    monkeypatch.setattr(revision, "_full_digest", forbidden_hash)
    polls = 30
    started = time.perf_counter()
    stamps = [revision.content_stamp(path) for _ in range(polls)]
    elapsed = time.perf_counter() - started
    logical_bytes = sum(length for _requested, length in file_reads) + sum(mappings)
    assert all(stamp == stamps[0] for stamp in stamps)
    assert max(requests) <= 100
    assert logical_bytes <= polls * 1024
    print(f"synthetic active WAL polling: {polls} polls, {logical_bytes} header bytes, {elapsed:.4f}s")

    assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    connection.close()
    monkeypatch.setattr(revision, "_full_digest", full_digest)
    file_reads.clear()
    requests.clear()
    mappings.clear()
    fallback_polls = 3
    started = time.perf_counter()
    fallback_stamps = [revision.content_stamp(path) for _ in range(fallback_polls)]
    elapsed = time.perf_counter() - started
    fallback_bytes = sum(length for _requested, length in file_reads)
    expected_fallback = ("sha256", _digest(path))
    assert all(stamp == expected_fallback for stamp in fallback_stamps)
    assert fallback_bytes >= path.stat().st_size * fallback_polls
    assert fallback_bytes < path.stat().st_size * fallback_polls + 4096
    assert mappings == []
    print(f"synthetic closed database fallback: {fallback_polls} polls, {fallback_bytes} read bytes, {elapsed:.4f}s")
