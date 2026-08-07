"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from backend.services.compatibility_coverage import COMPATIBILITY_COVERAGE_VERSION

from .runtime import (
    EVIDENCE_ENVELOPE_V2,
    GeminiQuestionsSettings,
    PerguntasPosVendaDomainError,
    IAChatRequest,
    ML_POS_VENDA_LIMITE_SEGURO,
    ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO,
    ML_RESPOSTA_PERGUNTA_MAX_CHARS,
    Optional,
    PerguntasIARespostaIndisponivel,
    QuestionAnswerOrchestrator,
    QuestionCategory,
    _PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
    _ia_modelo_perguntas_configurado,
    _ia_modelo_pos_venda_configurado,
    _ia_raciocinio_perguntas_configurado,
    _ia_raciocinio_pos_venda_configurado,
    _modelo_eh_codex,
    _modelo_eh_vertex_ai,
    _perguntas_ia_assinatura_loja,
    _perguntas_ia_compactar_contexto,
    _perguntas_ia_fluxo_pos_venda,
    _perguntas_ia_intencao_agent,
    _perguntas_ia_limpar_resposta,
    _perguntas_ia_memoria_bloco_prompt,
    _perguntas_ia_resposta_fallback_invalida,
    _perguntas_ia_resposta_final_loja,
    context_from_agent_input,
    copy,
    normalize_evidence_envelope,
    resolve_runtime_adapter,
    time,
)
from .context import (
    _ia_agent_perguntas_log_perf,
)
from .inputs import (
    _perguntas_codex_compact_json,
    _perguntas_codex_provider_selection,
    _perguntas_codex_public_listing_evidence,
    _perguntas_ia_categoria_classificada,
    _perguntas_ia_compatibilidade_classificada,
    _perguntas_ia_legacy_sku_memory_reader_enabled,
)
from .tools import (
    _ia_agent_perguntas_chamar_modelo,
)
from .validation import (
    ML_PERGUNTAS_IA_V2_MODO,
    _PERGUNTAS_IA_SAFE_INSUFFICIENT_VIOLATION,
    _PERGUNTAS_IA_SELLER_STYLE_PREFIX,
    _ia_agent_perguntas_violacoes_resposta,
    _ia_agent_perguntas_exige_rascunho_insuficiente_seguro,
    _ia_agent_perguntas_rascunho_insuficiente_seguro,
    _perguntas_ia_compactar_estilo_vendedor,
    _perguntas_ia_v2_exigir_aprovacao,
    _pos_venda_ia_v2_exigir_aprovacao,
)
from .clients import (
    _PerguntasCodexV3Client,
)


@dataclass(frozen=True, slots=True)
class LegacyResponseBindings:
    settings_type: object = GeminiQuestionsSettings
    orchestrator_type: object = QuestionAnswerOrchestrator
    context_factory: object = context_from_agent_input
    client_type: object = _PerguntasCodexV3Client
    log_perf: object = _ia_agent_perguntas_log_perf
    response_violations: object = _ia_agent_perguntas_violacoes_resposta
    public_model: object = _ia_modelo_perguntas_configurado
    post_sale_model: object = _ia_modelo_pos_venda_configurado
    public_reasoning: object = _ia_raciocinio_perguntas_configurado
    post_sale_reasoning: object = _ia_raciocinio_pos_venda_configurado
    is_codex: object = _modelo_eh_codex
    is_vertex: object = _modelo_eh_vertex_ai
    is_post_sale: object = _perguntas_ia_fluxo_pos_venda
    clean_response: object = _perguntas_ia_limpar_resposta
    invalid_fallback: object = _perguntas_ia_resposta_fallback_invalida
    final_response: object = _perguntas_ia_resposta_final_loja
    public_requires_approval: object = _perguntas_ia_v2_exigir_aprovacao
    post_sale_requires_approval: object = _pos_venda_ia_v2_exigir_aprovacao
    unavailable_error: object = PerguntasIARespostaIndisponivel
    call_model: object = _ia_agent_perguntas_chamar_modelo
    public_max_chars: int = ML_RESPOSTA_PERGUNTA_MAX_CHARS
    post_sale_max_chars: int = ML_POS_VENDA_LIMITE_SEGURO

