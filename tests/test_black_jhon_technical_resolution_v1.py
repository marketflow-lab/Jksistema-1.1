from __future__ import annotations

import copy
from types import SimpleNamespace

from ml_questions_gemini.schemas import AIAnswer

from backend.modules.perguntas_pos_venda.ai import clients
from backend.modules.perguntas_pos_venda.ai.client_workflows import _prepare_grounding
from backend.modules.perguntas_pos_venda.ai.general_commercial import (
    _evaluate_general_fit_and_alternative,
)
from backend.modules.perguntas_pos_venda.ai.technical_resolution import (
    TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
    TECHNICAL_QUESTION_PLAN_SCHEMA,
    TECHNICAL_RESOLUTION_SCHEMA,
    commit_technical_resolution,
    normalize_technical_evidence_graph,
    normalize_technical_question_plan,
    normalize_technical_resolution,
    resolve_technical_question,
    technical_resolution_needs_followup,
)


def _plan():
    return normalize_technical_question_plan({
        "schema": TECHNICAL_QUESTION_PLAN_SCHEMA,
        "requirements": [{
            "id": "q1",
            "essential": True,
            "kind": "installation_location",
            "question": "Onde esta peça é instalada?",
            "relation": "mounted_on",
            "required_fields": ["installation.location"],
        }],
        "queries": [{
            "type": "installation_location_official",
            "query": "interruptor ventoinha local instalação Honda Fit",
            "requirement_ids": ["q1"],
        }],
    })


def test_question_plan_preserves_identifier_origin_and_removes_vin():
    plan = normalize_technical_question_plan({
        "schema": TECHNICAL_QUESTION_PLAN_SCHEMA,
        "requirements": [{
            "id": "fitment/front-left.01",
            "essential": True,
            "kind": "compatibility",
            "question": "Serve no veículo informado?",
            "subject": {
                "kind": "part",
                "name": "Sensor",
                "identifiers": ["37760-P00-003", "8AD2MKFWXCG035615"],
                "identifier_origins": [{
                    "value": "37760-P00-003",
                    "origin": "listing_attribute",
                    "source_ref": "PART_NUMBER",
                }],
            },
            "relation": "compatible_with",
            "required_fields": ["compatibility.application"],
        }],
        "queries": [],
    })

    requirement = plan.requirements[0]
    assert requirement["id"] == "fitment/front-left.01"
    assert requirement["subject"]["identifiers"] == ["37760-P00-003"]
    assert requirement["subject"]["identifier_origins"] == [{
        "value": "37760-P00-003",
        "origin": "listing_attribute",
        "source_ref": "PART_NUMBER",
    }]


def _graph(*, relation: str = "mounted_on") -> dict:
    return {
        "schema": TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
        "entities": [
            {"id": "piece", "kind": "part", "name": "Interruptor da ventoinha", "identifiers": []},
            {"id": "housing", "kind": "location", "name": "Carcaça da válvula termostática", "identifiers": []},
        ],
        "claims": [{
            "id": "claim-1",
            "entity_id": "piece",
            "field_name": "installation.location",
            "value": "carcaça da válvula termostática",
            "unit": "",
            "source_refs": ["passage-1"],
            "support": "supports",
            "requirement_ids": ["q1"],
        }],
        "passages": [{
            "id": "passage-1",
            "source_ref": "https://example.test/manual",
            "section_ref": "instalação",
            "text": "O interruptor é instalado na carcaça da válvula termostática.",
            "matched_identifiers": [],
            "requirement_ids": ["q1"],
        }],
        "relations": [{
            "id": "relation-1",
            "from_entity_id": "piece",
            "relation": relation,
            "to_entity_id": "housing",
            "claim_ids": ["claim-1"],
            "source_refs": ["passage-1"],
            "requirement_ids": ["q1"],
        }],
        "unresolved_requirement_ids": [],
    }


