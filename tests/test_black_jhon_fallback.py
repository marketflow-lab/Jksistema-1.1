import logging
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from starlette.requests import Request

from backend.schemas.ia import IAChatRequest
from backend.services import ia_endpoints
from backend.services.marketplace_tools import images as marketplace_images


class BlackJhonFallbackTest(unittest.TestCase):
    def test_fallback_read_only_skips_tools_and_image_generation(self):
        payload = IAChatRequest(
            message="Explique o resumo em texto.",
            page="dashboard",
            context={"fallback_read_only": True},
            history=[{"role": "assistant", "text": "historico sensivel"}],
            attachments=[],
            model="gpt-5.5",
            fallback_read_only=True,
        )
        request = Request({"type": "http", "method": "POST", "path": "/api/ia/chat", "headers": [], "client": ("127.0.0.1", 12345)})
        tools = Mock(side_effect=AssertionError("ferramentas nao podem rodar no fallback"))
        image = Mock(side_effect=AssertionError("imagem nao pode ser gerada no fallback"))
        provider_payloads = []

        def provider(exec_payload, _client_id):
            provider_payloads.append(exec_payload)
            return "Resposta segura de leitura."

        replacements = {
            "logger": logging.getLogger("test_black_jhon_fallback"),
            "_extrair_username_do_request": lambda _request: "",
            "_ia_chat_resumo_historico": lambda *_args, **_kwargs: "",
            "_ia_chat_executar_funcoes": tools,
            "_ia_modelo_chat_configurado": lambda: "gpt-5.5",
            "_usuario_pode_escolher_modelo_chat": lambda *_args: True,
            "_normalizar_ia_modelo_padrao": lambda value: value,
            "_modelo_eh_vertex_ai": lambda _value: False,
            "_modelo_eh_gemini_api": lambda _value: False,
            "_modelo_eh_codex": lambda _value: False,
            "_chamar_openai_responses": provider,
        }
        with ExitStack() as stack:
            for name, value in replacements.items():
                stack.enter_context(patch.object(ia_endpoints, name, value, create=True))
            stack.enter_context(patch.object(marketplace_images, "generate_response", image))
            result = ia_endpoints.ia_chat(payload, request, client_id="000002")

        tools.assert_not_called()
        image.assert_not_called()
        self.assertEqual(result["resposta"], "Resposta segura de leitura.")
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(len(provider_payloads), 1)
        self.assertIn("FALLBACK ESTRITAMENTE DE LEITURA", provider_payloads[0].message)
        self.assertIn("Nao execute nem alegue ter executado alteracoes", provider_payloads[0].message)
        self.assertTrue(payload.context.get("fallback_read_only"))


if __name__ == "__main__":
    unittest.main()
