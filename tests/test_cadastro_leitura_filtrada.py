"""Early legacy filtering keeps complete-view values and request boundaries."""

import pytest

from backend.services import cadastro_compatibilidade as cadastro
from backend.services import cadastro_lojas_produtos as produtos
from backend.services.medias_compras_leitura import leitura_medias
from test_medias_compras_carregamento import ambiente, _csv


@pytest.fixture
def legado(ambiente):
    tenant = ambiente["root"] / "cliente-a"
    rows = []
    for store in ("store-a", "store-b"):
        for sku in ("375-1", "3751", "001-JAN", "001", "1.0", "OTHER", "DELETED"):
            rows.append({
                "store_id": store, "loja_sync": "Stale name",
                "sku": sku, "nome": "Product " + sku,
                "m3 individual": "0.25" if store == "store-a" else "0.9",
                "oem": "OEM-" + sku,
            })
    _csv(tenant / "cadastro_produtos.csv", rows)
    _csv(tenant / "produtos_compilado.csv", [
        {"store_id": "store-a", "sku": "375-1", "produto_bling": "Scoped title", "ncm": "87089990"},
        {"store_id": "deleted-store", "loja_sync": "Loja A", "sku": "ORPHAN", "nome": "Not allowed"},
    ])
    _csv(tenant / "cadastro_custos_lojas.csv", [
        {"store_id": "store-a", "sku": "375-1", "custo": "23"},
        {"loja_sync": "Loja A", "sku": "375-1", "custo": "99"},
    ])
    persisted, _ = produtos._ler_registros_persistidos("cliente-a")
    persisted.append({"store_id": "store-a", "sku": "DELETED", "sku_normalizado": "DELETED",
                      "deleted_at_utc": "2026-10-20T10:00:00Z"})
    _csv(tenant / "cadastro_produtos_lojas.csv", persisted)
    return ambiente


@pytest.mark.parametrize("scope", ["store-a", "store-b", "__todas"])
@pytest.mark.parametrize("query,identities", [
    ("375-1", {"375-1", "3751"}),
    ("1", {"001", "1.0"}),
    ("001-JAN", {"1-1"}),
    ("DELETED", {"DELETED"}),
    ("ORPHAN", set()),
    ("MISSING", set()),
])
def test_filtered_legacy_projection_matches_full_values(legado, scope, query, identities):
    full = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", scope)
    filtered = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", scope, skus=[query])
    expected = [row for row in full["produtos"] if row["sku"] in identities]
    assert filtered == {**full, "produtos": expected}
    if scope == "store-a" and query == "375-1":
        selected = next(row for row in filtered["produtos"] if row["sku"] == "375-1")
        assert selected["custo"] == "23"
        assert selected["produto_bling"] == "Scoped title"
    assert not any(row["sku"] == "ORPHAN" for row in filtered["produtos"])


def test_unrelated_legacy_rows_do_not_resolve_store_labels(legado, monkeypatch):
    labels = []
    original = produtos._labels_loja

    def track(row):
        labels.append(row["sku"])
        return original(row)

    monkeypatch.setattr(produtos, "_labels_loja", track)
    cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=["375-1"])
    assert labels and set(labels) <= {"375-1", "3751"}
    labels.clear()
    assert cadastro.visao_produtos_cadastro_contexto_loja(
        "cliente-a", "store-a", skus=["MISSING"],
    )["produtos"] == []
    assert labels == []


def test_disjoint_filtered_and_full_reads_do_not_reuse_incomplete_index(legado):
    expected = {scope: cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", scope)
                for scope in ("store-a", "store-b")}
    with leitura_medias():
        first = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=["375-1"])
        second = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-b", skus=["OTHER"])
        empty = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=[])
        full = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
        repeated = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=iter(["375-1"]))
    assert first == repeated
    assert second["produtos"] == [row for row in expected["store-b"]["produtos"] if row["sku"] == "OTHER"]
    assert empty["produtos"] == []
    assert full == expected["store-a"]


def test_exact_document_filter_and_alias_filter_have_distinct_cached_indices(legado):
    exact = produtos._normalizar_sku_chave
    with leitura_medias():
        narrow = cadastro.visao_produtos_cadastro_contexto_loja(
            "cliente-a", "store-a", skus=["375-1"], _normalizar_sku_consulta=exact,
        )
        aliases = cadastro.visao_produtos_cadastro_contexto_loja(
            "cliente-a", "store-a", skus=["375-1"],
        )
    assert [row["sku"] for row in narrow["produtos"]] == ["375-1"]
    assert {row["sku"] for row in aliases["produtos"]} == {"375-1", "3751"}


def test_new_request_observes_updated_legacy_source(legado):
    before = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=["375-1"])
    _csv(legado["root"] / "cliente-a" / "cadastro_custos_lojas.csv", [
        {"store_id": "store-a", "sku": "375-1", "custo": "31"},
    ])
    after = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=["375-1"])
    assert next(row["custo"] for row in before["produtos"] if row["sku"] == "375-1") == "23"
    assert next(row["custo"] for row in after["produtos"] if row["sku"] == "375-1") == "31"
