from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import bling_vendas, cadastro_catalogo_bling


def _store(
    store_id: str,
    token: str,
    *,
    name: str = "Loja Homonima",
) -> dict:
    return {
        "store_id": store_id,
        "nome": name,
        "integracoes": {
            "bling": {
                "id": f"client-{store_id}",
                "secret": f"secret-{store_id}",
                "oauth_connection_id": f"connection-{store_id}",
                "access_token": token,
                "refresh_token": f"refresh-{store_id}",
                "connected": True,
            }
        },
    }


def _configure_stores(monkeypatch, stores_by_tenant=None):
    stores_by_tenant = stores_by_tenant or {
        "tenant-a": [_store("store-a", "token-a"), _store("store-b", "token-b")]
    }
    monkeypatch.setattr(
        cadastro_catalogo_bling.integracoes,
        "carregar_lojas",
        lambda client_id: stores_by_tenant.get(str(client_id), []),
    )
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_preparar_credencial_coleta",
        lambda _client_id, _store_id, _store_name, cfg: dict(cfg),
    )
    return stores_by_tenant


def _stock_ids(params):
    return [str(value) for key, value in list(params or []) if key == "idsProdutos[]"]


def _stock_rows(params, *, virtual=0, physical=0):
    return [
        {
            "produto": {"id": product_id},
            "saldoFisicoTotal": physical,
            "saldoVirtualTotal": virtual,
            "depositos": [{"id": 1, "saldoFisico": physical}],
        }
        for product_id in _stock_ids(params)
    ]


def _assert_full_catalog_params(params):
    assert params["tipo"] == "T"
    assert params["criterio"] == 5


