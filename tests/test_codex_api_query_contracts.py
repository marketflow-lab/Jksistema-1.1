from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.services import codex_assistant


def _permissions() -> dict[str, bool]:
    return {"vendas": True, "anuncios_ml": True, "integracao": True}


def _api_raw(function: str, rows: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "function": function,
        "arguments": {},
        "result": {"pedidos": list(rows or []), "read_only": True, **extra},
    }


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if codex_assistant._ASSISTANT_CACHE_SECRET_KEY_RE.search(str(key)):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def test_mercado_livre_orders_contract_is_explicit_read_only_and_permission_scoped():
    meta = codex_assistant._assistant_tool_meta("mercado_livre_orders")
    schema = codex_assistant._assistant_tool_input_schema("mercado_livre_orders")

    assert meta["external"] is True
    assert meta["read_only"] is True
    assert meta["executor"] == "_ia_tool_get_mercado_livre_orders"
    assert meta["cache_ttl_seconds"] == 120
    assert meta["zero_is_authoritative"] is True
    assert meta["fallbacks"] == []
    assert meta["companion_tools"] == ["bling_sales_orders"]
    assert codex_assistant.ASSISTANT_TOOL_PERMISSION_REQUIREMENTS["mercado_livre_orders"] == (
        "vendas",
        "anuncios_ml",
    )
    assert {
        "loja",
        "data_inicio",
        "data_fim",
        "status",
        "sku",
        "item_id",
        "id_pedido",
        "offset",
        "limite",
        "max_paginas",
        "incluir_detalhes",
        "force_refresh",
    } <= set(schema)
    assert all(tool.get("read_only") is True for tool in codex_assistant.CODEX_DATA_TOOLS)
    full_catalog = codex_assistant._assistant_tools_public({"full": True})
    assert all(tool.get("read_only") is True for tool in full_catalog)
    public_orders = next(tool for tool in full_catalog if tool["id"] == "mercado_livre_orders")
    assert public_orders["companion_tools"] == ["bling_sales_orders"]
    assert public_orders["zero_is_authoritative"] is True
    assert public_orders["aggregation_policy"] == "separate_sources_no_sum"

    denied = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_orders",
        args={"message": "pedidos via API"},
        permissions={"vendas": True},
    )
    assert denied["error_code"] == "tool_permission_denied"
    assert denied["missing_permissions"] == ["anuncios_ml"]


def test_mercado_livre_order_normalizer_prioritizes_complete_sku_aggregation():
    result = {
        "by_sku": [
            {"sku": "422", "quantity": 2, "gross_amount": 120.0},
            {"sku": "190", "quantity": 1, "gross_amount": 50.9},
        ],
        "orders": [{"order_id": "1"}, {"order_id": "2"}, {"order_id": "3"}],
    }

    assert codex_assistant._assistant_tool_rows(result) == result["by_sku"]
    assert codex_assistant._assistant_result_count(result) == 2


def test_daily_report_period_is_today_in_sao_paulo():
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()

    assert codex_assistant._assistant_resolve_period(
        "tenant",
        "Relatorio do dia da JK Pecas",
        {},
    ) == (today, today)


def test_period_comparison_honors_explicit_months_and_orders_them_chronologically(monkeypatch):
    captured_plans: list[dict[str, Any]] = []

    def fake_execute_registry_tool(
        client_id: str,
        tool_id: str,
        message: str,
        screen_context: Any,
        plan: dict[str, Any],
        existing_registry_results: Any = None,
    ):
        del client_id, message, screen_context, existing_registry_results
        captured_plans.append(dict(plan))
        raw = {
            "function": "get_period_comparison",
            "arguments": {},
            "result": {
                "periodo_a": {"data_inicio": "2026-05-01", "data_fim": "2026-05-31"},
                "periodo_b": {"data_inicio": "2026-06-01", "data_fim": "2026-06-30"},
                "comparativo": {"variacao_faturamento": -100},
            },
        }
        return [raw], [codex_assistant._assistant_standard_result(tool_id, raw, plan)], []

    monkeypatch.setattr(codex_assistant, "_assistant_execute_registry_tool", fake_execute_registry_tool)

    result = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="period_comparison",
        args={
            "data_inicio_a": "2026-06-01",
            "data_fim_a": "2026-06-30",
            "data_inicio_b": "2026-05-01",
            "data_fim_b": "2026-05-31",
            "loja": "JK Peças",
        },
        permissions=_permissions(),
    )

    assert result["success"] is True
    assert len(captured_plans) == 1
    assert captured_plans[0]["periodo_anterior"] == {
        "data_inicio": "2026-05-01",
        "data_fim": "2026-05-31",
    }
    assert captured_plans[0]["periodo"] == {
        "data_inicio": "2026-06-01",
        "data_fim": "2026-06-30",
    }
    assert captured_plans[0]["data_inicio"] == "2026-06-01"
    assert captured_plans[0]["data_fim"] == "2026-06-30"
    assert codex_assistant._assistant_result_count({
        "periodo_a": {"data_inicio": "2026-05-01"},
        "periodo_b": {"data_inicio": "2026-06-01"},
    }) == 2


