import json

from backend.services import codex_whatsapp_agents
from backend.services.whatsapp import black_jhon_prompting


def _legacy_decision(**overrides):
    payload = {
        "action": "reply",
        "reply_text": "Tudo certo.",
        "job_title": "",
        "job_prompt": "",
        "related_job_id": "",
        "needs_user_input": False,
        "requires_web": False,
        "resolved_context": {},
        "subtasks": [],
    }
    payload.update(overrides)
    return payload


def test_prompt_contract_v2_has_stable_hash_and_schemas():
    diagnostic = black_jhon_prompting.prompt_contract_diagnostics()

    assert diagnostic["version"] == "black-jhon-whatsapp-prompts.v2"
    assert len(diagnostic["hash"]) == 64
    int(diagnostic["hash"], 16)
    assert diagnostic["schemas"] == {
        "conversation_decision": "jk.whatsapp.conversation-decision.v2",
        "evidence_envelope": "jk.whatsapp.evidence-envelope.v2",
        "retrieval_result": "jk.whatsapp.retrieval-result.v2",
        "prompt_context": "jk.whatsapp.prompt-context.v2",
    }
    assert diagnostic["max_context_chars"] == 24_000
    assert {
        "schema_version",
        "intent",
        "response_mode",
        "missing_fields",
        "confidence",
        "task",
        "subtasks",
        "action",
        "resolved_context",
    } <= set(codex_whatsapp_agents.DECISION_SCHEMA["required"])
    assert {"field", "value", "store", "period", "source"} == set(
        black_jhon_prompting.EVIDENCE_ENVELOPE_V2_SCHEMA["properties"]["records"]["items"]["required"]
    )


