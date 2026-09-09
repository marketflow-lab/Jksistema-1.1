"""Windows owner-file sharing during background reader election."""
import json
from pathlib import Path

from backend.modules.context_hub.locking import _remove_owned_lock


def sharing_violation():
    error = PermissionError("synthetic sharing violation")
    error.winerror = 32
    return error


def test_release_retries_transient_windows_reader(tmp_path, monkeypatch):
    lock = tmp_path / "operation.lock"
    lock.write_text(json.dumps({"token": "owner"}))
    original = Path.unlink
    attempts = []

    def unlink(path, *args, **kwargs):
        if path == lock:
            attempts.append(True)
            if len(attempts) < 3:
                raise sharing_violation()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert _remove_owned_lock(lock, "owner")
    assert len(attempts) == 3 and not lock.exists()


def test_retry_never_removes_another_owner(tmp_path, monkeypatch):
    lock = tmp_path / "operation.lock"
    lock.write_text(json.dumps({"token": "owner"}))
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == lock:
            lock.write_text(json.dumps({"token": "replacement"}))
            raise sharing_violation()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert not _remove_owned_lock(lock, "owner")
    assert json.loads(lock.read_text())["token"] == "replacement"
