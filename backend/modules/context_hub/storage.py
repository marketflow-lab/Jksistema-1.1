"""Context Hub storage component."""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Iterator



from backend.modules.context_hub.contracts import (
    ContextHubPaths,
)

from backend.modules.context_hub.runtime import (
    _utc_now,
)


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


_CORE_SCHEMA_SQL = """
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
    store_ref TEXT NOT NULL DEFAULT '',
    tags_text TEXT NOT NULL DEFAULT '',
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS context_hub_curated_approvals (
    relative_path TEXT PRIMARY KEY,
    document_id TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('draft','reviewed','approved','rejected')),
    present INTEGER NOT NULL DEFAULT 1 CHECK(present IN (0,1)),
    missing_at TEXT,
    validated_sha256 TEXT,
    validated_at TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    rejection_reason TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_hub_curated_state
    ON context_hub_curated_approvals(state, updated_at DESC);
CREATE TABLE IF NOT EXISTS context_hub_generation_curated_approvals (
    generation_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (generation_id, relative_path),
    FOREIGN KEY (generation_id) REFERENCES context_hub_generations(generation_id) ON DELETE CASCADE
);
"""

_FTS_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS context_hub_chunks_fts USING fts5(
    generation_id UNINDEXED,
    chunk_id UNINDEXED,
    doc_id UNINDEXED,
    title,
    content,
    module UNINDEXED,
    kind UNINDEXED,
    surface UNINDEXED,
    truth_class UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
)
"""


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_CORE_SCHEMA_SQL)
    try:
        connection.execute(_FTS_SCHEMA_SQL)
    except sqlite3.OperationalError:
        pass


def _migrate_curation_schema(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(context_hub_curated_approvals)")}
    additions = (
        ("document_id", "TEXT NOT NULL DEFAULT ''"),
        ("present", "INTEGER NOT NULL DEFAULT 1"),
        ("missing_at", "TEXT"),
    )
    for column, definition in additions:
        if column not in columns:
            connection.execute(f"ALTER TABLE context_hub_curated_approvals ADD COLUMN {column} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_context_hub_curated_document "
        "ON context_hub_curated_approvals(document_id, present)"
    )


def _migrate_document_schema(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(context_hub_documents)")}
    additions = (
        ("store_ref", "TEXT NOT NULL DEFAULT ''"),
        ("tags_text", "TEXT NOT NULL DEFAULT ''"),
        ("valid_from", "TEXT NOT NULL DEFAULT ''"),
        ("valid_to", "TEXT NOT NULL DEFAULT ''"),
    )
    for column, definition in additions:
        if column not in columns:
            connection.execute(f"ALTER TABLE context_hub_documents ADD COLUMN {column} {definition}")


def _seed_database(connection: sqlite3.Connection) -> None:
    now = _utc_now()
    connection.execute(
        "INSERT OR IGNORE INTO context_hub_active_generation"
        "(singleton_id, generation_id, version, updated_at) VALUES (1, NULL, 0, ?)",
        (now,),
    )
    connection.execute(
        "INSERT OR IGNORE INTO context_hub_settings("
        "singleton_id, auto_publish_enabled, watch_enabled, paused, "
        "debounce_seconds, retention_generations, updated_at"
        ") VALUES (1, 0, 0, 0, 10, 5, ?)",
        (now,),
    )
    connection.execute("UPDATE context_hub_settings SET auto_publish_enabled=0 WHERE singleton_id=1")


def _initialize_database(paths: ContextHubPaths, surface: str) -> None:
    del surface
    with _connect(paths) as connection:
        _create_schema(connection)
        _migrate_curation_schema(connection)
        _migrate_document_schema(connection)
        _seed_database(connection)