def test_conversation_decision_v2_normalizes_legacy_and_native_payloads():
    legacy = codex_whatsapp_agents.normalize_decision(_legacy_decision(), event_type="user_message")
    native = codex_whatsapp_agents.normalize_decision(
        _legacy_decision(
            schema_version=black_jhon_prompting.CONVERSATION_DECISION_V2,
            action="delegate",
            intent="sku_stock",
            response_mode="task_delegation",
            missing_fields=["store"],
            confidence="high",
            task={
                "title": "Consultar estoque",
                "prompt": "Consulte o SKU 001.",
                "requires_web": False,
                "reasoning_effort": "low",
            },
        ),
        event_type="user_message",
    )

    assert legacy["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert legacy["intent"] == "conversation_reply"
    assert legacy["response_mode"] == "direct_reply"
    assert legacy["task"]["prompt"] == legacy["job_prompt"] == ""
    assert native["schema_version"] == black_jhon_prompting.CONVERSATION_DECISION_V2
    assert native["intent"] == "sku_stock"
    assert native["response_mode"] == "task_delegation"
    assert native["missing_fields"] == ["store"]
    assert native["confidence"] == "high"
    assert native["task"]["prompt"] == native["job_prompt"] == "Consulte o SKU 001."
    assert native["task"]["title"] == native["job_title"] == "Consultar estoque"
    assert native["needs_user_input"] is True


def test_evidence_envelope_v2_accepts_v1_and_prioritizes_native_fields():
    legacy = black_jhon_prompting.normalize_evidence_envelope_v2(
        {
            "status": "partial",
            "summary": "Resumo legado",
            "verified_facts": ["Fato V1"],
            "sources": ["Fonte V1"],
            "missing": ["Lacuna V1"],
            "confidence": "medium",
            "evidence_sufficient": False,
            "coverage_complete": False,
        }
    )
    native = black_jhon_prompting.normalize_evidence_envelope_v2(
        {
            "schema_version": black_jhon_prompting.EVIDENCE_ENVELOPE_V2,
            "status": "completed",
            "summary": "Resumo V2",
            "records": [
                {
                    "field": "stock",
                    "value": 7,
                    "store": "JK Pecas",
                    "period": "agora",
                    "source": "Fonte V2",
                }
            ],
            "facts": ["Fato V2"],
            "verified_facts": ["Nao deve substituir o V2"],
            "sources": ["Fonte V2"],
            "gaps": ["Lacuna V2"],
            "missing": ["Nao deve substituir o V2"],
            "confidence": "high",
            "evidence_sufficient": True,
            "coverage_complete": True,
        }
    )

    assert legacy["schema_version"] == black_jhon_prompting.EVIDENCE_ENVELOPE_V2
    assert legacy["records"] == [
        {"field": "fact", "value": "Fato V1", "store": "", "period": "", "source": "Fonte V1"}
    ]
    assert legacy["facts"] == legacy["verified_facts"] == ["Fato V1"]
    assert legacy["gaps"] == legacy["missing"] == ["Lacuna V1"]
    assert native["facts"] == native["verified_facts"] == ["Fato V2"]
    assert native["records"][0]["field"] == "stock"
    assert native["records"][0]["value"] == 7
    assert native["sources"] == ["Fonte V2"]
    assert native["gaps"] == native["missing"] == ["Lacuna V2"]
    retrieval = black_jhon_prompting.normalize_retrieval_result_v2(
        {
            "query": "estoque",
            "rows": [
                {"field": "stock", "value": 7, "store": "JK Pecas", "period": "agora", "source": "Bling"}
            ],
            "coverage_complete": True,
        }
    )
    assert retrieval == {
        "schema_version": black_jhon_prompting.RETRIEVAL_RESULT_V2,
        "source_schema_version": "legacy-v1",
        "query": "estoque",
        "records": [
            {"field": "stock", "value": 7, "store": "JK Pecas", "period": "agora", "source": "Bling"}
        ],
        "sources": ["Bling"],
        "gaps": [],
        "coverage_complete": True,
        "count": 1,
    }


def test_context_budget_reduces_values_before_serialization_and_preserves_evidence():
    context_json = black_jhon_prompting.bounded_context_json(
        {
            "noise": "N" * 200_000,
            "user_message": "PEDIDO_ATUAL_PRIORITARIO " + ("U" * 100_000),
            "conversation_context": [{"role": "user", "text": "C" * 12_000} for _ in range(50)],
            "worker_result": {
                "records": [
                    {
                        "field": "stock",
                        "value": "REGISTRO_PRIORITARIO",
                        "store": "JK Pecas",
                        "period": "agora",
                        "source": "Bling",
                    }
                ],
                "facts": ["FATO_PRIORITARIO", *("F" * 2000 for _ in range(20))],
                "sources": ["FONTE_PRIORITARIA", *("S" * 1000 for _ in range(20))],
                "gaps": ["LACUNA_PRIORITARIA", *("G" * 1000 for _ in range(20))],
            },
        }
    )

    assert len(context_json) <= 24_000
    decoded = json.loads(context_json)
    assert decoded["schema_version"] == black_jhon_prompting.PROMPT_CONTEXT_V2
    assert decoded["user_message"].startswith("PEDIDO_ATUAL_PRIORITARIO")
    assert decoded["worker_result"]["records"][0]["value"] == "REGISTRO_PRIORITARIO"
    assert decoded["worker_result"]["facts"][0] == "FATO_PRIORITARIO"
    assert decoded["worker_result"]["sources"][0] == "FONTE_PRIORITARIA"
    assert decoded["worker_result"]["gaps"][0] == "LACUNA_PRIORITARIA"


def test_decision_and_manager_contexts_are_valid_bounded_json():
    decision_prompt = codex_whatsapp_agents._decision_prompt(
        event_type="worker_result",
        user_message="U" * 100_000,
        active_job={"job_id": "job", "verified_partial": ["P" * 5000 for _ in range(20)]},
        worker_result={
            "status": "partial",
            "verified_facts": ["Fato confirmado", *("F" * 2000 for _ in range(30))],
            "sources": ["Fonte confirmada", *("S" * 1000 for _ in range(30))],
            "missing": ["Dado pendente", *("G" * 1000 for _ in range(20))],
        },
        conversation_context=[{"role": "user", "text": "C" * 5000} for _ in range(20)],
        ai_behavior="A" * 20_000,
        tick_index=0,
    )
    manager_prompt = codex_whatsapp_agents._manager_prompt(
        request_text="R" * 100_000,
        job_prompt="J" * 100_000,
        query_policy={"policy": "P" * 100_000},
        tool_catalog=[{"id": f"tool-{index}", "description": "D" * 5000} for index in range(80)],
        previous_evidence={
            "verified_facts": ["Fato anterior"],
            "sources": ["Fonte anterior"],
            "missing": ["Lacuna anterior"],
        },
        data_requests=[{"need": "N" * 500, "fields": ["campo"], "reason": "R" * 1000}],
    )

    for prompt in (decision_prompt, manager_prompt):
        context_json = prompt.rsplit("\n\n", 1)[1]
        assert len(context_json) <= 24_000
        assert json.loads(context_json)["schema_version"] == black_jhon_prompting.PROMPT_CONTEXT_V2


def test_prompts_use_functional_roles_and_runtime_reports_contract():
    prompts = "\n".join(
        [
            codex_whatsapp_agents.worker_output_instruction(),
            codex_whatsapp_agents._manager_prompt(
                request_text="teste",
                job_prompt="teste",
                query_policy={},
                tool_catalog=[],
                previous_evidence=None,
                data_requests=None,
            ),
            codex_whatsapp_agents._decision_prompt(
                event_type="user_message",
                user_message="oi",
                active_job=None,
                worker_result=None,
                conversation_context=None,
                ai_behavior="",
                tick_index=0,
            ),
        ]
    )
    diagnostic = codex_whatsapp_agents.WarmConversationRuntime().diagnostics()

    assert "agente Sol" not in prompts
    assert "Luna Gerenciador" not in prompts
    assert "agente Codex" not in prompts
    assert black_jhon_prompting.PROMPT_CONTRACT_VERSION in prompts
    assert diagnostic["prompt_contract"] == black_jhon_prompting.prompt_contract_diagnostics()
