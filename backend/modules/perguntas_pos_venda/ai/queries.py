"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from backend.services.marketplace_tools import integrations as marketplace_integrations

from .runtime import (
    Any,
    Optional,
    QuestionCategory,
    _PERGUNTAS_IA_COMPATIBILITY_PROFILES,
    _PERGUNTAS_IA_TARGET_TYPES,
    _favoritos_busca_externa_extrair_codigos,
    _ia_web_busca_ativa,
    _ml_api_request,
    _normalizar_texto,
    _obter_cfg_ml,
    resolve_runtime_adapter,
    json,
    logger,
    normalize_profile,
    normalize_target_type,
    os,
    quote,
    re,
    requests,
    requests_tls_verify,
    urlparse,
)
from .inputs import (
    _perguntas_ia_allowed_tools_classificadas,
    _perguntas_ia_categoria_classificada,
    _perguntas_ia_compatibilidade_classificada,
    _perguntas_ia_deve_buscar_web_publica,
)

def _ia_agent_perguntas_texto_busca(agent_input: dict) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    partes = [
        question.get("text"),
        question.get("item_id"),
        item.get("id"),
        item.get("seller_sku"),
        item.get("title"),
        item.get("description"),
        context.get("sku"),
        context.get("item_id"),
        context.get("titulo"),
        context.get("descricao"),
    ]
    texto = " ".join([str(parte or "").strip() for parte in partes if str(parte or "").strip()])
    return texto[:1200]

def _ia_agent_perguntas_precisa_web(agent_input: dict) -> bool:
    if not _ia_web_busca_ativa():
        return False
    allowed = _perguntas_ia_allowed_tools_classificadas(agent_input)
    allowed_set = {str(item or "").strip() for item in allowed}
    if not (allowed_set & {"web_search", "web_search_product_identity", "web_search_question_context"}):
        return False
    required = _perguntas_ia_deve_buscar_web_publica(agent_input)
    return bool(required and _ia_agent_perguntas_texto_busca(agent_input))

def _ia_agent_perguntas_adicionar_parte_busca(parte: object, destino: list[str], vistos: set[str], limite: int = 180) -> None:
    texto = re.sub(r"\s+", " ", str(parte or "").strip())
    if not texto:
        return
    chave = _normalizar_texto(texto)
    if not chave or chave in vistos:
        return
    vistos.add(chave)
    destino.append(texto[:limite])

def _ia_agent_perguntas_query_web(agent_input: dict, tool_results: list[dict]) -> str:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}

    def _texto_busca_pergunta(valor: str) -> str:
        del valor
        if _perguntas_ia_categoria_classificada(agent_input) == QuestionCategory.COMPATIBILITY.value:
            return _perguntas_ia_v2_alvo_compatibilidade(agent_input)
        return _perguntas_ia_v2_foco_tecnico_pergunta(agent_input)

    codigos = _ia_agent_perguntas_codigos_web(agent_input, tool_results)
    partes: list[str] = []
    vistos: set[str] = set()
    _ia_agent_perguntas_adicionar_parte_busca(item.get("title"), partes, vistos)
    _ia_agent_perguntas_adicionar_parte_busca(item.get("seller_sku") or item.get("sku"), partes, vistos)
    for codigo in codigos[:4]:
        _ia_agent_perguntas_adicionar_parte_busca(codigo, partes, vistos)
    _ia_agent_perguntas_adicionar_parte_busca(_texto_busca_pergunta(question.get("text")), partes, vistos)
    for resultado in tool_results or []:
        if not isinstance(resultado, dict):
            continue
        matches = ((resultado.get("result") or {}).get("matches") or [])
        if not matches:
            continue
        primeiro = {}
        for match in matches:
            if isinstance(match, dict) and _ia_agent_perguntas_match_relevante_web(agent_input, match):
                primeiro = match
                break
        if not primeiro:
            continue
        for valor in (
            primeiro.get("nome"),
            primeiro.get("title"),
            primeiro.get("sku"),
            primeiro.get("id"),
            primeiro.get("id_bling"),
            primeiro.get("mlb_principal"),
            primeiro.get("marca"),
            primeiro.get("categoria"),
        ):
            _ia_agent_perguntas_adicionar_parte_busca(valor, partes, vistos)
    consulta = " ".join([str(parte or "").strip() for parte in partes if str(parte or "").strip()])
    consulta = re.sub(r"\s+", " ", consulta).strip()
    if not consulta:
        return ""
    if (
        _perguntas_ia_categoria_classificada(agent_input) == QuestionCategory.COMPATIBILITY.value
        and "compat" not in _normalizar_texto(consulta)
    ):
        consulta += " compatibilidade especificacao aplicacao"
    return consulta[:500]

