"""Fail-closed structural scoping for multi-product technical pages."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from .deep_research_contracts import (
    _IDENTITY_STOPWORDS,
    _label_metadata_code,
    _missing_fact_value,
    _plain,
)


def _identity_terms(agent_input: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    title_tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", _plain(item.get("title")))
        if token not in _IDENTITY_STOPWORDS and not token.isdigit()
    }
    # Tenant-scoped seller SKU is not an external product identity.
    raw_codes: list[object] = []
    labels = {
        "part number", "part_number", "mpn", "oem", "oem_code",
        "codigo oem", "código oem",
        "codigo original", "código original", "modelo", "model",
    }
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, Mapping):
            continue
        attribute_labels = {_plain(attribute.get("id")), _plain(attribute.get("name"))}
        if attribute_labels & labels:
            raw_codes.extend((attribute.get("value_name"), attribute.get("value_id")))
    codes = {
        normalized
        for value in raw_codes
        if len(normalized := re.sub(r"[^a-z0-9]", "", _plain(value))) >= 4
        and not _label_metadata_code(normalized)
        and not _missing_fact_value(normalized)
    }
    return title_tokens, codes


def _variation_selection_state(agent_input: Mapping[str, Any]) -> str:
    identity = (
        agent_input.get("product_evidence_identity")
        if isinstance(agent_input.get("product_evidence_identity"), Mapping)
        else {}
    )
    variation_id = str(identity.get("variation_id") or "").strip()
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    variations = [
        variation
        for variation in (item.get("variations") or [])
        if isinstance(variation, Mapping) and str(variation.get("id") or "").strip()
    ]
    if not variations:
        return "selected_unverified" if variation_id else "global"
    if not variation_id:
        return "unresolved_multi"
    if any(str(variation.get("id") or "").strip() == variation_id for variation in variations):
        return "selected"
    return "invalid"


def _selected_variation_tokens(agent_input: Mapping[str, Any]) -> set[str]:
    state = _variation_selection_state(agent_input)
    if state == "global":
        return set()
    if state != "selected":
        return {"__variation_unresolved__"}
    identity = agent_input.get("product_evidence_identity") or {}
    variation_id = str(identity.get("variation_id") or "").strip()
    if not variation_id:
        return {"__variation_unresolved__"}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    for variation in item.get("variations") or []:
        if not isinstance(variation, Mapping) or str(variation.get("id") or "").strip() != variation_id:
            continue
        selectors = {
            selector
            for combination in variation.get("attribute_combinations") or []
            if isinstance(combination, Mapping)
            if len(
                selector := re.sub(
                    r"[^a-z0-9]",
                    "",
                    _plain(combination.get("value_name") or combination.get("value_id")),
                )
            ) >= 2
        }
        if selectors:
            return selectors
        # A local variation SKU is meaningful inside the seller account only;
        # it cannot prove the identity of an external technical page.
        return {"__variation_unresolved__"}
    return {"__variation_unresolved__"}


def _contains_identity_code(text: object, code: str) -> bool:
    if not code:
        return False
    joined = r"[\s._/\\-]*".join(re.escape(character) for character in code)
    return re.search(rf"(?<![a-z0-9]){joined}(?![a-z0-9])", _plain(text)) is not None


def _table_product_block(lines: list[str], index: int) -> str:
    if "|" not in lines[index]:
        return ""
    start = index
    while start > 0 and "|" in lines[start - 1] and lines[start - 1].strip():
        start -= 1
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines[start : index + 1]
        if "|" in line
    ]
    if not rows:
        return ""
    data = rows[-1]
    header = next(
        (
            row
            for row in rows[:-1]
            if len(row) == len(data)
            and not all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell or "") for cell in row)
        ),
        [],
    )
    if header and len(header) == len(data):
        return "\n".join(
            f"{label}: {value}" for label, value in zip(header, data) if label and value
        )
    return lines[index].strip()


def _heading_product_block(lines: list[str], index: int) -> str:
    heading = re.match(r"^\s*(#{1,6})\s+", lines[index])
    if heading is None:
        return ""
    level = len(heading.group(1))
    end = index + 1
    while end < len(lines):
        following = re.match(r"^\s*(#{1,6})\s+", lines[end])
        if following is not None and len(following.group(1)) <= level:
            break
        end += 1
    return "\n".join(lines[index:end]).strip()


def _owning_identity_heading_block(lines: list[str], index: int, codes: set[str]) -> str:
    for heading_index in range(index - 1, -1, -1):
        if not re.match(r"^\s*#{1,6}\s+", lines[heading_index]):
            continue
        if any(_contains_identity_code(lines[heading_index], code) for code in codes):
            return _heading_product_block(lines, heading_index)
        return ""
    return ""


_PRODUCT_ANCHOR_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#{1,6}\s*)?(?:"
    r"(?:codigo|código|code|part\s+number|sku|modelo|model|referencia|referência)"
    r"(?:\s+(?:oem|original|do\s+produto))?\s*[:#=\-]|"
    r"(?:produto|product)\s+(?=\S*[0-9]))",
    re.IGNORECASE,
)


def _labelled_product_block(lines: list[str], index: int) -> str:
    if _PRODUCT_ANCHOR_RE.match(lines[index]) is None:
        return ""
    end = index + 1
    while end < len(lines) and _PRODUCT_ANCHOR_RE.match(lines[end]) is None:
        if re.match(r"^\s*#{1,6}\s+", lines[end]):
            break
        end += 1
    return "\n".join(lines[index:end]).strip()


_PARAGRAPH_CODE_RE = re.compile(
    r"(?<![a-z0-9])([a-z0-9][a-z0-9._/\-]{5,})(?![a-z0-9])",
    re.IGNORECASE,
)
_INLINE_PRODUCT_IDENTITY_RE = re.compile(
    r"(?:^|[.;|]\s+)(?:[-*]\s*)?"
    r"(?:produto|product|c[oó]digo|code|part\s+number|sku|refer[eê]ncia)\b"
    r"\s*(?:oem\s*)?[:#=\-]?\s*([^:;,|\r\n]{1,48})",
    re.IGNORECASE | re.MULTILINE,
)


def _inline_competing_identity(block: str, codes: set[str]) -> bool:
    for match in _INLINE_PRODUCT_IDENTITY_RE.finditer(block):
        segment = str(match.group(1) or "").strip()
        normalized_segment = re.sub(r"[^a-z0-9]", "", segment.casefold())
        if any(code in normalized_segment for code in codes):
            continue
        tokens = re.findall(r"[a-z0-9._/\-]+", segment, flags=re.IGNORECASE)[:2]
        if not tokens:
            continue
        raw_candidate = "".join(tokens)
        candidate = re.sub(r"[^a-z0-9]", "", raw_candidate.casefold())
        plausible_alphabetic_code = bool(
            any(character.isalpha() for character in raw_candidate)
            and raw_candidate == raw_candidate.upper()
        )
        if (
            len(candidate) >= 4
            and (
                any(character.isdigit() for character in candidate)
                or plausible_alphabetic_code
            )
            and candidate not in codes
        ):
            return True
    return False


def _block_has_competing_code(block: str, codes: set[str]) -> bool:
    return _inline_competing_identity(block, codes) or any(
        len(normalized) >= 6
        and sum(character.isdigit() for character in normalized) >= 3
        and normalized not in codes
        for match in _PARAGRAPH_CODE_RE.finditer(_plain(block))
        if (normalized := re.sub(r"[^a-z0-9]", "", match.group(1).casefold()))
    )


def _paragraph_product_block(lines: list[str], index: int, codes: set[str]) -> str:
    start = index
    while start > 0 and lines[start - 1].strip() and not re.match(r"^\s*#{1,6}\s+", lines[start - 1]):
        start -= 1
    end = index + 1
    while end < len(lines) and lines[end].strip() and not re.match(r"^\s*#{1,6}\s+", lines[end]):
        end += 1
    block = "\n".join(lines[start:end]).strip()
    return "" if _block_has_competing_code(block, codes) else block


def _identity_scoped_blocks(
    text: str,
    codes: set[str],
    *,
    allowed_codes: set[str] | None = None,
) -> list[str]:
    lines = text.splitlines() or [text]
    permitted_codes = allowed_codes if allowed_codes is not None else codes
    blocks: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if not any(_contains_identity_code(line, code) for code in codes):
            continue
        block = (
            _table_product_block(lines, index)
            or _heading_product_block(lines, index)
            or _owning_identity_heading_block(lines, index, codes)
            or _labelled_product_block(lines, index)
            or _paragraph_product_block(lines, index, permitted_codes)
        )
        if block and _block_has_competing_code(block, permitted_codes):
            block = ""
        normalized = _plain(block)
        if block and normalized not in seen:
            seen.add(normalized)
            blocks.append(block)
    return blocks


def _block_has_selected_variation(block: str, agent_input: Mapping[str, Any]) -> bool:
    variation_tokens = _selected_variation_tokens(agent_input)
    if "__variation_unresolved__" in variation_tokens:
        return False
    return not variation_tokens or all(
        _contains_identity_code(block, token) for token in variation_tokens
    )


def _variation_scoped_block(
    block: str,
    codes: set[str],
    agent_input: Mapping[str, Any],
    claim_extractor: Callable[[object], Sequence[object]],
) -> str:
    selectors = _selected_variation_tokens(agent_input)
    if not selectors:
        return block
    if "__variation_unresolved__" in selectors:
        return ""
    candidates = [
        candidate
        for candidate in _identity_scoped_blocks(
            block,
            selectors,
            allowed_codes=selectors | codes,
        )
        if _block_has_selected_variation(candidate, agent_input)
    ]
    claiming = [candidate for candidate in candidates if claim_extractor(candidate)]
    if len(claiming) == 1:
        selected = claiming[0]
    elif len(candidates) == 1 and not claim_extractor(block):
        selected = candidates[0]
    else:
        return ""
    if any(_contains_identity_code(selected, code) for code in codes):
        return selected
    identity_line = next(
        (
            line
            for line in block.splitlines()
            if any(_contains_identity_code(line, code) for code in codes)
        ),
        "",
    )
    return f"{identity_line}\n{selected}".strip() if identity_line else ""


def scope_research_text_to_product(
    text: object,
    agent_input: Mapping[str, Any],
    *,
    source_type: str = "technical_independent",
    claim_extractor: Callable[[object], Sequence[object]],
) -> str:
    """Return one structural block linked to the exact product, or fail closed."""

    source = str(text or "")[:600_000].strip()
    if not source:
        return ""
    if source_type == "official_listing":
        return source
    _title_tokens, codes = _identity_terms(agent_input)
    matching_codes = {code for code in codes if _contains_identity_code(source, code)}
    if matching_codes:
        blocks = [
            scoped
            for block in _identity_scoped_blocks(source, matching_codes)
            if (
                scoped := _variation_scoped_block(
                    block,
                    matching_codes,
                    agent_input,
                    claim_extractor,
                )
            )
        ]
        claiming = [block for block in blocks if claim_extractor(block)]
        if len(claiming) == 1:
            return claiming[0]
        if len(claiming) > 1:
            return ""
        if len(blocks) == 1 and not claim_extractor(source):
            return blocks[0]
        return ""
    if source_type in {"official_manufacturer", "official_oem"}:
        return ""
    return source if _block_has_selected_variation(source, agent_input) else ""
