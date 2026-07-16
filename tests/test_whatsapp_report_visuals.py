from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.services import codex_console, whatsapp_bridge, whatsapp_report_visuals


def _sales_result(store: str = "JK Peças", *, offset: int = 0) -> dict:
    points = [
        {
            "date": "2026-07-11",
            "orders": 1,
            "items_quantity": 1,
            "gross_amount": 50,
            "paid_amount": 50,
            "refund_amount": 0,
            "net_amount": 50,
        },
        {
            "date": "2026-07-12",
            "orders": 2,
            "items_quantity": 2,
            "gross_amount": 80,
            "paid_amount": 80,
            "refund_amount": 0,
            "net_amount": 80,
        },
        {
            "date": "2026-07-13",
            "orders": 1,
            "items_quantity": 2,
            "gross_amount": 90,
            "paid_amount": 90,
            "refund_amount": 0,
            "net_amount": 90,
        },
    ]
    totals = {
        "orders": 4,
        "items_quantity": 5,
        "gross_amount": 220,
        "paid_amount": 220,
        "refund_amount": 0,
        "net_amount": 220,
    }
    return {
        "success": True,
        "tool_id": "mercado_livre_orders",
        "loja": store,
        "all_rows": [
            {"sku": f"SKU-{offset + 1}", "title": "Produto com nome completo", "quantity": 3, "gross_amount": 150},
            {"sku": f"SKU-{offset + 2}", "title": "Outro produto", "quantity": 2, "gross_amount": 70},
        ],
        "chart_data": {
            "schema": "jk.marketplace.sales_by_day.v1",
            "points": points,
            "totals": totals,
            "coverage_complete": True,
            "pii_included": False,
        },
        "summary": [
            {
                "tool_id": "mercado_livre_orders",
                "loja": store,
                "periodo": {"data_inicio": "2026-07-11", "data_fim": "2026-07-13"},
                "summary": {"totals": totals, "coverage_complete": True},
            }
        ],
    }


def _local_sales_result(
    start: str,
    end: str,
    *,
    orders: int,
    items: int,
    gross: float,
    store: str = "JK Peças",
    sku: str = "124-1",
) -> dict:
    ranking = [{"sku": sku, "nome": "Produto com nome completo", "qtd": items, "valor": gross}]
    payload = {
        "data_inicio": start,
        "data_fim": end,
        "loja": store,
        "quantidade_total": items,
        "valor_total": gross,
        "pedidos_total": orders,
    }
    return {
        "success": True,
        "tool_id": "sales_ranking",
        "records": len(ranking),
        "top_rows": ranking,
        "source_label": "Histórico de vendas do JK Sistema",
        "summary": [{
            "tool_id": "sales_ranking",
            "loja": store,
            "arguments": {"data_inicio": start, "data_fim": end, "loja": store},
            "periodo": {"data_inicio": start, "data_fim": end},
            "rows": ranking,
            "summary": payload,
        }],
    }


def _local_period_comparison() -> dict:
    return {
        "success": True,
        "tool_id": "period_comparison",
        "source_label": "Comparação entre período atual e anterior",
        "summary": [{
            "tool_id": "period_comparison",
            "loja": "JK Peças",
            "arguments": {
                "data_inicio_a": "2026-06-01",
                "data_fim_a": "2026-06-30",
                "data_inicio_b": "2026-05-01",
                "data_fim_b": "2026-05-31",
                "loja": "JK Peças",
            },
            "summary": {
                "periodo_a": {
                    "data_inicio": "2026-06-01",
                    "data_fim": "2026-06-30",
                    "quantidade_vendida_total": 3078,
                    "valor_vendido_total": 399255.92,
                    "pedidos_total": 2658,
                },
                "periodo_b": {
                    "data_inicio": "2026-05-01",
                    "data_fim": "2026-05-31",
                    "quantidade_vendida_total": 3456,
                    "valor_vendido_total": 455368.01,
                    "pedidos_total": 2561,
                },
            },
        }],
    }


def test_only_analytical_or_explicit_visual_requests_trigger_charts():
    assert whatsapp_report_visuals.should_generate_report_charts("Faça um relatório de vendas") is True
    assert whatsapp_report_visuals.should_generate_report_charts("Compare as lojas em gráfico") is True
    assert whatsapp_report_visuals.should_generate_report_charts("Qual é o saldo do SKU 001?") is False


