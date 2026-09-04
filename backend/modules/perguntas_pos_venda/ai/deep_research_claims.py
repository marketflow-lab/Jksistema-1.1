"""Technical-claim extraction for bounded public product research."""

from __future__ import annotations

import re

from backend.services.vin_transient import contains_vin_like_identifier

from .deep_research_contracts import (
    TechnicalClaimV1,
    _label_metadata_code,
    _missing_fact_value,
    _plain,
    _safe_text,
)

def _measurement_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    voltage_pattern = re.compile(
        r"\b(tens(?:ao|ão)|voltagem|voltage|entrada|input|saida|saída|output)\s*"
        r"(?:nominal\s*)?[:=\-]?\s*(\d+(?:[.,]\d+)?)\s*(m?v|kv)\b", re.IGNORECASE,
    )
    for match in voltage_pattern.finditer(text):
        label = _plain(match.group(1))
        if label in {"entrada", "input"}:
            field_name = "electrical.input_voltage"
        elif label in {"saida", "output"}:
            field_name = "electrical.output_voltage"
        else:
            field_name = "electrical.voltage"
        claims.append(TechnicalClaimV1(field_name, "product", match.group(2), match.group(3)))
    patterns = (
        ("electrical.current", "product", re.compile(
            r"\b(?:corrente|amperagem|current)\s*(?:nominal\s*)?[:=\-]?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(m?a)\b", re.IGNORECASE)),
        ("electrical.power", "product", re.compile(
            r"\b(?:potencia|potência|power)\s*(?:nominal\s*)?[:=\-]?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(kw|w)\b", re.IGNORECASE)),
        ("electrical.frequency", "product", re.compile(
            r"\b(?:frequencia|frequência|frequency)\s*[:=\-]?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(khz|hz)\b", re.IGNORECASE)),
    )
    for field_name, scope, pattern in patterns:
        for match in pattern.finditer(text):
            claims.append(TechnicalClaimV1(field_name, scope, match.group(1), match.group(2)))
    weight_pattern = re.compile(
        r"\b(peso\s+(?:liquido|líquido|do\s+produto|da\s+peca|da\s+peça)|net\s+weight|"
        r"peso\s+(?:bruto|da\s+embalagem|com\s+embalagem)|gross\s+weight)\s*[:=\-]?\s*"
        r"(\d+(?:[.,]\d+)?)\s*(kg|g|mg)\b", re.IGNORECASE,
    )
    for match in weight_pattern.finditer(text):
        label = _plain(match.group(1))
        package = any(token in label for token in ("bruto", "embalagem", "gross"))
        claims.append(TechnicalClaimV1(
            "weight.package" if package else "weight.product",
            "package" if package else "product", match.group(2), match.group(3),
        ))
    dimension_pattern = re.compile(
        r"\b(dimens(?:oes|ões)|medidas|tamanho|dimensions|dimens(?:oes|ões)\s+da\s+embalagem|"
        r"package\s+dimensions)\s*[:=\-]?\s*"
        r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)"
        r"(?:\s*[x×]\s*(\d+(?:[.,]\d+)?))?\s*(mm|cm|m)\b", re.IGNORECASE,
    )
    for match in dimension_pattern.finditer(text):
        label = _plain(match.group(1))
        package = "embalagem" in label or "package" in label
        values = [match.group(2), match.group(3)]
        if match.group(4):
            values.append(match.group(4))
        claims.append(TechnicalClaimV1(
            "dimensions.package" if package else "dimensions.product",
            "package" if package else "product", "x".join(values) + match.group(5),
        ))
    diameter_pattern = re.compile(
        r"\b(?:diametro|diâmetro|diameter)\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)\s*(mm|cm|m)\b", re.IGNORECASE,
    )
    for match in diameter_pattern.finditer(text):
        claims.append(TechnicalClaimV1("dimensions.diameter", "product", match.group(1), match.group(2)))
    return claims


