from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.perguntas_pos_venda.ai import deep_research_analysis
from backend.modules.perguntas_pos_venda.ai import deep_research_contracts
from backend.modules.perguntas_pos_venda.ai import deep_research_crawler
from backend.modules.perguntas_pos_venda.ai import deep_research_documents
from backend.modules.perguntas_pos_venda.ai import client_general_workflow
from backend.modules.perguntas_pos_venda.ai import client_workflows
from backend.modules.perguntas_pos_venda.ai import inputs as research_inputs
from backend.modules.perguntas_pos_venda.ai import queries as research_queries
from backend.modules.perguntas_pos_venda.ai import sources as research_sources
from backend.modules.perguntas_pos_venda.ai.deep_research_contracts import (
    PRODUCT_EVIDENCE_POLICY,
    PUBLIC_RESEARCH_POLICY,
    ResearchSessionV1,
)
from backend.modules.perguntas_pos_venda.ai.runtime import AIAnswer


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "black_jhon_honda_fit_v16.json"


@pytest.fixture
def honda_fit_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _plain(value: object) -> str:
    return research_queries._normalizar_texto(str(value or ""))


@pytest.mark.parametrize("category", ["warranty_originality", "other_product"])
def test_v16_all_nonregulated_public_categories_plan_vision_and_resolve(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
) -> None:
    events: list[str] = []
    plan_payload = {
        "requirements": [{"id": "requirement-1"}],
        "queries": [{"query": "consulta tecnica"}],
    }
    plan = SimpleNamespace(
        requirements=(plan_payload["requirements"][0],),
        queries=(plan_payload["queries"][0],),
        to_dict=lambda: deepcopy(plan_payload),
    )

    monkeypatch.setattr(
        client_general_workflow,
        "_collect_general_internal_sources",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        client_general_workflow,
        "_prepare_general_technical_plan",
        lambda *_args, **_kwargs: (events.append("plan") or (plan, "completed")),
    )

    def resolve(*_args, **_kwargs):
        events.append("resolver")
        return (
            AIAnswer(answer="Rascunho tecnico.", confidence=0.4),
            {"commercial_state": "insufficient", "compatibility_analysis": {"decision": "insufficient"}},
            {"result": {"found": False, "read_only": True}},
        )

    monkeypatch.setattr(
        client_general_workflow,
        "_evaluate_general_fit_and_alternative",
        resolve,
    )
    monkeypatch.setattr(
        client_workflows,
        "_mandatory_web_tool",
        lambda *_args, **_kwargs: (
            events.append("web")
            or {"function": "web_search_question_context", "result": {"found": False}}
        ),
    )
    monkeypatch.setattr(
        client_workflows,
        "_prepare_document_vision",
        lambda *_args, phase, **_kwargs: events.append(f"vision:{phase}") or [],
    )
    monkeypatch.setattr(
        client_workflows,
        "_perguntas_ia_context_hub_deve_buscar",
        lambda *_args, **_kwargs: False,
    )

    client = SimpleNamespace(
        client_id="tenant-fixture",
        loja="Loja Fixture",
        agent_input={
            "question": {"text": "Pergunta publica de pre-venda"},
            "intent": {"categoria": category, "categorias": [category]},
        },
        context_pipeline=[],
        compatibility_analysis={"decision": "insufficient"},
        commercial_state="insufficient",
        _call_model=lambda *_args, stage, **_kwargs: (
            events.append(stage)
            or AIAnswer(answer="Resposta final literal.", confidence=0.4)
        ),
    )
    bindings = client_workflows.GeneralBindings(
        context_hub_tool=lambda *_args, **_kwargs: {},
        web_tool=lambda *_args, **_kwargs: {},
    )

    answer = client_workflows.run_general(
        client,
        "prompt base",
        {"category": category, "item_id": "MLB-FIXTURE"},
        bindings,
    )

    assert answer.answer == "Resposta final literal."
    if category == "other_product":
        assert events == ["adaptive_simple_public_answer"]
        assert "question_plan" not in client.agent_input
    else:
        assert events == ["plan", "web", "vision:initial", "resolver", "external_research_final"]
        assert client.agent_input["question_plan"] == plan_payload


