import hashlib
import re
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from backend.services import report_charts


def _sales_data() -> dict:
    return {
        "analysis_type": "sales_report",
        "title": "Relatório de vendas — Uai Mineirinho",
        "source": "Mercado Livre API",
        "period_start": "2026-07-01",
        "period_end": "2026-07-03",
        "stores": ["Uai Mineirinho"],
        "coverage_complete": True,
        "kpis": {
            "Pedidos": 6,
            "Itens": 9,
            "Valor bruto": 900.0,
            "Valor líquido": 840.0,
        },
        "series": [
            {"date": "2026-07-01", "orders": 2, "items": 3, "gross": 300, "net": 280},
            {"date": "2026-07-02", "orders": 1, "items": 2, "gross": 200, "net": 190},
            {"date": "2026-07-03", "orders": 3, "items": 4, "gross": 400, "net": 370},
        ],
        "ranking": [
            {
                "sku": f"SKU {index:03d}",
                "title": "Produto completo com acentuação, descrição longa e compatibilidade preservada",
                "quantity": index,
                "gross": index * 37.5,
            }
            for index in range(1, 13)
        ],
    }


def _assert_valid_artifact(artifact: dict, root: Path) -> None:
    assert artifact["artifact_type"] == "report_chart"
    assert artifact["mime_type"] == "image/png"
    path = Path(artifact["path"])
    assert path.parent == root.resolve()
    assert path.name.startswith("report-chart-")
    assert path.suffix == ".png"
    assert 0 < artifact["byte_size"] <= 5 * 1024 * 1024
    assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
    assert datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00")) > datetime.now().astimezone()
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1350)
        assert image.mode == "RGB"


def test_sales_report_generates_trend_and_full_ranking(tmp_path: Path):
    artifacts = report_charts.generate_report_charts(_sales_data(), tmp_path)

    assert [item["kind"] for item in artifacts] == ["sales_trend", "sku_ranking"]
    assert len({item["path"] for item in artifacts}) == 2
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)
        assert "Uai Mineirinho" in artifact["title"]
        assert artifact["caption"] == ""
        assert "..." not in artifact["title"]


def test_zero_or_one_point_uses_visual_kpi_card(tmp_path: Path):
    data = {
        "analysis_type": "analysis",
        "title": "Análise sem histórico suficiente",
        "source": "JK Sistema",
        "stores": ["JK Peças"],
        "coverage_complete": False,
        "kpis": {"Pedidos": 0, "Valor bruto": "R$ 0,00"},
        "series": [{"date": "2026-07-13", "orders": 0, "gross": 0}],
    }

    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "kpi_card"
    assert artifacts[0]["caption"] == ""
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_empty_data_still_generates_insufficient_data_card(tmp_path: Path):
    artifacts = report_charts.generate_report_charts(
        {"analysis_type": "analysis", "title": "Análise de vendas", "source": "Mercado Livre"},
        tmp_path,
    )
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "kpi_card"
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_weekly_report_is_limited_to_one_category_chart(tmp_path: Path):
    data = {
        "analysis_type": "relatorio_semanal",
        "title": "Resumo operacional semanal",
        "source": "JK Sistema",
        "period_start": "07/07/2026",
        "period_end": "13/07/2026",
        "categories": [
            {"severity": "Crítico", "category": "Risco de ruptura", "count": 8},
            {"severity": "Alto", "category": "Estoque parado", "count": 17},
        ],
        "series": [
            {"date": "2026-07-07", "items": 2},
            {"date": "2026-07-13", "items": 3},
        ],
    }
    artifacts = report_charts.generate_report_charts(data, tmp_path, max_images=2)
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "weekly_categories"
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_weekly_report_without_categories_uses_one_trend(tmp_path: Path):
    data = {
        "analysis_type": "weekly_report",
        "title": "Vendas da semana",
        "source": "Mercado Livre API",
        "period_start": "2026-07-07",
        "period_end": "2026-07-13",
        "stores": ["JK Peças"],
        "series": [
            {"date": "2026-07-07", "gross": 100},
            {"date": "2026-07-13", "gross": 250},
        ],
    }
    artifacts = report_charts.generate_report_charts(data, tmp_path, max_images=2)
    assert len(artifacts) == 1
    assert artifacts[0]["kind"] == "weekly_trend"
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_multiple_stores_generate_comparison_and_trend(tmp_path: Path):
    data = {
        "analysis_type": "sales_comparison",
        "title": "Comparativo de vendas",
        "source": "Mercado Livre API",
        "period_start": "2026-07-01",
        "period_end": "2026-07-02",
        "stores": ["JK Peças", "Uai Mineirinho"],
        "series": [
            {"date": "2026-07-01", "store": "JK Peças", "orders": 2, "items": 3, "gross": 300},
            {"date": "2026-07-02", "store": "JK Peças", "orders": 1, "items": 1, "gross": 100},
            {"date": "2026-07-01", "store": "Uai Mineirinho", "orders": 1, "items": 2, "gross": 200},
            {"date": "2026-07-02", "store": "Uai Mineirinho", "orders": 2, "items": 4, "gross": 400},
        ],
    }
    artifacts = report_charts.generate_report_charts(data, tmp_path)
    assert [item["kind"] for item in artifacts] == ["store_comparison", "store_trend"]
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


