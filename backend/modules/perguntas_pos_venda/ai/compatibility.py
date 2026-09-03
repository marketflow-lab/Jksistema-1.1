"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from .runtime import (
    Any,
    Optional,
    _favoritos_normalizar_sem_acentos,
    _perguntas_ia_v2_grounding_texto,
    copy,
    normalize_comparison_attributes,
    normalize_profile,
    normalize_target_type,
    re,
)
from .evidence import (
    _perguntas_ia_v2_compatibilidade_padrao,
    _perguntas_ia_v2_evidencias_normalizar,
    _perguntas_ia_v2_grounding_url_key,
)
from .sources import (
    _perguntas_ia_v2_recorte_confirma_interface,
)
from .deep_research_contracts import technical_assertion_occurrence_is_positive

def _perguntas_ia_v2_evidencia_texto(itens: list[dict[str, Any]]) -> str:
    return " ".join(
        str(item.get(campo) or "")
        for item in itens
        for campo in ("reference", "fact", "claim", "snippet", "title")
    )

def _perguntas_ia_v2_termos_interface(texto: object) -> set[str]:
    stopwords = {
        "base", "suporte", "interface", "encaixe", "produto", "veiculo", "moto", "carro",
        "original", "preparacao", "compativel", "compatibilidade", "posterior", "modelo",
        "maquina", "ferramenta", "aparelho", "equipamento", "universal",
    }
    texto_normalizado = _favoritos_normalizar_sem_acentos(str(texto or ""))
    tokens = set(_perguntas_ia_v2_grounding_texto(texto).split())
    termos = {
        token for token in tokens
        if token not in stopwords
        and (len(token) >= 4 or bool(re.search(r"\d", token)) or token in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"})
    }
    for numero, unidade in re.findall(
        r"\b(\d+(?:[.,]\d+)?)\s*(mm|cm|pol(?:egadas?)?|in|v|volts?|hz|w|watts?|a|amperes?|bar|psi)\b",
        texto_normalizado,
    ):
        unidade_norm = {
            "pol": "in", "polegada": "in", "polegadas": "in", "volt": "v", "volts": "v",
            "watt": "w", "watts": "w", "ampere": "a", "amperes": "a",
        }.get(unidade, unidade)
        termos.add(numero.replace(",", ".") + unidade_norm)
    termos.update(re.findall(r"\bm\d{2,3}\b", texto_normalizado))
    return termos

def _perguntas_ia_v2_termos_identificam_interface(termos: set[str]) -> bool:
    familias = {
        "navigator", "garmin", "usb", "lightning", "micro", "typec", "canbus", "bluetooth",
        "carplay", "androidauto", "magsafe", "mount", "cradle", "socket", "plug", "pino", "pin",
        "eixo", "haste", "estria", "estrias", "dente", "dentes", "rosca", "diametro", "flange",
        "furacao", "furos", "fixacao", "hdmi", "displayport", "wifi", "tensao", "voltagem",
        "frequencia", "potencia", "pressao", "protocolo",
    }
    unidades_tecnicas = re.compile(r"^(?:m\d+|\d+(?:mm|cm|in|pol|v|hz|w|a|bar|psi|pinos?|pins?))$", re.IGNORECASE)
    return bool(termos & familias) or any(bool(unidades_tecnicas.search(termo)) for termo in termos)

def _perguntas_ia_v2_grounding_recorte_interface(texto: object, descricao: object) -> str:
    bruto = str(texto or "").strip()
    if not bruto:
        return ""
    termos_descricao = _perguntas_ia_v2_termos_interface(descricao)
    familias_preferidas = {
        "navigator", "garmin", "usb", "lightning", "typec", "canbus", "bluetooth", "carplay",
        "androidauto", "magsafe", "mount", "cradle", "socket", "plug", "pino", "pin",
        "eixo", "haste", "estria", "estrias", "rosca", "diametro", "flange", "furacao",
        "fixacao", "hdmi", "displayport", "wifi", "tensao", "voltagem", "frequencia",
        "potencia", "pressao", "protocolo",
    }
    familias_descricao = termos_descricao & familias_preferidas
    partes = [
        re.sub(r"\s+", " ", parte).strip()
        for parte in re.split(r"(?<=[.!?])\s+|[\r\n]+", bruto)
        if re.sub(r"\s+", " ", parte).strip()
    ]
    candidatos: list[tuple[int, int, str]] = []
    for indice, parte in enumerate(partes):
        termos_parte = _perguntas_ia_v2_termos_interface(parte)
        compartilhados = termos_descricao & termos_parte
        if len(compartilhados) < 2 or not _perguntas_ia_v2_termos_identificam_interface(compartilhados):
            continue
        if familias_descricao and not (compartilhados & familias_descricao):
            continue
        bonus_decisao = 3 if _perguntas_ia_v2_recorte_confirma_interface(parte) else 0
        bonus_familia = len(compartilhados & familias_preferidas) * 10
        candidatos.append((len(compartilhados) + bonus_decisao + bonus_familia, -indice, parte))
    if not candidatos:
        return ""
    candidatos.sort(reverse=True)
    melhor = candidatos[0][2]
    if len(melhor) <= 800:
        return melhor
    normalizado = _perguntas_ia_v2_grounding_texto(melhor)
    ordem_ancoras = (
        "navigator", "garmin", "usb", "lightning", "typec", "canbus", "carplay", "androidauto",
        "magsafe", "mount", "cradle", "socket", "plug", "conector", "connector", "pino", "pin",
        "eixo", "haste", "estria", "estrias", "rosca", "diametro", "flange", "furacao",
        "fixacao", "hdmi", "displayport", "wifi", "tensao", "voltagem", "frequencia",
        "potencia", "pressao", "protocolo",
    )
    termo_ancora = next(
        (termo for termo in ordem_ancoras if termo in termos_descricao and termo in normalizado),
        "",
    )
    if not termo_ancora:
        termo_ancora = next(
            (
                termo
                for termo in sorted(termos_descricao, key=lambda valor: (-len(valor), valor))
                if re.search(r"\d", termo) and termo in normalizado
            ),
            "",
        )
    if not termo_ancora:
        return melhor[:800]
    match = re.search(re.escape(termo_ancora), _favoritos_normalizar_sem_acentos(melhor))
    centro = match.start() if match else 0
    inicio = max(0, centro - 300)
    return melhor[inicio:inicio + 800].strip()

def _perguntas_ia_v2_grounding_evidencia_interface(
    grupo: str,
    descricao: object,
    grounding: dict[str, Any],
) -> Optional[dict[str, Any]]:
    termos_descricao = _perguntas_ia_v2_termos_interface(descricao)
    if len(termos_descricao) < 2:
        return None
    candidatos = grounding.get(grupo) if isinstance(grounding.get(grupo), list) else []
    melhores: list[tuple[int, dict[str, Any], str]] = []
    for fonte in candidatos:
        if not isinstance(fonte, dict) or (grupo == "target_vehicle" and fonte.get("marketplace")):
            continue
        termos_fonte = _perguntas_ia_v2_termos_interface(fonte.get("text_norm") or fonte.get("text"))
        compartilhados = termos_descricao & termos_fonte
        if len(compartilhados) < 2 or not _perguntas_ia_v2_termos_identificam_interface(compartilhados):
            continue
        recorte = _perguntas_ia_v2_grounding_recorte_interface(fonte.get("text"), descricao)
        if not recorte:
            continue
        autoridade = _favoritos_normalizar_sem_acentos(str(fonte.get("authority") or ""))
        bonus = 4 if autoridade in {
            "official_document",
            "internal_listing",
            "approved_internal_memory",
            "context_hub_canonical",
            "context_hub_verified",
        } else 0
        melhores.append((len(compartilhados) + bonus, fonte, recorte))
    if not melhores:
        return None
    melhores.sort(key=lambda item: item[0], reverse=True)
    _, fonte, recorte = melhores[0]
    evidencia = {
        "source_type": fonte.get("source_type") or "collected_source",
        "authority": fonte.get("authority") or "collected_source",
        "reference": recorte[:800],
        "grounded": True,
    }
    if fonte.get("url"):
        evidencia["url"] = fonte.get("url")
    return evidencia

def _perguntas_ia_v2_equivalencia_explicita(
    decisao: str,
    evidencias_produto: list[dict[str, Any]],
    evidencias_alvo: list[dict[str, Any]],
    evidencias_equivalencia: list[dict[str, Any]],
) -> bool:
    if not evidencias_equivalencia:
        return False
    texto_equivalencia = _perguntas_ia_v2_grounding_texto(_perguntas_ia_v2_evidencia_texto(evidencias_equivalencia))
    negativos = (
        "incompativel", "nao compativel", "nao encaixa", "nao serve", "interface diferente",
        "conector diferente", "nao suporta", "not compatible", "does not fit",
    )
    if decisao == "no":
        return any(marcador in texto_equivalencia for marcador in negativos)
    positivos = (
        "mesma interface", "mesmo encaixe", "compativel", "encaixa", "serve", "equivalente",
        "aceita", "suporta", "fits", "compatible",
    )
    if any(marcador in texto_equivalencia for marcador in positivos):
        return True
    termos_produto = _perguntas_ia_v2_termos_interface(_perguntas_ia_v2_evidencia_texto(evidencias_produto))
    termos_alvo = _perguntas_ia_v2_termos_interface(_perguntas_ia_v2_evidencia_texto(evidencias_alvo))
    termos_equivalencia = _perguntas_ia_v2_termos_interface(texto_equivalencia)
    compartilhados = termos_produto & termos_alvo
    return bool(compartilhados and (compartilhados & termos_equivalencia))

def _perguntas_ia_v2_equivalencia_derivada(
    valor_bruto: Any,
    evidencias_produto: list[dict[str, Any]],
    evidencias_alvo: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    origem = valor_bruto if isinstance(valor_bruto, dict) else {}
    candidatos = origem.get("equivalence") if isinstance(origem.get("equivalence"), list) else []
    termos_produto = _perguntas_ia_v2_termos_interface(_perguntas_ia_v2_evidencia_texto(evidencias_produto))
    termos_alvo = _perguntas_ia_v2_termos_interface(_perguntas_ia_v2_evidencia_texto(evidencias_alvo))
    compartilhados = termos_produto & termos_alvo
    if len(compartilhados) < 2 or not _perguntas_ia_v2_termos_identificam_interface(compartilhados):
        return None
    for item in candidatos[:8]:
        if isinstance(item, str):
            registro = {"reference": item}
        elif isinstance(item, dict):
            registro = dict(item)
        else:
            continue
        if str(registro.get("url") or "").strip():
            continue
        texto = _perguntas_ia_v2_grounding_texto(" ".join(
            str(registro.get(campo) or "") for campo in ("reference", "fact", "claim", "snippet")
        ))
        termos_registro = _perguntas_ia_v2_termos_interface(texto)
        marcador_derivacao = any(
            marcador in texto
            for marcador in ("mesma interface", "mesmo encaixe", "equivalente", "interfaces coincidem", "same interface")
        )
        if not marcador_derivacao and not (compartilhados & termos_registro):
            continue
        referencia = str(registro.get("reference") or registro.get("fact") or registro.get("claim") or "equivalencia textual")[:800]
        return {
            "source_type": "derived_from_grounded_evidence",
            "authority": "derived",
            "reference": referencia,
            "grounded": True,
            "derived_from": {
                "product": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_produto[:3]],
                "target": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_alvo[:3]],
                "target_vehicle": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_alvo[:3]],
                "shared_terms": sorted(compartilhados)[:12],
            },
        }
    return {
        "source_type": "derived_from_grounded_evidence",
        "authority": "derived",
        "reference": "Mesma interface tecnica verificada: " + ", ".join(sorted(compartilhados)[:8]),
        "grounded": True,
        "derived_from": {
            "product": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_produto[:3]],
            "target": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_alvo[:3]],
            "target_vehicle": [str(item.get("reference") or item.get("fact") or item.get("claim") or "")[:300] for item in evidencias_alvo[:3]],
            "shared_terms": sorted(compartilhados)[:12],
        },
    }