def test_mercado_livre_returns_contract_is_direct_read_only_and_permission_scoped():
    meta = codex_assistant._assistant_tool_meta("mercado_livre_returns")
    schema = codex_assistant._assistant_tool_input_schema("mercado_livre_returns")

    assert meta["external"] is True
    assert meta["read_only"] is True
    assert meta["executor"] == "_ia_tool_get_mercado_livre_returns"
    assert meta["cache_ttl_seconds"] == 15
    assert meta["zero_is_authoritative"] is True
    assert meta["fallbacks"] == []
    assert meta["source_role"] == "primary_api"
    assert codex_assistant.ASSISTANT_TOOL_PERMISSION_REQUIREMENTS["mercado_livre_returns"] == (
        "vendas",
        "anuncios_ml",
    )
    assert {"loja", "data_inicio", "data_fim", "offset", "limite", "force_refresh"} <= set(schema)

    denied = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_returns",
        args={"message": "ultima devolucao", "loja": "JK Pecas"},
        permissions={"vendas": True},
    )
    assert denied["error_code"] == "tool_permission_denied"
    assert denied["missing_permissions"] == ["anuncios_ml"]


def test_generic_sales_api_plan_keeps_two_apis_and_local_history_separate():
    message = "Consulte vendas via API de 01/07/2026 a 12/07/2026"
    selected = codex_assistant._assistant_select_tool_ids(message, "chat", {})
    plan = codex_assistant._assistant_registry_plan("tenant", message, {}, "chat")

    assert selected[:2] == ["mercado_livre_orders", "bling_sales_orders"]
    assert "bling_sales_orders" in selected
    assert "mercado_livre_orders" in selected
    assert "sales_returns_query" in selected
    assert "sales_ranking" in selected
    assert "product_data" not in selected
    assert codex_assistant._assistant_message_has_product_ref(message) is False
    assert codex_assistant._assistant_normalize_sku("01/07/2026") == ""
    assert plan["source_roles"]["bling_sales_orders"] == "primary_api"
    assert plan["source_roles"]["mercado_livre_orders"] == "primary_api"
    assert plan["source_roles"]["sales_returns_query"] == "supporting_local_history"
    assert plan["aggregation_policy"] == "separate_sources_no_sum"


def test_source_routing_prefers_bling_stock_and_ml_for_sales_listings_and_full():
    stock = codex_assistant._assistant_source_routing_policy("Qual o estoque atual do SKU 001?")
    assert stock["required_tools"] == ["bling_stock_balances"]
    assert stock["preferred_providers"] == ["bling"]
    assert stock["bling_stock_scope"] == "exclude_full"

    listing = codex_assistant._assistant_source_routing_policy("Qual a descricao do anuncio MLB123456789?")
    assert listing["required_tools"] == ["mercado_livre_listing"]
    assert listing["include_listing_details"] is True

    sales = codex_assistant._assistant_source_routing_policy("Mostre os pedidos e vendas de hoje")
    assert sales["required_tools"] == ["mercado_livre_orders"]
    assert codex_assistant._assistant_select_tool_ids("Mostre os pedidos e vendas de hoje", "chat", {})[0] == "mercado_livre_orders"

    full = codex_assistant._assistant_source_routing_policy("Some o estoque Full dos SKUs 001 e 002")
    assert full["required_tools"] == ["mercado_livre_full_stock"]
    assert full["full_exclusive"] is True
    assert "bling_stock_balances" in full["forbidden_tools"]
    assert "stock_data" in full["forbidden_tools"]

    combined = codex_assistant._assistant_source_routing_policy("Some o estoque da loja + Full do SKU 001")
    assert combined["required_tools"] == ["bling_stock_balances", "mercado_livre_full_stock"]
    assert combined["aggregation_policy"] == "sum_bling_store_plus_mercado_livre_full"


