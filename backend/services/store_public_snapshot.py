"""Credential-free display projection; callers own transaction and tenant scope.

This module never reads canonical stores, performs recovery, or acquires business
locks. A reader sees one complete published generation or SnapshotUnavailable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from backend.services.mercadolivre_oauth_status import oauth_status

_FILENAME = "lojas_public_snapshot.json"
_MAX_BYTES = 4 * 1024 * 1024
_ENVELOPE_KEYS = {"schema_version", "generation", "published_at", "lojas"}
_ROW_KEYS = {
    "nome", "store_id", "site_id", "seller_id", "mercadolivre_conectado",
    "mercadolivre_status", "mercadolivre_motivo", "mercadolivre_oauth_faltando",
    "precisa_reintegrar",
}
_OAUTH_FIELDS = {"access_token", "refresh_token", "app_id", "client_secret"}
_PUBLIC_REASONS = {
    "conectado": "",
    "pendente": "Mercado Livre ainda não foi autenticado.",
    "reautenticar": "OAuth do Mercado Livre incompleto. Refaça a autenticação.",
}


class SnapshotUnavailable(ValueError):
    """No valid public generation is available; contains no underlying data."""


def snapshot_path(tenant_path):
    """The tenant path must already have been resolved and authorized by caller."""
    path = Path(tenant_path) / _FILENAME
    expected = os.path.join(os.path.realpath(path.parent), _FILENAME)
    if (path.is_symlink()
            or os.path.normcase(os.path.realpath(path)) != os.path.normcase(expected)):
        raise SnapshotUnavailable("unsafe_snapshot_path")
    return path


def _text(value, *, max_length=512):
    if not isinstance(value, str) or len(value) > max_length or any(ord(c) < 32 for c in value):
        raise SnapshotUnavailable("invalid_snapshot")
    return value


def _identity(value):
    # Canonical legacy IDs may be numeric. Never stringify containers or secrets.
    if value is None:
        return ""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return _text(value, max_length=128).strip()


def _validate(snapshot):
    if (not isinstance(snapshot, dict) or set(snapshot) != _ENVELOPE_KEYS
            or type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1):
        raise SnapshotUnavailable("invalid_snapshot")
    try:
        UUID(_text(snapshot["generation"], max_length=36))
        published_at = datetime.fromisoformat(_text(snapshot["published_at"]).replace("Z", "+00:00"))
        if published_at.utcoffset() is None or published_at.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise SnapshotUnavailable("invalid_snapshot") from None
    if not isinstance(snapshot["lojas"], list) or len(snapshot["lojas"]) > 10000:
        raise SnapshotUnavailable("invalid_snapshot")
    seen = set()
    for row in snapshot["lojas"]:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise SnapshotUnavailable("invalid_snapshot")
        if not _text(row["nome"]).strip():
            raise SnapshotUnavailable("invalid_snapshot")
        for key in ("store_id", "site_id", "seller_id"):
            value = _text(row[key], max_length=128)
            if value != value.strip():
                raise SnapshotUnavailable("invalid_snapshot")
        store_id = row["store_id"]
        # Missing legacy IDs remain display-only; never resolve identities by name.
        if store_id and store_id in seen:
            raise SnapshotUnavailable("invalid_snapshot")
        if store_id:
            seen.add(store_id)
        if type(row["mercadolivre_conectado"]) is not bool or type(row["precisa_reintegrar"]) is not bool:
            raise SnapshotUnavailable("invalid_snapshot")
        status = _text(row["mercadolivre_status"])
        if (status not in _PUBLIC_REASONS
                or row["mercadolivre_motivo"] != _PUBLIC_REASONS[status]
                or row["mercadolivre_conectado"] != (status == "conectado")):
            raise SnapshotUnavailable("invalid_snapshot")
        missing = row["mercadolivre_oauth_faltando"]
        if (not isinstance(missing, list) or any(not isinstance(v, str) or v not in _OAUTH_FIELDS for v in missing)
                or len(set(missing)) != len(missing) or (row["mercadolivre_conectado"] and missing)):
            raise SnapshotUnavailable("invalid_snapshot")
    return snapshot


def build_snapshot(stores, *, generation=None, published_at=None):
    """Project committed canonical rows or this session's central public stores."""
    if not isinstance(stores, list):
        raise SnapshotUnavailable("invalid_snapshot")
    rows = []
    for store in stores:
        if not isinstance(store, dict):
            raise SnapshotUnavailable("invalid_snapshot")
        integrations = store.get("integracoes") or {}
        if not isinstance(integrations, dict):
            raise SnapshotUnavailable("invalid_snapshot")
        cfg = integrations.get("mercadolivre") or {}
        if not isinstance(cfg, dict):
            raise SnapshotUnavailable("invalid_snapshot")
        status = oauth_status(cfg)
        connected = bool(status["conectado"])
        # Custom OAuth error strings can contain provider responses and secrets.
        # Public disk projection only stores fixed presentation messages.
        public_status = "conectado" if connected else ("reautenticar" if cfg else "pendente")
        rows.append({
            "nome": _text(store.get("nome", "")).strip(),
            "store_id": _identity(store.get("store_id")),
            "site_id": _identity(cfg.get("site_id") or store.get("site_id")),
            "seller_id": _identity(cfg.get("user_id")),
            "mercadolivre_conectado": connected,
            "mercadolivre_status": public_status,
            "mercadolivre_motivo": _PUBLIC_REASONS[public_status],
            "mercadolivre_oauth_faltando": list(status["faltando"]),
            "precisa_reintegrar": False,
        })
    result = {
        "schema_version": 1,
        "generation": str(uuid4()) if generation is None else generation,
        "published_at": datetime.now(timezone.utc).isoformat() if published_at is None else published_at,
        "lojas": rows,
    }
    return _validate(result)


def write_snapshot(tenant_path, snapshot):
    """Atomically publish an already committed projection, without business locks."""
    _validate(snapshot)
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > _MAX_BYTES:
        raise SnapshotUnavailable("invalid_snapshot")
    path = snapshot_path(tenant_path)
    # Caller owns tenant initialization. Never implicitly create a tenant directory.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".lojas-public-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + 0.25
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows readers or scanners can briefly reject replacement.
                # Reads only hold the file while copying bounded bytes. Keep the
                # prior generation and bound retries; failures go to the owner.
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotUnavailable("invalid_snapshot")
        result[key] = value
    return result


def read_snapshot(tenant_path):
    try:
        deadline = time.monotonic() + 0.05
        while True:
            try:
                with snapshot_path(tenant_path).open("rb") as handle:
                    payload = handle.read(_MAX_BYTES + 1)
                break
            except PermissionError:
                # A simultaneous Windows replace can briefly deny opening the
                # pathname. This bounded retry never waits for business locks.
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.001)
        if len(payload) > _MAX_BYTES:
            raise SnapshotUnavailable("invalid_snapshot")
        return _validate(json.loads(payload, object_pairs_hook=_unique_object))
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SnapshotUnavailable("snapshot_unavailable") from None