def test_collects_more_than_one_page_and_maps_provider_fields(monkeypatch):
    _configure_stores(monkeypatch)
    calls = []

    def fake_get(token, path, *, params, **_kwargs):
        recorded_params = list(params) if isinstance(params, list) else dict(params or {})
        calls.append((token, path, recorded_params))
        if path == "/produtos":
            _assert_full_catalog_params(params)
            tipo = params["tipo"]
            pagina = params["pagina"]
            if tipo == "T" and pagina == 1:
                return [
                    {
                        "id": index,
                        "codigo": f"SKU-{index}",
                        "nome": f"Produto {index}",
                        "preco": index,
                    }
                    for index in range(1, 101)
                ], 200
            if tipo == "T" and pagina == 2:
                return [
                    {"id": 101, "codigo": "SKU-101", "nome": "Produto 101"},
                    {"id": 201, "codigo": "VAR-201", "nome": "Variacao"},
                ], 200
            raise AssertionError((tipo, pagina))
        if path == "/produtos/1":
            return {
                "id": 1,
                "codigo": "SKU-1",
                "nome": "Produto detalhado",
                "situacao": "A",
                "tipo": "P",
                "formato": "S",
                "dataValidade": "2030-12-31",
                "tipoProducao": "P",
                "condicao": 1,
                "freteGratis": False,
                "actionEstoque": "A",
                "linhaProduto": "Linha X",
                "artigoPerigoso": False,
                "duns": "123456789",
                "marca": "Marca Teste",
                "gtin": "7890000000001",
                "gtinEmbalagem": "17890000000008",
                "preco": 19.9,
                "precoCusto": 8.25,
                "fornecedor": {
                    "id": 44,
                    "codigo": "FOR-44",
                    "contato": {"nome": "Fornecedor Teste"},
                    "precoCompra": 8.0,
                },
                "unidade": "UN",
                "pesoLiquido": 1.2,
                "pesoBruto": 1.4,
                "estoque": {"saldoVirtualTotal": 0, "localizacao": "A-01"},
                "tributacao": {"ncm": "87089990", "cest": "0100100"},
                "categoria": {"id": 9},
                "dimensoes": {
                    "altura": 10,
                    "largura": 20,
                    "profundidade": 30,
                    "unidadeMedida": "CENTIMETROS",
                },
                "descricaoCurta": "Descricao curta",
                "descricaoComplementar": "Descricao completa",
                "estrutura": {
                    "tipoEstoque": "FISICO",
                    "componentes": [
                        {"produto": {"id": 500}, "quantidade": 2}
                    ],
                },
                "midia": {
                    "imagens": {
                        "externas": [{"ordem": 1, "link": "https://img.example/p.jpg"}]
                    }
                },
            }, 200
        if path.startswith("/produtos/"):
            product_id = path.rsplit("/", 1)[-1]
            return {"id": product_id}, 200
        if path == "/estoques/saldos":
            assert 1 <= len(_stock_ids(params)) <= 50
            return _stock_rows(params, virtual=7, physical=11), 200
        if path == "/categorias/produtos":
            assert params == {"pagina": 1, "limite": 100}
            return [{"id": 9, "descricao": "Autopecas"}], 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["source"] == "bling"
    assert result["store_id"] == "store-a"
    assert result["config_fingerprint"] == (
        cadastro_catalogo_bling.configuracao_catalogo_fingerprint(
            "bling",
            "store-a",
            "Loja Homonima",
            _store("store-a", "token-a")["integracoes"]["bling"],
        )
    )
    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert len(result["items"]) == 102


    assert result["stats"]["pages_by_type"] == {"T": 2}
    assert result["stats"]["details_complete"] == 102
    assert result["stats"]["balances_requested"] == 102
    assert result["stats"]["balances_returned"] == 102
    assert result["stats"]["balance_batches"] == 3
    assert result["stats"]["balances_failed"] == 0
    assert result["stats"]["categories_requested"] == 1
    assert result["stats"]["categories_complete"] == 1
    assert result["stats"]["category_pages"] == 1
    assert [params for _token, path, params in calls if path == "/produtos"] == [
        {"pagina": 1, "limite": 100, "tipo": "T", "criterio": 5},
        {"pagina": 2, "limite": 100, "tipo": "T", "criterio": 5},
    ]

    item = next(entry for entry in result["items"] if entry["sku"] == "SKU-1")
    assert item["external_ids"] == {"id_bling": "1"}
    assert item["sku_normalizado"] == "SKU-1"
    assert item["fields"]["produto_bling"] == "Produto detalhado"
    assert item["fields"]["nome_bling"] == "Produto detalhado"
    assert item["fields"]["data_validade_bling"] == "2030-12-31"
    assert item["fields"]["tipo_producao_bling"] == "P"
    assert item["fields"]["condicao_bling"] == 1
    assert item["fields"]["frete_gratis_bling"] is False
    assert item["fields"]["action_estoque_bling"] == "A"
    assert item["fields"]["linha_produto_bling"] == "Linha X"
    assert item["fields"]["artigo_perigoso_bling"] is False
    assert item["fields"]["duns_bling"] == "123456789"
    assert item["fields"]["ncm_bling"] == "87089990"
    assert item["fields"]["cest_bling"] == "0100100"
    assert item["fields"]["categoria_id_bling"] == 9
    assert item["fields"]["categoria_bling"] == "Autopecas"
    assert item["fields"]["preco_bling"] == 19.9
    assert item["fields"]["custo_bling"] == 8.25
    assert item["fields"]["estoque_fisico_bling"] == 11
    assert item["fields"]["estoque_virtual_bling"] == 7
    assert json.loads(item["fields"]["estoques_bling_json"])["produto"]["id"] == "1"
    assert item["fields"]["localizacao_bling"] == "A-01"
    assert json.loads(item["fields"]["fornecedor_bling_json"])["id"] == 44
    assert json.loads(item["fields"]["componentes_bling_json"]) == [
        {"produto": {"id": 500}, "quantidade": 2}
    ]
    assert item["fields"]["consultado_em_utc_bling"].endswith("Z")
    assert item["fields"]["imagens_bling"] == (
        '{"externas":[{"link":"https://img.example/p.jpg","ordem":1}]}'
    )
    assert json.loads(item["fields"]["dimensoes_bling"])["largura"] == 20
    assert {"custo", "preco", "imposto", "foto"}.isdisjoint(item["fields"])
    assert {call[0] for call in calls} == {"token-a"}


def test_partial_page_overlap_invalidates_sku_coverage_and_deduplicates_id(
    monkeypatch,
):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            if params["pagina"] == 1:
                return [
                    {"id": index, "codigo": f"SKU-{index}"}
                    for index in range(1, 101)
                ], 200
            if params["pagina"] == 2:
                return [
                    {"id": 100, "codigo": "SKU-100"},
                    {"id": 101, "codigo": "SKU-101"},
                ], 200
            raise AssertionError(params)
        if path.startswith("/produtos/"):
            product_id = path.rsplit("/", 1)[-1]
            return {"id": product_id, "codigo": f"SKU-{product_id}"}, 200
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert len(result["items"]) == 101
    assert len({item["external_ids"]["id_bling"] for item in result["items"]}) == 101
    assert result["stats"]["records_scanned"] == 102
    assert result["stats"]["duplicate_product_ids"] == 1
    duplicate = next(
        item for item in result["skipped"] if item["reason"] == "duplicate_product_id"
    )
    assert duplicate["sku"] == "SKU-100"
    assert duplicate["warnings"] == ["id_produto_bling_repetido"]
    assert "catalog_duplicate_product_id:id=100" in result["warnings"]