def _product_descriptor_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    condition_pattern = re.compile(
        r"\b(?:condicao|condição|condition)\s*[:=\-]\s*"
        r"(novo|nova|new|usado|usada|used|recondicionado|recondicionada|"
        r"remanufaturado|remanufaturada|refurbished)\b", re.IGNORECASE,
    )
    for match in condition_pattern.finditer(text):
        claims.append(TechnicalClaimV1("product.condition", "product", _safe_text(match.group(1), 40)))
    material_pattern = re.compile(
        r"\b(?:material|fabricad[oa]\s+em|material\s+de\s+fabricacao|material\s+de\s+fabricação)\s*"
        r"[:=\-]?\s*(aluminio|alumínio|aco\s+inox(?:idavel)?|aço\s+inox(?:idável)?|aco|aço|"
        r"cobre|latao|latão|ferro\s+fundido|abs|polipropileno|nylon|borracha|silicone)\b", re.IGNORECASE,
    )
    for match in material_pattern.finditer(text):
        claims.append(TechnicalClaimV1("construction.material", "product", _safe_text(match.group(1), 80)))
    certifications = {
        value.upper()
        for value in re.findall(
            r"\b(INMETRO|CE|UL|RoHS|ISO\s*9001|ISO\s*14001|ABNT\s*NBR\s*\d{3,6})\b", text, flags=re.IGNORECASE,
        )
    }
    claims.extend(
        TechnicalClaimV1(
            "certification." + re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")[:60],
            "product",
            value,
        )
        for value in sorted(certifications)
    )
    return claims


