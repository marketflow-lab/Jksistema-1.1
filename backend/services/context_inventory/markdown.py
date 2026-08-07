"""Markdown component."""



from __future__ import annotations



import json
import re
from pathlib import Path
from typing import (
    Any,
    Mapping,
    Sequence,
)



from .contracts import ENTITY_KEYS



from .normalization import _sha256_value






from .entities import _entity



def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (list, dict, int, float)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return json.dumps(str(value), ensure_ascii=False)

def _markdown_safe_text(value: Any) -> str:
    """Remove sequencias que podem ser telefone/PII do artefato publicado.

    Dossies SKU continuam canonicos e intocados. Numeros tecnicos longos ficam
    disponiveis somente na entidade estruturada/adaptador autorizado, nao no
    Markdown agregado aberto no Obsidian. Alvos de wikilinks gerados pelo
    publicador sao identificadores tecnicos internos e precisam permanecer
    byte a byte para que o grafo nao seja corrompido pela redacao numerica.
    """

    text = str(value or "")
    preserved_links: list[str] = []

    def preserve_generated_link(match: re.Match[str]) -> str:
        target = str(match.group(1) or "")
        label = str(match.group(2) or "")
        if not target.startswith("70_Gerado/"):
            return match.group(0)
        safe_label = re.sub(
            r"(?<!\d)\d{8,14}(?!\d)",
            "[codigo-numerico-protegido]",
            label,
        )
        preserved_links.append(f"[[{target}|{safe_label}]]")
        return f"__JK_WIKILINK_{len(preserved_links) - 1}__"

    text = re.sub(
        r"\[\[([^\]|]+)\|([^\]]*)\]\]",
        preserve_generated_link,
        text,
    )
    text = re.sub(
        r"(?<!\d)\d{8,14}(?!\d)",
        "[codigo-numerico-protegido]",
        text,
    )
    for index, link in enumerate(preserved_links):
        text = text.replace(f"__JK_WIKILINK_{index}__", link)
    return text

def _obsidian_wikilink(path: str, label: str | None = None) -> str:
    """Cria um link interno portavel, sempre relativo a raiz do vault."""

    target = str(path or "").replace("\\", "/").strip().removesuffix(".md")
    target = target.replace("[[", "").replace("]]", "").replace("|", "-")
    safe_label = _markdown_safe_text(label or Path(target).name.replace("-", " "))
    safe_label = safe_label.replace("[[", "").replace("]]", "").replace("|", "-")
    return f"[[{target}|{safe_label}]]"

def _with_graph_navigation(
    content: str,
    links: Sequence[tuple[str, str]],
) -> str:
    """Acrescenta arestas reais ao grafo do Obsidian sem links duplicados."""

    unique: dict[str, str] = {}
    for path, label in links:
        normalized = str(path or "").replace("\\", "/").strip()
        if normalized:
            unique.setdefault(normalized, str(label or ""))
    if not unique:
        return content
    navigation = "\n".join(
        f"- {_obsidian_wikilink(path, label)}" for path, label in sorted(unique.items())
    )
    # A navegacao vem primeiro para continuar integra mesmo quando uma fonte
    # agregada atingir o limite defensivo de tamanho do corpo.
    body = content.strip()
    prefix = "## Navegacao no grafo\n\n" + navigation
    return prefix + ("\n\n" + body if body else "")

def _paginate_markdown_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    maximum_chars: int = 60_000,
) -> list[list[Mapping[str, Any]]]:
    """Divide mapas extensos sem cortar uma entidade ou um link Markdown."""

    pages: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_size = 0
    for row in rows:
        line_size = len(f"- `{row.get('id', '')}` - {row.get('title', '')}\n")
        if current and current_size + line_size > maximum_chars:
            pages.append(current)
            current = []
            current_size = 0
        current.append(row)
        current_size += line_size
    if current:
        pages.append(current)
    return pages

def render_context_entity_markdown(
    entity: Mapping[str, Any],
    *,
    source_version: str = "unknown",
    generated_at: str = "1970-01-01T00:00:00Z",
) -> str:
    """Renderiza uma entidade em Markdown gerenciado sem efeitos colaterais."""

    missing = [key for key in ENTITY_KEYS if key not in entity]
    if missing:
        raise ValueError("entidade incompleta: " + ", ".join(missing))
    metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
    frontmatter = {
        "id": entity["id"],
        "type": entity["kind"],
        "managed": True,
        "status": "published",
        "ai_usage": "allowed",
        "tenant_scope": entity["tenant_scope"],
        "sensitivity": entity["sensitivity"],
        "truth_class": entity["truth_class"],
        "required_permissions": metadata.get("required_permissions", ["admin_full"]),
        "surface": entity["surface"],
        "source_version": source_version,
        "source_refs": list(entity["source_refs"]),
        "source_hash": entity["source_hash"],
        "generated_at": generated_at,
    }
    lines = ["---", *[f"{key}: {_yaml_scalar(value)}" for key, value in frontmatter.items()], "---", ""]
    lines.extend(
        [
            f"# {_markdown_safe_text(entity['title'])}",
            "",
            _markdown_safe_text(entity.get("content") or "").strip(),
            "",
        ]
    )
    relationships = entity.get("relationships")
    if isinstance(relationships, list) and relationships:
        lines.extend(["## Relacoes", ""])
        for row in relationships:
            if isinstance(row, dict):
                lines.append(
                    f"- {row.get('type', 'related_to')}: `{_markdown_safe_text(row.get('target_id', ''))}`"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

def _aggregate_entity(
    *,
    entity_id: str,
    kind: str,
    domain: str,
    title: str,
    rows: Sequence[Mapping[str, Any]],
    inventory: Mapping[str, Any],
    content: str,
) -> dict[str, Any]:
    refs = sorted({ref for row in rows for ref in row.get("source_refs", [])})
    return _entity(
        entity_id=entity_id,
        kind=kind,
        domain=domain,
        title=title,
        surface=str(inventory.get("surface") or "checkout"),
        tenant_scope="mixed",
        sensitivity="internal",
        truth_class="generated",
        source_refs=refs[:250],
        source_hash=_sha256_value([(row["id"], row["source_hash"]) for row in rows]),
        metadata={"required_permissions": ["admin_full"], "entity_count": len(rows)},
        content=content,
    )