def _perguntas_ia_v2_prompt(
    client_id: str,
    agent_input: dict,
    *,
    resposta_bloqueada: str = "",
    violacoes: Optional[list[str]] = None,
) -> str:
    agent_input = agent_input if isinstance(agent_input, dict) else {}
    question = agent_input.get("question") if isinstance(agent_input.get("question"), dict) else {}
    item = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    context = agent_input.get("context") if isinstance(agent_input.get("context"), dict) else {}
    intent = _perguntas_ia_intencao_agent(agent_input)
    fluxo_pos_venda = intent.get("fluxo") == "pos_venda"
    fluxo_compatibilidade = bool(
        not fluxo_pos_venda
        and _perguntas_ia_categoria_classificada(agent_input) == QuestionCategory.COMPATIBILITY.value
    )
    app_guidance = str(agent_input.get("app_guidance") or "").strip()
    memoria_sku = (
        resolve_runtime_adapter("state", "memory_prompt", _perguntas_ia_memoria_bloco_prompt)(client_id, agent_input)
        if not (fluxo_pos_venda or fluxo_compatibilidade)
        and _perguntas_ia_legacy_sku_memory_reader_enabled()
        else ""
    )
    dados = {
        "loja": agent_input.get("store") or agent_input.get("loja") or "",
        "assinatura_obrigatoria": resolve_runtime_adapter("state", "store_signature", _perguntas_ia_assinatura_loja)(str(agent_input.get("store") or agent_input.get("loja") or "")),
        "pergunta": question,
        "anuncio": {
            "id": item.get("id") or "",
            "title": item.get("title") or "",
            "description": item.get("description") or "",
            "attributes": item.get("attributes") or [],
        },
        "intencao": intent,
        "compatibilidade_classificada": {} if fluxo_pos_venda else _perguntas_ia_compatibilidade_classificada(agent_input),
        "contexto_produto": {
            "titulo": context.get("titulo") or "",
            "descricao": context.get("descricao") or "",
            "busca_outra_peca": context.get("busca_outra_peca") or {},
        },
    }
    partes = [
        "Voce e a nova IA V2 de respostas do Mercado Livre do JK Sistema.",
        "Nunca se apresente como IA, assistente, Gemini, Vertex ou JK Sistema.",
        "Responda como a equipe da loja, sem mencionar sistema interno, app, prompt ou treinamento.",
        f"A resposta deve terminar exatamente com: {resolve_runtime_adapter('state', 'store_signature', _perguntas_ia_assinatura_loja)(str(agent_input.get('store') or agent_input.get('loja') or ''))}",
        "Gere sempre UM rascunho de resposta ao comprador com as informacoes disponiveis.",
        "Nao envie, nao publique, nao altere anuncio, nao altere estoque e nao chame ferramentas externas.",
        "Use somente os dados deste prompt e das referencias read-only fornecidas pelo aplicativo: pergunta, historico, anuncio, Context Hub, memoria do SKU e contexto interno.",
        "Nao use web, nao use Bling ao vivo e nao invente dados ausentes.",
        "Responda em portugues do Brasil, sem markdown, sem tabela, sem emoji e sem aspas externas.",
        "Para pergunta publica, responda como vendedor cordial, com a informacao principal na primeira frase e no maximo tres frases de conteudo antes da assinatura.",
        f"Limite maximo: {ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO} caracteres.",
    ]
    if fluxo_pos_venda:
        partes.extend([
            "A intencao foi classificada como POS-VENDA.",
            "Nao responda como venda, compatibilidade, aplicacao ou convite de compra.",
            "Se houver defeito, troca, garantia ou mau funcionamento, reconheca o problema e responda primeiro com o que ja estiver confirmado.",
            "Evite solicitar dados; somente quando indispensavel, solicite a evidencia minima pelo detalhe da compra.",
            "Nao peca foto por padrao; solicite-a pelo detalhe da compra somente quando for indispensavel para orientar o atendimento.",
        ])
    else:
        partes.extend([
            "A intencao foi classificada como PERGUNTA DE ANUNCIO.",
            "Responda diretamente a ultima pergunta do comprador; nao reinicie o atendimento.",
            "Nao mencione SKU, codigo interno, quantidade em estoque, status do anuncio, nome da loja ou link do proprio anuncio.",
            "Em compatibilidade, compare interface, encaixe, base, conector, medida ou codigo; nao decida apenas pela lista de modelos do anuncio.",
            "Deixe a conclusao clara nas primeiras frases com redacao natural, sem palavra ou prefixo obrigatorio.",
            "Se faltar dado tecnico, responda primeiro com os fatos disponiveis. Somente quando nenhum rascunho util for possivel, identifique o perfil do alvo e solicite no maximo dois dados textuais decisivos de interface, medida, conexao, modelo ou aplicacao.",
            "Nunca mencione evidencia, analise, validacao, schema, decisao, ferramenta, sistema, interface alvo ou revisao humana ao comprador.",
            "Nunca solicite foto, imagem, anexo, arquivo, documento, PDF, video, chassi/VIN ou confirmacao generica com mecanico/oficina em pergunta publica.",
            "Se a pergunta for sobre outra peca, so informe link quando o contexto interno trouxer anuncio ativo e link.",
        ])
    if app_guidance:
        partes.append(
            "Politica de resposta versionada pelo aplicativo "
            f"(source={agent_input.get('app_guidance_source') or _PERGUNTAS_IA_RESPONSE_POLICY_VERSION}; "
            f"truth_class={agent_input.get('app_guidance_truth_class') or 'versioned_technical'}). "
            "Use para comportamento e seguranca; nunca como evidencia de compatibilidade, OEM, medida, estoque ou fato tecnico:\n"
            + app_guidance[:18000]
        )
    if memoria_sku:
        partes.append("Memoria tecnica local aprovada deste SKU:\n" + memoria_sku[:6000])
    partes.append("Dados normalizados para a resposta:\n" + _perguntas_codex_compact_json(dados, 18000))
    resposta_bloqueada = str(resposta_bloqueada or "").strip()
    if resposta_bloqueada or violacoes:
        instrucao_insuficiente = ""
        if _PERGUNTAS_IA_SAFE_INSUFFICIENT_VIOLATION in (violacoes or []):
            instrucao_insuficiente = (
                "\nA evidencia tecnica continua insuficiente. Nao conclua que serve ou que nao serve. "
                "Produza um rascunho curto com os fatos disponiveis. Evite solicitar dados; somente se nenhum "
                "rascunho util for possivel, solicite no maximo dois dados textuais decisivos de interface, "
                "codigo ou medida, sem pedir foto, anexo, chassi/VIN ou validacao de mecanico."
            )
        partes.append(
            "A tentativa anterior foi bloqueada e nao pode ser reaproveitada literalmente.\n"
            f"Resposta bloqueada:\n{resposta_bloqueada or '-'}\n\n"
            f"Problemas detectados: {', '.join(violacoes or []) or '-'}\n"
            "Reescreva corrigindo todos os problemas, com resposta curta e objetiva."
            + instrucao_insuficiente
        )
    return _perguntas_ia_compactar_contexto("\n\n".join(partes), 32000)


