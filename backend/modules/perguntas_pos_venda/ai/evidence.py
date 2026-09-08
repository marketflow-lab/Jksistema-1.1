"""Extracted legacy AI implementation with static dependencies."""
from __future__ import annotations
from .runtime import (
    Any,
    Optional,
    QuestionCategory,
    _favoritos_ml_url_item_id,
    _favoritos_normalizar_sem_acentos,
    _perguntas_ia_v2_grounding_texto,
    copy,
    json,
    re,
    urlparse,
)
from .context import (
    _PERGUNTAS_CONTEXT_HUB_TRUTH_CLASSES_FACTUAIS,
)
from .inputs import (
    _perguntas_ia_categoria_classificada,
    _perguntas_ia_compatibilidade_classificada,
)
from .queries import (
    _perguntas_ia_v2_alvo_compatibilidade,
    _perguntas_ia_v2_perfil_compatibilidade,
)
from backend.services.vin_transient import contains_vin_like_identifier
from .deep_research_contracts import (
    safe_agent_product_research_evidence,
    safe_agent_research_passages,
    safe_agent_research_metadata as _perguntas_ia_safe_research_metadata,
    safe_agent_research_metrics as _perguntas_ia_safe_research_metrics,
    sanitize_public_research_text,
)
def _perguntas_ia_v2_query_pesquisa(metadata: Optional[dict[str, Any]]) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    pergunta = re.sub(r"\s+", " ", str(meta.get("question_text") or "").strip())
    link = str(meta.get("listing_link") or "").strip()
    titulo = re.sub(r"\s+", " ", str(meta.get("listing_title") or "").strip())[:240]
    item_id = str(meta.get("item_id") or "").strip()
    if not link and item_id:
        link = _favoritos_ml_url_item_id(item_id)
    partes = [parte for parte in (titulo, pergunta, link) if parte]
    if not partes:
        return ""
    return " ".join(partes)[:600]

def _perguntas_ia_v2_resposta_precisa_web(resposta: Any, metadata: Optional[dict[str, Any]] = None) -> bool:
    meta = metadata if isinstance(metadata, dict) else {}
    categoria = str(meta.get("category") or "").strip().lower()
    if categoria not in {"compatibility", "product_feature", "warranty_originality", "other_product", "unknown"}:
        return False
    texto = str(getattr(resposta, "answer", "") or "").strip()
    if not texto:
        return True
    try:
        confianca = float(getattr(resposta, "confidence", 0.0) or 0.0)
    except Exception:
        confianca = 0.0
    motivo = _favoritos_normalizar_sem_acentos(str(getattr(resposta, "reason", "") or ""))
    texto_norm = _favoritos_normalizar_sem_acentos(texto)
    marcadores_ausencia = (
        "missing_listing_evidence",
        "listing evidence missing",
        "nao consta no anuncio",
        "nao consta na descricao",
        "nao encontrei essa informacao",
        "nao foi informado no anuncio",
        "informacao nao disponivel no anuncio",
        "sem evidencia no anuncio",
        "nao esta especificado",
        "nao informa objetivamente",
        "nao podemos afirmar com seguranca",
        "precisamos verificar essa especificacao",
    )
    return bool(
        getattr(resposta, "requires_human_review", False)
        or confianca < 0.78
        or any(marcador in motivo or marcador in texto_norm for marcador in marcadores_ausencia)
    )

