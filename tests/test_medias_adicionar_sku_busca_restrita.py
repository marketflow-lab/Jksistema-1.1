"""Adding one SKU reads its store catalog without the full listing fallback."""

import copy

import pytest
from fastapi import HTTPException

from backend.services import cadastro_listagem as listagem
from backend.services import cadastro_produtos as lookup
from backend.services.medias_compras_common import TITULO_PRODUTO_INGLES_KEY
from test_medias_adicionar_sku_leitura import inclusao, _add
from test_medias_compras_carregamento import ambiente, _csv


def _forbid_global_lookup(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A store list must not query or list the global catalog")

    monkeypatch.setattr(lookup, "_obter_produto_cadastro_sync", forbidden)
    monkeypatch.setattr(listagem, "_listar_produtos_cadastro_sync", forbidden)


def _global_list(state):
    current = state["lists"]["cliente-a"][0]
    current.pop("store_id", None)
    current["loja"] = "__todas"


def test_store_add_keeps_distinct_skus_and_recalculates_without_global_lookup(inclusao, monkeypatch):
    _forbid_global_lookup(monkeypatch)

    result = _add("375-1")
    items = result["lista"]["itens"]

    assert result["acao"] == "adicionado"
    assert result["sku"] == "375-1"
    assert [(row["SKU"], row["Quantidade"]) for row in items if row["SKU"].startswith("375")] == [
        ("3751", 3), ("375-1", 20),
    ]
    assert items[-1]["M3 individual"] == 0.25
    assert items[-1]["M3"] == 5
    assert items[-1]["II"] == 12.5
    assert inclusao["counts"]["persistencias"] == 1


def test_store_add_accepts_proven_compiled_shadow_absent_from_global_base(inclusao, monkeypatch):
    _forbid_global_lookup(monkeypatch)
    tenant = inclusao["root"] / "cliente-a"
    compiled = tenant / "produtos_compilado.csv"
    _csv(compiled, [
        {"sku": "SHADOW-ONLY", "store_id": "store-a", "nome_bling": "Scoped legacy product"},
        {"sku": "SHADOW-ONLY", "store_id": "store-b", "nome_bling": "Another store product"},
    ])
    source_before = compiled.read_bytes()

    result = _add("shadow-only")

    assert result["sku"] == "SHADOW-ONLY"
    assert result["lista"]["itens"][-1][TITULO_PRODUTO_INGLES_KEY] == "Scoped legacy product"
    assert compiled.read_bytes() == source_before
    assert inclusao["counts"]["persistencias"] == 1


@pytest.mark.parametrize("case", ["missing", "deleted", "other_store", "other_tenant", "ambiguous_store"])
def test_unavailable_store_sku_does_not_use_global_fallback_or_change_sources(inclusao, monkeypatch, case):
    _forbid_global_lookup(monkeypatch)
    tenant = inclusao["root"] / "cliente-a"
    sku = "MISSING"
    if case == "deleted":
        sku = "DELETED"
        _csv(tenant / "produtos_compilado.csv", [
            {"sku": sku, "store_id": "store-a", "nome_bling": "Stale deleted product"},
        ])
    elif case == "other_store":
        sku = "ONLY-OTHER-STORE"
        _csv(tenant / "produtos_compilado.csv", [
            {"sku": sku, "store_id": "store-b", "nome_bling": "Different store"},
        ])
    elif case == "other_tenant":
        sku = "ONLY-OTHER-TENANT"
        _csv(inclusao["root"] / "cliente-b" / "produtos_compilado.csv", [
            {"sku": sku, "store_id": "store-a", "nome_bling": "Different tenant"},
        ])
    elif case == "ambiguous_store":
        sku = "375-1"
        inclusao["stores"]["cliente-a"][1]["nomes_anteriores"] = ["Loja Antiga"]
        inclusao["write_stores"]("cliente-a")
        current = inclusao["lists"]["cliente-a"][0]
        current.pop("store_id")
        current["loja"] = "Loja Antiga"
    before = copy.deepcopy(inclusao["lists"])
    sources_before = {path: path.read_bytes() for path in inclusao["root"].rglob("*.csv")}

    with pytest.raises(HTTPException) as error:
        _add(sku)

    assert error.value.status_code == 404
    assert inclusao["lists"] == before
    assert inclusao["counts"]["persistencias"] == 0
    assert all(path.read_bytes() == data for path, data in sources_before.items())


@pytest.mark.parametrize("status", [404, 401, 403, 503])
def test_global_legacy_lookup_failure_never_lists_or_saves(inclusao, monkeypatch, status):
    _global_list(inclusao)
    before = copy.deepcopy(inclusao["lists"])

    def unavailable(**kwargs):
        raise HTTPException(status, "Synthetic lookup failure")

    def forbidden(*args, **kwargs):
        pytest.fail("A missing global SKU must not list or repair the full catalog")

    monkeypatch.setattr(lookup, "_obter_produto_cadastro_sync", unavailable)
    monkeypatch.setattr(listagem, "_listar_produtos_cadastro_sync", forbidden)

    with pytest.raises(HTTPException) as error:
        _add("MISSING")

    assert error.value.status_code == status
    assert inclusao["lists"] == before
    assert inclusao["counts"]["persistencias"] == 0


def test_global_legacy_product_still_uses_point_lookup(inclusao, monkeypatch):
    _global_list(inclusao)
    calls = []
    original = lookup._obter_produto_cadastro_sync

    def point_lookup(**kwargs):
        calls.append(kwargs["sku"])
        return original(**kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy inclusion must not list the full catalog")

    monkeypatch.setattr(lookup, "_obter_produto_cadastro_sync", point_lookup)
    monkeypatch.setattr(listagem, "_listar_produtos_cadastro_sync", forbidden)

    result = _add("LEGACY-1")

    assert calls == ["LEGACY-1"]
    assert result["sku"] == "LEGACY-1"
    assert inclusao["counts"]["persistencias"] == 1


def test_global_tombstone_blocks_stale_legacy_row_without_listing(inclusao, monkeypatch):
    _global_list(inclusao)
    _csv(inclusao["root"] / "cliente-a" / "cadastro_produtos.csv", [
        {"sku": "DELETED", "nome": "Stale deleted global row"},
    ])
    before = copy.deepcopy(inclusao["lists"])

    def forbidden(*args, **kwargs):
        pytest.fail("A tombstone must not trigger full-catalog fallback")

    monkeypatch.setattr(listagem, "_listar_produtos_cadastro_sync", forbidden)

    with pytest.raises(HTTPException) as error:
        _add("DELETED")

    assert error.value.status_code == 404
    assert inclusao["lists"] == before
    assert inclusao["counts"]["persistencias"] == 0