def _perguntas_ia_v2_corrigir_resposta_bloqueada(
    client_id: str,
    loja: str,
    agent_input: dict,
    model_req: str,
    resposta_bloqueada: str,
    violacoes: list[str],
    *,
    bindings: LegacyResponseBindings | None = None,
) -> tuple[str, str]:
    bindings = bindings or LegacyResponseBindings()
    mensagem = _perguntas_ia_v2_prompt(
        client_id,
        agent_input,
        resposta_bloqueada=resposta_bloqueada,
        violacoes=violacoes,
    )
    payload = IAChatRequest(
        message=mensagem,
        page="Perguntas e pos venda",
        context={
            "modulo": "perguntas_pos_venda",
            "tipo": "correcao_app_perguntas_v2",
            "tipo_treinamento": "perguntas_anuncio",
            "origem_ia": "mercado_livre_perguntas_v2_correcao_validacao_app",
            "desativar_recursos_chat": True,
            "desativar_busca_web_chat": True,
            "modo_rapido_sidebar": True,
            "loja": loja,
        },
        model=model_req,
    )
    resposta, model_usado = bindings.call_model(client_id, payload, model_req)
    resposta_limpa = bindings.clean_response(resposta)
    if not resposta_limpa:
        return "", model_usado
    return bindings.final_response(resposta_limpa, loja), model_usado

