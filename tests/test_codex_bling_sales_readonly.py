from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.services import codex_bling_tools


class _Response:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload


def test_bling_complete_query_deadline_caps_calls_and_fails_closed():
    with patch.object(codex_bling_tools.time, "monotonic", return_value=100.0):
        assert codex_bling_tools._remaining_timeout(105.0, 20) == 5
        with pytest.raises(codex_bling_tools.requests.exceptions.Timeout):
            codex_bling_tools._remaining_timeout(100.0, 20)


def test_codex_bling_get_never_retries_429():
    with patch.object(codex_bling_tools.requests, "get", return_value=_Response(429, {"error": "rate"})) as request_get:
        payload, status = codex_bling_tools._get_json("token", "/pedidos/vendas", {"pagina": 1})

    assert status == 429
    assert payload == {"error": "rate"}
    request_get.assert_called_once()
    assert request_get.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_codex_bling_get_retries_one_transient_failure_only():
    with (
        patch.object(
            codex_bling_tools.requests,
            "get",
            side_effect=[_Response(503, {"error": "temporary"}), _Response(200, {"data": [{"id": 1}]})],
        ) as request_get,
        patch.object(codex_bling_tools.time, "sleep"),
    ):
        payload, status = codex_bling_tools._get_json("token", "/pedidos/vendas")

    assert status == 200
    assert payload == [{"id": 1}]
    assert request_get.call_count == 2


def test_connected_bling_store_is_required_and_exact():
    stores = [
        {"nome": "JK Peças", "integracoes": {"bling": {"access_token": "a", "refresh_token": "r"}}},
        {"nome": "Carlos José", "integracoes": {"bling": {"access_token": "b", "refresh_token": "s"}}},
    ]
    with patch("backend.services.integracoes.carregar_lojas", return_value=stores):
        missing, missing_warnings = codex_bling_tools._connected_stores("000002", None, require_exact=True)
        invalid, invalid_warnings = codex_bling_tools._connected_stores("000002", "Carlos", require_exact=True)
        exact, exact_warnings = codex_bling_tools._connected_stores("000002", "Carlos Jose", require_exact=True)

    assert missing == []
    assert "Informe uma loja" in missing_warnings[0]
    assert invalid == []
    assert "inexistente ou ambigua" in invalid_warnings[0]
    assert [name for name, _ in exact] == ["Carlos José"]
    assert exact_warnings == []


def test_bling_sales_orders_are_compact_readonly_and_capped():
    list_order = {
        "id": 123,
        "numero": 9,
        "data": "2026-07-12",
        "total": 120.0,
        "buyer": {"email": "must-not-leak@example.com"},
    }
    detail = {
        **list_order,
        "contato": {"nome": "Must Not Leak", "numeroDocumento": "000"},
        "itens": [
            {"codigo": "SKU-1", "descricao": "Produto", "quantidade": 2, "valor": 60.0},
        ],
    }
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        return ([list_order], 200, cfg) if len(calls) == 1 else (detail, 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Peças", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_sales_orders(
            "000002", "vendas julho", "JK Pecas", "2026-07-01", "2026-07-12", limit=999
        )

    result = raw["result"]
    assert raw["arguments"]["limite"] == 100
    assert result["read_only"] is True
    assert result["method"] == "GET"
    assert result["pedidos_total"] == 1
    assert result["faturamento_total"] == 120.0
    assert result["top_skus"][0]["sku"] == "SKU-1"
    assert result["paging"]["limit"] == 100
    assert "buyer" not in result["pedidos"][0]
    assert "contato" not in result["pedidos"][0]


def test_bling_sales_orders_honors_offset_and_returns_next_offset():
    rows = [{"id": index, "numero": index, "total": 10.0} for index in range(1, 6)]
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        if len(calls) == 1:
            return rows, 200, cfg
        return {"id": len(calls), "itens": []}, 200, cfg

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Pecas", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_sales_orders(
            "000002",
            "vendas julho",
            "JK Pecas",
            "2026-07-01",
            "2026-07-12",
            limit=2,
            offset=2,
        )

    result = raw["result"]
    assert [item["id"] for item in result["pedidos"]] == [3, 4]
    assert result["paging"]["offset"] == 2
    assert result["paging"]["next_offset"] == 4
    assert result["paging"]["has_more"] is True