def _perguntas_ia_v2_texto_busca_curto(valor: object, max_palavras: int = 14, max_chars: int = 180) -> str:
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", str(valor or ""), flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU\s*[:#-]?\s*[A-Z0-9._/-]+\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"https?://\S+", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[^0-9A-Za-zÀ-ÿ+./-]+", " ", texto)
    palavras = [parte for parte in texto.split() if parte]
    return " ".join(palavras[:max(1, int(max_palavras or 14))])[:max_chars].strip()

def _perguntas_ia_v2_foco_tecnico_pergunta(agent_input: dict) -> str:
    compatibilidade = _perguntas_ia_compatibilidade_classificada(agent_input)
    focus = str(compatibilidade.get("technical_focus") or "").strip()
    return _perguntas_ia_v2_texto_busca_curto(focus, max_palavras=16, max_chars=180)

def _perguntas_ia_v2_perfil_compatibilidade(agent_input: Optional[dict[str, Any]] = None) -> dict[str, str]:
    compatibilidade = _perguntas_ia_compatibilidade_classificada(agent_input)
    target_type_raw = str(compatibilidade.get("target_type") or "").strip()
    target_type = normalize_target_type(target_type_raw) if target_type_raw else ""
    if target_type not in _PERGUNTAS_IA_TARGET_TYPES:
        target_type = ""
    profile_raw = str(compatibilidade.get("compatibility_profile") or "").strip()
    profile = normalize_profile(profile_raw, target_type or "generic") if profile_raw else ""
    if profile not in _PERGUNTAS_IA_COMPATIBILITY_PROFILES:
        profile = ""
    return {"target_type": target_type, "compatibility_profile": profile}

def _perguntas_ia_v2_alvo_compatibilidade(agent_input: dict) -> str:
    compatibilidade = _perguntas_ia_compatibilidade_classificada(agent_input)
    alvo = str(compatibilidade.get("target_item") or "").strip()
    return _perguntas_ia_v2_texto_busca_curto(alvo, max_palavras=10, max_chars=120) if alvo else ""

def _ia_agent_perguntas_valor_codigo_web(valor: object) -> str:
    texto = re.sub(r"\s+", " ", str(valor or "").strip()).strip(" ,;|")
    if not texto:
        return ""
    if len(texto) > 80:
        return ""
    norm = re.sub(r"[^A-Z0-9]", "", texto.upper())
    if len(norm) < 4:
        return ""
    if norm in {"NONE", "NULL", "NAN", "TRUE", "FALSE"}:
        return ""
    if re.fullmatch(r"(19|20)\d{2}", norm):
        return ""
    return texto

def _ia_agent_perguntas_codigo_norm_web(valor: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())

def _ia_agent_perguntas_adicionar_codigo_web(valor: object, codigos: list[str], vistos: set[str]) -> None:
    texto = _ia_agent_perguntas_valor_codigo_web(valor)
    if not texto:
        return
    for parte in re.split(r"[,;|]", texto):
        parte = _ia_agent_perguntas_valor_codigo_web(parte)
        if not parte:
            continue
        chave = _ia_agent_perguntas_codigo_norm_web(parte)
        if chave in vistos:
            continue
        vistos.add(chave)
        codigos.append(parte[:60])
        if len(codigos) >= 10:
            return