def _perguntas_ia_execucao_configurar(client_id: str, agent_input: dict, started: float) -> dict:
    loja = str((agent_input or {}).get("store") or (agent_input or {}).get("loja") or "").strip()
    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja no input da nova IA.")
    if not _perguntas_ia_categoria_classificada(agent_input):
        raise PerguntasIARespostaIndisponivel(
            "Classificacao estruturada da IA sem categoria canonica; use o rascunho neutro disponivel."
        )
    settings = GeminiQuestionsSettings.from_env()
    post_sale = _perguntas_ia_fluxo_pos_venda(agent_input)
    settings.max_sentences = 3
    settings.max_chars = int(ML_POS_VENDA_LIMITE_SEGURO if post_sale else ML_RESPOSTA_PERGUNTA_MAX_CHARS)
    exige_aprovacao = _pos_venda_ia_v2_exigir_aprovacao() if post_sale else _perguntas_ia_v2_exigir_aprovacao()
    settings.auto_publish_enabled = bool(settings.auto_publish_enabled and not exige_aprovacao)
    configured_model = (
        resolve_runtime_adapter("models", "post_sale_model", _ia_modelo_pos_venda_configurado)()
        if post_sale else resolve_runtime_adapter("models", "public_model", _ia_modelo_perguntas_configurado)()
    )
    reasoning = (
        resolve_runtime_adapter("models", "post_sale_reasoning", _ia_raciocinio_pos_venda_configurado)()
        if post_sale else resolve_runtime_adapter("models", "public_reasoning", _ia_raciocinio_perguntas_configurado)()
    )
    selection = _perguntas_codex_provider_selection(
        configured_model or settings.model, (agent_input or {}).get("_codex_operational_failure_count"),
    )
    model_req = str(selection.get("model") or "codex:gpt-5.5")
    settings.model = model_req
    diagnostics = [{
        "function": ML_PERGUNTAS_IA_V2_MODO,
        "result": {
            "found": True,
            "message": "Fluxo Codex usa evidencias estruturadas e validacao antes de qualquer envio.",
            "read_only": True,
            "response_provider_policy": selection.get("policy"),
            "effective_model": model_req,
            "codex_model": selection.get("codex_model"),
            "configured_fallback": selection.get("configured_fallback"),
            "fallback_used": bool(selection.get("fallback_used")),
            "operational_failure_count": selection.get("operational_failure_count"),
            "gemini_model": model_req,
            "vertex_gemini": _modelo_eh_vertex_ai(model_req),
            "codex": _modelo_eh_codex(model_req),
            "reasoning_effort": reasoning,
            "auto_publish_enabled": settings.auto_publish_enabled,
            "fluxo_pos_venda": post_sale,
        },
    }]
    return {
        "client_id": client_id, "agent_input": agent_input, "started": started, "loja": loja,
        "settings": settings, "post_sale": post_sale, "reasoning": reasoning,
        "model_req": model_req, "diagnostics": diagnostics,
    }


def _perguntas_ia_execucao_orquestrar(contexto: dict) -> tuple:
    agent_input = contexto["agent_input"]
    settings = contexto["settings"]
    question_ctx, listing_snapshot, previous_questions, seller_rules = context_from_agent_input(
        agent_input, auto_publish_enabled=settings.auto_publish_enabled,
    )
    seller_rules.min_confidence = settings.min_confidence
    seller_rules.max_chars = settings.max_chars
    seller_rules.max_sentences = settings.max_sentences
    seller_rules.whitelisted_domains = list(settings.whitelisted_domains)
    client = _PerguntasCodexV3Client(
        contexto["client_id"], contexto["loja"], contexto["model_req"], agent_input,
        reasoning_effort=contexto["reasoning"],
    )
    orchestrator = QuestionAnswerOrchestrator(settings=settings, gemini_client=client)
    started = time.perf_counter()
    resultado = orchestrator.process(
        question=question_ctx, listing=listing_snapshot, previous_questions=previous_questions, rules=seller_rules,
    )
    resposta = resolve_runtime_adapter("state", "clean_response", _perguntas_ia_limpar_resposta)(resultado.answer)
    if not resposta:
        raise PerguntasIARespostaIndisponivel("Nova IA de perguntas nao gerou resposta.")
    resposta = resolve_runtime_adapter("state", "final_response", _perguntas_ia_resposta_final_loja)(resposta, contexto["loja"])
    return resultado, resposta, client.model_usado or contexto["model_req"], client, started


def _perguntas_ia_evidencia_publica(agent_input: dict, loja: str, client) -> dict:
    listing = agent_input.get("item") if isinstance(agent_input.get("item"), dict) else {}
    records = [
        {
            "field": "pergunta", "value": (agent_input.get("question") or {}), "store": loja,
            "source": "mercado_livre_question", "authority": "confirmed",
        },
        {
            "field": "anuncio", "value": listing, "store": loja,
            "source": "mercado_livre_listing", "authority": "confirmed",
        },
        *_perguntas_codex_public_listing_evidence(listing, loja),
        *client.evidence_records,
    ]
    records = [record for record in records if record.get("value") not in (None, "", [], {})]
    return normalize_evidence_envelope({
        "schema_version": EVIDENCE_ENVELOPE_V2,
        "status": "partial" if records else "missing",
        "records": records,
        "sources": [str(record.get("source") or "") for record in records],
        "gaps": ["intent_coverage_pending"] if records else ["evidence_missing"],
        "confidence": "medium" if records else "unknown",
        "evidence_sufficient": False,
        "coverage_complete": False,
        "scope": {
            "task_type": "public_question", "store": loja,
            "item_id": str((agent_input.get("item") or {}).get("id") or ""),
            "buyer_id": str((agent_input.get("question") or {}).get("buyer_id") or ""),
        },
    }).to_dict()