def test_bling_sales_orders_applies_explicit_status_filter_locally():
    rows = [
        {"id": 1, "situacao": {"nome": "Em aberto"}, "total": 10.0},
        {"id": 2, "situacao": {"nome": "Atendido"}, "total": 20.0},
        {"id": 3, "situacao": {"nome": "Atendido"}, "total": 30.0},
    ]
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        return (rows, 200, cfg) if len(calls) == 1 else ({"id": 2, "itens": []}, 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Pecas", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_sales_orders(
            "000002",
            "vendas atendidas",
            "JK Pecas",
            limit=1,
            status="Atendido",
        )

    result = raw["result"]
    assert [item["id"] for item in result["pedidos"]] == [2]
    assert result["paging"]["next_offset"] == 1
    assert any("filtro de status" in warning for warning in result["warnings"])


def test_bling_sales_401_is_reconnect_required():
    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Peças", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", return_value=({"error": "unauthorized"}, 401, {})),
    ):
        raw = codex_bling_tools.tool_bling_sales_orders(
            "000002", "vendas julho", "JK Pecas", "2026-07-01", "2026-07-12", limit=10
        )

    result = raw["result"]
    assert result["records"] == 0
    assert result["reconnect_required"] is True
    assert result["sources"][0]["method"] == "GET"
    assert result["sources"][0]["status"] == 401


