"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from backend.services.favoritos_ml import _favoritos_ml_url_item_id
from backend.services.mercadolivre_legacy_items import _ml_extrair_sku

from .attachments import message_attachments as _ml_pos_venda_mensagem_anexos

from .runtime import (
    Any,
    PerguntasPosVendaDomainError,
    ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO,
    ML_RESPOSTA_PERGUNTA_MAX_CHARS,
    Optional,
    PerguntasIARespostaIndisponivel,
    QuestionCategory,
    Request,
    _PERGUNTAS_IA_CATEGORY_VALUES,
    _PERGUNTAS_IA_RESPONSE_POLICY,
    _PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
    _env_config_bool,
    _ia_agent_endpoint_api_key_configurada,
    _ia_agent_endpoint_headers,
    _ia_agent_endpoint_query_url,
    _ia_agent_endpoint_url_configurado,
    _ia_agent_engine_query_url,
    _ia_agent_extrair_texto,
    _ia_agent_http_post,
    _ia_agent_resource_name_configurado,
    _ia_treinamento_ppv_bloco_prompt,
    _modelo_eh_codex,
    _normalizar_ia_modelo_padrao,
    _perguntas_ia_intencao_agent,
    _perguntas_ia_limpar_resposta,
    resolve_runtime_adapter,
    _vertex_ai_headers_e_project,
    compact_json_structural,
    hashlib,
    json,
    logger,
    os,
    secrets,
)


ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS = 12000


def _perguntas_ia_item_para_agente(item: dict, descricao: str = "") -> dict:
    item = item if isinstance(item, dict) else {}
    item_id = str(item.get("id") or "").strip()
    permalink = str(item.get("permalink") or item.get("url") or item.get("link") or "").strip()
    if not permalink and item_id:
        permalink = _favoritos_ml_url_item_id(item_id)
    descricao = str(
        descricao or item.get("description") or item.get("descricao") or item.get("plain_text") or ""
    ).strip()
    atributos = [
        {
            "id": attr.get("id") or "",
            "name": attr.get("name") or "",
            "value_name": attr.get("value_name") or attr.get("value_id") or "",
        }
        for attr in (item.get("attributes") or [])[:40]
        if isinstance(attr, dict)
    ]
    sale_terms = [
        {
            "id": term.get("id") or "",
            "name": term.get("name") or "",
            "value_name": term.get("value_name") or term.get("value_id") or "",
        }
        for term in (item.get("sale_terms") or [])[:20]
        if isinstance(term, dict)
    ]
    variations = [
        {
            "id": variation.get("id") or "",
            "available_quantity": variation.get("available_quantity"),
            "price": variation.get("price"),
            "attribute_combinations": variation.get("attribute_combinations") or [],
        }
        for variation in (item.get("variations") or [])[:20]
        if isinstance(variation, dict)
    ]
    return {
        "id": item_id, "title": item.get("title") or "", "permalink": permalink,
        "link": permalink, "url": permalink, "thumbnail": item.get("thumbnail") or "",
        "price": item.get("price"), "currency_id": item.get("currency_id") or "",
        "available_quantity": item.get("available_quantity"), "status": item.get("status") or "",
        "condition": item.get("condition") or "", "category_id": item.get("category_id") or "",
        "catalog_product_id": item.get("catalog_product_id") or "",
        "listing_type_id": item.get("listing_type_id") or "", "buying_mode": item.get("buying_mode") or "",
        "seller_sku": _ml_extrair_sku(item),
        "description": descricao[:ML_PERGUNTAS_IA_DESCRICAO_AGENT_MAX_CHARS],
        "attributes": atributos, "sale_terms": sale_terms,
        "shipping": item.get("shipping") if isinstance(item.get("shipping"), dict) else {},
        "variations": variations, "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "pictures_count": len(item.get("pictures") or []) if isinstance(item.get("pictures"), list) else 0,
    }


