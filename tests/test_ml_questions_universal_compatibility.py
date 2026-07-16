from __future__ import annotations

import pytest

from ml_questions_gemini.classifier import QuestionClassifier
from ml_questions_gemini.compatibility import (
    extract_compatibility_target,
    infer_compatibility_profile,
    normalize_comparison_attributes,
)
from ml_questions_gemini.schemas import ListingSnapshot, QuestionCategory, QuestionContext, SellerRules
from ml_questions_gemini.validator import AnswerValidator


@pytest.mark.parametrize(
    ("question", "title", "target", "target_type"),
    [
        ("Serve na Stihl 120?", "Enxada Rotativa Rocadeira Disco Capina", "Stihl 120", "machine_tool"),
        ("Serve no iPhone 15 Pro?", "Capa MagSafe", "iPhone 15 Pro", "phone_computing"),
        ("Funciona na TV Samsung QN90?", "Controle remoto universal", "TV Samsung QN90", "electrical_electronic"),
        ("Cabe na torneira de 1/2?", "Adaptador de torneira", "torneira de 1/2", "hydraulic"),
        ("Serve na R1300GS?", "Adaptador Smartphone BMW Navigator", "R1300GS", "vehicle"),
    ],
)
def test_classifier_and_profile_cover_non_automotive_targets(question, title, target, target_type):
    classifier = QuestionClassifier()
    classification = classifier.classify(
        QuestionContext(id="Q1", text=question),
        ListingSnapshot(id="MLB1", title=title),
    )

    assert classification.category == QuestionCategory.COMPATIBILITY
    assert extract_compatibility_target(question) == target
    assert infer_compatibility_profile(question=question, title=title)["target_type"] == target_type


def test_phone_as_device_is_not_confused_with_external_contact():
    classifier = QuestionClassifier()
    listing = ListingSnapshot(id="MLB1", title="Capa para celular Samsung")

    device = classifier.classify(QuestionContext(id="Q1", text="Serve no telefone Samsung A54?"), listing)
    contact = classifier.classify(QuestionContext(id="Q2", text="Qual o telefone da loja?"), listing)

    assert device.category == QuestionCategory.COMPATIBILITY
    assert contact.category == QuestionCategory.PROHIBITED_CONTACT


def test_automotive_fuel_pump_pressure_question_uses_vehicle_profile():
    profile = infer_compatibility_profile(
        question="Serve na Land Rover Evoque SE 2.0 gasolina 2017? Quantos bar de pressao?",
        title="Bomba com filtro combustivel Land Rover Evoque 2.0 original",
    )

    assert profile["target_type"] == "vehicle"
    assert profile["compatibility_profile"] == "vehicle_fitment"
    assert extract_compatibility_target(
        "Serve na Evoque SE 2.0 gasolina 2017? Quantos bar de pressao?"
    ) == "Evoque SE 2.0 gasolina 2017"


def test_comparison_attributes_normalize_units_and_results():
    normalized = normalize_comparison_attributes([
        {
            "name": "diametro do eixo",
            "product_value": "26",
            "target_value": "26",
            "unit": "milimetros",
            "status": "compatible",
            "decisive": True,
            "sources": ["manual", "cadastro"],
        }
    ])

    assert normalized == [{
        "attribute": "diametro do eixo",
        "product_value": "26",
        "target_value": "26",
        "unit": "mm",
        "result": "match",
        "decisive": True,
        "evidence_refs": ["manual", "cadastro"],
    }]


def _machine_analysis(decision: str = "yes") -> dict:
    conflict = decision == "no"
    return {
        "target_type": "machine_tool",
        "target_item": "Stihl 120",
        "target_vehicle": "Stihl 120",
        "compatibility_profile": "machine_interface",
        "product_interface": "eixo de 26 mm com 9 estrias",
        "target_interface": "eixo de 28 mm com 7 estrias" if conflict else "eixo de 26 mm com 9 estrias",
        "comparison_attributes": [{
            "attribute": "eixo e estrias",
            "product_value": "26 mm / 9 estrias",
            "target_value": "28 mm / 7 estrias" if conflict else "26 mm / 9 estrias",
            "unit": "mm",
            "result": "conflict" if conflict else "match",
            "decisive": True,
            "evidence_refs": ["cadastro", "manual"],
        }],
        "decision": decision,
        "condition": "",
        "missing_fields": [],
        "evidence": {
            "product": [{"authority": "internal_catalog", "reference": "eixo de 26 mm com 9 estrias"}],
            "target": [{"authority": "official_document", "reference": "manual tecnico da Stihl"}],
            "target_vehicle": [{"authority": "official_document", "reference": "manual tecnico da Stihl"}],
            "equivalence": [{
                "authority": "technical_comparison",
                "reference": "interfaces diferentes" if conflict else "mesma interface de eixo e estrias",
            }],
        },
    }