def test_v16_policy_and_honda_fit_plan_reach_search_before_fallback(
    honda_fit_fixture: dict,
) -> None:
    assert PRODUCT_EVIDENCE_POLICY == "jk_product_evidence_v2"
    assert PUBLIC_RESEARCH_POLICY == "jk_black_jhon_research_v3"
    agent_input = deepcopy(honda_fit_fixture["agent_input"])
    agent_input["gap_queries"] = []
    agent_input["research_attempt"] = 1

    projected = research_inputs._perguntas_ia_research_input(agent_input)
    generated = research_queries._ia_agent_perguntas_queries_web(projected, [])

    assert projected["technical_question_plan"]["queries"]
    assert "question_plan" not in projected
    assert generated[0]["research_phase"] == "plan"
    assert generated[1]["research_phase"] == "plan"
    first = _plain(generated[0]["query"])
    assert "37760-P00-003" in first
    assert "HONDA FIT 1.4" in first
    assert all(year in first for year in ("2003", "2004", "2005"))
    assert "CARCACA DA VALVULA TERMOSTATICA" in first
    phases = [value["research_phase"] for value in generated]
    assert phases[:2] == ["plan", "plan"]
    assert "fallback" in phases[2:]


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        ("codigo OEM 55212345 bomba combustivel Fiat", "55212345"),
        ("part number 0281002437 sensor Bosch", "0281002437"),
        ("referencia 377600003 Honda Fit 1.4 2003 2004", "377600003"),
    ],
)
def test_v16_public_query_preserves_explicit_numeric_part_numbers(
    raw: str,
    expected_code: str,
) -> None:
    sanitized = research_inputs._perguntas_ia_v2_texto_classificado_busca(
        raw,
        max_palavras=36,
        max_chars=260,
    )

    assert expected_code in sanitized


@pytest.mark.parametrize(
    "raw",
    [
        "telefone 31987654321",
        "WhatsApp: 31987654321",
        "contato 31987654321",
        "telefone codigo 31987654321",
        "codigo 31987654321 WhatsApp",
    ],
)
def test_v16_numeric_part_number_protection_never_exposes_labeled_phone(raw: str) -> None:
    sanitized = research_inputs._perguntas_ia_v2_texto_classificado_busca(
        raw,
        max_palavras=36,
        max_chars=260,
    )

    assert "31987654321" not in re.sub(r"\D", "", sanitized)


def test_v16_resolution_gap_becomes_a_real_gap_only_query_call(
    honda_fit_fixture: dict,
) -> None:
    agent_input = deepcopy(honda_fit_fixture["agent_input"])
    agent_input["research_attempt"] = 2

    assert "37760-PWA-J01" in json.dumps(honda_fit_fixture, ensure_ascii=False)

    projected = research_inputs._perguntas_ia_research_input(agent_input)
    generated = research_queries._ia_agent_perguntas_queries_web(projected, [])

    assert projected["research_gap_only"] is True
    assert generated
    assert {value["research_phase"] for value in generated} == {"gap"}
    assert "37760-PHM-004" in _plain(generated[0]["query"])
    assert "37760-P00-003" in _plain(generated[0]["query"])
    assert "37760 PWA J01" not in _plain(generated)


