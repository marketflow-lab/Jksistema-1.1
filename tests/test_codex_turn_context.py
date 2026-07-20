from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import codex_turn_context as turn_context


def _decision_state(*, now: datetime, store: str = "JK Pecas", scope: dict | None = None) -> dict:
    decision = turn_context.decide_conversation(
        None,
        surface="perguntas_publicas",
        client_id="000002",
        store=store,
        user="operador",
        subject="MLB-123:buyer-9",
        prompt_contract={"version": "prompt-v2"},
        schema_contract={"version": "schema-v2"},
        scope=scope or {"account": "seller-1"},
        now=now,
    )
    return {
        **decision.to_dict(),
        "thread_id": "thread-provider-1",
        "updated_at": now.isoformat(),
    }


def test_contracts_expose_v2_schemas_and_public_diagnostics():
    diagnostics = turn_context.contract_diagnostics()

    assert diagnostics["contract_version"] == turn_context.CONTRACT_VERSION
    assert diagnostics["contract_hash"] == turn_context.CONTRACT_HASH
    assert diagnostics["v1_readers_enabled"] is True
    assert diagnostics["identity_dimensions"] == ["surface", "client_id", "store", "user", "subject"]
    assert turn_context.CONVERSATION_DECISION_V2_SCHEMA["properties"]["schema_version"]["enum"] == [
        turn_context.CONVERSATION_DECISION_V2
    ]
    assert turn_context.TURN_CONTEXT_V2_SCHEMA["properties"]["schema_version"]["enum"] == [
        turn_context.TURN_CONTEXT_V2
    ]
    assert turn_context.EVIDENCE_ENVELOPE_V2_SCHEMA["properties"]["schema_version"]["enum"] == [
        turn_context.EVIDENCE_ENVELOPE_V2
    ]
    assert turn_context.FINAL_RESPONSE_V2_SCHEMA["properties"]["schema_version"]["enum"] == [
        turn_context.FINAL_RESPONSE_V2
    ]


def test_v1_readers_normalize_all_contracts_to_v2():
    decision = turn_context.read_conversation_decision(
        {
            "schema_version": 1,
            "action": "delegate",
            "job_title": "Consultar SKU",
            "job_prompt": "Consulte o SKU 001",
            "requires_web": False,
            "prompt_hash": "legacy-prompt",
        }
    )
    evidence = turn_context.read_evidence_envelope(
        {
            "schema_version": "legacy-v1",
            "rows": [{"name": "compatibilidade", "value": "confirmada", "reference": "catalogo"}],
            "verified_facts": ["Codigo confirmado"],
            "references": ["Catalogo oficial"],
            "missing": [],
            "data_sufficient": True,
            "data_complete": True,
        }
    )
    context = turn_context.read_turn_context(
        {
            "schema_version": "v1",
            "surface": "sidebar",
            "tenant_id": "000002",
            "username": "admin",
            "subject_key": "conversation-1",
            "prompt": "Continue a analise",
            "conversation_context": [{"role": "user", "text": "Contexto anterior"}],
            "memory": {"preferences": ["Respostas curtas"]},
            "worker_result": evidence.to_dict(),
        }
    )
    response = turn_context.read_final_response(
        {
            "schema_version": 1,
            "resposta": "Rascunho pronto",
            "sources": ["Anuncio"],
            "requires_approval": True,
            "pode_enviar_automaticamente": True,
        }
    )

    assert decision.schema_version == turn_context.CONVERSATION_DECISION_V2
    assert decision.source_schema_version == turn_context.CONVERSATION_DECISION_V1
    assert decision.task["prompt"] == "Consulte o SKU 001"
    assert evidence.schema_version == turn_context.EVIDENCE_ENVELOPE_V2
    assert evidence.source_schema_version == turn_context.EVIDENCE_ENVELOPE_V1
    assert evidence.records[0]["field"] == "compatibilidade"
    assert context.schema_version == turn_context.TURN_CONTEXT_V2
    assert context.source_schema_version == turn_context.TURN_CONTEXT_V1
    assert context.request == "Continue a analise"
    assert response.schema_version == turn_context.FINAL_RESPONSE_V2
    assert response.source_schema_version == turn_context.FINAL_RESPONSE_V1
    assert response.approval_required is True
    assert response.automation_eligible is True
    assert response.publish_authorized is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("surface", "pos_venda"),
        ("client_id", "000003"),
        ("store", "Uai Mineirinho"),
        ("user", "outro-operador"),
        ("subject", "MLB-999:buyer-9"),
    ],
)
def test_conversation_key_is_isolated_by_every_dimension(field: str, replacement: str):
    base = {
        "surface": "perguntas_publicas",
        "client_id": "000002",
        "store": "JK Pecas",
        "user": "operador",
        "subject": "MLB-123:buyer-9",
    }
    first = turn_context.conversation_key(**base)
    changed = turn_context.conversation_key(**{**base, field: replacement})

    assert first.startswith("ctx2_perguntas-publicas_")
    assert first == turn_context.conversation_key(**base)
    assert changed != first