def test_repeated_product_id_with_divergent_sku_keeps_first_identity(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {"id": 1, "codigo": "SKU-A", "nome": "Primeiro"},
                {"id": 1, "codigo": "SKU-B", "nome": "Segundo"},
            ], 200
        if path == "/produtos/1":
            return {"id": 1, "codigo": "SKU-A", "nome": "Primeiro"}, 200
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert [item["sku"] for item in result["items"]] == ["SKU-A"]
    assert result["stats"]["duplicate_product_ids"] == 1
    duplicate = next(
        item for item in result["skipped"] if item["reason"] == "duplicate_product_id"
    )
    assert duplicate["sku"] == "SKU-B"
    assert duplicate["warnings"] == [
        "id_produto_bling_repetido",
        "sku_bling_divergente_para_id_repetido",
    ]
    assert "catalog_duplicate_product_id:id=1:sku_mismatch" in result["warnings"]


def test_maps_components_from_direct_product_field_as_canonical_json():
    fields = cadastro_catalogo_bling._mapear_campos_produto(
        {"componentes": [{"quantidade": 1, "produto": {"id": 10}}]},
        consultado_em_utc="2026-09-01T12:00:00Z",
    )

    assert fields["componentes_bling_json"] == (
        '[{"produto":{"id":10},"quantidade":1}]'
    )
    assert fields["consultado_em_utc_bling"] == "2026-09-01T12:00:00Z"


def test_get_json_usa_jwt_em_toda_requisicao_autenticada(monkeypatch):
    captured = {}

    def fake_request(url, **kwargs):
        captured.update(url=url, **kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"data": []})

    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_get_with_adaptive_limit",
        fake_request,
    )
    params = [("idsProdutos[]", "1"), ("idsProdutos[]", "2")]

    data, status = cadastro_catalogo_bling._get_json(
        "token-seguro",
        "/estoques/saldos",
        params=params,
        limiter=object(),
        cancel_callback=lambda: None,
        deadline=time.monotonic() + 5,
    )

    assert (data, status) == ([], 200)
    assert captured["headers"] == {
        "Authorization": "Bearer token-seguro",
        "enable-jwt": "1",
    }
    assert captured["params"] == params


def test_preparo_da_coleta_exige_refresh_proprio_e_token_novo(monkeypatch):
    calls = []

    def fake_refresh(client_id, store_name, cfg, *, store_id, return_disposition):
        calls.append((client_id, store_name, cfg, store_id, return_disposition))
        return {**cfg, "access_token": "access-novo"}, "committed_by_caller"

    monkeypatch.setattr(cadastro_catalogo_bling, "_bling_renovar_token_loja", fake_refresh)
    cfg = {"access_token": "access-antigo", "refresh_token": "refresh", "id": "app"}

    renewed = cadastro_catalogo_bling._preparar_credencial_coleta(
        "tenant-a", "store-a", "Loja A", cfg
    )

    assert renewed["access_token"] == "access-novo"
    assert calls == [("tenant-a", "Loja A", cfg, "store-a", True)]
    assert cadastro_catalogo_bling.BLING_CATALOG_TIMEOUT_SECONDS < 6 * 60 * 60