def _perguntas_ia_pergunta_para_agente(pergunta: dict) -> dict:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    resposta_atual = str(pergunta.get("_resposta_atual") or "").strip()
    return {
        "id": pergunta.get("id") or "", "text": pergunta.get("text") or "",
        "current_draft_to_avoid": resposta_atual[:ML_RESPOSTA_PERGUNTA_MAX_CHARS], "item_id": pergunta.get("item_id") or "",
        "date_created": pergunta.get("date_created") or "", "status": pergunta.get("status") or "",
        "buyer_id": pergunta.get("buyer_id") or "", "buyer_name": pergunta.get("buyer_name") or "",
        "history_count": pergunta.get("buyer_question_history_count") or len(historico),
        "history_source": "Mercado Livre questions/search: mesmo comprador no mesmo anuncio",
        "history": historico[-10:],
    }

def _perguntas_ia_classificacao_agent(agent_input: Optional[dict[str, Any]]) -> dict[str, Any]:
    entrada = agent_input if isinstance(agent_input, dict) else {}
    context = entrada.get("context") if isinstance(entrada.get("context"), dict) else {}
    intent = entrada.get("intent") if isinstance(entrada.get("intent"), dict) else {}
    if not intent and isinstance(context.get("intencao_atendimento"), dict):
        intent = context.get("intencao_atendimento") or {}
    return dict(intent)

def _perguntas_ia_categoria_classificada(agent_input: Optional[dict[str, Any]]) -> str:
    classificacao = _perguntas_ia_classificacao_agent(agent_input)
    categoria = str(classificacao.get("categoria") or "").strip().lower()
    categoria = categoria.replace("-", "_").replace(" ", "_")
    if categoria in _PERGUNTAS_IA_CATEGORY_VALUES:
        return categoria
    return ""

def _perguntas_ia_compatibilidade_classificada(agent_input: Optional[dict[str, Any]]) -> dict[str, Any]:
    classificacao = _perguntas_ia_classificacao_agent(agent_input)
    compatibilidade = classificacao.get("compatibilidade")
    return dict(compatibilidade) if isinstance(compatibilidade, dict) else {}

def _perguntas_ia_bool_classificado(classificacao: dict[str, Any], *fields: str) -> bool:
    for field in fields:
        if field not in classificacao:
            continue
        value = classificacao.get(field)
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        if normalized in {"1", "true", "sim", "yes", "on"}:
            return True
        if normalized in {"0", "false", "nao", "não", "no", "off"}:
            return False
    return False

_PERGUNTAS_IA_CATEGORIAS_WEB_PUBLICA = {
    QuestionCategory.COMPATIBILITY.value,
    QuestionCategory.PRODUCT_FEATURE.value,
    QuestionCategory.WARRANTY_ORIGINALITY.value,
    QuestionCategory.OTHER_PRODUCT.value,
}

_PERGUNTAS_IA_CATEGORIAS_WEB_BLOQUEADA = {
    QuestionCategory.GREETING.value,
    QuestionCategory.PRICE.value,
    QuestionCategory.STOCK.value,
    QuestionCategory.SHIPPING.value,
    QuestionCategory.INVOICE.value,
    QuestionCategory.PROHIBITED_CONTACT.value,
    QuestionCategory.REGULATED_PRODUCT.value,
    QuestionCategory.POST_SALE.value,
    QuestionCategory.UNKNOWN.value,
}

def _perguntas_ia_deve_buscar_web_publica(agent_input: Optional[dict[str, Any]]) -> bool:
    classificacao = _perguntas_ia_classificacao_agent(agent_input)
    flags = classificacao.get("flags") if isinstance(classificacao.get("flags"), dict) else {}
    categoria = _perguntas_ia_categoria_classificada(agent_input)
    if categoria in _PERGUNTAS_IA_CATEGORIAS_WEB_BLOQUEADA:
        return False
    return bool(
        categoria in _PERGUNTAS_IA_CATEGORIAS_WEB_PUBLICA
        or _perguntas_ia_bool_classificado(flags, "usar_busca_web")
    )