def test_numeric_comparison_phrasing_also_triggers_visuals():
    assert whatsapp_report_visuals.should_generate_report_charts("compare as vendas por loja") is True
    assert whatsapp_report_visuals.should_generate_report_charts("qual foi o SKU mais vendido no mes?") is True
    assert whatsapp_report_visuals.should_generate_report_charts("mostre a evolucao do faturamento por dia") is True


def test_local_month_comparison_builds_real_period_chart_without_query_policy(tmp_path):
    comparison = _local_period_comparison()
    june = _local_sales_result(
        "2026-06-01", "2026-06-30", orders=2658, items=3078, gross=399255.92,
    )
    may = _local_sales_result(
        "2026-05-01", "2026-05-31", orders=2561, items=3456, gross=455368.01,
    )

    chart = whatsapp_report_visuals.build_chart_data(
        "me dê o relatório do mês passado e um comparativo do mês anterior",
        [comparison, june, may],
        {},
    )

    assert chart["analysis_type"] == "sales_period_comparison"
    assert chart["period_start"] == "2026-05-01"
    assert chart["period_end"] == "2026-06-30"
    assert chart["stores"] == [{"name": "JK Peças"}]
    assert [row["label"] for row in chart["series"]] == ["05/2026", "06/2026"]
    assert chart["series"][0]["gross"] == 455368.01
    assert chart["series"][1]["gross"] == 399255.92
    assert chart["series"][0]["items"] == 3456
    assert chart["series"][1]["orders"] == 2658
    assert chart["ranking"][0]["sku"] == "124-1"
    assert chart["coverage_complete"] is True

    outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        task_id="month-comparison",
        prompt="compare as vendas de um mês para o outro",
        tool_results=[comparison, june, may],
        query_policy={},
    )
    assert outcome["status"] == "generated"
    assert [item["kind"] for item in outcome["artifacts"]] == ["period_comparison", "sku_ranking"]


def test_month_comparison_is_independent_of_result_order():
    comparison = _local_period_comparison()
    june = _local_sales_result(
        "2026-06-01", "2026-06-30", orders=2658, items=3078, gross=399255.92,
    )
    may = _local_sales_result(
        "2026-05-01", "2026-05-31", orders=2561, items=3456, gross=455368.01,
    )
    expected = whatsapp_report_visuals.build_chart_data("comparativo", [comparison, june, may], {})
    actual = whatsapp_report_visuals.build_chart_data("comparativo", [may, comparison, june], {})
    assert actual == expected


def test_two_ml_periods_from_same_store_are_periods_not_stores():
    may = _sales_result()
    may["summary"][0]["periodo"] = {"data_inicio": "2026-05-01", "data_fim": "2026-05-31"}
    june = copy.deepcopy(may)
    june["summary"][0]["periodo"] = {"data_inicio": "2026-06-01", "data_fim": "2026-06-30"}
    june["chart_data"]["points"] = [
        {"date": "2026-06-01", "orders": 3, "items_quantity": 4, "gross_amount": 300,
         "paid_amount": 300, "refund_amount": 0, "net_amount": 300},
    ]
    june["chart_data"]["totals"] = {
        "orders": 3, "items_quantity": 4, "gross_amount": 300,
        "paid_amount": 300, "refund_amount": 0, "net_amount": 300,
    }
    june["summary"][0]["summary"]["totals"] = copy.deepcopy(june["chart_data"]["totals"])

    chart = whatsapp_report_visuals.build_chart_data("compare maio e junho", [may, june], {})

    assert chart["analysis_type"] == "sales_period_comparison"
    assert chart["stores"] == [{"name": "JK Peças"}]
    assert [row["label"] for row in chart["series"]] == ["05/2026", "06/2026"]


def test_local_sales_timeseries_is_normalized_for_visual_analysis():
    chart_contract = {
        "schema": "jk.sales.timeseries.v1",
        "period_start": "2026-06-01",
        "period_end": "2026-06-02",
        "store": "JK Peças",
        "coverage_complete": True,
        "kpis": {"items": 5, "gross": 350, "refunds": 0, "net": 350},
        "series": [
            {"date": "2026-06-01", "items": 2, "gross": 100, "refunds": 0, "net": 100},
            {"date": "2026-06-02", "items": 3, "gross": 250, "refunds": 0, "net": 250},
        ],
    }
    result = {
        "function": "get_sales_timeseries",
        "result": {
            "data_inicio": "2026-06-01",
            "data_fim": "2026-06-02",
            "loja": "JK Peças",
            "chart_data": chart_contract,
        },
    }

    chart = whatsapp_report_visuals.build_chart_data("analise da evolução das vendas", [result], {})

    assert chart["analysis_type"] == "sales"
    assert chart["stores"][0]["name"] == "JK Peças"
    assert len(chart["series"]) == 2
    assert sum(row["gross"] for row in chart["series"]) == 350


