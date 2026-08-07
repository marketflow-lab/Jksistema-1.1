"""Entities component."""



from __future__ import annotations



from typing import (
    Any,
    Mapping,
    Sequence,
)






from .normalization import (
    _slug,
    _domain,
)



from .security import (
    _safe_summary,
    _safe_content,
)



def _finding(
    code: str,
    severity: str,
    source_ref: str,
    message: str,
    *,
    entity_id: str = "",
) -> dict[str, str]:
    result = {
        "code": _slug(code),
        "severity": str(severity),
        "source_ref": str(source_ref),
        "message": _safe_summary(message, limit=240),
    }
    if entity_id:
        result["entity_id"] = str(entity_id)
    return result

def _entity(
    *,
    entity_id: str,
    kind: str,
    domain: str,
    title: str,
    surface: str,
    tenant_scope: str,
    sensitivity: str,
    truth_class: str,
    source_refs: Sequence[str],
    source_hash: str,
    relationships: Sequence[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
    content: str = "",
) -> dict[str, Any]:
    result = {
        "id": str(entity_id),
        "kind": str(kind),
        "domain": _domain(domain),
        "title": _safe_summary(title, limit=180) or str(entity_id),
        "surface": str(surface),
        "tenant_scope": str(tenant_scope),
        "sensitivity": str(sensitivity),
        "truth_class": str(truth_class),
        "source_refs": sorted({str(ref).replace("\\", "/") for ref in source_refs if str(ref).strip()}),
        "source_hash": str(source_hash),
        "relationships": sorted(
            [dict(item) for item in (relationships or [])],
            key=lambda item: (str(item.get("type") or ""), str(item.get("target_id") or "")),
        ),
        "metadata": dict(metadata or {}),
        "content": _safe_content(content),
    }
    return result
