"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

import copy
import re
import unicodedata

from backend.services.favoritos_ml import _favoritos_ml_url_item_id
from backend.services.mercadolivre_legacy_items import _ml_extrair_sku
from backend.services.vin_transient import contains_vin_like_identifier

from .attachments import message_attachments as _ml_pos_venda_mensagem_anexos
from .input_contracts import (
    VEHICLE_IDENTITY_PROMPT_FIELDS as _VEHICLE_IDENTITY_PROMPT_FIELDS,
    build_input_operational_contract,
)
from .deep_research_contracts import safe_agent_product_research_evidence

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
    _PERGUNTAS_IA_COMMERCIAL_STATE_POLICY,
    _PERGUNTAS_IA_RESPONSE_POLICY,
    _PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
    _PERGUNTAS_IA_SELLER_METHOD_VERSION,
    _env_config_bool,
    _ia_agent_endpoint_api_key_configurada,
    _ia_agent_endpoint_headers,
    _ia_agent_endpoint_query_url,
    _ia_agent_endpoint_url_configurado,
    _ia_agent_engine_query_url,
    _ia_agent_http_post,
    _ia_agent_resource_name_configurado,
    _ia_treinamento_ppv_bloco_prompt,
    _ia_treinamento_ppv_profile_v2_resolver,
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
_PERGUNTAS_IA_RESEARCH_INPUT_FIELDS = frozenset({
    "_codex_job_id", "_research_session_key", "allowed_tools", "category", "classification", "context", "force_external_research",
    "intent", "item", "locale", "question", "question_plan", "gap_queries", "research_attempt", "research_directive", "research_gap_only",
    "research_gaps", "research_history", "store", "subquestions", "task", "tenant_id",
    "use_web_search", "vehicle_identity", "verified_product_evidence", "product_research_evidence", "web_search_required",
    "product_evidence_identity", "technical_question_plan", "technical_gap_queries",
    "sku_question_context", "adaptive_route", "web_research_reason",
    "_catalog_identity_proof",
})