def _ia_agent_perguntas_match_relevante_web(agent_input: dict, match: dict) -> bool:
    if not isinstance(match, dict):
        return False
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}

    campos_codigo_base = ("sku", "seller_sku", "item_id", "id", "mlb_principal", "mlb_ids", "gtin", "ean", "codigo", "code")
    codigos_base = set()
    for origem in (question, item, context):
        for campo in campos_codigo_base:
            if origem is question and campo == "id":
                continue
            valor = origem.get(campo)
            for parte in re.split(r"[,;|]", str(valor or "")):
                codigo = _ia_agent_perguntas_valor_codigo_web(parte)
                if codigo:
                    codigos_base.add(_ia_agent_perguntas_codigo_norm_web(codigo))

    codigos_match = set()
    for campo in (
        "sku", "seller_sku", "id", "item_id", "mlb_principal", "mlb_ids", "id_bling",
        "gtin", "ean", "codigo", "code", "referencia", "oem", "part_number",
    ):
        valor = match.get(campo)
        for parte in re.split(r"[,;|]", str(valor or "")):
            codigo = _ia_agent_perguntas_valor_codigo_web(parte)
            if codigo:
                codigos_match.add(_ia_agent_perguntas_codigo_norm_web(codigo))
    for variacao in (match.get("variations") or [])[:5]:
        if isinstance(variacao, dict):
            for campo in ("sku", "seller_sku", "id", "gtin", "ean", "codigo"):
                codigo = _ia_agent_perguntas_valor_codigo_web(variacao.get(campo))
                if codigo:
                    codigos_match.add(_ia_agent_perguntas_codigo_norm_web(codigo))

    if codigos_base and codigos_match and codigos_base & codigos_match:
        return True

    titulo_base = " ".join([
        str(item.get("title") or ""),
        str(context.get("titulo") or ""),
    ])
    texto_match = " ".join([
        str(match.get("nome") or ""),
        str(match.get("title") or ""),
        str(match.get("descricao") or match.get("description") or ""),
    ])
    stopwords = {
        "DE", "DA", "DO", "DAS", "DOS", "PARA", "COM", "SEM", "POR", "UMA", "UM",
        "KIT", "NOVO", "ORIGINAL", "PRODUTO", "PECA", "PEÇA", "AUTOMOTIVO",
        "AUTOMOTIVA", "AUTO", "CARRO", "VEICULO", "VEÍCULO", "MOTOR",
    }
    genericos = stopwords | {
        "SENSOR", "TEMPERATURA", "VALVULA", "VÁLVULA", "FILTRO", "BOMBA",
        "INTERRUPTOR", "BOTAO", "BOTÃO", "CHAVE", "CABO", "MANGUEIRA",
        "SUPORTE", "TAMPA", "TRAVA", "CONEXAO", "CONEXÃO", "RESERVATORIO",
        "RESERVATÓRIO", "CONDICIONADO", "EVAPORADOR",
    }
    tokens_base = {
        token for token in re.findall(r"[A-Z0-9]{3,}", _normalizar_texto(titulo_base))
        if token not in stopwords and not re.fullmatch(r"(19|20)\d{2}", token)
    }
    tokens_match = {
        token for token in re.findall(r"[A-Z0-9]{3,}", _normalizar_texto(texto_match))
        if token not in stopwords and not re.fullmatch(r"(19|20)\d{2}", token)
    }
    if not tokens_base:
        return True
    intersecao = tokens_base & tokens_match
    tokens_base_fortes = {
        token for token in tokens_base
        if token not in genericos and (any(ch.isdigit() for ch in token) or len(token) <= 5)
    }
    if tokens_base_fortes:
        return bool(tokens_base_fortes & tokens_match)
    return len(intersecao) >= 3 and (len(intersecao) / max(1, len(tokens_base))) >= 0.5

