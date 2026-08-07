"""Context Hub path safety component."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional



from backend.modules.context_hub.contracts import (
    ContextHubValidationError,
    _InfoRootSnapshot,
)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_link_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(path))
    except OSError:
        return True


def _snapshot_info_root(root: Path) -> _InfoRootSnapshot:
    """Capture a stable, non-redirected info root for one path operation."""

    absolute = root.expanduser().absolute()

    def capture() -> tuple[Path, Optional[tuple[int, int, int]]]:
        try:
            resolved = absolute.resolve(strict=False)
            if _is_link_or_junction(absolute):
                raise ContextHubValidationError(
                    "Links simbolicos ou junctions nao sao aceitos como raiz info do Context Hub."
                )
            try:
                stat = os.lstat(absolute)
            except FileNotFoundError:
                identity = None
            else:
                identity = (int(stat.st_dev), int(stat.st_ino), int(stat.st_mode))
            return resolved, identity
        except ContextHubValidationError:
            raise
        except OSError as error:
            raise ContextHubValidationError(
                "Nao foi possivel validar a raiz info do Context Hub."
            ) from error

    resolved_before, identity_before = capture()
    resolved_after, identity_after = capture()
    if resolved_before != resolved_after or identity_before != identity_after:
        raise ContextHubValidationError(
            "A raiz info do Context Hub foi alterada durante a validacao."
        )
    if resolved_after != absolute:
        raise ContextHubValidationError(
            "A raiz info do Context Hub nao pode ser redirecionada."
        )
    return _InfoRootSnapshot(absolute=absolute, resolved=resolved_after, identity=identity_after)


def _assert_path_chain_safe(path: Path, root: Path) -> None:
    root_resolved = root.resolve()
    candidate = path.absolute()
    if not _is_relative_to(candidate, root.absolute()) and candidate != root.absolute():
        raise ContextHubValidationError("Caminho fora da raiz autorizada do Context Hub.")
    relative = candidate.relative_to(root.absolute()) if candidate != root.absolute() else Path()
    cursor = root.absolute()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.exists() and _is_link_or_junction(cursor):
            raise ContextHubValidationError("Links simbolicos ou junctions nao sao aceitos no Context Hub.")
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, root_resolved) and resolved != root_resolved:
        raise ContextHubValidationError("Caminho resolvido fora da raiz autorizada do Context Hub.")