def test_preparo_falha_fechado_se_refresh_concorrente_venceu(monkeypatch):
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_renovar_token_loja",
        lambda *_args, **_kwargs: (
            {"access_token": "outra-conta", "refresh_token": "outro"},
            "reused_concurrent",
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling._preparar_credencial_coleta(
            "tenant-a",
            "store-a",
            "Loja A",
            {"access_token": "antigo", "refresh_token": "refresh"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "bling_refresh_not_owned"


def test_preparo_aceita_reused_concurrent_da_mesma_identidade_estavel(monkeypatch):
    cfg = {
        "id": "app-a",
        "secret": "secret-a",
        "oauth_connection_id": "connection-a",
        "access_token": "access-antigo",
        "refresh_token": "refresh-antigo",
        "connected": True,
        "updated_at": "1",
        "_sync_version": 1,
    }
    renewed = {
        **cfg,
        "access_token": "access-concorrente",
        "refresh_token": "refresh-concorrente",
        "updated_at": "2",
        "_sync_version": 2,
    }
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_renovar_token_loja",
        lambda *_args, **_kwargs: (dict(renewed), "reused_concurrent"),
    )

    result = cadastro_catalogo_bling._preparar_credencial_coleta(
        "tenant-a",
        "store-a",
        "Loja A",
        cfg,
    )

    assert result == renewed


@pytest.mark.parametrize(
    ("changed", "disposition"),
    [
        ({"oauth_connection_id": "connection-b"}, "reused_concurrent"),
        ({"secret": "secret-b"}, "reused_concurrent"),
        ({}, "cas_lost"),
    ],
)
def test_preparo_rejeita_reused_de_outra_identidade_e_cas_lost(
    monkeypatch,
    changed,
    disposition,
):
    cfg = {
        "id": "app-a",
        "secret": "secret-a",
        "oauth_connection_id": "connection-a",
        "access_token": "access-antigo",
        "refresh_token": "refresh-antigo",
        "connected": True,
    }
    renewed = {
        **cfg,
        "access_token": "access-concorrente",
        "refresh_token": "refresh-concorrente",
        **changed,
    }
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_renovar_token_loja",
        lambda *_args, **_kwargs: (dict(renewed), disposition),
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling._preparar_credencial_coleta(
            "tenant-a",
            "store-a",
            "Loja A",
            cfg,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "bling_refresh_not_owned"


def test_coleta_respeita_limite_de_tres_requisicoes_bling_por_segundo(monkeypatch):
    _configure_stores(monkeypatch)
    limiter_kwargs = {}

    class LimiterSpy:
        def __init__(self, **kwargs):
            limiter_kwargs.update(kwargs)

    monkeypatch.setattr(cadastro_catalogo_bling, "_BlingAdaptiveLimiter", LimiterSpy)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            ([], 200) if path == "/produtos" else pytest.fail(path)
        ),
    )

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert limiter_kwargs["min_interval"] >= (1 / 3)
    assert limiter_kwargs["start_interval"] >= (1 / 3)


def test_produto_com_sku_sem_id_continua_disponivel_para_cadastro(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        return [
            {
                "codigo": "SKU-ONLY",
                "nome": "Produto somente da listagem",
                "tributacao": {"ncm": "87089990", "cest": "0100100"},
            }
        ], 200

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["missing_sku"] == 0
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["sku"] == "SKU-ONLY"
    assert item["external_ids"] == {}
    assert item["fields"]["nome_bling"] == "Produto somente da listagem"
    assert item["fields"]["ncm_bling"] == "87089990"
    assert item["fields"]["cest_bling"] == "0100100"
    assert "id_produto_bling_ausente" in item["warnings"]
    assert "catalog_product_id_missing" in result["warnings"]


def test_saldo_ausente_falha_fechado_sem_inventar_zero(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {"id": 1, "codigo": "SKU-1"},
                {"id": 2, "codigo": "SKU-2"},
            ], 200
        if path.startswith("/produtos/"):
            product_id = path.rsplit("/", 1)[-1]
            return {"id": product_id}, 200
        if path == "/estoques/saldos":
            return _stock_rows([("idsProdutos[]", "1")], virtual=4, physical=3), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["balances_requested"] == 2
    assert result["stats"]["balances_returned"] == 1
    assert result["stats"]["balances_failed"] == 1
    missing = next(item for item in result["items"] if item["sku"] == "SKU-2")
    assert "saldo_bling_ausente" in missing["warnings"]
    assert "estoque_fisico_bling" not in missing["fields"]
    assert any("stock_balance_missing" in warning for warning in result["warnings"])


def test_saldo_sem_totais_obrigatorios_e_incompleto(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [{"id": 1, "codigo": "SKU-1"}], 200
        if path == "/produtos/1":
            return {"id": 1, "codigo": "SKU-1"}, 200
        if path == "/estoques/saldos":
            return [{"produto": {"id": "1"}, "saldoFisicoTotal": 3}], 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["balances_returned"] == 0
    assert result["stats"]["balances_failed"] == 1
    assert result["stats"]["balance_invalid_rows"] == 1
    assert "saldo_bling_totais_invalidos" in result["items"][0]["warnings"]
    assert "estoque_fisico_bling" not in result["items"][0]["fields"]


def test_keeps_duplicate_skus_and_skips_only_missing_explicit_sku(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {"id": 1, "codigo": "AbC", "nome": "Primeiro"},
                {"id": 2, "codigo": " abc ", "nome": "Segundo"},
                {"id": 3, "codigo": "", "nome": "Sem SKU"},
            ], 200
        if path == "/produtos/1":
            return {"id": 1, "codigo": "AbC", "nome": "Primeiro"}, 200
        if path == "/produtos/2":
            return {"id": 2, "codigo": "abc", "nome": "Segundo"}, 200
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert [item["sku_normalizado"] for item in result["items"]] == ["ABC", "ABC"]
    assert [item["external_ids"]["id_bling"] for item in result["items"]] == ["1", "2"]
    assert all("sku_duplicado_bling:ABC" in item["warnings"] for item in result["items"])
    assert result["stats"]["duplicate_skus"] == 1
    assert result["stats"]["duplicate_items"] == 2
    assert result["stats"]["missing_sku"] == 1
    assert result["skipped"][0]["reason"] == "missing_sku"
    assert result["skipped"][0]["external_ids"] == {"id_bling": "3"}


def test_detail_with_insufficient_scope_is_fail_closed(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [{"id": 1, "codigo": "SKU-1", "nome": "Da listagem"}], 200
        if path == "/produtos/1":
            # Bling v328: insufficient application scope is 403, not 401.
            return None, 403
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["details_failed"] == 1
    assert result["items"][0]["fields"]["nome_bling"] == "Da listagem"
    assert "detalhe_bling_http_403" in result["items"][0]["warnings"]
    assert any("product_detail_incomplete" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    ("detail", "expected_item_warning", "expected_global_warning"),
    [
        ({}, "detalhe_bling_vazio", "product_detail_empty:id=1"),
        (
            {"id": 999, "codigo": "SKU-1", "nome": "Produto incorreto"},
            "detalhe_bling_id_divergente",
            "product_detail_id_mismatch:requested=1:received=999",
        ),
        (
            {"id": 1, "codigo": "OUTRO-SKU", "nome": "Produto incorreto"},
            "detalhe_bling_sku_divergente",
            "product_detail_sku_mismatch:id=1",
        ),
    ],
    ids=["empty", "id-mismatch", "sku-mismatch"],
)
def test_invalid_200_detail_is_incomplete_and_never_merged(
    monkeypatch,
    detail,
    expected_item_warning,
    expected_global_warning,
):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {
                    "id": 1,
                    "codigo": "SKU-1",
                    "nome": "Nome da listagem",
                    "preco": 10,
                }
            ], 200
        if path == "/produtos/1":
            return detail, 200
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    item = result["items"][0]
    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["details_failed"] == 1
    assert result["stats"]["details_complete"] == 0
    assert item["external_ids"] == {"id_bling": "1"}
    assert item["fields"]["id_bling"] == "1"
    assert item["fields"]["nome_bling"] == "Nome da listagem"
    assert item["fields"]["preco_bling"] == 10
    assert "Produto incorreto" not in item["fields"].values()
    assert expected_item_warning in item["warnings"]
    assert expected_global_warning in result["warnings"]


def test_falha_de_categoria_nao_invalida_cobertura_dos_skus(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {
                    "id": 1,
                    "codigo": "SKU-1",
                    "nome": "Produto listado",
                    "categoria": {"id": 9},
                }
            ], 200
        if path == "/produtos/1":
            return {"id": 1, "codigo": "SKU-1", "categoria": {"id": 9}}, 200
        if path == "/estoques/saldos":
            return _stock_rows(params), 200
        if path == "/categorias/produtos":
            return None, 403
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is True
    assert result["stats"]["categories_failed"] == 1
    assert "categoria_bling_ausente:9" in result["items"][0]["warnings"]
    assert "category_catalog_incomplete:page=1:status=403" in result["warnings"]


@pytest.mark.parametrize("status", [403, 429])
def test_catalog_page_permission_or_rate_limit_never_looks_complete(monkeypatch, status):
    _configure_stores(monkeypatch)

    def fake_get(_token, path, *, params, **_kwargs):
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        return None, status

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["items"] == []
    serialized = json.dumps(result)
    assert f"catalog_page_http_{status}" in serialized
    assert "token-a" not in serialized
    assert "refresh-store-a" not in serialized


def test_internal_catalog_limit_stops_before_details_and_fails_closed(monkeypatch):
    _configure_stores(monkeypatch)
    monkeypatch.setattr(cadastro_catalogo_bling, "BLING_MAX_CATALOG_RECORDS", 1)
    calls = []

    def fake_get(_token, path, *, params, **_kwargs):
        calls.append((path, dict(params or {})))
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {"id": 1, "codigo": "SKU-1", "nome": "Um"},
                {"id": 2, "codigo": "SKU-2", "nome": "Dois"},
            ], 200
        raise AssertionError((path, params))

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert [item["sku"] for item in result["items"]] == ["SKU-1"]
    assert result["stats"]["details_requested"] == 0
    assert result["warnings"] == ["catalog_item_limit:max=1"]
    assert calls == [
        ("/produtos", {"pagina": 1, "limite": 100, "tipo": "T", "criterio": 5})
    ]


def test_cancellation_returns_partial_read_without_claiming_coverage(monkeypatch):
    _configure_stores(monkeypatch)
    cancel_event = threading.Event()

    def fake_get(_token, path, *, params, **_kwargs):
        if path == "/produtos":
            _assert_full_catalog_params(params)
            return [
                {"id": 1, "codigo": "SKU-1", "nome": "Um"},
                {"id": 2, "codigo": "SKU-2", "nome": "Dois"},
            ], 200
        if path == "/produtos/1":
            cancel_event.set()
            return {"id": 1, "codigo": "SKU-1", "marca": "Marca"}, 200
        raise AssertionError(path)

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling(
        "tenant-a", "store-a", cancel_event=cancel_event
    )

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["stats"]["cancelled"] is True
    assert result["stats"]["details_complete"] == 1
    assert len(result["items"]) == 2
    assert "catalog_collection_cancelled" in result["warnings"]


def test_deadline_incompleto_invalida_tambem_cobertura_dos_skus(monkeypatch):
    _configure_stores(monkeypatch)
    monkeypatch.setattr(cadastro_catalogo_bling, "BLING_CATALOG_TIMEOUT_SECONDS", -1)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda *_args, **_kwargs: pytest.fail("prazo deve impedir a primeira chamada"),
    )

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["stats"]["deadline_exceeded"] is True
    assert "catalog_collection_deadline_exceeded" in result["warnings"]


def test_autenticacao_final_invalida_tambem_cobertura_dos_skus(monkeypatch):
    _configure_stores(monkeypatch)

    def fake_execute(_client_id, _store_name, cfg, operation, **_kwargs):
        result, status = operation(str(cfg.get("access_token") or ""))
        return result, status, dict(cfg)

    monkeypatch.setattr(cadastro_catalogo_bling, "_bling_executar_com_refresh", fake_execute)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            (None, 401) if path == "/produtos" else pytest.fail(path)
        ),
    )

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is False
    assert result["sku_coverage_complete"] is False
    assert result["items"] == []
    assert "bling_authentication_failed" in result["warnings"]


