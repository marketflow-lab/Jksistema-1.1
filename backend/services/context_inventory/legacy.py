"""Legacy component."""



from __future__ import annotations



import json
import re
from collections import (
    Counter,
    defaultdict,
)
from pathlib import Path
from typing import (
    Any,
    Iterable,
)






from .normalization import (
    _sha256_bytes,
    _sha256_value,
    _slug,
)



from .security import (
    _read_bytes,
    _read_text,
    _relative_ref,
)



from .entities import (
    _finding,
    _entity,
)

from .python_scanner import _path_is_excluded



def _scan_legacy_docs(base: Path, surface: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    paths: set[Path] = set(base.glob("*.md"))
    docs = base / "docs"
    if docs.is_dir():
        paths.update(path for path in docs.rglob("*.md") if "knowledge" not in path.relative_to(docs).parts)
    entities: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda item: _relative_ref(item, base)):
        if _path_is_excluded(path, base):
            findings.append(
                _finding("legacy_doc_excluded", "info", _relative_ref(path, base), "Documento sensivel excluido do inventario.")
            )
            continue
        ref = _relative_ref(path, base)
        title = path.stem.replace("_", " ").replace("-", " ").title()
        entity_id = f"jk:document:{_slug(ref)}"
        entities.append(
            _entity(
                entity_id=entity_id,
                kind="legacy_document",
                domain="documentacao",
                title=title,
                surface=surface,
                tenant_scope="system",
                sensitivity="internal",
                truth_class="legacy_unverified",
                source_refs=[ref],
                source_hash=_sha256_bytes(_read_bytes(path)),
                metadata={"content_ingested": False},
                content=f"Documento legado {title}. Conteudo nao promovido automaticamente; requer revisao humana.",
            )
        )
    return entities, findings

def _add_domains(entities: list[dict[str, Any]], surface: str) -> list[dict[str, Any]]:
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        if entity["kind"] != "domain":
            by_domain[str(entity["domain"])].append(entity)
    domains: list[dict[str, Any]] = []
    for domain, rows in sorted(by_domain.items()):
        counts = Counter(str(row["kind"]) for row in rows)
        sources = sorted({ref for row in rows for ref in row["source_refs"]})
        domains.append(
            _entity(
                entity_id=f"jk:domain:{domain}",
                kind="domain",
                domain=domain,
                title=domain.replace("-", " ").title(),
                surface=surface,
                tenant_scope="mixed",
                sensitivity="internal",
                truth_class="generated",
                source_refs=sources[:250],
                source_hash=_sha256_value([(row["id"], row["source_hash"]) for row in sorted(rows, key=lambda item: item["id"])]),
                relationships=[{"type": "contains", "target_id": row["id"]} for row in rows],
                metadata={"counts": dict(sorted(counts.items())), "entity_count": len(rows)},
                content=f"Dominio {domain}. Entidades: {len(rows)}. "
                + ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items())),
            )
        )
    return domains

def _deduplicate_entities(
    entities: Iterable[dict[str, Any]], findings: list[dict[str, str]]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        if not entity_id:
            continue
        existing = by_id.get(entity_id)
        if existing is None:
            by_id[entity_id] = entity
            continue
        if existing["source_hash"] != entity["source_hash"]:
            findings.append(
                _finding(
                    "duplicate_entity_id",
                    "blocker",
                    entity["source_refs"][0] if entity["source_refs"] else "inventory",
                    "Duas entidades diferentes produziram o mesmo identificador estavel.",
                    entity_id=entity_id,
                )
            )
    return sorted(by_id.values(), key=lambda item: item["id"])

def _source_version(base: Path) -> str:
    for candidate in (base / "package.json", base / "runtime-manifest.json"):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(_read_text(candidate))
            version = str(payload.get("version") or "").strip() if isinstance(payload, dict) else ""
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}", version):
                return version
        except (OSError, json.JSONDecodeError):
            continue
    return "unknown"