_COMPATIBILITY_EXACT_MISMATCH_ATTRIBUTES = {
    "bolt_pattern": {
        "bolt_pattern", "bolt_hole_pattern", "padrao_de_furos", "padrao_furos",
        "padrao_da_furacao", "padrao_furacao", "furacao", "fixation_holes",
        "fixacao_furos", "interface_fixation_holes", "interface_bolt_pattern",
        "bcd", "pcd", "bcd_pcd", "bolt_circle", "bolt_circle_pattern",
    },
    "symmetry": {
        "symmetry", "simetria", "fixation_symmetry", "simetria_da_fixacao",
        "simetria_de_fixacao", "simetria_da_furacao", "simetria_furacao",
        "interface_symmetry", "interface_fixation_symmetry",
    },
    "fixation_geometry": {
        "fixation_geometry", "mounting_geometry", "geometria_da_fixacao",
        "geometria_de_fixacao", "geometria_fixacao", "geometria_da_furacao",
        "geometria_furacao", "interface_fixation_geometry",
    },
    "connector_type": {
        "connector_type", "tipo_de_conector", "tipo_conector", "conector_tipo",
        "interface_connector_type",
    },
}
_COMPATIBILITY_GROUNDED_AUTHORITIES = {
    "approved_internal_memory", "context_hub_canonical", "context_hub_verified",
    "generated_verified", "internal_catalog", "internal_listing", "manufacturer",
    "manufacturer_document", "oem_document", "official", "official_document",
    "technical_web_source", "generated_verified_target",
}
_COMPATIBILITY_ATTRIBUTE_EVIDENCE_MARKERS = {
    "bolt_pattern": ("bolt", "bcd", "pcd", "furacao", "furos", "holes", "fixacao"),
    "symmetry": ("symmetric", "asymmetric", "simetr", "assimetr"),
    "fixation_geometry": ("geometr", "fixacao", "furacao", "mounting"),
    "connector_type": (
        "conector", "connector", "usb", "lightning", "hdmi", "displayport", "plug", "socket",
    ),
}