def test_v16_session_partitions_two_calls_into_8_plus_4_queries_and_35_plus_15_pages() -> None:
    now = time.monotonic()
    session = ResearchSessionV1(
        key="partitioned-v16",
        started_monotonic=now,
        expires_monotonic=now + 600,
    )
    initial = session.reserve_queries([
        {
            "type": "technical_plan",
            "query": f"Honda Fit technical initial query {index}",
            "research_phase": "plan",
        }
        for index in range(10)
    ])
    gaps = session.reserve_queries([
        {
            "type": "technical_gap",
            "query": f"Honda Fit technical gap query {index}",
            "research_phase": "gap",
        }
        for index in range(6)
    ])
    assert len(initial) == 8
    assert len(gaps) == 4
    assert len(session.queries_used) == 12

    initial_pages = sum(
        session.reserve_page(
            f"https://initial-{index // 5}.example/parts/diagram-{index}.pdf",
            research_phase="initial",
        )
        for index in range(40)
    )
    gap_pages = sum(
        session.reserve_page(
            f"https://gap-{index // 5}.example/parts/diagram-{index}.pdf",
            research_phase="gap",
        )
        for index in range(20)
    )
    assert initial_pages == 35
    assert gap_pages == 15
    assert session.pages_used == 50
    assert session.phase_deadline_monotonic("initial") <= now + 225.01
    assert session.phase_deadline_monotonic("gap") <= time.monotonic() + 75.01


def test_v16_initial_and_gap_reuse_transient_session_without_codex_job_id() -> None:
    with deep_research_contracts._SESSION_LOCK:
        deep_research_contracts._SESSIONS.clear()
    base = {
        "tenant_id": "tenant-fixture",
        "store": "store-fixture",
        "_research_session_key": "research-fixture-01",
        "question": {"id": "question-fixture", "item_id": "item-fixture"},
        "item": {"id": "item-fixture"},
        "product_evidence_identity": {"seller_id": "seller-fixture", "site_id": "MLB"},
    }
    initial = deep_research_contracts.research_session(base)
    initial.reserve_queries([
        {"query": f"initial technical query {index}", "research_phase": "plan"}
        for index in range(8)
    ])
    gap = deep_research_contracts.research_session(dict(base, research_gap_only=True))
    accepted = gap.reserve_queries([
        {"query": f"gap technical query {index}", "research_phase": "gap"}
        for index in range(5)
    ])
    assert gap is initial
    assert len(accepted) == 4
    assert len(gap.queries_used) == 12


def test_v16_missing_fields_without_model_queries_use_gap_bucket_and_new_strategy() -> None:
    projected = research_inputs._perguntas_ia_research_input({
        "research_attempt": 2,
        "force_external_research": True,
        "research_gaps": ["local de instalacao", "relacao entre codigos"],
        "technical_question_plan": {
            "requirements": [],
            "queries": [{
                "type": "installation_location_by_code",
                "query": '"37760-P00-003" Honda Fit 1.4 2003 2004 2005 carcaça válvula termostática',
            }],
        },
    })

    assert projected["research_gap_only"] is True
    generated = research_queries._ia_agent_perguntas_queries_web(projected, [])
    assert generated
    assert {item["research_phase"] for item in generated} == {"gap"}
    normalized = _plain(generated[0]["query"])
    assert "37760-P00-003" in normalized
    assert "HONDA FIT 1.4" in normalized
    assert "CATALOGO OEM" in normalized
    assert "DIAGRAMA EXPLODIDO" in normalized


@pytest.mark.parametrize("original_has_hits", [True, False])
def test_v16_relaxes_only_after_the_original_provider_search_is_empty(
    original_has_hits: bool,
) -> None:
    query = '"37760-P00-003" Honda Fit thermostat housing diagram'
    raw_queries = [{
        "type": "installation_location_by_code",
        "query": query,
        "research_phase": "plan",
    }]
    session = ResearchSessionV1(
        key=f"relax-{original_has_hits}",
        started_monotonic=time.monotonic(),
        expires_monotonic=time.monotonic() + 600,
    )
    accepted = session.reserve_queries(raw_queries)
    variants = {query: [query]}
    calls: list[list[str]] = []

    def prefetch(_client_id, values, _search, **_kwargs):
        calls.append(list(values))
        if len(calls) == 1 and original_has_hits:
            return {values[0]: [{"url": "https://maker.example/parts/diagram.pdf"}]}
        return {value: [] for value in values}

    callbacks = deep_research_crawler.DeepResearchCallbacks(
        reserve_queries=lambda *_args: (session, accepted, variants),
        prefetch_web=prefetch,
        select_items=lambda *_args: ("", "", []),
        read_batch=lambda *_args, **_kwargs: ({}, {}, False),
        discover_links=lambda *_args: [],
        render_results=lambda *_args: [],
        render_listings=lambda *_args: [],
    )
    _prefetch, all_accepted, relaxed_count = deep_research_crawler._prefetch_reserved_queries(
        "tenant-fixture",
        accepted,
        variants,
        session=session,
        search=lambda *_args, **_kwargs: [],
        callbacks=callbacks,
        deadline_monotonic=time.monotonic() + 10,
    )

    assert calls[0] == [query]
    if original_has_hits:
        assert len(calls) == 1
        assert relaxed_count == 0
        assert len(all_accepted) == 1
        assert variants[query] == [query]
    else:
        assert len(calls) == 2
        assert calls[1][0] != query
        assert relaxed_count == 1
        assert variants[query] == [query, calls[1][0]]