def test_latest_sale_and_return_use_only_the_corresponding_mercado_livre_api():
    latest_sale = "Qual foi a ultima venda da loja JK Pecas?"
    sale_policy = codex_assistant._assistant_source_routing_policy(latest_sale)
    sale_selected = codex_assistant._assistant_select_tool_ids(latest_sale, "chat", {})
    sale_plan = codex_assistant._assistant_registry_plan("tenant", latest_sale, {}, "chat")

    assert sale_policy["required_tools"] == ["mercado_livre_orders"]
    assert sale_policy["force_refresh"] is True
    assert sale_selected[0] == "mercado_livre_orders"
    assert not {"sales_returns_query", "sales_ranking", "sales_summary"}.intersection(sale_selected)
    assert sale_plan["source_roles"]["mercado_livre_orders"] == "primary_api"

    latest_return = "Qual foi a ultima devolucao da loja JK Pecas?"
    return_policy = codex_assistant._assistant_source_routing_policy(latest_return)
    return_selected = codex_assistant._assistant_select_tool_ids(latest_return, "chat", {})
    return_plan = codex_assistant._assistant_registry_plan("tenant", latest_return, {}, "chat")

    assert return_policy["required_tools"] == ["mercado_livre_returns"]
    assert return_policy["force_refresh"] is True
    assert return_selected[0] == "mercado_livre_returns"
    assert not {
        "sales_returns_query",
        "sales_ranking",
        "sales_summary",
        "returns_summary",
        "return_rate",
    }.intersection(return_selected)
    assert return_plan["source_roles"]["mercado_livre_returns"] == "primary_api"


def test_latest_ml_event_context_does_not_add_local_direct_or_dispatcher_sources(monkeypatch):
    message = "Qual foi a ultima devolucao da loja JK Pecas?"
    plan = codex_assistant._assistant_registry_plan("tenant", message, {}, "chat")
    cache_reads = []

    monkeypatch.setattr(
        codex_assistant,
        "_assistant_cache_get",
        lambda *_args, **_kwargs: cache_reads.append(True) or {"stale": True},
    )
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_execute_registry",
        lambda *_args, **_kwargs: ([], [], plan, []),
    )
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_direct_tools",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local direct tools are forbidden")),
    )
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_prompt_queries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dispatcher fallbacks are forbidden")),
    )

    context = codex_assistant._assistant_collect_data("tenant", message, {}, mode="chat")

    assert cache_reads == []
    assert context["tool_plan"]["selected_tools"][0] == "mercado_livre_returns"
    assert context["cache_hit"] is False


def test_mercado_livre_full_stock_tool_uses_inventory_api_and_sums_only_full(monkeypatch):
    from backend.services import full_mercadolivre

    calls = []
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)

    def fake_full(client_id, loja, limite, *, force_refresh=False):
        calls.append((client_id, loja, limite, force_refresh))
        return {
            "success": True,
            "loja": loja,
            "results": [
                {"id": "MLB1", "sku": "001", "full_available_quantity": 7, "full_not_available_quantity": 2, "full_total_quantity": 9, "stock_source": "fulfillment_stock"},
                {"id": "MLB2", "sku": "002", "full_available_quantity": 3, "full_not_available_quantity": 0, "full_total_quantity": 3, "stock_source": "fulfillment_stock"},
            ],
        }

    monkeypatch.setattr(full_mercadolivre, "listar_anuncios_full_mercadolivre_payload", fake_full)
    result = codex_assistant.codex_assistant_execute_tool_call(
        "tenant",
        "mercado_livre_full_stock",
        {"message": "some o estoque full", "loja": "JK Pecas", "force_refresh": True},
        permissions={"mercado_full": True},
    )

    assert calls == [("tenant", "JK Pecas", 10000, True)]
    assert result["records"] == 2
    summary = result["summary"][0]["summary"]
    assert summary["full_available_quantity_total"] == 10.0
    assert summary["full_total_quantity"] == 12.0
    assert summary["stock_scope"] == "mercado_livre_fulfillment_only"