def _compatibility_exact_attribute(value: object) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+", "_", _favoritos_normalizar_sem_acentos(str(value or "")),
    ).strip("_")
    for canonical, aliases in _COMPATIBILITY_EXACT_MISMATCH_ATTRIBUTES.items():
        if normalized in aliases:
            return canonical
    return ""


def _compatibility_measure_to_mm(match: re.Match) -> str:
    value = float(match.group(1))
    unit = match.group(2)
    factor = 1.0 if unit.startswith(("mm", "milim", "millim")) else (
        10.0 if unit.startswith(("cm", "centim")) else (1000.0 if unit.startswith(("m", "metr")) else 25.4)
    )
    millimeters = f"{value * factor:.6f}".rstrip("0").rstrip(".")
    return f"{millimeters}mm"


def _compatibility_exact_value(value: object) -> str:
    normalized = _favoritos_normalizar_sem_acentos(str(value or ""))
    normalized = re.sub(r"(?<=\d),(?=\d)", ".", normalized)
    replacements = (
        (r"\b(?:assimetrico|assimetrica|assimetricos|assimetricas|asymmetric|asymmetrical)\b", "asymmetric"),
        (r"\b(?:simetrico|simetrica|simetricos|simetricas|symmetric|symmetrical)\b", "symmetric"),
        (r"\b(?:bcd|pcd)\b", "bolt_circle"),
        (r"\b(?:furos?|holes?|pontos?)\b", "holes"),
        (r"\b(?:(?:usb\s*[- ]*)?(?:tipo|type)\s*[- ]*c|usb\s*[- ]*c)\b", "usb_c"),
        (r"\b(?:(?:usb\s*[- ]*)?(?:tipo|type)\s*[- ]*a|usb\s*[- ]*a)\b", "usb_a"),
        (r"\b(?:(?:usb\s*[- ]*)?(?:tipo|type)\s*[- ]*b|usb\s*[- ]*b)\b", "usb_b"),
        (r"\bmicro[\s-]*usb\b", "micro_usb"),
        (r"\bmini[\s-]*usb\b", "mini_usb"),
        (r"\bdisplay[\s-]*port\b", "displayport"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    normalized = re.sub(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(mm|milimetros?|millimeters?|cm|centimetros?|centimeters?|"
        r"m|metros?|meters?|in|pol(?:egadas?)?|inches?)\b",
        _compatibility_measure_to_mm,
        normalized,
    )
    normalized = re.sub(
        r"\b(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)mm\b", r"\1mm/\2mm", normalized,
    )
    normalized = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", "x", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .,:;/-")
    return normalized[:300]


def _compatibility_value_occurrence_is_positive(text: str, value: str) -> bool:
    """Require at least one non-negated occurrence of an asserted exact value."""

    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])")
    for match in pattern.finditer(text):
        if technical_assertion_occurrence_is_positive(text, match.start(), match.end()):
            return True
    return False


