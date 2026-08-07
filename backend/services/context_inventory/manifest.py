"""Manifest component."""



from __future__ import annotations



from pathlib import Path
from typing import Any






from .normalization import _sha256_bytes



from .security import (
    _read_bytes,
    _safe_resolve,
)






def build_context_bundle_manifest(knowledge_dir: str | Path, source_version: str) -> dict[str, Any]:
    """Retorna manifesto deterministico dos arquivos versionados de conhecimento."""

    root = Path(knowledge_dir).expanduser().resolve(strict=True)
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == "context-bundle-manifest.json":
            continue
        resolved = _safe_resolve(path, root)
        if resolved is None:
            raise ValueError(f"arquivo fora da raiz de conhecimento: {path.name}")
        data = _read_bytes(resolved)
        relative = resolved.relative_to(root).as_posix()
        files.append(
            {
                "path": f"docs/knowledge/{relative}",
                "sha256": _sha256_bytes(data),
                "size": len(data),
            }
        )
    return {
        "schema_version": 1,
        "source_version": str(source_version or "unknown"),
        "files": files,
    }
