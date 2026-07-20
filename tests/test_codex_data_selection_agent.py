from __future__ import annotations

import json

import pytest

from backend.services.codex_data_selection_agent import (
    DATA_SELECTION_PLAN_SCHEMA,
    MAX_CONTEXT_HUB_SNIPPETS,
    MAX_CONTEXT_HUB_SNIPPET_CHARS,
    SCHEMA_VERSION,
    MAX_EVIDENCE_BYTES,
    MAX_PLAN_BYTES,
    CodexDataSelectionRuntime,
    DataSelectionPlanError,
    build_planner_prompt,
    compact_evidence,
    normalize_data_selection_plan,
)


def _plan(*, tool_calls=None, context_hub=None, action="collect", requested_fields=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "action": action,
        "intents": ["current_store_stock"],
        "entities": {
            "sku": "245",
            "mlb": "",
            "order_id": "",
            "period": "",
            "store_ref": "JK Pecas",
            "store_mode": "single",
        },
        "requested_fields": list(requested_fields or ["stock"]),
        "tool_calls": tool_calls if tool_calls is not None else [
            {
                "tool_id": "bling_stock_balances",
                "arguments": json.dumps({"sku": "245", "loja": "JK Pecas"}),
                "required": True,
                "reason": "saldo atual",
                "depends_on": [],
            }
        ],
        "context_hub": context_hub if context_hub is not None else {
            "mode": "not_applicable",
            "query": "",
            "filters": {},
            "top_k": 0,
            "snippet_max_chars": 0,
        },
        "missing_user_fields": [],
        "confidence": 0.91,
        "reason": "consulta operacional atual",
    }


def _runtime(planner):
    return CodexDataSelectionRuntime(default_size=1, planner=planner)


def _call(runtime, **overrides):
    values = {
        "request_text": "Quantas unidades do SKU 245 existem?",
        "job_prompt": "Consultar saldo confirmado",
        "surface": "whatsapp",
        "allowed_tools": [{"id": "bling_stock_balances", "read_only": True}],
        "authorized_stores": ["JK Pecas"],
        "conversation_anchors": {},
    }
    values.update(overrides)
    return runtime.plan(**values)


def test_schema_e_fechado_em_todos_os_objetos_controlados():
    assert DATA_SELECTION_PLAN_SCHEMA["additionalProperties"] is False
    assert DATA_SELECTION_PLAN_SCHEMA["properties"]["entities"]["additionalProperties"] is False
    call_schema = DATA_SELECTION_PLAN_SCHEMA["properties"]["tool_calls"]["items"]
    assert call_schema["additionalProperties"] is False
    assert DATA_SELECTION_PLAN_SCHEMA["properties"]["context_hub"]["additionalProperties"] is False


def test_plano_valido_usa_schema_e_planner_injetado():
    seen = []

    def planner(**kwargs):
        seen.append(kwargs)
        return _plan()

    runtime = _runtime(planner)
    result = _call(runtime)
    assert result["schema_version"] == SCHEMA_VERSION
    assert result["tool_calls"][0]["tool_id"] == "bling_stock_balances"
    assert json.loads(result["tool_calls"][0]["arguments"])["sku"] == "245"
    assert seen[0]["output_schema"] is DATA_SELECTION_PLAN_SCHEMA
    assert seen[0]["attempt"] == 0
    assert runtime.diagnostics()["plans"] == 1


def test_saida_invalida_recebe_exatamente_um_retry():
    attempts = []

    def planner(**kwargs):
        attempts.append(kwargs["attempt"])
        invalid = _plan()
        invalid["action"] = "invented_action"
        return invalid if kwargs["attempt"] == 0 else json.dumps(_plan())

    runtime = _runtime(planner)
    assert _call(runtime)["action"] == "collect"
    assert attempts == [0, 1]
    diagnostics = runtime.diagnostics()
    assert diagnostics["replans"] == 1
    assert diagnostics["schema_failures"] == 1


def test_duas_saidas_invalidas_falham_sem_terceira_tentativa():
    attempts = []

    def planner(**kwargs):
        attempts.append(kwargs["attempt"])
        return {**_plan(), "action": "invented_action"}

    runtime = _runtime(planner)
    with pytest.raises(RuntimeError, match="data_selection_agent_failed"):
        _call(runtime)
    assert attempts == [0, 1]
    assert runtime.diagnostics()["failures"] == 1