def _compatibility_evidence_supports_value(item: dict[str, Any], value: str, attribute: str) -> bool:
    text = _compatibility_exact_value(" ".join(
        str(item.get(field) or "")
        for field in ("reference", "fact", "claim", "snippet", "title")
    ))
    if not text or not value:
        return False
    if not any(marker in text for marker in _COMPATIBILITY_ATTRIBUTE_EVIDENCE_MARKERS[attribute]):
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text):
        return _compatibility_value_occurrence_is_positive(text, value)
    tokens = [token for token in value.split() if token]
    return bool(
        tokens
        and all(
            re.search(rf"\b{re.escape(token)}\b", text)
            and _compatibility_value_occurrence_is_positive(text, token)
            for token in tokens
        )
    )


def _compatibility_grounded_value_sources(
    evidence: list[dict[str, Any]], value: str, attribute: str,
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict) or item.get("grounded") is not True:
            continue
        authority = _favoritos_normalizar_sem_acentos(str(item.get("authority") or ""))
        if authority not in _COMPATIBILITY_GROUNDED_AUTHORITIES:
            continue
        if _compatibility_evidence_supports_value(item, value, attribute):
            matched.append(item)
    return matched


def _compatibility_domain_key(value: object) -> str:
    match = re.match(r"^https?://([^/:?#]+)", str(value or "").strip().lower())
    host = match.group(1).strip(".") if match else ""
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return ""
    suffix = ".".join(labels[-2:])
    if suffix in {"com.br", "com.ar", "com.mx", "co.uk", "com.au", "co.jp"} and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _compatibility_qualified_target_sources(
    evidence: list[dict[str, Any]], value: str, attribute: str,
) -> list[dict[str, Any]]:
    matched = _compatibility_grounded_value_sources(evidence, value, attribute)
    return [
        item for item in matched
        if _favoritos_normalizar_sem_acentos(str(item.get("authority") or "")) == "generated_verified_target"
        and _favoritos_normalizar_sem_acentos(str(item.get("source_type") or "")) == "verified_target_evidence"
    ][:1]


