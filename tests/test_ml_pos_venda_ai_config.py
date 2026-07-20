import json
import hashlib
import os
import re
import unicodedata
import unittest
from unittest.mock import patch
from pathlib import Path


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


class MlPosVendaAIConfigTests(unittest.TestCase):
    def test_compatibility_intent_cannot_disable_required_web_research(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        context = {
            "intencao_atendimento": {
                "fluxo": "perguntas_anuncio",
                "intencao": "compatibilidade",
                "usar_busca_web": False,
            }
        }
        with patch.object(agent, "_ia_treinamento_ppv_bloco_prompt", return_value=""):
            payload = agent._perguntas_ia_agent_input(
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
        self.assertEqual(payload["app_guidance_source"], "jk_ppv_response_policy_v1")
        self.assertEqual(payload["context_collection_pipeline"][4]["name"], "context_hub_sku_reference")
        self.assertIn("dados de referencia nao confiaveis", payload["context_collection_pipeline"][4]["description"])
        self.assertIn("compatibilidade, aplicacao, caracteristicas e funcoes", payload["context_collection_pipeline"][6]["description"])
        self.assertIn("fabricante, manuais, catalogos OEM", payload["context_collection_pipeline"][6]["description"])

    def test_legacy_training_is_hashed_but_not_injected_by_default(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        canary = "RESPOSTA-IDEAL-ANTIGA-NAO-DEVE-ENTRAR"
        context = {
            "intencao_atendimento": {
                "fluxo": "perguntas_anuncio",
                "intencao": "duvida_produto",
            }
        }
        with patch.dict(os.environ, {"IA_PPV_LEGACY_GUIDANCE_FALLBACK_ENABLED": ""}), \
             patch.object(agent, "_ia_treinamento_ppv_bloco_prompt", return_value=canary):
            payload = agent._perguntas_ia_agent_input(
                "000002",
                "JK Pecas",
                {"id": "Q1", "text": "Qual o conector?"},
                {"id": "MLB1", "seller_sku": "001", "title": "Adaptador"},
                context,
                "prompt",
            )
            prompt = agent._perguntas_ia_v2_prompt("000002", payload)

        self.assertNotIn(canary, payload["app_guidance"])
        self.assertNotIn(canary, prompt)
        self.assertTrue(payload["legacy_guidance_available"])
        self.assertEqual(payload["legacy_guidance_hash"], hashlib.sha256(canary.encode()).hexdigest())
        self.assertFalse(payload["legacy_fallback_enabled"])
        self.assertFalse(payload["legacy_fallback_used"])

    def test_legacy_fallback_requires_opt_in_and_non_security_empty_hub(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        canary = "REGRA-LEGADA-AUDITADA"
        payload = {
            "store": "JK Pecas",
            "context": {},
            "intent": {"fluxo": "perguntas_anuncio"},
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
             patch.object(agent, "_ia_treinamento_ppv_bloco_prompt", return_value=canary):
            self.assertEqual(
                agent._perguntas_ia_legacy_guidance_fallback("000002", payload, empty_hub),
                canary,
            )
            self.assertEqual(
                agent._perguntas_ia_legacy_guidance_fallback("000002", payload, unsafe_hub),
                "",
            )
            self.assertEqual(
                agent._perguntas_ia_legacy_guidance_fallback("000002", payload, nonempty_legacy_hub),
                "",
            )

    def test_technical_product_question_cannot_disable_web_research(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        context = {
            "intencao_atendimento": {
                "fluxo": "perguntas_anuncio",
                "intencao": "duvida_produto",
                "usar_busca_web": False,
            }
        }
        with patch.object(agent, "_ia_treinamento_ppv_bloco_prompt", return_value=""):
            payload = agent._perguntas_ia_agent_input(
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

        self.assertTrue(payload["use_web_search"])
        self.assertTrue(payload["web_search_required"])
        self.assertIn("web_search_product_identity", payload["allowed_tools"])
        self.assertIn("web_search_question_context", payload["allowed_tools"])

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
        self.assertIn("_pos_venda_ia_resposta_final_loja", body)
        self.assertIn("Finalize exatamente com", body)
        self.assertIn("sem se apresentar como assistente", body)
        self.assertIn("contexto_pipeline", body)

    def test_pos_venda_automation_requires_review_by_default(self):
        body = function_body(backend_text(), "ml_pos_venda_automacao_poll")
        approval_builder = function_body(backend_text(), "_customer_reply_post_sale_approval")
        self.assertIn("_customer_reply_requires_approval()", body)
        self.assertIn("_customer_reply_post_sale_approval", body)
        self.assertIn('"aprovacao_obrigatoria_ia": _pos_venda_ia_v2_exigir_aprovacao()', approval_builder)
        self.assertIn('"ia_modo": _ia_modo_pos_venda_configurado()', approval_builder)
        self.assertIn("_ml_pos_venda_executar_pipeline_ia", body)
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

    def test_pos_venda_all_accounts_interval_and_ai_scope_are_exposed(self):
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
                self.assertIn("Padrao para todas as contas", html)
                self.assertIn("lojaEscopoTreinamento", html)
                self.assertIn("config-lote", html)
                self.assertIn("Todas as contas conectadas", html)

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
        body = function_body(backend_text(), "_ia_agent_perguntas_violacoes_resposta")
        self.assertIn("not pergunta_compatibilidade", body)
        self.assertIn("nao respondeu ao modelo/codigo perguntado", body)

    def test_public_question_chassis_is_compatibility_and_not_asked_again(self):
        source = backend_text()
        intent_body = function_body(source, "_perguntas_ia_intencao_heuristica")
        validator_body = function_body(source, "_ia_agent_perguntas_violacoes_resposta")
        v2_body = function_body(source, "_perguntas_ia_v2_gerar_resposta")
        self.assertIn('"chassi"', intent_body)
        self.assertIn('"chassi"', validator_body)
        self.assertIn("_ia_agent_perguntas_resposta_pede_chassi(texto)", validator_body)
        self.assertIn("_perguntas_ia_v2_corrigir_resposta_bloqueada", v2_body)
        self.assertIn("_perguntas_ia_v2_resposta_segura_compatibilidade", v2_body)

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
        source = backend_text()
        vertex_client = re.search(r"^class _PerguntasVertexGeminiV2Client:.*?^def _perguntas_ia_v2_prompt", source, re.M | re.S)
        self.assertIsNotNone(vertex_client)
        body = vertex_client.group(0)
        ordem = [
            "_ia_tool_get_mercado_livre_listing",
            "_ia_tool_get_product_data",
            "_ia_tool_get_bling_product",
            "_perguntas_ia_memoria_bloco_prompt",
            "_ia_agent_perguntas_product_identity_web_tool",
            "_ia_agent_perguntas_web_tool",
            'stage="compatibility_final"',
        ]
        for trecho in ordem:
            self.assertIn(trecho, body)
        for atual, seguinte in zip(ordem, ordem[1:]):
            self.assertLess(body.index(atual), body.index(seguinte))
        self.assertIn("self.compatibility_analysis", body)
        self.assertIn('"desativar_busca_web_chat": True', body)

    def test_r1300gs_queries_are_short_and_separate_vehicle_from_listing_ids(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        agent_input = {
            "question": {"text": "Serve no suporte gps da 1300gs?"},
            "item": {
                "id": "MLB2785882411",
                "seller_sku": "241-1",
                "title": "Adaptador Smartphone Suporte GPS BMW R1250 R1200 F850 GS ADV Preto",
            },
        }
        queries = agent._ia_agent_perguntas_queries_web(agent_input, [])

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
        from backend.services import perguntas_pos_venda_agent as agent

        agent_input = {
            "question": {"text": "Serve no suporte gps da 1300gs?"},
            "item": {
                "id": "MLB2785882411",
                "seller_sku": "241-1",
                "title": "Adaptador Smartphone Suporte GPS BMW R1250 R1200 F850 GS ADV Preto",
            },
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

        queries = agent._ia_agent_perguntas_queries_web(agent_input, collected)
        identity_queries = agent._ia_agent_perguntas_queries_identificacao_produto(agent_input, collected)

        self.assertTrue(all("Navigator" in item["query"] for item in queries))
        self.assertIn("Navigator", identity_queries[0]["query"])
        self.assertIn("BMW R1300GS", queries[0]["query"])
        for item in [*queries, *identity_queries]:
            self.assertNotIn("MLB2785882411", item["query"])
            self.assertNotIn("241-1", item["query"])
            self.assertLessEqual(len(item["query"]), 260)

    def test_sku_307_k_builds_code_and_attribute_queries_instead_of_treating_question_as_target(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
            "intent": {"fluxo": "perguntas_anuncio", "intencao": "duvida_produto"},
        }

        queries = agent._ia_agent_perguntas_queries_web(agent_input, [])

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
        self.assertNotIn('"', agent._ia_agent_perguntas_relaxar_query_web(queries[0]["query"]))
        self.assertIn("11537534521", agent._ia_agent_perguntas_query_ml_publica(queries[0]["query"]))

    def test_public_questions_v2_skips_web_when_listing_answer_is_sufficient(self):
        import backend_api  # noqa: F401 - configura os globals do runtime modular
        from backend.services import perguntas_pos_venda_agent as agent

        answer = '{"answer":"Acompanha cabo USB.","confidence":0.96,"requires_human_review":false,"reason":"listing_evidence"}'
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {"question": {"text": "Acompanha cabo?"}})
        with patch.object(agent, "_ia_agent_perguntas_chamar_modelo", return_value=(answer, "codex:gpt-5.5")) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            side_effect=AssertionError("internet must not be called"),
        ):
            result = client.generate("prompt com historico e anuncio", {
                "category": "product_feature",
                "question_text": "Acompanha cabo?",
                "item_id": "MLB1",
                "listing_title": "Produto com cabo USB",
                "history_count": 2,
            })

        self.assertEqual(result.answer, "Acompanha cabo USB.")
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(client.context_pipeline[-1]["status"], "skipped")

    def test_public_question_missing_technical_attribute_uses_external_research_fallback(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        first = json.dumps({
            "answer": "O anuncio nao informa objetivamente se a conexao e por engate rapido ou abracadeira.",
            "confidence": 0.95,
            "requires_human_review": False,
            "reason": "listing_evidence",
        })
        second = json.dumps({
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
            },
        }
        agent_input = {
            "question": {"text": "Essa carcaca e de engate rapido ou para abracadeira?"},
            "item": {
                "id": "MLB4129425225",
                "seller_sku": "307-K",
                "title": "Carcaca Valvula Termostatica THP 1.6",
                "description": "Codigo 9810916980",
            },
            "intent": {"fluxo": "perguntas_anuncio", "intencao": "duvida_produto"},
            "allowed_tools": ["web_search_question_context"],
            "use_web_search": True,
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        with patch.object(agent, "_ia_agent_perguntas_chamar_modelo", side_effect=[
            (first, "codex:gpt-5.5"),
            (second, "codex:gpt-5.5"),
        ]) as model_call, patch.object(
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

        self.assertEqual(model_call.call_count, 2)
        web_call.assert_called_once()
        self.assertIn("engate rapido", result.answer.lower())
        self.assertNotIn("anuncio nao informa", result.answer.lower())
        self.assertEqual(client.context_pipeline[-1]["status"], "completed")

    def test_public_questions_v2_builds_structured_compatibility_analysis(self):
        import backend_api  # noqa: F401 - configura os globals do runtime modular
        from backend.services import perguntas_pos_venda_agent as agent

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
                    "product": [{"authority": "internal_listing", "reference": "Navigator IV/V/VI"}],
                    "target_vehicle": [{
                        "authority": "official",
                        "url": "https://manuals.bmw-motorrad.com/manuals/BA-Extern/IN/BA-INTERNET-COM/PDF/R_0M23_RM_0223_07.pdf",
                        "reference": "preparacao de navegacao adequada ao BMW Motorrad Navigator IV e posteriores",
                    }],
                    "equivalence": [{"authority": "official", "reference": "Navigator IV"}],
                },
                "confidence": 0.91,
                "reason": "same_navigation_interface",
            },
        })
        identity = {
            "function": "web_search_product_identity",
            "arguments": {"queries": [{"type": "product_interface_identity", "query": "adaptador BMW interface base"}]},
            "result": {"found": True, "context": "Ficha tecnica: encaixa na base Navigator IV/V/VI\nURL: https://fabricante.example/produto"},
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
                "read_only": True,
            },
        }
        agent_input = {
            "question": {"text": "Serve no suporte GPS da R1300GS?"},
            "item": {"id": "MLB1", "title": "Adaptador smartphone BMW Navigator", "description": "Compativel com base Navigator IV, V e VI"},
            "intent": {"fluxo": "perguntas_anuncio", "intencao": "compatibilidade"},
            "app_guidance": "Compare a base antes de responder.",
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        cadastro = {"function": "get_product_data", "result": {"found": True, "matches": [{"sku": "241-1", "nome": "Adaptador BMW"}]}}
        anuncio = {"function": "get_mercado_livre_listing", "result": {"found": True, "matches": [{"id": "MLB1", "description": "Navigator IV/V/VI"}]}}
        bling = {"function": "get_bling_product", "result": {"found": True, "matches": [{"nome": "Adaptador BMW"}]}}
        with patch.object(agent, "_ia_agent_perguntas_chamar_modelo", return_value=(answer, "codex:gpt-5.5")) as model_call, \
             patch.object(agent, "_ia_agent_perguntas_product_identity_web_tool", return_value=identity), \
             patch.object(agent, "_ia_tool_get_product_data", return_value=cadastro), \
             patch.object(agent, "_ia_tool_get_mercado_livre_listing", return_value=anuncio), \
             patch.object(agent, "_ia_tool_get_bling_product", return_value=bling), \
             patch.object(agent, "_perguntas_ia_context_hub_tool", return_value={
                 "function": "context_hub_search",
                 "result": {"found": False, "results": [], "count": 0, "read_only": True},
             }), \
             patch.object(agent, "_perguntas_ia_memoria_bloco_prompt", return_value="Base Navigator IV/V/VI confirmada"), \
             patch.object(agent, "_ia_agent_perguntas_web_tool", return_value=web_final):
            result = client.generate("prompt com historico e anuncio", {
                "category": "compatibility",
                "question_text": "Serve no suporte GPS da R1300GS?",
                "item_id": "MLB1",
                "listing_title": "Adaptador smartphone BMW Navigator",
                "history_count": 1,
            })

        self.assertTrue(result.answer.startswith("Esse adaptador é compatível"))
        self.assertIn("Ele encaixa nessa base e não acompanha nem substitui o suporte original.", result.answer)
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(client.compatibility_analysis["decision"], "conditional")
        self.assertEqual(client.compatibility_analysis["target_vehicle"], "BMW R1300GS")
        self.assertEqual(len(client.compatibility_analysis["evidence"]["equivalence"]), 1)
        self.assertIn(
            "https://manuals.bmw-motorrad.com/manuals/BA-Extern/IN/BA-INTERNET-COM/PDF/R_0M23_RM_0223_07.pdf",
            client.compatibility_analysis["sources"],
        )
        self.assertEqual(client.context_pipeline[-1]["status"], "completed")
        self.assertEqual(
            [step["name"] for step in client.context_pipeline],
            [
                "buyer_question_history_and_listing_snapshot", "mercado_livre_api_listing",
                "internal_product_registry", "bling_product", "context_hub_sku_reference",
                "approved_sku_memory_and_legacy_rules",
                "product_interface_research",
                "official_technical_research", "compatibility_decision_and_answer",
            ],
        )
        payload_modelo = model_call.call_args.args[1]
        self.assertEqual(payload_modelo.tool_results[0]["function"], "web_search_question_context")
        self.assertIn("PESQUISA_TECNICA_PRIORIZADA", payload_modelo.message)
        self.assertIn("copie somente fatos e URLs que aparecam no contexto coletado", payload_modelo.message)
        self.assertIn("repita somente URLs realmente coletadas", payload_modelo.message)

    def test_official_technical_result_is_enriched_with_relevant_source_excerpt(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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

        with patch.object(agent, "_ia_web_buscar_cached", return_value=search_result), patch.object(
            agent.requests, "get", return_value=Response()
        ) as source_read:
            context = agent._ia_agent_perguntas_contexto_web("cliente", "Loja", [{
                "type": "target_interface_official",
                "query": "BMW R1300GS Navigator 4 5 e 6 manual fabricante preparacao",
            }])

        self.assertIn("Leitura tecnica da fonte", context)
        self.assertIn("Navigator IV and later", context)
        self.assertIn(official_url, context)
        self.assertTrue(source_read.call_args.args[0].startswith("https://r.jina.ai/http://https://"))

    def test_product_feature_result_reads_technical_page_for_exact_part_code(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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

        with patch.object(agent, "_ia_web_buscar_cached", return_value=search_result), patch.object(
            agent, "_ia_agent_perguntas_anuncios_ml_autenticado", return_value=[]
        ), patch.object(
            agent, "_ia_agent_perguntas_anuncios_publicos_ml", return_value=[]
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
        from backend.services import perguntas_pos_venda_agent as agent

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
        from backend.services import perguntas_pos_venda_agent as agent

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

        with patch.object(agent, "_ia_web_buscar_cached", return_value=results), patch.object(
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
        from backend.services import perguntas_pos_venda_agent as agent

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
        from backend.services import perguntas_pos_venda_agent as agent

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
                        "Resumo: La preparacion de la navegacion es adecuada a partir del BMW Motorrad Navigator IV."
                    ),
                },
            },
        ])
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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
        self.assertEqual(analysis["evidence"]["target_vehicle"][0]["authority"], "official_document")
        self.assertEqual(analysis["evidence"]["equivalence"][0]["authority"], "derived")

    def test_public_question_compatibility_rules_block_photo_but_allow_listing_photo_reference(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
            "intent": {"fluxo": "perguntas_anuncio", "intencao": "compatibilidade"},
        }
        post_sale_input = {
            "question": {"text": "O produto chegou quebrado"},
            "item": {"title": "Adaptador BMW"},
            "intent": {"fluxo": "pos_venda", "intencao": "pos_venda"},
        }
        self.assertIn(
            "pediu anexo/arquivo em pergunta publica",
            agent._ia_agent_perguntas_violacoes_resposta(public_input, "Envie uma foto da base para confirmarmos."),
        )
        self.assertNotIn(
            "pediu anexo/arquivo em pergunta publica",
            agent._ia_agent_perguntas_violacoes_resposta(post_sale_input, "Envie uma foto do problema para verificarmos."),
        )

    def test_compatibility_decision_without_evidence_becomes_insufficient(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
            "decision": "yes",
            "confidence": 0.95,
            "evidence": {},
        })
        self.assertEqual(analysis["decision"], "insufficient")
        self.assertLessEqual(analysis["confidence"], 0.49)
        self.assertIn("product_evidence", analysis["missing_fields"])
        self.assertIn("target_vehicle_evidence", analysis["missing_fields"])

    def test_compatibility_analysis_rejects_uncollected_url_and_filters_sources(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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

        self.assertEqual(analysis["decision"], "insufficient")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])
        self.assertEqual(analysis["sources"], ["https://manuals.bmw-motorrad.com/manual.pdf"])
        self.assertNotIn("https://inventada.example/manual", analysis["sources"])

    def test_compatibility_target_evidence_cannot_be_marketplace_only(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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

        self.assertEqual(analysis["decision"], "insufficient")
        self.assertEqual(analysis["evidence"]["target_vehicle"][0]["authority"], "marketplace_hint")
        self.assertIn("non_marketplace_target_evidence", analysis["missing_fields"])

    def test_web_grounding_does_not_move_marketplace_claim_to_official_url(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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

        self.assertEqual(analysis["decision"], "insufficient")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])

    def test_grounding_keeps_same_url_entries_separate_by_evidence_group(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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

    def test_compatibility_requires_explicit_equivalence_or_incompatibility_evidence(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
            **base,
            "decision": "no",
            "evidence": {**base["evidence"], "equivalence": [{"reference": "Navigator IV"}]},
        })

        self.assertEqual(positive["decision"], "insufficient")
        self.assertIn("explicit_equivalence_evidence", positive["missing_fields"])
        self.assertEqual(negative["decision"], "insufficient")
        self.assertIn("explicit_incompatibility_evidence", negative["missing_fields"])

    def test_compatibility_can_derive_equivalence_only_from_shared_grounded_interface_terms(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

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
        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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
        from backend.services import perguntas_pos_venda_agent as agent

        analysis = agent._perguntas_ia_v2_compatibilidade_normalizar({
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

        self.assertEqual(analysis["decision"], "insufficient")
        self.assertEqual(analysis["evidence"]["target_vehicle"], [])
        self.assertIn("target_vehicle_evidence", analysis["missing_fields"])

    def test_insufficient_analysis_replaces_positive_draft_with_textual_fallback(self):
        import backend_api  # noqa: F401
        from backend.services import perguntas_pos_venda_agent as agent

        agent_input = {
            "question": {"text": "Serve na minha moto?"},
            "item": {"id": "MLB1", "title": "Adaptador automotivo"},
            "intent": {"fluxo": "perguntas_anuncio", "intencao": "compatibilidade"},
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", agent_input)
        with patch.object(agent, "_ia_agent_perguntas_product_identity_web_tool", return_value=None), \
             patch.object(agent, "_ia_tool_get_product_data", return_value=None), \
             patch.object(agent, "_ia_tool_get_mercado_livre_listing", return_value=None), \
             patch.object(agent, "_ia_tool_get_bling_product", return_value=None), \
             patch.object(agent, "_perguntas_ia_memoria_bloco_prompt", return_value=""), \
             patch.object(agent, "_ia_agent_perguntas_web_tool", return_value=None), \
             patch.object(client, "_call_model", return_value=agent.AIAnswer(
                 answer="Esse adaptador e compativel com a moto informada.",
                 confidence=0.93,
                 requires_human_review=False,
                 reason="unsupported_positive",
             )):
            result = client.generate("prompt", {
                "category": "compatibility",
                "question_text": "Serve na minha moto?",
                "item_id": "MLB1",
                "listing_title": "Adaptador automotivo",
            })

        self.assertIn("informe o ano", result.answer.lower())
        self.assertNotIn("e compativel", result.answer.lower())
        self.assertTrue(result.requires_human_review)


if __name__ == "__main__":
    unittest.main()