def _ia_agent_perguntas_codigos_web(agent_input: dict, tool_results: list[dict]) -> list[str]:
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    codigos: list[str] = []
    vistos: set[str] = set()

    campos_codigo_produto = (
        "sku", "seller_sku", "id", "item_id", "mlb", "mlb_id", "mlb_principal", "mlb_ids",
        "id_bling", "codigo", "code", "codigo_produto", "codigo_do_produto",
        "referencia", "referência", "ref", "oem", "codigo_oem", "part_number",
        "numero_peca", "numero_da_peca", "ean", "gtin", "barcode", "codigo_barras",
        "catalog_product_id", "product_id",
    )
    campos_codigo_pergunta = tuple(campo for campo in campos_codigo_produto if campo != "id")
    for origem, campos in (
        (question, campos_codigo_pergunta),
        (item, campos_codigo_produto),
        (context, campos_codigo_produto),
    ):
        for campo in campos:
            _ia_agent_perguntas_adicionar_codigo_web(origem.get(campo), codigos, vistos)

    textos_para_extrair = [
        question.get("text"),
        item.get("title"),
        item.get("description"),
        context.get("titulo"),
        context.get("descricao"),
    ]
    for resultado in tool_results or []:
        if not isinstance(resultado, dict):
            continue
        result = resultado.get("result") if isinstance(resultado.get("result"), dict) else {}
        for match in (result.get("matches") or [])[:3]:
            if not isinstance(match, dict):
                continue
            if not _ia_agent_perguntas_match_relevante_web(agent_input, match):
                continue
            for campo in campos_codigo_produto:
                _ia_agent_perguntas_adicionar_codigo_web(match.get(campo), codigos, vistos)
            for variacao in (match.get("variations") or [])[:5]:
                if isinstance(variacao, dict):
                    for campo in ("sku", "seller_sku", "id", "codigo", "ean", "gtin"):
                        _ia_agent_perguntas_adicionar_codigo_web(variacao.get(campo), codigos, vistos)
            textos_para_extrair.extend([
                match.get("nome"),
                match.get("title"),
                match.get("descricao"),
                match.get("description"),
                match.get("titulos_anuncios_mlb"),
            ])

    for texto in textos_para_extrair:
        for codigo in _favoritos_busca_externa_extrair_codigos(str(texto or "")):
            _ia_agent_perguntas_adicionar_codigo_web(codigo, codigos, vistos)
        for codigo in re.findall(r"\b\d{8,14}\b", str(texto or "")):
            _ia_agent_perguntas_adicionar_codigo_web(codigo, codigos, vistos)
        if len(codigos) >= 10:
            break
    return codigos[:10]

def _ia_agent_perguntas_slug_link_produto(permalink: object) -> str:
    url = str(permalink or "").strip()
    if not url:
        return ""
    try:
        caminho = urlparse(url).path or ""
    except Exception:
        caminho = url
    slug = os.path.basename(caminho).strip()
    slug = re.sub(r"_JM$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"\bMLB[-_ ]?\d{5,}\b", " ", slug, flags=re.IGNORECASE)
    slug = slug.replace("-", " ").replace("_", " ")
    slug = re.sub(r"\s+", " ", slug).strip()
    if len(slug) < 8:
        return ""
    return slug[:180]

def _perguntas_ia_v2_interface_busca(agent_input: dict, tool_results: Optional[list[dict]] = None) -> str:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    textos = [
        item.get("description"),
        context.get("descricao"),
        item.get("title"),
        context.get("titulo"),
    ]
    for tool in tool_results or []:
        if not isinstance(tool, dict):
            continue
        result = tool.get("result") if isinstance(tool.get("result"), dict) else {}
        textos.extend([result.get("memory"), result.get("context")])
        for match in (result.get("matches") or [])[:3]:
            if not isinstance(match, dict):
                continue
            textos.extend([
                match.get("description"), match.get("descricao"), match.get("title"), match.get("nome"),
                json.dumps(match.get("attributes") or [], ensure_ascii=False, default=str),
            ])
    bloco = re.sub(r"\s+", " ", " ".join(str(texto or "") for texto in textos if str(texto or "").strip())).strip()
    if not bloco:
        return ""
    padroes = (
        r"\b(?:(?:base|suporte|prepara[cç][aã]o)\s+(?:original\s+)?(?:bmw\s+)?(?:garmin\s+)?)?navigator\s+(?:vi|iv|v|iii|ii|i|[1-9])(?:(?:\s*[,/+-]\s*|\s+e\s+|\s+ou\s+)(?:vi|iv|v|iii|ii|i|[1-9])){0,5}\b",
        r"\b(?:usb\s*[- ]?\s*c|type\s*c|tipo\s*c|micro\s*[- ]?\s*usb|lightning)\b",
        r"\b(?:conector|base|encaixe|interface)\s+(?:de\s+)?(?:\d{1,3}\s*)?(?:pinos?|pins?|[a-z][a-z0-9+./-]{2,24})\b",
        r"\b(?:eixo|haste)\s+(?:de\s+)?\d+(?:[.,]\d+)?\s*(?:mm|cm|polegadas?|pol\.?|in)\b",
        r"\b\d{1,3}\s*(?:estrias?|dentes?|pinos?|furos?)\b",
        r"\b(?:rosca\s*)?(?:m\d{2,3}|\d+\s*/\s*\d+\s*(?:polegadas?|pol\.?|in))\b",
        r"\b(?:110|127|220|230|240)\s*v(?:olts?)?\b|\b(?:bivolt|50\s*/?\s*60\s*hz)\b",
        r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm|polegadas?|pol\.?|in)\b",
        r"\b(?:bluetooth|wifi|wi-fi|hdmi|displayport|magsafe|canbus|carplay|android\s*auto)\b",
    )
    for padrao in padroes:
        match = re.search(padrao, bloco, flags=re.IGNORECASE)
        if match:
            return _perguntas_ia_v2_texto_busca_curto(match.group(0), max_palavras=12, max_chars=120)
    return ""

