"""Extracted legacy AI implementation with static dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from .runtime import (
    AIAnswer,
    AIResponseParser,
    Any,
    IAChatRequest,
    Optional,
    _ia_raciocinio_perguntas_configurado,
    _ia_raciocinio_pos_venda_configurado,
    _ia_tool_get_bling_product,
    marketplace_listing_query,
    _ia_tool_get_product_data,
    _normalizar_codex_reasoning_effort,
    _perguntas_ia_assinatura_loja,
    _perguntas_ia_compactar_contexto,
    _perguntas_ia_fluxo_pos_venda,
    _perguntas_ia_limpar_resposta,
    _perguntas_ia_memoria_bloco_prompt,
    copy,
    json,
    logger,
    resolve_runtime_adapter,
)
from .compatibility import (
    _perguntas_ia_v2_compatibilidade_normalizar,
)
from .context import (
    _perguntas_ia_context_hub_deve_buscar,
    _perguntas_ia_context_hub_tool,
    _perguntas_ia_v2_coverage_analysis,
    _perguntas_ia_v2_coverage_match,
)
from .inputs import (
    _perguntas_codex_compact_json,
    _perguntas_ia_allowed_tools_classificadas,
    _perguntas_ia_compatibilidade_classificada,
    _perguntas_ia_legacy_guidance_fallback,
    _perguntas_ia_legacy_sku_memory_reader_enabled,
)
from .evidence import (
    _perguntas_ia_v2_compatibilidade_padrao,
    _perguntas_ia_v2_fontes_web,
    _perguntas_ia_v2_grounding_coletar,
    _perguntas_ia_v2_json_obj,
    _perguntas_ia_v2_query_pesquisa,
    _perguntas_ia_v2_resposta_precisa_web,
)
from .queries import (
    _ia_agent_perguntas_texto_busca,
)
from .sources import (
    _ia_agent_perguntas_product_identity_web_tool,
    _ia_agent_perguntas_tool_error,
    _ia_agent_perguntas_web_tool,
)
from .tools import (
    _ia_agent_perguntas_chamar_modelo,
)
from .validation import (
    ML_PERGUNTAS_IA_V2_MODO,
    ML_POS_VENDA_IA_V2_MODO,
)
from .client_workflows import (
    CompatibilityBindings,
    GeneralBindings,
    run_compatibility,
    run_general,
)


@dataclass(frozen=True, slots=True)
class LegacyVertexBindings:
    call_model: Any = _ia_agent_perguntas_chamar_modelo
    product_identity_web: Any = _ia_agent_perguntas_product_identity_web_tool
    get_product_data: Any = _ia_tool_get_product_data
    get_listing: Any = marketplace_listing_query
    get_bling_product: Any = _ia_tool_get_bling_product
    context_hub: Any = _perguntas_ia_context_hub_tool
    memory_prompt: Any = _perguntas_ia_memoria_bloco_prompt
    question_web: Any = _ia_agent_perguntas_web_tool

class _PerguntasVertexGeminiV2Client:
    def __init__(
        self,
        client_id: str,
        loja: str,
        model_req: str,
        agent_input: Optional[dict[str, Any]] = None,
        reasoning_effort: str | None = None,
    ):
        self.client_id = client_id
        self.loja = loja
        self.model_req = model_req
        self.model_usado = model_req
        self.parser = AIResponseParser()
        self.agent_input = copy.deepcopy(agent_input) if isinstance(agent_input, dict) else {}
        fluxo_pos_venda = _perguntas_ia_fluxo_pos_venda(self.agent_input)
        effort_configurado = (
            _ia_raciocinio_pos_venda_configurado()
            if fluxo_pos_venda
            else _ia_raciocinio_perguntas_configurado()
        )
        self.reasoning_effort = _normalizar_codex_reasoning_effort(reasoning_effort or effort_configurado)
        self.codex_thread_id = str(self.agent_input.get("_codex_thread_id") or "").strip()
        self.context_pipeline: list[dict[str, Any]] = []
        self.evidence_records: list[dict[str, Any]] = []
        self.compatibility_analysis: dict[str, Any] = _perguntas_ia_v2_compatibilidade_padrao(self.agent_input)
        self._compatibility_queries: list[dict[str, Any]] = []
        self._compatibility_sources: list[str] = []
        self._compatibility_grounding: dict[str, Any] = {}

    def _call_model(
        self,
        prompt: str,
        metadata: dict[str, Any],
        *,
        stage: str,
        tool_results: Optional[list[dict[str, Any]]] = None,
    ) -> Any:
        fluxo_pos_venda = str(metadata.get("category") or "").strip() == "post_sale"
        subquestions = self.agent_input.get("subquestions") if isinstance(self.agent_input.get("subquestions"), list) else []
        if subquestions:
            prompt = (
                prompt
                + "\n\nSUBPERGUNTAS OBRIGATORIAS IDENTIFICADAS PELO ORQUESTRADOR:\n"
                + _perguntas_codex_compact_json(subquestions[:8], 5000)
                + "\nResponda a cada assunto identificado no mesmo rascunho, sem ignorar compatibilidade, entrega, estoque ou outra parte. "
                "Quando uma parte nao puder ser comprovada, responda apenas o que esta confirmado e solicite somente o dado indispensavel."
            )
        research_attempt = max(1, int(self.agent_input.get("research_attempt") or 1))
        research_history = self.agent_input.get("research_history") if isinstance(self.agent_input.get("research_history"), list) else []
        if research_attempt > 1 or self.agent_input.get("force_external_research"):
            prompt += (
                f"\n\nNOVA TENTATIVA DE PESQUISA TECNICA: {research_attempt}. "
                "Use os achados confirmados das tentativas anteriores, mas nao repita apenas as mesmas consultas ou as mesmas fontes inconclusivas. "
                "Procure preencher especificamente os campos ainda ausentes ou conflitantes com manual, fabricante, catalogo OEM, ficha tecnica ou duas fontes tecnicas independentes concordantes.\n"
                + str(self.agent_input.get("research_directive") or "")[:1200]
                + "\nHISTORICO_COMPACTO_DAS_TENTATIVAS:\n"
                + _perguntas_codex_compact_json(research_history[-6:], 7000)
            )
        payload = IAChatRequest(
            message=prompt,
            page="Perguntas e pos venda",
            context={
                "modulo": "perguntas_pos_venda",
                "tipo": ML_POS_VENDA_IA_V2_MODO if fluxo_pos_venda else f"{ML_PERGUNTAS_IA_V2_MODO}_{stage}",
                "tipo_treinamento": "pos_venda" if fluxo_pos_venda else "perguntas_anuncio",
                "origem_ia": "mercado_livre_perguntas_pos_venda_v2" if fluxo_pos_venda else "mercado_livre_perguntas_v2_contexto_sequencial",
                "desativar_recursos_chat": True,
                "desativar_busca_web_chat": True,
                "context_collection_stage": stage,
                "loja": self.loja,
                "metadata": metadata,
                "_codex_thread_id": self.codex_thread_id,
                "_codex_persist_thread": bool(self.agent_input.get("_codex_job_id")),
                "_codex_job_id": str(self.agent_input.get("_codex_job_id") or ""),
                "_codex_active_turn_key": str(
                    self.agent_input.get("_codex_active_turn_key")
                    or self.agent_input.get("_codex_job_id")
                    or ""
                ),
                "_codex_conversation_key": str(
                    self.agent_input.get("_codex_conversation_key")
                    or self.agent_input.get("_codex_job_id")
                    or ""
                ),
                "_codex_on_thread_ready": self.agent_input.get("_codex_on_thread_ready"),
                "research_attempt": research_attempt,
                "_codex_reasoning_effort": self.reasoning_effort,
            },
            model=self.model_req,
            tool_results=list(tool_results or []),
        )
        resposta, model_usado = _ia_agent_perguntas_chamar_modelo(self.client_id, payload, self.model_req)
        if isinstance(payload.context, dict) and payload.context.get("_codex_thread_id_result"):
            self.codex_thread_id = str(payload.context.get("_codex_thread_id_result") or "").strip()
        self.model_usado = model_usado
        parsed = self.parser.parse(resposta)
        if str(metadata.get("category") or "").strip().lower() == "compatibility":
            payload_obj = _perguntas_ia_v2_json_obj(getattr(parsed, "raw", resposta))
            self.compatibility_analysis = _perguntas_ia_v2_compatibilidade_normalizar(
                payload_obj.get("compatibility_analysis"),
                base=self.compatibility_analysis,
                queries=self._compatibility_queries,
                sources=self._compatibility_sources,
                grounding=self._compatibility_grounding,
            )
            if self.compatibility_analysis.get("decision") == "insufficient":
                parsed.requires_human_review = True
                parsed.confidence = min(float(getattr(parsed, "confidence", 0.0) or 0.0), 0.49)
        if getattr(parsed, "answer", ""):
            return parsed
        resposta_limpa = resolve_runtime_adapter("state", "clean_response", _perguntas_ia_limpar_resposta)(resposta)
        if resposta_limpa:
            return AIAnswer(
                answer=resposta_limpa,
                confidence=0.70,
                requires_human_review=True,
                reason="plain_text_requires_evidence_review",
                raw=resposta,
            )
        return parsed

    def _registrar_etapa_tool(self, step: int, name: str, tool_result: Optional[dict[str, Any]]) -> None:
        result = tool_result.get("result") if isinstance(tool_result, dict) and isinstance(tool_result.get("result"), dict) else {}
        matches = result.get("matches") if isinstance(result.get("matches"), list) else []
        reference_results = result.get("results") if isinstance(result.get("results"), list) else []
        contexto = str(result.get("context") or "").strip()
        erro = str(result.get("error") or "").strip()
        found = bool(result.get("found") or matches or reference_results or contexto)
        for reference in reference_results[:8]:
            if not isinstance(reference, dict):
                continue
            self.evidence_records.append({
                "field": "context_hub",
                "value": {
                    "doc_id": reference.get("doc_id"),
                    "chunk_id": reference.get("chunk_id"),
                    "snippet": reference.get("snippet"),
                    "version": reference.get("source_version") or reference.get("version"),
                    "hash": reference.get("source_hash") or reference.get("hash"),
                },
                "store": self.loja,
                "source": reference.get("reference") or reference.get("doc_id") or "context_hub",
                "authority": reference.get("truth_class") or "legacy_unverified",
            })
        self.context_pipeline.append({
            "step": step,
            "name": name,
            "status": "error" if erro else ("completed" if found else "unavailable"),
            "found": found,
            "matches": len(matches),
            "reference_count": len(reference_results),
            "source_count": len(_perguntas_ia_v2_fontes_web(tool_result)) or len(reference_results),
            "error": erro[:180],
            "empty_result_is_not_incompatibility": not found,
        })

    def _tool_segura(self, function_name: str, callback: Callable[[], Optional[dict]]) -> dict:
        try:
            resultado = callback()
            if isinstance(resultado, dict):
                return resultado
            return {
                "function": function_name,
                "arguments": {},
                "result": {"found": False, "unavailable": True, "read_only": True},
            }
        except Exception as exc:
            logger.warning("[PERGUNTAS V2] Falha na etapa sequencial %s: %s", function_name, exc)
            return _ia_agent_perguntas_tool_error(function_name, exc)

    def _seller_fallback(self) -> AIAnswer:
        analysis = self.compatibility_analysis if isinstance(self.compatibility_analysis, dict) else {}
        target = str(analysis.get("target_item") or analysis.get("target_vehicle") or "esse modelo").strip()
        decision = str(analysis.get("decision") or "insufficient").strip().lower()
        scope = str(((analysis.get("_coverage_rule") or {}).get("scope") if isinstance(analysis.get("_coverage_rule"), dict) else "") or "").strip()
        scope_labels = {
            "physical_fit": "o encaixe físico",
            "vehicle_application": "a aplicação informada",
            "dimensional": "as medidas informadas",
            "electrical": "a conexão elétrica informada",
            "protocol": "a conexão informada",
            "function": "a função informada",
        }
        label = scope_labels.get(scope, "essa aplicação")
        if decision == "yes":
            answer = f"Sim, dá certo! {target} está dentro da compatibilidade indicada para {label}."
        elif decision == "no":
            answer = f"Não, este produto não é compatível com {target} para {label}."
        elif decision == "conditional":
            condition = str(analysis.get("condition") or "as condições informadas do produto sejam atendidas").strip()
            answer = f"Dá certo com {target}, desde que {condition}."
        else:
            answer = f"No momento, não temos confirmação segura para {target}. As informações já confirmadas do produto continuam válidas."
        return AIAnswer(
            answer=answer,
            confidence=float(analysis.get("confidence") or (0.45 if decision == "insufficient" else 0.85)),
            requires_human_review=False,
            reason=str(analysis.get("reason") or "available_information_fallback"),
            raw=None,
        )

    def _render_seller_answer(self, metadata: dict[str, Any]) -> AIAnswer:
        analysis = self.compatibility_analysis if isinstance(self.compatibility_analysis, dict) else {}
        question = self.agent_input.get("question") if isinstance(self.agent_input.get("question"), dict) else {}
        comparison = analysis.get("comparison_attributes") if isinstance(analysis.get("comparison_attributes"), list) else []
        coverage_rule = analysis.get("_coverage_rule") if isinstance(analysis.get("_coverage_rule"), dict) else {}
        approved_facts = {
            "decision": str(analysis.get("decision") or "insufficient"),
            "target": str(analysis.get("target_item") or analysis.get("target_vehicle") or ""),
            "product_fact": str(analysis.get("product_interface") or "")[:1200],
            "target_fact": str(analysis.get("target_interface") or "")[:800],
            "comparison": comparison[:8],
            "condition": str(analysis.get("condition") or "")[:1200],
            "related_conditions": list(coverage_rule.get("related_conditions") or [])[:8],
        }
        prompt = (
            "Voce e o redator final de uma loja no Mercado Livre. Escreva somente a mensagem publica ao comprador, "
            "como um vendedor cordial, simples e objetivo. Use exclusivamente os fatos aprovados abaixo; nao acrescente "
            "medida, modelo, compatibilidade, funcao ou promessa. A informacao principal deve estar na primeira frase. "
            "Use no maximo tres frases de conteudo, sem contar a assinatura, sem markdown, tabela ou emoji. "
            "Nao mencione evidencia, analise, validacao, schema, decisao, ferramenta, sistema, interface alvo ou revisao humana. "
            "Termos tecnicos so podem aparecer quando forem um fato aprovado e responderem diretamente a pergunta. "
            f"Finalize exatamente com: {resolve_runtime_adapter('state', 'store_signature', _perguntas_ia_assinatura_loja)(self.loja)}\n\n"
            "Responda exclusivamente em JSON com answer, confidence, category, requires_human_review e reason. "
            "requires_human_review deve ser false.\n\n"
            f"PERGUNTA: {str(question.get('text') or '')[:2000]}\n"
            "FATOS_APROVADOS: "
            + json.dumps(approved_facts, ensure_ascii=False, default=str)
        )
        try:
            rendered = self._call_model(
                prompt,
                {**metadata, "category": "seller_render"},
                stage="seller_render",
                tool_results=[],
            )
        except Exception:
            rendered = self._seller_fallback()
        if not getattr(rendered, "answer", ""):
            rendered = self._seller_fallback()
        rendered.requires_human_review = False
        rendered.reason = str(analysis.get("reason") or getattr(rendered, "reason", "") or "seller_rendered")
        rendered.raw = None
        return rendered

    def _render_public_answer(self, candidate: Any, metadata: dict[str, Any]) -> AIAnswer:
        question = self.agent_input.get("question") if isinstance(self.agent_input.get("question"), dict) else {}
        approved_draft = str(getattr(candidate, "answer", "") or "").strip()
        if not approved_draft:
            return candidate
        prompt = (
            "Voce e o redator final de uma loja no Mercado Livre. Reescreva o rascunho aprovado abaixo como uma "
            "resposta curta, cordial e natural de vendedor. Preserve exatamente todos os fatos, ressalvas e limites; "
            "nao acrescente nem transforme informacao. Coloque a resposta principal na primeira frase e use no maximo "
            "tres frases de conteudo, sem contar a assinatura. Use termos tecnicos somente quando indispensaveis. "
            "Nao mencione evidencia, analise, validacao, schema, decisao, ferramenta, sistema, interface alvo ou revisao humana. "
            f"Finalize exatamente com: {resolve_runtime_adapter('state', 'store_signature', _perguntas_ia_assinatura_loja)(self.loja)}\n\n"
            "Responda exclusivamente em JSON com answer, confidence, category, requires_human_review e reason. "
            "requires_human_review deve ser false.\n\n"
            f"PERGUNTA: {str(question.get('text') or '')[:2000]}\n"
            f"RASCUNHO_APROVADO: {approved_draft[:2000]}"
        )
        try:
            rendered = self._call_model(
                prompt,
                {**metadata, "category": str(metadata.get("category") or "product_feature")},
                stage="seller_render",
                tool_results=[],
            )
        except Exception:
            rendered = candidate
        if not getattr(rendered, "answer", ""):
            rendered = candidate
        rendered.requires_human_review = False
        rendered.confidence = float(getattr(candidate, "confidence", 0.0) or getattr(rendered, "confidence", 0.0) or 0.0)
        rendered.reason = str(getattr(candidate, "reason", "") or getattr(rendered, "reason", "") or "seller_rendered")
        rendered.raw = None
        self.context_pipeline.append({
            "step": 8,
            "name": "seller_response_render",
            "status": "completed",
            "render_policy": "seller-voice-v1",
        })
        return rendered

    def _generate_compatibility(self, prompt: str, metadata: dict[str, Any]) -> AIAnswer:
        bindings = CompatibilityBindings(
            listing_tool=resolve_runtime_adapter("tools", "mercado_livre_listing", marketplace_listing_query),
            product_tool=resolve_runtime_adapter("tools", "product_data", _ia_tool_get_product_data),
            bling_tool=resolve_runtime_adapter("tools", "bling_product", _ia_tool_get_bling_product),
            context_hub_tool=_perguntas_ia_context_hub_tool,
            memory_prompt=resolve_runtime_adapter("state", "memory_prompt", _perguntas_ia_memoria_bloco_prompt),
            legacy_reader_enabled=_perguntas_ia_legacy_sku_memory_reader_enabled,
            legacy_fallback=_perguntas_ia_legacy_guidance_fallback,
            product_identity_tool=_ia_agent_perguntas_product_identity_web_tool,
            web_tool=_ia_agent_perguntas_web_tool,
        )
        return run_compatibility(self, prompt, metadata, bindings)

    def generate(self, prompt: str, metadata: Optional[dict[str, Any]] = None) -> AIAnswer:
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        if str(metadata_dict.get("category") or "").strip().lower() == "compatibility":
            return self._generate_compatibility(prompt, metadata_dict)
        bindings = GeneralBindings(
            context_hub_tool=_perguntas_ia_context_hub_tool,
            web_tool=_ia_agent_perguntas_web_tool,
            response_needs_web=_perguntas_ia_v2_resposta_precisa_web,
        )
        return run_general(self, prompt, metadata_dict, bindings)


class _PerguntasCodexV3Client(_PerguntasVertexGeminiV2Client):
    """Codex-native functional role; legacy class name remains a compatibility reader."""