def test_bling_stock_balances_excludes_full_deposits():
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        if len(calls) == 1:
            return ({"products": [{"id": 10, "codigo": "001", "nome": "Produto"}], "used": {}}, 200, cfg)
        if len(calls) == 2:
            return ([
                {"id": 1, "descricao": "Loja", "situacao": 1, "padrao": True, "desconsiderarSaldo": False},
                {"id": 2, "descricao": "Mercado Livre Full", "situacao": 1, "padrao": False, "desconsiderarSaldo": False},
            ], 200, cfg)
        return ([{
            "produto": {"id": 10, "codigo": "001", "nome": "Produto"},
            "depositos": [
                {"id": 1, "saldoFisico": 5, "saldoVirtual": 5},
                {"id": 2, "saldoFisico": 9, "saldoVirtual": 9},
            ],
        }], 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Pecas", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_stock_balances("000002", "estoque SKU 001", "JK Pecas", limit=10)

    row = raw["result"]["saldos"][0]
    assert row["saldo_bruto_retornado"] == 14
    assert row["saldo_total"] == 5
    assert row["saldo_loja_total"] == 5
    assert row["saldo_total_confiavel"] is True
    assert row["cobertura_depositos_completa"] is True
    assert row["full_excluido"] is True
    assert [item["descricao"] for item in row["depositos"]] == ["Loja"]
    assert [item["descricao"] for item in row["depositos_excluidos"]] == ["Mercado Livre Full"]
    assert row["depositos_excluidos"][0]["motivo"] == "Estoque Full/Fulfillment"
    assert raw["result"]["stock_scope"] == "bling_non_full_only"
    assert any("Full foram ignorados" in warning for warning in raw["result"]["warnings"])


def test_bling_deposit_metadata_parses_active_and_ignored_flags_without_string_truthiness():
    assert codex_bling_tools._bling_deposito_ativo({"situacao": "Ativo"}) is True
    assert codex_bling_tools._bling_deposito_ativo({"situacao": 0}) is False
    assert codex_bling_tools._bling_deposito_ativo({}) is None
    assert codex_bling_tools._bling_deposito_desconsidera_saldo({"desconsiderarSaldo": "false"}) is False
    assert codex_bling_tools._bling_deposito_desconsidera_saldo({"desconsiderarSaldo": "true"}) is True
    assert codex_bling_tools._bling_deposito_desconsidera_saldo({}) is None


def test_bling_stock_balances_maps_real_deposits_and_returns_only_eligible_store_stock():
    catalog = [
        {"id": 1, "descricao": "Loja", "situacao": 1, "padrao": True, "desconsiderarSaldo": False},
        {"id": 2, "descricao": "Fulfillment", "situacao": 1, "padrao": False, "desconsiderarSaldo": False},
        {"id": 3, "descricao": "Devolucoes/Conserto", "situacao": 1, "padrao": False, "desconsiderarSaldo": True},
        {"id": 4, "descricao": "Defeito", "situacao": 1, "padrao": False, "desconsiderarSaldo": True},
        {
            "id": 5,
            "descricao": "Shopee 205945277 (Fulfillment)",
            "situacao": 1,
            "padrao": False,
            "desconsiderarSaldo": False,
        },
    ]
    balances = [{
        "produto": {"id": 10, "codigo": "001", "nome": "Cebolao do Radiador Sensor Temperatura"},
        "depositos": [
            {"id": 1, "saldoFisico": 61, "saldoVirtual": 61},
            {"id": 2, "saldoFisico": 113, "saldoVirtual": 113},
            {"id": 3, "saldoFisico": 10, "saldoVirtual": 10},
            {"id": 4, "saldoFisico": 2, "saldoVirtual": 2},
            {"id": 5, "saldoFisico": 0, "saldoVirtual": 0},
        ],
    }]
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        if len(calls) == 1:
            return ({"products": [{"id": 10, "codigo": "001", "nome": "Cebolao"}], "used": {}}, 200, cfg)
        if len(calls) == 2:
            return (catalog, 200, cfg)
        return (balances, 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("Uai Mineirinho", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_stock_balances(
            "000002", "estoque SKU 001", "Uai Mineirinho", limit=10
        )

    row = raw["result"]["saldos"][0]
    assert row["saldo_bruto_retornado"] == 186
    assert row["saldo_loja_total"] == 61
    assert row["saldo_total"] == 61
    assert row["saldo_total_confiavel"] is True
    assert row["cobertura_depositos_completa"] is True
    assert row["full_excluido"] is True
    assert [(item["descricao"], item["saldo_fisico"]) for item in row["depositos"]] == [("Loja", 61)]
    assert [(item["descricao"], item["saldo_fisico"]) for item in row["depositos_excluidos"]] == [
        ("Fulfillment", 113),
        ("Devolucoes/Conserto", 10),
        ("Defeito", 2),
        ("Shopee 205945277 (Fulfillment)", 0),
    ]
    assert [item["motivo"] for item in row["depositos_excluidos"]] == [
        "Estoque Full/Fulfillment",
        "desconsiderarSaldo=true na Bling",
        "desconsiderarSaldo=true na Bling",
        "Estoque Full/Fulfillment",
    ]
    assert [source["path"] for source in raw["result"]["sources"]] == ["/depositos", "/estoques/saldos"]


def test_bling_stock_balances_unknown_deposit_fails_closed_without_summing_it():
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        if len(calls) == 1:
            return ({"products": [{"id": 10, "codigo": "001", "nome": "Produto"}], "used": {}}, 200, cfg)
        if len(calls) == 2:
            return ([
                {"id": 1, "descricao": "Loja", "situacao": 1, "padrao": True, "desconsiderarSaldo": False},
            ], 200, cfg)
        return ([{
            "produto": {"id": 10, "codigo": "001", "nome": "Produto"},
            "depositos": [
                {"id": 1, "saldoFisico": 5, "saldoVirtual": 5},
                {"id": 999, "saldoFisico": 7, "saldoVirtual": 7},
            ],
        }], 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Pecas", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_stock_balances("000002", "estoque SKU 001", "JK Pecas", limit=10)

    row = raw["result"]["saldos"][0]
    assert row["saldo_bruto_retornado"] == 12
    assert row["saldo_loja_parcial"] == 5
    assert row["saldo_loja_total"] is None
    assert row["saldo_total"] is None
    assert row["saldo_total_confiavel"] is False
    assert row["cobertura_depositos_completa"] is False
    assert row["full_excluido"] is False
    assert row["depositos_excluidos"][-1]["descricao"] == "Deposito ID 999 - nao classificado"
    assert "catalogo" in row["depositos_excluidos"][-1]["motivo"]


def test_bling_stock_balances_catalog_failure_keeps_raw_balance_but_no_store_total():
    calls = []

    def fake_call(_client_id, _loja, cfg, _callback):
        calls.append(1)
        if len(calls) == 1:
            return ({"products": [{"id": 10, "codigo": "001", "nome": "Produto"}], "used": {}}, 200, cfg)
        if len(calls) == 2:
            return ({"error": "temporary"}, 503, cfg)
        return ([{
            "produto": {"id": 10, "codigo": "001", "nome": "Produto"},
            "depositos": [{"id": 2, "saldoFisico": 9, "saldoVirtual": 9}],
        }], 200, cfg)

    with (
        patch.object(codex_bling_tools, "_connected_stores", return_value=([("JK Pecas", {"access_token": "x"})], [])),
        patch.object(codex_bling_tools, "_call_store", side_effect=fake_call),
    ):
        raw = codex_bling_tools.tool_bling_stock_balances("000002", "estoque SKU 001", "JK Pecas", limit=10)

    row = raw["result"]["saldos"][0]
    assert row["saldo_bruto_retornado"] == 9
    assert row["saldo_loja_parcial"] == 0
    assert row["saldo_loja_total"] is None
    assert row["saldo_total"] is None
    assert row["cobertura_depositos_completa"] is False
    assert row["full_excluido"] is False
    assert row["depositos"] == []
    assert row["depositos_excluidos"][0]["descricao"] == "Deposito ID 2 - nao classificado"
    assert any("catalogo de depositos retornou HTTP 503" in warning for warning in raw["result"]["warnings"])
