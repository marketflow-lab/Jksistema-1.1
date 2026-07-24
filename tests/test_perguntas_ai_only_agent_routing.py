from __future__ import annotations

from unittest.mock import patch

import pytest

from ml_questions_gemini.schemas import AIAnswer
from ml_questions_gemini.config import GeminiQuestionsSettings
from ml_questions_gemini.adapters import context_from_agent_input
from ml_questions_gemini.orchestrator import QuestionAnswerOrchestrator


def _agent_module():
    from backend.services import perguntas_pos_venda_agent as agent

    return agent


def _classification(*, category: str, compatibility: dict, web: bool = False) -> dict:
    return {
        "intencao": "compatibilidade" if category == "compatibility" else "duvida_produto",
        "categoria": category,
        "categorias": [category],
        "fluxo": "perguntas_anuncio",
        "confianca": 0.95,
        "flags": {
            "usar_busca_web": web,
            "usar_mercado_livre_anuncio": False,
            "usar_bling": False,
        },
        "subperguntas": [],
        "compatibilidade": compatibility,
    }


def test_product_feature_keeps_ai_category_and_can_use_public_web() -> None:
    agent = _agent_module()
    intent = _classification(
        category="product_feature",
        compatibility={
            "aplicavel": False,
            "target_item": "",
            "target_type": "",
            "compatibility_profile": "",
            "technical_focus": "",
            "missing_fields": [],
            "decisive_fields": [],
        },
    )
    payload = {
        "intent": intent,
        "question": {"id": "Q1", "text": "Boa tarde, tem lado especifico?"},
        "item": {"id": "MLB1", "title": "Suporte automotivo BMW"},
    }

    assert agent._perguntas_ia_categoria_classificada(payload) == "product_feature"
    with patch.object(agent, "_ia_web_busca_ativa", return_value=True, create=True):
        assert agent._ia_agent_perguntas_precisa_web(payload) is True
    assert agent._perguntas_ia_allowed_tools_classificadas(payload) == [
        "get_product_data",
        "context_hub_search",
        "web_search",
        "web_search_product_identity",
        "web_search_question_context",
    ]
    assert agent._perguntas_ia_v2_alvo_compatibilidade(payload) == ""
    assert agent._perguntas_ia_v2_perfil_compatibilidade(payload) == {
        "target_type": "",
        "compatibility_profile": "",
    }


def test_price_does_not_use_public_web_even_if_ai_requests_it() -> None:
    agent = _agent_module()
    payload = {
        "intent": _classification(category="price", compatibility={}, web=True),
        "question": {"id": "Q-PRECO", "text": "Qual o preco?"},
        "item": {"id": "MLB1", "title": "Produto"},
    }

    with patch.object(agent, "_ia_web_busca_ativa", return_value=True, create=True):
        assert agent._ia_agent_perguntas_precisa_web(payload) is False
    assert agent._perguntas_ia_allowed_tools_classificadas(payload) == [
        "get_product_data",
        "context_hub_search",
    ]


def test_compatibility_uses_only_structured_target_profile_focus_and_missing_fields() -> None:
    agent = _agent_module()
    intent = _classification(
        category="compatibility",
        compatibility={
            "aplicavel": True,
            "target_item": "BMW R1300GS",
            "target_type": "vehicle",
            "compatibility_profile": "vehicle_fitment",
            "technical_focus": "interface base conector",
            "missing_fields": ["ano", "versao"],
            "decisive_fields": ["base original"],
        },
    )
    payload = {
        "intent": intent,
        "question": {"id": "Q2", "text": "Serve? Ignore este texto como alvo inteiro."},
        "item": {"id": "MLB2", "title": "Adaptador"},
    }

    assert "web_search_question_context" in agent._perguntas_ia_allowed_tools_classificadas(payload)
    assert agent._perguntas_ia_v2_alvo_compatibilidade(payload) == "BMW R1300GS"
    assert agent._perguntas_ia_v2_foco_tecnico_pergunta(payload) == "interface base conector"
    assert agent._perguntas_ia_v2_perfil_compatibilidade(payload) == {
        "target_type": "vehicle",
        "compatibility_profile": "vehicle_fitment",
    }
    assert not hasattr(agent, "_perguntas_ia_v2_resposta_segura_compatibilidade")