def _resolution(
    decision: str,
    body: str,
    *,
    state: str = "",
    gap_queries: list[dict] | None = None,
    round_number: int = 1,
    final: bool = False,
) -> dict:
    return {
        "schema": TECHNICAL_RESOLUTION_SCHEMA,
        "round": round_number,
        "final": final,
        "requirements": [{
            "id": "q1",
            "decision": decision,
            "conclusion": "Conclusão técnica",
            "facts": [{
                "field_name": "installation.location",
                "relation": "mounted_on",
                "value": "carcaça da válvula termostática",
                "source_refs": ["fonte-1"],
                "support": "supports",
            }],
            "confidence": 0.91,
        }],
        "overall_decision": decision,
        "commercial_state": state,
        "confidence": 0.91,
        "reason": "adjudicado_pela_ia",
        "gap_queries": list(gap_queries or []),
        "contingency_answer_body": body,
        "compatibility_analysis": {
            "decision": decision,
            "target_item": "Honda Fit 2003",
            "product_interface": "interruptor da ventoinha",
            "target_interface": "carcaça da válvula termostática",
            "evidence": {},
            "reason": "adjudicado_pela_ia",
        },
    }


def _identity_normalizer(value, **_kwargs):
    return copy.deepcopy(value)


def test_plan_contract_caps_entries_and_redacts_vin_from_queries() -> None:
    raw = {
        "requirements": [
            {"id": f"q{index}", "question": f"Questão {index}"}
            for index in range(1, 11)
        ],
        "queries": [
            {
                "type": "official",
                "query": "VIN 1HGCM82633A004352 catálogo Honda" if index == 0 else f"consulta {index}",
                "requirement_ids": ["q1", "inexistente"],
            }
            for index in range(5)
        ],
    }

    plan = normalize_technical_question_plan(raw)

    assert plan.schema == TECHNICAL_QUESTION_PLAN_SCHEMA
    assert len(plan.requirements) == 8
    assert len(plan.queries) == 4
    assert "1HGCM82633A004352" not in plan.queries[0]["query"]
    assert "CHASSI_PROTEGIDO" in plan.queries[0]["query"]
    assert plan.queries[0]["requirement_ids"] == ["q1"]


def test_plan_preserves_origin_requirement_identifier_and_query_link() -> None:
    origin_id = "fitment:front/left.01"

    plan = normalize_technical_question_plan({
        "schema": TECHNICAL_QUESTION_PLAN_SCHEMA,
        "requirements": [{"id": origin_id, "question": "Serve no lado esquerdo?"}],
        "queries": [{
            "type": "fitment",
            "query": "aplicação lado esquerdo",
            "requirement_ids": [origin_id],
        }],
    })

    assert plan.requirements[0]["id"] == origin_id
    assert plan.queries[0]["requirement_ids"] == [origin_id]


def test_evidence_graph_keeps_required_directional_relation() -> None:
    graph = normalize_technical_evidence_graph(_graph(relation="installed_in"))

    assert graph.contract_valid is True
    assert graph.schema == TECHNICAL_EVIDENCE_GRAPH_SCHEMA
    assert graph.relations == ({
        "id": "relation-1",
        "from_entity_id": "piece",
        "relation": "installed_in",
        "to_entity_id": "housing",
        "claim_ids": ["claim-1"],
        "source_refs": ["passage-1"],
        "requirement_ids": ["q1"],
    },)


def test_evidence_graph_rejects_incomplete_or_open_structured_payloads() -> None:
    assert normalize_technical_evidence_graph({
        "schema": TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
    }).contract_valid is False

    open_payload = _graph()
    open_payload["unexpected"] = "not allowed by the closed contract"
    assert normalize_technical_evidence_graph(open_payload).contract_valid is False


