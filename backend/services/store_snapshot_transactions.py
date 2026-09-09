"""Durable publication boundary for local store projections.

The journal contains relative file references only. Preimages stay in the same
private tenant directory, never in the public projection or synchronization.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from .path_coordination import canonical_path_key

_LOCAL = threading.local()
_DIRECTORY = "_stores_publication"
_JOURNAL = "transaction.json"
_BASE_FILES = (
    "lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json",
    "integracoes.json", "_shared_sync_backups/pending_lojas_transaction.json",
)


class StorePublicationRecoveryError(RuntimeError):
    pass


def _states():
    if getattr(_LOCAL, "pid", None) != os.getpid():
        _LOCAL.pid = os.getpid()
        _LOCAL.states = {}
    return _LOCAL.states


def _atomic(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    try:
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _save(root, journal):
    _atomic(root / _DIRECTORY / _JOURNAL,
            json.dumps(journal, ensure_ascii=False).encode("utf-8"))


def _safe_file(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise StorePublicationRecoveryError("invalid_relative_path")
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise StorePublicationRecoveryError("invalid_relative_path")
    if canonical_path_key(candidate) != os.path.normcase(os.path.abspath(candidate)):
        raise StorePublicationRecoveryError("unsafe_file")
    if os.path.commonpath([str(root), str(candidate.resolve())]) != str(root):
        raise StorePublicationRecoveryError("unsafe_file")
    return candidate


def _load(root):
    path = root / _DIRECTORY / _JOURNAL
    if not path.exists():
        return None
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(journal, dict) or set(journal) != {"schema", "state", "files", "recovery"}
                or journal["recovery"] not in {"validate", "rollback"}
                or journal["schema"] != 1 or journal["state"] not in {"prepared", "committed"}
                or not isinstance(journal["files"], dict)):
            raise ValueError()
        for name, row in journal["files"].items():
            _safe_file(root, name)
            if (not isinstance(row, dict) or set(row) != {"backup", "sha256", "exists"}
                    or not isinstance(row["exists"], bool)):
                raise ValueError()
            if row["exists"]:
                if (not isinstance(row["backup"], str) or len(row["backup"]) != 32
                        or any(c not in "0123456789abcdef" for c in row["backup"])):
                    raise ValueError()
                backup = _safe_file(root, _DIRECTORY + "/" + row["backup"])
                if (journal["state"] == "prepared" and journal["recovery"] == "rollback"
                        and hashlib.sha256(backup.read_bytes()).hexdigest() != row["sha256"]):
                    raise ValueError()
        return journal
    except StorePublicationRecoveryError:
        raise
    except Exception:
        raise StorePublicationRecoveryError("invalid_publication_journal") from None


def _cleanup(root, journal, *, strict=False):
    # A confirmed business write cannot fail because projection housekeeping
    # failed. Keep a committed marker when its removal is temporarily denied.
    try:
        (root / _DIRECTORY / _JOURNAL).unlink(missing_ok=True)
    except OSError:
        if strict:
            raise
        return
    for row in journal["files"].values():
        if row["exists"]:
            try:
                (root / _DIRECTORY / row["backup"]).unlink(missing_ok=True)
            except OSError:
                pass


def _restore(root, journal):
    for name, row in reversed(list(journal["files"].items())):
        destination = _safe_file(root, name)
        if row["exists"]:
            data = (root / _DIRECTORY / row["backup"]).read_bytes()
            _atomic(destination, data)
        else:
            destination.unlink(missing_ok=True)


def _capture(state, names):
    root = state["root"]
    journal = state["journal"]
    for name in names:
        if name in journal["files"]:
            continue
        path = _safe_file(root, name)
        row = {"exists": path.exists(), "backup": "", "sha256": ""}
        if row["exists"]:
            data = path.read_bytes()
            row.update(backup=uuid.uuid4().hex, sha256=hashlib.sha256(data).hexdigest())
            _atomic(root / _DIRECTORY / row["backup"], data)
        journal["files"][name] = row
    # Must be durable before any caller changes a captured file.
    _save(root, journal)


def before_write(path):
    """Record an atomic writer's preimage when inside a store transaction."""
    target = Path(os.path.abspath(path))
    for state in _states().values():
        root = state["root"]
        try:
            relative = target.relative_to(root).as_posix()
        except ValueError:
            continue
        if state.get("recovering"):
            return
        if relative.startswith(_DIRECTORY + "/") or relative == "lojas_public_snapshot.json":
            return
        if state["journal"] is None:
            state["journal"] = {"schema": 1, "state": "prepared", "files": {}, "recovery": state["recovery"]}
            _capture(state, _BASE_FILES)
        _capture(state, [relative])
        return


def capture_files(tenant_path, paths):
    """Register additional files before an outer operation modifies them."""
    key = canonical_path_key(tenant_path)
    if key not in _states():
        raise StorePublicationRecoveryError("transaction_required")
    for path in paths:
        before_write(path)


def publication_pending(tenant_path):
    return (Path(tenant_path) / _DIRECTORY / _JOURNAL).exists()


@contextlib.contextmanager
def publication_transaction(tenant_path, publish, recover, *, recovery="validate"):
    """Enter only under the tenant store lock; nested writers never publish."""
    key = canonical_path_key(tenant_path)
    states = _states()
    if key in states:
        yield
        return
    root = Path(key)
    previous = _load(root)
    state = {"root": root, "journal": None, "recovery": recovery, "recovering": True}
    states[key] = state
    try:
        if previous:
            if previous["state"] == "prepared":
                if previous["recovery"] == "rollback":
                    _restore(root, previous)
                recover()
                previous["state"] = "committed"
                _save(root, previous)
            try:
                publish()
            except Exception:
                # A committed mutation must remain editable even if its public
                # projection needs repair (e.g. an explicit lifecycle sequence).
                if previous["state"] != "committed":
                    raise
            else:
                _cleanup(root, previous)
    except Exception:
        states.pop(key, None)
        raise StorePublicationRecoveryError("publication_repair_required") from None
    state["recovering"] = False
    try:
        yield
    except BaseException:
        journal = state["journal"]
        if journal and journal["recovery"] == "rollback":
            _restore(root, journal)
            _cleanup(root, journal, strict=True)
        # Generic domain callers may already have committed CSV/SQLite. Preserve
        # their state and let canonical validation/recovery resolve the journal.
        raise
    else:
        journal = state["journal"]
        if journal:
            journal["state"] = "committed"
            try:
                _save(root, journal)
            except OSError:
                if journal["recovery"] == "rollback":
                    _restore(root, journal)
                    _cleanup(root, journal, strict=True)
                    raise
                # Cross-domain writes may already have committed SQLite/CSV.
                # Prepared/validate safely defers canonical revalidation.
                return
            try:
                publish()
            except Exception:
                # Projection failure must not retry a confirmed business write.
                pass
            else:
                _cleanup(root, journal)
        else:
            # Also materialize the projection on the first healthy canonical read.
            try:
                publish()
            except Exception:
                pass
    finally:
        states.pop(key, None)