def _compatibility_target_support_origins(sources: list[dict[str, Any]]) -> list[str]:
    origins: list[str] = []
    for source in sources:
        supporting = source.get("supporting_sources") if isinstance(source.get("supporting_sources"), list) else []
        for support in supporting:
            if not isinstance(support, dict):
                continue
            origin = str(support.get("origin_key") or "").strip().lower()
            if not origin:
                origin = _compatibility_domain_key(support.get("url"))
            if origin and origin not in origins:
                origins.append(origin)
    return origins[:8]


def _compatibility_exact_unit_allowed(attribute: str, value: object) -> bool:
    normalized = _favoritos_normalizar_sem_acentos(str(value or "")).strip().replace(" ", "_")
    if attribute == "bolt_pattern":
        return normalized in {"", "mm", "cm", "in", "hole", "holes", "furo", "furos", "ponto", "pontos"}
    return normalized in {"", "unitless", "sem_unidade"}


def _compatibility_bolt_components(value: str) -> dict[str, set[str]]:
    patterns = set(re.findall(r"\b\d{1,3}x\d+(?:\.\d+)?(?:mm)?\b", value))
    for match in re.finditer(
        r"\b(\d{1,3})x(\d+(?:\.\d+)?)(?:mm)?/(\d+(?:\.\d+)?)(?:mm)?\b", value,
    ):
        patterns.update({f"{match.group(1)}x{match.group(2)}mm", f"{match.group(1)}x{match.group(3)}mm"})
    return {
        "patterns": patterns,
        "diameters": set(re.findall(r"\b\d+(?:\.\d+)?mm\b", value)),
        "holes": set(re.findall(r"\b\d{1,3}(?=\s*holes\b)", value)),
    }


def _compatibility_bolt_conflict(product_value: str, target_value: str) -> bool:
    product = _compatibility_bolt_components(product_value)
    target = _compatibility_bolt_components(target_value)
    for key in ("patterns", "holes", "diameters"):
        if product[key] and target[key] and product[key].isdisjoint(target[key]):
            return True
    return False


def _compatibility_named_options(value: str, options: tuple[str, ...]) -> set[str]:
    return {
        option for option in options
        if re.search(rf"(?<![a-z0-9]){re.escape(option)}(?![a-z0-9])", value)
    }


def _compatibility_exact_values_conflict(attribute: str, product_value: str, target_value: str) -> bool:
    if product_value == target_value:
        return False
    symmetry_options = ("symmetric", "asymmetric")
    if attribute == "symmetry":
        product = _compatibility_named_options(product_value, symmetry_options)
        target = _compatibility_named_options(target_value, symmetry_options)
        return bool(product and target and product.isdisjoint(target))
    if attribute == "connector_type":
        connector_options = (
            "usb_a", "usb_b", "usb_c", "micro_usb", "mini_usb", "lightning", "hdmi", "displayport",
        )
        product = _compatibility_named_options(product_value, connector_options)
        target = _compatibility_named_options(target_value, connector_options)
        return bool(product and target and product.isdisjoint(target))
    if attribute == "fixation_geometry":
        product = _compatibility_named_options(product_value, symmetry_options)
        target = _compatibility_named_options(target_value, symmetry_options)
        if product and target:
            return product.isdisjoint(target)
    return _compatibility_bolt_conflict(product_value, target_value) if attribute in {
        "bolt_pattern", "fixation_geometry",
    } else False


