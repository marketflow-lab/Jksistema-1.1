import json
import logging

from backend.schemas import IAChatRequest
from backend.services import ia  # noqa: F401 - configures the compatibility peers
from backend.services import ia_providers


class _OpenAIResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {"output_text": "resposta sintetica"}


def _capture_openai_request(monkeypatch, payload: IAChatRequest) -> dict:
    captured: dict = {}

    def post(*_args, **kwargs):
        captured.update(kwargs.get("json") or {})
        return _OpenAIResponse()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider tentou pre-carregar contexto automatico")

    monkeypatch.setattr(ia_providers, "logger", logging.getLogger("test_ia_provider_planned_context"))
    monkeypatch.setattr(ia_providers, "_ia_validar_provedor_ativo", lambda *_args: None)
    monkeypatch.setattr(ia_providers, "_obter_openai_api_key", lambda: "synthetic-test-key")
    monkeypatch.setattr(ia_providers, "_ia_chat_eh_saudacao_curta", lambda *_args: False)
    for name in (
        "_ia_chat_bloco_prompt_analise_especialista",
        "_ia_treinamento_ppv_bloco_prompt",
        "_ia_rag_contexto",
        "_ia_estoque_texto",
        "_montar_contexto_ia",
        "_ia_web_contexto",
        "_ia_chat_contexto_funcoes",
        "_ia_vendas_db_texto",
        "_ia_vendas_contexto_exato",
    ):
        monkeypatch.setattr(ia_providers, name, forbidden)
    monkeypatch.setattr(ia_providers.requests, "post", post)

    assert ia_providers._chamar_openai_responses(payload, "tenant-test") == "resposta sintetica"
    return captured


def _openai_user_text(request_json: dict) -> str:
    return str(request_json["input"][-1]["content"][0]["text"])


def test_openai_always_preserves_question_and_ignores_raw_screen_context(monkeypatch):
    question = "PERGUNTA_CANARIO: quantos SKUs estao com estoque?"
    request_json = _capture_openai_request(
        monkeypatch,
        IAChatRequest(
            message=question,
            context={"visible_text": "RAW_SCREEN_CANARY", "table_rows": ["x" * 10_000]},
            model="gpt-5.4-nano",
        ),
    )

    user_text = _openai_user_text(request_json)
    assert question in user_text
    assert "RAW_SCREEN_CANARY" not in user_text
    assert "Contexto recuperado por busca semantica" not in user_text
    assert "Estoque atual" not in user_text


def test_openai_preserves_question_with_compact_planned_evidence(monkeypatch):
    question = "PERGUNTA_COM_EVIDENCIA"
    request_json = _capture_openai_request(
        monkeypatch,
        IAChatRequest(
            message=question,
            context={
                "modo_rapido_sidebar": True,
                "data_selection": {
                    "schema_version": 1,
                    "intent": "stock",
                    "evidence": [{"sku": "001", "quantity": 7, "source": "Bling"}],
                    "coverage_complete": True,
                }
            },
            model="gpt-5.4-nano",
        ),
    )

    user_text = _openai_user_text(request_json)
    assert question in user_text
    assert "Evidencia compacta selecionada pelo backend" in user_text
    assert '"quantity":7' in user_text


def test_planned_evidence_is_bounded_valid_and_redacts_sensitive_fields():
    payload = IAChatRequest(
        message="resuma",
        context={
            "data_selection": {
                "intent": "summary",
                "access_token": "SECRET_CANARY",
                "path": r"C:\\private\\tenant",
                "evidence": [
                    {"id": index, "description": "x" * 5_000}
                    for index in range(100)
                ],
            }
        },
    )

    text = ia_providers._ia_chat_planned_context_text(payload, "tenant-test")
    prefix, serialized = text.split("\n", 1)

    assert prefix.startswith("Evidencia compacta")
    assert len(serialized) <= ia_providers.IA_CHAT_PLANNED_CONTEXT_MAX_CHARS
    assert "SECRET_CANARY" not in text
    assert "private" not in text
    decoded = json.loads(serialized)
    assert isinstance(decoded, dict)
    assert "preview" not in decoded