def _perguntas_ia_atualizar_diagnostico(
    contexto: dict,
    resultado,
    resposta: str,
    model_usado: str,
    client,
    perf_orq_t0: float,
) -> None:
    analysis = client.compatibility_analysis
    contexto["diagnostics"][0]["result"].update({
        "category": resultado.category.value,
        "route": resultado.route.value,
        "decision": resultado.decision.value,
        "needs_human_review": resultado.needs_human,
        "confidence": resultado.confidence,
        "source": resultado.source,
        "reason": resultado.reason,
        "validation_ok": resultado.validation.ok,
        "validation_issues": list(resultado.validation.issues),
        "prompt_chars": len(resultado.prompt or ""),
        "audit": resultado.audit,
        "context_collection_pipeline": list(client.context_pipeline),
        "compatibility_analysis": copy.deepcopy(analysis),
        "response_policy_version": _PERGUNTAS_IA_RESPONSE_POLICY_VERSION,
        "seller_render_policy": "seller-voice-v1",
        "decision_origin": (
            "canonical_coverage" if str(analysis.get("_coverage_contract_version") or "") == COMPATIBILITY_COVERAGE_VERSION
            else "technical_analysis"
        ),
        "coverage_rule_used": str(
            (analysis.get("_coverage_rule") if isinstance(analysis.get("_coverage_rule"), dict) else {}).get("rule_id") or ""
        )[:40],
        "research_skipped": str(analysis.get("research_skipped") or "")[:80],
        "codex_thread_id": client.codex_thread_id,
        "orchestrator_profile": str(contexto["agent_input"].get("orchestrator_profile") or ""),
        "subquestions": list(contexto["agent_input"].get("subquestions") or []),
        "evidence_envelope": _perguntas_ia_evidencia_publica(contexto["agent_input"], contexto["loja"], client),
    })
    _ia_agent_perguntas_log_perf(
        contexto["client_id"], contexto["loja"], contexto["agent_input"], "v3_orquestrador_codex",
        time.perf_counter() - perf_orq_t0, tentativa=1, modelo=model_usado,
        status="revisao" if resultado.needs_human else "ok", prompt_chars=len(resultado.prompt or ""),
        resposta_chars=len(resposta or ""), categoria=resultado.category.value,
        rota=resultado.route.value, decisao=resultado.decision.value,
    )


def _perguntas_ia_violacoes_execucao(contexto: dict, resposta: str, client) -> tuple[list[str], bool]:
    insuficiente = _ia_agent_perguntas_exige_rascunho_insuficiente_seguro(
        contexto["agent_input"], client.compatibility_analysis, post_sale=contexto["post_sale"],
    )
    violacoes = _ia_agent_perguntas_violacoes_resposta(contexto["agent_input"], resposta)
    if insuficiente and not _ia_agent_perguntas_rascunho_insuficiente_seguro(resposta, client.compatibility_analysis):
        violacoes.append(_PERGUNTAS_IA_SAFE_INSUFFICIENT_VIOLATION)
    return violacoes, insuficiente