def test_normalizador_remove_tenant_credenciais_e_ferramentas_mutaveis():
    raw = _plan(tool_calls=[
        {
            "tool_id": "bling_stock_balances",
            "arguments": json.dumps({
                "sku": "245",
                "tenant_id": "tenant-secreto",
                "client_id": "cliente-secreto",
                "authorization": "Bearer segredo",
                "token": "token-secreto",
            }),
            "required": True,
            "reason": "consulta",
            "depends_on": [],
        },
        {
            "tool_id": "stock_update",
            "arguments": "{}",
            "required": False,
            "reason": "nao pode",
            "depends_on": [],
        },
    ])
    plan, stats = normalize_data_selection_plan(
        raw,
        allowed_tools=[
            {"id": "bling_stock_balances", "read_only": True},
            {"id": "stock_update", "read_only": False},
        ],
        authorized_stores=["JK Pecas"],
    )
    assert json.loads(plan["tool_calls"][0]["arguments"]) == {"sku": "245"}
    assert stats["accepted"] == ["bling_stock_balances"]
    assert stats["rejected"] == ["stock_update"]


def test_prompt_remove_segredos_de_ancoras_e_evidencia():
    prompt = build_planner_prompt(
        request_text="consultar estoque",
        job_prompt="consulta read-only",
        surface="whatsapp",
        allowed_tools=[{
            "id": "stock_data",
            "read_only": True,
            "input_schema": {"properties": {"sku": {}, "tenant_id": {}, "api_key": {}}},
        }],
        authorized_stores=["JK Pecas"],
        conversation_anchors={"topic": "estoque", "tenant": "tenant-secreto", "password": "senha-secreta"},
        previous_evidence={"summary": "ok", "access_token": "acesso-secreto"},
        data_gap=None,
    )
    for secret in ("tenant-secreto", "senha-secreta", "acesso-secreto"):
        assert secret not in prompt
    assert '"sku"' in prompt
    assert '"tenant_id"' not in prompt
    assert '"api_key"' not in prompt


def test_prompt_entrega_contexto_resolvido_do_luna_ao_seletor():
    prompt = build_planner_prompt(
        request_text="Estoque do 001 na mesma loja",
        job_prompt="Consulte o estoque do SKU 001 na JK Pecas",
        surface="whatsapp",
        allowed_tools=[{"id": "bling_stock_balances", "read_only": True}],
        authorized_stores=["JK Pecas", "Deckas"],
        conversation_anchors={
            "recent_turns": [{"role": "user", "text": "na mesma loja"}],
            "resolved_context": {
                "store_mode": "single",
                "store": "JK Pecas",
                "sku": "001",
                "mlb": "",
                "period": "",
            },
        },
        previous_evidence={},
        data_gap={},
    )

    payload = json.loads(prompt.split("\n\n", 1)[1])
    resolved = payload["conversation_anchors"]["resolved_context"]
    assert resolved["store"] == "JK Pecas"
    assert resolved["sku"] == "001"
    assert "conversation_anchors.resolved_context" in prompt


def test_dedupe_allowlist_e_limite_sao_aplicados():
    calls = []
    for tool_id in ("tool_a", "tool_a", "tool_b", "tool_c", "unknown_tool"):
        calls.append({
            "tool_id": tool_id,
            "arguments": {"sku": "245"},
            "required": False,
            "reason": "consulta",
            "depends_on": [],
        })
    raw = _plan(tool_calls=calls)
    plan, stats = normalize_data_selection_plan(
        raw,
        allowed_tools=["tool_a", "tool_b", "tool_c"],
        authorized_stores=["JK Pecas"],
        max_calls=2,
    )
    assert [item["tool_id"] for item in plan["tool_calls"]] == ["tool_a", "tool_b"]
    assert stats["proposed"] == ["tool_a", "tool_a", "tool_b", "tool_c", "unknown_tool"]
    assert stats["accepted"] == ["tool_a", "tool_b"]
    assert stats["rejected"] == ["tool_c", "unknown_tool"]


def test_loja_nao_autorizada_e_bloqueada():
    raw = _plan()
    raw["entities"]["store_ref"] = "Loja Invasora"
    with pytest.raises(DataSelectionPlanError, match="data_selection_unauthorized_store"):
        normalize_data_selection_plan(
            raw,
            allowed_tools=["bling_stock_balances"],
            authorized_stores=["JK Pecas"],
        )