def test_exact_store_id_selects_only_its_tenant_credentials(monkeypatch):
    stores = {
        "tenant-a": [_store("store-a", "token-a"), _store("store-b", "token-b")],
        "tenant-b": [_store("store-b", "token-other-tenant")],
    }
    _configure_stores(monkeypatch, stores)
    seen_tokens = []

    def fake_get(token, path, *, params, **_kwargs):
        seen_tokens.append(token)
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        return [], 200

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-b")

    assert result["store_id"] == "store-b"
    assert result["store_name"] == "Loja Homonima"
    assert seen_tokens == ["token-b"]
    serialized = json.dumps(result)
    assert "token-b" not in serialized
    assert "secret-store-b" not in serialized

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling("tenant-b", "store-a")
    assert exc_info.value.status_code == 404


def test_fingerprint_inicial_divergente_bloqueia_antes_da_api(monkeypatch):
    _configure_stores(monkeypatch)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda *_args, **_kwargs: pytest.fail("configuracao divergente nao pode consultar Bling"),
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling(
            "tenant-a",
            "store-a",
            expected_config_fingerprint="f" * 64,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"


def test_fingerprint_inicial_aceita_refresh_da_mesma_identidade_estavel(monkeypatch):
    stores = _configure_stores(monkeypatch)
    cfg_atual = stores["tenant-a"][0]["integracoes"]["bling"]
    cfg_anterior = {
        **cfg_atual,
        "access_token": "token-anterior",
        "refresh_token": "refresh-anterior",
    }
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            ([], 200) if path == "/produtos" else pytest.fail(path)
        ),
    )

    result = cadastro_catalogo_bling.coletar_catalogo_bling(
        "tenant-a",
        "store-a",
        expected_config_fingerprint=(
            cadastro_catalogo_bling.configuracao_catalogo_fingerprint(
                "bling",
                "store-a",
                "Loja Homonima",
                cfg_anterior,
            )
        ),
        expected_apply_config_fingerprint=(
            cadastro_catalogo_bling.configuracao_catalogo_aplicacao_fingerprint(
                "bling",
                "store-a",
                "Loja Homonima",
                cfg_anterior,
            )
        ),
    )

    assert result["coverage_complete"] is True
    assert result["started_apply_config_fingerprint"] == (
        result["apply_config_fingerprint"]
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"oauth_connection_id": "connection-anterior"},
        {"secret": "secret-anterior"},
    ],
)
def test_fingerprint_inicial_rejeita_mudanca_estavel_antes_do_provider(
    monkeypatch,
    changed,
):
    stores = _configure_stores(monkeypatch)
    cfg_atual = stores["tenant-a"][0]["integracoes"]["bling"]
    cfg_anterior = {
        **cfg_atual,
        "access_token": "token-anterior",
        "refresh_token": "refresh-anterior",
        **changed,
    }
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda *_args, **_kwargs: pytest.fail(
            "mudanca de identidade nao pode consultar a Bling"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling(
            "tenant-a",
            "store-a",
            expected_config_fingerprint=(
                cadastro_catalogo_bling.configuracao_catalogo_fingerprint(
                    "bling",
                    "store-a",
                    "Loja Homonima",
                    cfg_anterior,
                )
            ),
            expected_apply_config_fingerprint=(
                cadastro_catalogo_bling.configuracao_catalogo_aplicacao_fingerprint(
                    "bling",
                    "store-a",
                    "Loja Homonima",
                    cfg_anterior,
                )
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"


def test_troca_de_conexao_durante_coleta_descarta_resultado(monkeypatch):
    stores = _configure_stores(monkeypatch)

    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            ([], 200) if path == "/produtos" else pytest.fail(path)
        ),
    )

    def fake_execute(_client_id, _store_name, cfg, operation, **_kwargs):
        result, status = operation(str(cfg.get("access_token") or ""))
        cfg_final = dict(cfg)
        stores["tenant-a"][0]["integracoes"]["bling"] = {
            **cfg,
            "oauth_connection_id": "connection-reconnected",
            "access_token": "token-reconnected",
            "refresh_token": "refresh-reconnected",
        }
        return result, status, cfg_final

    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_executar_com_refresh",
        fake_execute,
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"


def test_refresh_concorrente_ao_final_da_coleta_mesma_conexao_e_aceito(monkeypatch):
    stores = _configure_stores(monkeypatch)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            ([], 200) if path == "/produtos" else pytest.fail(path)
        ),
    )

    def fake_execute(_client_id, _store_name, cfg, operation, **_kwargs):
        result, status = operation(str(cfg.get("access_token") or ""))
        cfg_final = dict(cfg)
        stores["tenant-a"][0]["integracoes"]["bling"] = {
            **cfg,
            "access_token": "token-concorrente",
            "refresh_token": "refresh-concorrente",
            "updated_at": "2",
            "_sync_version": 2,
        }
        return result, status, cfg_final

    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_executar_com_refresh",
        fake_execute,
    )

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["config_fingerprint"] != result["started_config_fingerprint"]
    assert result["apply_config_fingerprint"] == (
        result["started_apply_config_fingerprint"]
    )