def test_two_pass_resolution_isolated_final_can_overturn_without_early_state() -> None:
    class FakeClient:
        def __init__(self):
            self.compatibility_analysis = {"decision": "insufficient", "sentinel": True}
            self.commercial_state = ""
            self._compatibility_queries = []
            self._compatibility_sources = []
            self._compatibility_grounding = {}
            self.calls = []

        def _call_structured_model(self, _prompt, _metadata, *, stage, tool_results, isolated=False):
            self.calls.append((stage, isolated, list(tool_results)))
            if stage == "technical_evidence_graph":
                return _graph()
            if stage == "technical_resolution_round_1":
                return _resolution("yes", "  Corpo da rodada 1  ")
            assert stage == "technical_resolution_final"
            return _resolution(
                "no",
                "  Corpo final\n\nAssinatura literal  ",
                round_number=2,
                final=True,
            )

    client = FakeClient()
    initial_analysis = copy.deepcopy(client.compatibility_analysis)
    round_1, final, statuses = resolve_technical_question(
        client,
        {"category": "compatibility"},
        _plan(),
        {"question": {"text": "Serve?"}},
        tool_results=[{"result": {"found": True}}],
    )

    assert round_1.overall_decision == "yes"
    assert final.overall_decision == "no"
    assert final.commercial_state == "incompatible"
    assert final.contingency_answer_body == "  Corpo final\n\nAssinatura literal  "
    assert statuses == {
        "evidence_graph": "completed",
        "round_1": "completed",
        "gap_research": "not_needed",
        "final_evidence_graph": "reused",
        "final": "completed",
    }
    assert [(stage, isolated) for stage, isolated, _ in client.calls] == [
        ("technical_evidence_graph", False),
        ("technical_resolution_round_1", False),
        ("technical_resolution_final", True),
    ]
    assert client.compatibility_analysis == initial_analysis
    assert client.commercial_state == ""

    commit_technical_resolution(
        client,
        final,
        compatibility_normalizer=_identity_normalizer,
    )
    assert client.compatibility_analysis["decision"] == "no"
    assert client.commercial_state == "incompatible"


def test_final_failure_uses_round_one_body_byte_for_byte() -> None:
    body = " Primeira linha.\n\nEquipe Loja agradece!  "

    class FakeClient:
        compatibility_analysis = {"decision": "insufficient"}
        commercial_state = ""

        def _call_structured_model(self, _prompt, _metadata, *, stage, **_kwargs):
            if stage == "technical_evidence_graph":
                return _graph()
            if stage == "technical_resolution_round_1":
                return _resolution("conditional", body)
            raise RuntimeError("final unavailable")

    first, final, statuses = resolve_technical_question(
        FakeClient(),
        {"category": "compatibility"},
        _plan(),
        {"question": {"text": "Serve?"}},
    )

    assert first.contingency_answer_body == body
    assert final.contingency_answer_body == body
    assert final.final is True
    assert final.round == 2
    assert final.overall_decision == "insufficient"
    assert final.commercial_state == "insufficient"
    assert final.contract_valid is False
    assert statuses["final"] == "error"


def test_final_failure_after_gap_never_promotes_round_one_yes_to_cta_state() -> None:
    gap_calls = []

    class FakeClient:
        compatibility_analysis = {"decision": "insufficient"}
        commercial_state = ""

        def _call_structured_model(self, _prompt, _metadata, *, stage, **_kwargs):
            if stage == "technical_evidence_graph":
                return _graph()
            if stage == "technical_resolution_round_1":
                return _resolution("yes", "Corpo factual da rodada um")
            raise RuntimeError("final unavailable")

    def gap_research(first):
        gap_calls.append(first)
        return ({"research_metrics": {"coverage_complete": True}}, [], "completed")

    first, final, statuses = resolve_technical_question(
        FakeClient(),
        {"category": "compatibility"},
        _plan(),
        {"research_metrics": {"coverage_complete": False}},
        gap_research_callback=gap_research,
    )

    assert first.overall_decision == "yes"
    assert len(gap_calls) == 1
    assert final.contingency_answer_body == "Corpo factual da rodada um"
    assert final.overall_decision == "insufficient"
    assert final.commercial_state == "insufficient"
    assert final.compatibility_analysis["decision"] == "insufficient"
    assert final.contract_valid is False
    assert statuses["gap_research"] == "completed"
    assert statuses["final"] == "error"