def _reference_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    patterns = (
        ("oem", re.compile(
            r"\b(?:codigo\s+oem|código\s+oem|oem(?:\s+(?:code|number|numero|número))?)\s*[:#=\-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]{3,31})\b", re.IGNORECASE)),
        ("original", re.compile(
            r"\b(?:codigo\s+original|código\s+original)\s*[:#=\-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]{3,31})\b", re.IGNORECASE)),
        ("part", re.compile(
            r"\bpart\s+number\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9./\-]{3,31})\b", re.IGNORECASE)),
    )
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            code = match.group(1).strip(" .-/")
            plain_code = _plain(code)
            normalized = re.sub(r"[^a-z0-9]", "", _plain(code))
            valid_length = len(code) >= 4 if kind == "oem" else len(normalized) >= 4
            valid = valid_length and not _label_metadata_code(code) and not _missing_fact_value(code)
            valid = valid and (kind != "oem" or not plain_code.isdigit() or len(plain_code) >= 5)
            if not valid:
                continue
            if kind == "oem":
                suffix = re.sub(r"[^a-z0-9]+", "_", _plain(code)).strip("_")[:48]
                field_name = f"reference.oem_code.{suffix}"
            elif kind == "original":
                field_name = "reference.original_code"
            else:
                field_name = "reference.part_number"
            claims.append(TechnicalClaimV1(field_name, "product", code.upper()))
    for field_name, pattern in (
        ("product.brand", re.compile(
            r"\b(?:marca|brand)\s*[:=\-]\s*([A-Z0-9][A-Z0-9 .&+\-/]{1,80})", re.IGNORECASE)),
        ("product.manufacturer", re.compile(
            r"\b(?:fabricante|manufacturer)\s*[:=\-]\s*([A-Z0-9][A-Z0-9 .&+\-/]{1,100})", re.IGNORECASE)),
    ):
        for match in pattern.finditer(text):
            value = re.split(
                r"[;|\r\n.]|\s+-\s+|\s+/\s+(?=(?:marca|brand|fabricante|manufacturer)\b)",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip(" .,-")
            if value and not _missing_fact_value(value):
                claims.append(TechnicalClaimV1(field_name, "product", value))
    return claims


def _interface_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    connector_pins = re.compile(
        r"\b(?:conector|connector)\s*[:=\-]?\s*(?:de\s+)?(\d{1,3})\s*(?:pinos?|pins?)\b|"
        r"\b(\d{1,3})\s*(?:pinos?|pins?)\s+(?:no\s+)?(?:conector|connector)\b", re.IGNORECASE,
    )
    for match in connector_pins.finditer(text):
        claims.append(TechnicalClaimV1(
            "interface.connector_pins", "product", match.group(1) or match.group(2),
        ))
    connector_type = re.compile(
        r"\b(?:tipo\s+de\s+conector|conector|connector)\s*[:=\-]\s*"
        r"(USB(?:\s*[- ]?C)?|TYPE\s*C|MICRO\s*USB|LIGHTNING|HDMI|DISPLAYPORT|"
        r"[A-Z]{1,5}\d{1,5}(?:[A-Z0-9+./-]{0,12})?)\b", re.IGNORECASE,
    )
    for match in connector_type.finditer(text):
        value = _safe_text(match.group(1), 40)
        slug = re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")[:48]
        if slug:
            claims.append(TechnicalClaimV1(f"interface.connector_type.{slug}", "product", value))
    thread_pattern = re.compile(
        r"\b(?:rosca|thread)\s*[:=\-]?\s*"
        r"(M\s*\d{2,3}\s*(?:[x×]\s*\d+(?:[.,]\d+)?)?|"
        r"\d+\s*/\s*\d+\s*(?:pol(?:egadas?)?|in)?)\b", re.IGNORECASE,
    )
    for match in thread_pattern.finditer(text):
        claims.append(TechnicalClaimV1("interface.thread", "product", _safe_text(match.group(1), 40)))
    fixation_pattern = re.compile(
        r"\b(?:fixacao|fixação|mounting|furacao|furação)\s*[:=\-]?\s*(?:por\s+)?"
        r"(\d{1,3})\s*(?:furos?|pontos?|holes?)\b", re.IGNORECASE,
    )
    for match in fixation_pattern.finditer(text):
        claims.append(TechnicalClaimV1("interface.fixation_holes", "product", match.group(1)))
    return claims


def _performance_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    patterns = (
        ("performance.flow_rate", re.compile(
            r"\b(?:vazao|vazão|flow(?:\s+rate)?)\s*[:=\-]?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(l\s*/\s*(?:h|hr|min)|lph|lpm|m3\s*/\s*h)\b", re.IGNORECASE)),
        ("performance.pressure", re.compile(
            r"\b(?:pressao|pressão|pressure)\s*[:=\-]?\s*"
            r"(\d+(?:[.,]\d+)?)\s*(bar|psi|kpa|mpa)\b", re.IGNORECASE)),
        ("performance.lift", re.compile(
            r"\b(?:altura\s+manometrica|altura\s+manométrica|elevacao|elevação|lift|head)"
            r"\s*[:=\-]?\s*(\d+(?:[.,]\d+)?)\s*(m|cm)\b", re.IGNORECASE)),
    )
    for field_name, pattern in patterns:
        for match in pattern.finditer(text):
            unit = re.sub(r"\s+", "", match.group(2))
            claims.append(TechnicalClaimV1(field_name, "product", f"{match.group(1)} {unit}"))
    return claims


_DIRECTED_RELATION_PREDICATES = frozenset({
    "applies_to",
    "equivalent_to",
    "installed_in",
    "mounted_on",
    "part_of",
    "replaces",
    "superseded_by",
})
_RELATION_CODE_RE = re.compile(
    r"(?<![A-Z0-9])(?=[A-Z0-9./\-]{4,40}(?![A-Z0-9]))"
    r"(?=[A-Z0-9./\-]*[A-Z])(?=[A-Z0-9./\-]*\d)"
    r"[A-Z0-9]+(?:[./\-][A-Z0-9]+)+(?![A-Z0-9])",
    re.IGNORECASE,
)
_LABELLED_RELATION_SUBJECT_RE = re.compile(
    r"\b(?:part\s+number|oem(?:\s+(?:code|number))?|c[oó]digo\s+(?:oem|original)|"
    r"refer[eê]ncia)\s*[:#=\-]?\s*"
    r"([A-Z0-9][A-Z0-9./\-]{3,31})\b",
    re.IGNORECASE,
)


def _canonical_relation_code(value: object) -> str:
    code = _safe_text(value, 48).strip(" ./-").upper()
    compact = re.sub(r"[^A-Z0-9]", "", code)
    if (
        len(compact) < 4
        or not any(character.isdigit() for character in compact)
        or len(compact) == 17
        or contains_vin_like_identifier(code)
        or _label_metadata_code(code)
        or _missing_fact_value(code)
    ):
        return ""
    return code


def _directed_relation_claim(
    predicate: str,
    subject: object,
    relation_object: object,
    *,
    scope: str,
) -> TechnicalClaimV1 | None:
    """Encode one directed edge atomically in the existing claim schema.

    ``field_name`` retains the predicate and canonical subject key, ``value``
    retains the canonical object, and the repository's claim-source join later
    supplies provenance. Keeping the edge in one claim prevents unrelated
    subject/predicate/object rows from being recombined.
    """

    normalized_predicate = str(predicate or "").strip().lower()
    if normalized_predicate not in _DIRECTED_RELATION_PREDICATES:
        return None
    canonical_subject = _canonical_relation_code(subject)
    value = _safe_text(relation_object, 200).strip(" .,-")
    if (
        not canonical_subject
        or not value
        or canonical_subject.casefold() == value.casefold()
        or contains_vin_like_identifier(value)
        or "_PROTEGIDO]" in value.upper()
        or _missing_fact_value(value)
    ):
        return None
    subject_key = re.sub(
        r"[^a-z0-9]+", "_", _plain(canonical_subject),
    ).strip("_")[:48]
    if not subject_key:
        return None
    return TechnicalClaimV1(
        f"relation.{normalized_predicate}.{subject_key}",
        scope,
        value,
    )


def _nearest_labelled_relation_subject(lines: list[str], index: int) -> str:
    for prior in range(index, max(-1, index - 4), -1):
        match = _LABELLED_RELATION_SUBJECT_RE.search(lines[prior])
        if match:
            return _canonical_relation_code(match.group(1))
    return ""


def _directed_relation_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    superseded_by_pattern = re.compile(
        r"\b([A-Z0-9][A-Z0-9./\-]{3,31})\s+"
        r"(?:foi\s+)?(?:substitu[ií]d[oa]\s+por|superseded\s+by|replaced\s+by)\s+"
        r"([A-Z0-9][A-Z0-9./\-]{3,31})\b",
        re.IGNORECASE,
    )
    replaces_pattern = re.compile(
        r"\b([A-Z0-9][A-Z0-9./\-]{3,31})\s+"
        r"(?:substitui|supersedes|replaces)\s+"
        r"([A-Z0-9][A-Z0-9./\-]{3,31})\b",
        re.IGNORECASE,
    )
    for match in superseded_by_pattern.finditer(text):
        claim = _directed_relation_claim(
            "superseded_by",
            match.group(1),
            _canonical_relation_code(match.group(2)),
            scope="product",
        )
        if claim is not None:
            claims.append(claim)
    for match in replaces_pattern.finditer(text):
        claim = _directed_relation_claim(
            "replaces",
            match.group(1),
            _canonical_relation_code(match.group(2)),
            scope="product",
        )
        if claim is not None:
            claims.append(claim)

    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    location_pattern = re.compile(
        r"\b(?:instalad[oa]|montad[oa]|installed|mounted|located)\s+"
        r"(?:em|na|no|junto\s+(?:a|ao)|inside|in|on|at)\s+"
        r"([^.;\r\n]{3,140})",
        re.IGNORECASE,
    )
    for index, line in enumerate(lines):
        match = location_pattern.search(line)
        if not match:
            continue
        claim = _directed_relation_claim(
            "installed_in",
            _nearest_labelled_relation_subject(lines, index),
            match.group(1),
            scope="application",
        )
        if claim is not None:
            claims.append(claim)

    # A table relation is emitted only when labelled columns identify both
    # subject and object. Positional guesses cannot become reusable evidence.
    for index, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        headings = [_plain(cell).strip() for cell in line.split("|")]
        try:
            subject_index = next(
                position
                for position, heading in enumerate(headings)
                if "part number" in heading
                or "codigo oem" in heading
                or "codigo original" in heading
            )
            location_index = next(
                position
                for position, heading in enumerate(headings)
                if "installation location" in heading
                or "local de instalacao" in heading
                or "localizacao de instalacao" in heading
            )
        except StopIteration:
            continue
        for row in lines[index + 1:index + 7]:
            if "|" not in row:
                break
            cells = [cell.strip() for cell in row.split("|")]
            if max(subject_index, location_index) >= len(cells):
                continue
            subject_match = _RELATION_CODE_RE.search(cells[subject_index])
            subject = subject_match.group(0) if subject_match else cells[subject_index]
            claim = _directed_relation_claim(
                "installed_in",
                subject,
                cells[location_index],
                scope="application",
            )
            if claim is not None:
                claims.append(claim)
    return claims


def _kit_installation_claims(text: str) -> list[TechnicalClaimV1]:
    claims: list[TechnicalClaimV1] = []
    kit_pattern = re.compile(
        r"\b(?:kit\s+(?:contem|contém|inclui)|conteudo\s+do\s+kit|conteúdo\s+do\s+kit|"
        r"package\s+includes)\s*[:=\-]?\s*([^.;\r\n]{3,240})", re.IGNORECASE,
    )
    for match in kit_pattern.finditer(text):
        parts = re.split(r"\s*(?:,|\+|\be\b|\band\b)\s*", match.group(1), flags=re.IGNORECASE)
        for part in parts[:16]:
            value = _safe_text(part, 80).strip(" .,-")
            slug = re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")[:48]
            if slug:
                claims.append(TechnicalClaimV1(f"kit.content.{slug}", "kit", value))
    cross_reference_pattern = re.compile(
        r"\b(?:referencia\s+cruzada|referência\s+cruzada|cross[ -]?reference|substitui|"
        r"replacement\s+for)\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9./\-]{3,31})\b", re.IGNORECASE,
    )
    for match in cross_reference_pattern.finditer(text):
        value = match.group(1).strip(" .-/").upper()
        slug = re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")[:48]
        if slug and not _label_metadata_code(value) and not _missing_fact_value(value):
            claims.append(TechnicalClaimV1(f"reference.cross_reference.{slug}", "product", value))
    location_patterns = (
        re.compile(
            r"\b(?:local(?:iza[cç][aã]o)?\s+de\s+instala[cç][aã]o|installation\s+location)"
            r"\s*[:=\-]\s*([^.;\r\n]{3,140})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:instalad[oa]|montad[oa]|installed|mounted|located|fits)\s+"
            r"(?:em|na|no|junto\s+(?:a|ao)|inside|in|on|at)\s+([^.;\r\n]{3,140})",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:vai|fica)\s+na\s+([^.;\r\n]{3,120})", re.IGNORECASE),
    )
    for pattern in location_patterns:
        for match in pattern.finditer(text):
            value = _safe_text(match.group(1), 120).strip(" .,-")
            if value and not _missing_fact_value(value):
                claims.append(TechnicalClaimV1("installation.location", "application", value))
    assembly_patterns = (
        re.compile(
            r"\b(?:montagem|conjunto|assembly|assembly\s+name)\s*[:=\-]\s*"
            r"([^.;\r\n]{3,140})",
            re.IGNORECASE,
        ),
        re.compile(r"\b([A-Z][A-Z0-9 ,./\-]{2,80}\bASSY\.?[A-Z0-9 ,./\-]{0,60})"),
    )
    for pattern in assembly_patterns:
        for match in pattern.finditer(text):
            value = _safe_text(match.group(1), 120).strip(" .,-")
            if value and not _missing_fact_value(value):
                claims.append(TechnicalClaimV1("installation.assembly", "application", value))
    requirement_pattern = re.compile(
        r"\b(?:requer|necessita|requires?|installation\s+requires?)\s*[:=\-]?\s*"
        r"([^.;\r\n]{3,160})", re.IGNORECASE,
    )
    for match in requirement_pattern.finditer(text):
        value = _safe_text(match.group(1), 120).strip(" .,-")
        slug = re.sub(r"[^a-z0-9]+", "_", _plain(value)).strip("_")[:48]
        if slug:
            claims.append(TechnicalClaimV1(f"installation.requirement.{slug}", "product", value))
    return claims


def _categorical_claims(text: str) -> list[TechnicalClaimV1]:
    return [
        *_product_descriptor_claims(text),
        *_reference_claims(text),
        *_interface_claims(text),
        *_performance_claims(text),
        *_directed_relation_claims(text),
        *_kit_installation_claims(text),
    ]


def extract_technical_claims(text: object) -> list[TechnicalClaimV1]:
    """Extract only label-bound product facts; generic prose never becomes a claim."""

    source = str(text or "")[:600_000]
    if not source:
        return []
    claims = [*_measurement_claims(source), *_categorical_claims(source)]
    unique: dict[tuple[str, str, str, str], TechnicalClaimV1] = {}
    for claim in claims:
        unique.setdefault(claim.signature, claim)
    return list(unique.values())[:80]
