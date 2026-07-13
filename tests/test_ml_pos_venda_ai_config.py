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
    def test_backend_has_dedicated_pos_venda_config(self):
        source = backend_text()
        self.assertIn('"ia_modelo_pos_venda": "vertex:gemini-2.5-flash"', source)
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
        self.assertIn("config.get(\"solicitar_aprovacao\") or _pos_venda_ia_v2_exigir_aprovacao()", body)
        self.assertIn('"aprovacao_obrigatoria_ia": _pos_venda_ia_v2_exigir_aprovacao()', body)
        self.assertIn('"ia_modo": _ia_modo_pos_venda_configurado()', body)
        self.assertIn("_ml_pos_venda_executar_pipeline_ia", body)
        self.assertIn("not resultado_ia.get(\"pode_enviar_automaticamente\")", body)
        self.assertIn('"ia_pipeline": pipeline_resumo', body)

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

    def test_public_questions_v2_uses_web_only_after_listing_is_insufficient(self):
        source = backend_text()
        client_body = function_body(source, "_perguntas_ia_v2_query_pesquisa")
        self.assertIn("question_text", client_body)
        self.assertIn("listing_link", client_body)
        self.assertIn("listing_title", client_body)
        self.assertIn("_favoritos_ml_url_item_id", client_body)

        vertex_client = re.search(r"^class _PerguntasVertexGeminiV2Client:.*?^def _perguntas_ia_v2_prompt", source, re.M | re.S)
        self.assertIsNotNone(vertex_client)
        body = vertex_client.group(0)
        self.assertIn('stage="listing_only"', body)
        self.assertIn("_perguntas_ia_v2_resposta_precisa_web", body)
        self.assertIn("_ia_agent_perguntas_web_tool", body)
        self.assertIn('stage="external_fallback"', body)
        self.assertIn('"desativar_busca_web_chat": True', body)
        self.assertLess(body.index('stage="listing_only"'), body.index("_ia_agent_perguntas_web_tool"))
        self.assertLess(body.index("_ia_agent_perguntas_web_tool"), body.index('stage="external_fallback"'))

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

    def test_public_questions_v2_searches_web_when_listing_lacks_answer(self):
        import backend_api  # noqa: F401 - configura os globals do runtime modular
        from backend.services import perguntas_pos_venda_agent as agent

        first = '{"answer":"Nao consta no anuncio.","confidence":0.45,"requires_human_review":true,"reason":"missing_listing_evidence"}'
        second = '{"answer":"Segundo a aplicacao encontrada, serve no modelo informado.","confidence":0.84,"requires_human_review":true,"reason":"external_evidence_review"}'
        web = {
            "function": "web_search_question_context",
            "arguments": {"query": "Produto modelo compatibilidade"},
            "result": {
                "found": True,
                "context": "Manual do fabricante\nURL: https://fabricante.example/manual",
                "read_only": True,
            },
        }
        client = agent._PerguntasVertexGeminiV2Client("cliente", "Loja", "codex:gpt-5.5", {"question": {"text": "Serve no modelo?"}})
        with patch.object(agent, "_ia_agent_perguntas_chamar_modelo", side_effect=[(first, "codex:gpt-5.5"), (second, "codex:gpt-5.5")]) as model_call, patch.object(
            agent,
            "_ia_agent_perguntas_web_tool",
            return_value=web,
        ) as web_call:
            result = client.generate("prompt com historico e anuncio", {
                "category": "compatibility",
                "question_text": "Serve no modelo?",
                "item_id": "MLB1",
                "listing_title": "Produto",
                "history_count": 1,
            })

        self.assertIn("serve no modelo", result.answer)
        self.assertEqual(model_call.call_count, 2)
        web_call.assert_called_once()
        self.assertEqual(client.context_pipeline[-1]["status"], "completed")
        self.assertEqual(client.context_pipeline[-1]["source_count"], 1)


if __name__ == "__main__":
    unittest.main()