def _ia_agent_perguntas_queries_identificacao_produto(
    agent_input: dict,
    tool_results: Optional[list[dict]] = None,
) -> list[dict]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    titulo = (
        item.get("title")
        or context.get("titulo")
        or _ia_agent_perguntas_slug_link_produto(item.get("permalink") or context.get("permalink") or context.get("link"))
    )
    base = _perguntas_ia_v2_texto_busca_curto(titulo, max_palavras=9, max_chars=120)
    if not base:
        return []
    interface = _perguntas_ia_v2_interface_busca(agent_input, tool_results)
    foco = _perguntas_ia_v2_foco_tecnico_pergunta(agent_input)
    complemento = interface or foco
    detalhe = f" {complemento}" if complemento and _normalizar_texto(complemento) not in _normalizar_texto(base) else ""
    queries = [{
        "type": "product_interface_identity",
        "query": f"{base}{detalhe} ficha tecnica catalogo fabricante"[:260],
    }]
    tentativa = max(1, int(agent_input.get("research_attempt") or 1))
    if tentativa > 1:
        codigos = _ia_agent_perguntas_codigos_web(agent_input, list(tool_results or []))
        codigo = next((str(value or "").strip() for value in codigos if str(value or "").strip()), "")
        sufixos = (
            "manual servico pdf part number",
            "catalogo OEM aplicacao referencia cruzada",
            "datasheet especificacoes tecnicas fabricante",
            "service manual technical specifications",
        )
        sufixo = sufixos[(tentativa - 2) % len(sufixos)]
        queries.append({
            "type": "product_identity_retry",
            "query": f"{codigo or base} {complemento or ''} {sufixo}"[:260],
        })
    return queries[:2]

