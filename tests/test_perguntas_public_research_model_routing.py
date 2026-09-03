from __future__ import annotations

import pytest

from backend.modules.perguntas_pos_venda.ai import clients


_MODEL_RESPONSE = (
    '{"answer":"Resposta tecnica.","confidence":0.9,'
    '"requires_human_review":false,"reason":"verified_evidence",'
    '"commercial_state":"insufficient",'
    '"compatibility_analysis":{"decision":"insufficient","missing_fields":["codigo OEM"]}}'
)


def _agent_input(question: str, *, post_sale: bool = False, regulated: bool = False) -> dict:
    category = "post_sale" if post_sale else ("regulated_product" if regulated else "product_feature")
    return {
        "question": {"text": question},
        "intent": {
            "intencao": "pos_venda_defeito" if post_sale else "duvida_produto",
            "categoria": category,
            "categorias": [category],
            "fluxo": "pos_venda" if post_sale else "perguntas_anuncio",
            "confianca": 0.97,
            "flags": {
                "usar_busca_web": not post_sale,
                "usar_mercado_livre_anuncio": not post_sale,
                "usar_bling": not post_sale,
            },
            "subperguntas": [{
                "intent": "general" if regulated else category,
                "question": question,
                "required_evidence": "anuncio, cadastro ou fonte tecnica coletada",
            }],
            "compatibilidade": {
                "aplicavel": False,
                "target_item": "",
                "target_type": "",
                "compatibility_profile": "",
                "technical_focus": "",
                "missing_fields": [],
                "decisive_fields": [],
            },
        },
    }


def _capture_model_call(monkeypatch):
    captured: list[dict] = []

    def _fake_call(_client_id, payload, model_req):
        captured.append({"payload": payload, "model_req": model_req})
        return _MODEL_RESPONSE, model_req

    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "medium")
    monkeypatch.setattr(clients, "_ia_raciocinio_pos_venda_configurado", lambda: "medium")
    monkeypatch.setattr(clients, "_ia_agent_perguntas_chamar_modelo", _fake_call)
    return captured


@pytest.mark.parametrize(
    "stage",
    (
        "commercial_fit_evaluation",
        "compatibility_analysis",
        "compatibility_public_answer",
        "external_research_final",
        "technical_question_plan",
        "technical_evidence_graph",
        "technical_resolution_round_1",
        "technical_resolution_final",
        "factual_critic",
        "factual_revision",
    ),
)
def test_public_technical_research_stages_use_sol_high(monkeypatch, stage):
    captured = _capture_model_call(monkeypatch)
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test",
        "Loja Teste",
        "codex:gpt-5.5",
        _agent_input("Serve no veiculo?"),
        reasoning_effort="low",
    )

    client._invoke_stage_model("Analise tecnica", {"category": "compatibility"}, stage=stage)

    call = captured[0]
    assert call["model_req"] == "codex:gpt-5.6-sol"
    assert call["payload"].model == "codex:gpt-5.6-sol"
    assert call["payload"].context["_codex_reasoning_effort"] == "high"
    assert call["payload"].context["context_collection_stage"] == stage


def test_public_listing_generation_keeps_configured_model_and_effort(monkeypatch):
    captured = _capture_model_call(monkeypatch)
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test",
        "Loja Teste",
        "codex:gpt-5.5",
        _agent_input("Acompanha cabo?"),
        reasoning_effort="low",
    )

    client._call_model("Responda pelo anuncio", {"category": "general"}, stage="listing_only")

    call = captured[0]
    assert call["model_req"] == "codex:gpt-5.5"
    assert call["payload"].model == "codex:gpt-5.5"
    assert call["payload"].context["_codex_reasoning_effort"] == "low"


@pytest.mark.parametrize(
    "stage",
    ("technical_resolution_final", "factual_critic", "factual_revision"),
)
def test_v16_independent_stages_do_not_reuse_operational_thread(monkeypatch, stage):
    captured = _capture_model_call(monkeypatch)
    data = _agent_input("Onde esta peça é instalada?")
    data.update({"_codex_thread_id": "thread-old", "_codex_job_id": "job-1"})
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test", "Loja Teste", "codex:gpt-5.5", data,
    )
    client.codex_thread_id = "thread-old"

    client._invoke_stage_model(
        "Adjudique de forma independente",
        {"category": "compatibility"},
        stage=stage,
        isolated=True,
    )

    context = captured[0]["payload"].context
    assert context["_codex_thread_id"] == ""
    assert context["_codex_persist_thread"] is False
    assert context["_codex_active_turn_key"] == ""
    assert context["_codex_conversation_key"] == ""


def test_multimodal_evidence_graph_is_always_ephemeral_even_without_caller_flag(monkeypatch):
    captured = _capture_model_call(monkeypatch)
    data = _agent_input("Onde esta peça é instalada?")
    data.update({"_codex_thread_id": "thread-old", "_codex_job_id": "job-1"})
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test", "Loja Teste", "codex:gpt-5.5", data,
    )
    client.codex_thread_id = "thread-old"

    client._invoke_stage_model(
        "Extraia o grafo das imagens",
        {"category": "compatibility"},
        stage="technical_evidence_graph",
    )

    context = captured[0]["payload"].context
    assert context["_codex_thread_id"] == ""
    assert context["_codex_persist_thread"] is False
    assert context["_codex_active_turn_key"] == ""
    assert context["_codex_conversation_key"] == ""
    assert client.codex_thread_id == "thread-old"


def test_post_sale_never_uses_public_technical_research_override(monkeypatch):
    captured = _capture_model_call(monkeypatch)
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test",
        "Loja Teste",
        "codex:gpt-5.5",
        _agent_input("Meu pedido chegou com problema.", post_sale=True),
        reasoning_effort="xhigh",
    )

    client._call_model(
        "Atenda o pos-venda",
        {"category": "compatibility"},
        stage="external_research_final",
    )

    call = captured[0]
    assert call["model_req"] == "codex:gpt-5.5"
    assert call["payload"].model == "codex:gpt-5.5"
    assert call["payload"].context["_codex_reasoning_effort"] == "xhigh"


def test_regulated_product_never_uses_public_technical_research_override(monkeypatch):
    captured = _capture_model_call(monkeypatch)
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-test",
        "Loja Teste",
        "codex:gpt-5.5",
        _agent_input("Este produto exige receita?", regulated=True),
        reasoning_effort="medium",
    )

    client._call_model(
        "Gere o rascunho factual regulado",
        {"category": "regulated_product"},
        stage="external_research_final",
    )

    call = captured[0]
    assert call["model_req"] == "codex:gpt-5.5"
    assert call["payload"].model == "codex:gpt-5.5"
    assert call["payload"].context["_codex_reasoning_effort"] == "medium"