def _perguntas_ia_tentar_reparo(
    contexto: dict,
    resultado,
    client,
    resposta: str,
    model_usado: str,
    violacoes: list[str],
    insuficiente: bool,
) -> tuple[str, str, list[str]]:
    pendentes = list(violacoes)
    if resultado.source == "gemini":
        started = time.perf_counter()
        try:
            corrigida, model_corrigido = _perguntas_ia_v2_corrigir_resposta_bloqueada(
                contexto["client_id"], contexto["loja"], contexto["agent_input"], contexto["model_req"], resposta, violacoes,
            )
        except Exception as exc:
            corrigida, model_corrigido = "", model_usado
            _ia_agent_perguntas_log_perf(
                contexto["client_id"], contexto["loja"], contexto["agent_input"], "v2_correcao_validacao_app",
                time.perf_counter() - started, tentativa=2, modelo=contexto["model_req"], status="erro", erro=type(exc).__name__,
            )
        else:
            corrigidas = _ia_agent_perguntas_violacoes_resposta(contexto["agent_input"], corrigida)
            if insuficiente and not _ia_agent_perguntas_rascunho_insuficiente_seguro(corrigida, client.compatibility_analysis):
                corrigidas.append(_PERGUNTAS_IA_SAFE_INSUFFICIENT_VIOLATION)
            if corrigida and not resolve_runtime_adapter("state", "invalid_fallback", _perguntas_ia_resposta_fallback_invalida)(corrigida) and not corrigidas:
                resposta, model_usado, pendentes = corrigida, model_corrigido or model_usado, []
                contexto["diagnostics"][0]["result"].update({"app_validation_repaired": True, "app_validation_repair_issues": []})
            else:
                pendentes = corrigidas or pendentes
                contexto["diagnostics"][0]["result"].update({
                    "app_validation_repaired": False, "app_validation_repair_issues": pendentes[:8],
                })
            _ia_agent_perguntas_log_perf(
                contexto["client_id"], contexto["loja"], contexto["agent_input"], "v2_correcao_validacao_app",
                time.perf_counter() - started, tentativa=2, modelo=model_corrigido or contexto["model_req"],
                status="ok" if not pendentes else "violacao",
                violacoes="|".join(pendentes[:5]) if pendentes else "",
            )
    return resposta, model_usado, pendentes


def _perguntas_ia_validar_resposta(contexto: dict, resultado, client, resposta: str, model_usado: str) -> tuple[str, str]:
    violacoes, insuficiente = _perguntas_ia_violacoes_execucao(contexto, resposta, client)
    if not violacoes:
        return resposta, model_usado
    contexto["diagnostics"][0]["result"]["app_validation_issues"] = violacoes[:8]
    resposta, model_usado, pendentes = _perguntas_ia_tentar_reparo(
        contexto, resultado, client, resposta, model_usado, violacoes, insuficiente,
    )
    if pendentes and all(str(issue or "").startswith(_PERGUNTAS_IA_SELLER_STYLE_PREFIX) for issue in pendentes):
        compactada = _perguntas_ia_compactar_estilo_vendedor(resposta, contexto["loja"])
        compactadas = _ia_agent_perguntas_violacoes_resposta(contexto["agent_input"], compactada)
        if compactada and not resolve_runtime_adapter("state", "invalid_fallback", _perguntas_ia_resposta_fallback_invalida)(compactada) and not compactadas:
            resposta, pendentes = compactada, []
            contexto["diagnostics"][0]["result"].update({
                "seller_style_fallback_used": True, "seller_style_violation_codes": violacoes[:8],
            })
    if pendentes:
        mensagem = "Nova IA de perguntas gerou resposta fora das orientacoes"
        if resultado.source == "gemini":
            mensagem += " do app"
        raise PerguntasIARespostaIndisponivel(mensagem + ": " + ", ".join(pendentes[:6]))
    return resposta, model_usado


def _perguntas_ia_v2_gerar_resposta(client_id: str, agent_input: dict) -> tuple[str, str, list[dict]]:
    contexto = _perguntas_ia_execucao_configurar(client_id, agent_input, time.perf_counter())
    try:
        resultado, resposta, model_usado, client, perf_orq_t0 = _perguntas_ia_execucao_orquestrar(contexto)
        _perguntas_ia_atualizar_diagnostico(contexto, resultado, resposta, model_usado, client, perf_orq_t0)
        if resolve_runtime_adapter("state", "invalid_fallback", _perguntas_ia_resposta_fallback_invalida)(resposta):
            raise PerguntasIARespostaIndisponivel("Resposta de fallback da nova IA de perguntas bloqueada.")
        resposta, model_usado = _perguntas_ia_validar_resposta(contexto, resultado, client, resposta, model_usado)
        _ia_agent_perguntas_log_perf(
            client_id, contexto["loja"], agent_input, "total", time.perf_counter() - contexto["started"],
            status="ok", modelo=model_usado, modo=ML_PERGUNTAS_IA_V2_MODO,
        )
        return resposta, model_usado, contexto["diagnostics"]
    except Exception as exc:
        _ia_agent_perguntas_log_perf(
            client_id, contexto["loja"], agent_input, "total", time.perf_counter() - contexto["started"],
            status="erro", erro=type(exc).__name__, modo=ML_PERGUNTAS_IA_V2_MODO,
        )
        raise