def _compatibility_exact_value_options(attribute: str, value: str) -> set[str]:
    """Return the complete allowlisted option set represented by one value."""

    if attribute == "symmetry":
        return _compatibility_named_options(value, ("symmetric", "asymmetric"))
    if attribute == "connector_type":
        return _compatibility_named_options(value, (
            "usb_a", "usb_b", "usb_c", "micro_usb", "mini_usb", "lightning", "hdmi", "displayport",
        ))
    if attribute in {"bolt_pattern", "fixation_geometry"}:
        components = _compatibility_bolt_components(value)
        options = {
            f"{kind}:{option}"
            for kind, values in components.items()
            for option in values
        }
        if attribute == "fixation_geometry":
            options.update(
                f"symmetry:{option}"
                for option in _compatibility_named_options(value, ("symmetric", "asymmetric"))
            )
        return options
    return set()


def _compatibility_source_fully_supports_value(
    item: dict[str, Any], value: str, target_value: str, attribute: str,
) -> bool:
    """Reject a model-selected subvalue when grounded evidence exposes more options."""

    source_value = _compatibility_exact_value(" ".join(
        str(item.get(field) or "")
        for field in ("reference", "fact", "claim", "snippet", "title")
    ))
    model_options = _compatibility_exact_value_options(attribute, value)
    source_options = _compatibility_exact_value_options(attribute, source_value)
    return bool(
        model_options
        and source_options == model_options
        and all(
            _compatibility_value_occurrence_is_positive(
                source_value, option.split(":", maxsplit=1)[-1],
            )
            for option in source_options
        )
        and _compatibility_exact_values_conflict(attribute, source_value, target_value)
    )


def _compatibility_derive_incompatibility(analise: dict, grounding: Optional[dict[str, Any]]) -> None:
    if analise.get("decision") != "no" or not isinstance(grounding, dict):
        return
    product_evidence = analise["evidence"].get("product") or []
    target_evidence = analise["evidence"].get("target_vehicle") or []
    for comparison in analise.get("comparison_attributes") or []:
        if comparison.get("result") != "conflict" or comparison.get("decisive") is not True:
            continue
        attribute = _compatibility_exact_attribute(comparison.get("attribute"))
        product_value = _compatibility_exact_value(comparison.get("product_value"))
        target_value = _compatibility_exact_value(comparison.get("target_value"))
        if (
            not attribute or not _compatibility_exact_unit_allowed(attribute, comparison.get("unit"))
            or not product_value or not target_value
            or not _compatibility_exact_values_conflict(attribute, product_value, target_value)
        ):
            continue
        product_sources = _compatibility_grounded_value_sources(product_evidence, product_value, attribute)
        target_sources = _compatibility_qualified_target_sources(target_evidence, target_value, attribute)
        if (
            not product_sources or not target_sources
            or not all(
                _compatibility_source_fully_supports_value(
                    source, product_value, target_value, attribute,
                )
                for source in product_sources
            )
        ):
            continue
        product_source = product_sources[0]
        product_reference = str(
            product_source.get("reference") or product_source.get("fact") or product_source.get("claim")
            or product_source.get("snippet") or product_source.get("title") or ""
        )[:300]
        target_references = [
            str(
                source.get("reference") or source.get("fact") or source.get("claim")
                or source.get("snippet") or source.get("title") or ""
            )[:300]
            for source in target_sources
        ]
        analise["evidence"]["equivalence"].append({
            "source_type": "derived_incompatibility_from_grounded_evidence",
            "authority": "derived",
            "reference": (
                f"Nao compativel: {attribute} difere entre produto "
                f"({comparison.get('product_value')}) e alvo ({comparison.get('target_value')})."
            )[:800],
            "grounded": True,
            "derived_from": {
                "attribute": attribute,
                "product_value": product_value,
                "target_value": target_value,
                "product": [product_reference],
                "target": target_references,
                "target_vehicle": target_references,
                "target_domains": _compatibility_target_support_origins(target_sources),
            },
        })
        analise["evidence"]["equivalence"] = analise["evidence"]["equivalence"][:8]
        return


