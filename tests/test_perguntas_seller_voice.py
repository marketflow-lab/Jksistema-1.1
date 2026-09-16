from __future__ import annotations

import pytest

from ml_questions_gemini.schemas import AIAnswer

from backend.modules.perguntas_pos_venda.ai.clients import _PerguntasVertexGeminiV2Client


FORMAL_DRAFT = (
    "Não há dados suficientes para confirmar a compatibilidade. "
    "A avaliação exige a identificação exata da bomba injetora anunciada, "
    "o modelo completo do motor Branca e as medidas de fixação, eixo e conexões."
)
SELLER_DRAFT = (
    "Olá! O anúncio apresenta a bomba para motores diesel de 5, 7 e 10 hp, "
    "mas só a potência não confirma se ela serve no seu Branca de 5 hp. "
    "Você pode me informar o modelo exato do motor?"
)


class _VoiceClient(_PerguntasVertexGeminiV2Client):
    def __init__(self, response: str | Exception = SELLER_DRAFT):
        self._is_post_sale = False
        self._is_regulated = False
        self.loja = "Loja Teste"
        self.agent_input = {
            "question": {"text": "É compatível com motor Branca diesel de 5 hp?"},
            "item": {"title": "Bomba Injetora Motor Diesel 5-7-10hp Branco Buffalo Toyama"},
        }
        self.compatibility_analysis = {"decision": "insufficient", "confidence": 0.45}
        self.sku_question_context = {}
        self._technical_resolution_final = {}
        self._technical_evidence_graph = {}
        self.context_pipeline = []
        self.manual_review_required = False
        self.calls: list[str] = []
        self.response = response

    def _call_model(self, _prompt, _metadata, *, stage, **_kwargs):
        self.calls.append(stage)
        if isinstance(self.response, Exception):
            raise self.response
        return AIAnswer(answer=self.response, confidence=0.9, reason="editorial")


def test_seller_voice_facade_preserves_formal_model_reply_without_editing() -> None:
    client = _VoiceClient()
    candidate = AIAnswer(answer=FORMAL_DRAFT, confidence=0.45, requires_human_review=True)

    result = client._enforce_public_seller_voice(candidate, {"category": "compatibility"})

    assert result is candidate
    assert result.answer == FORMAL_DRAFT
    assert result.requires_human_review is True
    assert client.calls == []
    assert client.context_pipeline == []


def test_seller_voice_facade_never_calls_an_editor_even_on_timeout() -> None:
    client = _VoiceClient(TimeoutError("provider_timeout"))
    candidate = AIAnswer(answer=FORMAL_DRAFT, confidence=0.45)

    assert client._enforce_public_seller_voice(candidate, {"category": "product_feature"}) is candidate
    assert client.calls == []


def test_public_compatibility_reply_is_the_model_output_literal() -> None:
    literal = "  Temos a peça. Confira este link oficial: https://produto.mercadolivre.com.br/MLB-222  \n"
    client = _VoiceClient(literal)

    result = client._generate_public_compatibility_answer(
        {"category": "compatibility"}, technical=AIAnswer(answer=FORMAL_DRAFT), alternative={},
    )

    assert result.answer == literal
    assert client.calls == ["compatibility_public_answer"]


def test_public_generation_failure_is_reported_without_manufactured_answer() -> None:
    client = _VoiceClient(RuntimeError("provider_unavailable"))

    with pytest.raises(RuntimeError, match="provider_unavailable"):
        client._generate_public_compatibility_answer(
            {"category": "compatibility"}, technical=AIAnswer(answer=FORMAL_DRAFT), alternative={},
        )
    assert client.calls == ["compatibility_public_answer"]