def test_mercado_livre_orders_normalizes_and_forwards_explicit_arguments(monkeypatch):
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def fake_call(name: str, *args: Any, **kwargs: Any):
        calls.append((name, args, kwargs))
        return _api_raw("get_mercado_livre_orders")

    monkeypatch.setattr(codex_assistant, "_assistant_call_ia_tool", fake_call)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_agent_fallback_ids",
        lambda *_args: (_ for _ in ()).throw(AssertionError("API zero must not trigger fallback")),
    )

    result = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_orders",
        args={
            "message": "pedidos do periodo",
            "loja": "JK Pecas",
            "data_inicio": "01/07/2026",
            "data_fim": "12/07/2026",
            "status": "PAID | partially-refunded",
            "sku": " abc-01 ",
            "mlb": "MLB-123456789",
            "id_pedido": "#998877",
            "offset": -4,
            "limite": 500,
            "incluir_detalhes": "sim",
            "force_refresh": True,
        },
        permissions=_permissions(),
    )

    assert result["success"] is True
    assert result["records"] == 0
    assert result["next_fallbacks"] == []
    assert len(calls) == 1
    name, positional, kwargs = calls[0]
    assert name == "_ia_tool_get_mercado_livre_orders"
    assert positional[:2] == ("tenant", "pedidos do periodo")
    assert isinstance(kwargs.pop("query_deadline"), float)
    assert kwargs == {
        "loja": "JK Pecas",
        "data_inicio": "2026-07-01",
        "data_fim": "2026-07-12",
        "status": "paid,partially-refunded",
        "sku": "ABC-01",
        "item_id": "MLB123456789",
        "id_pedido": "998877",
        "offset": 0,
        "limite": 100,
        "incluir_detalhes": True,
        "force_refresh": True,
        "modo_relatorio": False,
        "max_paginas": 2,
    }


def test_exact_order_audit_records_only_technical_metadata(monkeypatch, tmp_path):
    audit_path = tmp_path / "api_query_audit.jsonl"
    monkeypatch.setattr(codex_assistant, "_assistant_path", lambda *_args: audit_path)
    result = {
        "records": 1,
        "summary": [],
        "warnings": [],
        "all_rows": [{"buyer_name": "Não registrar", "conversations": {"messages": ["fala privada"]}}],
        "exact_metadata": {
            "exact_lookup": True,
            "requested_id": "2000013990113115",
            "identifier_type": "pack",
            "matched_stores": ["JK Peças"],
            "resolved_order_ids": ["2000017389080442"],
            "resources": ["orders/{id}", "packs/{id}", "shipments/{id}"],
            "searched_stores": [{"store": "JK Peças", "order_http": 404, "pack_http": 200, "result": "matched_pack"}],
            "partial_response": False,
        },
    }

    codex_assistant._assistant_api_query_audit(
        "000002",
        "mercado_livre_orders",
        {"loja": "JK Peças", "id_pedido": "2000013990113115", "limite": 1},
        result,
        audit_user="operador",
    )

    raw = audit_path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert event["exact_lookup"]["identifier_type"] == "pack"
    assert event["exact_lookup"]["resolved_order_ids"] == ["2000017389080442"]
    assert event["exact_lookup"]["stores_checked"][0]["pack_http"] == 200
    assert "Não registrar" not in raw
    assert "fala privada" not in raw


def test_period_report_forwards_multipage_contract_to_mercado_livre(monkeypatch):
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def fake_call(name: str, *args: Any, **kwargs: Any):
        calls.append((name, args, kwargs))
        return _api_raw("get_mercado_livre_orders")

    monkeypatch.setattr(codex_assistant, "_assistant_call_ia_tool", fake_call)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)

    result = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_orders",
        args={
            "message": "relatorio de vendas de 01/07/2026 a 12/07/2026",
            "loja": "JK Pecas",
        },
        permissions=_permissions(),
    )

    assert result["success"] is True
    assert len(calls) == 1
    name, _positional, kwargs = calls[0]
    assert name == "_ia_tool_get_mercado_livre_orders"
    assert kwargs["limite"] == 20_000
    assert kwargs["modo_relatorio"] is True
    assert kwargs["max_paginas"] == 400

    plan = codex_assistant._assistant_registry_plan(
        "tenant",
        "relatorio de vendas de 01/07/2026 a 12/07/2026",
        {},
        "chat",
    )
    assert plan["mode"] == "report"