def test_refresh_concorrente_legado_sem_connection_id_permanece_fail_closed(
    monkeypatch,
):
    stores = {
        "tenant-a": [
            {
                "store_id": "store-a",
                "nome": "Loja A",
                "integracoes": {
                    "bling": {
                        "id": "app-a",
                        "secret": "secret-a",
                        "access_token": "token-a",
                        "refresh_token": "refresh-a",
                        "connected": True,
                    }
                },
            }
        ]
    }
    _configure_stores(monkeypatch, stores)
    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_get_json",
        lambda _token, path, *, params, **_kwargs: (
            ([], 200) if path == "/produtos" else pytest.fail(path)
        ),
    )

    def fake_execute(_client_id, _store_name, cfg, operation, **_kwargs):
        result, status = operation(str(cfg.get("access_token") or ""))
        stores["tenant-a"][0]["integracoes"]["bling"] = {
            **cfg,
            "access_token": "token-concorrente",
            "refresh_token": "refresh-concorrente",
        }
        return result, status, dict(cfg)

    monkeypatch.setattr(
        cadastro_catalogo_bling,
        "_bling_executar_com_refresh",
        fake_execute,
    )

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "store_config_changed"


def test_401_refreshes_only_the_exact_store_and_restarts_collection(monkeypatch):
    stores = _configure_stores(monkeypatch)
    seen_tokens = []
    refreshes = []

    def fake_get(token, path, *, params, **_kwargs):
        seen_tokens.append(token)
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        if token == "token-a":
            return None, 401
        return [], 200

    def fake_refresh(client_id, name, cfg, *, store_id, return_disposition=False):
        refreshes.append((client_id, name, cfg["access_token"], store_id))
        renewed = {**cfg, "access_token": "token-a-renewed", "refresh_token": "refresh-new"}
        stores["tenant-a"][0]["integracoes"]["bling"] = dict(renewed)
        if return_disposition:
            return renewed, "committed_by_caller"
        return renewed

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", fake_refresh)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert refreshes == [("tenant-a", "Loja Homonima", "token-a", "store-a")]
    assert seen_tokens == ["token-a", "token-a-renewed"]
    assert result["config_fingerprint"] == (
        cadastro_catalogo_bling.configuracao_catalogo_fingerprint(
            "bling",
            "store-a",
            "Loja Homonima",
            stores["tenant-a"][0]["integracoes"]["bling"],
        )
    )
    assert "token-a-renewed" not in json.dumps(result)


