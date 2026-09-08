import json
import hashlib
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import unittest
from unittest.mock import patch
from pathlib import Path

from backend.modules.perguntas_pos_venda.ai import clients as agent_clients
from backend.modules.perguntas_pos_venda.ai import client_workflows as agent_workflows
from backend.modules.perguntas_pos_venda.ai import compatibility as agent_compatibility
from backend.modules.perguntas_pos_venda.ai import evidence as agent_evidence
from backend.modules.perguntas_pos_venda.ai import execution as agent_execution
from backend.modules.perguntas_pos_venda.ai import inputs as agent_inputs
from backend.modules.perguntas_pos_venda.ai import queries as agent_queries
from backend.modules.perguntas_pos_venda.ai import sources as agent_sources
from backend.modules.perguntas_pos_venda.ai import validation as agent_validation
from ml_questions_gemini import AIAnswer


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend_api.py"
BACKEND_DIR = ROOT / "backend"
CONFIG_HTMLS = [ROOT / "configuracoes.html", ROOT / "static" / "configuracoes.html"]
POS_VENDA_HTMLS = [ROOT / "perguntas_pos_venda.html", ROOT / "static" / "perguntas_pos_venda.html"]


def backend_text() -> str:
    parts = [BACKEND.read_text(encoding="utf-8")]
    if BACKEND_DIR.exists():
        for path in sorted(BACKEND_DIR.rglob("*.py")):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", source, re.M | re.S)
    if not match:
        raise AssertionError(f"Funcao {name} nao encontrada")
    return match.group(0)