def _compatibility_bind_fields(bruto: dict, analise: dict, classification_bound: bool, grounding: Optional[dict[str, Any]]) -> Any:
    for campo in ("product_interface", "target_interface", "condition", "reason"):
        if bruto.get(campo) not in (None, ""):
            analise[campo] = re.sub(r"\s+", " ", str(bruto.get(campo) or "")).strip()[:1200]
    alvo = (
        bruto.get("target_item")
        or bruto.get("target_vehicle")
        or analise.get("target_item")
        or analise.get("target_vehicle")
    )
    analise["target_item"] = re.sub(r"\s+", " ", str(alvo or "")).strip()[:300]
    analise["target_vehicle"] = analise["target_item"]
    default_type = normalize_target_type(analise.get("target_type"), "generic")
    analise["target_type"] = normalize_target_type(bruto.get("target_type"), default_type)
    analise["compatibility_profile"] = normalize_profile(
        bruto.get("compatibility_profile") or analise.get("compatibility_profile"),
        analise["target_type"],
    )
    if classification_bound:
        analise["classification_reference_advisory"] = True
    raw_comparisons = bruto.get("comparison_attributes")
    if not isinstance(raw_comparisons, list):
        raw_comparisons = analise.get("comparison_attributes")
    analise["comparison_attributes"] = normalize_comparison_attributes(raw_comparisons)
    aliases = {
        "sim": "yes", "compativel": "yes", "compatible": "yes", "yes": "yes", "nao": "no", "incompativel": "no",
        "incompatible": "no", "no": "no", "condicional": "conditional", "conditional": "conditional",
        "insuficiente": "insufficient", "evidencia_insuficiente": "insufficient", "insufficient": "insufficient",
    }
    decision = _favoritos_normalizar_sem_acentos(str(bruto.get("decision") or analise.get("decision") or "insufficient"))
    analise["decision"] = aliases.get(decision, "insufficient")
    missing = bruto.get("missing_fields") if isinstance(bruto.get("missing_fields"), list) else analise.get("missing_fields") or []
    analise["missing_fields"] = list(dict.fromkeys(str(item or "").strip()[:160] for item in missing if str(item or "").strip()))[:12]
    raw_evidence = bruto.get("evidence") or analise.get("evidence")
    analise["evidence"] = _perguntas_ia_v2_evidencias_normalizar(raw_evidence, grounding=grounding)
    return raw_evidence


def _compatibility_ground_evidence(analise: dict, raw_evidence: Any, grounding: Optional[dict[str, Any]]) -> None:
    if not isinstance(grounding, dict) or analise["decision"] not in {"yes", "conditional"}:
        return
    for group, description in {"product": analise.get("product_interface"), "target_vehicle": analise.get("target_interface")}.items():
        if analise["evidence"].get(group):
            continue
        raw_group = raw_evidence.get(group) if isinstance(raw_evidence, dict) and isinstance(raw_evidence.get(group), list) else []
        declared_urls = {
            _perguntas_ia_v2_grounding_url_key(item.get("url")) for item in raw_group
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        }
        declared_urls.discard("")
        collected_urls = {
            str(item.get("url") or "") for item in (grounding.get(group) or [])
            if isinstance(item, dict) and str(item.get("url") or "")
        }
        for grounded in grounding.get(group) or []:
            if not isinstance(grounded, dict):
                continue
            for url_text in re.findall(r"https?://[^\s<>'\"\\]+", str(grounded.get("text") or ""), flags=re.IGNORECASE):
                url_key = _perguntas_ia_v2_grounding_url_key(url_text.rstrip(".,;:)]}"))
                if url_key:
                    collected_urls.add(url_key)
        if declared_urls and not declared_urls <= collected_urls:
            continue
        collected = _perguntas_ia_v2_grounding_evidencia_interface(group, description, grounding)
        if collected:
            analise["evidence"][group] = [collected]
    if not analise["evidence"].get("equivalence"):
        derived = _perguntas_ia_v2_equivalencia_derivada(
            raw_evidence, analise["evidence"].get("product") or [], analise["evidence"].get("target_vehicle") or [],
        )
        if derived:
            analise["evidence"]["equivalence"] = [derived]


