import json
from datetime import datetime

from backend.modules.vendas import analytics
from backend.modules.vendas.analytics import _calcular_skus_pareto_80, _filtrar_vendas_lojas_ativas
from backend.services import estoque_historico


def _configurar_tenant(monkeypatch, tmp_path):
    def tenant_path(client_id):
        path = tmp_path / str(client_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(estoque_historico, "get_tenant_path", tenant_path)


def _venda(sku, valor, identificador, situacao="Faturado", nota_fiscal_id=""):
    return (
        identificador,
        "2026-07-15",
        0,
        sku,
        1,
        valor,
        "Loja A",
        "1",
        identificador,
        "Comprador",
        "Mercado Livre",
        situacao,
        nota_fiscal_id,
    )


def test_pareto_inclui_menor_conjunto_e_preserva_zero_a_esquerda():
    rows = [
        _venda("001", 50, "v1"),
        _venda("B", 30, "v2"),
        _venda("C", 20, "v3"),
        _venda("ESTORNO DE CRÉDITO ICMS", 500, "fiscal"),
        _venda("ESTORNO DE CREDITO ICMS", 500, "fiscal-sem-acento"),
        _venda("", 100, "sem-sku"),
        _venda("CANCELADO", 900, "cancelada", situacao="Cancelado"),
        _venda("B", 30, "pedido-b", situacao="Em aberto", nota_fiscal_id="NF-B"),
        _venda("B", 0, "fatura-b", situacao="Faturado", nota_fiscal_id="NF-B"),
    ]

    skus, meta = _calcular_skus_pareto_80(rows)

    assert skus == ["001", "B"]
    assert meta["pareto_skus_total"] == 2
    assert meta["pareto_participacao"] == 0.8
    assert meta["pareto_sku_corte"] == "B"


def test_pareto_todas_lojas_ignora_conta_fora_do_cadastro(monkeypatch, tmp_path):
    tenant = tmp_path / "cliente-a"
    tenant.mkdir(parents=True)
    (tenant / "lojas_config.json").write_text(
        json.dumps([{"nome": "JK Peças"}, {"nome": "Carlos José"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(analytics, "get_tenant_path", lambda _client_id: str(tenant))
    rows = [
        _venda("A", 50, "jk")[:6] + ("JK Peças",) + _venda("A", 50, "jk")[7:],
        _venda("B", 30, "carlos")[:6] + ("Carlos José",) + _venda("B", 30, "carlos")[7:],
        _venda("LEGADO", 999, "antiga")[:6] + ("Loja Antiga",) + _venda("LEGADO", 999, "antiga")[7:],
    ]

    filtradas = _filtrar_vendas_lojas_ativas(rows, "cliente-a", "__todas")

    assert [row[3] for row in filtradas] == ["A", "B"]


def test_linha_pareto_conta_apenas_saldo_loja_positivo(monkeypatch, tmp_path):
    _configurar_tenant(monkeypatch, tmp_path)
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        [
            {"sku": "001", "saldo_loja": 5, "saldo_full": 0},
            {"sku": "B", "saldo_loja": 3, "saldo_full": 0},
            {"sku": "C", "saldo_loja": 0, "saldo_full": 90},
        ],
        event_id="junho",
        recorded_at="2026-06-30T20:00:00-03:00",
    )
    estoque_historico._registrar_snapshot_historico_estoque(
        "cliente-a",
        "Loja A",
        [
            {"sku": "001", "saldo_loja": 0, "saldo_full": 80},
            {"sku": "B", "saldo_loja": 2, "saldo_full": 0},
            {"sku": "C", "saldo_loja": 4, "saldo_full": 0},
        ],
        event_id="julho",
        recorded_at="2026-07-31T20:00:00-03:00",
    )

    serie = estoque_historico._vendas_series_estoque_historico(
        "cliente-a",
        "Loja A",
        "mes",
        datetime.fromisoformat("2026-06-01"),
        datetime.fromisoformat("2026-07-31"),
        ["2026-06", "2026-07"],
        True,
        False,
        None,
        pareto_skus=["001", "C"],
    )

    assert serie["estoque_skus_com_saldo"] == [2, 2]
    assert serie["estoque_skus_pareto_com_saldo"] == [1, 1]
