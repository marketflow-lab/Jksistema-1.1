from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from backend.modules.vendas import analytics
from backend.modules.vendas.performance import invalidate_vendas_cache
from backend.modules.vendas.router import create_vendas_router
from backend.services import vendas_grafico


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 10, 12, 0, 0)
        return value if tz is None else value.astimezone(tz)


def _create_sales_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE vendas (
                id_unico TEXT,
                data TEXT,
                devolucao INTEGER,
                sku TEXT,
                quantidade REAL,
                valor REAL,
                loja_conta TEXT,
                unidade_negocio TEXT,
                numero TEXT,
                comprador TEXT,
                canal TEXT,
                situacao TEXT,
                nota_fiscal_id TEXT
            );
            INSERT INTO vendas VALUES
                ('dup', '2026-08-02T08:00:00', 0, 'SKU-A', 1, 100, 'Loja A', 'Unidade 1', '1', 'Cliente', 'Marketplace', 'Faturado', 'NF-1'),
                ('dup', '2026-08-02T08:00:00', 0, 'SKU-A', 1, 100, 'Loja A', 'Unidade 1', '1', 'Cliente', 'Marketplace', 'Faturado', 'NF-1'),
                ('a-2', '2026-08-10T09:00:00', 0, 'SKU-A', 2, 170, 'Loja A', 'Unidade 1', '2', 'Cliente', 'Marketplace', 'Faturado', 'NF-2'),
                ('a-old', '2026-07-15T09:00:00', 0, 'SKU-A', 1, 1000, 'Loja A', 'Unidade 1', '3', 'Cliente', 'Marketplace', 'Faturado', 'NF-3'),
                ('b-1', '2026-08-05T09:00:00', 0, 'SKU-B', 1, 500, 'Loja A', 'Unidade 1', '4', 'Cliente', 'Marketplace', 'Faturado', 'NF-4');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _configure_graph_test(monkeypatch, db_path: Path) -> None:
    monkeypatch.setattr(analytics, "datetime", _FixedDatetime)
    monkeypatch.setattr(analytics, "_listar_bancos_vendas_tenant", lambda *_args: [str(db_path)])
    monkeypatch.setattr(analytics, "_deve_excluir_venda_ebazar", lambda *_args: False)
    monkeypatch.setattr(analytics, "_filtrar_vendas_lojas_ativas", lambda rows, *_args: rows)
    monkeypatch.setattr(analytics, "_sql_filtro_unidade_devolucao", lambda *_args: ("", []))
    monkeypatch.setattr(analytics, "_sql_filtro_loja_notas_entrada", lambda *_args: ("", []))
    monkeypatch.setattr(
        analytics,
        "_vendas_series_estoque_historico",
        lambda **_kwargs: {
            "estoque_geral": [],
            "estoque_sku": [],
            "estoque_skus_com_saldo": [],
            "estoque_skus_pareto_com_saldo": [],
            "estoque_meta": {},
        },
    )


def test_forecast_is_separate_filtered_deduplicated_and_part_of_cache_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "vendas.db"
    _create_sales_db(db_path)
    _configure_graph_test(monkeypatch, db_path)
    invalidate_vendas_cache("forecast-test")

    arguments = {
        "intervalo": "mes",
        "sku": "SKU-A",
        "data_inicio": "2026-07-01",
        "data_fim": "2026-08-10",
        "client_id": "forecast-test",
    }
    without_forecast = analytics.grafico_vendas(**arguments)
    with_forecast = analytics.grafico_vendas(
        **arguments,
        incluir_previsao_mes_atual=True,
    )

    assert "previsao_mes_atual" not in without_forecast
    assert without_forecast["valores_vendas"] == [1000, 270]
    assert with_forecast["valores_vendas"] == without_forecast["valores_vendas"]
    assert with_forecast["previsao_mes_atual"] == {
        "mes": "2026-08",
        "data_referencia": "2026-08-10",
        "valor_realizado": 270.0,
        "valor_projetado": 837.0,
        "ritmo_diario": 27.0,
        "dias_decorridos": 10,
        "dias_no_mes": 31,
        "metodo": "media_diaria_linear",
    }

    outside_range = analytics.grafico_vendas(
        **{**arguments, "data_fim": "2026-08-09"},
        incluir_previsao_mes_atual=True,
    )
    assert "previsao_mes_atual" not in outside_range

    incomplete_current_month = analytics.grafico_vendas(
        **{**arguments, "data_inicio": "2026-08-05"},
        incluir_previsao_mes_atual=True,
    )
    assert "previsao_mes_atual" not in incomplete_current_month

    zero_sales = analytics.grafico_vendas(
        **{**arguments, "sku": "SKU-ZERO"},
        incluir_previsao_mes_atual=True,
    )
    assert zero_sales["previsao_mes_atual"]["valor_realizado"] == 0.0
    assert zero_sales["previsao_mes_atual"]["valor_projetado"] == 0.0
    assert zero_sales["previsao_mes_atual"]["ritmo_diario"] == 0.0


def test_linear_forecast_uses_calendar_days_and_public_default_is_false() -> None:
    rows = [
        ("1", "2024-02-10T08:00:00", 0, "SKU-A", 1, 100, "Loja A"),
        ("2", "2024-01-31T08:00:00", 0, "SKU-A", 1, 900, "Loja A"),
        ("3", "2024-02-11T08:00:00", 0, "SKU-A", 1, 700, "Loja A"),
    ]
    forecast = analytics._calcular_previsao_mes_atual(rows, datetime(2024, 2, 10, 12, 0, 0))

    assert forecast["dias_decorridos"] == 10
    assert forecast["dias_no_mes"] == 29
    assert forecast["valor_realizado"] == 100.0
    assert forecast["ritmo_diario"] == 10.0
    assert forecast["valor_projetado"] == 290.0

    primeiro_dia = analytics._calcular_previsao_mes_atual(
        [("4", "2026-08-01T08:00:00", 0, "SKU-A", 1, 100, "Loja A")],
        datetime(2026, 8, 1, 12, 0, 0),
    )
    assert primeiro_dia["dias_decorridos"] == 1
    assert primeiro_dia["valor_projetado"] == 3100.0

    ultimo_dia = analytics._calcular_previsao_mes_atual(
        [("5", "2026-08-31T08:00:00", 0, "SKU-A", 1, 310, "Loja A")],
        datetime(2026, 8, 31, 12, 0, 0),
    )
    assert ultimo_dia["dias_decorridos"] == 31
    assert ultimo_dia["valor_projetado"] == ultimo_dia["valor_realizado"]

    assert inspect.signature(analytics.grafico_vendas).parameters["incluir_previsao_mes_atual"].default is False
    assert inspect.signature(vendas_grafico.grafico_vendas).parameters["incluir_previsao_mes_atual"].default is False


def test_router_forwards_optional_forecast_flag() -> None:
    captured = {}

    class _Service:
        def grafico_vendas(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    router = create_vendas_router(
        _Service(),
        object(),
        SimpleNamespace(get_tenant_id=lambda: "tenant-test"),
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/vendas/grafico")

    assert endpoint(client_id="tenant-test", incluir_previsao_mes_atual=True) == {"ok": True}
    assert captured["client_id"] == "tenant-test"
    assert captured["incluir_previsao_mes_atual"] is True