@pytest.mark.parametrize(
    ("decision", "answer"),
    [
        ("yes", "Esse acessorio e compativel com a Stihl 120 porque utiliza o mesmo eixo de 26 mm e 9 estrias."),
        ("no", "Esse acessorio nao serve na Stihl 120 porque o eixo e a quantidade de estrias sao diferentes."),
    ],
)
def test_machine_compatibility_yes_and_no_follow_structured_comparison(decision, answer):
    validation = AnswerValidator().validate(
        answer,
        question=QuestionContext(id="Q1", text="Serve na Stihl 120?"),
        listing=ListingSnapshot(id="MLB1", title="Enxada rotativa para rocadeira"),
        category=QuestionCategory.COMPATIBILITY,
        rules=SellerRules(max_sentences=3, max_chars=900, min_confidence=0.78),
        confidence=0.95,
        compatibility_analysis=_machine_analysis(decision),
    )

    assert validation.ok, validation.issues


def test_machine_profile_blocks_vehicle_language():
    validation = AnswerValidator().validate(
        "Para confirmar a aplicacao, informe o ano e a versao do veiculo.",
        question=QuestionContext(id="Q1", text="Serve na Stihl 120?"),
        listing=ListingSnapshot(id="MLB1", title="Enxada rotativa para rocadeira"),
        category=QuestionCategory.COMPATIBILITY,
        rules=SellerRules(max_sentences=3, max_chars=900, min_confidence=0.78),
        confidence=0.95,
        compatibility_analysis={
            "target_type": "machine_tool",
            "target_item": "Stihl 120",
            "decision": "insufficient",
            "missing_fields": ["machine_model", "shaft_interface"],
        },
    )

    assert "compatibility_profile_language_mismatch" in validation.issues


def test_stihl_safe_fallback_is_machine_specific_and_text_only():
    import backend_api  # noqa: F401
    from backend.services import perguntas_pos_venda_agent as agent

    agent_input = {
        "question": {"text": "Serve na stihl 120?"},
        "item": {"title": "Enxada Rotativa Rocadeira Disco Capina Grama Universal"},
        "intent": {"fluxo": "perguntas_anuncio", "intencao": "compatibilidade"},
    }
    analysis = agent._perguntas_ia_v2_compatibilidade_padrao(agent_input)
    answer = agent._perguntas_ia_v2_resposta_segura_compatibilidade(agent_input, "JK Pecas", analysis)
    normalized = answer.lower()

    assert analysis["target_type"] == "machine_tool"
    assert analysis["target_item"].lower() == "stihl 120"
    assert "modelo completo da ro" in normalized
    assert "medida do eixo" in normalized
    assert "estrias" in normalized
    assert "veiculo" not in normalized
    assert "foto" not in normalized
    assert "chassi" not in normalized


def test_universal_analysis_persists_canonical_target_and_legacy_alias():
    import backend_api  # noqa: F401
    from backend.services import perguntas_pos_venda_agent as agent

    agent_input = {
        "question": {"text": "Serve na Stihl 120?"},
        "item": {"title": "Enxada Rotativa Rocadeira"},
    }
    analysis = agent._perguntas_ia_v2_compatibilidade_normalizar(
        _machine_analysis("yes"),
        base=agent._perguntas_ia_v2_compatibilidade_padrao(agent_input),
    )

    assert analysis["decision"] == "yes"
    assert analysis["target_type"] == "machine_tool"
    assert analysis["target_item"] == "Stihl 120"
    assert analysis["target_vehicle"] == "Stihl 120"
    assert analysis["evidence"]["target"] == analysis["evidence"]["target_vehicle"]
    assert analysis["comparison_attributes"][0]["unit"] == "mm"


def test_approval_diagnostics_persist_universal_profile_and_comparison():
    from backend.services import perguntas_pos_venda_endpoints as endpoints

    persisted = endpoints._perguntas_ia_compatibility_analysis_normalizar(_machine_analysis("yes"))

    assert persisted["target_type"] == "machine_tool"
    assert persisted["target_item"] == "Stihl 120"
    assert persisted["target_vehicle"] == "Stihl 120"
    assert persisted["compatibility_profile"] == "machine_interface"
    assert persisted["comparison_attributes"][0]["result"] == "match"
    assert persisted["evidence"]["target"] == persisted["evidence"]["target_vehicle"]