def test_multiple_store_names_without_per_store_data_do_not_invent_comparison(tmp_path: Path):
    data = {
        "analysis_type": "sales_comparison",
        "title": "Comparativo sem abertura por loja",
        "source": "Mercado Livre API",
        "stores": ["JK Peças", "Uai Mineirinho"],
        "kpis": {"Pedidos": 3, "Valor bruto": 450},
        "series": [
            {"date": "2026-07-01", "orders": 1, "gross": 150},
            {"date": "2026-07-02", "orders": 2, "gross": 300},
        ],
    }
    artifacts = report_charts.generate_report_charts(data, tmp_path)
    assert [item["kind"] for item in artifacts] == ["kpi_card"]
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_pii_is_removed_and_unknown_payload_is_never_used(tmp_path: Path):
    data = {
        "analysis_type": "analysis",
        "title": "Comprador: Maria Silva | email: maria@example.com | telefone: +55 37 99837-9212",
        "source": "Bearer super-secret-access-token-value",
        "kpis": {"Pedidos": 1},
        "buyer_payload": {"address": "Rua privada", "cpf": "123.456.789-01"},
    }
    artifacts = report_charts.generate_report_charts(data, tmp_path)
    metadata = "\n".join(str(value) for value in artifacts[0].values())
    assert "Maria Silva" not in metadata
    assert "maria@example.com" not in metadata
    assert "99837-9212" not in metadata
    assert "super-secret" not in metadata
    assert "Rua privada" not in metadata
    assert "123.456.789-01" not in metadata
    assert "[dado protegido]" in metadata
    _assert_valid_artifact(artifacts[0], tmp_path)


def test_series_totals_must_close_before_rendering(tmp_path: Path):
    data = _sales_data()
    data["kpis"]["Valor bruto"] = 901
    with pytest.raises(ValueError, match="chart_data_total_mismatch:gross"):
        report_charts.generate_report_charts(data, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_image_limit_is_enforced_and_zero_disables_generation(tmp_path: Path):
    assert report_charts.generate_report_charts(_sales_data(), tmp_path, max_images=0) == []
    artifacts = report_charts.generate_report_charts(_sales_data(), tmp_path, max_images=99)
    assert len(artifacts) == 2


def test_invalid_contract_is_rejected(tmp_path: Path):
    with pytest.raises(TypeError, match="chart_data_must_be_dict"):
        report_charts.generate_report_charts([], tmp_path)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="chart_data_invalid_period"):
        report_charts.generate_report_charts(
            {"period_start": "2026-07-13", "period_end": "2026-07-01"},
            tmp_path,
        )
    with pytest.raises(ValueError, match="chart_data_ranking_too_many_rows"):
        report_charts.generate_report_charts({"ranking": [{}] * 20001}, tmp_path)


@pytest.mark.parametrize(
    ("days", "expected_mode", "expected_first_label"),
    [
        (31, "day", "01/01"),
        (32, "week", "Semana 29/12"),
        (180, "week", "Semana 29/12"),
        (181, "month", "01/2026"),
    ],
)
def test_period_boundaries_choose_daily_weekly_or_monthly_buckets(
    days: int,
    expected_mode: str,
    expected_first_label: str,
):
    from datetime import date, timedelta

    start = date(2026, 1, 1)
    end = start + timedelta(days=days - 1)
    data = report_charts._validate_and_normalize(
        {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "series": [
                {"date": start.isoformat(), "orders": 1, "items": 2, "gross": 10},
                {"date": end.isoformat(), "orders": 3, "items": 4, "gross": 20},
            ],
        }
    )

    points = report_charts._aggregate_series(data)

    assert report_charts._bucket_mode(data) == expected_mode
    assert points[0]["label"] == expected_first_label
    assert sum(point["orders"] for point in points) == 4
    assert sum(point["items"] for point in points) == 6
    assert sum(point["gross"] for point in points) == 30