def test_conversation_decision_reuses_only_same_contract_scope_and_fresh_thread():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    previous = _decision_state(now=now)

    decision = turn_context.decide_conversation(
        previous,
        surface="perguntas_publicas",
        client_id="000002",
        store="JK Pecas",
        user="operador",
        subject="MLB-123:buyer-9",
        prompt_contract={"version": "prompt-v2"},
        schema_contract={"version": "schema-v2"},
        scope={"account": "seller-1"},
        now=now + timedelta(days=2),
    )

    assert decision.thread_action == "reuse"
    assert decision.reuse_thread is True
    assert decision.restart_required is False
    assert decision.restart_reasons == ()


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"prompt_contract": {"version": "prompt-v3"}}, "prompt_contract_changed"),
        ({"schema_contract": {"version": "schema-v3"}}, "schema_contract_changed"),
        ({"scope": {"account": "seller-2"}}, "scope_changed"),
    ],
)
def test_conversation_decision_restarts_on_contract_or_scope_change(overrides, expected_reason):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    previous = _decision_state(now=now)
    kwargs = {
        "surface": "perguntas_publicas",
        "client_id": "000002",
        "store": "JK Pecas",
        "user": "operador",
        "subject": "MLB-123:buyer-9",
        "prompt_contract": {"version": "prompt-v2"},
        "schema_contract": {"version": "schema-v2"},
        "scope": {"account": "seller-1"},
        "now": now + timedelta(days=1),
    }
    decision = turn_context.decide_conversation(previous, **{**kwargs, **overrides})

    assert decision.thread_action == "restart"
    assert decision.restart_required is True
    assert decision.reuse_thread is False
    assert expected_reason in decision.restart_reasons


def test_conversation_decision_restarts_after_thirty_days():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    previous = _decision_state(now=now - timedelta(days=30))

    decision = turn_context.decide_conversation(
        previous,
        surface="perguntas_publicas",
        client_id="000002",
        store="JK Pecas",
        user="operador",
        subject="MLB-123:buyer-9",
        prompt_contract={"version": "prompt-v2"},
        schema_contract={"version": "schema-v2"},
        scope={"account": "seller-1"},
        now=now,
    )

    assert decision.thread_action == "restart"
    assert "context_expired_30d" in decision.restart_reasons
    assert decision.age_days == 30.0


def test_durable_memory_sanitizer_removes_volatile_operations_and_raw_results():
    sanitized = turn_context.sanitize_durable_memory(
        {
            "preferences": ["Prefere respostas curtas", "Mostrar o preco atual"],
            "decisions": [{"content": "Usar linguagem simples"}],
            "stock": 7,
            "pedido": {"id": "2000001"},
            "shipping": {"status": "delivered"},
            "payment": {"status": "paid"},
            "claim": {"id": "claim-1"},
            "tool_results": [{"value": 123}],
            "nested": {
                "notes": [
                    {"category": "preferencias", "content": "Evitar tabelas"},
                    {"category": "operacional", "content": "Estoque do SKU 001 e 4"},
                ]
            },
        }
    )

    serialized = json.dumps(sanitized.value, ensure_ascii=False).lower()
    assert "prefere respostas curtas" in serialized
    assert "usar linguagem simples" in serialized
    assert "evitar tabelas" in serialized
    for forbidden in ("preco", "stock", "pedido", "shipping", "payment", "claim", "tool_results", "estoque"):
        assert forbidden not in serialized
    assert sanitized.removed_paths
    assert sanitized.fingerprint