def _perguntas_ia_allowed_tools_classificadas(agent_input: Optional[dict[str, Any]]) -> list[str]:
    classificacao = _perguntas_ia_classificacao_agent(agent_input)
    flags = classificacao.get("flags") if isinstance(classificacao.get("flags"), dict) else {}
    fluxo = str(classificacao.get("fluxo") or "").strip()
    tools: list[str] = []
    if fluxo == "perguntas_anuncio":
        tools.extend(["get_product_data", "context_hub_search"])
        if _perguntas_ia_bool_classificado(flags, "usar_mercado_livre_anuncio"):
            tools.append("get_mercado_livre_listing")
        if _perguntas_ia_bool_classificado(flags, "usar_bling"):
            tools.append("get_bling_product")
        if _perguntas_ia_deve_buscar_web_publica(agent_input):
            tools.extend(["web_search", "web_search_product_identity", "web_search_question_context"])
    return list(dict.fromkeys(tools))

def _perguntas_codex_response_provider_policy() -> str:
    policy = str(os.getenv("JK_PPV_RESPONSE_PROVIDER_POLICY") or "codex_only").strip().lower()
    return policy if policy in {"codex_only", "codex_then_configured_fallback"} else "codex_only"

def _perguntas_codex_provider_selection(
    configured_model: Any,
    operational_failure_count: Any = 0,
) -> dict[str, Any]:
    """Select Codex normally; a configured provider is only an operational fallback."""

    configured = _normalizar_ia_modelo_padrao(str(configured_model or "").strip())
    codex_model = _normalizar_ia_modelo_padrao(
        str(os.getenv("IA_PPV_CODEX_MODEL") or (configured if _modelo_eh_codex(configured) else "codex:gpt-5.5"))
    )
    try:
        failures = max(0, int(operational_failure_count or 0))
    except (TypeError, ValueError):
        failures = 0
    policy = _perguntas_codex_response_provider_policy()
    fallback_configured = configured if configured and not _modelo_eh_codex(configured) else ""
    use_fallback = bool(
        policy == "codex_then_configured_fallback"
        and failures >= 2
        and fallback_configured
    )
    return {
        "policy": policy,
        "model": fallback_configured if use_fallback else codex_model,
        "codex_model": codex_model,
        "configured_fallback": fallback_configured,
        "fallback_used": use_fallback,
        "operational_failure_count": failures,
    }