def test_context_hub_aplica_budget_e_nao_coleta_em_mutacao():
    hub = {
        "mode": "required",
        "query": "compatibilidade do SKU 245",
        "filters": {"source_type": "sku", "tenant_id": "nao-pode"},
        "top_k": 999,
        "snippet_max_chars": 999999,
    }
    plan, _stats = normalize_data_selection_plan(
        _plan(tool_calls=[], context_hub=hub, requested_fields=["compatibility"]),
        allowed_tools=[],
        authorized_stores=["JK Pecas"],
    )
    assert plan["context_hub"]["top_k"] == MAX_CONTEXT_HUB_SNIPPETS
    assert plan["context_hub"]["snippet_max_chars"] == MAX_CONTEXT_HUB_SNIPPET_CHARS
    assert plan["context_hub"]["filters"] == {"source_type": "sku"}

    mutation, _stats = normalize_data_selection_plan(
        _plan(context_hub=hub, action="mutation_candidate", requested_fields=["compatibility"]),
        allowed_tools=["bling_stock_balances"],
        authorized_stores=["JK Pecas"],
    )
    assert mutation["tool_calls"] == []
    assert mutation["context_hub"]["mode"] == "not_applicable"


def test_context_hub_nao_substitui_fonte_operacional_atual():
    hub = {
        "mode": "required",
        "query": "estoque do SKU 245",
        "filters": {"sku": "245"},
        "top_k": 6,
        "snippet_max_chars": 320,
    }
    with pytest.raises(DataSelectionPlanError, match="context_hub_not_operational_source"):
        normalize_data_selection_plan(
            _plan(tool_calls=[], context_hub=hub, requested_fields=["current_stock"]),
            allowed_tools=[],
            authorized_stores=["JK Pecas"],
        )


def test_diagnostics_normais_nao_expoem_prompt_plano_ou_dados_do_usuario():
    runtime = _runtime(lambda **_kwargs: _plan())
    _call(runtime, request_text="conteudo-ultra-sensivel-123")
    measured = runtime.record_evidence_size({"commercial_result": "segredo-comercial-789"})
    diagnostic = json.dumps(runtime.diagnostics(), ensure_ascii=False)
    assert "conteudo-ultra-sensivel-123" not in diagnostic
    assert "JK Pecas" not in diagnostic
    assert "245" not in diagnostic
    assert "segredo-comercial-789" not in diagnostic
    assert runtime.diagnostics()["last_evidence_bytes"] == measured
    assert set(runtime.diagnostics()) >= {
        "plans", "replans", "schema_failures", "runtime_failures", "failures",
        "average_latency_ms", "accepted_tools", "rejected_tools",
    }


def test_diagnostics_de_erro_nao_expoem_conteudo_sensivel():
    def planner(**_kwargs):
        raise RuntimeError("token-ultra-secreto-456")

    runtime = _runtime(planner)
    with pytest.raises(RuntimeError, match="data_selection_agent_failed"):
        _call(runtime)
    diagnostic = json.dumps(runtime.diagnostics(), ensure_ascii=False)
    assert "token-ultra-secreto-456" not in diagnostic


def test_data_gap_nao_expoe_credenciais_no_prompt():
    prompt = build_planner_prompt(
        request_text="consultar",
        job_prompt="",
        surface="app",
        allowed_tools=[],
        authorized_stores=[],
        conversation_anchors={},
        previous_evidence={},
        data_gap={"need": "estoque", "token": "gap-token-secreto"},
    )
    assert "gap-token-secreto" not in prompt


def test_orcamentos_preservam_json_completo_sem_corte_no_meio():
    evidence = compact_evidence(
        {"rows": [{"sku": str(index), "description": "x" * 2000} for index in range(100)]}
    )
    encoded = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) <= MAX_EVIDENCE_BYTES
    assert isinstance(json.loads(encoded), dict)

    prompt = build_planner_prompt(
        request_text="q" * 12000,
        job_prompt="j" * 6000,
        surface="app",
        allowed_tools=[{"id": f"tool_{index}", "description": "d" * 500} for index in range(100)],
        authorized_stores=[f"Loja {index}" for index in range(100)],
        conversation_anchors={"history": ["a" * 1000 for _ in range(30)]},
        previous_evidence={"rows": ["b" * 2000 for _ in range(30)]},
        data_gap={"rows": ["c" * 2000 for _ in range(30)]},
    )
    payload_text = prompt.split("\n\n", 1)[1]
    payload = json.loads(payload_text)
    assert isinstance(payload, dict)
    assert len(payload_text.encode("utf-8")) <= MAX_PLAN_BYTES
