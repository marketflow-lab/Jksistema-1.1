import re
import unicodedata
import unittest
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

    def test_public_questions_v2_sends_link_and_question_to_web_search(self):
        source = backend_text()
        client_body = function_body(source, "_perguntas_ia_v2_query_pesquisa")
        self.assertIn("question_text", client_body)
        self.assertIn("listing_link", client_body)
        self.assertIn("_favoritos_ml_url_item_id", client_body)

        vertex_client = re.search(r"^class _PerguntasVertexGeminiV2Client:.*?^def _perguntas_ia_v2_prompt", source, re.M | re.S)
        self.assertIsNotNone(vertex_client)
        body = vertex_client.group(0)
        self.assertIn('"forcar_busca_web_chat": not fluxo_pos_venda', body)
        self.assertIn('"web_search_required": not fluxo_pos_venda', body)
        self.assertIn('"web_search_query": web_search_query', body)
        self.assertIn('"ativar_google_search_grounding": not fluxo_pos_venda', body)
        self.assertNotIn('"desativar_busca_web_chat": True', body)

        vertex_call = function_body(source, "_chamar_vertex_ai_chat")
        self.assertIn("fluxo_perguntas_publicas_v2", vertex_call)
        self.assertIn('ctx_payload.get("web_search_query") or mensagem', vertex_call)
        self.assertIn('"novo_fluxo_perguntas_v2"', function_body(source, "_vertex_google_search_grounding_ativo"))


if __name__ == "__main__":
    unittest.main()
