from __future__ import annotations

from backend.modules.perguntas_pos_venda.ai.document_vision import (
    collect_document_vision_batch,
)
from backend.modules.perguntas_pos_venda.ai.technical_planning import (
    TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
    build_technical_evidence_graph,
)
from backend.modules.perguntas_pos_venda.ai.technical_resolution import (
    TECHNICAL_QUESTION_PLAN_SCHEMA,
    TECHNICAL_RESOLUTION_SCHEMA,
    build_technical_question_plan,
    normalize_technical_question_plan,
    resolve_technical_question,
)


def _plan():
    return normalize_technical_question_plan({
        "schema": TECHNICAL_QUESTION_PLAN_SCHEMA,
        "requirements": [{
            "id": "q1",
            "essential": True,
            "kind": "installation_location",
            "question": "A peca e instalada na carcaca da valvula termostatica?",
            "subject": {
                "kind": "product",
                "name": "interruptor",
                "identifiers": ["37760-P00-003"],
            },
            "target": {
                "kind": "component",
                "name": "carcaca da valvula termostatica",
                "identifiers": [],
            },
            "relation": "installed_in",
            "required_fields": ["installation.location"],
            "search_terms": ["Honda Fit 1.4 2003 2004 2005"],
        }],
        "queries": [{
            "type": "installation_location_by_code",
            "query": "37760-P00-003 Honda Fit 1.4 carcaca valvula termostatica",
            "requirement_ids": ["q1"],
        }],
    })


def _insufficient_round_one(body: str) -> dict:
    return {
        "schema": TECHNICAL_RESOLUTION_SCHEMA,
        "round": 1,
        "final": False,
        "requirements": [{
            "id": "q1",
            "decision": "insufficient",
            "conclusion": "O diagrama ainda nao pode ser lido.",
            "condition": "",
            "commercial_impact": "unknown",
            "facts": [],
            "missing_fields": ["installation.location"],
            "confidence": 0.2,
        }],
        "reference_relations": [],
        "overall_decision": "insufficient",
        "commercial_state": "insufficient",
        "confidence": 0.2,
        "reason": "vision_unavailable",
        "gap_queries": [],
        "contingency_answer_body": body,
        "compatibility_analysis": {
            "decision": "insufficient",
            "missing_fields": ["installation.location"],
        },
    }


def test_planning_timeout_falls_back_without_losing_the_full_question() -> None:
    question = (
        "A peca e instalada na carcaca da valvula termostatica "
        "do Honda Fit 1.4 2003?"
    )

    class Client:
        def _call_structured_model(self, *_args, **_kwargs):
            raise TimeoutError("planning deadline")

    plan, status = build_technical_question_plan(
        Client(),
        {"category": "compatibility"},
        {"question": {"text": question}, "subquestions": [{"question": question}]},
    )

    assert status == "fallback"
    assert plan.model_generated is False
    assert [requirement["question"] for requirement in plan.requirements] == [question]
    assert plan.queries == ()


def test_vision_fetch_timeout_is_unprocessed_and_never_becomes_fact_absence() -> None:
    def timeout_fetch(*_args, **_kwargs):
        raise TimeoutError("vision fetch deadline")

    batch = collect_document_vision_batch(
        {"result": {"research_sources": ["https://catalog.example/fit-diagram.pdf"]}},
        focus_terms=("37760-P00-003", "thermostat housing"),
        fetch_pdf=timeout_fetch,
    )

    assert batch.attachments == ()
    assert batch.page_refs == ()
    assert batch.documents_attempted == 1
    assert batch.documents_processed == 0
    assert batch.documents_unprocessed == 1
    assert "absent" not in str(batch.diagnostics()).casefold()


def test_scanned_document_extraction_timeout_returns_invalid_graph_not_negative_fact() -> None:
    class Client:
        def _call_structured_model(self, *_args, **_kwargs):
            raise TimeoutError("multimodal OCR deadline")

    graph, status = build_technical_evidence_graph(
        Client(),
        {"category": "compatibility"},
        _plan(),
        {
            "document_vision_page_refs": [{
                "source_url": "https://catalog.example/scanned-diagram.pdf",
                "page": 12,
                "text_layer": False,
            }],
            "research_metrics": {"coverage_complete": False},
        },
    )

    assert status == "error"
    assert graph.contract_valid is False
    assert graph.claims == ()
    assert graph.relations == ()


def test_final_adjudication_timeout_preserves_round_one_body_and_disables_cta() -> None:
    body = "  Nao foi possivel concluir a leitura do diagrama.\r\n  "

    class Client:
        compatibility_analysis = {"decision": "insufficient"}
        commercial_state = ""

        def _call_structured_model(self, _prompt, _metadata, *, stage, **_kwargs):
            if stage == "technical_evidence_graph":
                return {
                    "schema": TECHNICAL_EVIDENCE_GRAPH_SCHEMA,
                    "entities": [],
                    "claims": [],
                    "passages": [],
                    "relations": [],
                    "unresolved_requirement_ids": ["q1"],
                }
            if stage == "technical_resolution_round_1":
                return _insufficient_round_one(body)
            if stage == "technical_resolution_final":
                raise TimeoutError("final adjudication deadline")
            raise AssertionError(stage)

    first, final, statuses = resolve_technical_question(
        Client(),
        {"category": "compatibility"},
        _plan(),
        {"research_metrics": {"coverage_complete": False}},
    )

    assert statuses["final"] == "error"
    assert first.contingency_answer_body == body
    assert final.contingency_answer_body == body
    assert final.overall_decision == "insufficient"
    assert final.commercial_state == "insufficient"
    assert final.contract_valid is False