def test_structural_compaction_preserves_priority_and_always_returns_valid_json():
    payload = {
        "schema_version": turn_context.TURN_CONTEXT_V2,
        "request": {"message": "PEDIDO_PRIORITARIO " + ("U" * 50_000)},
        "evidence": {
            "facts": ["FATO_PRIORITARIO", *("F" * 2000 for _ in range(80))],
            "sources": ["FONTE_PRIORITARIA", *("S" * 1000 for _ in range(80))],
            "gaps": ["LACUNA_PRIORITARIA", *("G" * 1000 for _ in range(80))],
        },
        "history": [{"role": "user", "text": "H" * 5000} for _ in range(100)],
        "diagnostics": {"raw": "D" * 100_000},
    }

    result = turn_context.compact_json_structural(
        payload,
        max_bytes=4096,
        priority_paths=("request", "evidence.facts", "evidence.sources", "evidence.gaps"),
    )
    decoded = json.loads(result.json_text)

    assert result.compacted_bytes <= 4096
    assert result.truncated is True
    assert decoded["schema_version"] == turn_context.TURN_CONTEXT_V2
    assert decoded["request"]["message"].startswith("PEDIDO_PRIORITARIO")
    assert decoded["evidence"]["facts"][0] == "FATO_PRIORITARIO"
    assert decoded["evidence"]["sources"][0] == "FONTE_PRIORITARIA"
    assert decoded["evidence"]["gaps"][0] == "LACUNA_PRIORITARIA"
    assert result.fingerprint


def test_build_turn_context_sanitizes_memory_and_produces_stable_fingerprints():
    kwargs = {
        "surface": "pos_venda",
        "client_id": "000002",
        "store": "JK Pecas",
        "user": "operador",
        "subject": "pack-123",
        "request": {"message": "Produto apresentou defeito"},
        "policy": {"version": "ppv-v2", "draft_only": True},
        "scope": {"account": "seller-1"},
        "short_memory": [{"role": "buyer", "text": "Nao funciona"}],
        "durable_memory": {
            "preferences": ["Responder de forma objetiva"],
            "payment": {"status": "paid"},
        },
        "evidence": {
            "status": "partial",
            "facts": ["Compra identificada"],
            "sources": ["Mercado Livre"],
            "gaps": ["Diagnostico do defeito"],
        },
        "turn_id": "turn-fixed",
        "created_at": "2026-07-20T12:00:00Z",
    }
    first = turn_context.build_turn_context(**kwargs)
    second = turn_context.build_turn_context(**kwargs)
    diagnostics = turn_context.turn_context_diagnostics(first)

    assert first.conversation_key == second.conversation_key
    assert first.fingerprints == second.fingerprints
    assert first.durable_memory == {"preferences": ["Responder de forma objetiva"]}
    assert first.evidence.status == "partial"
    assert diagnostics["durable_memory_removed_count"] == 1
    compacted = first.compact(6000)
    assert json.loads(compacted.json_text)["conversation_key"] == first.conversation_key
    assert compacted.compacted_bytes <= 6000


def test_final_response_keeps_automation_separate_from_publish_authorization():
    response = turn_context.normalize_final_response(
        {
            "schema_version": turn_context.FINAL_RESPONSE_V2,
            "status": "completed",
            "text": "Rascunho validado",
            "evidence_refs": ["listing:MLB123"],
            "automation_eligible": True,
            "approval_required": True,
            "publish_authorized": False,
            "requires_human_review": False,
        }
    )

    assert response.automation_eligible is True
    assert response.approval_required is True
    assert response.publish_authorized is False
    assert response.fingerprint