def _ia_agent_perguntas_queries_web(agent_input: dict, tool_results: list[dict]) -> list[dict]:
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    titulo = _perguntas_ia_v2_texto_busca_curto(
        item.get("title") or context.get("titulo"),
        max_palavras=12,
        max_chars=160,
    )
    pergunta_compatibilidade = (
        _perguntas_ia_categoria_classificada(agent_input) == QuestionCategory.COMPATIBILITY.value
    )
    foco_tecnico = _perguntas_ia_v2_foco_tecnico_pergunta(agent_input)
    alvo = _perguntas_ia_v2_alvo_compatibilidade(agent_input) if pergunta_compatibilidade else ""
    interface = _perguntas_ia_v2_interface_busca(agent_input, tool_results)
    target_type = _perguntas_ia_v2_perfil_compatibilidade(agent_input).get("target_type") or ""
    termos_perfil = {
        "vehicle": "interface base conector preparacao ano versao",
        "machine_tool": "eixo estrias rosca diametro fixacao",
        "phone_computing": "modelo geracao dimensoes conector protocolo",
        "electrical_electronic": "tensao frequencia potencia conector",
        "hydraulic": "medida rosca diametro pressao padrao",
        "dimensional": "medidas furacao encaixe fixacao",
        "generic": "interface encaixe conexao medida codigo",
    }.get(target_type, "")
    titulo_normalizado = _normalizar_texto(titulo)
    if target_type == "vehicle" and "BOMBA" in titulo_normalizado and "COMBUST" in titulo_normalizado:
        termos_perfil = "pressao vazao tensao codigo OEM aplicacao motor ano"
    codigos = _ia_agent_perguntas_codigos_web(agent_input, tool_results)
    sku_norm = _ia_agent_perguntas_codigo_norm_web(item.get("seller_sku") or item.get("sku"))
    codigos_tecnicos = [
        codigo for codigo in codigos
        if not re.fullmatch(r"MLB\d+", _ia_agent_perguntas_codigo_norm_web(codigo), flags=re.IGNORECASE)
        and _ia_agent_perguntas_codigo_norm_web(codigo) != sku_norm
    ]
    if not pergunta_compatibilidade and titulo and foco_tecnico:
        queries: list[dict] = []
        produto_base = _perguntas_ia_v2_texto_busca_curto(titulo, max_palavras=6, max_chars=100)
        foco_busca = " ".join(foco_tecnico.split()[:3])
        for codigo in codigos_tecnicos[:2]:
            queries.append({
                "type": "product_specification_by_code",
                "query": f'"{codigo}" {produto_base} {foco_busca}'[:260],
            })
        queries.append({
            "type": "product_feature_technical",
            "query": f"{produto_base} {foco_busca}"[:260],
        })
        return queries[:3]
    codigo_tecnico = next(
        (
            codigo for codigo in codigos_tecnicos
        ),
        "",
    )
    queries: list[dict] = []
    if alvo:
        detalhes_alvo = " ".join(
            dict.fromkeys(value for value in (foco_tecnico, interface or termos_perfil) if str(value or "").strip())
        )
        detalhe_interface = f" {detalhes_alvo}" if detalhes_alvo else ""
        queries.append({
            "type": "target_interface_official",
            "query": f"{alvo}{detalhe_interface} manual especificacoes fabricante"[:260],
        })
    if titulo:
        sufixo_codigo = f" {codigo_tecnico}" if codigo_tecnico else ""
        detalhes_produto = " ".join(
            dict.fromkeys(value for value in (foco_tecnico, interface or termos_perfil) if str(value or "").strip())
        )
        detalhe_interface = f" {detalhes_produto}" if detalhes_produto else ""
        queries.append({
            "type": "product_interface_technical",
            "query": f"{_perguntas_ia_v2_texto_busca_curto(titulo, max_palavras=8, max_chars=110)}{sufixo_codigo}{detalhe_interface} especificacoes fabricante"[:260],
        })
    if titulo and alvo:
        produto_equivalencia = " ".join(
            value
            for value in (
                interface or _perguntas_ia_v2_texto_busca_curto(titulo, max_palavras=8, max_chars=100),
                foco_tecnico,
            )
            if value
        )
        queries.append({
            "type": "interface_equivalence",
            "query": f"{alvo} {produto_equivalencia} compatibilidade interface oficial"[:260],
        })
    tentativa = max(1, int(agent_input.get("research_attempt") or 1))
    if tentativa > 1:
        historico = agent_input.get("research_history") if isinstance(agent_input.get("research_history"), list) else []
        consultas_anteriores = {
            _normalizar_texto(item)
            for tentativa_anterior in historico
            if isinstance(tentativa_anterior, dict)
            for item in (tentativa_anterior.get("queries") or [])
            if str(item or "").strip()
        }
        estrategias = (
            ("official_pdf_retry", "manual servico pdf catalogo OEM"),
            ("cross_reference_retry", "part number cross reference aplicacao"),
            ("technical_spec_retry", "datasheet especificacoes tecnicas fabricante"),
            ("target_service_retry", "service manual especificacao tecnica"),
        )
        tipo_retry, sufixo_retry = estrategias[(tentativa - 2) % len(estrategias)]
        bases_retry = [
            " ".join(value for value in (codigo_tecnico, titulo) if value),
            " ".join(value for value in (alvo, foco_tecnico or interface) if value),
            " ".join(value for value in (codigo_tecnico, alvo) if value),
        ]
        for base_retry in bases_retry:
            consulta_retry = re.sub(r"\s+", " ", f"{base_retry} {sufixo_retry}").strip()[:260]
            if not consulta_retry or _normalizar_texto(consulta_retry) in consultas_anteriores:
                continue
            queries.append({"type": tipo_retry, "query": consulta_retry})
    return queries[:6]

