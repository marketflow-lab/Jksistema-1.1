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

def _compatibility_bind_fields(bruto: dict, analise: dict, classification_bound: bool, grounding: Optional[dict[str, Any]]) -> Any:
    for campo in ("product_interface", "target_interface", "condition", "reason"):
        if bruto.get(campo) not in (None, ""):
            analise[campo] = re.sub(r"\s+", " ", str(bruto.get(campo) or "")).strip()[:1200]
    alvo = (analise.get("target_item") or analise.get("target_vehicle")) if classification_bound else (
        bruto.get("target_item") or bruto.get("target_vehicle") or analise.get("target_item") or analise.get("target_vehicle")
    )
    analise["target_item"] = re.sub(r"\s+", " ", str(alvo or "")).strip()[:300]
    analise["target_vehicle"] = analise["target_item"]
    if classification_bound:
        analise["target_type"] = str(analise.get("target_type") or "")
        analise["compatibility_profile"] = str(analise.get("compatibility_profile") or "")
    else:
        default_type = normalize_target_type(analise.get("target_type"), "generic")
        analise["target_type"] = normalize_target_type(bruto.get("target_type"), default_type)
        analise["compatibility_profile"] = normalize_profile(bruto.get("compatibility_profile") or analise.get("compatibility_profile"), analise["target_type"])
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
    if analise["decision"] != "insufficient":
        analise["missing_fields"] = []
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
    original_decision = analise["decision"]
    explicit_equivalence = _perguntas_ia_v2_equivalencia_explicita(analise["decision"], product, target, equivalence)
    complete_condition = analise["decision"] != "conditional" or bool(str(analise.get("condition") or "").strip())
    decisive = [item for item in analise["comparison_attributes"] if item.get("decisive")]
    comparison_results = {str(item.get("result") or "") for item in decisive}
    coherent = bool(decisive) and ((analise["decision"] in {"yes", "conditional"} and "match" in comparison_results and "conflict" not in comparison_results) or (analise["decision"] == "no" and "conflict" in comparison_results))
    if analise["decision"] not in {"yes", "no", "conditional"} or all((interfaces_complete, both_sides, technical_target, explicit_equivalence, complete_condition, coherent, not marketplace_only)):
        return
    analise["decision"] = "insufficient"
    analise["confidence"] = min(analise["confidence"], 0.49)
    checks = (
        (not analise.get("product_interface"), "product_interface"), (not analise.get("target_item"), "target_item"),
        (not analise.get("target_interface"), "target_interface"), (not product, "product_evidence"),
        (not target, "target_vehicle_evidence"), (bool(target) and not technical_target, "non_marketplace_target_evidence"),
        (not complete_condition, "condition"), (marketplace_only, "authoritative_technical_evidence"),
    )
    analise["missing_fields"].extend(name for failed, name in checks if failed)
    if not analise.get("target_item"):
        analise["missing_fields"].append("target_vehicle")
    if not explicit_equivalence:
        analise["missing_fields"].append("explicit_incompatibility_evidence" if original_decision == "no" else "explicit_equivalence_evidence")
    if not coherent:
        analise["missing_fields"].append("comparison_conflict" if "conflict" in comparison_results and original_decision != "no" else "comparison_attributes")
    analise["missing_fields"] = list(dict.fromkeys(analise["missing_fields"]))[:12]
    analise["reason"] = "compatibility_decision_without_sufficient_evidence"


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
    _compatibility_validate_decision(analise)
    return analise