def test_question_research_prefetches_separate_queries_in_fast_mode(monkeypatch):
    import backend_api  # noqa: F401
    from backend.services import perguntas_pos_venda_agent as agent

    calls = []

    def fake_search(query, client_id=None, max_results=5, *, fast=False):
        calls.append((query, client_id, max_results, fast))
        slug = str(abs(hash(query)))
        return [{
            "title": "Resultado tecnico",
            "url": f"https://example.com/{slug}",
            "snippet": "especificacao coletada",
            "provider": "fake",
        }]

    monkeypatch.setattr(agent, "_ia_web_buscar_cached", fake_search)
    context = agent._ia_agent_perguntas_contexto_web("000002", "JK Pecas", [
        {"type": "product_interface_identity", "query": "produto eixo 26mm"},
        {"type": "target_interface_official", "query": "stihl 120 eixo manual"},
        {"type": "interface_equivalence", "query": "produto stihl 120 equivalencia"},
    ])

    assert len(calls) == 3
    assert all(call[1:] == ("000002", 4, True) for call in calls)
    assert context.count("Busca ") == 3


_PROFILE_CASES = [
    ("vehicle", "BMW R1300GS", "informe o ano e a versao do veiculo", ["ano", "versao"]),
    ("machine_tool", "Stihl FS 120", "informe o modelo completo e a medida do eixo", ["modelo completo", "medida do eixo"]),
    ("phone_computing", "iPhone 15 Pro", "informe o modelo completo e o tipo de conector", ["modelo completo", "tipo de conector"]),
    ("electrical_electronic", "TV Samsung QN90", "informe a tensao e o tipo de conector", ["tensao", "tipo de conector"]),
    ("hydraulic", "torneira de 1/2", "informe a medida e o tipo de rosca", ["medida", "tipo de rosca"]),
    ("dimensional", "flange de 26 mm", "informe a medida e o padrao de furacao", ["medida", "furacao"]),
    ("generic", "equipamento modelo X", "informe o modelo completo e o tipo de encaixe", ["modelo completo", "tipo de encaixe"]),
]


def _profile_decision_analysis(target_type: str, target_item: str, decision: str) -> dict:
    result = "conflict" if decision == "no" else "match"
    condition = "interface padrao X" if decision == "conditional" else ""
    comparison_reference = "interfaces diferentes" if decision == "no" else "mesma interface padrao X"
    return {
        "target_type": target_type,
        "target_item": target_item,
        "target_vehicle": target_item,
        "compatibility_profile": "generic_interface",
        "product_interface": "interface padrao X de 26 mm",
        "target_interface": "interface padrao Y de 28 mm" if decision == "no" else "interface padrao X de 26 mm",
        "comparison_attributes": [{
            "attribute": "interface decisiva",
            "product_value": "26",
            "target_value": "28" if decision == "no" else "26",
            "unit": "milimetros",
            "result": result,
            "decisive": True,
            "evidence_refs": ["cadastro", "manual oficial"],
        }],
        "decision": decision,
        "condition": condition,
        "missing_fields": [],
        "evidence": {
            "product": [{"authority": "internal_catalog", "reference": "interface padrao X de 26 mm"}],
            "target": [{"authority": "official_document", "reference": "interface padrao X de 26 mm"}],
            "target_vehicle": [{"authority": "official_document", "reference": "interface padrao X de 26 mm"}],
            "equivalence": [{"authority": "technical_comparison", "reference": comparison_reference}],
        },
    }


@pytest.mark.parametrize(("target_type", "target_item", "detail_request", "missing_fields"), _PROFILE_CASES)
@pytest.mark.parametrize("decision", ["yes", "no", "conditional", "insufficient"])
def test_every_profile_accepts_all_structured_decisions(
    target_type,
    target_item,
    detail_request,
    missing_fields,
    decision,
):
    if decision == "yes":
        answer = f"Este produto serve no {target_item} porque utiliza a mesma interface padrao X."
        analysis = _profile_decision_analysis(target_type, target_item, decision)
    elif decision == "no":
        answer = f"Este produto nao serve no {target_item} porque as interfaces sao diferentes."
        analysis = _profile_decision_analysis(target_type, target_item, decision)
    elif decision == "conditional":
        answer = f"A compatibilidade depende da interface padrao X do {target_item}."
        analysis = _profile_decision_analysis(target_type, target_item, decision)
    else:
        answer = f"Para confirmar a compatibilidade, {detail_request}."
        analysis = {
            "target_type": target_type,
            "target_item": target_item,
            "target_vehicle": target_item,
            "decision": "insufficient",
            "condition": "",
            "missing_fields": missing_fields,
        }

    validation = AnswerValidator().validate(
        answer,
        question=QuestionContext(id="Q1", text=f"Serve no {target_item}?"),
        listing=ListingSnapshot(id="MLB1", title="Produto de teste de compatibilidade"),
        category=QuestionCategory.COMPATIBILITY,
        rules=SellerRules(max_sentences=3, max_chars=900, min_confidence=0.78),
        confidence=0.95,
        compatibility_analysis=analysis,
    )

    assert validation.ok, (target_type, decision, validation.issues)