def test_sales_artifacts_use_complete_structured_series(tmp_path):
    outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        task_id="task-1",
        prompt="relatório completo de vendas",
        tool_results=[_sales_result()],
        query_policy={"store": "JK Peças"},
    )

    assert outcome["status"] == "generated"
    assert [item["kind"] for item in outcome["artifacts"]] == ["sales_trend", "sku_ranking"]
    for artifact in outcome["artifacts"]:
        path = Path(artifact["path"])
        assert path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert 0 < artifact["byte_size"] < 5 * 1024 * 1024
        assert isinstance(artifact["expires_at"], int)


def test_all_stores_generate_comparison_and_store_trend(tmp_path):
    second = _sales_result("Uai Mineirinho", offset=10)
    second["chart_data"]["points"][0]["gross_amount"] = 30
    second["chart_data"]["points"][1]["gross_amount"] = 40
    second["chart_data"]["points"][2]["gross_amount"] = 50
    second["chart_data"]["totals"]["gross_amount"] = 120
    second["summary"][0]["summary"]["totals"]["gross_amount"] = 120

    outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        task_id="task-all",
        prompt="comparação de vendas de todas as lojas",
        tool_results=[_sales_result(), second],
        query_policy={"store_mode": "all"},
    )

    assert outcome["status"] == "generated"
    assert [item["kind"] for item in outcome["artifacts"]] == ["store_comparison", "store_trend"]


def test_weekly_chart_contract_drops_details_and_pii():
    chart = whatsapp_report_visuals.build_weekly_chart_data(
        [
            {"severity": "critical", "category": "Estoque", "detail": "Comprador 11999999999"},
            {"severity": "high", "category": "Vendas", "detail": "segredo@example.com"},
        ],
        "2026-W29",
    )
    serialized = json.dumps(chart, ensure_ascii=False)
    assert "11999999999" not in serialized
    assert "segredo@example.com" not in serialized
    assert chart["kpis"] == {"alerts": 2, "critical": 1, "attention": 1}


def test_bridge_sends_only_valid_private_chart_and_retains_it_for_seven_days(tmp_path, monkeypatch):
    outcome = whatsapp_report_visuals.generate_task_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        task_id="task-send",
        prompt="relatorio de vendas",
        tool_results=[_sales_result()],
        query_policy={"store": "JK Peças"},
        max_images=1,
    )
    artifact = outcome["artifacts"][0]
    source_path = Path(artifact["path"])
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "status": "sent"}

    monkeypatch.setattr(whatsapp_bridge, "_post_outbound_image", fake_post)
    results = whatsapp_bridge._whatsapp_deliver_report_artifacts(
        {"worker_url": "https://worker.test", "bridge_token": "token", "machine_id": "machine"},
        "wamid.1",
        [artifact],
        "000002",
        max_images=1,
    )

    assert results[0]["success"] is True
    assert calls[0][1]["artifact_type"] == "report_chart"
    assert calls[0][0][4] == ""
    assert source_path.exists() is True


def test_scheduled_report_chart_uses_the_same_period_and_has_no_external_caption(tmp_path):
    period = {
        "key": "2026-W29",
        "title": "Relatório semanal",
        "event_type": "weekly_report",
        "start": "2026-07-06",
        "end": "2026-07-12",
    }
    context = {
        "tool_results": [_sales_result()],
        "suggestions": [],
        "tool_plan": {"loja": "JK Peças", "store_mode": "single"},
    }

    chart_data = whatsapp_report_visuals.build_scheduled_report_chart_data(context, period, "weekly")
    outcome = whatsapp_report_visuals.generate_scheduled_report_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        period=period,
        kind="weekly",
        chart_data=chart_data,
        max_images=2,
    )

    assert chart_data["period_start"] == "2026-07-06"
    assert chart_data["period_end"] == "2026-07-12"
    assert chart_data["title"] == "Relatório semanal — vendas e operações"
    assert outcome["status"] == "generated"
    assert len(outcome["artifacts"]) == 1
    assert outcome["artifacts"][0]["kind"] != "kpi_card"
    assert all(item["caption"] == "" for item in outcome["artifacts"])