def test_weekly_and_monthly_buckets_consolidate_rows_without_losing_totals():
    weekly = report_charts._validate_and_normalize(
        {
            "period_start": "2026-01-01",
            "period_end": "2026-02-01",
            "series": [
                {"date": "2026-01-05", "orders": 1, "gross": 10},
                {"date": "2026-01-06", "orders": 2, "gross": 20},
            ],
        }
    )
    monthly = report_charts._validate_and_normalize(
        {
            "period_start": "2026-01-01",
            "period_end": "2026-07-01",
            "series": [
                {"date": "2026-01-05", "orders": 1, "gross": 10},
                {"date": "2026-01-20", "orders": 2, "gross": 20},
            ],
        }
    )

    weekly_points = report_charts._aggregate_series(weekly)
    monthly_points = report_charts._aggregate_series(monthly)

    assert [(row["label"], row["orders"], row["gross"]) for row in weekly_points] == [
        ("Semana 05/01", 3.0, 30.0)
    ]
    assert [(row["label"], row["orders"], row["gross"]) for row in monthly_points] == [
        ("01/2026", 3.0, 30.0)
    ]


def test_stock_analysis_generates_distribution_and_balance_ranking(tmp_path: Path):
    data = {
        "analysis_type": "stock",
        "title": "Análise visual de estoque",
        "source": "Bling",
        "coverage_complete": False,
        "stores": ["Uai Mineirinho"],
        "kpis": {"Itens": 186},
        "categories": [
            {"label": "Loja", "value": 61},
            {"label": "Fulfillment", "value": 113},
            {"label": "Devoluções e conserto", "value": 12},
        ],
        "ranking": [
            {"sku": "SKU 001", "title": "Cebolão do Radiador Sensor Temperatura", "quantity": 61, "value": 61},
            {"sku": "SKU 442", "title": "Macaco Pneumático", "quantity": 25, "value": 25},
        ],
    }

    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert [item["kind"] for item in artifacts] == ["stock_distribution", "stock_ranking"]
    assert artifacts[0]["caption"] == ""
    assert "Maiores saldos" in artifacts[1]["title"]
    normalized = report_charts._validate_and_normalize(data)
    ranking = report_charts._ranking_rows(normalized)
    assert ranking[0]["money"] is False
    assert ranking[0]["value"] == 61
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


def test_internal_kpi_keys_are_rendered_with_clear_portuguese_labels():
    data = report_charts._validate_and_normalize(
        {
            "kpis": {
                "orders": 4,
                "items": 5,
                "gross": 220,
                "refunds": 10,
                "net": 210,
                "listings": 12,
            }
        }
    )
    labels = [item["label"] for item in data["kpis"]]
    assert labels == ["Pedidos", "Itens", "Valor bruto", "Estornos", "Valor líquido", "Anúncios"]


def test_stock_capital_ranking_respects_explicit_money_and_value_label(tmp_path: Path):
    data = {
        "analysis_type": "estoque_parado",
        "title": "Capital conhecido em estoque parado",
        "source": "JK Sistema",
        "stores": ["JK Peças"],
        "ranking": [
            {
                "sku": "SKU 100",
                "title": "Produto com custo cadastrado",
                "quantity": 20,
                "value": 2500.5,
                "money": True,
                "value_label": "Capital conhecido",
            }
        ],
    }

    normalized = report_charts._validate_and_normalize(data)
    ranking = report_charts._ranking_rows(normalized)
    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert ranking[0]["money"] is True
    assert ranking[0]["value_label"] == "Capital conhecido"
    assert "Maior capital em estoque" in artifacts[-1]["title"]
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


