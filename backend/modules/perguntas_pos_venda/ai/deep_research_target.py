"""Target-identity matching and evidence activation for deep research."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .deep_research_contracts import (
    ResearchDocumentV1,
    sanitize_public_research_text,
    technical_assertion_occurrence_is_positive,
)
from .official_source_registry import (
    is_reviewed_official_domain,
    official_domains_for_target_identity,
)
from .runtime import _normalizar_texto

ResearchEntry = tuple[dict, str, str, str]
ResearchGroup = tuple[str, str, list, list[dict]]

_TARGET_SOURCE_AUTHORITIES = {
    "official_manufacturer", "official_oem", "technical_distributor", "technical_independent",
}
_TARGET_OFFICIAL_AUTHORITIES = {"official_manufacturer", "official_oem"}
_TARGET_TECHNICAL_AUTHORITIES = {"technical_distributor", "technical_independent"}
_COMPATIBILITY_CRITICAL_QUERY_TYPES = {
    "target_interface_official", "target_interface_technical", "product_interface_technical",
    "interface_equivalence",
}


@dataclass(frozen=True)
class DeepResearchCallbacks:
    """Source-layer operations kept injectable to preserve the legacy facade."""

    reserve_queries: Callable[..., tuple[object, list[dict[str, str]], dict[str, list[str]]]]
    prefetch_web: Callable[..., dict[str, list[dict[str, Any]]]]
    select_items: Callable[[dict, dict, set[str]], tuple[str, str, list]]
    read_batch: Callable[..., tuple[dict[str, str], dict[str, int], bool]]
    discover_links: Callable[[str, str], list[str]]
    render_results: Callable[[list, str, str, dict], list[str]]
    render_listings: Callable[[list[dict]], list[str]]

def _target_identity_from_input(agent_input: dict) -> str:
    intent = agent_input.get("intent") if isinstance(agent_input.get("intent"), dict) else {}
    compatibility = (
        intent.get("compatibilidade")
        if isinstance(intent.get("compatibilidade"), dict)
        else {}
    )
    value = compatibility.get("target_item") or compatibility.get("target_vehicle") or ""
    safe = sanitize_public_research_text(value, 300).strip()
    return "" if "PROTEGIDO]" in safe else safe


def _target_identity_pattern(target_identity: str) -> re.Pattern | None:
    normalized = _normalizar_texto(target_identity).casefold()
    parts = re.findall(r"[a-z]+|\d+", normalized)
    if len("".join(parts)) < 4:
        return None
    separator = r"[\s._/\-]*"
    return re.compile(
        r"(?<![a-z0-9])" + separator.join(re.escape(part) for part in parts) + r"(?![a-z0-9])",
        flags=re.IGNORECASE,
    )


def _target_identity_is_specific(target_identity: str) -> bool:
    """Reject brand/model-only targets before they can activate interface facts."""

    normalized = _normalizar_texto(target_identity).casefold()
    tokens = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", normalized)
    numeric = [token for token in tokens if re.fullmatch(r"\d+(?:[.,]\d+)?", token)]
    non_year = [
        token for token in numeric
        if not (token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2099)
    ]
    if not non_year:
        return False
    if re.search(r"\b(?=[a-z0-9-]{4,}\b)(?=[a-z0-9-]*[a-z])(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*\b", normalized):
        return True
    if len(non_year) >= 2 or any(len(re.sub(r"\D", "", token)) >= 5 for token in non_year):
        return True
    for index, token in enumerate(tokens[:-1]):
        if token.isalpha() and 1 < len(token) <= 4 and tokens[index + 1] in non_year:
            return True
    return bool(re.search(r"\b\d+[.,]\d+\b", normalized))


def _target_scoped_text(
    document: ResearchDocumentV1, pattern: re.Pattern,
) -> str:
    matches = lambda value: bool(pattern.search(_normalizar_texto(value).casefold()))
    if not matches(document.text):
        return ""
    lines = document.text.splitlines() or [document.text]
    interface_line = re.compile(
        r"\b(?:bcd|pcd|bolt|furacao|fixacao|montagem|mounting|simetr|assimetr|symmetr|asymmetr|"
        r"bracos?|arms?|furos?|holes?|parafusos?|conector|connector)\b",
        flags=re.IGNORECASE,
    )
    model_code = re.compile(
        r"\b(?=[A-Z0-9-]{4,30}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d{3})"
        r"[A-Z0-9]+(?:-[A-Z0-9]+)*\b",
        flags=re.IGNORECASE,
    )
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if not matches(line):
            continue
        target_heading = re.match(r"^\s*(#{1,6})\s+", line)
        selected = [line.strip()]
        for candidate in lines[index + 1 : index + 13]:
            normalized = _normalizar_texto(candidate)
            heading = re.match(r"^\s*(#{1,6})\s+", candidate)
            if re.match(r"^\s*(?:-{3,}|={3,})\s*$", candidate):
                break
            if model_code.search(normalized) and not matches(candidate):
                break
            if heading and target_heading and len(heading.group(1)) <= len(target_heading.group(1)):
                break
            if interface_line.search(normalized):
                selected.append(candidate.strip())
        blocks.append("\n".join(value for value in selected if value))
    return "\n".join(dict.fromkeys(value for value in blocks if value))[:12_000]


def _target_source_authority(
    document: ResearchDocumentV1, target_identity: str,
) -> str:
    authority = str(document.source_type or "").strip().lower()
    host = str(urlparse(document.url).hostname or "").strip(".").lower()
    target_roots = official_domains_for_target_identity(target_identity)
    if any(host == root or host.endswith("." + root) for root in target_roots):
        return authority if authority in _TARGET_OFFICIAL_AUTHORITIES else "official_manufacturer"
    if authority in _TARGET_OFFICIAL_AUTHORITIES or is_reviewed_official_domain(host):
        return "technical_independent"
    if authority in _TARGET_TECHNICAL_AUTHORITIES:
        return authority
    return ""


def _target_interface_claims(text: str) -> list[tuple[str, str, str]]:
    normalized = _normalizar_texto(text).casefold()
    claims: set[tuple[str, str, str]] = set()
    symmetry_values: set[str] = set()
    for value, pattern in (
        ("asymmetric", re.compile(r"\b(?:assimetr(?:ico|ica)|asymmetr(?:ic|ical))\b")),
        ("symmetric", re.compile(r"\b(?:simetr(?:ico|ica)|symmetr(?:ic|ical))\b")),
    ):
        if any(
            technical_assertion_occurrence_is_positive(
                normalized, match.start(), match.end(),
            )
            for match in pattern.finditer(normalized)
        ):
            symmetry_values.add(value)
    if len(symmetry_values) == 1:
        claims.add(("interface.symmetry", next(iter(symmetry_values)), ""))
    bolt_pattern = re.compile(
        r"\b(?:bcd|pcd)(?:\s*/\s*(?:bcd|pcd))?\b\s*[:=\-]?\s*"
        r"(?:\d{1,2}\s*[x×]\s*)?(\d{2,4}(?:[.,]\d+)?(?:\s*/\s*\d{2,4}(?:[.,]\d+)?)?)\s*mm\b",
        flags=re.IGNORECASE,
    )
    for match in bolt_pattern.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        value = re.sub(r"\s+", "", match.group(1)).replace(",", ".")
        claims.add(("interface.bolt_pattern", value, "mm"))
    geometry = re.compile(
        r"\b(\d{1,2})\s*(bracos?|arms?|furos?|holes?|parafusos?|bolts?)\b",
        flags=re.IGNORECASE,
    )
    for match in geometry.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        kind = "arm" if match.group(2).startswith(("braco", "arm")) else "hole"
        claims.add(("interface.fixation_geometry", f"{int(match.group(1))}-{kind}", ""))
    connector = re.compile(
        r"\b(?:tipo\s+de\s+conector|connector\s+type|conector|connector)\s*[:=\-]\s*"
        r"([a-z0-9][a-z0-9+._ /\-]{1,40})",
        flags=re.IGNORECASE,
    )
    for match in connector.finditer(normalized):
        if not technical_assertion_occurrence_is_positive(
            normalized, match.start(), match.end(),
        ):
            continue
        value = re.split(r"[;|,.]", match.group(1), maxsplit=1)[0].strip()
        if value:
            claims.add(("interface.connector_type", value[:40], ""))
    return sorted(claims)


def _target_claim_copy_fingerprint(
    text: str,
    pattern: re.Pattern,
    target_identity: str,
    claim: tuple[str, str, str],
) -> str:
    """Fingerprint only the assertion that supports one target claim."""

    assertions: set[str] = set()
    for segment in re.split(r"[\r\n|]+|(?<=[.!?;])\s+", str(text or "")):
        if claim not in _target_interface_claims(segment):
            continue
        normalized = re.sub(
            r"\s+", " ", _normalizar_texto(re.sub(r"https?://\S+", " ", segment)).casefold(),
        ).strip(" .,:;|-")
        identity_match = pattern.search(normalized)
        if identity_match:
            normalized = normalized[identity_match.start():]
        else:
            anchors = {
                "interface.symmetry": r"\b(?:fixacao|montagem|simetr|assimetr|symmetr|asymmetr)",
                "interface.bolt_pattern": r"\b(?:bcd|pcd|bolt|furacao)",
                "interface.fixation_geometry": r"\b(?:fixacao|montagem|furacao|\d+\s*(?:bracos?|arms?|furos?|holes?))",
                "interface.connector_type": r"\b(?:tipo\s+de\s+conector|connector\s+type|conector|connector)",
            }
            anchor_match = re.search(anchors.get(claim[0], r"$^"), normalized)
            if anchor_match:
                normalized = normalized[anchor_match.start():]
        if normalized:
            assertions.add(normalized)
    payload = "\n".join([
        "target-claim-copy-v1",
        _normalizar_texto(target_identity).casefold(),
        "|".join(claim),
        *sorted(assertions),
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _verified_target_evidence(
    agent_input: dict, documents: list[ResearchDocumentV1],
) -> list[dict[str, Any]]:
    target_identity = _target_identity_from_input(agent_input)
    pattern = _target_identity_pattern(target_identity)
    if not target_identity or pattern is None or not _target_identity_is_specific(target_identity):
        return []
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for document in documents:
        if not str(document.query_type or "").startswith("target_"):
            continue
        scoped_text = _target_scoped_text(document, pattern)
        authority = _target_source_authority(document, target_identity) if scoped_text else ""
        if authority not in _TARGET_SOURCE_AUTHORITIES:
            continue
        for claim in _target_interface_claims(scoped_text):
            source = {
                "authority": authority,
                "url": document.url,
                "origin_key": document.origin_key,
                "copy_fingerprint": _target_claim_copy_fingerprint(
                    scoped_text, pattern, target_identity, claim,
                ),
            }
            values = grouped.setdefault(claim, [])
            if not any(value["url"] == source["url"] for value in values):
                values.append(source)
    verified: list[dict[str, Any]] = []
    for (field_name, value, unit), sources in sorted(grouped.items()):
        official = [source for source in sources if source["authority"] in _TARGET_OFFICIAL_AUTHORITIES]
        technical = [source for source in sources if source["authority"] in _TARGET_TECHNICAL_AUTHORITIES]
        technical_origins = {source["origin_key"] for source in technical if source["origin_key"]}
        technical_copies = {source["copy_fingerprint"] for source in technical if source["copy_fingerprint"]}
        if not official and not (len(technical_origins) >= 2 and len(technical_copies) >= 2):
            continue
        verified.append({
            "scope": "target",
            "target_identity": target_identity,
            "field_name": field_name,
            "value": value,
            "unit": unit,
            "activation_policy": "official_or_two_independent_sources",
            "sources": [*official, *technical][:8],
        })
    conflicting_fields = {
        field_name
        for field_name in {value["field_name"] for value in verified}
        if len({
            (value["value"].casefold(), value["unit"].casefold())
            for value in verified if value["field_name"] == field_name
        }) > 1
    }
    return [value for value in verified if value["field_name"] not in conflicting_fields]


def _render_research_context(
    groups: list[ResearchGroup],
    preloaded: dict[str, str],
    pages_read: int,
    callbacks: DeepResearchCallbacks,
) -> list[str]:
    state = {
        "tentadas": pages_read,
        "confirmada": False,
        "preloaded": preloaded,
        "disable_live_reads": True,
    }
    lines: list[str] = []
    for used_query, query_type, items, listings in groups:
        if not items and not listings:
            continue
        lines.append(f"Busca {len(lines) + 1} ({query_type}): {used_query}")
        lines.extend(callbacks.render_results(items[:6], used_query, query_type, state))
        lines.extend(callbacks.render_listings(listings))
        if sum(len(value) for value in lines) >= 12_000:
            break
    return lines
