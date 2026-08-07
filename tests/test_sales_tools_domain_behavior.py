from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

from backend.services.sales_tools import api
from backend.services.sales_tools import inventory, repository


def _returns_database(path, sku: str, quantity: float, value: float, return_date: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE notas_entrada_itens (
            data_emissao TEXT, sku TEXT, descricao TEXT,
            quantidade REAL, valor_total REAL, devolucao INTEGER
        )"""
    )
    connection.execute(
        "INSERT INTO notas_entrada_itens VALUES (?, ?, ?, ?, ?, 1)",
        (return_date, sku, f"Produto {sku}", quantity, value),
    )
    connection.commit()
    connection.close()


def test_comparison_period_parser_preserves_named_and_numeric_months() -> None:
    assert api.extract_comparison_periods("Compare maio de 2026 com junho de 2026") == (
        ("2026-05-01", "2026-05-31"),
        ("2026-06-01", "2026-06-30"),
    )
    assert api.extract_comparison_periods("Compare 04/25 com 05/25") == (
        ("2025-04-01", "2025-04-30"),
        ("2025-05-01", "2025-05-31"),
    )


def test_returns_summary_aggregates_all_authorized_databases(tmp_path, monkeypatch) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _returns_database(first, "SKU-A", 2, 100, "2026-07-01")
    _returns_database(second, "SKU-A", 3, 180, "2026-07-03")
    monkeypatch.setattr(
        repository,
        "_listar_bancos_vendas_tenant",
        lambda *_args, **_kwargs: [str(first), str(second)],
    )
    monkeypatch.setattr(
        repository,
        "_sql_filtro_loja_notas_entrada",
        lambda *_args, **_kwargs: ("", []),
    )

    result = api.returns_summary("tenant", "2026-07-01", "2026-07-31")

    assert result is not None
    assert result["bancos_consultados"] == 2
    assert result["quantidade_devolvida_total"] == 5
    assert result["valor_devolvido_total"] == 280
    assert result["ultima_devolucao"] == "2026-07-03"
    assert result["top_skus"][0]["sku"] == "SKU-A"


def test_stockout_forecast_keeps_complete_chart_contract(tmp_path, monkeypatch) -> None:
    database = tmp_path / "sales.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE vendas (
            data TEXT, sku TEXT, quantidade REAL,
            devolucao INTEGER, situacao TEXT
        )"""
    )
    connection.execute(
        "INSERT INTO vendas VALUES ('2026-07-31', 'SKU-A', 30, 0, 'Concluida')"
    )
    connection.commit()
    connection.close()
    products = pd.DataFrame([
        {
            "sku_norm": "SKU-A",
            "nome_tool": "Produto A",
            "saldo_loja": 5,
            "saldo_full": 0,
        }
    ])
    monkeypatch.setattr(inventory, "_ia_carregar_produtos_tool_df", lambda *_args: products)
    monkeypatch.setattr(inventory, "_ia_obter_data_referencia_vendas", lambda *_args: date(2026, 7, 31))
    monkeypatch.setattr(inventory, "_listar_bancos_vendas_tenant", lambda *_args: [str(database)])
    monkeypatch.setattr(inventory, "_sql_filtro_loja_vendas", lambda *_args: ("", []))
    monkeypatch.setattr(inventory, "_ia_tool_resolver_sku", lambda *_args: "")

    payload = api.get_stockout_forecast("tenant", "previsao", lookback_days=30)

    assert payload is not None
    result = payload["result"]
    assert result["total_skus_analisados"] == 1
    assert result["itens"][0]["risco_ruptura"] == "critico"
    assert result["itens"][0]["dias_ate_ruptura"] == 5
    assert result["chart_data"]["schema"] == "jk.stock.stockout_forecast.v1"
    assert result["chart_data"]["coverage_complete"] is True