def _perguntas_codex_compact_json(value: Any, max_chars: int) -> str:
    """Compact a payload structurally and always return valid JSON."""

    limit = max(2, int(max_chars or 2))
    try:
        return compact_json_structural(
            value,
            max_bytes=limit,
            priority_paths=("question", "request", "facts", "records", "gaps", "sources", "history"),
        ).json_text
    except Exception:
        # Compatibility fallback for partial upgrades where the shared helper is unavailable.
        pass

    def encode(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    raw = encode(value)
    if len(raw) <= limit:
        return raw

    def shrink(payload: Any, *, string_limit: int, list_limit: int, depth: int = 0) -> Any:
        if depth >= 7:
            return "[compactado]"
        if isinstance(payload, dict):
            return {
                str(key): shrink(item, string_limit=string_limit, list_limit=list_limit, depth=depth + 1)
                for key, item in list(payload.items())[: max(2, list_limit)]
            }
        if isinstance(payload, (list, tuple)):
            return [
                shrink(item, string_limit=string_limit, list_limit=list_limit, depth=depth + 1)
                for item in list(payload)[:list_limit]
            ]
        if isinstance(payload, str):
            return payload if len(payload) <= string_limit else payload[: max(1, string_limit - 1)] + "…"
        return payload

    for string_limit, list_limit in ((1200, 12), (600, 8), (300, 6), (120, 4), (48, 3), (16, 2)):
        compacted = shrink(value, string_limit=string_limit, list_limit=list_limit)
        raw = encode(compacted)
        if len(raw) <= limit:
            return raw
    marker = encode({"_truncated": True})
    return marker if len(marker) <= limit else "{}"

def _perguntas_codex_public_listing_evidence(item: Any, store: str) -> list[dict[str, Any]]:
    """Extract only listing fields that directly support a response intent."""

    listing = item if isinstance(item, dict) else {}
    records: list[dict[str, Any]] = []

    def add(field: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        records.append({
            "field": field,
            "value": value,
            "store": str(store or ""),
            "source": "mercado_livre_listing",
            "authority": "confirmed",
            "coverage": "confirmed",
        })

    if "available_quantity" in listing:
        add("estoque_anuncio", listing.get("available_quantity"))
    if "price" in listing:
        add("preco_anuncio", {
            "value": listing.get("price"),
            "currency_id": listing.get("currency_id") or "",
        })
    attributes = [entry for entry in list(listing.get("attributes") or []) if isinstance(entry, dict)]
    if attributes:
        add("atributos_anuncio", attributes[:80])
    warranty_terms = []
    sale_terms = [entry for entry in list(listing.get("sale_terms") or []) if isinstance(entry, dict)]
    for term in [*attributes, *sale_terms]:
        marker = f"{term.get('id') or ''} {term.get('name') or ''}".casefold()
        if "warranty" in marker or "garantia" in marker:
            warranty_terms.append(term)
    if listing.get("warranty") not in (None, "", [], {}):
        warranty_terms.append({"value_name": listing.get("warranty")})
    if warranty_terms:
        add("garantia_anuncio", warranty_terms[:20])
    return records

def _perguntas_ia_legacy_sku_memory_reader_enabled() -> bool:
    return str(os.getenv("IA_PPV_LEGACY_SKU_MEMORY_READER_ENABLED") or "").strip().lower() in {
        "1", "true", "sim", "on", "yes",
    }

def _perguntas_ia_legacy_guidance_fallback_enabled() -> bool:
    return str(os.getenv("IA_PPV_LEGACY_GUIDANCE_FALLBACK_ENABLED") or "").strip().lower() in {
        "1", "true", "sim", "on", "yes",
    }

def _perguntas_ia_contexto_treinamento(
    loja: str,
    contexto: Optional[dict[str, Any]],
    tipo_treinamento: str,
) -> dict[str, Any]:
    contexto_dict = contexto if isinstance(contexto, dict) else {}
    return {
        "modulo": "perguntas_pos_venda",
        "tipo": "resposta_pos_venda" if tipo_treinamento == "pos_venda" else "resposta_automatica_ml",
        "tipo_treinamento": tipo_treinamento,
        "loja": str(loja or "").strip(),
        "produto": contexto_dict,
    }

def _perguntas_ia_legacy_guidance_metadata(
    client_id: str,
    loja: str,
    contexto: Optional[dict[str, Any]],
    tipo_treinamento: str,
    *,
    training_prompt_fn=None,
) -> tuple[bool, str]:
    """Detecta o legado sem transportar seu conteudo ao modelo ou aos logs."""

    prompt_loader = training_prompt_fn or _ia_treinamento_ppv_bloco_prompt
    legacy = prompt_loader(
        client_id,
        "Perguntas e pos venda",
        _perguntas_ia_contexto_treinamento(loja, contexto, tipo_treinamento),
    ).strip()
    if not legacy:
        return False, ""
    return True, hashlib.sha256(legacy.encode("utf-8", errors="ignore")).hexdigest()

def _perguntas_ia_legacy_guidance_fallback(
    client_id: str,
    agent_input: Optional[dict[str, Any]],
    context_hub_result: Optional[dict[str, Any]],
    *,
    training_prompt_fn=None,
) -> str:
    """Carrega o legado somente por opt-in e apos falha vazia nao relacionada a seguranca."""

    prompt_loader = training_prompt_fn or _ia_treinamento_ppv_bloco_prompt
    entrada = agent_input if isinstance(agent_input, dict) else {}
    if not _perguntas_ia_legacy_guidance_fallback_enabled():
        return ""
    hub = (
        context_hub_result.get("result")
        if isinstance(context_hub_result, dict) and isinstance(context_hub_result.get("result"), dict)
        else {}
    )
    reason_code = str(hub.get("reason_code") or "").strip().lower()
    if (
        not hub
        or bool(hub.get("found"))
        or int(hub.get("count") or 0) > 0
        or bool(hub.get("results"))
        or bool(hub.get("unavailable"))
        or int(hub.get("blocked_by_dlp_count") or 0) > 0
        or reason_code in {"forbidden", "security_blocked", "dlp_blocked", "tenant_mismatch"}
        or int(hub.get("authoritative_count") or 0) > 0
    ):
        return ""
    intent = _perguntas_ia_intencao_agent(entrada)
    tipo_treinamento = "pos_venda" if intent.get("fluxo") == "pos_venda" else "perguntas_anuncio"
    context = entrada.get("context") if isinstance(entrada.get("context"), dict) else {}
    legacy = prompt_loader(
        client_id,
        "Perguntas e pos venda",
        _perguntas_ia_contexto_treinamento(
            str(entrada.get("store") or entrada.get("loja") or ""),
            context,
            tipo_treinamento,
        ),
    ).strip()[:12000]
    if not legacy:
        return ""
    guidance_hash = hashlib.sha256(legacy.encode("utf-8", errors="ignore")).hexdigest()
    logger.info(
        "[PPV LEGACY FALLBACK] tenant_hash=%s guidance_hash=%s tipo=%s",
        hashlib.sha256(str(client_id or "").encode("utf-8", errors="ignore")).hexdigest()[:12],
        guidance_hash,
        tipo_treinamento,
    )
    return legacy

def _perguntas_ia_mensagens_aprovacao(pergunta: dict, loja: str) -> list[dict]:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    chat = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    mensagens = []
    for evento in chat[-20:]:
        if not isinstance(evento, dict):
            continue
        texto = str(evento.get("text") or "").strip()
        if not texto:
            continue
        role = str(evento.get("role") or evento.get("from_role") or "").strip().lower()
        mensagens.append({
            "date": evento.get("date") or evento.get("date_created") or "",
            "from_role": "seller" if role in {"seller", "loja", "store"} else "buyer",
            "text": texto,
            "attachments": [],
        })
    if mensagens:
        return mensagens
    return [{
        "date": pergunta.get("date_created") or pergunta.get("created_at") or "",
        "from_role": "buyer",
        "text": pergunta.get("text") or "",
        "attachments": _ml_pos_venda_mensagem_anexos(pergunta, loja),
    }]

def _build_agent_payload(
    client_id, loja, pergunta, item, contexto_dict, prompt, intencao_atendimento,
    fluxo_intencao, classification_input, allowed_tools, usar_busca_web,
    legacy_available, legacy_hash, app_guidance,
) -> dict:
    return {
        "task": "mercado_livre_post_sale_draft" if fluxo_intencao == "pos_venda" else "mercado_livre_public_question_draft",
        "orchestrator_profile": "mercado_livre_customer_reply",
        "locale": "pt-BR",
        "tenant_id": str(client_id or "").strip(),
        "store": str(loja or "").strip(),
        "prompt": str(prompt or "").strip(),
        "app_guidance": app_guidance[:24000],
        "app_guidance_source": _PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
        "app_guidance_truth_class": "versioned_technical",
        "app_guidance_usage": "published_behavior_policy_not_product_evidence",
        "legacy_guidance_available": legacy_available,
        "legacy_guidance_hash": legacy_hash,
        "legacy_fallback_enabled": _perguntas_ia_legacy_guidance_fallback_enabled(),
        "legacy_fallback_used": False,
        "legacy_retirement_zero_use_days": 30,
        "question": _perguntas_ia_pergunta_para_agente(pergunta),
        "item": _perguntas_ia_item_para_agente(item, contexto_dict.get("descricao") or ""),
        "context": contexto_dict,
        "intent": intencao_atendimento,
        "classification": _perguntas_ia_classificacao_agent(classification_input),
        "category": _perguntas_ia_categoria_classificada(classification_input),
        "subquestions": list(
            _perguntas_ia_classificacao_agent(classification_input).get("subperguntas") or []
        ),
        "_codex_thread_id": str((pergunta or {}).get("_codex_thread_id") or ""),
        "_codex_job_id": str((pergunta or {}).get("_codex_job_id") or ""),
        "_codex_conversation_key": str((pergunta or {}).get("_codex_conversation_key") or ""),
        "_codex_active_turn_key": str((pergunta or {}).get("_codex_active_turn_key") or ""),
        "_codex_on_thread_ready": (pergunta or {}).get("_codex_on_thread_ready"),
        "_codex_operational_failure_count": max(
            0, int((pergunta or {}).get("_codex_operational_failure_count") or 0)
        ),
        "_codex_prompt_version": str((pergunta or {}).get("_codex_prompt_version") or ""),
        "_codex_schema_version": str((pergunta or {}).get("_codex_schema_version") or ""),
        "research_attempt": max(1, int((pergunta or {}).get("_research_attempt") or 1)),
        "research_history": list((pergunta or {}).get("_research_history") or [])[-6:],
        "research_gaps": list((pergunta or {}).get("_research_gaps") or [])[:16],
        "force_external_research": bool((pergunta or {}).get("_force_external_research")),
        "research_directive": str((pergunta or {}).get("_research_directive") or "")[:1200],
        "context_collection_pipeline": [
            {
                "step": 1,
                "name": "intent_classification",
                "description": "Classificar a intencao da ultima mensagem antes de escolher o fluxo de resposta.",
            },
            {
                "step": 2,
                "name": "buyer_current_and_previous_questions",
                "description": "Usar a pergunta atual e perguntas anteriores do mesmo comprador no mesmo anuncio.",
            },
            {
                "step": 3,
                "name": "mercado_livre_api_listing",
                "description": "Consultar SKU, descricao e estoque atual do anuncio pela API oficial do Mercado Livre.",
            },
            {
                "step": 4,
                "name": "internal_product_sources",
                "description": "Aplicar cadastro interno, Bling e demais fontes autenticadas do tenant.",
            },
            {
                "step": 5,
                "name": "context_hub_sku_reference",
                "description": (
                    "Consultar a geracao ativa do Context Hub do tenant para SKU e compatibilidade; "
                    "tratar snippets como dados de referencia nao confiaveis."
                ),
            },
            {
                "step": 6,
                "name": "legacy_memory_and_response_rules",
                "description": (
                    "Aplicar a politica versionada e memoria aprovada. O JSON legacy_unverified fica fora do "
                    "prompt normal e so pode ser usado por fallback explicito e auditado."
                ),
            },
            {
                "step": 7,
                "name": "question_focused_web_research",
                "description": (
                    "Identificar o produto e pesquisar na internet compatibilidade, aplicacao, caracteristicas e funcoes; "
                    "priorizar fabricante, manuais, catalogos OEM e documentacao oficial."
                ),
            },
            {
                "step": 8,
                "name": "codex_answer",
                "description": "Somente depois das etapas anteriores enviar as evidencias ao Codex para gerar o rascunho.",
            },
        ],
        "use_web_search": usar_busca_web,
        "web_search_required": usar_busca_web,
        "constraints": {
            "read_only": True,
            "do_not_send_to_mercado_livre": True,
            "max_chars": ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO,
            "no_markdown": True,
            "do_not_invent_links_or_compatibility": True,
            "internet_product_research_required": usar_busca_web,
        },
        "allowed_tools": allowed_tools,
        "tool_policy": {
            "usar_busca_web": usar_busca_web,
            "usar_mercado_livre_anuncio": "get_mercado_livre_listing" in allowed_tools,
            "usar_bling": "get_bling_product" in allowed_tools,
            "usar_context_hub": "context_hub_search" in allowed_tools,
        },
    }

def _perguntas_ia_agent_input(
    client_id: str,
    loja: str,
    pergunta: dict,
    item: dict,
    contexto: dict,
    prompt: str,
    *,
    training_prompt_fn=None,
    legacy_metadata_fn=None,
) -> dict:
    contexto_dict = contexto if isinstance(contexto, dict) else {}
    intencao_atendimento = contexto_dict.get("intencao_atendimento") if isinstance(contexto_dict.get("intencao_atendimento"), dict) else {}
    fluxo_intencao = str(intencao_atendimento.get("fluxo") or "perguntas_anuncio").strip()
    tipo_treinamento = "pos_venda" if fluxo_intencao == "pos_venda" else "perguntas_anuncio"
    classification_input = {
        "intent": intencao_atendimento,
        "context": contexto_dict,
    }
    allowed_tools = _perguntas_ia_allowed_tools_classificadas(classification_input)
    usar_busca_web = bool(
        fluxo_intencao != "pos_venda"
        and _perguntas_ia_deve_buscar_web_publica(classification_input)
        and any(tool.startswith("web_search") for tool in allowed_tools)
    )
    prompt_loader = training_prompt_fn or _ia_treinamento_ppv_bloco_prompt
    metadata_loader = legacy_metadata_fn or _perguntas_ia_legacy_guidance_metadata
    legacy_available, legacy_hash = metadata_loader(
        client_id,
        loja,
        contexto_dict,
        tipo_treinamento,
        training_prompt_fn=prompt_loader,
    )
    app_guidance = _PERGUNTAS_IA_RESPONSE_POLICY[tipo_treinamento]
    return _build_agent_payload(
        client_id, loja, pergunta, item, contexto_dict, prompt, intencao_atendimento,
        fluxo_intencao, classification_input, allowed_tools, usar_busca_web,
        legacy_available, legacy_hash, app_guidance,
    )

def _perguntas_ia_chamar_agente_cloud(
    client_id: str,
    loja: str,
    pergunta: dict,
    item: dict,
    contexto: dict,
    prompt: str,
) -> tuple[str, str]:
    agent_input = _perguntas_ia_agent_input(client_id, loja, pergunta, item, contexto, prompt)
    endpoint_url = _ia_agent_endpoint_url_configurado()
    resource_name = _ia_agent_resource_name_configurado()
    if endpoint_url:
        body = {"classMethod": "query", "input": agent_input}
        data = _ia_agent_http_post(_ia_agent_endpoint_query_url(endpoint_url), body, _ia_agent_endpoint_headers())
        origem = "agent:endpoint"
    elif resource_name:
        headers, _project_id = _vertex_ai_headers_e_project()
        url = _ia_agent_engine_query_url(resource_name)
        body = {"classMethod": "query", "input": agent_input}
        data = _ia_agent_http_post(url, body, headers)
        origem = "agent:reasoningEngine"
    else:
        raise PerguntasIARespostaIndisponivel(
            "Agente Cloud nao configurado. Informe o endpoint ou o resource name nas configuracoes."
        )

    texto = _ia_agent_extrair_texto(data.get("output") if isinstance(data, dict) else data)
    resposta_limpa = resolve_runtime_adapter("state", "clean_response", _perguntas_ia_limpar_resposta)(texto)
    if not resposta_limpa:
        raise PerguntasIARespostaIndisponivel("Agente Cloud nao retornou uma resposta para enviar ao comprador.")
    return resposta_limpa, origem

def _ia_agent_endpoint_autorizar(request: Request) -> None:
    expected = _ia_agent_endpoint_api_key_configurada()
    if not expected:
        if _env_config_bool(("JK_AGENT_ENDPOINT_ALLOW_WITHOUT_KEY",), default=False):
            return
        raise PerguntasPosVendaDomainError(
            status_code=503,
            detail="Endpoint do agente sem chave configurada. Configure JK_AGENT_ENDPOINT_API_KEY no Cloud Run.",
        )
    auth = str(request.headers.get("authorization") or "").strip()
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    provided = str(request.headers.get("x-jk-agent-key") or bearer or "").strip()
    if not provided or not secrets.compare_digest(provided, expected):
        raise PerguntasPosVendaDomainError(status_code=401, detail="Chave do agente invalida.")

def _ia_agent_input_dict(payload: IAAgentQueryRequest) -> dict:
    entrada = payload.input
    if isinstance(entrada, dict):
        return entrada
    if isinstance(entrada, str):
        return {"prompt": entrada, "question": {"text": entrada}}
    return {}
