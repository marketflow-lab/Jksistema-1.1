"""Pure analysis for bounded public product research.

This module classifies sources, extracts normalized technical claims and
evaluates whether documents belong to the advertised product.  Network access,
document construction and evidence persistence deliberately live elsewhere.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from backend.services.vin_transient import contains_vin_like_identifier

from .deep_research_contracts import (
    _BLOG_PATH_PARTS,
    _CURATED_TECHNICAL_DISTRIBUTOR_DOMAINS,
    _CURATED_TECHNICAL_INDEPENDENT_DOMAINS,
    _FORUM_HOST_PARTS,
    _IDENTITY_STOPWORDS,
    _MARKETPLACE_HOSTS,
    ResearchDocumentV1,
    TechnicalClaimV1,
    _domain,
    _label_metadata_code,
    _missing_fact_value,
    _plain,
    _safe_text,
)
from .official_source_registry import official_domains_for_brand
from .deep_research_scoping import (
    _contains_identity_code,
    _identity_terms,
    _selected_variation_tokens,
    _variation_selection_state,
    scope_research_text_to_product as _scope_research_text_to_product,
)


from .deep_research_claims import (
    _categorical_claims,
    _directed_relation_claims,
    _interface_claims,
    _kit_installation_claims,
    _measurement_claims,
    _performance_claims,
    _product_descriptor_claims,
    _reference_claims,
    extract_technical_claims,
)


def scope_research_text_to_product(
    text: object,
    agent_input: Mapping[str, Any],
    *,
    source_type: str = "technical_independent",
) -> str:
    return _scope_research_text_to_product(
        text,
        agent_input,
        source_type=source_type,
        claim_extractor=extract_technical_claims,
    )


def _item_attribute(agent_input: Mapping[str, Any], *names: str) -> str:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    wanted = {_plain(name) for name in names}
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, Mapping):
            continue
        keys = {_plain(attribute.get("id")), _plain(attribute.get("name"))}
        if keys & wanted:
            return _safe_text(attribute.get("value_name"), 120)
    return ""


def _brand_tokens(agent_input: Mapping[str, Any]) -> set[str]:
    vehicle = (
        agent_input.get("vehicle_identity")
        if isinstance(agent_input.get("vehicle_identity"), Mapping)
        else {}
    )
    values = [
        _item_attribute(agent_input, "brand", "marca", "manufacturer", "fabricante"),
        vehicle.get("make"),
        vehicle.get("manufacturer"),
    ]
    tokens: set[str] = set()
    for value in values:
        tokens.update(
            token
            for token in re.findall(r"[a-z0-9]{3,}", _plain(value))
            if token not in _IDENTITY_STOPWORDS
        )
    return tokens


def _host_matches_official_brand(host: str, brand_tokens: set[str]) -> bool:
    for token in brand_tokens:
        compact = re.sub(r"[^a-z0-9]", "", token)
        for root in official_domains_for_brand(compact):
            if host == root or host.endswith("." + root):
                return True
    return False


def _host_matches_roots(host: str, roots: Iterable[str]) -> bool:
    return any(host == root or host.endswith("." + root) for root in roots)


def classify_research_source(
    item: Mapping[str, Any],
    url: object,
    agent_input: Mapping[str, Any],
    *,
    query_type: str = "",
) -> str:
    """Classify conservatively; a search result cannot declare itself official."""

    host = _domain(url)
    path = _plain(urlsplit(str(url or "")).path if host else "")
    if any(part in host for part in _MARKETPLACE_HOSTS):
        return "marketplace"
    if any(part in host for part in _FORUM_HOST_PARTS):
        return "forum"
    if any(part in path for part in _BLOG_PATH_PARTS):
        return "blog"
    if _host_matches_official_brand(host, _brand_tokens(agent_input)):
        return "official_manufacturer"
    if _host_matches_roots(host, _CURATED_TECHNICAL_DISTRIBUTOR_DOMAINS):
        return "technical_distributor"
    technical_text = _plain(
        " ".join(
            str(item.get(field) or "")
            for field in ("title", "provider", "source", "snippet", "description")
        )
    )
    if _host_matches_roots(host, _CURATED_TECHNICAL_INDEPENDENT_DOMAINS) and any(
        marker in technical_text
        for marker in (
            "manual tecnico",
            "technical manual",
            "datasheet",
            "data sheet",
            "ficha tecnica",
            "catalogo tecnico",
            "technical catalog",
            "especificacoes tecnicas",
        )
    ):
        return "technical_independent"
    return "blog"


def _decisive_title_identity_tokens(agent_input: Mapping[str, Any]) -> set[str]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    title = _plain(item.get("title"))
    tokens = {
        re.sub(r"[^a-z0-9]", "", token)
        for token in re.findall(r"[a-z0-9./+-]+", title)
        if any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
    }
    for match in re.finditer(
        r"\b\d+(?:[.,]\d+)?\s*(?:v|w|a|hz|khz|l\s*/\s*h|lph|lpm|bar|psi|mm|cm)\b",
        title,
    ):
        tokens.add(re.sub(r"[^a-z0-9]", "", match.group(0)))
    return {token for token in tokens if len(token) >= 2}


def _product_oem_codes(agent_input: Mapping[str, Any]) -> set[str]:
    """Return only product-side codes explicitly identified as OEM/original."""

    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    codes: set[str] = set()
    for attribute in item.get("attributes") or []:
        if not isinstance(attribute, Mapping):
            continue
        label = _plain(f"{attribute.get('id') or ''} {attribute.get('name') or ''}")
        if not any(marker in label for marker in ("oem", "codigo original", "código original")):
            continue
        normalized = re.sub(
            r"[^a-z0-9]",
            "",
            _plain(attribute.get("value_name") or attribute.get("value_id")),
        )
        if len(normalized) >= 4 and not _label_metadata_code(normalized):
            codes.add(normalized)
    labelled_text = " ".join(str(item.get(field) or "") for field in ("title", "description"))
    for match in re.finditer(
        r"\b(?:codigo\s+oem|código\s+oem|codigo\s+original|código\s+original|oem)"
        r"\s*[:#=\-]?\s*([A-Z0-9][A-Z0-9./\-]{3,31})\b",
        labelled_text,
        flags=re.IGNORECASE,
    ):
        normalized = re.sub(r"[^a-z0-9]", "", _plain(match.group(1)))
        if len(normalized) >= 4 and not _label_metadata_code(normalized):
            codes.add(normalized)
    for fact in agent_input.get("verified_product_evidence") or []:
        if not isinstance(fact, Mapping) or not str(fact.get("field_name") or "").startswith(
            "reference.oem_code."
        ):
            continue
        normalized = re.sub(r"[^a-z0-9]", "", _plain(fact.get("value")))
        if len(normalized) >= 4 and not _label_metadata_code(normalized):
            codes.add(normalized)
    return codes


def document_matches_product(
    text: object,
    agent_input: Mapping[str, Any],
    *,
    source_type: str = "technical_independent",
) -> bool:
    scoped = scope_research_text_to_product(
        text,
        agent_input,
        source_type=source_type,
    )
    if not scoped:
        return False
    normalized = _plain(scoped)
    title_tokens, codes = _identity_terms(agent_input)
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    exact_code_match = any(_contains_identity_code(scoped, code) for code in codes)
    if source_type in {"official_manufacturer", "official_oem"}:
        identity_match = exact_code_match
    elif exact_code_match:
        identity_match = True
    else:
        decisive_tokens = _decisive_title_identity_tokens(agent_input)
        document_tokens = set(re.findall(r"[a-z0-9]{3,}", normalized))
        descriptive_tokens = {
            token
            for token in title_tokens
            if token not in decisive_tokens and not any(character.isdigit() for character in token)
        }
        required_descriptive = min(2, len(descriptive_tokens))
        brand_tokens = _brand_tokens(agent_input)
        identity_match = (
            bool(decisive_tokens)
            and all(token in compact for token in decisive_tokens)
            and len(descriptive_tokens & document_tokens) >= required_descriptive
            and (not brand_tokens or bool(brand_tokens & document_tokens))
        )
    if not identity_match:
        return False
    variation_tokens = _selected_variation_tokens(agent_input)
    if "__variation_unresolved__" in variation_tokens:
        return False
    return not variation_tokens or all(
        _contains_identity_code(scoped, token) for token in variation_tokens
    )


def extract_guarded_commercial_claims(
    document: ResearchDocumentV1,
    agent_input: Mapping[str, Any],
) -> list[TechnicalClaimV1]:
    """Create fitment/originality claims only from a complete official linkage."""

    if document.source_type not in {"official_manufacturer", "official_oem"}:
        return []
    normalized = _plain(document.text)
    claims: list[TechnicalClaimV1] = []
    identity = (
        agent_input.get("vehicle_identity")
        if isinstance(agent_input.get("vehicle_identity"), Mapping)
        else {}
    )
    make = _plain(identity.get("make") or identity.get("manufacturer"))
    model = _plain(identity.get("model"))
    year = _plain(identity.get("model_year"))
    product_codes = _product_oem_codes(agent_input)
    document_oem_codes = {
        re.sub(r"[^a-z0-9]", "", _plain(claim.value))
        for claim in extract_technical_claims(document.text)
        if claim.field_name.startswith("reference.oem_code.")
    }
    shared_codes = sorted(code for code in product_codes & document_oem_codes if len(code) >= 4)
    compatibility_language = any(
        marker in normalized
        for marker in (
            "compativel",
            "aplicacao",
            "aplica se",
            "serve para",
            "fits",
            "suitable for",
            "replacement for",
        )
    )
    configuration_values = [
        _plain(identity.get(field))
        for field in (
            "series",
            "body_class",
            "engine_model",
            "engine_displacement_l",
            "fuel_type",
        )
        if _plain(identity.get(field))
    ]
    complete_configuration_link = all(value in normalized for value in configuration_values)
    if (
        str(identity.get("status") or "") == "confirmed"
        and make
        and model
        and year
        and make in normalized
        and model in normalized
        and year in normalized
        and shared_codes
        and compatibility_language
        and complete_configuration_link
    ):
        claims.append(
            TechnicalClaimV1(
                "compatibility.vehicle_oem_product_link",
                "application",
                f"{make} {model} {year}|oem:{shared_codes[0]}",
            )
        )
    if re.search(r"\b(?:genuine|peca\s+genuina|peça\s+genuína|original\s+(?:part|oem))\b", normalized):
        claims.append(TechnicalClaimV1("originality.status", "product", "original"))
    if re.search(
        r"\b(?:equivalent\s+to\s+original|original\s+standard|padroes?\s+(?:da\s+)?original|padrões?\s+(?:da\s+)?original)\b",
        normalized,
    ):
        claims.append(TechnicalClaimV1("quality.original_standard", "product", "confirmed"))
    return claims


def expected_fields_from_question(question: object) -> set[str]:
    text = _plain(question)
    expected: set[str] = set()
    mapping = {
        "electrical.current": ("corrente", "amperagem", "amperes"),
        "electrical.power": ("potencia", "watts"),
        "electrical.frequency": ("frequencia", "hertz"),
        "compatibility": ("compativel", "serve", "servir", "aplicacao", "encaixa"),
        "reference.oem_code": ("codigo oem", "codigo original", "part number"),
    }
    for field_name, terms in mapping.items():
        if any(term in text for term in terms):
            expected.add(field_name)
    if any(term in text for term in ("voltagem", "tensao", "volts")):
        if "entrada" in text or "input" in text:
            expected.add("electrical.input_voltage")
        if "saida" in text or "output" in text:
            expected.add("electrical.output_voltage")
        if not ({"electrical.input_voltage", "electrical.output_voltage"} & expected):
            expected.add("electrical.voltage")
    if "peso" in text:
        expected.add("weight.package" if "embalagem" in text or "bruto" in text else "weight.product")
    if "diametro" in text:
        expected.add("dimensions.diameter")
    if any(term in text for term in ("tamanho", "dimensao", "dimensoes", "medida", "medidas")):
        expected.add("dimensions.package" if "embalagem" in text else "dimensions.product")
    return expected


def expected_fields_for_research(agent_input: Mapping[str, Any]) -> set[str]:
    """Combine the buyer's question with conservative category-applicable fields."""

    question = (
        agent_input.get("question", {}).get("text")
        if isinstance(agent_input.get("question"), Mapping)
        else ""
    )
    expected = expected_fields_from_question(question)
    intent = agent_input.get("intent") if isinstance(agent_input.get("intent"), Mapping) else {}
    compatibility = (
        intent.get("compatibilidade")
        if isinstance(intent.get("compatibilidade"), Mapping)
        else {}
    )
    categories = intent.get("categorias")
    if isinstance(categories, (str, bytes)):
        normalized_categories = {_plain(categories)}
    elif isinstance(categories, Iterable):
        normalized_categories = {_plain(value) for value in categories}
    else:
        normalized_categories = set()
    classified_as_compatibility = (
        _plain(intent.get("categoria")) == "compatibility"
        or "compatibility" in normalized_categories
        or compatibility.get("aplicavel") is True
        or bool(compatibility.get("target_item") or compatibility.get("target_vehicle"))
    )
    if classified_as_compatibility:
        expected.add("compatibility")
    item = agent_input.get("item") if isinstance(agent_input.get("item"), Mapping) else {}
    product_text = _plain(
        " ".join(
            str(value or "")
            for value in (
                item.get("title"),
                item.get("category_id"),
                item.get("domain_id"),
                " ".join(
                    f"{attribute.get('name') or attribute.get('id') or ''} {attribute.get('value_name') or ''}"
                    for attribute in item.get("attributes") or []
                    if isinstance(attribute, Mapping)
                ),
            )
        )
    )
    expected.update({"product.brand", "weight.product", "dimensions.product", "construction.material"})
    if any(
        token in product_text
        for token in (
            "eletric",
            "eletron",
            "bateria",
            "bomba",
            "motor",
            "sensor",
            "lampada",
            "fonte",
            "carregador",
            "voltagem",
            "tensao",
        )
    ):
        expected.add("electrical.voltage")
    electrically_driven = any(
        token in product_text
        for token in ("bomba", "compressor", "motor", "lampada", "fonte", "carregador")
    )
    if electrically_driven or re.search(r"\b\d+(?:[.,]\d+)?\s*w\b", product_text):
        expected.add("electrical.power")
    if electrically_driven or re.search(r"\b\d+(?:[.,]\d+)?\s*a\b", product_text):
        expected.add("electrical.current")
    if any(token in product_text for token in ("hz", "hertz", "monofas", "trifas")):
        expected.add("electrical.frequency")
    if any(token in product_text for token in ("bomba", "compressor", "valvula", "hidraulic")):
        expected.update(
            {
                "performance.flow_rate",
                "performance.pressure",
                "performance.lift",
                "dimensions.diameter",
                "interface.connector",
                "interface.fixation_holes",
                "installation.requirement",
            }
        )
    if any(token in product_text for token in ("conector", "plug", "chicote", "sensor", "modulo")):
        expected.add("interface.connector")
    if "kit" in product_text:
        expected.add("kit.content")
    vehicle = (
        agent_input.get("vehicle_identity")
        if isinstance(agent_input.get("vehicle_identity"), Mapping)
        else {}
    )
    if vehicle or any(
        token in product_text for token in ("veiculo", "automot", "peugeot", "ford", "fiat", "renault")
    ):
        expected.update({"reference.oem_code", "compatibility"})
    return expected


def _field_is_covered(expected: str, verified_fields: set[str]) -> bool:
    if expected == "compatibility":
        return any(value.startswith("compatibility.") for value in verified_fields)
    if expected == "reference.oem_code":
        return any(value.startswith("reference.oem_code.") for value in verified_fields)
    if expected == "electrical.voltage":
        return any(
            value in verified_fields
            for value in (
                "electrical.voltage",
                "electrical.input_voltage",
                "electrical.output_voltage",
            )
        )
    if expected == "interface.connector":
        return any(
            value == expected
            or value.startswith(expected + ".")
            or value.startswith(expected + "_")
            for value in verified_fields
        )
    if expected in {"kit.content", "installation.requirement"}:
        return any(value.startswith(expected + ".") for value in verified_fields)
    return expected in verified_fields