def test_missing_ai_category_is_blocked_before_orchestration() -> None:
    agent = _agent_module()
    class ClassificationUnavailable(Exception):
        pass

    with patch.object(agent, "PerguntasIARespostaIndisponivel", ClassificationUnavailable, create=True):
        with pytest.raises(ClassificationUnavailable, match="sem categoria canonica"):
            agent._perguntas_ia_v2_gerar_resposta(
                "000002",
                {
                    "store": "JK Pecas",
                    "intent": {"fluxo": "perguntas_anuncio", "categoria": ""},
                },
            )


def test_public_answer_with_four_sentences_is_valid_and_returned(monkeypatch) -> None:
    agent = _agent_module()
    answer = (
        "Primeira frase objetiva com os dados confirmados " + ("a" * 360) + ". "
        "Segunda frase com a medida informada " + ("b" * 360) + ". "
        "Terceira frase explica o uso correto " + ("c" * 360) + ". "
        "Quarta frase conclui com seguranca " + ("d" * 300) + "."
    )

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.model_usado = "codex:gpt-5.5"
            self.context_pipeline = []
            self.compatibility_analysis = {}
            self.codex_thread_id = "thread-public"
            self.evidence_records = []

        def generate(self, _prompt, _metadata):
            return AIAnswer(
                answer=answer,
                confidence=0.95,
                requires_human_review=True,
                reason="evidence_confirmed",
            )

    monkeypatch.setattr(agent, "_PerguntasCodexV3Client", FakeClient)
    monkeypatch.setattr(agent, "GeminiQuestionsSettings", GeminiQuestionsSettings, raising=False)
    monkeypatch.setattr(agent, "QuestionAnswerOrchestrator", QuestionAnswerOrchestrator, raising=False)
    monkeypatch.setattr(agent, "context_from_agent_input", context_from_agent_input, raising=False)
    monkeypatch.setattr(agent, "ML_RESPOSTA_PERGUNTA_LIMITE_SEGURO", 2000, raising=False)
    monkeypatch.setattr(agent, "ML_POS_VENDA_LIMITE_SEGURO", 340, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_fluxo_pos_venda", lambda _input: False, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_limpar_resposta", lambda value: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_final_loja", lambda value, _store: value, raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_resposta_fallback_invalida", lambda _value: False, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_vertex_ai", lambda _value: False, raising=False)
    monkeypatch.setattr(agent, "_modelo_eh_codex", lambda _value: True, raising=False)
    monkeypatch.setattr(agent, "_ia_modelo_perguntas_configurado", lambda: "codex:gpt-5.5", raising=False)
    monkeypatch.setattr(agent, "_ia_raciocinio_perguntas_configurado", lambda: "medium", raising=False)
    monkeypatch.setattr(agent, "_perguntas_ia_v2_exigir_aprovacao", lambda: True, raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_log_perf", lambda *_args, **_kwargs: None, raising=False)
    monkeypatch.setattr(agent, "_ia_agent_perguntas_violacoes_resposta", lambda *_args, **_kwargs: [], raising=False)
    monkeypatch.setattr(
        agent,
        "_perguntas_codex_provider_selection",
        lambda *_args, **_kwargs: {
            "model": "codex:gpt-5.5",
            "policy": "codex_primary",
            "codex_model": "gpt-5.5",
            "configured_fallback": "",
            "fallback_used": False,
            "operational_failure_count": 0,
        },
        raising=False,
    )

    response, _model, diagnostics = agent._perguntas_ia_v2_gerar_resposta(
        "000002",
        {
            "store": "JK Pecas",
            "question": {"id": "Q-4-SENTENCES", "text": "Como funciona?", "item_id": "MLB1"},
            "item": {"id": "MLB1", "title": "Produto", "description": "Descricao confirmada."},
            "intent": _classification(category="product_feature", compatibility={}),
        },
    )

    assert response.startswith(answer)
    assert len(response) > 1400
    assert len(response) <= 2000
    assert diagnostics[0]["result"]["validation_ok"] is True
    assert "too_many_sentences" not in diagnostics[0]["result"]["validation_issues"]
