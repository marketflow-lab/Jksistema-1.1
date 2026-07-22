from __future__ import annotations

from unittest.mock import patch

import pytest


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