def extract_assignment(source: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\s*=\s*\{{.*?^\}}\s*$", source, re.M | re.S)
    if not match:
        raise AssertionError(f"Atribuicao {name} nao encontrada")
    return match.group(0)


def ai_classification(
    question: str,
    *,
    category: str = "product_feature",
    intent: str | None = None,
    use_web: bool = False,
    target_item: str = "",
    target_type: str = "",
    compatibility_profile: str = "",
    technical_focus: str = "",
    missing_fields: tuple[str, ...] = (),
    decisive_fields: tuple[str, ...] = (),
    required_evidence: str = "anuncio, cadastro ou fonte tecnica coletada",
) -> dict:
    post_sale = category == "post_sale"
    if intent is None:
        intent = {
            "compatibility": "compatibilidade",
            "other_product": "outra_peca",
            "price": "preco_estoque",
            "stock": "preco_estoque",
            "post_sale": "pos_venda_defeito",
            "unknown": "nao_entendi",
        }.get(category, "duvida_produto")
    subquestion_intent = category if category in {
        "compatibility", "shipping", "stock", "price", "invoice",
        "warranty_originality", "product_feature", "other_product", "post_sale",
    } else "general"
    compatibility = category == "compatibility"
    return {
        "intencao": intent,
        "categoria": category,
        "categorias": [category],
        "fluxo": "pos_venda" if post_sale else "perguntas_anuncio",
        "confianca": 0.97,
        "flags": {
            "usar_busca_web": bool(use_web and not post_sale),
            "usar_mercado_livre_anuncio": not post_sale,
            "usar_bling": not post_sale,
        },
        "subperguntas": [{
            "intent": subquestion_intent,
            "question": question,
            "required_evidence": required_evidence,
        }],
        "compatibilidade": {
            "aplicavel": compatibility,
            "target_item": target_item if compatibility else "",
            "target_type": target_type if compatibility else "",
            "compatibility_profile": compatibility_profile if compatibility else "",
            "technical_focus": technical_focus,
            "missing_fields": list(missing_fields),
            "decisive_fields": list(decisive_fields),
        },
    }


def _pipeline_step(client, name: str) -> dict:
    return next(step for step in client.context_pipeline if step.get("name") == name)


def _verified_fact(field_name: str, value: str, *, scope: str = "product") -> dict:
    return {
        "field_name": field_name,
        "scope": scope,
        "value": value,
        "unit": "",
        "activation_policy": "official_exact_identity",
        "source_authorities": ["official_manufacturer"],
    }


def _v16_model_stages(model_call) -> list[str]:
    return [
        str(call.args[1].context.get("context_collection_stage") or "")
        for call in model_call.call_args_list
    ]


def _v16_model_payload(model_call, stage: str, occurrence: int = 0):
    matches = [
        call.args[1]
        for call in model_call.call_args_list
        if call.args[1].context.get("context_collection_stage") == stage
    ]
    return matches[occurrence]


def _v16_model_responder(
    public_response: str | dict,
    *,
    decision: str | None = None,
    compatibility_analysis: dict | None = None,
    fail_stage: str = "",
):
    if isinstance(public_response, dict):
        public_payload = dict(public_response)
    else:
        try:
            parsed = json.loads(public_response)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {"answer": str(public_response or "")}
        public_payload = parsed if isinstance(parsed, dict) else {"answer": str(public_response or "")}
    supplied_analysis = (
        dict(compatibility_analysis)
        if isinstance(compatibility_analysis, dict)
        else dict(public_payload.get("compatibility_analysis") or {})
    )
    technical_decision = str(decision or supplied_analysis.get("decision") or "yes")
    confidence = float(public_payload.get("confidence") or supplied_analysis.get("confidence") or 0.9)
    commercial_state = {
        "yes": "fits",
        "no": "incompatible",
        "conditional": "partial",
        "insufficient": "insufficient",
        "not_applicable": "not_applicable",
    }.get(technical_decision, "insufficient")
    analysis = {
        "target_type": "generic",
        "target_item": "",
        "target_vehicle": "",
        "compatibility_profile": "generic",
        "product_interface": "",
        "target_interface": "",
        "comparison_attributes": [],
        "decision": "insufficient" if technical_decision == "not_applicable" else technical_decision,
        "condition": "",
        "missing_fields": [],
        "evidence": {"product": [], "target": [], "target_vehicle": [], "equivalence": []},
        "queries": [],
        "sources": [],
        "confidence": confidence,
        "reason": str(public_payload.get("reason") or "v16_test_resolution"),
    }
    analysis.update(supplied_analysis)

    def respond(_client_id, request, model_req):
        stage = str(request.context.get("context_collection_stage") or "")
        if stage == fail_stage:
            raise RuntimeError(f"{stage}_unavailable")
        if stage == "technical_question_plan":
            payload = {
                "schema": "jk_ml_technical_question_plan_v1",
                "requirements": [{
                    "id": "q1",
                    "essential": True,
                    "kind": "specification",
                    "question": "Responder a pergunta técnica do comprador",
                    "subject": {"kind": "product", "name": "produto", "identifiers": []},
                    "target": {"kind": "application", "name": "alvo informado", "identifiers": []},
                    "relation": "has_property",
                    "required_fields": [],
                    "search_terms": [],
                }],
                "queries": [],
            }
        elif stage == "technical_evidence_graph":
            payload = {
                "schema": "jk_ml_evidence_graph_v2",
                "entities": [],
                "claims": [],
                "passages": [],
                "relations": [],
                "unresolved_requirement_ids": [],
            }
        elif stage in {"technical_resolution_round_1", "technical_resolution_final"}:
            is_final = stage == "technical_resolution_final"
            payload = {
                "schema": "jk_ml_technical_resolution_v1",
                "round": 2 if is_final else 1,
                "final": is_final,
                "requirements": [{
                    "id": "q1",
                    "decision": technical_decision,
                    "conclusion": str(public_payload.get("answer") or ""),
                    "condition": str(analysis.get("condition") or ""),
                    "commercial_impact": "satisfies" if technical_decision == "yes" else "unknown",
                    "facts": [],
                    "missing_fields": list(analysis.get("missing_fields") or []),
                    "confidence": confidence,
                }],
                "reference_relations": [],
                "overall_decision": technical_decision,
                "commercial_state": commercial_state,
                "confidence": confidence,
                "reason": str(public_payload.get("reason") or "v16_test_resolution"),
                "gap_queries": [],
                "contingency_answer_body": str(public_payload.get("answer") or ""),
                "compatibility_analysis": analysis,
            }
        elif stage == "factual_critic":
            payload = {
                "schema": "jk_ml_factual_review_v1",
                "verdict": "pass",
                "issues": [],
                "revision_instructions": [],
                "confidence": 1.0,
            }
        else:
            payload = public_payload
        return json.dumps(payload, ensure_ascii=False), model_req

    return respond


class MlPosVendaAIConfigTests(unittest.TestCase):
    def test_compatibility_intent_enables_required_web_research_from_ai_classification(self):
        import backend_api  # noqa: F401

        context = {
            "intencao_atendimento": ai_classification(
                "Serve na R1300GS?",
                category="compatibility",
                use_web=True,
                target_item="BMW R1300GS",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="interface base conector preparacao",
                required_evidence="interface do produto, interface da moto e equivalencia tecnica",
            )
        }
        with patch.object(agent_inputs, "_ia_treinamento_ppv_bloco_prompt", return_value=""):
            payload = agent_inputs._perguntas_ia_agent_input(
                "000002",
                "Uai Mineirinho",
                {"id": "Q1", "text": "Serve na R1300GS?"},
                {"id": "MLB1", "title": "Adaptador BMW Navigator"},
                context,
                "prompt",
            )

        self.assertTrue(payload["use_web_search"])
        self.assertTrue(payload["web_search_required"])
        self.assertIn("context_hub_search", payload["allowed_tools"])
        self.assertIn("web_search_question_context", payload["allowed_tools"])
        self.assertEqual(payload["app_guidance_truth_class"], "versioned_technical")
        self.assertEqual(payload["app_guidance_source"], "jk_ppv_response_policy_v8")
        self.assertEqual(payload["commercial_method_version"], "seller-conversion-v1")
        self.assertEqual(payload["commercial_state_policy"]["fits"]["cta"], "direct_purchase")
        self.assertEqual(payload["commercial_state_policy"]["insufficient"]["cta"], "none")
        pipeline = payload["context_collection_pipeline"]
        self.assertEqual(pipeline[2]["name"], "public_vehicle_identity")
        self.assertEqual(pipeline[5]["name"], "context_hub_sku_reference")
        self.assertIn("dados de referencia nao confiaveis", pipeline[5]["description"])
        self.assertEqual(pipeline[6]["name"], "compiled_product_research")
        self.assertEqual(pipeline[7]["name"], "question_focused_web_research")
        self.assertIn("compatibilidade, originalidade, conflito", pipeline[7]["description"])
        self.assertIn("campos ainda necessarios", pipeline[7]["description"])
        self.assertEqual(pipeline[8]["name"], "commercial_fit_evaluation")
        self.assertEqual(pipeline[9]["name"], "seller_behavior_profile_v2")
        self.assertEqual(pipeline[10]["name"], "codex_commercial_answer")

    def test_public_questions_retire_legacy_training_and_profile_from_runtime(self):
        import backend_api  # noqa: F401

        canary = "RESPOSTA-IDEAL-ANTIGA-NAO-DEVE-ENTRAR"
        context = {
            "intencao_atendimento": ai_classification("Qual o conector?")
        }
        profile = {
            "schema": "seller_behavior_profile_v2",
            "method_version": "seller-conversion-v1",
            "profile_version": 2,
            "profile_active": True,
            "profile_scope": "store",
            "layers": [{
                "scope": "store",
                "behavior_guidance": "Tom consultivo e profissional.",
                "style_examples": [{"answer": canary, "fact_authority": "none"}],
            }],
        }
        with patch.dict(os.environ, {"IA_PPV_LEGACY_GUIDANCE_FALLBACK_ENABLED": ""}), \
             patch.object(agent_inputs, "_ia_treinamento_ppv_bloco_prompt", return_value=canary):
            payload = agent_inputs._perguntas_ia_agent_input(
                "000002",
                "JK Pecas",
                {"id": "Q1", "text": "Qual o conector?"},
                {"id": "MLB1", "seller_sku": "001", "title": "Adaptador"},
                context,
                "prompt",
                profile_resolver_fn=lambda *_args, **_kwargs: profile,
            )
            prompt = agent_execution._perguntas_ia_v2_prompt("000002", payload)

        self.assertNotIn(canary, payload["app_guidance"])
        self.assertEqual(payload["seller_behavior_profile"], {
            "schema": "jk_seller_behavior_profile_v2",
            "source": "context_hub_store_sku_v18_pending",
            "legacy_json_used": False,
        })
        self.assertNotIn(canary, prompt)
        self.assertFalse(payload["legacy_guidance_available"])
        self.assertEqual(payload["legacy_guidance_hash"], "")
        self.assertFalse(payload["legacy_fallback_enabled"])
        self.assertFalse(payload["legacy_fallback_used"])

    def test_raw_legacy_fallback_is_permanently_disabled_because_profile_v2_owns_activation(self):
        import backend_api  # noqa: F401

        canary = "REGRA-LEGADA-AUDITADA"
        payload = {
            "store": "JK Pecas",
            "context": {},
            "intent": ai_classification("Qual o conector?"),
        }
        empty_hub = {"result": {"found": False, "count": 0, "authoritative_count": 0}}
        nonempty_legacy_hub = {
            "result": {
                "found": True,
                "count": 1,
                "authoritative_count": 0,
                "results": [{"truth_class": "legacy_unverified"}],
            }
        }
        unsafe_hub = {"result": {"found": False, "unavailable": True, "reason_code": "security_blocked"}}
        with patch.dict(os.environ, {"IA_PPV_LEGACY_GUIDANCE_FALLBACK_ENABLED": "true"}), \
             patch.object(agent_inputs, "_ia_treinamento_ppv_bloco_prompt", return_value=canary):
            for hub in (empty_hub, unsafe_hub, nonempty_legacy_hub):
                self.assertEqual(
                    agent_inputs._perguntas_ia_legacy_guidance_fallback("000002", payload, hub),
                    "",
                )

    def test_technical_product_question_defers_web_decision_until_sku_context(self):
        import backend_api  # noqa: F401

        context = {
            "intencao_atendimento": ai_classification(
                "Essa carcaca da valvula termostatica e de engate rapido ou para abracadeira?",
                use_web=True,
                technical_focus="engate rapido abracadeira",
                required_evidence="codigo da peca e especificacao das conexoes",
            )
        }
        with patch.object(agent_inputs, "_ia_treinamento_ppv_bloco_prompt", return_value=""):
            payload = agent_inputs._perguntas_ia_agent_input(
                "000002",
                "Uai Mineirinho",
                {
                    "id": "13621747832",
                    "text": "Essa carcaca da valvula termostatica e de engate rapido ou para abracadeira?",
                },
                {
                    "id": "MLB4129425225",
                    "title": "Carcaca Valvula Termostatica THP 1.6",
                },
                context,
                "prompt",
            )

        self.assertFalse(payload["use_web_search"])
        self.assertFalse(payload["web_search_required"])
        self.assertNotIn("web_search_product_identity", payload["allowed_tools"])
        self.assertNotIn("web_search_question_context", payload["allowed_tools"])
        self.assertIn("context_hub_search", payload["allowed_tools"])

    def test_backend_has_dedicated_pos_venda_config(self):
        source = backend_text()
        self.assertIn('"ia_modelo_pos_venda": "codex:gpt-5.5"', source)
        self.assertIn('"ia_modo_pos_venda": "modelo"', source)
        self.assertIn("def _ia_modelo_pos_venda_configurado()", source)
        self.assertIn("def _ia_modo_pos_venda_configurado()", source)
        self.assertIn('"pos_venda": _ia_modelo_pos_venda_configurado()', source)

    def test_pos_venda_generator_uses_pos_venda_model_not_public_questions(self):
        body = function_body(backend_text(), "_ml_pos_venda_gerar_resposta_ia")
        self.assertIn("_ia_modelo_pos_venda_configurado()", body)
        self.assertNotIn("_ia_modelo_perguntas_configurado()", body)
        self.assertIn("ML_POS_VENDA_IA_V2_MODO", body)
        self.assertNotIn("_pos_venda_ia_resposta_final_loja", body)
        self.assertIn("return resposta_literal, model_usado", body)
        self.assertIn("Finalize exatamente com", body)
        self.assertIn("sem se apresentar como assistente", body)
        self.assertIn("contexto_pipeline", body)

    def test_pos_venda_automation_is_manual_only(self):
        body = function_body(backend_text(), "ml_pos_venda_automacao_poll")
        approval_builder = function_body(backend_text(), "_customer_reply_post_sale_approval")
        self.assertIn('"disabled": True', body)
        self.assertIn('"motivo": "pos_venda_somente_manual"', body)
        self.assertNotIn("_customer_reply_requires_approval()", body)
        self.assertNotIn("_customer_reply_post_sale_approval", body)
        self.assertIn('"aprovacao_obrigatoria_ia": perguntas_agent_approval.post_sale_requires_approval()', approval_builder)
        self.assertIn('"ia_modo": _ia_modo_pos_venda_configurado()', approval_builder)
        self.assertNotIn("_ml_pos_venda_executar_pipeline_ia", body)
        self.assertNotIn('"sent_auto_pos_venda"', body)
        self.assertNotIn("_ml_pos_venda_enviar_resposta_ml", body)
        self.assertIn('"ia_pipeline": _ml_pos_venda_pipeline_resumo(contexto_ia)', approval_builder)

    def test_pos_venda_pipeline_has_user_requested_steps(self):
        source = backend_text()
        for etapa in (
            "receber_mensagem_e_conferir_historico",
            "buscar_dados_do_pedido",
            "buscar_dados_do_anuncio",
            "buscar_status_do_envio",
            "buscar_status_de_pagamento",
            "buscar_nota_fiscal",
            "buscar_reclamacao_ou_mediacao",
            "classificar_motivo_da_mensagem",
            "decidir_se_pode_responder_automaticamente",
            "decidir_se_precisa_consultar_regras_oficiais",
            "montar_contexto_para_ia",
            "ia_gera_resposta",
            "validador_revisa_resposta",
            "envia_resposta_ou_manda_para_humano",
            "salva_auditoria",
        ):
            self.assertIn(etapa, source)
        self.assertIn("def _ml_pos_venda_montar_contexto_pipeline", source)
        self.assertIn("def _ml_pos_venda_validar_resposta", source)
        self.assertIn("def _ml_pos_venda_auditoria_registrar", source)

    def test_config_pages_expose_pos_venda_model(self):
        for path in CONFIG_HTMLS:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("iaModeloPosVenda", source)
                self.assertIn("iaModoPosVenda", source)
                self.assertIn("ia_modelo_pos_venda", source)
                self.assertIn("ia_modo_pos_venda", source)

    def test_pos_venda_all_accounts_interval_and_store_isolated_ai_scope_are_exposed(self):
        source = backend_text()
        self.assertIn("class PerguntasLojasConfigLoteRequest", source)
        self.assertIn('"/api/mercadolivre/perguntas/lojas/config-lote"', source)
        lote_body = function_body(source, "ml_perguntas_salvar_config_lojas_lote")
        self.assertIn("_perguntas_loja_config_obter", lote_body)
        self.assertIn("somente_conectadas", lote_body)
        self.assertIn("intervalo_minutos", lote_body)

        for path in POS_VENDA_HTMLS:
            with self.subTest(path=path.name):
                html = path.read_text(encoding="utf-8")
                self.assertIn("ai-training-scope", html)
                self.assertIn("Selecione uma loja", html)
                self.assertIn("lojaEscopoTreinamento", html)
                self.assertIn("Cada loja possui sua própria base", html)
                self.assertIn("ai-training-sku-list", html)
                self.assertIn("ai-training-sku-popover", html)
                self.assertNotIn("Padrao para todas as contas", html)

    def test_whatsapp_question_approval_option_is_persisted_and_exposed(self):
        source = backend_text()
        self.assertIn("notificar_whatsapp_aprovacoes", source)
        self.assertIn("_forward_question_approvals", source)
        self.assertIn("_handle_question_approval_command", source)
        self.assertIn("_regenerate_question_approval_response", source)
        canonical = (ROOT / "static" / "perguntas_pos_venda.html").read_text(encoding="utf-8")
        mirror = (ROOT / "perguntas_pos_venda.html").read_text(encoding="utf-8")
        self.assertEqual(canonical, mirror)
        controller = (ROOT / "static" / "perguntas_pos_venda" / "lojas-automacao.js").read_text(encoding="utf-8")
        self.assertIn('data-config="notificar_whatsapp_aprovacoes"', controller)
        self.assertIn("Aprovar, Negar e Gerar nova resposta", controller)

    def test_public_question_code_validator_ignores_article_before_model(self):
        source = backend_text()
        namespace = {
            "re": re,
            "_favoritos_normalizar_sem_acentos": lambda texto: "".join(
                ch for ch in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(ch)
            ).lower(),
        }
        exec(
            "\n\n".join([
                extract_assignment(source, "ML_PERGUNTAS_IA_PREFIXOS_CODIGO_IGNORADOS"),
                function_body(source, "_ia_agent_perguntas_codigos_modelo"),
                function_body(source, "_ia_agent_perguntas_codigos_modelo_tem_match"),
            ]),
            namespace,
        )
        codigos = namespace["_ia_agent_perguntas_codigos_modelo"]("Minha e uma 320i 2008/2009 serve")
        self.assertIn("320I", codigos)
        self.assertNotIn("UMA320I", codigos)
        self.assertNotIn("2008", codigos)
        self.assertTrue(namespace["_ia_agent_perguntas_codigos_modelo_tem_match"]({"320I"}, {"BMW320I"}))
        self.assertTrue(namespace["_ia_agent_perguntas_codigos_modelo_tem_match"]({"4000"}, {"F4000"}))

    def test_public_question_code_validator_does_not_require_literal_code_for_compatibility(self):
        body = function_body(backend_text(), "_ia_agent_perguntas_violacoes_aderencia")
        self.assertIn("not pergunta_compatibilidade", body)
        self.assertIn("nao respondeu ao modelo/codigo perguntado", body)

    def test_public_question_chassis_is_compatibility_and_not_asked_again(self):
        source = backend_text()
        classifier_body = function_body(source, "_perguntas_ia_classificar_intencao")
        classifier_prompt_body = function_body(source, "_perguntas_ia_classification_prompt")
        validator_body = "\n".join([
            function_body(source, "_ia_agent_perguntas_contexto_validacao"),
            function_body(source, "_ia_agent_perguntas_violacoes_politica"),
        ])
        repair_body = "\n".join([
            function_body(source, "_perguntas_ia_tentar_reparo"),
            function_body(source, "_perguntas_ia_validar_resposta"),
        ])
        self.assertNotIn("def _perguntas_ia_intencao_heuristica", source)
        self.assertIn("_perguntas_ia_classification_prompt", classifier_body)
        self.assertIn("categoria deve ser uma de", classifier_prompt_body)
        self.assertIn("REGRA IFF OBRIGATORIA", classifier_prompt_body)
        self.assertIn("_perguntas_ia_categoria_classificada", validator_body)
        self.assertIn("QuestionCategory.COMPATIBILITY.value", validator_body)
        self.assertIn("_ia_agent_perguntas_resposta_pede_chassi(texto)", validator_body)
        self.assertNotIn("_perguntas_ia_v2_corrigir_resposta_bloqueada", repair_body)
        self.assertIn("sem reparo ou substituicao", repair_body)
        self.assertIn("return resposta, model_usado", repair_body)
        self.assertNotIn("_perguntas_ia_v2_resposta_segura_compatibilidade", repair_body)
        self.assertNotIn("fallback_local_compatibilidade", repair_body)
        self.assertNotIn("_perguntas_ia_v2_resposta_aterrada_navigator", source)
        self.assertNotIn("preparação original BMW para Navigator", source)

        import backend_api  # noqa: F401

        classified_input = {
            "question": {"text": "Serve no veiculo de chassi WVGS565NXDW555974?"},
            "item": {"title": "Peca automotiva"},
            "intent": ai_classification(
                "Serve no veiculo de chassi WVGS565NXDW555974?",
                category="compatibility",
                use_web=True,
                target_item="veiculo de chassi WVGS565NXDW555974",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="codigo OEM interface e aplicacao",
            ),
        }
        self.assertIn(
            "pediu chassi em pergunta de compatibilidade",
            agent_validation._ia_agent_perguntas_violacoes_resposta(
                classified_input,
                "Informe o chassi para verificarmos.",
            ),
        )

        namespace = {
            "re": re,
            "_favoritos_normalizar_sem_acentos": lambda texto: "".join(
                ch for ch in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(ch)
            ).lower(),
        }
        exec(function_body(source, "_ia_agent_perguntas_resposta_pede_chassi"), namespace)
        pede_chassi = namespace["_ia_agent_perguntas_resposta_pede_chassi"]
        self.assertTrue(pede_chassi("Informe o chassi para verificarmos."))
        self.assertFalse(pede_chassi("Para o chassi informado, recomendamos confirmar com mecanico."))

    def test_public_questions_v2_compatibility_pipeline_is_sequential(self):
        source = (
            ROOT / "backend" / "modules" / "perguntas_pos_venda" / "ai"
            / "client_compatibility_workflow.py"
        ).read_text(encoding="utf-8")
        collect_body = function_body(source, "collect_internal")
        start_body = function_body(source, "_start_compatibility")
        resolve_body = function_body(source, "_resolve_compatibility")
        run_body = function_body(source, "run_compatibility")
        tool_order = [
            "bindings.listing_tool",
            "bindings.product_tool",
            "bindings.bling_tool",
            "bindings.context_hub_tool",
        ]
        phase_order = [
            "collect_internal",
            "canonical_coverage_reference",
            "build_technical_question_plan",
            "collect_external",
            "prepare_document_vision",
            "prepare_grounding",
            "compatibility_prompt",
        ]
        for atual, seguinte in zip(tool_order, tool_order[1:]):
            self.assertLess(collect_body.index(atual), collect_body.index(seguinte))
        for atual, seguinte in zip(phase_order, phase_order[1:]):
            self.assertLess(start_body.index(atual), start_body.index(seguinte))
        self.assertLess(resolve_body.index("resolve_technical_question"), resolve_body.index("commit_technical_resolution"))
        for atual, seguinte in zip(
            ["_start_compatibility", "_resolve_compatibility", "_compatibility_alternative", "_public_compatibility_response"],
            ["_resolve_compatibility", "_compatibility_alternative", "_public_compatibility_response"],
        ):
            self.assertLess(run_body.index(atual), run_body.index(seguinte))

    def test_r1300gs_queries_are_short_and_separate_vehicle_from_listing_ids(self):
        import backend_api  # noqa: F401

        agent_input = {
            "question": {"text": "Serve no suporte gps da 1300gs?"},
            "item": {
                "id": "MLB2785882411",
                "seller_sku": "241-1",
                "title": "Adaptador Smartphone Suporte GPS BMW R1250 R1200 F850 GS ADV Preto",
            },
            "intent": ai_classification(
                "Serve no suporte gps da 1300gs?",
                category="compatibility",
                use_web=True,
                target_item="BMW R1300GS",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="interface base conector preparacao",
            ),
        }
        queries = agent_queries._ia_agent_perguntas_queries_web(agent_input, [])

        self.assertEqual([item["type"] for item in queries], [
            "target_interface_official", "product_interface_technical", "interface_equivalence",
        ])
        self.assertIn("BMW R1300GS", queries[0]["query"])
        self.assertNotIn("suporte gps da", queries[0]["query"].lower())
        for item in queries:
            self.assertNotIn("MLB2785882411", item["query"])
            self.assertNotIn("241-1", item["query"])
            self.assertLessEqual(len(item["query"]), 260)

    def test_sku_241_1_queries_use_collected_navigator_interface_without_internal_ids(self):
        import backend_api  # noqa: F401

        agent_input = {
            "question": {"text": "Serve no suporte gps da 1300gs?"},
            "item": {
                "id": "MLB2785882411",
                "seller_sku": "241-1",
                "title": "Adaptador Smartphone Suporte GPS BMW R1250 R1200 F850 GS ADV Preto",
            },
            "intent": ai_classification(
                "Serve no suporte gps da 1300gs?",
                category="compatibility",
                use_web=True,
                target_item="BMW R1300GS",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="interface base conector preparacao",
            ),
        }
        collected = [{
            "function": "get_mercado_livre_listing",
            "result": {
                "found": True,
                "matches": [{
                    "id": "MLB2785882411",
                    "description": "Necessaria base original Garmin Navigator 4, 5 e 6.",
                }],
            },
        }]

        queries = agent_queries._ia_agent_perguntas_queries_web(agent_input, collected)
        identity_queries = agent_queries._ia_agent_perguntas_queries_identificacao_produto(agent_input, collected)

        self.assertTrue(all("Navigator" in item["query"] for item in queries))
        self.assertIn("Navigator", identity_queries[0]["query"])
        self.assertIn("BMW R1300GS", queries[0]["query"])
        for item in [*queries, *identity_queries]:
            self.assertNotIn("MLB2785882411", item["query"])
            self.assertNotIn("241-1", item["query"])
            self.assertLessEqual(len(item["query"]), 260)

    def test_sku_307_k_builds_code_and_attribute_queries_instead_of_treating_question_as_target(self):
        import backend_api  # noqa: F401

        agent_input = {
            "question": {
                "text": "Boa tarde, essa carcaca da valvula termostatica e de engate rapido ou para abracadeira?",
            },
            "item": {
                "id": "MLB4129425225",
                "seller_sku": "307-K",
                "title": "Carcaca Valvula Termostatica THP 1.6 16v DS3 308 4008 C4 408",
                "description": "Codigos de referencia 11537534521 / 52514 / 7534521 / 9810916980",
            },
            "intent": ai_classification(
                "Boa tarde, essa carcaca da valvula termostatica e de engate rapido ou para abracadeira?",
                use_web=True,
                technical_focus="engate rapido abracadeira",
                required_evidence="codigos da peca e especificacao tecnica das conexoes",
            ),
        }

        queries = agent_queries._ia_agent_perguntas_queries_web(agent_input, [])

        self.assertEqual([item["type"] for item in queries], [
            "product_specification_by_code",
            "product_specification_by_code",
            "product_feature_technical",
        ])
        self.assertIn("11537534521", queries[0]["query"])
        self.assertIn("9810916980", queries[1]["query"])
        self.assertTrue(all("engate rapido abracadeira" in item["query"] for item in queries))
        self.assertTrue(all("Boa tarde" not in item["query"] for item in queries))
        self.assertTrue(all("MLB4129425225" not in item["query"] for item in queries))
        self.assertTrue(all("307-K" not in item["query"] for item in queries))
        self.assertNotIn('"', agent_queries._ia_agent_perguntas_relaxar_query_web(queries[0]["query"]))
        self.assertIn("11537534521", agent_queries._ia_agent_perguntas_query_ml_publica(queries[0]["query"]))

    def test_public_feature_without_canonical_fact_uses_high_risk_research(self):
        import backend_api  # noqa: F401 - configura os globals do runtime modular
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        answer = '{"answer":"Acompanha cabo USB.","confidence":0.96,"requires_human_review":false,"reason":"listing_evidence"}'
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Acompanha cabo?"},
            "intent": ai_classification("Acompanha cabo?"),
        })
        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(answer),
        ) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=None,
        ) as web_call:
            result = client.generate("prompt com historico e anuncio", {
                "category": "product_feature",
                "question_text": "Acompanha cabo?",
                "item_id": "MLB1",
                "listing_title": "Produto com cabo USB",
                "history_count": 2,
            })

        self.assertEqual(result.answer, "Acompanha cabo USB.")
        self.assertEqual(model_call.call_count, 6)
        self.assertEqual(
            _v16_model_stages(model_call),
            [
                "technical_question_plan",
                "technical_evidence_graph",
                "technical_resolution_round_1",
                "technical_resolution_final",
                "external_research_final",
                "factual_critic",
            ],
        )
        web_call.assert_called_once()
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "unavailable")
        self.assertEqual(research_step["reason"], "decisive_fact_missing")
        self.assertEqual(research_step["synthesis_status"], "completed_without_external_result")
        self.assertNotIn("seller_response_render", [step["name"] for step in client.context_pipeline])

    def test_public_categories_use_adaptive_external_research_end_to_end(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        draft = '{"answer":"Rascunho da loja.","confidence":0.9,"requires_human_review":false,"reason":"listing_evidence"}'
        categories = (
            "greeting", "price", "stock", "shipping", "product_feature",
            "warranty_originality", "invoice", "other_product", "prohibited_contact",
        )
        for category in categories:
            with self.subTest(category=category):
                client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
                    "question": {"text": "Pode informar?"},
                    "item": {"id": "MLB1", "seller_sku": "SKU-001", "title": "Produto"},
                    "product_evidence_identity": {
                        "store_ref": "store-test",
                        "seller_id": "588182191",
                        "site_id": "MLB",
                        "sku": "SKU-001",
                        "item_id": "MLB1",
                        "variation_id": "",
                    },
                    "intent": ai_classification("Pode informar?", category=category),
                })
                with patch.object(
                    agent,
                    "_ia_agent_perguntas_chamar_modelo",
                    side_effect=_v16_model_responder(draft),
                ), patch.object(
                    agent,
                    "_perguntas_ia_context_hub_tool",
                    return_value=None,
                ), patch.object(
                    agent,
                    "_ia_agent_perguntas_web_tool",
                    return_value=None,
                ) as web_call:
                    result = client.generate("prompt com anuncio", {
                        "category": category,
                        "question_text": "Pode informar?",
                        "item_id": "MLB1",
                        "listing_title": "Produto",
                    })

                self.assertEqual(result.answer, "Rascunho da loja.")
                if category in {"product_feature", "warranty_originality", "prohibited_contact"}:
                    web_call.assert_called_once()
                    research_step = _pipeline_step(client, "question_focused_web_research")
                    self.assertIn(
                        research_step["reason"],
                        {"decisive_fact_missing", "originality_required", "no_research_needed"},
                    )
                else:
                    web_call.assert_not_called()
                    self.assertEqual(
                        _pipeline_step(client, "adaptive_simple_public_generation")["route"],
                        "simple_operational",
                    )

    def test_incomplete_operational_identity_escalates_without_unneeded_public_research(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        draft = '{"answer":"Rascunho seguro.","confidence":0.9,"requires_human_review":true,"reason":"identity_incomplete"}'
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Tem estoque?"},
            "item": {"id": "MLB1", "seller_sku": "SKU-001", "title": "Produto"},
            "intent": ai_classification("Tem estoque?", category="stock"),
        })
        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(draft),
        ), patch.object(
            agent,
            "_perguntas_ia_context_hub_tool",
            return_value=None,
        ), patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=None,
        ) as web_call:
            result = client.generate("prompt com anuncio", {
                "category": "stock",
                "question_text": "Tem estoque?",
                "item_id": "MLB1",
                "listing_title": "Produto",
            })

        self.assertEqual(result.answer, "Rascunho seguro.")
        self.assertEqual(client.adaptive_route, "high_risk")
        self.assertIn("identity_incomplete", client.sku_question_context["route_reasons"])
        web_call.assert_not_called()
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "unavailable")
        self.assertEqual(research_step["reason"], "no_research_needed")
        self.assertEqual(research_step["synthesis_status"], "completed_without_external_result")

    def test_public_questions_v2_preserves_draft_when_external_synthesis_fails(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        draft = "  Acompanha cabo USB.  \n"
        web_result = {
            "function": "web_search_question_context",
            "arguments": {"queries": [{"type": "product_feature_technical", "query": "Produto cabo USB fabricante"}]},
            "result": {
                "found": True,
                "context": "Catalogo tecnico do produto.\nURL: https://fabricante.example/catalogo",
                "verified_product_evidence": [
                    _verified_fact("kit.contents", "cabo USB incluso", scope="kit"),
                ],
            },
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Acompanha cabo?", "current_draft_to_avoid": draft},
            "intent": ai_classification("Acompanha cabo?"),
        })
        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(
                {"answer": draft, "confidence": 0.9, "reason": "listing_evidence"},
                fail_stage="external_research_final",
            ),
        ) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=web_result,
        ) as web_call:
            result = client.generate("prompt com anuncio", {
                "category": "product_feature",
                "question_text": "Acompanha cabo?",
                "item_id": "MLB1",
                "listing_title": "Produto com cabo USB",
            })

        web_call.assert_called_once()
        self.assertEqual(model_call.call_count, 6)
        self.assertEqual(result.answer, draft)
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "completed")
        self.assertEqual(research_step["synthesis_status"], "error")
        self.assertEqual(research_step["fallback"], "best_existing_ai_draft")
        self.assertNotIn("seller_response_render", [step["name"] for step in client.context_pipeline])

    def test_public_questions_v2_preserves_draft_when_external_research_times_out(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        draft = " \nAcompanha cabo USB.  \n"
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Acompanha cabo?", "current_draft_to_avoid": draft},
            "intent": ai_classification("Acompanha cabo?"),
        })
        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(
                {"answer": draft, "confidence": 0.9, "reason": "listing_evidence"},
                fail_stage="external_research_final",
            ),
        ) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            side_effect=TimeoutError("public research timeout"),
        ) as web_call:
            result = client.generate("prompt com anuncio", {
                "category": "product_feature",
                "question_text": "Acompanha cabo?",
                "item_id": "MLB1",
                "listing_title": "Produto com cabo USB",
            })

        web_call.assert_called_once()
        self.assertEqual(model_call.call_count, 6)
        self.assertEqual(result.answer, draft)
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "error")
        self.assertEqual(research_step["synthesis_status"], "error")
        self.assertEqual(research_step["fallback"], "best_existing_ai_draft")
        self.assertNotIn("seller_response_render", [step["name"] for step in client.context_pipeline])

    def test_store_bound_public_questions_research_without_replacing_trusted_draft(self):
        web_result = {
            "function": "web_search_question_context",
            "arguments": {"queries": [{"type": "public_reference", "query": "produto"}]},
            "result": {
                "found": True,
                "context": "Pagina publica com texto conflitante que nao pode alterar dados da loja.",
            },
        }

        for category in (
            "greeting", "price", "stock", "shipping", "invoice",
            "warranty_originality", "prohibited_contact", "other_product",
        ):
            with self.subTest(category=category):
                draft = AIAnswer(
                    answer=f"Rascunho autenticado de {category}.",
                    confidence=0.96,
                    requires_human_review=False,
                    reason="authenticated_store_evidence",
                )

                class Client:
                    client_id = "cliente"
                    agent_input = {"question": {"text": "Pergunta operacional"}}
                    context_pipeline = []

                    def _call_model(self, *_args, **_kwargs):
                        raise AssertionError("pesquisa publica nao deve sintetizar fatos operacionais")

                client = Client()
                binding = agent_workflows.GeneralBindings(
                    context_hub_tool=lambda *_args, **_kwargs: {},
                    web_tool=lambda *_args, **_kwargs: web_result,
                )

                result = agent_workflows._web_fallback(
                    client,
                    "prompt",
                    {"category": category},
                    {},
                    draft,
                    binding,
                )

                self.assertIs(result, draft)
                self.assertEqual(result.answer, f"Rascunho autenticado de {category}.")
                research_step = _pipeline_step(client, "question_focused_web_research")
                self.assertEqual(research_step["synthesis_status"], "error")
                self.assertEqual(research_step["fallback"], "best_existing_ai_draft")

    def test_store_bound_public_questions_preserve_draft_end_to_end_against_conflicting_renderer(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        web_result = {
            "function": "web_search_question_context",
            "arguments": {"queries": [{"type": "public_reference", "query": "produto"}]},
            "result": {
                "found": True,
                "context": "Pagina publica conflitante e nao autoritativa para a operacao da loja.",
            },
        }
        cases = {
            "greeting": "Ola! Como podemos ajudar?",
            "price": "O preco atual e R$ 100.",
            "stock": "Temos 3 unidades disponiveis.",
            "shipping": "O envio ocorre hoje.",
            "invoice": "Emitimos nota fiscal.",
            "warranty_originality": "A garantia desta unidade e de 90 dias.",
            "prohibited_contact": "Podemos atender somente pelos canais permitidos no Mercado Livre.",
            "other_product": "Nao temos outro modelo cadastrado nesta loja.",
        }

        for category, expected in cases.items():
            with self.subTest(category=category):
                draft = json.dumps({
                    "answer": expected,
                    "confidence": 0.96,
                    "requires_human_review": False,
                    "reason": "authenticated_store_evidence",
                })
                conflicting = json.dumps({
                    "answer": "A informacao operacional autenticada foi alterada.",
                    "confidence": 0.99,
                    "requires_human_review": False,
                    "reason": "conflicting_renderer",
                })
                v16_responder = _v16_model_responder(draft)

                def preserve_against_conflicting_critic(client_id, request, model_req):
                    if request.context.get("context_collection_stage") == "factual_critic":
                        return conflicting, model_req
                    return v16_responder(client_id, request, model_req)

                client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
                    "question": {"text": "Pergunta operacional"},
                    "item": {"id": "MLB1", "seller_sku": "SKU-001", "title": "Produto"},
                    "product_evidence_identity": {
                        "store_ref": "store-test",
                        "seller_id": "588182191",
                        "site_id": "MLB",
                        "sku": "SKU-001",
                        "item_id": "MLB1",
                        "variation_id": "",
                    },
                    "intent": ai_classification("Pergunta operacional", category=category),
                })
                with patch.object(
                    agent,
                    "_ia_agent_perguntas_chamar_modelo",
                    side_effect=preserve_against_conflicting_critic,
                ) as model_call, patch.object(
                    agent,
                    "_ia_agent_perguntas_web_tool",
                    return_value=web_result,
                ) as web_call:
                    result = client.generate("prompt com dados autenticados", {
                        "category": category,
                        "question_text": "Pergunta operacional",
                        "item_id": "MLB1",
                        "listing_title": "Produto",
                    })

                if category in {"warranty_originality", "prohibited_contact"}:
                    web_call.assert_called_once()
                    self.assertEqual(model_call.call_count, 6)
                    self.assertEqual(
                        _v16_model_stages(model_call),
                        [
                            "technical_question_plan",
                            "technical_evidence_graph",
                            "technical_resolution_round_1",
                            "technical_resolution_final",
                            "external_research_final",
                            "factual_critic",
                        ],
                    )
                else:
                    web_call.assert_not_called()
                    self.assertEqual(model_call.call_count, 1)
                    self.assertEqual(_v16_model_stages(model_call), ["adaptive_simple_public_answer"])
                self.assertEqual(result.answer, expected)
                if category in {"warranty_originality", "prohibited_contact"}:
                    research_step = _pipeline_step(client, "question_focused_web_research")
                    self.assertEqual(research_step["source_precedence"], "official_store_only")
                else:
                    adaptive_step = _pipeline_step(client, "adaptive_simple_public_generation")
                    self.assertEqual(adaptive_step["web_research"], "skipped")
                self.assertNotIn("seller_response_render", [step["name"] for step in client.context_pipeline])

    def test_mixed_technical_and_shipping_question_preserves_store_draft_after_research(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        intent = ai_classification(
            "Com quantos graus aciona e voces enviam hoje?",
            category="product_feature",
        )
        intent["categorias"] = ["product_feature", "shipping"]
        intent["subperguntas"].append({
            "intent": "shipping",
            "question": "Voces enviam hoje?",
            "required_evidence": "dados autenticados de envio da loja",
        })
        final = json.dumps({
            "answer": "Aciona a 93 C e enviamos hoje.",
            "confidence": 0.95,
            "requires_human_review": False,
            "reason": "combined_store_and_external_evidence",
        })
        web_result = {
            "function": "web_search_question_context",
            "arguments": {"queries": [{"type": "product_feature_technical", "query": "sensor 93 C"}]},
            "result": {"found": True, "context": "Catalogo tecnico: acionamento a 93 C."},
        }
        web_result["result"]["verified_product_evidence"] = [
            _verified_fact("temperature.activation", "93", scope="product"),
        ]
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Com quantos graus aciona e voces enviam hoje?"},
            "intent": intent,
        })

        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(final),
        ) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=web_result,
        ) as web_call:
            result = client.generate("prompt com dados autenticados", {
                "category": "product_feature",
                "question_text": "Com quantos graus aciona e voces enviam hoje?",
                "item_id": "MLB1",
                "listing_title": "Sensor Cebolao",
            })

        web_call.assert_called_once()
        self.assertEqual(model_call.call_count, 6)
        self.assertEqual(result.answer, "Aciona a 93 C e enviamos hoje.")
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "completed")

    def test_sequential_tool_error_does_not_expose_exception_message(self):
        private_url = "https://externo.example/?buyer=Joao-Silva"
        client = agent_clients._PerguntasVertexGeminiV2Client(
            "cliente",
            "Loja",
            "codex:gpt-5.5",
            {
                "question": {"text": "Pergunta"},
                "intent": ai_classification("Pergunta"),
            },
        )

        with patch.object(agent_clients.logger, "warning") as warning:
            result = client._tool_segura(
                "context_hub_search",
                lambda: (_ for _ in ()).throw(RuntimeError(private_url)),
            )

        self.assertEqual(result["result"]["error"], "RuntimeError")
        self.assertNotIn(private_url, str(warning.call_args))
        self.assertNotIn(private_url, json.dumps(result))

    def test_public_questions_v2_enforces_hard_web_timeout_with_daemon_worker(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        release = threading.Event()
        started = threading.Event()
        finished = threading.Event()

        def blocked_web(*_args, **_kwargs):
            started.set()
            release.wait(2.0)
            finished.set()
            return None

        draft = '{"answer":"Acompanha cabo USB.","confidence":0.96,"requires_human_review":false,"reason":"listing_evidence"}'
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {
            "question": {"text": "Acompanha cabo?"},
            "intent": ai_classification("Acompanha cabo?"),
        })
        result = None
        worker_snapshot = []
        started_at = time.monotonic()
        try:
            with patch.object(
                agent_workflows,
                "_ia_agent_perguntas_tools_timeout_s",
                return_value=0.05,
            ), patch.object(
                agent,
                "_perguntas_ia_context_hub_tool",
                return_value=None,
            ), patch.object(
                agent,
                "_ia_agent_perguntas_chamar_modelo",
                return_value=(draft, "codex:gpt-5.5"),
            ), patch.object(
                agent,
                "_ia_agent_perguntas_web_tool",
                side_effect=blocked_web,
            ):
                result = client.generate("prompt com anuncio", {
                    "category": "product_feature",
                    "question_text": "Acompanha cabo?",
                    "item_id": "MLB1",
                    "listing_title": "Produto com cabo USB",
                })
                worker_snapshot = [
                    worker for worker in threading.enumerate()
                    if worker.name.startswith(agent_workflows._MANDATORY_WEB_THREAD_PREFIX)
                ]
        finally:
            elapsed = time.monotonic() - started_at
            release.set()

        self.assertTrue(started.is_set())
        self.assertTrue(worker_snapshot)
        self.assertTrue(all(worker.daemon for worker in worker_snapshot))
        self.assertLessEqual(len(worker_snapshot), agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)
        self.assertTrue(finished.wait(1.0))
        self.assertLess(elapsed, 0.8)
        self.assertIsNotNone(result)
        self.assertEqual(result.answer, "Acompanha cabo USB.")
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "error")
        self.assertEqual(research_step["synthesis_status"], "completed_without_external_result")

    def test_compatibility_enforces_hard_timeout_for_both_required_web_calls(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        release = threading.Event()
        identity_started = threading.Event()
        identity_finished = threading.Event()
        question_started = threading.Event()
        question_finished = threading.Event()

        def blocked(event_started, event_finished):
            def callback(*_args, **_kwargs):
                event_started.set()
                release.wait(2.0)
                event_finished.set()
                return None
            return callback

        answer = json.dumps({
            "answer": "Ainda precisamos confirmar a interface.",
            "confidence": 0.4,
            "requires_human_review": True,
            "reason": "missing_listing_evidence",
            "compatibility_analysis": {
                "decision": "insufficient",
                "missing_fields": ["interface alvo"],
                "confidence": 0.4,
                "reason": "missing_listing_evidence",
            },
        })
        agent_input = {
            "store": "Loja",
            "question": {"text": "Serve no Samsung S25?"},
            "item": {"id": "MLB1", "title": "Suporte para celular"},
            "intent": ai_classification(
                "Serve no Samsung S25?",
                category="compatibility",
                use_web=True,
                target_item="Samsung S25",
                target_type="phone_computing",
                compatibility_profile="device_interface",
                technical_focus="encaixe fisico",
                missing_fields=("interface alvo",),
                decisive_fields=("encaixe",),
            ),
        }
        client = agent._PerguntasVertexGeminiV2Client(
            "cliente", "Loja", "codex:gpt-5.5", agent_input
        )
        result = None
        model_call = None
        worker_snapshot = []
        started_at = time.monotonic()
        try:
            with patch.object(
                agent_workflows,
                "_ia_agent_perguntas_tools_timeout_s",
                return_value=0.05,
            ), patch.object(
                agent,
                "marketplace_listing_query",
                return_value={},
            ), patch.object(
                agent,
                "_ia_tool_get_product_data",
                return_value={},
            ), patch.object(
                agent,
                "_ia_tool_get_bling_product",
                return_value={},
            ), patch.object(
                agent,
                "_perguntas_ia_context_hub_tool",
                return_value=None,
            ), patch.object(
                agent,
                "_ia_agent_perguntas_product_identity_web_tool",
                side_effect=blocked(identity_started, identity_finished),
            ), patch.object(
                agent,
                "_ia_agent_perguntas_web_tool",
                side_effect=blocked(question_started, question_finished),
            ), patch.object(
                agent,
                "_ia_agent_perguntas_chamar_modelo",
                side_effect=_v16_model_responder(answer),
            ) as model_call:
                result = client.generate("prompt com anuncio", {
                    "category": "compatibility",
                    "question_text": "Serve no Samsung S25?",
                    "item_id": "MLB1",
                    "listing_title": "Suporte para celular",
                })
                worker_snapshot = [
                    worker for worker in threading.enumerate()
                    if worker.name.startswith(agent_workflows._MANDATORY_WEB_THREAD_PREFIX)
                ]
        finally:
            elapsed = time.monotonic() - started_at
            release.set()

        self.assertTrue(identity_started.is_set())
        self.assertTrue(question_started.is_set())
        self.assertGreaterEqual(len(worker_snapshot), 2)
        self.assertTrue(all(worker.daemon for worker in worker_snapshot))
        self.assertLessEqual(len(worker_snapshot), agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)
        self.assertTrue(identity_finished.wait(1.0))
        self.assertTrue(question_finished.wait(1.0))
        self.assertLess(elapsed, 0.8)
        self.assertIsNotNone(result)
        self.assertEqual(
            _v16_model_stages(model_call),
            [
                "technical_question_plan",
                "technical_evidence_graph",
                "technical_resolution_round_1",
                "technical_evidence_graph",
                "technical_resolution_final",
                "compatibility_public_answer",
                "factual_critic",
            ],
        )
        technical_payload = _v16_model_payload(model_call, "technical_evidence_graph")
        self.assertEqual(
            [item.get("function") for item in technical_payload.tool_results],
            [
                "store_sku_question_context",
                "web_search_question_context",
                "web_search_product_identity",
            ],
        )
        self.assertEqual(
            technical_payload.tool_results[0]["arguments"]["schema"],
            "jk_ml_store_sku_question_context_v1",
        )
        self.assertNotIn("web_search_product_identity", technical_payload.message)
        self.assertNotIn("web_search_question_context", technical_payload.message)
        self.assertEqual(
            [step["status"] for step in client.context_pipeline if step["name"] in {
                "product_interface_research", "official_technical_research",
            }],
            ["error", "error"],
        )

    def test_mandatory_web_timeout_bounds_stuck_daemon_workers(self):
        release = threading.Event()
        started = [threading.Event() for _ in range(agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)]
        worker_snapshot = []

        def blocked(started_event):
            started_event.set()
            release.wait(2.0)
            return None

        try:
            with patch.object(
                agent_workflows,
                "_ia_agent_perguntas_tools_timeout_s",
                return_value=0.02,
            ):
                results = [
                    agent_workflows._mandatory_web_tool(
                        "web_search_question_context",
                        lambda event=event: blocked(event),
                    )
                    for event in started
                ]
                overflow_started = threading.Event()
                overflow_started_at = time.monotonic()
                overflow = agent_workflows._mandatory_web_tool(
                    "web_search_question_context",
                    lambda: blocked(overflow_started),
                )
                overflow_elapsed = time.monotonic() - overflow_started_at
                worker_snapshot = [
                    worker for worker in threading.enumerate()
                    if worker.name.startswith(agent_workflows._MANDATORY_WEB_THREAD_PREFIX)
                ]
        finally:
            release.set()
            for worker in worker_snapshot:
                worker.join(1.0)

        self.assertTrue(all(event.is_set() for event in started))
        self.assertTrue(all(result["result"].get("timeout") is True for result in results))
        self.assertFalse(overflow_started.is_set())
        self.assertTrue(overflow["result"].get("timeout") is True)
        self.assertLess(overflow_elapsed, 0.2)
        self.assertEqual(len(worker_snapshot), agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)
        self.assertTrue(all(worker.daemon for worker in worker_snapshot))

    def test_mandatory_web_timeout_does_not_hold_process_exit(self):
        script = """
import threading
from backend.modules.perguntas_pos_venda.ai import client_workflows as workflows
from backend.modules.perguntas_pos_venda.ai import sources
from backend.services import ia_web
workflows._ia_agent_perguntas_tools_timeout_s = lambda: 0.02
ia_web._ia_web_busca_ativa = lambda: True
ia_web._favoritos_busca_externa_provedores_configurados = lambda **_kwargs: ["blocked"]
ia_web._favoritos_busca_externa_chamar_api = lambda *_args, **_kwargs: threading.Event().wait()
provider_result = workflows._mandatory_web_tool(
    "web_search_question_context",
    lambda: ia_web._ia_web_buscar_amplo("produto", max_results=3, fast=True),
)
source_result = workflows._mandatory_web_tool(
    "web_search_question_context",
    lambda: sources._ia_agent_perguntas_contexto_web(
        "tenant",
        "loja",
        [{"type": "product_feature_technical", "query": "produto tecnico"}],
        search_fn=lambda *_args, **_kwargs: threading.Event().wait(),
        authenticated_listings_fn=lambda *_args, **_kwargs: [],
        public_listings_fn=lambda *_args, **_kwargs: [],
    ),
)
assert provider_result["result"].get("timeout") is True
assert source_result["result"].get("timeout") is True
print("nested-deadlines-returned", flush=True)
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("nested-deadlines-returned", completed.stdout)

    def test_mandatory_web_thread_start_failure_restores_capacity(self):
        class FailingThread:
            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("thread unavailable")

        with patch.object(agent_workflows, "Thread", FailingThread):
            failed = agent_workflows._mandatory_web_tool(
                "web_search_question_context",
                lambda: None,
            )

        recovered = agent_workflows._mandatory_web_tool(
            "web_search_question_context",
            lambda: None,
        )

        self.assertTrue(failed["result"].get("error"))
        self.assertFalse(failed["result"].get("timeout", False))
        self.assertTrue(recovered["result"].get("unavailable"))

    def test_mandatory_web_thread_constructor_failure_restores_all_capacity(self):
        slots = threading.BoundedSemaphore(agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)

        class FailingThread:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("thread unavailable")

        with patch.object(agent_workflows, "_MANDATORY_WEB_SLOTS", slots), patch.object(
            agent_workflows,
            "Thread",
            FailingThread,
        ):
            failed = agent_workflows._mandatory_web_tool(
                "web_search_question_context",
                lambda: None,
            )

        acquired = [
            slots.acquire(blocking=False)
            for _ in range(agent_workflows._MANDATORY_WEB_MAX_IN_FLIGHT)
        ]
        try:
            self.assertTrue(failed["result"].get("error"))
            self.assertTrue(all(acquired))
            self.assertFalse(slots.acquire(blocking=False))
        finally:
            for was_acquired in acquired:
                if was_acquired:
                    slots.release()

    def test_query_worker_thread_constructor_failure_restores_all_capacity(self):
        slots = threading.BoundedSemaphore(12)

        class FailingThread:
            def __init__(self, *_args, **_kwargs):
                raise RuntimeError("thread unavailable")

        with patch.object(agent_sources, "_IA_AGENT_PERGUNTAS_WEB_QUERY_SLOTS", slots), patch.object(
            agent_sources,
            "Thread",
            FailingThread,
        ):
            result = agent_sources._ia_agent_perguntas_prefetch_web(
                "cliente",
                ["produto tecnico"],
                lambda *_args, **_kwargs: [],
            )

        acquired = [slots.acquire(blocking=False) for _ in range(12)]
        try:
            self.assertEqual(result, {})
            self.assertTrue(all(acquired))
            self.assertFalse(slots.acquire(blocking=False))
        finally:
            for was_acquired in acquired:
                if was_acquired:
                    slots.release()

    def test_public_question_missing_technical_attribute_uses_external_research_fallback(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        final = json.dumps({
            "answer": "Essa carcaca utiliza engate rapido nas conexoes das mangueiras.",
            "confidence": 0.88,
            "requires_human_review": True,
            "reason": "external_technical_sources",
        })
        web_result = {
            "function": "web_search_question_context",
            "arguments": {
                "query": "9810916980 engate rapido abracadeira",
                "queries": [{
                    "type": "product_specification_by_code",
                    "query": "9810916980 engate rapido abracadeira ficha tecnica",
                }],
            },
            "result": {
                "found": True,
                "context": (
                    "Produto equivalente pelo codigo 9810916980: carcaca com dois engates rapidos.\n"
                    "URL: https://fabricante.example/9810916980"
                ),
                "verified_product_evidence": [
                    _verified_fact("interface.connection", "dois engates rapidos"),
                ],
            },
        }
        agent_input = {
            "store": "Loja",
            "question": {"text": "Essa carcaca e de engate rapido ou para abracadeira?"},
            "item": {
                "id": "MLB4129425225",
                "seller_sku": "307-K",
                "title": "Carcaca Valvula Termostatica THP 1.6",
                "description": "Codigo 9810916980",
            },
            "intent": ai_classification(
                "Essa carcaca e de engate rapido ou para abracadeira?",
                use_web=True,
                technical_focus="engate rapido abracadeira",
                required_evidence="codigo da peca e ficha tecnica das conexoes",
            ),
            "allowed_tools": ["web_search_question_context"],
            "use_web_search": True,
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        with patch.object(
            agent,
            "_ia_agent_perguntas_chamar_modelo",
            side_effect=_v16_model_responder(final),
        ) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=web_result,
        ) as web_call:
            result = client.generate("prompt com anuncio", {
                "category": "product_feature",
                "question_text": "Essa carcaca e de engate rapido ou para abracadeira?",
                "item_id": "MLB4129425225",
                "listing_title": "Carcaca Valvula Termostatica THP 1.6",
            })

        self.assertEqual(model_call.call_count, 6)
        web_call.assert_called_once()
        self.assertEqual(web_call.call_args.args[0], "cliente")
        self.assertEqual(web_call.call_args.args[1]["store"], "Loja")
        self.assertIn("engate rapido", result.answer.lower())
        self.assertNotIn("anuncio nao informa", result.answer.lower())
        research_step = _pipeline_step(client, "question_focused_web_research")
        self.assertEqual(research_step["status"], "completed")
        external_payload = _v16_model_payload(model_call, "technical_evidence_graph")
        self.assertEqual(external_payload.context["loja"], "Loja")
        self.assertIn("UNTRUSTED_REFERENCE_DATA", external_payload.message)
        self.assertEqual(external_payload.tool_results[0]["function"], "store_sku_question_context")
        self.assertIn(
            "engate rapido",
            json.dumps(external_payload.tool_results, ensure_ascii=False).lower(),
        )
        self.assertNotIn("engate rapido", external_payload.message.lower())

    def test_public_questions_v2_builds_structured_compatibility_analysis(self):
        import backend_api  # noqa: F401 - configura os globals do runtime modular
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        answer = json.dumps({
            "answer": "Esse adaptador e compativel com a R1300GS equipada com a base original BMW Navigator IV ou posterior. Ele encaixa nessa base e nao acompanha nem substitui o suporte original.",
            "confidence": 0.91,
            "requires_human_review": True,
            "reason": "technical_interface_equivalence",
            "compatibility_analysis": {
                "product_interface": "base original BMW Navigator IV/V/VI",
                "target_vehicle": "BMW R1300GS",
                "target_interface": "preparacao BMW Navigator IV ou posterior",
                "decision": "conditional",
                "condition": "moto equipada com a base original BMW Navigator",
                "missing_fields": [],
                "evidence": {
                    "product": [{
                        "authority": "generated_verified",
                        "reference": "base original BMW Navigator IV V VI",
                    }],
                    "target_vehicle": [{
                        "authority": "generated_verified",
                        "reference": "preparacao de navegacao adequada ao BMW Motorrad Navigator IV e posteriores",
                    }],
                    "equivalence": [{"authority": "generated_verified", "reference": "Navigator IV"}],
                },
                "confidence": 0.91,
                "reason": "same_navigation_interface",
            },
        })
        identity = {
            "function": "web_search_product_identity",
            "arguments": {"queries": [{"type": "product_interface_identity", "query": "adaptador BMW interface base"}]},
            "result": {
                "found": True,
                "context": "Ficha tecnica candidata sanitizada para avaliacao do modelo.",
                "verified_product_evidence": [
                    _verified_fact(
                        "interface.product",
                        "base original BMW Navigator IV V VI",
                    ),
                ],
            },
        }
        web_final = {
            "function": "web_search_question_context",
            "arguments": {"queries": [{"type": "target_interface_official", "query": "R1300GS manual interface base"}]},
            "result": {
                "found": True,
                "context": (
                    "Manual oficial BMW: preparacao de navegacao adequada ao BMW Motorrad Navigator IV e posteriores\n"
                    "URL: https://manuals.bmw-motorrad.com/manuals/BA-Extern/IN/BA-INTERNET-COM/PDF/R_0M23_RM_0223_07.pdf"
                ),
                "verified_product_evidence": [
                    _verified_fact(
                        "compatibility.application",
                        "BMW R1300GS com preparacao de navegacao adequada ao BMW Motorrad Navigator IV e posteriores",
                        scope="application",
                    ),
                ],
                "read_only": True,
            },
        }
        agent_input = {
            "question": {"text": "Serve no suporte GPS da R1300GS?"},
            "item": {"id": "MLB1", "title": "Adaptador smartphone BMW Navigator", "description": "Compativel com base Navigator IV, V e VI"},
            "intent": ai_classification(
                "Serve no suporte GPS da R1300GS?",
                category="compatibility",
                use_web=True,
                target_item="BMW R1300GS",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="base Navigator interface e encaixe",
                required_evidence="interface do adaptador, interface oficial da moto e equivalencia",
            ),
            "app_guidance": "Compare a base antes de responder.",
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        cadastro = {"function": "get_product_data", "result": {"found": True, "matches": [{"sku": "241-1", "nome": "Adaptador BMW"}]}}
        anuncio = {"function": "get_mercado_livre_listing", "result": {"found": True, "matches": [{"id": "MLB1", "description": "Navigator IV/V/VI"}]}}
        bling = {"function": "get_bling_product", "result": {"found": True, "matches": [{"nome": "Adaptador BMW"}]}}
        with patch.object(
             agent,
             "_ia_agent_perguntas_chamar_modelo",
             side_effect=_v16_model_responder(answer),
        ) as model_call, \
             patch.object(agent, "_ia_agent_perguntas_product_identity_web_tool", return_value=identity), \
             patch.object(agent, "_ia_tool_get_product_data", return_value=cadastro), \
             patch.object(agent, "marketplace_listing_query", return_value=anuncio), \
             patch.object(agent, "_ia_tool_get_bling_product", return_value=bling), \
             patch.object(agent, "_perguntas_ia_context_hub_tool", return_value={
                 "function": "context_hub_search",
                 "result": {"found": False, "results": [], "count": 0, "read_only": True},
             }), \
             patch.object(agent, "_perguntas_ia_memoria_bloco_prompt", return_value="Base Navigator IV/V/VI confirmada"), \
             patch.object(agent, "_ia_agent_perguntas_web_tool", return_value=web_final) as web_call:
            result = client.generate("prompt com historico e anuncio", {
                "category": "compatibility",
                "question_text": "Serve no suporte GPS da R1300GS?",
                "item_id": "MLB1",
                "listing_title": "Adaptador smartphone BMW Navigator",
                "history_count": 1,
            })

        self.assertEqual(
            result.answer,
            "Esse adaptador e compativel com a R1300GS equipada com a base original BMW Navigator IV ou posterior. "
            "Ele encaixa nessa base e nao acompanha nem substitui o suporte original.",
        )
        self.assertEqual(model_call.call_count, 7)
        self.assertEqual(web_call.call_count, 2)
        self.assertEqual(
            _v16_model_stages(model_call),
            [
                "technical_question_plan",
                "technical_evidence_graph",
                "technical_resolution_round_1",
                "technical_evidence_graph",
                "technical_resolution_final",
                "compatibility_public_answer",
                "factual_critic",
            ],
        )
        self.assertEqual(client.compatibility_analysis["decision"], "conditional")
        self.assertEqual(client.compatibility_analysis["target_vehicle"], "BMW R1300GS")
        self.assertEqual(len(client.compatibility_analysis["evidence"]["equivalence"]), 1)
        self.assertTrue(all(
            evidence["authority"] == "generated_verified"
            for group in ("product", "target_vehicle", "equivalence")
            for evidence in client.compatibility_analysis["evidence"][group]
        ))
        self.assertEqual(_pipeline_step(client, "factual_critic")["status"], "pass")
        pipeline_names = [step["name"] for step in client.context_pipeline]
        for expected_stage in (
            "technical_question_plan_v1",
            "technical_gap_web_research",
            "technical_evidence_graph",
            "technical_resolution_round_1",
            "technical_evidence_graph_final",
            "technical_resolution_final",
            "compatibility_public_generation",
            "factual_critic",
        ):
            self.assertIn(expected_stage, pipeline_names)
        payload_modelo = _v16_model_payload(model_call, "technical_evidence_graph")
        self.assertEqual(payload_modelo.tool_results[0]["function"], "store_sku_question_context")
        self.assertNotIn("MATERIAL_DE_EVIDENCIA_NAO_CONFIAVEL", payload_modelo.message)
        self.assertIn(
            "web_search_product_identity",
            [item.get("function") for item in payload_modelo.tool_results],
        )
        typed_evidence = json.dumps(payload_modelo.tool_results, ensure_ascii=False)
        self.assertIn("base original BMW Navigator", typed_evidence)
        self.assertIn("BMW R1300GS", typed_evidence)
        self.assertNotIn("base original BMW Navigator", payload_modelo.message)
        self.assertNotIn("BMW R1300GS", payload_modelo.message)
        self.assertNotIn("Ficha tecnica candidata", payload_modelo.message)
        self.assertNotIn("Manual oficial BMW:", payload_modelo.message)
        self.assertNotIn("source_url", payload_modelo.message)
        self.assertNotIn("attachment_name", payload_modelo.message)
        self.assertIn("UNTRUSTED_REFERENCE_DATA", payload_modelo.message)

    def test_official_technical_result_is_enriched_with_relevant_source_excerpt(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import sources as agent

        official_url = "https://manuals.bmw-motorrad.com/manuals/r1300gs.pdf"
        search_result = [{
            "title": "PDF Rider's manual R 1300 GS - BMW Motorrad",
            "url": official_url,
            "snippet": "Official rider manual.",
            "provider": "jina_duckduckgo",
        }]

        class Response:
            status_code = 200
            text = (
                "# R 1300 GS\n"
                "General safety information.\n"
                "The navigation preparation is suitable for BMW Motorrad Navigator IV and later.\n"
            )

            @staticmethod
            def raise_for_status():
                return None

        with patch.object(agent, "_ia_agent_perguntas_buscar_web_publica", return_value=search_result), patch.object(
            agent.requests, "get", return_value=Response()
        ) as source_read:
            context = agent._ia_agent_perguntas_contexto_web("cliente", "Loja", [{
                "type": "target_interface_official",
                "query": "BMW R1300GS Navigator 4 5 e 6 manual fabricante preparacao",
            }])

        self.assertIn("Leitura tecnica da fonte", context)
        self.assertIn("Navigator IV and later", context)
        self.assertIn(official_url, context)
        self.assertEqual(
            source_read.call_args.args[0],
            "https://r.jina.ai/" + official_url,
        )

    def test_product_feature_result_reads_technical_page_for_exact_part_code(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import sources as agent

        source_url = "https://catalogo.fabricante.example/pecas/9810916980"
        search_result = [{
            "title": "Carcaca da valvula termostatica 9810916980",
            "url": source_url,
            "snippet": "Ficha tecnica da peca.",
            "provider": "jina_duckduckgo",
        }]

        class Response:
            status_code = 200
            text = (
                "Codigo 9810916980: carcaca da valvula termostatica THP com dois engates rapidos "
                "para as mangueiras do sistema de arrefecimento."
            )

            @staticmethod
            def raise_for_status():
                return None

        with patch.object(agent, "_ia_agent_perguntas_buscar_web_publica", return_value=search_result), patch.object(
            agent, "_ia_agent_perguntas_anuncios_ml_autenticado", return_value=[]
        ), patch.object(
            agent, "_ia_agent_perguntas_anuncios_publicos_ml", return_value=[]
        ), patch.object(
            agent, "_perguntas_ia_v2_host_resolve_somente_publico", return_value=True
        ), patch.object(
            agent.requests, "get", return_value=Response()
        ) as source_read:
            context = agent._ia_agent_perguntas_contexto_web("cliente", "Loja", [{
                "type": "product_specification_by_code",
                "query": '"9810916980" Carcaca Valvula Termostatica engate rapido abracadeira',
            }])

        self.assertEqual(source_read.call_count, 1)
        self.assertIn("Leitura tecnica da fonte", context)
        self.assertIn("dois engates rapidos", context)
        self.assertIn(source_url, context)

    def test_official_manufacturer_manual_is_ranked_above_manual_mirror(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import sources as agent

        official = {
            "title": "PDF Rider's manual R1300GS - BMW Motorrad",
            "url": "https://manuals.bmw-motorrad.com/manuals/r1300gs.pdf",
        }
        mirror = {
            "title": "BMW R1300GS manual PDF download",
            "url": "https://www.manualslib.com/manual/r1300gs.html",
        }

        self.assertGreater(
            agent._perguntas_ia_v2_prioridade_fonte_web(official, official["url"])[0],
            agent._perguntas_ia_v2_prioridade_fonte_web(mirror, mirror["url"])[0],
        )

    def test_http_403_on_first_official_source_uses_next_source(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import sources as agent

        results = [
            {"title": "Manual oficial alternativo", "url": "https://manuals.fabricante.example/a-success.pdf"},
            {"title": "Manual oficial indisponivel", "url": "https://manuals.fabricante.example/z-fail.pdf"},
        ]

        class Response:
            def __init__(self, status_code, text=""):
                self.status_code = status_code
                self.text = text

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

        def source_response(url, **_kwargs):
            if "z-fail.pdf" in url:
                return Response(403)
            return Response(200, "The navigation preparation is suitable for Navigator IV and later.")

        with patch.object(agent, "_ia_agent_perguntas_buscar_web_publica", return_value=results), patch.object(
            agent, "_perguntas_ia_v2_host_resolve_somente_publico", return_value=True
        ), patch.object(
            agent.requests, "get", side_effect=source_response
        ) as source_read:
            context = agent._ia_agent_perguntas_contexto_web("cliente", "Loja", [{
                "type": "target_interface_official",
                "query": "R1300GS Navigator IV manual fabricante",
            }])

        self.assertEqual(source_read.call_count, 2)
        self.assertIn("a-success.pdf", context)
        self.assertIn("Navigator IV and later", context)

    def test_pdf_hyphenation_does_not_hide_interface_compatibility_sentence(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import sources as agent

        source = (
            "Si esta conectado el BMW Motorrad ConnectedRide Navigator, se puede cambiar el manejo.\n"
            "La preparacion de la na-vegacion es adecuada a partir del BMW Motorrad Navi-gator IV.\n"
        )
        excerpt = agent._perguntas_ia_v2_recortes_fonte_tecnica(
            source,
            "BMW R1300GS Navigator IV manual fabricante preparacion",
        )

        self.assertIn("Navigator IV", excerpt)
        self.assertIn("adecuada a partir", excerpt)
        self.assertTrue(agent._perguntas_ia_v2_recorte_confirma_interface(excerpt))

    def test_grounded_interface_facts_fill_paraphrased_model_evidence(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        official_url = "https://manuals.bmw-motorrad.com/manuals/r1300gs.pdf"
        listing_url = "https://produto.mercadolivre.com.br/MLB-1-_JM"
        grounding = agent._perguntas_ia_v2_grounding_coletar([
            {
                "function": "get_mercado_livre_listing",
                "result": {"found": True, "matches": [{
                    "description": (
                        "BMW codigos internos 4 5 6 " + ("atributo tecnico sem relacao " * 50)
                        + ". Compativel com base Garmin Navigator 4, 5 e 6."
                    ),
                    "permalink": listing_url,
                }]},
            },
            {
                "function": "web_search_question_context",
                "result": {
                    "found": True,
                    "context": (
                        "1. PDF Manual R1300GS - BMW Motorrad\n"
                        f"URL: {official_url}\n"
                        "Autoridade: public_web_reference\n"
                        "Resumo: La preparacion de la navegacion es adecuada a partir del BMW Motorrad Navigator IV."
                    ),
                },
            },
        ])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Base Garmin original BMW Navigator 4, 5 e 6",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Preparacao BMW adequada ao Navigator IV ou posterior",
            "decision": "conditional",
            "condition": "moto equipada com a preparacao original BMW Navigator",
            "confidence": 0.94,
            "evidence": {
                "product": [{"url": listing_url, "reference": "parafrase que nao aparece literalmente"}],
                "target_vehicle": [{"url": official_url, "reference": "traducao livre nao literal"}],
                "equivalence": [],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "conditional")
        self.assertTrue(analysis["evidence"]["product"][0]["grounded"])
        self.assertIn("Navigator", analysis["evidence"]["product"][0]["reference"])
        self.assertEqual(analysis["evidence"]["target_vehicle"][0]["authority"], "technical_web_source")
        self.assertEqual(analysis["evidence"]["equivalence"][0]["authority"], "derived")

    def test_public_question_compatibility_rules_block_photo_but_allow_listing_photo_reference(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import validation as agent

        self.assertTrue(agent._ia_agent_perguntas_resposta_pede_foto("Envie uma foto da base para confirmarmos."))
        self.assertTrue(agent._ia_agent_perguntas_resposta_pede_foto("Anexe o arquivo aqui."))
        self.assertTrue(agent._ia_agent_perguntas_resposta_pede_foto("Encaminhe o PDF do suporte."))
        self.assertFalse(agent._ia_agent_perguntas_resposta_pede_foto("As fotos do anuncio mostram a base incluida."))
        self.assertFalse(agent._ia_agent_perguntas_resposta_pede_foto(
            "As fotos do anuncio mostram a base; informe o ano e o codigo gravado."
        ))
        self.assertFalse(agent._ia_agent_perguntas_resposta_pede_foto(
            "As fotos do anuncio mostram a base e mande somente o modelo gravado."
        ))
        self.assertFalse(agent._ia_agent_perguntas_resposta_pede_foto(
            "Nao e necessario enviar foto; informe o codigo gravado."
        ))
        public_input = {
            "question": {"text": "Serve na R1300GS?"},
            "item": {"title": "Adaptador BMW R1300GS"},
            "intent": ai_classification(
                "Serve na R1300GS?",
                category="compatibility",
                use_web=True,
                target_item="BMW R1300GS",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="interface base conector",
            ),
        }
        post_sale_input = {
            "question": {"text": "O produto chegou quebrado"},
            "item": {"title": "Adaptador BMW"},
            "intent": ai_classification(
                "O produto chegou quebrado",
                category="post_sale",
                intent="pos_venda_defeito",
                required_evidence="historico do pedido e mensagem do comprador",
            ),
        }
        self.assertIn(
            "pediu anexo/arquivo em pergunta publica",
            agent._ia_agent_perguntas_violacoes_resposta(public_input, "Envie uma foto da base para confirmarmos."),
        )
        self.assertNotIn(
            "pediu anexo/arquivo em pergunta publica",
            agent._ia_agent_perguntas_violacoes_resposta(post_sale_input, "Envie uma foto do problema para verificarmos."),
        )

    def test_compatibility_model_decision_is_not_downgraded_without_server_claims(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import compatibility as agent

        for decision in ("yes", "no", "conditional"):
            with self.subTest(decision=decision):
                analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
                    "decision": decision,
                    "condition": "apenas na versao indicada" if decision == "conditional" else "",
                    "confidence": 0.95,
                    "reason": "model_factual_decision",
                    "evidence": {},
                })
                self.assertEqual(analysis["decision"], decision)
                self.assertEqual(analysis["confidence"], 0.95)
                self.assertEqual(analysis["reason"], "model_factual_decision")

    def test_compatibility_analysis_rejects_uncollected_url_and_filters_sources(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        official_url = "https://manuals.bmw-motorrad.com/manual.pdf?download=1#page=250"
        grounding = agent._perguntas_ia_v2_grounding_coletar([
            {
                "function": "get_mercado_livre_listing",
                "result": {"found": True, "matches": [{"description": "Base Navigator 4, 5 e 6"}]},
            },
            {
                "function": "web_search_question_context",
                "result": {
                    "found": True,
                    "context": (
                        "Manual oficial: preparacao adequada ao Navigator IV e posteriores\n"
                        f"URL: {official_url}"
                    ),
                },
            },
        ])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV e posteriores",
            "decision": "yes",
            "confidence": 0.96,
            "sources": [official_url, "https://inventada.example/manual"],
            "evidence": {
                "product": [{"reference": "Base Navigator IV/V/VI"}],
                "target_vehicle": [{
                    "url": "https://inventada.example/manual",
                    "reference": "preparacao adequada ao Navigator IV e posteriores",
                }],
                "equivalence": [{"reference": "Navigator IV"}],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "yes")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])
        self.assertEqual(analysis["sources"], ["https://manuals.bmw-motorrad.com/manual.pdf"])
        self.assertNotIn("https://inventada.example/manual", analysis["sources"])

    def test_compatibility_target_evidence_cannot_be_marketplace_only(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        ml_url = "https://produto.mercadolivre.com.br/MLB-123"
        grounding = agent._perguntas_ia_v2_grounding_coletar([
            {
                "function": "get_mercado_livre_listing",
                "result": {"found": True, "matches": [{"description": "Base Navigator IV/V/VI"}]},
            },
            {
                "function": "web_search_question_context",
                "result": {
                    "found": True,
                    "context": f"R1300GS usa Navigator IV\nURL: {ml_url}",
                },
            },
        ])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV",
            "decision": "conditional",
            "condition": "base Navigator IV",
            "confidence": 0.90,
            "evidence": {
                "product": [{"reference": "Base Navigator IV/V/VI"}],
                "target_vehicle": [{"url": ml_url, "reference": "R1300GS usa Navigator IV"}],
                "equivalence": [{"reference": "Navigator IV"}],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "conditional")
        self.assertEqual(analysis["evidence"]["target_vehicle"][0]["authority"], "marketplace_hint")

    def test_web_grounding_does_not_move_marketplace_claim_to_official_url(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        official = "https://manuals.bmw-motorrad.com/manual-r1300.pdf"
        marketplace = "https://produto.mercadolivre.com.br/MLB-123"
        grounding = agent._perguntas_ia_v2_grounding_coletar([{
            "function": "web_search_question_context",
            "result": {
                "found": True,
                "context": (
                    "1. Manual oficial BMW R1300GS\n"
                    f"URL: {official}\n"
                    "Resumo: orientacoes gerais de navegacao.\n"
                    "2. Anuncio de vendedor\n"
                    f"URL: {marketplace}\n"
                    "Resumo: R1300GS usa Navigator IV e posteriores."
                ),
            },
        }])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Navigator IV",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV e posteriores",
            "decision": "conditional",
            "condition": "base Navigator IV",
            "evidence": {
                "product": [{"reference": "Navigator IV"}],
                "target_vehicle": [{
                    "url": official,
                    "reference": "R1300GS usa Navigator IV e posteriores",
                }],
                "equivalence": [{"reference": "Navigator IV"}],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "conditional")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])

    def test_grounding_keeps_same_url_entries_separate_by_evidence_group(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        url = "https://fabricante.example/navigator"
        grounding = agent._perguntas_ia_v2_grounding_coletar([
            {
                "function": "web_search_product_identity",
                "result": {"found": True, "context": f"Produto usa base Navigator IV/V/VI\nURL: {url}"},
            },
            {
                "function": "web_search_question_context",
                "result": {"found": True, "context": f"R1300GS aceita Navigator IV e posteriores\nURL: {url}"},
            },
        ])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV e posteriores",
            "decision": "conditional",
            "condition": "preparacao Navigator original",
            "evidence": {
                "product": [{"url": url, "reference": "Produto usa base Navigator IV/V/VI"}],
                "target_vehicle": [{"url": url, "reference": "R1300GS aceita Navigator IV e posteriores"}],
                "equivalence": [{"authority": "derived", "reference": "mesma interface"}],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "conditional")
        self.assertEqual(analysis["evidence"]["product"][0]["reference"], "Produto usa base Navigator IV/V/VI")
        self.assertEqual(analysis["evidence"]["target_vehicle"][0]["reference"], "R1300GS aceita Navigator IV e posteriores")

    def test_compatibility_requires_explicit_equivalence_and_rejects_self_attested_incompatibility(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import compatibility as agent

        base = {
            "product_interface": "Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV e posteriores",
            "confidence": 0.90,
            "evidence": {
                "product": [{"authority": "internal_listing", "reference": "Navigator IV/V/VI"}],
                "target_vehicle": [{"authority": "official", "reference": "Navigator IV e posteriores"}],
                "equivalence": [],
            },
        }
        positive = agent._perguntas_ia_v2_compatibilidade_normalizar({**base, "decision": "yes"})
        negative = agent._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "coroa com geometria simetrica",
            "target_vehicle": "Shimano FC-MT510-1",
            "target_interface": "fixacao com geometria assimetrica",
            "decision": "no",
            "comparison_attributes": [{
                "attribute": "fixation_geometry",
                "product_value": "simetrica",
                "target_value": "assimetrica",
                "result": "conflict",
                "decisive": True,
            }],
            "evidence": {
                "product": [{
                    "authority": "internal_listing",
                    "reference": "geometria simetrica",
                    "grounded": True,
                }],
                "target_vehicle": [{
                    "authority": "official_document",
                    "reference": "geometria assimetrica",
                    "grounded": True,
                }],
                "equivalence": [],
            },
        })

        self.assertEqual(positive["decision"], "yes")
        self.assertEqual(negative["decision"], "no")
        self.assertFalse(any(
            item.get("source_type") == "derived_incompatibility_from_grounded_evidence"
            for item in negative["evidence"]["equivalence"]
        ))

    def test_compatibility_can_derive_equivalence_only_from_shared_grounded_interface_terms(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import evidence as agent

        official = "https://manuals.bmw-motorrad.com/manual-r1300.pdf"
        grounding = agent._perguntas_ia_v2_grounding_coletar([
            {
                "function": "get_mercado_livre_listing",
                "result": {"found": True, "matches": [{"description": "Base Navigator 4, 5 e 6"}]},
            },
            {
                "function": "web_search_question_context",
                "result": {
                    "found": True,
                    "context": (
                        "Manual oficial: preparacao adequada ao Navigator IV e posteriores\n"
                        f"URL: {official}"
                    ),
                },
            },
        ])
        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "Navigator IV e posteriores",
            "decision": "conditional",
            "condition": "moto equipada com a preparacao original",
            "confidence": 0.91,
            "evidence": {
                "product": [{"reference": "Base Navigator IV/V/VI"}],
                "target_vehicle": [{
                    "url": official,
                    "reference": "preparacao adequada ao Navigator IV e posteriores",
                }],
                "equivalence": [{"authority": "derived", "reference": "mesma interface"}],
            },
        }, grounding=grounding)

        self.assertEqual(analysis["decision"], "conditional")
        derived = analysis["evidence"]["equivalence"][0]
        self.assertEqual(derived["source_type"], "derived_from_grounded_evidence")
        self.assertEqual(derived["derived_from"]["shared_terms"], ["4", "navigator"])

    def test_compatibility_http_403_or_one_sided_evidence_is_not_a_match(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import compatibility as agent

        analysis = agent_compatibility._perguntas_ia_v2_compatibilidade_normalizar({
            "product_interface": "base BMW Navigator IV/V/VI",
            "target_vehicle": "BMW R1300GS",
            "target_interface": "preparacao BMW Navigator IV ou posterior",
            "decision": "conditional",
            "condition": "moto equipada com a base original BMW",
            "confidence": 0.92,
            "evidence": {
                "product": [{"authority": "internal_listing", "reference": "Navigator IV/V/VI"}],
                "target_vehicle": [{"status": "error", "http_status": 403, "reference": "HTTP 403"}],
                "equivalence": [{"authority": "derived", "reference": "interfaces aparentemente iguais"}],
            },
        })

        self.assertEqual(analysis["decision"], "conditional")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])

    def test_insufficient_analysis_keeps_draft_blocked_for_human_review(self):
        import backend_api  # noqa: F401
        from backend.modules.perguntas_pos_venda.ai import clients as agent

        agent_input = {
            "question": {"text": "Serve na minha moto?"},
            "item": {"id": "MLB1", "title": "Adaptador automotivo"},
            "intent": ai_classification(
                "Serve na minha moto?",
                category="compatibility",
                use_web=True,
                target_item="minha moto",
                target_type="vehicle",
                compatibility_profile="vehicle_fitment",
                technical_focus="aplicacao ano versao e interface",
                missing_fields=("ano", "versao"),
                decisive_fields=("ano", "versao"),
                required_evidence="aplicacao comprovada e equivalencia de interface",
            ),
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        public_response = {
            "answer": "Esse adaptador e compativel com a moto informada.",
            "confidence": 0.4,
            "requires_human_review": False,
            "reason": "unsupported_positive",
            "compatibility_analysis": {
                "decision": "insufficient",
                "missing_fields": ["ano", "versao"],
                "confidence": 0.4,
                "reason": "missing_listing_evidence",
            },
        }
        with patch.object(agent, "_ia_agent_perguntas_product_identity_web_tool", return_value=None), \
             patch.object(agent, "_ia_tool_get_product_data", return_value=None), \
             patch.object(agent, "marketplace_listing_query", return_value=None), \
             patch.object(agent, "_ia_tool_get_bling_product", return_value=None), \
             patch.object(agent, "_perguntas_ia_memoria_bloco_prompt", return_value=""), \
             patch.object(agent, "_ia_agent_perguntas_web_tool", return_value=None) as web_call, \
             patch.object(
                 agent,
                 "_ia_agent_perguntas_chamar_modelo",
                 side_effect=_v16_model_responder(public_response),
             ) as model_call:
            result = client.generate("prompt", {
                "category": "compatibility",
                "question_text": "Serve na minha moto?",
                "item_id": "MLB1",
                "listing_title": "Adaptador automotivo",
            })

        self.assertEqual(result.answer, "Esse adaptador e compativel com a moto informada.")
        self.assertEqual(result.confidence, 0.4)
        self.assertFalse(result.requires_human_review)
        self.assertEqual(web_call.call_count, 2)
        self.assertEqual(model_call.call_count, 7)
        self.assertEqual(
            _v16_model_stages(model_call),
            [
                "technical_question_plan",
                "technical_evidence_graph",
                "technical_resolution_round_1",
                "technical_evidence_graph",
                "technical_resolution_final",
                "compatibility_public_answer",
                "factual_critic",
            ],
        )


if __name__ == "__main__":
    unittest.main()
