from __future__ import annotations

import copy
import csv
import sqlite3
from datetime import datetime

import pytest

from backend.services import cadastro_compatibilidade, medias_compras_visao as visao
from backend.services import bling_vendas
from backend.services.promocoes_core_parsing import normalizar_texto


@pytest.fixture
def sources(tmp_path, monkeypatch):
    catalog = {("tenant-a", "store-jk"): []}
    history = {}
    transit = {}
    db = tmp_path / "sales.db"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE vendas (id_unico TEXT, sku TEXT, data TEXT, produto TEXT, quantidade REAL, loja_conta TEXT)")
    stock = tmp_path / "produtos_compilado.csv"
    stock.write_text("sku,loja_sync,saldo_loja\n", encoding="utf-8")
    monkeypatch.setattr(visao, "normalizar_texto", normalizar_texto, raising=False)
    monkeypatch.setattr(visao, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(visao, "ARQUIVO_DB_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "ARQUIVO_DB_CADASTRO_PRODUTOS", "", raising=False)
    monkeypatch.setattr(visao, "_migrar_arquivo_legado_para_tenant", lambda _client, name, _legacy: str(stock) if name == "produtos_compilado.csv" else "", raising=False)
    monkeypatch.setattr(visao, "_listar_bancos_vendas_tenant", lambda *_args: [str(db)], raising=False)
    monkeypatch.setattr(visao, "_sql_filtro_loja_vendas", bling_vendas._sql_filtro_loja_vendas, raising=False)
    monkeypatch.setattr(visao, "_cadastro_mapa_fotos_locais", lambda *_args: {}, raising=False)
    monkeypatch.setattr(visao, "_cadastro_resolver_foto_local", lambda *_args: "", raising=False)
    monkeypatch.setattr(visao, "_mapa_ultima_venda_por_sku", lambda client, _loja: history.get(client, {}))
    monkeypatch.setattr(visao, "_mapa_estoque_em_transito_detalhado_por_sku", lambda client, _loja: transit.get(client, {}))
    monkeypatch.setattr(visao, "_resolver_escopo_loja_medias", lambda _client, loja, store: {"loja": loja, "store_id": store, "scope": "global" if loja == "__todas" else "store"})

    def context(client, _legacy, store):
        return {"produtos": catalog.get((client, store), []), "store_id": "" if store == "__todas" else store, "loja_resolvida": True, "scope": "global_safe" if store == "__todas" else "store"}

    monkeypatch.setattr(cadastro_compatibilidade, "mesclar_produtos_legados_com_contexto_loja", context)
    return {"catalog": catalog, "history": history, "transit": transit, "db": db, "stock": stock}


def _view(months=6, client="tenant-a", loja="JK Peças", store="store-jk"):
    return visao._api_medias_compras_visao_sync(meses=months, loja=loja, store_id=store, client_id=client)


def _items(payload):
    return {item["sku"]: item for item in payload["itens"]}


@pytest.mark.parametrize("months", [3, 6, 12])
def test_excluded_bling_catalog_cannot_create_empty_rows(sources, months):
    rows = [
        {"sku": "X14", "situacao_bling": "E", "produto_bling": "Excluded X14"},
        {"sku": "L17", "situacao_bling": " e ", "produto_bling": "Excluded L17"},
        {"sku": "ACTIVE", "situacao_bling": "A"},
        {"sku": "INACTIVE", "situacao_bling": "I"},
        {"sku": "LEGACY", "situacao_bling": ""},
    ]
    sources["catalog"]["tenant-a", "store-jk"] = rows
    before = copy.deepcopy(rows)
    response = _view(months)
    assert set(_items(response)) == {"ACTIVE", "INACTIVE", "LEGACY"}
    assert response["resumo"]["total_skus"] == 3
    assert response["periodo_meses"] == months
    assert response["loja"] == "JK Peças"
    assert response["store_id"] == "store-jk"
    assert rows == before


@pytest.mark.parametrize("evidence", ["sale", "historical_sale", "stock", "transit"])
def test_excluded_catalog_keeps_operational_sku_and_metadata(sources, evidence):
    sources["catalog"]["tenant-a", "store-jk"] = [{"sku": "OPERATIVE", "situacao_bling": "E", "produto_bling": "Preserved title"}]
    if evidence == "sale":
        with sqlite3.connect(sources["db"]) as con:
            con.execute("INSERT INTO vendas VALUES (?,?,?,?,?,?)", ("sale-jk", "OPERATIVE", datetime.now().strftime("%Y-%m-%d"), "Sale title", 6, "JK Peças"))
            con.execute("INSERT INTO vendas VALUES (?,?,?,?,?,?)", ("sale-other", "OTHER", datetime.now().strftime("%Y-%m-%d"), "Other store", 100, "Outra Loja"))
    elif evidence == "historical_sale":
        sources["history"]["tenant-a"] = {"OPERATIVE": "2020-01-15"}
    elif evidence == "stock":
        with sources["stock"].open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["sku", "loja_sync", "saldo_loja"])
            writer.writeheader()
            writer.writerows([{"sku": "OPERATIVE", "loja_sync": "JK Peças", "saldo_loja": 4}, {"sku": "OTHER", "loja_sync": "Outra Loja", "saldo_loja": 100}])
    else:
        sources["transit"]["tenant-a"] = {"OPERATIVE": {"total": 5, "listas": [{"lista_id": "list-jk", "nome_lista": "Import JK", "quantidade": 5}]}}
    payload = _view()
    rows = _items(payload)
    assert set(rows) == {"OPERATIVE"}
    assert rows["OPERATIVE"]["titulo_anuncio"] == ("Sale title" if evidence == "sale" else "Preserved title")
    expected = {"sale": (6, 0, 0), "historical_sale": (0, 0, 0), "stock": (0, 4, 0), "transit": (0, 0, 5)}[evidence]
    assert (rows["OPERATIVE"]["total_vendas_periodo"], rows["OPERATIVE"]["saldo_atual_estoque"], rows["OPERATIVE"]["estoque_em_transito"]) == expected
    assert payload["resumo"]["total_skus"] == 1


@pytest.mark.parametrize("listing", [{"mlb_ids": "MLB123"}, {"mlb_principal": "MLB456"}, {"qtd_anuncios_mlb": "1"}])
def test_marketplace_listing_keeps_sku_even_with_stale_bling_exclusion(sources, listing):
    sources["catalog"]["tenant-a", "store-jk"] = [{"sku": "LISTED", "situacao_bling": "E", **listing}]
    assert set(_items(_view())) == {"LISTED"}


def test_zero_listing_count_is_not_marketplace_evidence(sources):
    sources["catalog"]["tenant-a", "store-jk"] = [{"sku": "X14", "situacao_bling": "E", "qtd_anuncios_mlb": "0"}]
    assert _view()["itens"] == []


def test_shared_sku_uses_selected_store_and_tenant_catalog(sources):
    sources["catalog"].update({
        ("tenant-a", "store-jk"): [{"sku": "COMMON", "situacao_bling": "E"}],
        ("tenant-a", "store-other"): [{"sku": "COMMON", "situacao_bling": "A"}],
        ("tenant-b", "store-jk"): [{"sku": "COMMON", "situacao_bling": "A"}],
    })
    assert _view()["itens"] == []
    assert set(_items(_view(loja="Outra Loja", store="store-other"))) == {"COMMON"}
    assert set(_items(_view(client="tenant-b"))) == {"COMMON"}
    assert _view()["itens"] == []


def test_global_view_excludes_only_catalog_only_deleted_skus(sources):
    sources["catalog"]["tenant-a", "__todas"] = [{"sku": "X14", "situacao_bling": "E"}, {"sku": "ACTIVE", "situacao_bling": "A"}]
    response = _view(loja="__todas", store="")
    assert set(_items(response)) == {"ACTIVE"}
    assert response["loja"] == "__todas"
    assert response["store_id"] == ""


def test_active_occurrence_keeps_shared_sku_in_merged_catalog(sources):
    sources["catalog"]["tenant-a", "store-jk"] = [{"sku": "COMMON", "situacao_bling": "E"}, {"sku": "COMMON", "situacao_bling": "A"}]
    assert set(_items(_view())) == {"COMMON"}