def test_scheduled_reports_discard_simple_kpi_card_and_keep_only_other_model(tmp_path, monkeypatch):
    from backend.services import report_charts

    created = {}

    def fake_generate(_chart_data, output_dir, max_images=2):
        assert max_images == 2
        kpi = output_dir / "simple-kpi.png"
        detailed = output_dir / "detailed-ranking.png"
        kpi.write_bytes(b"simple-kpi")
        detailed.write_bytes(b"detailed-ranking")
        created.update({"kpi": kpi, "detailed": detailed})
        return [
            {"path": str(kpi), "kind": "kpi_card", "caption": ""},
            {"path": str(detailed), "kind": "sku_ranking", "caption": ""},
        ]

    monkeypatch.setattr(report_charts, "generate_report_charts", fake_generate)
    outcome = whatsapp_report_visuals.generate_scheduled_report_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000002",
        period={"key": "2026-W29"},
        kind="weekly",
        chart_data={"analysis_type": "sales"},
        max_images=2,
    )

    assert [item["kind"] for item in outcome["artifacts"]] == ["sku_ranking"]
    assert outcome["discarded_kinds"] == ["kpi_card"]
    assert created["kpi"].exists() is False
    assert created["detailed"].exists() is True

    monthly = whatsapp_report_visuals.generate_scheduled_report_chart_artifacts(
        base_info_dir=tmp_path,
        client_id="000003",
        period={"key": "2026-07"},
        kind="monthly",
        chart_data={"analysis_type": "sales"},
        max_images=2,
    )
    assert monthly["report_kind"] == "monthly"
    assert [item["kind"] for item in monthly["artifacts"]] == ["sku_ranking"]
    assert monthly["discarded_kinds"] == ["kpi_card"]


def test_bridge_rejects_tampered_or_external_chart(tmp_path, monkeypatch):
    external = tmp_path / "outside.png"
    external.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-chart")
    monkeypatch.setattr(whatsapp_bridge, "_info_dir", lambda: tmp_path / "info")
    called = False

    def fake_post(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr(whatsapp_bridge, "_post_outbound_image", fake_post)
    results = whatsapp_bridge._whatsapp_deliver_report_artifacts(
        {},
        "wamid.1",
        [{"path": str(external), "sha256": "0" * 64, "expires_at": 9999999999}],
        "000002",
        max_images=1,
    )

    assert results == [{"success": False, "artifact_type": "report_chart", "error": "report_artifact_invalid_or_expired"}]
    assert called is False


def test_codex_task_persists_private_artifacts_without_exposing_them(tmp_path, monkeypatch):
    task = {
        "task_id": "chart-private-task",
        "status": "completed",
        "origin": "whatsapp",
        "client_id": "000002",
        "created_by": "admin",
        "whatsapp_artifacts": [{"path": "private.png", "artifact_type": "report_chart"}],
        "whatsapp_chart_expected": True,
        "whatsapp_chart_status": "generated",
        "whatsapp_chart_error": "",
    }
    task_path = tmp_path / "task.json"
    monkeypatch.setattr(codex_console, "_codex_task_path", lambda _task_id: str(task_path))

    public = codex_console._codex_public_task(task)
    codex_console._codex_persist_task(task)
    persisted = json.loads(task_path.read_text(encoding="utf-8"))

    assert "whatsapp_artifacts" not in public
    assert persisted["whatsapp_artifacts"] == task["whatsapp_artifacts"]
    assert persisted["whatsapp_chart_expected"] is True
    assert persisted["whatsapp_chart_status"] == "generated"


def test_codex_chart_keeps_store_scope_from_channel_metadata(monkeypatch):
    captured: dict = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return {"expected": False, "status": "test", "artifacts": []}

    monkeypatch.setattr(whatsapp_report_visuals, "generate_task_chart_artifacts", fake_generate)
    monkeypatch.setattr(codex_console, "_codex_update_task", lambda *_args, **_kwargs: None)
    task = {
        "task_id": "chart-store-scope",
        "origin": "whatsapp",
        "client_id": "000002",
        "prompt": "compare as vendas em um relatorio em imagem",
        "query_policy": {},
        "channel_metadata": {
            "query_policy": {
                "mode": "store_scope",
                "store": "JK Peças",
                "store_mode": "single",
                "read_only": True,
            }
        },
    }

    outcome = codex_console._codex_generate_whatsapp_chart_artifacts(
        "chart-store-scope", task, [],
    )

    assert outcome["status"] == "generation_failed"
    assert captured["query_policy"]["store"] == "JK Peças"
    assert captured["query_policy"]["store_mode"] == "single"