def test_401_reused_concurrent_mesma_conexao_reinicia_coleta(monkeypatch):
    stores = _configure_stores(monkeypatch)
    seen_tokens = []

    def fake_get(token, path, *, params, **_kwargs):
        seen_tokens.append(token)
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        if token == "token-a":
            return None, 401
        return [], 200

    def fake_refresh(client_id, name, cfg, *, store_id, return_disposition=False):
        assert return_disposition is True
        renewed = {
            **cfg,
            "access_token": "token-a-concorrente",
            "refresh_token": "refresh-concorrente",
            "updated_at": "2",
            "_sync_version": 2,
        }
        stores[client_id][0]["integracoes"]["bling"] = dict(renewed)
        return renewed, "reused_concurrent"

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", fake_refresh)

    result = cadastro_catalogo_bling.coletar_catalogo_bling(
        "tenant-a",
        "store-a",
    )

    assert result["coverage_complete"] is True
    assert seen_tokens == ["token-a", "token-a-concorrente"]


def test_401_cas_lost_mesma_conexao_continua_bloqueado(monkeypatch):
    _configure_stores(monkeypatch)
    seen_tokens = []

    def fake_get(token, path, *, params, **_kwargs):
        seen_tokens.append(token)
        assert path == "/produtos"
        return None, 401

    def fake_refresh(_client_id, _name, cfg, **_kwargs):
        return {
            **cfg,
            "access_token": "token-cas-lost",
            "refresh_token": "refresh-cas-lost",
        }, "cas_lost"

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", fake_refresh)

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "bling_refresh_not_owned"
    assert seen_tokens == ["token-a"]


