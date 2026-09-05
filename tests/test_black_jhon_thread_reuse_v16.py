from backend.services import perguntas_pos_venda_codex as orchestrator


def _current_job(**overrides):
    value = {
        "thread_id": "thread-current",
        "prompt_version": orchestrator.PROMPT_VERSION,
        "schema_version": orchestrator.SCHEMA_VERSION,
        "prompt_hash": orchestrator.PROMPT_HASH,
        "updated_at": orchestrator._now(),
        "scope_verifiers": {},
    }
    value.update(overrides)
    return value


def test_rejected_public_draft_never_reuses_operational_thread():
    result = orchestrator._thread_reuse_decision(
        _current_job(verification={"status": "rejected", "confirmed": False}),
        task_type=orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        scope_verifiers={},
    )

    assert result == ("", "previous_draft_rejected", False)


def test_zero_fact_no_new_facts_research_never_reuses_operational_thread():
    result = orchestrator._thread_reuse_decision(
        _current_job(result={
            "contexto": {
                "diagnostics": [{
                    "result": {
                        "research_metrics": {
                            "fields_confirmed": 0,
                            "coverage_complete": False,
                            "stop_reason": "no_new_facts",
                        }
                    }
                }]
            }
        }),
        task_type=orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        scope_verifiers={},
    )

    assert result == ("", "zero_research_facts", False)


def test_zero_fact_timeout_research_never_reuses_operational_thread():
    result = orchestrator._thread_reuse_decision(
        _current_job(result={
            "research_metrics": {
                "fields_confirmed": 0,
                "coverage_complete": False,
                "stop_reason": "deadline",
            }
        }),
        task_type=orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        scope_verifiers={},
    )

    assert result == ("", "zero_research_facts", False)


def test_partial_or_successful_research_keeps_healthy_thread_continuity():
    result = orchestrator._thread_reuse_decision(
        _current_job(result={
            "contexto": {
                "research_metrics": {
                    "fields_confirmed": 1,
                    "coverage_complete": False,
                    "stop_reason": "no_new_facts",
                }
            }
        }),
        task_type=orchestrator.TASK_TYPE_PUBLIC_QUESTION,
        scope_verifiers={},
    )

    assert result == ("thread-current", "", True)


def test_candidate_or_conflict_research_fact_keeps_healthy_thread_continuity():
    for state in ("candidate", "conflict"):
        result = orchestrator._thread_reuse_decision(
            _current_job(result={
                "contexto": {
                    "research_metrics": {
                        "fields_confirmed": 0,
                        "coverage_complete": False,
                        "stop_reason": "no_new_facts",
                    },
                    "product_research_evidence": [{
                        "field_name": "compatibility.application",
                        "value": "Honda Fit 1.4 2003",
                        "state": state,
                    }],
                }
            }),
            task_type=orchestrator.TASK_TYPE_PUBLIC_QUESTION,
            scope_verifiers={},
        )

        assert result == ("thread-current", "", True)


def test_v17_contract_changes_prompt_hash_and_keeps_public_schema():
    assert orchestrator.PROMPT_VERSION == "jk_ml_customer_reply_codex_v17"
    assert orchestrator.SCHEMA_VERSION == "5.2"
    assert orchestrator.PRODUCT_EVIDENCE_POLICY == "jk_product_evidence_v2"
    assert orchestrator.TECHNICAL_EVIDENCE_GRAPH_VERSION == "jk_ml_evidence_graph_v2"
    assert orchestrator.FACTUAL_REVIEW_VERSION == "jk_ml_factual_review_v1"
    assert orchestrator.FACTUAL_CRITIC_POLICY == "jk_black_jhon_factual_critic_v1"
    assert orchestrator.PUBLIC_RESEARCH_POLICY == "jk_black_jhon_research_v3"
    assert orchestrator.MAX_SECONDS == orchestrator.PUBLIC_RESEARCH_DEADLINE_SECONDS