def test_structured_final_uses_sol_high_and_does_not_resume_thread(monkeypatch) -> None:
    captured = {}

    def fake_call(_client_id, payload, model_req):
        captured["context"] = dict(payload.context)
        captured["model"] = model_req
        payload.context["_codex_thread_id_result"] = "should-not-be-adopted"
        return '{"overall_decision":"yes"}', "codex:gpt-5.6-sol"

    monkeypatch.setattr(clients, "_ia_agent_perguntas_chamar_modelo", fake_call)
    monkeypatch.setattr(clients, "_ia_raciocinio_perguntas_configurado", lambda: "medium")
    client = clients._PerguntasVertexGeminiV2Client(
        "tenant-a",
        "Loja A",
        "codex:gpt-5.4-mini",
        agent_input={
            "_codex_job_id": "job-1",
            "_codex_thread_id": "thread-original",
            "question": {"text": "Serve?"},
            "intent": {
                "intencao": "duvida_produto",
                "categoria": "product_feature",
                "categorias": ["product_feature"],
                "fluxo": "perguntas_anuncio",
                "confianca": 0.97,
                "flags": {
                    "usar_busca_web": True,
                    "usar_mercado_livre_anuncio": True,
                    "usar_bling": True,
                },
                "subperguntas": [{
                    "intent": "product_feature",
                    "question": "Serve?",
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
        },
    )

    payload = client._call_structured_model(
        "adjudique",
        {"category": "compatibility"},
        stage="technical_resolution_final",
        isolated=True,
    )

    assert payload["overall_decision"] == "yes"
    assert captured["model"] == "codex:gpt-5.6-sol"
    assert captured["context"]["_codex_reasoning_effort"] == "high"
    assert captured["context"]["_codex_thread_id"] == ""
    assert captured["context"]["_codex_persist_thread"] is False
    assert captured["context"]["_codex_active_turn_key"] == ""
    assert captured["context"]["_codex_conversation_key"] == ""
    assert client.codex_thread_id == "thread-original"


def test_grounding_collection_does_not_commit_compatibility_state() -> None:
    client = SimpleNamespace(
        agent_input={},
        compatibility_analysis={"decision": "yes", "sentinel": "untouched"},
        _compatibility_queries=[],
        _compatibility_sources=[],
        _compatibility_grounding={},
    )
    before = copy.deepcopy(client.compatibility_analysis)
    empty_result = {
        "function": "web_search_question_context",
        "arguments": {"queries": []},
        "result": {"found": False, "context": ""},
    }

    _prepare_grounding(client, [empty_result], empty_result, empty_result)

    assert client.compatibility_analysis == before


def test_general_fit_uses_final_resolution_and_commits_once() -> None:
    class FakeClient:
        def __init__(self):
            self.agent_input = {
                "question": {"text": "Esse interruptor vai na carcaça da válvula?"},
                "subquestions": [],
                "item": {"title": "Interruptor ventoinha Honda Fit"},
            }
            self.loja = "Loja A"
            self.client_id = "tenant-a"
            self.context_pipeline = []
            self.compatibility_analysis = {"decision": "insufficient"}
            self.commercial_state = ""
            self._compatibility_queries = []
            self._compatibility_sources = []
            self._compatibility_grounding = {}
            self.calls = []

        def _call_structured_model(self, _prompt, _metadata, *, stage, isolated=False, **_kwargs):
            self.calls.append((stage, isolated))
            if stage == "technical_evidence_graph":
                return _graph()
            if stage == "technical_resolution_round_1":
                return _resolution("insufficient", "Rascunho inicial")
            return _resolution(
                "yes",
                "Resposta técnica final literal",
                round_number=2,
                final=True,
            )

        def _tool_segura(self, _name, callback):
            return callback()

    client = FakeClient()
    assessment, payload, alternative = _evaluate_general_fit_and_alternative(
        client,
        {"category": "product_feature", "item_id": "MLB1"},
        {},
        {"result": {"found": True, "context": "fonte técnica"}},
        SimpleNamespace(alternative_tool=None),
        {"product_feature"},
        regulated=False,
        question_plan=_plan(),
    )

    assert assessment.answer == "Resposta técnica final literal"
    assert payload["overall_decision"] == "yes"
    assert client.compatibility_analysis["decision"] == "yes"
    assert client.commercial_state == "fits"
    assert alternative["result"]["searched"] is False
    assert client.calls == [
        ("technical_evidence_graph", False),
        ("technical_resolution_round_1", False),
        ("technical_resolution_final", True),
    ]
    assert [step["name"] for step in client.context_pipeline] == [
        "technical_evidence_graph",
        "technical_resolution_round_1",
        "technical_evidence_graph_final",
        "technical_resolution_final",
        "commercial_fit_evaluation",
        "same_store_technically_verified_alternative",
    ]


def test_gap_queries_trigger_real_second_research_and_rebuild_graph() -> None:
    calls = []
    gap_result = {"result": {"research_passages": [{
        "passage_id": "gap-passage",
        "text": "achado decisivo",
        "source_ref": "manual-oficial",
    }]}}

    class FakeClient:
        compatibility_analysis = {"decision": "insufficient"}
        commercial_state = ""

        def _call_structured_model(self, prompt, _metadata, *, stage, tool_results, isolated=False):
            calls.append((stage, isolated, list(tool_results), prompt))
            if stage == "technical_evidence_graph":
                return _graph()
            if stage == "technical_resolution_round_1":
                return _resolution(
                    "insufficient",
                    "Rascunho inicial",
                    gap_queries=[{
                        "type": "official_manual",
                        "query": "manual oficial local de instalação",
                        "requirement_ids": ["q1"],
                    }],
                )
            assert stage == "technical_resolution_final"
            assert isolated is True
            assert gap_result in tool_results
            assert "achado decisivo" in prompt
            return _resolution(
                "yes",
                "Resposta depois da segunda pesquisa",
                round_number=2,
                final=True,
            )

    def gap_research(first):
        assert first.gap_queries[0]["query"] == "manual oficial local de instalação"
        calls.append(("real_gap_research", False, [gap_result], ""))
        return (
            {"question": {"text": "Serve?"}, "gap_research": gap_result},
            [gap_result],
            "completed",
        )

    _first, final, statuses = resolve_technical_question(
        FakeClient(),
        {"category": "compatibility"},
        _plan(),
        {"question": {"text": "Serve?"}},
        gap_research_callback=gap_research,
    )

    assert final.overall_decision == "yes"
    assert statuses["gap_research"] == "completed"
    assert statuses["final_evidence_graph"] == "completed"
    assert [item[0] for item in calls] == [
        "technical_evidence_graph",
        "technical_resolution_round_1",
        "real_gap_research",
        "technical_evidence_graph",
        "technical_resolution_final",
    ]


def test_resolution_contract_requires_schema_round_final_and_requirement_coverage() -> None:
    plan = _plan()
    valid = _resolution("yes", "Resposta técnica")

    assert normalize_technical_resolution(
        valid,
        plan=plan,
        round_number=1,
        final=False,
    ).contract_valid is True

    invalid_payloads = []
    for key, value in (
        ("schema", "schema-errado"),
        ("round", 2),
        ("final", True),
        ("requirements", []),
        ("requirements", [{key: value for key, value in valid["requirements"][0].items() if key != "id"}]),
        ("requirements", [{**valid["requirements"][0], "id": "outra-origem"}]),
    ):
        payload = copy.deepcopy(valid)
        payload[key] = value
        invalid_payloads.append(payload)

    assert all(
        normalize_technical_resolution(
            payload,
            plan=plan,
            round_number=1,
            final=False,
        ).contract_valid is False
        for payload in invalid_payloads
    )


def test_positive_final_preserves_ai_adjudication_without_rewriting_body() -> None:
    plan = _plan()
    body = "  Corpo factual preservado byte a byte.\n\nEquipe Loja agradece!  "

    for requested_state in ("fits", "variant"):
        payload = _resolution(
            "yes",
            body,
            state=requested_state,
            round_number=2,
            final=True,
        )
        payload["requirements"][0]["decision"] = "insufficient"
        payload["requirements"][0]["missing_fields"] = ["codigo OEM"]

        normalized = normalize_technical_resolution(
            payload,
            plan=plan,
            round_number=2,
            final=True,
        )

        assert normalized.overall_decision == "yes"
        assert normalized.contract_valid is True
        assert normalized.commercial_state == requested_state
        assert normalized.contingency_answer_body == body


def test_incomplete_coverage_and_conflicts_each_trigger_followup() -> None:
    for material in (
        {"research_metrics": {"coverage_complete": False}},
        {"product_research_evidence": [{"state": "conflict", "conflict_group": "fitment"}]},
    ):
        callback_calls = []

        class FakeClient:
            compatibility_analysis = {"decision": "insufficient"}
            commercial_state = ""

            def _call_structured_model(self, _prompt, _metadata, *, stage, **_kwargs):
                if stage == "technical_evidence_graph":
                    return _graph()
                if stage == "technical_resolution_round_1":
                    return _resolution("yes", "Rodada um")
                return _resolution(
                    "yes",
                    "Decisão final",
                    round_number=2,
                    final=True,
                )

        def gap_research(first):
            callback_calls.append(first)
            return ({"research_metrics": {"coverage_complete": True}}, [], "completed")

        first, final, statuses = resolve_technical_question(
            FakeClient(),
            {"category": "compatibility"},
            _plan(),
            material,
            gap_research_callback=gap_research,
        )

        assert first.overall_decision == "yes"
        assert final.overall_decision == "yes"
        assert len(callback_calls) == 1
        assert callback_calls[0].gap_queries == _plan().queries
        assert statuses["gap_research"] == "completed"


def test_round_one_error_and_invalid_json_each_trigger_followup() -> None:
    for first_result in (RuntimeError("round one failed"), {}):
        callback_calls = []

        class FakeClient:
            compatibility_analysis = {"decision": "insufficient"}
            commercial_state = ""

            def _call_structured_model(self, _prompt, _metadata, *, stage, **_kwargs):
                if stage == "technical_evidence_graph":
                    return _graph()
                if stage == "technical_resolution_round_1":
                    if isinstance(first_result, Exception):
                        raise first_result
                    return first_result
                return _resolution(
                    "yes",
                    "Recuperada após nova pesquisa",
                    round_number=2,
                    final=True,
                )

        def gap_research(first):
            callback_calls.append(first)
            return ({"research_metrics": {"coverage_complete": True}}, [], "completed")

        first, final, statuses = resolve_technical_question(
            FakeClient(),
            {"category": "compatibility"},
            _plan(),
            {"research_metrics": {"coverage_complete": True}},
            gap_research_callback=gap_research,
        )

        assert first.contract_valid is False
        assert final.contract_valid is True
        assert final.overall_decision == "yes"
        assert len(callback_calls) == 1
        assert callback_calls[0].gap_queries == _plan().queries
        assert statuses["round_1"] == (
            "error" if isinstance(first_result, Exception) else "invalid"
        )
        assert statuses["gap_research"] == "completed"


def test_followup_helper_reads_incomplete_coverage_and_conflict_material() -> None:
    resolution = normalize_technical_resolution(
        _resolution("yes", "Resolvida"),
        plan=_plan(),
        round_number=1,
        final=False,
    )

    assert technical_resolution_needs_followup(
        resolution,
        {"coverage_complete": False},
    ) is True
    assert technical_resolution_needs_followup(
        resolution,
        {"conflicts": 1},
    ) is True
