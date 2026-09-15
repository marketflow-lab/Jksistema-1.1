from __future__ import annotations

from ml_questions_gemini.schemas import AIAnswer

from backend.modules.perguntas_pos_venda.ai.clients import _PerguntasVertexGeminiV2Client
from backend.modules.perguntas_pos_venda.ai.validation import _perguntas_ia_seller_style_violations


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
        self._technical_resolution_final = {}
        self._technical_evidence_graph = {}
        self.context_pipeline = []
        self.manual_review_required = False
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.response = response

    def _call_structured_model(self, *_args, **_kwargs):
        raise AssertionError("A geração pública não deve chamar crítica factual")

    def _call_model(self, prompt, _metadata, *, stage, **_kwargs):
        self.calls.append(stage)
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return AIAnswer(answer=self.response, confidence=0.9, reason="editorial")


def test_direct_seller_answer_is_preserved_without_extra_model_calls() -> None:
    client = _VoiceClient()
    candidate = AIAnswer(answer=SELLER_DRAFT, confidence=0.45, requires_human_review=True)

    result = client._enforce_public_seller_voice(candidate, {"category": "compatibility"})

    assert result is candidate
    assert client.calls == []
    assert client.context_pipeline == []


def test_formal_report_is_rewritten_in_seller_voice_without_factual_review() -> None:
    client = _VoiceClient()
    candidate = AIAnswer(
        answer=FORMAL_DRAFT,
        confidence=0.45,
        requires_human_review=True,
        reason="insufficient",
    )

    result = client._enforce_public_seller_voice(candidate, {"category": "compatibility"})

    assert candidate.answer == FORMAL_DRAFT
    assert result.answer == SELLER_DRAFT
    assert result.requires_human_review is True
    assert result.confidence == 0.45
    assert "5, 7 e 10 hp" in result.answer
    assert "modelo exato do motor" in result.answer
    assert "compre" not in result.answer.lower()
    assert client.calls == ["seller_voice_edit"]
    assert "É compatível com motor Branca" in client.prompts[0]
    assert [step["name"] for step in client.context_pipeline] == ["seller_voice_edit"]
    assert _perguntas_ia_seller_style_violations(result.answer) == []


def test_style_timeout_uses_cordial_compatibility_fallback() -> None:
    client = _VoiceClient(TimeoutError("provider_timeout"))

    result = client._enforce_public_seller_voice(
        AIAnswer(answer=FORMAL_DRAFT, confidence=0.45),
        {"category": "compatibility"},
    )

    assert result.answer.startswith("Olá!")
    assert "5, 7 e 10 hp" in result.answer
    assert "modelo exato" in result.answer
    assert "não confirma" in result.answer
    assert result.requires_human_review is True
    assert _perguntas_ia_seller_style_violations(result.answer) == []
    assert client.calls == ["seller_voice_edit"]
    assert client.context_pipeline[-1]["status"] == "fallback"


def test_public_generation_failure_never_exposes_technical_report() -> None:
    client = _VoiceClient(RuntimeError("provider_unavailable"))
    technical = AIAnswer(answer=FORMAL_DRAFT, confidence=0.45)

    result = client._generate_public_compatibility_answer(
        {"category": "compatibility"},
        technical=technical,
        alternative={},
    )

    assert result.answer != technical.answer
    assert result.answer.startswith("Olá!")
    assert "5, 7 e 10 hp" in result.answer
    assert "modelo exato" in result.answer
    assert result.requires_human_review is True
    assert client.compatibility_public_fallback == "seller_voice_deterministic"
    assert client.calls == ["compatibility_public_answer"]


def test_general_question_with_formal_only_draft_gets_cordial_fallback() -> None:
    client = _VoiceClient(TimeoutError("provider_timeout"))

    result = client._enforce_public_seller_voice(
        AIAnswer(answer="A avaliação exige dados disponíveis do produto.", confidence=0.8),
        {"category": "product_feature"},
    )

    assert result.answer.startswith("Olá!")
    assert "laudo" not in result.answer
    assert result.requires_human_review is True
    assert _perguntas_ia_seller_style_violations(result.answer) == []


def test_fallback_without_a_power_in_the_question_does_not_claim_one() -> None:
    client = _VoiceClient(TimeoutError("provider_timeout"))
    client.agent_input["question"]["text"] = "Serve no meu motor?"
    client.agent_input["item"]["title"] = "Bomba injetora para motor diesel"

    result = client._enforce_public_seller_voice(
        AIAnswer(answer=FORMAL_DRAFT, confidence=0.45),
        {"category": "compatibility"},
    )

    assert "potência" not in result.answer
    assert "modelo exato" in result.answer


def test_verified_same_store_alternative_link_is_not_split_into_extra_sentences() -> None:
    client = _VoiceClient()
    body = (
        "Não, este modelo usa outro conector. "
        "Temos a alternativa confirmada aqui: "
        "https://produto.mercadolivre.com.br/MLB-2222222222-alternativa-_JM"
    )
    candidate = AIAnswer(answer=body, confidence=0.9)

    result = client._enforce_public_seller_voice(candidate, {"category": "compatibility"})

    assert result is candidate
    assert client.calls == []
    assert _perguntas_ia_seller_style_violations(result.answer) == []