def test_latest_return_forwards_365_day_period_limit_one_and_force_refresh(monkeypatch):
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def fake_call(name: str, *args: Any, **kwargs: Any):
        calls.append((name, args, kwargs))
        return {
            "function": "get_mercado_livre_returns",
            "arguments": {},
            "result": {"devolucoes": [], "read_only": True},
        }

    monkeypatch.setattr(codex_assistant, "_assistant_call_ia_tool", fake_call)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_api_query_audit", lambda *_args, **_kwargs: None)

    result = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_returns",
        args={
            "message": "qual foi a ultima devolucao da loja JK Pecas",
            "loja": "JK Pecas",
        },
        permissions=_permissions(),
    )

    assert result["success"] is True
    assert result["records"] == 0
    assert result["next_fallbacks"] == []
    assert result["source_role"] == "primary_api"
    assert len(calls) == 1
    name, positional, kwargs = calls[0]
    assert name == "_ia_tool_get_mercado_livre_returns"
    assert positional[:2] == ("tenant", "qual foi a ultima devolucao da loja JK Pecas")
    assert kwargs["loja"] == "JK Pecas"
    assert kwargs["limite"] == 1
    assert kwargs["offset"] == 0
    assert kwargs["force_refresh"] is True
    assert isinstance(kwargs["query_deadline"], float)
    period_days = (
        codex_assistant.datetime.fromisoformat(kwargs["data_fim"])
        - codex_assistant.datetime.fromisoformat(kwargs["data_inicio"])
    ).days
    assert period_days in {364, 365}


def test_listing_forwards_explicit_filters_and_has_legacy_signature_fallback(monkeypatch):
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_call(_name: str, *args: Any, **kwargs: Any):
        calls.append((args, kwargs))
        if kwargs:
            return {"function": "listing", "result": {"error": "unexpected keyword argument 'status'"}}
        return {"function": "get_mercado_livre_listing", "result": {"matches": [{"id": "MLB123456789"}]}}

    monkeypatch.setattr(codex_assistant, "_assistant_call_ia_tool", fake_call)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)

    result = codex_assistant.codex_assistant_execute_tool_call(
        client_id="tenant",
        tool_id="mercado_livre_listing",
        args={
            "message": "liste o anuncio",
            "status": "PAUSED",
            "sku": " sku-9 ",
            "item_id": "MLB 123456789",
            "offset": 12,
            "limite": 150,
            "incluir_detalhes": True,
            "force_refresh": True,
        },
        permissions={"anuncios_ml": True},
    )

    assert result["records"] == 1
    assert len(calls) == 2
    _, explicit = calls[0]
    assert explicit["status"] == "paused"
    assert explicit["sku"] == "SKU-9"
    assert explicit["item_id"] == "MLB123456789"
    assert explicit["offset"] == 12
    assert explicit["limite"] == 100
    assert explicit["incluir_detalhes"] is True
    legacy_args, legacy_kwargs = calls[1]
    assert legacy_kwargs == {}
    assert legacy_args[0] == "tenant"
    assert legacy_args[4] == 100


def test_external_cache_is_120_seconds_redacted_and_bypassed(monkeypatch):
    cache: dict[str, dict[str, Any]] = {}
    calls = {"provider": 0}

    def fake_get(_client_id: str, key: str, ttl: int):
        assert ttl == 120
        return cache.get(key)

    def fake_set(_client_id: str, key: str, payload: dict[str, Any]):
        assert not _contains_sensitive_key(payload)
        cache[key] = payload

    def fake_call(_name: str, *_args: Any, **_kwargs: Any):
        calls["provider"] += 1
        return _api_raw(
            "get_mercado_livre_orders",
            [{"id": str(calls["provider"])}],
            access_token="must-not-be-cached",
        )

    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", fake_get)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", fake_set)
    monkeypatch.setattr(codex_assistant, "_assistant_call_ia_tool", fake_call)

    base_args = {"message": "pedidos ML", "data_inicio": "2026-07-01", "data_fim": "2026-07-12"}
    first = codex_assistant.codex_assistant_execute_tool_call(
        "tenant", "mercado_livre_orders", base_args, permissions=_permissions()
    )
    second = codex_assistant.codex_assistant_execute_tool_call(
        "tenant", "mercado_livre_orders", base_args, permissions=_permissions()
    )
    refreshed = codex_assistant.codex_assistant_execute_tool_call(
        "tenant",
        "mercado_livre_orders",
        {**base_args, "force_refresh": True},
        permissions=_permissions(),
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert refreshed["cache_hit"] is False
    assert calls["provider"] == 2


def test_atualize_agora_phrase_bypasses_an_existing_cache(monkeypatch):
    provider_calls: list[bool] = []
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_cache_get",
        lambda *_args: {"success": True, "tool_id": "mercado_livre_orders", "records": 99},
    )
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_call_ia_tool",
        lambda *_args, **_kwargs: provider_calls.append(True) or _api_raw("get_mercado_livre_orders"),
    )

    result = codex_assistant.codex_assistant_execute_tool_call(
        "tenant",
        "mercado_livre_orders",
        {"message": "atualize agora os pedidos via API"},
        permissions=_permissions(),
    )

    assert provider_calls == [True]
    assert result["cache_hit"] is False


