from __future__ import annotations

from ml_questions_gemini.schemas import AIAnswer

from backend.modules.perguntas_pos_venda.ai.clients import _PerguntasVertexGeminiV2Client


class _CriticClient(_PerguntasVertexGeminiV2Client):
    def __init__(self, reviews, revisions=()):
        self._is_post_sale = False
        self._is_regulated = False
        self.loja = "Loja Teste"
        self.agent_input = {
            "subquestions": [{"question": "Vai na carcaça da válvula termostática?"}],
        }
        self.evidence_records = []
        self.context_pipeline = []
        self._technical_resolution_final = {
            "schema": "jk_ml_technical_resolution_v1",
            "overall_decision": "yes",
            "commercial_state": "fits",
        }
        self._technical_research_context = {
            "technical_evidence_graph": {"schema": "jk_ml_evidence_graph_v2"},
        }
        self._document_vision_attachments = []
        self._document_vision_page_refs = []
        self.factual_review = {}
        self.manual_review_required = False
        self._reviews = list(reviews)
        self._revisions = list(revisions)
        self.calls = []

    def _call_structured_model(self, _prompt, _metadata, *, stage, isolated=False, **_kwargs):
        self.calls.append((stage, isolated))
        value = self._reviews.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def _call_model(self, _prompt, _metadata, *, stage, isolated=False, **_kwargs):
        self.calls.append((stage, isolated))
        value = self._revisions.pop(0)
        if isinstance(value, Exception):
            raise value
        return AIAnswer(
            answer=value,
            confidence=0.9,
            requires_human_review=False,
            reason="ai_revision",
        )


def _review(verdict: str):
    return {
        "schema": "jk_ml_factual_review_v1",
        "verdict": verdict,
        "issues": [],
        "revision_instructions": [],
        "confidence": 0.92,
    }


def test_factual_pass_preserves_candidate_body_byte_for_byte():
    body = "  Sim, é instalado na carcaça.  "
    candidate = AIAnswer(answer=body, confidence=0.91, reason="draft")
    client = _CriticClient([_review("pass")])

    result = client._review_public_candidate(candidate, {"category": "compatibility"})

    assert result is candidate
    assert result.answer == body
    assert client.calls == [("factual_critic", True)]
    assert client.manual_review_required is False


def test_revise_creates_new_candidate_and_never_mutates_previous_body():
    original = "  Código sem procedência  "
    corrected = "  O interruptor é instalado na carcaça da válvula termostática.  "
    candidate = AIAnswer(answer=original, confidence=0.8, reason="draft")
    client = _CriticClient([_review("revise"), _review("pass")], [corrected])

    result = client._review_public_candidate(candidate, {"category": "compatibility"})

    assert candidate.answer == original
    assert result.answer == corrected
    assert result is not candidate
    assert client.calls == [
        ("factual_critic", True),
        ("factual_revision", True),
        ("factual_critic", True),
    ]


def test_critic_failure_preserves_last_nonempty_candidate_for_manual_review():
    body = "  Último candidato não vazio\n  "
    candidate = AIAnswer(answer=body, confidence=0.7, reason="draft")
    client = _CriticClient([RuntimeError("timeout")])

    result = client._review_public_candidate(candidate, {"category": "product_feature"})

    assert result.answer == body
    assert result.requires_human_review is True
    assert result.reason == "factual_review_unavailable"
    assert client.manual_review_required is True


def test_empty_or_failed_revision_preserves_candidate_instead_of_fallback():
    body = "Candidato preservado"
    client = _CriticClient([_review("revise")], [""])

    result = client._review_public_candidate(
        AIAnswer(answer=body, confidence=0.8, reason="draft"),
        {"category": "compatibility"},
    )

    assert result.answer == body
    assert result.requires_human_review is True
    assert result.reason == "factual_revision_empty"


def test_at_most_two_revisions_then_preserves_second_candidate_for_human():
    client = _CriticClient(
        [_review("revise"), _review("revise"), _review("revise")],
        ["candidato 1", "candidato 2"],
    )

    result = client._review_public_candidate(
        AIAnswer(answer="original", confidence=0.8, reason="draft"),
        {"category": "compatibility"},
    )

    assert result.answer == "candidato 2"
    assert result.requires_human_review is True
    assert client.calls == [
        ("factual_critic", True),
        ("factual_revision", True),
        ("factual_critic", True),
        ("factual_revision", True),
        ("factual_critic", True),
    ]


def test_post_sale_and_regulated_paths_are_not_changed():
    candidate = AIAnswer(answer="texto literal", confidence=0.8, reason="draft")
    post_sale = _CriticClient([])
    post_sale._is_post_sale = True
    regulated = _CriticClient([])
    regulated._is_regulated = True

    assert post_sale._review_public_candidate(candidate, {"category": "post_sale"}) is candidate
    assert regulated._review_public_candidate(candidate, {"category": "regulated_product"}) is candidate
    assert post_sale.calls == []
    assert regulated.calls == []