def _perguntas_ia_research_input(agent_input: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Keep seller instructions and examples outside research/tool decisions."""

    source = agent_input if isinstance(agent_input, dict) else {}
    projected = {
        key: copy.deepcopy(source[key])
        for key in _PERGUNTAS_IA_RESEARCH_INPUT_FIELDS
        if key in source
    }
    sku_packet = projected.get("sku_question_context")
    if isinstance(sku_packet, dict):
        # V18 transports the immutable, store/SKU-bound integral envelope.
        # Research tools still receive only the lookup identity fields.
        item = projected.get("item") if isinstance(projected.get("item"), dict) else {}
        projected["item"] = {
            key: item.get(key)
            for key in ("id", "title", "seller_sku", "catalog_product_id")
            if item.get(key) not in (None, "")
        }
        projected["question"] = dict(sku_packet.get("question") or {})
        projected["context"] = {
            "sku": (sku_packet.get("identity") or {}).get("sku"),
        }
    if "technical_question_plan" in projected:
        projected["technical_question_plan"] = _perguntas_ia_technical_question_plan_seguro(
            projected.get("technical_question_plan")
        )
    if "technical_gap_queries" in projected:
        projected["technical_gap_queries"] = _perguntas_ia_technical_gap_queries_seguras(
            projected.get("technical_gap_queries")
        )
    plan_source = projected.get("technical_question_plan") or projected.get("question_plan")
    gap_source = projected.get("technical_gap_queries") or projected.get("gap_queries")
    projected["technical_question_plan"] = _perguntas_ia_technical_question_plan_seguro(plan_source)
    projected["technical_gap_queries"] = _perguntas_ia_technical_gap_queries_seguras(gap_source)
    projected.pop("question_plan", None)
    projected.pop("gap_queries", None)
    if "_research_session_key" in projected:
        projected["_research_session_key"] = re.sub(
            r"[^A-Za-z0-9_.-]", "", str(projected.get("_research_session_key") or "")
        )[:128]
    try:
        research_attempt = int(projected.get("research_attempt") or 1)
    except (TypeError, ValueError, OverflowError):
        research_attempt = 1
    if research_attempt >= 2 and (
        projected["technical_gap_queries"]
        or projected.get("research_gaps")
        or projected.get("force_external_research") is True
    ):
        projected["research_gap_only"] = True
    return projected


def _perguntas_ia_cloud_extrair_resposta_literal(valor: Any) -> str:
    """Select one complete Cloud response without normalizing its text.

    The shared legacy extractor strips strings and joins list members. That is
    useful for generic display payloads, but it mutates a public-reply draft.
    This Cloud boundary therefore uses whitespace only to decide whether a
    candidate is empty and returns the selected string byte-for-byte.
    """

    if valor is None:
        return ""
    if isinstance(valor, str):
        return valor if valor.strip() else ""
    if isinstance(valor, dict):
        for chave in ("resposta", "response", "answer", "text", "message", "content", "output", "result"):
            texto = _perguntas_ia_cloud_extrair_resposta_literal(valor.get(chave))
            if texto:
                return texto
        for chave in ("messages", "candidates", "choices"):
            lista = valor.get(chave)
            if isinstance(lista, list):
                for item in reversed(lista):
                    texto = _perguntas_ia_cloud_extrair_resposta_literal(item)
                    if texto:
                        return texto
        return ""
    if isinstance(valor, list):
        for item in reversed(valor):
            texto = _perguntas_ia_cloud_extrair_resposta_literal(item)
            if texto:
                return texto
        return ""
    if isinstance(valor, (int, float, bool)):
        return str(valor)
    return ""


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
        "official_current_listing": item.get("_ppv_official_current_listing") is True,
    }


def _perguntas_ia_pergunta_para_agente(pergunta: dict) -> dict:
    pergunta = pergunta if isinstance(pergunta, dict) else {}
    historico = pergunta.get("buyer_question_chat") if isinstance(pergunta.get("buyer_question_chat"), list) else []
    resposta_atual_literal = str(pergunta.get("_resposta_atual") or "")
    resposta_atual = resposta_atual_literal if resposta_atual_literal.strip() else ""
    return {
        "id": pergunta.get("id") or "", "text": pergunta.get("text") or "",
        "current_draft_to_avoid": resposta_atual, "item_id": pergunta.get("item_id") or "",
        "date_created": pergunta.get("date_created") or "", "status": pergunta.get("status") or "",
        "buyer_id": pergunta.get("buyer_id") or "", "buyer_name": pergunta.get("buyer_name") or "",
        "history_count": pergunta.get("buyer_question_history_count") or len(historico),
        "history_source": "Mercado Livre questions/search: mesmo comprador no mesmo anuncio",
        "history": historico[-10:],
    }


def _perguntas_ia_vehicle_identity_segura(pergunta: dict) -> dict[str, str]:
    raw = pergunta.get("_vehicle_identity") if isinstance(pergunta.get("_vehicle_identity"), dict) else {}
    if (
        str(raw.get("schema") or "") != "jk.vehicle_identity_facts.v1"
        or str(raw.get("policy") or "") != "jk_public_vin_decode_v1"
        or str(raw.get("source") or "") != "nhtsa_vpic"
        or str(raw.get("status") or "")
        not in {"confirmed", "partial", "ambiguous", "not_found", "invalid", "unavailable"}
    ):
        return {}
    safe: dict[str, str] = {}
    for field in _VEHICLE_IDENTITY_PROMPT_FIELDS:
        value = re.sub(r"\s+", " ", str(raw.get(field) or "")).strip()[:160]
        if value and not contains_vin_like_identifier(value):
            safe[field] = value
    return safe


def _perguntas_ia_product_evidence_identity_segura(pergunta: dict) -> dict[str, str]:
    raw = (
        pergunta.get("_product_evidence_identity")
        if isinstance(pergunta.get("_product_evidence_identity"), dict)
        else {}
    )
    return {
        field: re.sub(r"\s+", " ", str(raw.get(field) or "")).strip()[:160]
        for field in ("store_ref", "seller_id", "site_id", "sku", "item_id", "variation_id")
        if str(raw.get(field) or "").strip()
    }


def _perguntas_ia_verified_product_evidence_segura(pergunta: dict) -> list[dict[str, Any]]:
    raw = (
        pergunta.get("_verified_product_evidence")
        if isinstance(pergunta.get("_verified_product_evidence"), list)
        else []
    )
    projected = safe_agent_product_research_evidence(raw[:120])
    return [
        {
            "field_name": str(value.get("field_name") or "")[:96],
            "scope": str(value.get("scope") or "")[:24],
            "value": str(value.get("value") or "")[:256],
            "unit": str(value.get("unit") or "")[:16],
            "activation_policy": str(value.get("activation_policy") or "")[:64],
            "source_authorities": [
                str(authority or "")[:24]
                for authority in (value.get("source_authorities") or [])[:4]
                if str(authority or "").strip()
            ],
        }
        for value in projected
    ]


from .input_sanitization import (
    _perguntas_ia_remover_nome_comprador_texto,
    _perguntas_ia_technical_gap_queries_seguras,
    _perguntas_ia_technical_query_segura,
    _perguntas_ia_technical_question_plan_seguro,
    _perguntas_ia_texto_sem_diacriticos_com_indices,
    _perguntas_ia_v2_texto_busca_curto,
    _perguntas_ia_v2_texto_classificado_busca,
)


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

_PERGUNTAS_IA_CATEGORIAS_SEM_PESQUISA_EXTERNA = {
    QuestionCategory.POST_SALE.value,
    QuestionCategory.REGULATED_PRODUCT.value,
    QuestionCategory.UNKNOWN.value,
}

_PERGUNTAS_IA_CATEGORIAS_PESQUISA_EXTERNA_OBRIGATORIA = {
    QuestionCategory.COMPATIBILITY.value,
    QuestionCategory.WARRANTY_ORIGINALITY.value,
}

def _perguntas_ia_deve_buscar_web_publica(agent_input: Optional[dict[str, Any]]) -> bool:
    classificacao = _perguntas_ia_classificacao_agent(agent_input)
    fluxo = str(classificacao.get("fluxo") or "").strip()
    categoria = _perguntas_ia_categoria_classificada(agent_input)
    entrada = agent_input if isinstance(agent_input, dict) else {}
    packet = entrada.get("sku_question_context")
    packet = packet if isinstance(packet, dict) else {}
    packet_web = packet.get("web") if isinstance(packet.get("web"), dict) else {}
    if fluxo != "perguntas_anuncio" or not categoria:
        return False
    if categoria in _PERGUNTAS_IA_CATEGORIAS_SEM_PESQUISA_EXTERNA:
        return False
    if packet:
        return bool(packet_web.get("required"))
    if entrada.get("force_external_research") is True:
        return True
    return categoria in _PERGUNTAS_IA_CATEGORIAS_PESQUISA_EXTERNA_OBRIGATORIA

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

    commercial_facts_are_current = listing.get("_ppv_official_current_listing") is True
    if commercial_facts_are_current and "available_quantity" in listing:
        add("estoque_anuncio", listing.get("available_quantity"))
    if commercial_facts_are_current and "price" in listing:
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
    return False

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
    """O legado e ativado pelo perfil v2; este fallback cru fica permanentemente inativo."""

    del client_id, agent_input, context_hub_result, training_prompt_fn
    return ""

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
    legacy_available, legacy_hash, app_guidance, seller_profile,
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
        "commercial_method_version": _PERGUNTAS_IA_SELLER_METHOD_VERSION,
        "commercial_state_policy": {state: dict(directive) for state, directive in _PERGUNTAS_IA_COMMERCIAL_STATE_POLICY.items()},
        "seller_behavior_profile": seller_profile,
        "legacy_guidance_available": legacy_available,
        "legacy_guidance_hash": legacy_hash,
        "legacy_fallback_enabled": _perguntas_ia_legacy_guidance_fallback_enabled(),
        "legacy_fallback_used": False,
        "legacy_retirement_zero_use_days": 30,
        "question": _perguntas_ia_pergunta_para_agente(pergunta),
        "item": _perguntas_ia_item_para_agente(item, contexto_dict.get("descricao") or ""),
        "context": contexto_dict,
        "vehicle_identity": _perguntas_ia_vehicle_identity_segura(pergunta),
        "product_evidence_identity": _perguntas_ia_product_evidence_identity_segura(pergunta),
        "_catalog_identity_proof": str(pergunta.get("_catalog_identity_proof") or ""),
        "verified_product_evidence": _perguntas_ia_verified_product_evidence_segura(pergunta),
        "product_research_evidence": safe_agent_product_research_evidence(
            pergunta.get("_product_research_evidence")
            if isinstance(pergunta.get("_product_research_evidence"), list)
            else []
        ),
        "intent": intencao_atendimento,
        "classification": _perguntas_ia_classificacao_agent(classification_input),
        "category": _perguntas_ia_categoria_classificada(classification_input),
        "subquestions": list(
            _perguntas_ia_classificacao_agent(classification_input).get("subperguntas") or []
        ),
        "_codex_thread_id": str((pergunta or {}).get("_codex_thread_id") or ""),
        "_codex_job_id": str((pergunta or {}).get("_codex_job_id") or ""),
        "_research_session_key": re.sub(
            r"[^A-Za-z0-9_.-]", "", str((pergunta or {}).get("_research_session_key") or "")
        )[:128],
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
        "technical_question_plan": _perguntas_ia_technical_question_plan_seguro(
            (pergunta or {}).get("_technical_question_plan")
        ),
        "technical_gap_queries": _perguntas_ia_technical_gap_queries_seguras(
            (pergunta or {}).get("_technical_gap_queries")
        ),
        "research_gap_only": bool((pergunta or {}).get("_research_gap_only")),
        "force_external_research": bool((pergunta or {}).get("_force_external_research")),
        "research_directive": str((pergunta or {}).get("_research_directive") or "")[:1200],
        **build_input_operational_contract(
            use_web_search=usar_busca_web,
            allowed_tools=allowed_tools,
            max_chars=ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO,
        ),
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
    profile_resolver_fn=None,
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
    if tipo_treinamento == "pos_venda":
        legacy_available, legacy_hash = metadata_loader(
            client_id,
            loja,
            contexto_dict,
            tipo_treinamento,
            training_prompt_fn=prompt_loader,
        )
    else:
        # V18 retires the legacy JSON from public-question responses. It is
        # read only by the explicit migration service.
        legacy_available, legacy_hash = False, ""
    app_guidance = _PERGUNTAS_IA_RESPONSE_POLICY[tipo_treinamento]
    profile_context = _perguntas_ia_contexto_treinamento(loja, contexto_dict, tipo_treinamento)
    profile_product = dict(profile_context.get("produto") or {})
    for key in ("seller_sku", "sku", "codigo", "codigo_produto"):
        if not profile_product.get(key) and (item or {}).get(key):
            profile_product[key] = (item or {}).get(key)
    profile_context["produto"] = profile_product
    if tipo_treinamento == "pos_venda":
        profile_resolver = profile_resolver_fn or _ia_treinamento_ppv_profile_v2_resolver
        seller_profile = profile_resolver(client_id, loja, profile_context)
    else:
        seller_profile = {
            "schema": "jk_seller_behavior_profile_v2",
            "source": "context_hub_store_sku_v18_pending",
            "legacy_json_used": False,
        }
    return _build_agent_payload(
        client_id, loja, pergunta, item, contexto_dict, prompt, intencao_atendimento,
        fluxo_intencao, classification_input, allowed_tools, usar_busca_web,
        legacy_available, legacy_hash, app_guidance, seller_profile,
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
        data = _ia_agent_http_post(
            _ia_agent_endpoint_query_url(endpoint_url),
            body,
            _ia_agent_endpoint_headers(client_id, loja),
        )
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

    texto = _perguntas_ia_cloud_extrair_resposta_literal(
        data.get("output") if isinstance(data, dict) else data
    )
    resposta = texto if isinstance(texto, str) else str(texto or "")
    if not resposta.strip():
        raise PerguntasIARespostaIndisponivel("Agente Cloud nao retornou uma resposta para enviar ao comprador.")
    return resposta, origem

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
