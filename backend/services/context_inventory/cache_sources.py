"""Content signatures over exactly the static inventory source selectors."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .legacy import _legacy_files, _source_version
from .python_scanner import _path_is_excluded, _python_files
from .route_scanner import _route_files
from .security import _read_text, _safe_resolve
from .surface_scanner import (
    _integration_specs,
    _resolve_static_script,
    _script_refs,
    _static_script_candidate,
    _test_files,
)


def _is_redirect(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def _validated_source_root(path: Path) -> Path:
    original = path.expanduser().absolute()
    resolved = original.resolve(strict=True)
    if original != resolved or _is_redirect(original) or not original.is_dir():
        raise ValueError("Inventory source root must not be redirected.")
    return original


def _validate_source_path(path: Path, base: Path) -> None:
    relative = path.relative_to(base)
    cursor = base
    for part in relative.parts:
        cursor /= part
        if _is_redirect(cursor):
            raise ValueError("Inventory sources must not contain links or junctions.")
    if _safe_resolve(path, base) is None:
        raise ValueError("Inventory source is outside its authorized root.")


def _static_source_files(base: Path) -> dict[Path, bool]:
    """False entries affect findings by name, without opening excluded content."""
    files = {path: True for path in _python_files(base)}
    files.update({path: True for path in _route_files(base) if not _path_is_excluded(path, base)})
    schemas = base / "backend" / "schemas"
    if schemas.is_dir():
        files.update({p: True for p in schemas.rglob("*.py") if not _path_is_excluded(p, schemas)})
    modules = base / "backend" / "modules"
    if modules.is_dir():
        files.update({p: True for p in modules.rglob("*.py") if not _path_is_excluded(p, modules)})
    static = base / "static"
    if static.is_dir():
        for page in static.glob("*.html"):
            _validate_source_path(page, base)
            files[page] = True
            for ref in _script_refs(_read_text(page)):
                original_script = _static_script_candidate(static, page, ref)
                script = _resolve_static_script(static, page, ref)
                if script is not None and script.suffix.lower() == ".js":
                    _validate_source_path(original_script, base)
                    files[script] = True
        files.update({p: True for p in static.rglob("*.js") if not _path_is_excluded(p, static)})
    files.update({p: True for p in _test_files(base)})
    for _slug, _title, paths in _integration_specs(base):
        files.update({p: True for p in paths})
    files.update({p: not _path_is_excluded(p, base) for p in _legacy_files(base)})
    for name in ("package.json", "runtime-manifest.json"):
        path = base / name
        if path.is_file():
            files[path] = True
    return files


def _static_source_signature(base: Path) -> tuple[str, str, int, int]:
    _validated_source_root(base)
    before = base.stat()
    digest = hashlib.sha256()
    files = _static_source_files(base)
    for path, read_content in sorted(files.items(), key=lambda item: item[0].relative_to(base).as_posix()):
        _validate_source_path(path, base)
        relative = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        source_digest = hashlib.sha256()
        if read_content:
            with path.open("rb") as source:
                while block := source.read(64 * 1024):
                    source_digest.update(block)
        else:
            source_digest.update(b"excluded")
        digest.update(source_digest.digest())
    after = base.stat()
    _validated_source_root(base)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("Inventory source root changed during validation.")
    return digest.hexdigest(), _source_version(base), int(after.st_dev), int(after.st_ino)