def test_v16_exact_code_diagram_ranks_first_and_navigation_pages_are_excluded(
    honda_fit_fixture: dict,
) -> None:
    query = honda_fit_fixture["agent_input"]["question_plan"]["queries"][0]["query"]
    raw_results = json.dumps(honda_fit_fixture["search_results"], ensure_ascii=False).lower()
    assert "37760-pwa-j01" in raw_results
    assert "mercadolivre.com.br" in raw_results
    assert "/support/" in raw_results
    assert "/contact/" in raw_results
    used, query_type, items = research_sources._ia_agent_perguntas_itens_web(
        {"type": "installation_location_by_code", "query": query},
        {query: honda_fit_fixture["search_results"]},
        set(),
    )

    assert used == query
    assert query_type == "installation_location_by_code"
    assert items[0][1].endswith("thermostat-housing-diagram.pdf")
    rendered_urls = " ".join(url for _item, url in items).lower()
    assert all(
        marker not in rendered_urls
        for marker in ("/help", "/contact", "/policy", "/login", "/sales")
    )
    assert "/support/" in rendered_urls
    assert "/support/" not in items[0][1].lower()
    assert "37760-pwa-j01" not in json.dumps(items[0]).lower()


def test_v16_honda_document_yields_structured_passages_location_assembly_and_supersession(
    honda_fit_fixture: dict,
) -> None:
    agent_input = honda_fit_fixture["agent_input"]
    source = honda_fit_fixture["document"]
    assert "37760-PWA-J01" in source["text"]
    document = deep_research_documents.make_research_document(
        item={"title": source["title"], "snippet": "technical catalog datasheet"},
        url=source["url"],
        query=agent_input["question_plan"]["queries"][0]["query"],
        query_type="installation_location_by_code",
        text=source["text"],
        agent_input=agent_input,
    )

    assert document is not None
    assert document.claim_eligible is True
    passage_types = {passage.passage_type for passage in document.passages}
    assert {"code_context", "section", "table"} <= passage_types
    assert any("37760-P00-003" in passage.codes for passage in document.passages)
    claims = deep_research_analysis.extract_technical_claims(document.text)
    by_field = {claim.field_name: claim for claim in claims}
    assert by_field["installation.location"].value.lower() == "thermostat housing"
    assert "SWITCH ASSY" in by_field["installation.assembly"].value
    assert by_field["relation.superseded_by.37760_phm_004"].value == "37760-P00-003"
    assert by_field["relation.installed_in.37760_p00_003"].value.lower() == "thermostat housing"
    assert {
        claim.field_name for claim in claims if claim.field_name.startswith("relation.")
    } == {
        "relation.superseded_by.37760_phm_004",
        "relation.installed_in.37760_p00_003",
    }
    assert all("PWA" not in claim.field_name.upper() and "PWA" not in claim.value.upper() for claim in claims)