def test_retryable_api_failure_uses_recent_cache(monkeypatch):
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_cache_get",
        lambda *_args: {
            "success": True,
            "tool_id": "mercado_livre_orders",
            "records": 2,
            "warnings": [],
            "summary": [{"summary": {"status": "ok"}}],
        },
    )
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_call_ia_tool",
        lambda *_args, **_kwargs: _api_raw(
            "get_mercado_livre_orders",
            [],
            error="rate_limited",
            warnings=["HTTP 429"],
        ),
    )
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_sales_returns_query",
        lambda *_args, **_kwargs: {"function": "sales_returns_query", "result": {"rows": []}},
    )

    result = codex_assistant.codex_assistant_execute_tool_call(
        "tenant",
        "mercado_livre_orders",
        {"message": "atualize agora os pedidos via API", "force_refresh": True},
        permissions=_permissions(),
    )

    assert result["cache_hit"] is True
    assert result["cache_fallback"] is True
    assert result["records"] == 2


def test_api_zero_preserves_primary_and_adds_local_history_as_separate_support(monkeypatch):
    monkeypatch.setattr(codex_assistant, "_assistant_cache_get", lambda *_args: None)
    monkeypatch.setattr(codex_assistant, "_assistant_cache_set", lambda *_args: None)
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_call_ia_tool",
        lambda *_args, **_kwargs: _api_raw("get_mercado_livre_orders", []),
    )
    monkeypatch.setattr(
        codex_assistant,
        "_assistant_sales_returns_query",
        lambda *_args, **_kwargs: {
            "function": "sales_returns_query",
            "result": {"rows": [{"sku": "001", "quantidade_vendida": 2}], "records": 1},
        },
    )

    result = codex_assistant.codex_assistant_execute_tool_call(
        "tenant",
        "mercado_livre_orders",
        {"message": "pedidos do Mercado Livre da loja JK Pecas"},
        permissions=_permissions(),
    )

    summaries = {item["tool_id"]: item for item in result["summary"]}
    assert summaries["mercado_livre_orders"]["records"] == 0
    assert summaries["mercado_livre_orders"]["source_role"] == "primary_api"
    assert summaries["sales_returns_query"]["records"] == 1
    assert summaries["sales_returns_query"]["source_role"] == "supporting_local_history"
    assert result["aggregation_policy"] == "separate_sources_no_sum"


def test_api_query_audit_uses_safe_whitelist(tmp_path, monkeypatch):
    audit_path = tmp_path / "api_query_audit.jsonl"
    monkeypatch.setattr(codex_assistant, "_assistant_path", lambda _client_id, _name: str(audit_path))

    codex_assistant._assistant_api_query_audit(
        "tenant-1",
        "mercado_livre_orders",
        {
            "loja": "JK Pecas",
            "data_inicio": "2026-07-01",
            "data_fim": "2026-07-12",
            "status": "paid",
            "sku": "001",
            "offset": 0,
            "limite": 50,
            "access_token": "NAO_PODE_VAZAR",
        },
        {
            "records": 3,
            "warnings": [],
            "summary": [{"summary": {"status": "ok", "buyer": {"phone": "NAO_PODE_VAZAR"}}}],
        },
        audit_user="usuario@jk",
        cache_hit=True,
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert event["user"] == "usuario@jk"
    assert event["tenant"] == "tenant-1"
    assert event["provider"] == "mercado_livre"
    assert event["method"] == "GET"
    assert event["records"] == 3
    assert event["cache_hit"] is True
    assert event["read_only"] is True
    assert "NAO_PODE_VAZAR" not in json.dumps(event)
