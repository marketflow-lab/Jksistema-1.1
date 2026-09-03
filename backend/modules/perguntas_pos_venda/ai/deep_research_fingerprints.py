"""Content fingerprints and copy-aware document deduplication."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

from .deep_research_contracts import ResearchDocumentV1, _plain


_RESEARCH_SOURCE_AUTHORITY_RANK = {
    "official_oem": 5,
    "official_manufacturer": 5,
    "technical_distributor": 4,
    "technical_independent": 4,
    "official_listing": 3,
    "marketplace": 1,
    "forum": 1,
    "blog": 1,
}


def research_document_rank(document: ResearchDocumentV1) -> tuple[int, int]:
    """Prefer claim-safe copies first, then the strongest source authority."""

    return (
        int(bool(getattr(document, "claim_eligible", True))),
        _RESEARCH_SOURCE_AUTHORITY_RANK.get(document.source_type, 0),
    )


def _technical_content_hash(text: object) -> str:
    """Hash normalized page content without turning equal facts into equal pages."""

    source = str(text or "")[:600_000]
    meaningful_lines: list[str] = []
    for line in source.splitlines():
        normalized = _plain(re.sub(r"https?://\S+", " ", line))
        if not normalized:
            continue
        if re.match(
            r"^(?:loja|store|vendedor|seller|distribuidor|revendedor|copyright|todos os direitos)\b",
            normalized,
        ):
            continue
        normalized = re.sub(r"\b(?:loja|store)\s+[a-z0-9_-]{1,24}\b", " ", normalized)
        meaningful_lines.append(re.sub(r"\s+", " ", normalized).strip())
    normalized_body = "\n".join(sorted(set(line for line in meaningful_lines if line)))
    return hashlib.sha256(normalized_body.encode("utf-8", errors="ignore")).hexdigest()


def _technical_copy_fingerprint(text: object) -> str:
    """Fingerprint syndicated technical content while ignoring navigation wrappers."""

    from .deep_research_analysis import extract_technical_claims

    source = str(text or "")[:600_000]
    claim_signatures = sorted({"|".join(claim.signature) for claim in extract_technical_claims(source)})
    prose_markers = (
        "alimentacao", "aplicacao", "assimetr", "bcd", "circuito", "compatibilidade",
        "conector", "desempenho", "fixacao", "furacao", "geometr", "instalacao",
        "material", "montagem", "motor", "mounting", "pcd", "potencia", "pressao",
        "simetr", "symmetr", "tensao", "vazao", "voltagem",
    )
    boilerplate_markers = (
        "ajuda", "contato", "copyright", "footer", "menu", "navegacao",
        "privacidade", "rodape", "termos de uso",
    )
    substantive: set[str] = set()
    for line in source.splitlines():
        normalized = _plain(re.sub(r"https?://\S+", " ", line))
        tokens = re.findall(r"[a-z0-9]+", normalized)
        if any(marker in normalized for marker in boilerplate_markers):
            continue
        technical_line = any(marker in normalized for marker in prose_markers)
        if claim_signatures:
            if (
                len(normalized) < 48
                or len(tokens) < 8
                or extract_technical_claims(line)
                or not technical_line
            ):
                continue
        elif len(tokens) < 4 or not technical_line:
            continue
        substantive.add(normalized)
    if not claim_signatures and not substantive:
        return _technical_content_hash(source)
    payload = "\n".join(["technical-copy-v1", *claim_signatures, *sorted(substantive)])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _technical_content_equivalent(left: object, right: object) -> bool:
    """Collapse mirrored facts even when each copy adds a short branded wrapper."""

    return _technical_copy_fingerprint(left) == _technical_copy_fingerprint(right)


def _deduplicate_research_documents(
    documents: Sequence[ResearchDocumentV1],
) -> list[ResearchDocumentV1]:
    selected: list[ResearchDocumentV1] = []
    seen_urls: set[str] = set()
    for document in documents:
        if document.url in seen_urls:
            continue
        seen_urls.add(document.url)
        duplicate_index = next(
            (
                index
                for index, current in enumerate(selected)
                if _technical_content_equivalent(document.text, current.text)
            ),
            None,
        )
        if duplicate_index is None:
            selected.append(document)
            continue
        current = selected[duplicate_index]
        if research_document_rank(document) > research_document_rank(current):
            selected[duplicate_index] = document
    return selected