def test_stockout_ranking_uses_textual_risk_while_bar_stays_numeric(tmp_path: Path):
    data = {
        "analysis_type": "stockout_forecast",
        "title": "Análise visual de risco de ruptura",
        "source": "Histórico de vendas e estoque do JK Sistema",
        "stores": ["Uai Mineirinho"],
        "categories": [
            {"label": "Crítico", "value": 1},
            {"label": "Alto", "value": 1},
        ],
        "ranking": [
            {
                "sku": "CRIT-1",
                "title": "Produto crítico",
                "quantity": 3,
                "value": 5,
                "money": False,
                "display_value": "Crítico · 3 dias",
                "value_label": "Risco",
            },
            {
                "sku": "HIGH-2",
                "title": "Produto alto risco",
                "quantity": 10,
                "value": 4,
                "money": False,
                "display_value": "Alto · 10 dias",
                "value_label": "Risco",
            },
        ],
    }

    normalized = report_charts._validate_and_normalize(data)
    ranking = report_charts._ranking_rows(normalized)
    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert ranking[0]["value"] == 5
    assert ranking[0]["quantity"] == 3
    assert ranking[0]["display_value"] == "Crítico · 3 dias"
    assert report_charts._rendered_ranking_value(ranking[0]) == "Crítico · 3 dias"
    assert [item["kind"] for item in artifacts] == ["stock_distribution", "stock_ranking"]
    assert "SKUs com maior risco de ruptura" in artifacts[1]["title"]
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


def test_listing_analysis_identifies_sold_quantity_as_accumulated(tmp_path: Path):
    data = {
        "analysis_type": "listings",
        "title": "Análise visual de anúncios",
        "source": "Mercado Livre — sold_quantity acumulado do anúncio",
        "coverage_complete": True,
        "stores": ["JK Peças"],
        "kpis": {"Anúncios": 3, "Ativos": 2},
        "categories": [
            {"label": "active", "value": 2},
            {"label": "paused", "value": 1},
        ],
        "ranking": [
            {"sku": "MLB100", "title": "Anúncio com nome completo", "quantity": 38, "value": 38},
            {"sku": "MLB200", "title": "Outro anúncio", "quantity": 12, "value": 12},
        ],
    }

    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert [item["kind"] for item in artifacts] == ["listing_status", "listing_sold_ranking"]
    assert "Vendidos acumulados por anúncio" in artifacts[1]["title"]
    assert artifacts[1]["caption"] == ""
    ranking = report_charts._ranking_rows(report_charts._validate_and_normalize(data))
    assert ranking[0]["money"] is False
    assert ranking[0]["value"] == 38
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


def test_long_labels_are_preserved_without_artificial_ellipsis(tmp_path: Path):
    long_name = (
        "Cebolão do Radiador Sensor Temperatura compatível com veículos nacionais e importados "
        "com conector reforçado e aplicação completa informada pelo catálogo"
    )
    data = _sales_data()
    data["ranking"] = [{"sku": "SKU 001-ABC", "title": long_name, "quantity": 2, "gross": 199.9}]

    normalized = report_charts._validate_and_normalize(data)
    ranking = report_charts._ranking_rows(normalized)
    artifacts = report_charts.generate_report_charts(data, tmp_path)

    assert ranking[0]["label"] == f"SKU 001-ABC — {long_name}"
    assert "..." not in ranking[0]["label"]
    assert len(artifacts) == 2
    for artifact in artifacts:
        _assert_valid_artifact(artifact, tmp_path)


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("orders", 7),
        ("items", 10),
        ("gross", 301),
        ("paid", 301),
        ("refunds", 16),
        ("net", 286),
    ],
)
def test_every_supported_series_total_is_reconciled(metric: str, expected: float, tmp_path: Path):
    data = {
        "analysis_type": "sales",
        "kpis": {"orders": 6, "items": 9, "gross": 300, "paid": 300, "refunds": 15, "net": 285},
        "series": [
            {"date": "2026-07-12", "orders": 2, "items": 4, "gross": 120, "paid": 120, "refunds": 5, "net": 115},
            {"date": "2026-07-13", "orders": 4, "items": 5, "gross": 180, "paid": 180, "refunds": 10, "net": 170},
        ],
    }
    data["kpis"][metric] = expected

    with pytest.raises(ValueError, match=f"chart_data_total_mismatch:{metric}"):
        report_charts.generate_report_charts(data, tmp_path)
    assert list(tmp_path.iterdir()) == []