def test_v16_passages_are_bounded_prompt_projections_and_persistence_receives_no_raw_body_fields(
    monkeypatch: pytest.MonkeyPatch,
    honda_fit_fixture: dict,
) -> None:
    agent_input = honda_fit_fixture["agent_input"]
    source = honda_fit_fixture["document"]
    assert "37760-PWA-J01" in source["text"]
    synthetic_vin = "8AD2MKFWXCG035615"
    synthetic_email = "comprador@example.invalid"
    synthetic_phone = "+55 11 99999-8888"
    tainted_text = "\n".join([
        "PAGE 42",
        source["text"],
        f"VIN: {synthetic_vin}",
        f"E-mail: {synthetic_email}",
        f"WhatsApp: {synthetic_phone}",
        "UNTRUSTED_RAW_BODY_MUST_NOT_BE_PERSISTED",
    ])
    document = deep_research_documents.make_research_document(
        item={"title": source["title"], "snippet": "technical catalog datasheet"},
        url=source["url"],
        query=agent_input["question_plan"]["queries"][0]["query"],
        query_type="installation_location_by_code",
        text=tainted_text,
        agent_input=agent_input,
    )
    assert document is not None

    source_payloads: list[dict] = []
    claim_payloads: list[dict] = []
    monkeypatch.setattr(
        deep_research_documents,
        "add_product_evidence_source",
        lambda _client, _batch, **kwargs: (
            source_payloads.append(dict(kwargs)) or {"source_id": "source-fixture"}
        ),
    )
    monkeypatch.setattr(
        deep_research_documents,
        "add_product_evidence_claim",
        lambda *_args, **kwargs: claim_payloads.append(dict(kwargs)) or {},
    )
    deep_research_documents._persist_document_claims(
        "tenant-fixture",
        "batch-fixture",
        [document],
        agent_input,
    )
    assert source_payloads
    assert all(
        key not in payload
        for payload in source_payloads
        for key in ("text", "body", "html", "snippet", "passages")
    )
    assert source_payloads[0]["section_ref"] == (
        "p. 42 | SECTION: THERMOSTAT HOUSING | FIGURE 12 - COOLING SYSTEM"
    )
    assert source_payloads[0]["section_ref"] != "installation_location_by_code"
    relations = {
        payload["field_name"]: payload
        for payload in claim_payloads
        if str(payload.get("field_name") or "").startswith("relation.")
    }
    assert relations["relation.superseded_by.37760_phm_004"] == {
        "field_name": "relation.superseded_by.37760_phm_004",
        "scope": "product",
        "value": "37760-P00-003",
        "unit": None,
        "source_ids": ["source-fixture"],
    }
    assert relations["relation.installed_in.37760_p00_003"]["source_ids"] == [
        "source-fixture"
    ]
    persisted_projection = json.dumps(
        {"sources": source_payloads, "claims": claim_payloads},
        ensure_ascii=False,
    )
    for forbidden in (
        synthetic_vin,
        synthetic_email,
        synthetic_phone,
        "UNTRUSTED_RAW_BODY_MUST_NOT_BE_PERSISTED",
        "RAW_PAGE_TAIL_SHOULD_NOT_BE_PROJECTED",
    ):
        assert forbidden not in persisted_projection
    assert "_PROTEGIDO]" not in persisted_projection.upper()

    monkeypatch.setattr(
        deep_research_documents,
        "load_product_research_evidence",
        lambda *_args, **_kwargs: [],
    )
    result = deep_research_documents.finalize_research_evidence_result(
        "tenant-fixture",
        agent_input,
        [document],
        [],
        {"stop_reason": "no_new_facts", "coverage_complete": False},
        [],
        persist=lambda *_args, metrics, **_kwargs: ([], dict(metrics)),
    )
    passages = result["research_passages"]
    assert passages
    assert all(len(value["text"]) <= 1200 for value in passages)
    rendered = json.dumps(passages, ensure_ascii=False)
    assert "RAW_PAGE_TAIL_SHOULD_NOT_BE_PROJECTED" not in rendered
    assert synthetic_vin not in rendered
    assert synthetic_email not in rendered
    assert synthetic_phone not in rendered
    assert "37760-PWA-J01" in rendered
    assert all(
        "PWA" not in str(payload.get("field_name") or "").upper()
        and "PWA" not in str(payload.get("value") or "").upper()
        for payload in claim_payloads
    )
    assert "buyer" not in rendered.lower()
