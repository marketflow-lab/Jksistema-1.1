"""Bounded technical-passage extraction for research documents."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from .deep_research_contracts import (
    PUBLIC_RESEARCH_MAX_PASSAGES,
    ResearchDocumentV1,
    ResearchPassageV1,
    _safe_page_text,
    safe_agent_research_metadata,
    safe_agent_research_passages,
)
from .deep_research_scoping import _identity_terms


_RESEARCH_PASSAGE_CODE_RE = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9./\-]{4,40}(?![A-Z0-9]))"
    r"(?=[A-Z0-9./\-]*[A-Z])(?=[A-Z0-9./\-]*\d)"
    r"[A-Z0-9]+(?:[./\-][A-Z0-9]+)+(?![A-Z0-9])",
    re.IGNORECASE,
)
_RESEARCH_PASSAGE_TECHNICAL_RE = re.compile(
    r"\b(?:part\s+number|oem|refer[eê]ncia|c[oó]digo|switch|sensor|interruptor|"
    r"thermostat|termost[aá]tica|housing|carca[cç]a|assembly|assy|montagem|"
    r"instala[cç][aã]o|aplica[cç][aã]o|compatib|supersed|substitui|replaced)\b",
    re.IGNORECASE,
)
_RESEARCH_PASSAGE_LABELLED_NUMERIC_CODE_RE = re.compile(
    r"\b(?:part\s+number|oem|c[oó]digo\s+(?:oem|original)|refer[eê]ncia)"
    r"\s*[:#=\-]?\s*(\d{5,16})\b",
    re.IGNORECASE,
)
_RESEARCH_PAGE_REF_RE = re.compile(
    r"\b(?:page|p[aá]gina|p\.)\s*[:#=\-]?\s*(\d{1,4})"
    r"(?:\s*(?:of|de)\s*\d{1,4})?\b",
    re.IGNORECASE,
)


def _research_passage_codes(text: object) -> tuple[str, ...]:
    values: list[str] = []
    for match in _RESEARCH_PASSAGE_CODE_RE.finditer(str(text or "")):
        code = match.group(0).upper().strip("./-")[:48]
        compact = re.sub(r"[^A-Z0-9]", "", code)
        if len(compact) == 17 or code in values:
            continue
        values.append(code)
    for match in _RESEARCH_PASSAGE_LABELLED_NUMERIC_CODE_RE.finditer(str(text or "")):
        code = match.group(1)
        if code not in values:
            values.append(code)
    return tuple(values[:12])


def _research_passage_heading(line: str) -> str:
    value = re.sub(r"\s+", " ", str(line or "")).strip(" #-:=\t")[:180]
    if not value:
        return ""
    page_ref = _research_page_ref(value)
    if page_ref and re.match(r"^(?:(?:page|p[aá]gina)\b|p\.)", value, re.IGNORECASE):
        return page_ref
    if re.match(r"^(?:section|se[cç][aã]o|group|grupo|figure|figura|table|tabela)\b", value, re.IGNORECASE):
        return value
    if "|" in value or "\t" in value or "_" in value:
        return ""
    letters = [character for character in value if character.isalpha()]
    if 3 <= len(letters) <= 100 and all(character.isupper() for character in letters):
        return value
    return ""
def _research_page_ref(value: object) -> str:
    match = _RESEARCH_PAGE_REF_RE.search(str(value or ""))
    if match:
        return f"p. {int(match.group(1))}"
    try:
        query = parse_qs(urlsplit(str(value or "")).query)
    except ValueError:
        return ""
    for key in ("page", "pageno", "page_number", "pagina", "p"):
        raw = str((query.get(key) or [""])[0]).strip()
        if raw.isdigit() and 0 < int(raw) <= 9999:
            return f"p. {int(raw)}"
    return ""


def _research_passage_coordinate(lines: Sequence[str], index: int) -> str:
    page_ref = ""
    section_heading = ""
    local_heading = ""
    for prior in range(index, max(-1, index - 24), -1):
        candidate = lines[prior]
        if not page_ref:
            page_ref = _research_page_ref(candidate)
        candidate_heading = _research_passage_heading(candidate)
        if candidate_heading and candidate_heading != page_ref:
            if re.match(
                r"^(?:section|se[cç][aã]o)\b",
                candidate_heading,
                re.IGNORECASE,
            ):
                if not section_heading:
                    section_heading = candidate_heading
            elif not local_heading:
                local_heading = candidate_heading
        if page_ref and section_heading and local_heading:
            break
    return " | ".join(
        value for value in (page_ref, section_heading, local_heading) if value
    )[:180]


def _bounded_research_passage(
    lines: Sequence[str],
    start: int,
    end: int,
) -> str:
    return "\n".join(lines[max(0, start):min(len(lines), end)]).strip()[:2400]


def _extract_research_passages(text: object) -> tuple[ResearchPassageV1, ...]:
    """Build ephemeral code-, section- and table-centred excerpts from a page."""

    safe = _safe_page_text(text)
    lines = [line.strip() for line in safe.splitlines() if line.strip()]
    if not lines:
        return ()
    candidates: list[ResearchPassageV1] = []
    for index, line in enumerate(lines):
        codes = _research_passage_codes(line)
        if codes:
            candidates.append(ResearchPassageV1(
                passage_type="code_context",
                text=_bounded_research_passage(lines, index - 1, index + 2),
                section_ref=_research_passage_coordinate(lines, index),
                codes=codes,
            ))
        table_like = (
            line.count("|") >= 2
            or line.count("\t") >= 2
            or (bool(codes) and bool(re.search(r"\s{2,}", line)))
        )
        if table_like and (codes or _RESEARCH_PASSAGE_TECHNICAL_RE.search(line)):
            candidates.append(ResearchPassageV1(
                passage_type="table",
                text=_bounded_research_passage(lines, index - 1, index + 2),
                section_ref=_research_passage_coordinate(lines, index),
                codes=_research_passage_codes(
                    _bounded_research_passage(lines, index - 1, index + 2)
                ),
            ))
        heading = _research_passage_heading(line)
        if not heading:
            continue
        end = index + 1
        while end < len(lines) and end < index + 9 and not _research_passage_heading(lines[end]):
            end += 1
        section = _bounded_research_passage(lines, index, end)
        section_codes = _research_passage_codes(section)
        if section_codes or _RESEARCH_PASSAGE_TECHNICAL_RE.search(section):
            candidates.append(ResearchPassageV1(
                passage_type="section",
                text=section,
                section_ref=_research_passage_coordinate(lines, index),
                codes=section_codes,
            ))
    result: list[ResearchPassageV1] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        signature = (
            candidate.passage_type,
            re.sub(r"\s+", " ", candidate.text).strip().casefold(),
        )
        if not signature[1] or signature in seen:
            continue
        seen.add(signature)
        result.append(candidate)
        if len(result) >= min(12, PUBLIC_RESEARCH_MAX_PASSAGES):
            break
    return tuple(result)


def _research_passage_projection(
    documents: Sequence[ResearchDocumentV1],
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for document in documents:
        for passage in document.passages:
            raw.append({
                "passage_type": passage.passage_type,
                "text": passage.text,
                "section_ref": passage.section_ref,
                "codes": list(passage.codes),
                "url": document.url,
                "title": document.title,
                "query_type": document.query_type,
                "source_type": document.source_type,
            })
            if len(raw) >= PUBLIC_RESEARCH_MAX_PASSAGES:
                break
        if len(raw) >= PUBLIC_RESEARCH_MAX_PASSAGES:
            break
    return safe_agent_research_passages(raw)


def _research_document_section_ref(
    document: ResearchDocumentV1,
    agent_input: Mapping[str, Any],
) -> str:
    """Choose one real, decisive coordinate for the current source row.

    The repository has one source row per canonical URL.  We therefore retain
    the best actual page/section coordinate found in the bounded passages and
    never substitute the internal search/query type for document provenance.
    """

    target_codes = _research_passage_target_codes(agent_input)
    candidates: list[tuple[int, int, int, str]] = []
    for index, passage in enumerate(document.passages):
        section_ref = safe_agent_research_metadata(passage.section_ref, 160)
        if not section_ref:
            continue
        passage_codes = {
            re.sub(r"[^a-z0-9]", "", code.casefold())
            for code in passage.codes
        }
        identity_rank = 0 if target_codes and passage_codes & target_codes else 1
        normalized = section_ref.casefold()
        if re.search(r"\b(?:section|se[cç][aã]o)\b", normalized):
            structure_rank = 0
        elif re.search(r"\b(?:figure|figura|table|tabela|group|grupo)\b", normalized):
            structure_rank = 1
        elif _research_page_ref(section_ref):
            structure_rank = 2
        else:
            structure_rank = 3
        candidates.append((identity_rank, structure_rank, index, section_ref))
    if candidates:
        return min(candidates)[3]
    return safe_agent_research_metadata(_research_page_ref(document.url), 160)


def _research_passage_target_codes(agent_input: Mapping[str, Any]) -> set[str]:
    _title_tokens, identity_codes = _identity_terms(agent_input)
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    labelled = " ".join(str(item.get(field) or "") for field in ("title", "description"))
    for match in re.finditer(
        r"\b(?:part\s+number|oem|c[oó]digo\s+(?:oem|original)|refer[eê]ncia)"
        r"\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9./\-]{3,31})\b",
        labelled,
        flags=re.IGNORECASE,
    ):
        normalized = re.sub(r"[^a-z0-9]", "", match.group(1).casefold())
        if len(normalized) >= 4:
            identity_codes.add(normalized)
    return identity_codes