def test_401_nos_saldos_renova_e_reinicia_toda_a_coleta(monkeypatch):
    stores = _configure_stores(monkeypatch)
    calls = []

    def fake_get(token, path, *, params, **_kwargs):
        calls.append((token, path))
        if path == "/produtos":
            _assert_full_catalog_params(params)
            name = "leitura-antiga" if token == "token-a" else "leitura-renovada"
            return [{"id": 1, "codigo": "SKU-1", "nome": name}], 200
        if path == "/produtos/1":
            name = "leitura-antiga" if token == "token-a" else "leitura-renovada"
            return {"id": 1, "codigo": "SKU-1", "nome": name}, 200
        if path == "/estoques/saldos":
            if token == "token-a":
                return None, 401
            return _stock_rows(params, virtual=6, physical=5), 200
        raise AssertionError(path)

    def fake_refresh(client_id, name, cfg, *, store_id, return_disposition=False):
        renewed = {**cfg, "access_token": "token-a-renewed", "refresh_token": "refresh-new"}
        stores[client_id][0]["integracoes"]["bling"] = dict(renewed)
        return (renewed, "committed_by_caller") if return_disposition else renewed

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", fake_refresh)

    result = cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert result["coverage_complete"] is True
    assert result["sku_coverage_complete"] is True
    assert result["items"][0]["fields"]["nome_bling"] == "leitura-renovada"
    assert result["items"][0]["fields"]["estoque_fisico_bling"] == 5
    assert calls.count(("token-a", "/produtos")) == 1
    assert calls.count(("token-a-renewed", "/produtos")) == 1
    assert "leitura-antiga" not in json.dumps(result)


def test_401_falha_fechado_se_outra_operacao_reconectou_a_conta(monkeypatch):
    stores = _configure_stores(monkeypatch)
    seen_tokens = []

    def fake_get(token, path, *, params, **_kwargs):
        seen_tokens.append(token)
        assert path == "/produtos"
        _assert_full_catalog_params(params)
        if token == "token-a":
            return None, 401
        pytest.fail("a coleta nao pode reiniciar com a credencial concorrente")

    def fake_refresh(client_id, name, cfg, *, store_id, return_disposition=False):
        assert (client_id, name, store_id, return_disposition) == (
            "tenant-a",
            "Loja Homonima",
            "store-a",
            True,
        )
        concurrent = {
            **cfg,
            "id": "client-conta-b",
            "access_token": "token-conta-b",
            "refresh_token": "refresh-conta-b",
        }
        stores["tenant-a"][0]["integracoes"]["bling"] = dict(concurrent)
        return concurrent, "reused_concurrent"

    monkeypatch.setattr(cadastro_catalogo_bling, "_get_json", fake_get)
    monkeypatch.setattr(bling_vendas, "_bling_renovar_token_loja", fake_refresh)

    with pytest.raises(HTTPException) as exc_info:
        cadastro_catalogo_bling.coletar_catalogo_bling("tenant-a", "store-a")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "bling_refresh_not_owned"
    assert exc_info.value.detail["disposition"] == "reused_concurrent"
    assert seen_tokens == ["token-a"]