def _ia_agent_perguntas_relaxar_query_web(query: str) -> str:
    texto = str(query or "")
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU[-_/A-Z0-9]{2,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\b\d{8,14}\b", " ", texto)
    texto = re.sub(r"\b[A-Z]{2,8}[-./][A-Z0-9]{3,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[\"']+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:500]

def _ia_agent_perguntas_query_ml_publica(query: str) -> str:
    texto = str(query or "")
    texto = re.sub(r"\bMLB[\s_-]*\d{5,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\bSKU[-_/A-Z0-9]{2,}\b", " ", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[\"']+", " ", texto)
    texto = re.sub(
        r"\b(mercado livre|anuncio|anuncios|descri[cç][aã]o|produto similar|compatibilidade|especificacao|aplicacao)\b",
        " ",
        texto,
        flags=re.IGNORECASE,
    )
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:180]

def _ia_agent_perguntas_anuncios_publicos_ml(query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
            "Origin": "https://www.mercadolivre.com.br",
            "Referer": "https://www.mercadolivre.com.br/",
        }
        resp = requests.get(
            "https://api.mercadolibre.com/sites/MLB/search",
            params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
            headers=headers,
            timeout=15,
            verify=requests_tls_verify(),
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        resultados = []
        for item in (payload.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            descricao = ""
            if item_id:
                try:
                    desc_resp = requests.get(
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        headers=headers,
                        timeout=10,
                        verify=requests_tls_verify(),
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                except Exception as exc:
                    logger.warning("[IA AGENT PERGUNTAS] Falha ao consultar descricao publica ML %s: %s", item_id, exc)
            resultados.append({
                "id": item_id,
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("permalink") or "").strip(),
                "price": item.get("price"),
                "available_quantity": item.get("available_quantity"),
                "condition": item.get("condition"),
                "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                "description": descricao[:900],
            })
        return resultados
    except Exception as exc:
        logger.warning("[IA AGENT PERGUNTAS] Falha na busca publica de anuncios ML: %s", exc)
        return []

def _ia_agent_perguntas_anuncios_ml_autenticado(client_id: str, loja: str, query: str, max_results: int = 4) -> list[dict]:
    consulta = _ia_agent_perguntas_query_ml_publica(query)
    if not consulta:
        return []
    lojas = marketplace_integrations.connected_stores(client_id, "mercadolivre", loja)
    if not lojas:
        return []
    for nome_loja in lojas[:3]:
        try:
            cfg = resolve_runtime_adapter("sources", "mercado_livre_config", _obter_cfg_ml)(client_id, nome_loja)
            resp, cfg = resolve_runtime_adapter("sources", "mercado_livre_request", _ml_api_request)(
                client_id,
                nome_loja,
                cfg,
                "GET",
                "https://api.mercadolibre.com/sites/MLB/search",
                params={"q": consulta, "limit": max(1, min(int(max_results or 4), 6))},
                timeout=18,
            )
            if resp.status_code != 200:
                continue
            payload = resp.json() or {}
            resultados = []
            for item in (payload.get("results") or [])[:max_results]:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                descricao = ""
                if item_id:
                    desc_resp, cfg = resolve_runtime_adapter("sources", "mercado_livre_request", _ml_api_request)(
                        client_id,
                        nome_loja,
                        cfg,
                        "GET",
                        f"https://api.mercadolibre.com/items/{quote(item_id, safe='')}/description",
                        timeout=10,
                    )
                    if desc_resp.status_code == 200:
                        desc_data = desc_resp.json() or {}
                        descricao = str(desc_data.get("plain_text") or desc_data.get("text") or "").strip()
                resultados.append({
                    "loja_consulta": nome_loja,
                    "id": item_id,
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("permalink") or "").strip(),
                    "price": item.get("price"),
                    "available_quantity": item.get("available_quantity"),
                    "condition": item.get("condition"),
                    "seller": ((item.get("seller") or {}).get("nickname") if isinstance(item.get("seller"), dict) else ""),
                    "description": descricao[:900],
                })
            if resultados:
                return resultados
        except Exception as exc:
            logger.warning("[IA AGENT PERGUNTAS] Falha na busca autenticada de anuncios ML (%s): %s", nome_loja, exc)
            continue
    return []