def _perguntas_ia_v2_fontes_web(tool_result: Optional[dict[str, Any]]) -> list[str]:
    result = tool_result.get("result") if isinstance(tool_result, dict) and isinstance(tool_result.get("result"), dict) else {}
    contexto = sanitize_public_research_text(result.get("context"), 12_000)
    fontes: list[str] = []
    for url in re.findall(
        r"^\s*URL:\s*(https?://[^\s<>'\"]+)\s*$",
        contexto,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        limpa = url.rstrip(".,;:)]}")[:600]
        if "_PROTEGIDO]" in limpa.upper() or contains_vin_like_identifier(limpa):
            continue
        if limpa and limpa not in fontes:
            fontes.append(limpa)
        if len(fontes) >= 16:
            break
    return fontes


_PERGUNTAS_VERIFIED_TARGET_FIELDS = {
    "interface.bolt_pattern",
    "interface.connector_type",
    "interface.fixation_geometry",
    "interface.symmetry",
}
_PERGUNTAS_VERIFIED_TARGET_SOURCE_AUTHORITIES = {
    "official_manufacturer", "official_oem", "technical_distributor", "technical_independent",
}


def _perguntas_ia_verified_target_evidence_compact(value: object) -> Optional[dict[str, Any]]:
    item = value if isinstance(value, dict) else {}
    field_name = str(item.get("field_name") or "").strip().lower()
    target_identity = re.sub(
        r"\s+", " ", sanitize_public_research_text(item.get("target_identity"), 300)
    ).strip()[:300]
    fact_value = re.sub(
        r"\s+", " ", sanitize_public_research_text(item.get("value"), 300)
    ).strip()[:300]
    if (
        str(item.get("scope") or "").strip().lower() != "target"
        or field_name not in _PERGUNTAS_VERIFIED_TARGET_FIELDS
        or not target_identity
        or not fact_value
        or contains_vin_like_identifier(target_identity)
        or contains_vin_like_identifier(fact_value)
    ):
        return None
    sources = []
    for source in (item.get("sources") or [])[:8]:
        if not isinstance(source, dict):
            continue
        authority = str(source.get("authority") or "").strip().lower()
        if authority not in _PERGUNTAS_VERIFIED_TARGET_SOURCE_AUTHORITIES:
            continue
        safe_url = _perguntas_ia_safe_research_metadata(source.get("url"), 800)
        url = _perguntas_ia_v2_grounding_url_key(safe_url)
        if not url.startswith(("http://", "https://")):
            continue
        raw_origin_key = str(source.get("origin_key") or "").strip()
        raw_copy_fingerprint = str(source.get("copy_fingerprint") or "").strip()
        safe_origin_key = _perguntas_ia_safe_research_metadata(raw_origin_key, 200).lower()
        safe_copy_fingerprint = _perguntas_ia_safe_research_metadata(raw_copy_fingerprint, 128).lower()
        if (
            (raw_origin_key and not safe_origin_key)
            or (raw_copy_fingerprint and not safe_copy_fingerprint)
        ):
            continue
        compact = {
            "authority": authority,
            "url": url,
            "origin_key": safe_origin_key,
            "copy_fingerprint": safe_copy_fingerprint,
        }
        sources.append({key: raw for key, raw in compact.items() if raw})
    return {
        "scope": "target",
        "target_identity": target_identity,
        "field_name": field_name,
        "value": fact_value,
        "unit": _perguntas_ia_safe_research_metadata(item.get("unit"), 24),
        "activation_policy": _perguntas_ia_safe_research_metadata(item.get("activation_policy"), 64).lower(),
        "sources": sources,
    }


def _perguntas_ia_verified_research_view(result: dict) -> dict:
    """Expose activated facts and safe research diagnostics, never page text."""

    data = (
        result.get("result")
        if isinstance(result, dict) and isinstance(result.get("result"), dict)
        else {}
    )
    verified = safe_agent_product_research_evidence([
        {**value, "state": "verified"}
        for value in (data.get("verified_product_evidence") or [])[:120]
        if isinstance(value, dict)
    ])
    verified_target = [
        compact
        for value in (data.get("verified_target_evidence") or [])[:120]
        if (compact := _perguntas_ia_verified_target_evidence_compact(value)) is not None
    ]
    return {
        "function": (
            str(result.get("function") or "web_search_question_context")
            if isinstance(result, dict)
            else "web_search_question_context"
        ),
        "arguments": {},
        "result": {
            "found": bool(verified or verified_target),
            "verified_product_evidence": verified,
            "verified_target_evidence": verified_target,
            "research_metrics": _perguntas_ia_safe_research_metrics(data.get("research_metrics")),
            "timeout": data.get("timeout") is True,
            "unavailable": data.get("unavailable") is True,
            "read_only": True,
            "scope": "activated_evidence_projection",
            "instruction": (
                "Esta e apenas a subprojecao de afirmacoes ativadas; nao e um gate de uso. "
                "Consulte a visao de pesquisa sanitizada completa para decidir."
            ),
        },
    }


def _perguntas_ia_research_view(result: dict) -> dict:
    """Expose all bounded, sanitized research for Black Jhon to evaluate."""

    data = (
        result.get("result")
        if isinstance(result, dict) and isinstance(result.get("result"), dict)
        else {}
    )
    verified_view = _perguntas_ia_verified_research_view(result)
    verified_data = verified_view["result"]
    context = sanitize_public_research_text(data.get("context"), 12_000)
    passages = safe_agent_research_passages(
        data.get("research_passages")
        if isinstance(data.get("research_passages"), list)
        else []
    )
    compiled: list[dict[str, Any]] = []
    for raw in (data.get("product_research_evidence") or [])[:160]:
        if not isinstance(raw, dict):
            continue
        safe_field_name = _perguntas_ia_safe_research_metadata(raw.get("field_name"), 96)
        field_name = re.sub(r"[^a-z0-9_.]", "", safe_field_name.lower())[:96]
        fact_value = re.sub(
            r"\s+", " ", sanitize_public_research_text(raw.get("value"), 300)
        ).strip()[:300]
        if not field_name or not fact_value:
            continue
        sources: list[dict[str, str]] = []
        for source in (raw.get("sources") or [])[:8]:
            if not isinstance(source, dict):
                continue
            safe_source_url = _perguntas_ia_safe_research_metadata(source.get("url"), 800)
            url = _perguntas_ia_v2_grounding_url_key(safe_source_url)
            if not url.startswith(("http://", "https://")):
                continue
            sources.append({
                "source_type": _perguntas_ia_safe_research_metadata(source.get("source_type"), 40),
                "authority": _perguntas_ia_safe_research_metadata(source.get("authority"), 40),
                "url": url,
                "domain": _perguntas_ia_safe_research_metadata(source.get("domain"), 200),
                "section_ref": _perguntas_ia_safe_research_metadata(source.get("section_ref"), 160),
                "collected_at": _perguntas_ia_safe_research_metadata(source.get("collected_at"), 40),
                "valid_until": _perguntas_ia_safe_research_metadata(source.get("valid_until"), 40),
            })
        compiled.append({
            "field_name": field_name,
            "scope": _perguntas_ia_safe_research_metadata(raw.get("scope"), 24),
            "value": fact_value,
            "unit": _perguntas_ia_safe_research_metadata(raw.get("unit"), 16),
            "state": _perguntas_ia_safe_research_metadata(raw.get("state") or "candidate", 24),
            "activation_policy": _perguntas_ia_safe_research_metadata(raw.get("activation_policy"), 64),
            "conflict_group": _perguntas_ia_safe_research_metadata(raw.get("conflict_group"), 40),
            "valid_from": _perguntas_ia_safe_research_metadata(raw.get("valid_from"), 40),
            "valid_until": _perguntas_ia_safe_research_metadata(raw.get("valid_until"), 40),
            "sources": sources,
        })
    return {
        "function": verified_view["function"],
        "arguments": {},
        "result": {
            "found": bool(
                context
                or passages
                or compiled
                or verified_data.get("verified_product_evidence")
                or verified_data.get("verified_target_evidence")
            ),
            "context": context,
            "research_passages": passages,
            "product_research_evidence": compiled,
            "verified_product_evidence": list(
                verified_data.get("verified_product_evidence") or []
            ),
            "verified_target_evidence": list(
                verified_data.get("verified_target_evidence") or []
            ),
            "research_sources": _perguntas_ia_v2_fontes_web(result),
            "research_metrics": _perguntas_ia_safe_research_metrics(
                data.get("research_metrics")
            ),
            "timeout": data.get("timeout") is True,
            "unavailable": data.get("unavailable") is True,
            "read_only": True,
            "scope": "sanitized_research_agent_discretion",
            "instruction": (
                "Todo o material compilado e sanitizado esta disponivel para julgamento do Black Jhon. "
                "Estado, autoridade, validade e conflito sao metadados de proveniencia, nao permissoes "
                "deterministicas. Avalie relevancia e confiabilidade, resolva conflitos e nao invente fatos."
            ),
        },
    }


def _perguntas_ia_general_research_contract(result: dict) -> tuple[dict, bool, bool, dict]:
    """Build the general-flow research view and its aggregate pipeline record."""

    data = result.get("result") if isinstance(result, dict) and isinstance(result.get("result"), dict) else {}
    metrics = data.get("research_metrics") if isinstance(data.get("research_metrics"), dict) else {}

    def metric_count(name: str) -> int:
        try:
            return max(0, int(metrics.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    research_result = _perguntas_ia_research_view(result)
    research_data = research_result["result"]
    found = bool(research_data.get("found"))
    tool_error = bool(str(data.get("error") or "").strip())
    arguments = result.get("arguments") if isinstance(result, dict) and isinstance(result.get("arguments"), dict) else {}
    step = {
        "step": 4,
        "name": "question_focused_web_research",
        "status": "error" if tool_error else ("completed" if found else "unavailable"),
        "reason": "decisive_fact_missing",
        "query_count": max(0, int(arguments.get("query_count") or len(list(arguments.get("queries") or [])[:8]))),
        "source_count": len(_perguntas_ia_v2_fontes_web(result)),
        "candidate_pages_found": bool(data.get("found") and str(data.get("context") or "").strip()),
        "verified_fields": len(list(research_data.get("verified_product_evidence") or [])),
        "compiled_fields": len(list(research_data.get("product_research_evidence") or [])),
        "passage_count": len(list(research_data.get("research_passages") or [])),
        "pages_discovered": metric_count("pages_discovered"),
        "pages_attempted": metric_count("pages_attempted"),
        "pages_read": metric_count("pages_read"),
        "read_failures": metric_count("read_failures"),
        "read_timeouts": metric_count("read_timeouts"),
        "retry_successes": metric_count("retry_successes"),
        "stop_reason": str(metrics.get("stop_reason") or "")[:40],
        "repository_status": str(metrics.get("repository_status") or "")[:40],
    }
    return research_result, found, tool_error, step

def _perguntas_ia_v2_json_obj(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    texto = str(payload or "").strip()
    if not texto:
        return {}
    try:
        data = json.loads(texto)
        return data if isinstance(data, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", texto, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

def _perguntas_ia_v2_grounding_url_key(valor: object) -> str:
    url = str(valor or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
    except Exception:
        return ""

def _perguntas_ia_v2_grounding_marketplace(url: object) -> bool:
    dominio = _perguntas_ia_v2_grounding_url_key(url)
    return any(
        termo in dominio
        for termo in ("mercadolivre", "mercadolibre", "amazon.", "shopee.", "aliexpress.", "magazineluiza.")
    )

def _perguntas_ia_v2_grounding_blocos_web(contexto: object) -> list[tuple[str, str]]:
    linhas = str(contexto or "").splitlines()
    blocos: list[tuple[str, str]] = []
    atual: list[str] = []

    def concluir() -> None:
        if not atual:
            return
        bloco = "\n".join(atual).strip()
        for linha in atual:
            match = re.fullmatch(r"\s*URL:\s*(https?://[^\s<>'\"]+)\s*", linha, flags=re.IGNORECASE)
            if not match:
                continue
            limpa = match.group(1).rstrip(".,;:)]}")
            if limpa:
                blocos.append((limpa, bloco))
            break

    for linha in linhas:
        inicio_resultado = bool(re.match(r"^\s*\d+\.\s+", linha))
        inicio_busca = bool(re.match(r"^\s*Busca\s+\d+", linha, flags=re.IGNORECASE))
        if (inicio_resultado or inicio_busca) and any("URL:" in parte.upper() for parte in atual):
            concluir()
            atual = []
        atual.append(linha)
    concluir()
    return blocos

def _grounding_add(grounding: dict[str, Any], groups: tuple[str, ...], text: object, source_type: str, authority: str, url: str = "", **metadata: Any) -> None:
    raw_text = str(text or "").strip()
    normalized = _perguntas_ia_v2_grounding_texto(raw_text)
    if not normalized:
        return
    url_key = _perguntas_ia_v2_grounding_url_key(url)
    marketplace = _perguntas_ia_v2_grounding_marketplace(url_key)
    record = {
        "text": raw_text[:16000], "text_norm": normalized[:24000], "source_type": source_type,
        "authority": "marketplace_hint" if marketplace else authority, "url": url_key, "marketplace": marketplace,
    }
    record.update({key: value for key, value in metadata.items() if value not in (None, "")})
    for group in groups:
        grounding[group].append(record)
    if url_key:
        grounding["urls"].setdefault(url_key, []).append(record)
        if url_key not in grounding["sources"]:
            grounding["sources"].append(url_key)


def _grounding_context_hub(grounding: dict[str, Any], rows: list) -> None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        snippet = str(row.get("snippet") or "").strip()
        truth_class = str(row.get("truth_class") or "legacy_unverified").strip().lower()
        if not snippet:
            continue
        if truth_class not in _PERGUNTAS_CONTEXT_HUB_TRUTH_CLASSES_FACTUAIS:
            grounding["legacy_unverified"].append({
                "text": snippet[:16000], "text_norm": _perguntas_ia_v2_grounding_texto(snippet)[:24000],
                "source_type": "context_hub_reference", "authority": "legacy_unverified", "truth_class": truth_class,
                "doc_id": str(row.get("doc_id") or "")[:240], "chunk_id": str(row.get("chunk_id") or "")[:240],
                "eligible_as_solo_evidence": False,
            })
            continue
        _grounding_add(
            grounding, ("product",), snippet, "context_hub_sku",
            "context_hub_canonical" if truth_class == "canonical" else "context_hub_verified",
            truth_class=truth_class, doc_id=str(row.get("doc_id") or "")[:240],
            chunk_id=str(row.get("chunk_id") or "")[:240], reference=str(row.get("reference") or "")[:300],
            eligible_as_solo_evidence=True,
        )


def _grounding_web(grounding: dict[str, Any], function_name: str, context: str) -> None:
    groups = ("product", "equivalence") if function_name == "web_search_product_identity" else ("target_vehicle", "equivalence")
    blocks = _perguntas_ia_v2_grounding_blocos_web(context)
    if not blocks:
        return
    for url, block in blocks:
        marketplace = _perguntas_ia_v2_grounding_marketplace(url)
        authority = "marketplace_hint" if marketplace else "technical_web_source"
        _grounding_add(grounding, groups, block, function_name, authority, url)


def _grounding_verified_product_evidence(
    grounding: dict[str, Any],
    values: list,
) -> None:
    for value in values[:120]:
        if not isinstance(value, dict):
            continue
        field_name = str(value.get("field_name") or "").strip().lower()
        fact_value = str(value.get("value") or "").strip()
        if not field_name or not fact_value:
            continue
        unit = str(value.get("unit") or "").strip()
        text = f"{field_name}: {fact_value}{(' ' + unit) if unit else ''}"
        groups = ("product",)
        if field_name.startswith(("compatibility.", "application.")):
            groups = ("target_vehicle", "equivalence")
        _grounding_add(
            grounding,
            groups,
            text,
            "product_evidence_verified",
            "generated_verified",
            field_name=field_name,
            scope=str(value.get("scope") or "")[:24],
            activation_policy=str(value.get("activation_policy") or "")[:64],
            eligible_as_solo_evidence=True,
        )


def _grounding_product_research_evidence(
    grounding: dict[str, Any],
    values: list,
) -> None:
    for value in values[:160]:
        if not isinstance(value, dict):
            continue
        field_name = str(value.get("field_name") or "").strip().lower()
        fact_value = str(value.get("value") or "").strip()
        if not field_name or not fact_value:
            continue
        unit = str(value.get("unit") or "").strip()
        state = str(value.get("state") or "candidate").strip().lower()
        sources = [source for source in (value.get("sources") or []) if isinstance(source, dict)]
        groups = ("product",)
        if field_name.startswith(("compatibility.", "application.")):
            groups = ("target_vehicle", "equivalence")
        primary = sources[0] if sources else {}
        _grounding_add(
            grounding,
            groups,
            f"{field_name}: {fact_value}{(' ' + unit) if unit else ''}",
            str(primary.get("source_type") or "compiled_product_research"),
            str(primary.get("authority") or "research_advisory"),
            str(primary.get("url") or ""),
            field_name=field_name,
            scope=str(value.get("scope") or "")[:24],
            evidence_state=state,
            activation_policy=str(value.get("activation_policy") or "")[:64],
            supporting_sources=sources[:8],
            eligible_as_solo_evidence=True,
            advisory_only=True,
        )


def _perguntas_ia_v2_target_identity_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _favoritos_normalizar_sem_acentos(str(value or "")))


def _perguntas_ia_v2_target_source_origin(source: dict[str, Any]) -> str:
    parsed = urlparse(str(source.get("url") or ""))
    host = str(parsed.hostname or "").strip(".").lower()
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return ""
    suffix = ".".join(labels[-2:])
    if suffix in {"com.br", "com.ar", "com.mx", "co.uk", "com.au", "co.jp"} and len(labels) >= 3:
        suffix = ".".join(labels[-3:])
    return suffix[:200]


def _perguntas_ia_v2_target_sources_qualified(sources: list[dict[str, Any]]) -> bool:
    eligible = [
        source for source in sources
        if isinstance(source, dict)
        and source.get("authority") in _PERGUNTAS_VERIFIED_TARGET_SOURCE_AUTHORITIES
        and not _perguntas_ia_v2_grounding_marketplace(source.get("url"))
        and _perguntas_ia_v2_target_source_origin(source)
    ]
    if any(source.get("authority") in {"official_manufacturer", "official_oem"} for source in eligible):
        return True
    technical = [
        source for source in eligible
        if source.get("authority") in {"technical_distributor", "technical_independent"}
        and str(source.get("copy_fingerprint") or "").strip()
    ]
    return (
        len({_perguntas_ia_v2_target_source_origin(source) for source in technical}) >= 2
        and len({str(source.get("copy_fingerprint") or "") for source in technical}) >= 2
    )


def _grounding_verified_target_evidence(
    grounding: dict[str, Any], values: list, target_identity: str,
) -> None:
    expected_identity = _perguntas_ia_v2_target_identity_key(target_identity)
    if not expected_identity:
        return
    for raw in values[:120]:
        value = _perguntas_ia_verified_target_evidence_compact(raw)
        if not value or _perguntas_ia_v2_target_identity_key(value.get("target_identity")) != expected_identity:
            continue
        if value.get("activation_policy") != "official_or_two_independent_sources":
            continue
        sources = value.get("sources") if isinstance(value.get("sources"), list) else []
        if not _perguntas_ia_v2_target_sources_qualified(sources):
            continue
        unit = str(value.get("unit") or "").strip()
        text = f"{value['field_name']}: {value['value']}{(' ' + unit) if unit else ''}"
        primary_url = next((str(source.get("url") or "") for source in sources if source.get("url")), "")
        _grounding_add(
            grounding,
            ("target_vehicle",),
            text,
            "verified_target_evidence",
            "generated_verified_target",
            primary_url,
            target_identity=str(value.get("target_identity") or "")[:300],
            field_name=str(value.get("field_name") or "")[:96],
            activation_policy=str(value.get("activation_policy") or "")[:64],
            supporting_sources=sources[:8],
            eligible_as_solo_evidence=True,
        )


def _collect_tool_grounding(grounding: dict[str, Any], tool: dict, target_identity: str = "") -> None:
    function_name = str(tool.get("function") or "").strip()
    result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    context = str(result.get("context") or "").strip()
    found = bool(result.get("found") or matches or context or result.get("memory"))
    status_text = _perguntas_ia_v2_grounding_texto(json.dumps(result, ensure_ascii=False, default=str)[:3000])
    if result.get("error") or not found or any(marker in status_text for marker in ("http 403", "http status 403", "status code 403")):
        return
    if function_name in {"context_hub_search", "context_hub_store_sku_read"}:
        _grounding_context_hub(grounding, result.get("results") if isinstance(result.get("results"), list) else [])
        return
    if function_name == "local_memory_and_rules":
        _grounding_add(grounding, ("product", "target_vehicle", "equivalence"), result.get("memory"), "approved_sku_memory", "approved_internal_memory")
        return
    internal_sources = {
        "get_mercado_livre_listing": ("mercado_livre_api", "internal_listing"),
        "get_product_data": ("internal_product_registry", "internal_catalog"),
        "get_bling_product": ("bling_product", "internal_catalog"),
    }
    if function_name in internal_sources:
        source_type, authority = internal_sources[function_name]
        _grounding_add(grounding, ("product",), json.dumps(result, ensure_ascii=False, default=str), source_type, authority)
        return
    if function_name in {"web_search_product_identity", "web_search_question_context"}:
        compiled = (
            result.get("product_research_evidence")
            if isinstance(result.get("product_research_evidence"), list)
            else []
        )
        verified = (
            result.get("verified_product_evidence")
            if isinstance(result.get("verified_product_evidence"), list)
            else []
        )
        verified_target = (
            result.get("verified_target_evidence")
            if function_name == "web_search_question_context"
            and isinstance(result.get("verified_target_evidence"), list)
            else []
        )
        if compiled:
            _grounding_product_research_evidence(grounding, compiled)
        if verified:
            _grounding_verified_product_evidence(grounding, verified)
        if verified_target:
            _grounding_verified_target_evidence(grounding, verified_target, target_identity)
        if context:
            _grounding_web(grounding, function_name, context)


def _perguntas_ia_v2_grounding_coletar(
    tool_results: list[dict[str, Any]],
    agent_input: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    grounding: dict[str, Any] = {
        "product": [], "target_vehicle": [], "equivalence": [], "legacy_unverified": [], "urls": {}, "sources": [],
    }
    entry = agent_input if isinstance(agent_input, dict) else {}
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
    snapshot = json.dumps({
        "title": item.get("title") or context.get("titulo") or "",
        "description": item.get("description") or context.get("descricao") or "",
        "attributes": item.get("attributes") or [],
    }, ensure_ascii=False, default=str)
    _grounding_add(grounding, ("product",), snapshot, "listing_snapshot", "internal_listing")
    target_identity = _perguntas_ia_v2_alvo_compatibilidade(entry)
    for tool in tool_results or []:
        if isinstance(tool, dict):
            _collect_tool_grounding(grounding, tool, target_identity)
    grounding["sources"] = grounding["sources"][:16]
    grounding["target"] = copy.deepcopy(grounding["target_vehicle"])
    return grounding

def _perguntas_ia_v2_grounding_campo_suportado(campo: object, texto_norm: str) -> bool:
    candidato = _perguntas_ia_v2_grounding_texto(campo)
    if not candidato:
        return True
    if candidato in texto_norm:
        return True
    tokens = [
        token for token in candidato.split()
        if token not in {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "para", "com"}
    ]
    return bool(len(tokens) >= 2 and all(re.search(rf"\b{re.escape(token)}\b", texto_norm) for token in tokens))

def _perguntas_ia_v2_grounding_evidencia(
    grupo: str,
    registro: dict[str, Any],
    grounding: dict[str, Any],
) -> Optional[dict[str, Any]]:
    url_key = _perguntas_ia_v2_grounding_url_key(registro.get("url"))
    candidatos = grounding.get(grupo) if isinstance(grounding.get(grupo), list) else []
    if url_key:
        candidatos = [fonte for fonte in candidatos if str(fonte.get("url") or "") == url_key]
        if not candidatos:
            return None
    campos_factuais = [
        registro.get(campo)
        for campo in ("reference", "fact", "claim", "snippet")
        if str(registro.get(campo) or "").strip()
    ]
    if not campos_factuais:
        return None
    for fonte in candidatos:
        texto_norm = str(fonte.get("text_norm") or "")
        if not texto_norm or not all(_perguntas_ia_v2_grounding_campo_suportado(campo, texto_norm) for campo in campos_factuais):
            continue
        saida = dict(registro)
        saida["source_type"] = fonte.get("source_type") or saida.get("source_type") or "collected_source"
        saida["authority"] = fonte.get("authority") or "collected_source"
        saida["grounded"] = True
        if fonte.get("url"):
            saida["url"] = fonte.get("url")
        else:
            saida.pop("url", None)
        for key in ("target_identity", "field_name", "activation_policy", "supporting_sources"):
            if fonte.get(key) not in (None, "", [], {}):
                saida[key] = copy.deepcopy(fonte.get(key))
        return saida
    return None

def _perguntas_ia_v2_evidencias_normalizar(
    valor: Any,
    grounding: Optional[dict[str, Any]] = None,
) -> dict[str, list[dict[str, Any]]]:
    origem = valor if isinstance(valor, dict) else {}
    saida: dict[str, list[dict[str, Any]]] = {"product": [], "target_vehicle": [], "equivalence": []}
    for grupo in saida:
        chave_origem = grupo
        if grupo == "target_vehicle" and not isinstance(origem.get(grupo), list):
            chave_origem = "target"
        itens = origem.get(chave_origem) if isinstance(origem.get(chave_origem), list) else []
        for item in itens[:8]:
            if isinstance(item, str):
                registro = {"reference": item[:800]}
            elif isinstance(item, dict):
                registro = {
                    chave: item.get(chave)
                    for chave in (
                        "source_type", "authority", "reference", "title", "url", "snippet",
                        "fact", "claim", "status", "http_status", "status_code", "grounded", "derived_from",
                    )
                    if item.get(chave) not in (None, "", [], {})
                }
            else:
                continue
            if not registro:
                continue
            status = _favoritos_normalizar_sem_acentos(str(registro.get("status") or ""))
            if status in {"error", "erro", "failed", "failure", "falha", "empty", "not_found", "sem_resultado"}:
                continue
            try:
                http_status = int(registro.get("http_status") or registro.get("status_code") or 0)
            except Exception:
                http_status = 0
            texto_evidencia = _favoritos_normalizar_sem_acentos(" ".join(
                str(registro.get(campo) or "")
                for campo in ("reference", "title", "url", "snippet", "fact", "claim")
            ))
            if http_status >= 400 or any(
                marcador in texto_evidencia
                for marcador in ("http 403", "erro 403", "sem resultado", "nenhum resultado", "busca falhou")
            ):
                continue
            if not texto_evidencia:
                continue
            if isinstance(grounding, dict):
                registro_aterrado = _perguntas_ia_v2_grounding_evidencia(grupo, registro, grounding)
                if not registro_aterrado:
                    continue
                registro = registro_aterrado
            saida[grupo].append(registro)
    saida["target"] = copy.deepcopy(saida["target_vehicle"])
    return saida

def _perguntas_ia_v2_compatibilidade_padrao(agent_input: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    entrada = agent_input if isinstance(agent_input, dict) else {}
    perfil = _perguntas_ia_v2_perfil_compatibilidade(entrada)
    alvo = _perguntas_ia_v2_alvo_compatibilidade(entrada)
    classificada = _perguntas_ia_compatibilidade_classificada(entrada)
    missing_fields = classificada.get("missing_fields") if isinstance(classificada.get("missing_fields"), list) else []
    return {
        "product_interface": "",
        "target_type": perfil.get("target_type") or "",
        "target_item": alvo,
        "target_vehicle": alvo,
        "compatibility_profile": perfil.get("compatibility_profile") or "",
        "target_interface": "",
        "comparison_attributes": [],
        "decision": "insufficient",
        "condition": "",
        "missing_fields": [str(item or "").strip()[:160] for item in missing_fields if str(item or "").strip()][:12],
        "evidence": {"product": [], "target": [], "target_vehicle": [], "equivalence": []},
        "queries": [],
        "sources": [],
        "confidence": 0.0,
        "reason": "compatibility_analysis_not_completed",
        "_classification_bound": bool(
            _perguntas_ia_categoria_classificada(entrada) == QuestionCategory.COMPATIBILITY.value
            and classificada.get("aplicavel") is True
        ),
    }
