"""Security component."""



from __future__ import annotations



import re
from pathlib import Path
from typing import (
    Any,
    Sequence,
)



from .contracts import _SENSITIVE_VALUE_PATTERNS



from .normalization import (
    _sha256_bytes,
    _sha256_value,
)



def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()

def _read_text(path: Path) -> str:
    # ``py_compile`` aceita BOM UTF-8, mas ``ast.parse`` recebe o caractere se
    # o arquivo for aberto como UTF-8 simples. Remover somente o BOM inicial
    # mantem a leitura equivalente ao importador do Python.
    return path.read_text(encoding="utf-8", errors="strict").lstrip("\ufeff")

def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def _safe_resolve(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _is_relative_to(resolved, root_resolved):
        return None
    return resolved

def _relative_ref(path: Path, base: Path, *, fallback: str = "") -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except (OSError, ValueError):
        return fallback or path.name

def _source_hash(paths: Sequence[Path], base: Path) -> str:
    rows: list[dict[str, str]] = []
    for path in sorted(set(paths), key=lambda item: _relative_ref(item, base, fallback=item.name)):
        try:
            rows.append(
                {
                    "path": _relative_ref(path, base, fallback=path.name),
                    "sha256": _sha256_bytes(_read_bytes(path)),
                }
            )
        except OSError:
            rows.append({"path": _relative_ref(path, base, fallback=path.name), "sha256": "unreadable"})
    return _sha256_value(rows)

def _safe_summary(value: Any, *, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    for _code, pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.search(text):
            return "Conteudo omitido pela politica de seguranca."
    return text[:limit]

def _safe_content(value: Any, *, limit: int = 100_000) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for _code, pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.search(text):
            return "Conteudo omitido pela politica de seguranca."
    return text[:limit]
