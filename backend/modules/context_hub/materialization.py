"""Byte-level attestation for immutable Context Hub generation snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from backend.modules.context_hub.contracts import (
    ContextHubConflictError,
    ContextHubValidationError,
)
from backend.modules.context_hub.path_safety import (
    _assert_path_chain_safe,
    _is_link_or_junction,
)
from backend.modules.context_hub.runtime import _json_canonical, _utc_now


def _stable_file_hash(path: Path) -> tuple[str, int]:
    try:
        before = os.stat(path, follow_symlinks=False)
        if _is_link_or_junction(path) or not path.is_file():
            raise OSError("materialized_entry_not_regular")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ContextHubValidationError(
            "Snapshot materializado indisponivel para verificacao."
        ) from error
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise ContextHubValidationError(
            "Snapshot materializado mudou durante a verificacao."
        )
    return digest.hexdigest(), int(after.st_size)


def capture_generation_materialization(root: Path) -> dict[str, Any]:
    _assert_path_chain_safe(root, root.parent)
    if _is_link_or_junction(root) or not root.is_dir():
        raise ContextHubValidationError("Snapshot materializado invalido.")
    manifest: list[list[Any]] = []
    try:
        entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as error:
        raise ContextHubValidationError("Snapshot materializado indisponivel.") from error
    for entry in entries:
        _assert_path_chain_safe(entry, root)
        if _is_link_or_junction(entry):
            raise ContextHubValidationError("Snapshot materializado contem redirecionamento.")
        if entry.is_dir():
            continue
        digest, size = _stable_file_hash(entry)
        manifest.append([entry.relative_to(root).as_posix(), digest, size])
    rendered = _json_canonical(manifest)
    return {
        "root_hash": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "file_count": len(manifest),
        "byte_count": sum(int(item[2]) for item in manifest),
    }


def persist_generation_materialization(
    connection: sqlite3.Connection,
    generation_id: str,
    attestation: Mapping[str, Any],
) -> None:
    root_hash = str(attestation.get("root_hash") or "").strip().lower()
    try:
        file_count = int(attestation.get("file_count"))
        byte_count = int(attestation.get("byte_count"))
    except (TypeError, ValueError) as error:
        raise ContextHubValidationError("Atestado de materializacao invalido.") from error
    if not re.fullmatch(r"[a-f0-9]{64}", root_hash) or file_count < 0 or byte_count < 0:
        raise ContextHubValidationError("Atestado de materializacao invalido.")
    connection.execute(
        "INSERT INTO context_hub_generation_materialization("
        "generation_id, root_hash, file_count, byte_count) VALUES (?, ?, ?, ?)",
        (generation_id, root_hash, file_count, byte_count),
    )


def assert_generation_materialization_current(
    connection: sqlite3.Connection,
    generation_id: str,
    root: Path,
) -> None:
    expected = connection.execute(
        "SELECT root_hash, file_count, byte_count "
        "FROM context_hub_generation_materialization WHERE generation_id=?",
        (generation_id,),
    ).fetchone()
    if expected is None:
        raise ContextHubConflictError(
            "Atestado do snapshot indisponivel; reconstrua antes de publicar."
        )
    try:
        actual = capture_generation_materialization(root)
    except ContextHubValidationError as error:
        raise ContextHubConflictError(
            "Snapshot materializado mudou; reconstrua antes de publicar."
        ) from error
    if any(
        str(expected[key]) != str(actual[key])
        for key in ("root_hash", "file_count", "byte_count")
    ):
        raise ContextHubConflictError(
            "Snapshot materializado mudou; reconstrua antes de publicar."
        )


def _legacy_snapshot_matches_persisted_documents(
    root: Path,
    expected: Mapping[str, tuple[str, str]],
) -> bool:
    try:
        actual_paths = {
            entry.relative_to(root).as_posix()
            for entry in root.rglob("*")
            if entry.is_file()
        }
    except OSError:
        return False
    if actual_paths != set(expected):
        return False
    for relative, (content, content_hash) in expected.items():
        target = root / Path(relative)
        try:
            _assert_path_chain_safe(target, root)
            digest, size = _stable_file_hash(target)
        except (ContextHubValidationError, OSError):
            return False
        rendered = content.encode("utf-8")
        if (
            size != len(rendered)
            or digest != content_hash
            or hashlib.sha256(rendered).hexdigest() != content_hash
        ):
            return False
        try:
            if target.read_bytes() != rendered:
                return False
        except OSError:
            return False
    return True


def _legacy_evidence_store_empty(connection: sqlite3.Connection) -> bool:
    evidence_count = int(
        connection.execute("SELECT COUNT(*) FROM product_evidence_claims").fetchone()[0]
    )
    outbox = connection.execute(
        "SELECT requested_revision, completed_revision "
        "FROM context_hub_product_evidence_outbox WHERE singleton_id=1"
    ).fetchone()
    return bool(
        not evidence_count
        and outbox is not None
        and int(outbox["requested_revision"] or 0) == 0
        and int(outbox["completed_revision"] or 0) == 0
    )


def _legacy_candidates(connection: sqlite3.Connection) -> tuple[list[Any], dict[str, dict[str, tuple[str, str]]]]:
    rows = connection.execute(
        "SELECT g.generation_id FROM context_hub_generations g "
        "LEFT JOIN context_hub_generation_materialization m ON m.generation_id=g.generation_id "
        "LEFT JOIN context_hub_generation_product_evidence e ON e.generation_id=g.generation_id "
        "WHERE g.status IN ('ready','active','superseded') "
        "AND (m.generation_id IS NULL OR e.generation_id IS NULL)"
    ).fetchall()
    expected_by_generation: dict[str, dict[str, tuple[str, str]]] = {}
    for row in rows:
        generation_id = str(row["generation_id"] or "")
        documents = connection.execute(
            "SELECT relative_path, content, content_hash FROM context_hub_documents "
            "WHERE generation_id=? AND managed=1 AND relative_path LIKE '70_Gerado/%'",
            (generation_id,),
        ).fetchall()
        expected: dict[str, tuple[str, str]] = {}
        for document in documents:
            relative_path = str(document["relative_path"] or "")
            relative = relative_path[len("70_Gerado/") :] if relative_path.startswith("70_Gerado/") else ""
            if not relative or relative in expected:
                expected = {}
                break
            expected[relative] = (
                str(document["content"] or ""),
                str(document["content_hash"] or "").lower(),
            )
        if expected:
            expected_by_generation[generation_id] = expected
    return list(rows), expected_by_generation


def _capture_legacy_materializations(
    paths: Any,
    rows: list[Any],
    expected_by_generation: Mapping[str, Mapping[str, tuple[str, str]]],
) -> list[tuple[str, dict[str, Any]]]:
    captured: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        generation_id = str(row["generation_id"] or "")
        root = paths.generations_dir / generation_id / "70_Gerado"
        expected = expected_by_generation.get(generation_id)
        if not root.is_dir() or not expected or not _legacy_snapshot_matches_persisted_documents(root, expected):
            continue
        try:
            materialization = capture_generation_materialization(root)
        except ContextHubValidationError:
            continue
        if _legacy_snapshot_matches_persisted_documents(root, expected):
            captured.append((generation_id, materialization))
    return captured


def _insert_legacy_attestations(
    connection: sqlite3.Connection,
    captured: list[tuple[str, dict[str, Any]]],
    snapshot: Mapping[str, Any],
    captured_at: str,
) -> None:
    for generation_id, materialization in captured:
        connection.execute(
            "INSERT OR IGNORE INTO context_hub_generation_materialization("
            "generation_id, root_hash, file_count, byte_count) VALUES (?, ?, ?, ?)",
            (generation_id, materialization["root_hash"], materialization["file_count"], materialization["byte_count"]),
        )
        connection.execute(
            "INSERT OR IGNORE INTO context_hub_generation_product_evidence("
            "generation_id, evidence_revision, snapshot_hash, projection_hash, "
            "policy_version, captured_at, next_transition_at, projection_next_transition_at) "
            "VALUES (?, 0, ?, ?, ?, ?, NULL, NULL)",
            (
                generation_id,
                str(snapshot["snapshot_hash"]),
                "",
                str(snapshot["policy_version"]),
                captured_at,
            ),
        )


def backfill_legacy_empty_evidence_attestations(paths: Any) -> int:
    """Attest pre-feature generations only while the evidence store is empty."""

    from backend.modules.context_hub.product_evidence_editorial import collect_product_evidence_editorial_snapshot
    from backend.modules.context_hub.storage import _connect

    with _connect(paths) as connection:
        if not _legacy_evidence_store_empty(connection):
            return 0
        rows, expected = _legacy_candidates(connection)
        if not rows:
            return 0
        captured_at = _utc_now()
        snapshot = collect_product_evidence_editorial_snapshot(
            connection, client_id=paths.client_id, as_of=captured_at
        )
        if snapshot.get("identities"):
            return 0
    captured = _capture_legacy_materializations(paths, rows, expected)
    if not captured:
        return 0
    with _connect(paths) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not _legacy_evidence_store_empty(connection):
            connection.rollback()
            return 0
        _insert_legacy_attestations(connection, captured, snapshot, captured_at)
        connection.commit()
    return len(captured)


__all__ = [
    "assert_generation_materialization_current",
    "backfill_legacy_empty_evidence_attestations",
    "capture_generation_materialization",
    "persist_generation_materialization",
]
