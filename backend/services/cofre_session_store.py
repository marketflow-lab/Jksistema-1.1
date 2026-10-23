"""Cofre tickets in Windows Credential Manager; never a plaintext fallback."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path

from backend.services import secure_credentials

_HEX = re.compile(r"[0-9a-f]{64}")
_CHUNK_BYTES = 2048  # Credential Manager permits at most 2560 bytes per blob.
_MAX_CHUNKS = 32


def namespace_for_info(info_root=None):
    root = Path(info_root or os.getenv("JK_INFO_DIR") or Path(__file__).resolve().parents[2] / "info")
    canonical = os.path.normcase(str(root.resolve()))
    return "cofre-session-v2:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CofreSessionStore:
    """Publish chunked tickets through one small, last-written secure index."""

    def __init__(self, info_root=None, *, credentials=None):
        self.namespace = namespace_for_info(info_root)
        self.credentials = credentials or secure_credentials
        self._lock = threading.RLock()

    def _target(self, token_key):
        if not isinstance(token_key, str) or not _HEX.fullmatch(token_key):
            raise ValueError("invalid_cofre_ticket_key")
        return self.namespace + ":ticket:" + token_key

    def _identity_target(self, identity_key):
        if not isinstance(identity_key, str) or not _HEX.fullmatch(identity_key):
            raise ValueError("invalid_cofre_identity_key")
        return self.namespace + ":identity:" + identity_key

    def _index(self, target):
        raw = self.credentials.read_scoped_secret(target)
        if not raw:
            return None
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {"version", "generation", "chunks", "sha256"}
                or value["version"] != 1 or type(value["chunks"]) is not int
                or not 1 <= value["chunks"] <= _MAX_CHUNKS
                or not re.fullmatch(r"[0-9a-f]{32}", str(value["generation"]))
                or not _HEX.fullmatch(str(value["sha256"]))):
            raise ValueError("invalid_cofre_ticket_index")
        return value

    def _remove_chunks(self, target, index):
        if index:
            for number in range(index["chunks"]):
                self.credentials.delete_scoped_secret(f"{target}:{index['generation']}:{number}")

    def load(self, token_key):
        with self._lock:
            target = self._target(token_key)
            index = self._index(target)
            if index is None:
                return None
            chunks = [self.credentials.read_scoped_secret(f"{target}:{index['generation']}:{n}")
                      for n in range(index["chunks"])]
            if any(not part or len(part.encode("utf-8")) > _CHUNK_BYTES for part in chunks):
                raise ValueError("incomplete_cofre_ticket")
            raw = "".join(chunks)
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != index["sha256"]:
                raise ValueError("invalid_cofre_ticket_digest")
            ticket = json.loads(raw)
            if (not isinstance(ticket, dict) or set(ticket) != {"session_grant", "capability", "identity_key", "context"}
                    or not all(isinstance(ticket[key], str) for key in ("session_grant", "capability", "identity_key"))
                    or not isinstance(ticket["context"], dict)):
                raise ValueError("invalid_cofre_ticket")
            if self.credentials.read_scoped_secret(self._identity_target(ticket["identity_key"])) != token_key:
                return None  # A later login for this identity revoked this ticket.
            return ticket

    def save(self, token_key, ticket):
        raw = json.dumps(ticket, ensure_ascii=True, separators=(",", ":"))
        if len(raw.encode("utf-8")) > _CHUNK_BYTES * _MAX_CHUNKS:
            raise ValueError("cofre_ticket_too_large")
        identity_target = self._identity_target(ticket["identity_key"])
        with self._lock:
            target = self._target(token_key)
            previous = self._index(target)
            previous_key = self.credentials.read_scoped_secret(identity_target)
            generation = secrets.token_hex(16)
            chunks = [raw[n:n + _CHUNK_BYTES] for n in range(0, len(raw), _CHUNK_BYTES)]
            try:
                for n, value in enumerate(chunks):
                    if not self.credentials.write_scoped_secret(f"{target}:{generation}:{n}", value):
                        raise RuntimeError("cofre_secure_store_unavailable")
                index = {"version": 1, "generation": generation, "chunks": len(chunks),
                         "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()}
                if not self.credentials.write_scoped_secret(target, json.dumps(index, separators=(",", ":"))):
                    raise RuntimeError("cofre_secure_store_unavailable")
                if not self.credentials.write_scoped_secret(identity_target, token_key):
                    raise RuntimeError("cofre_secure_store_unavailable")
            except Exception:
                # Restore the last published index if publication was interrupted.
                try:
                    if previous is not None:
                        self.credentials.write_scoped_secret(target, json.dumps(previous, separators=(",", ":")))
                    else:
                        self.credentials.delete_scoped_secret(target)
                    if previous_key:
                        self.credentials.write_scoped_secret(identity_target, previous_key)
                    else:
                        self.credentials.delete_scoped_secret(identity_target)
                finally:
                    self._remove_chunks(target, {"generation": generation, "chunks": len(chunks)})
                raise
            self._remove_chunks(target, previous)
            if previous_key and previous_key != token_key and _HEX.fullmatch(previous_key):
                self.delete(previous_key)

    def delete(self, token_key):
        with self._lock:
            target = self._target(token_key)
            index = self._index(target)
            self.credentials.delete_scoped_secret(target)
            self._remove_chunks(target, index)

    def revoke_identity(self, identity_key):
        with self._lock:
            target = self._identity_target(identity_key)
            previous_key = self.credentials.read_scoped_secret(target)
            self.credentials.delete_scoped_secret(target)
            if previous_key and _HEX.fullmatch(previous_key):
                self.delete(previous_key)