def _compatibility_metadata(analise: dict, bruto: dict, queries, sources, grounding) -> None:
    analise["evidence"]["target"] = copy.deepcopy(analise["evidence"].get("target_vehicle") or [])
    selected_queries = queries if isinstance(queries, list) else bruto.get("queries")
    analise["queries"] = [
        {"type": str(item.get("type") or "web")[:80], "query": str(item.get("query") or "")[:300]}
        for item in (selected_queries or [])[:12] if isinstance(item, dict) and str(item.get("query") or "").strip()
    ]
    collected_sources = list((grounding or {}).get("sources") or []) if isinstance(grounding, dict) else list(sources or [])
    model_sources = [] if isinstance(grounding, dict) else list(bruto.get("sources") or [])
    analise["sources"] = list(dict.fromkeys(str(item or "").strip()[:700] for item in collected_sources + model_sources if str(item or "").strip()))[:16]
    try:
        analise["confidence"] = max(0.0, min(float(bruto.get("confidence", analise.get("confidence") or 0.0)), 1.0))
    except Exception:
        analise["confidence"] = 0.0


def _compatibility_fill_comparisons(analise: dict) -> None:
    product = analise["evidence"].get("product") or []
    target = analise["evidence"].get("target_vehicle") or []
    equivalence = analise["evidence"].get("equivalence") or []
    if analise["comparison_attributes"] or analise["decision"] not in {"yes", "no", "conditional"}:
        return
    references = [
        str(item.get("url") or item.get("reference") or item.get("fact") or "")[:300]
        for item in [*product[:2], *target[:2], *equivalence[:2]]
        if isinstance(item, dict) and str(item.get("url") or item.get("reference") or item.get("fact") or "").strip()
    ]
    analise["comparison_attributes"] = normalize_comparison_attributes([{
        "attribute": "interface", "product_value": analise.get("product_interface"),
        "target_value": analise.get("target_interface"), "result": "conflict" if analise["decision"] == "no" else "match",
        "decisive": True, "evidence_refs": references,
    }])


def _compatibility_validate_decision(analise: dict) -> None:
    product = analise["evidence"].get("product") or []
    target = analise["evidence"].get("target_vehicle") or []
    equivalence = analise["evidence"].get("equivalence") or []
    evidence = [*product, *target, *equivalence]
    authorities = {_favoritos_normalizar_sem_acentos(str(item.get("authority") or "")) for item in evidence}
    marketplace_only = bool(evidence and authorities and authorities <= {"marketplace_hint", "marketplace"})
    interfaces_complete = all(str(analise.get(field) or "").strip() for field in ("product_interface", "target_item", "target_interface"))
    both_sides = bool(product and target)
    technical_target = any(_favoritos_normalizar_sem_acentos(str(item.get("authority") or "")) not in {"marketplace", "marketplace_hint"} for item in target)
    explicit_equivalence = _perguntas_ia_v2_equivalencia_explicita(analise["decision"], product, target, equivalence)
    complete_condition = analise["decision"] != "conditional" or bool(str(analise.get("condition") or "").strip())
    decisive = [item for item in analise["comparison_attributes"] if item.get("decisive")]
    comparison_results = {str(item.get("result") or "") for item in decisive}
    coherent = bool(decisive) and ((analise["decision"] in {"yes", "conditional"} and "match" in comparison_results and "conflict" not in comparison_results) or (analise["decision"] == "no" and "conflict" in comparison_results))
    checks = {
        "interfaces_complete": interfaces_complete,
        "both_sides_present": both_sides,
        "technical_target_present": technical_target,
        "explicit_equivalence_present": explicit_equivalence,
        "condition_complete": complete_condition,
        "comparison_coherent": coherent,
        "not_marketplace_only": not marketplace_only,
    }
    warnings = [name for name, passed in checks.items() if not passed]
    if marketplace_only:
        warnings.append("marketplace_only")
    analise["decision_diagnostics"] = {
        "policy": "jk_black_jhon_factual_discretion_v1",
        "advisory_only": True,
        "model_decision_preserved": True,
        "decision": analise["decision"],
        "checks": checks,
        "warnings": list(dict.fromkeys(warnings))[:12],
    }


def _perguntas_ia_v2_compatibilidade_normalizar(
    valor: Any, *, base: Optional[dict[str, Any]] = None, queries: Optional[list[dict[str, Any]]] = None,
    sources: Optional[list[str]] = None, grounding: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    bruto = valor if isinstance(valor, dict) else {}
    analise = copy.deepcopy(base) if isinstance(base, dict) else _perguntas_ia_v2_compatibilidade_padrao()
    raw_evidence = _compatibility_bind_fields(bruto, analise, bool(analise.get("_classification_bound")), grounding)
    _compatibility_ground_evidence(analise, raw_evidence, grounding)
    _compatibility_metadata(analise, bruto, queries, sources, grounding)
    _compatibility_fill_comparisons(analise)
    _compatibility_derive_incompatibility(analise, grounding)
    _compatibility_validate_decision(analise)
    return analise
