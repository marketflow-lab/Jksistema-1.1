"""Historical metadata indexing preserves the unindexed lookup contracts."""

import copy
import csv

import pytest

from backend.services import medias_compras_fiscal as fiscal
from test_medias_compras_carregamento import ambiente


@pytest.mark.parametrize("row", [
    {"Título": " ", "TITULO": " first ", "ti-tulo": "second", "Descrição": "fallback", "Foto": " photo "},
    {"monofasico_verificado_em": "2026-10-01", "OEM/ Model": "OEM-001", "CG OEM": "later", "color/side": "Left"},
    {"OEM": 0, "Titulo": False, "Descrição": None, "foto": "", "url produto": "https://example.test/a?q=1"},
    {"link aliexpress": "https://user:password@example.test/a", "product link": "https://example.test/b", "link": "https://later.test"},
    {"product description extra": " fuzzy first ", "Product Description": "exact", "image_url": "img", "nome completo": "name"},
])
def test_indexed_row_matches_plain_lookups_and_does_not_mutate_input(row):
    original = copy.deepcopy(row)
    indexed = fiscal._LinhaCadastroMeta(row)
    for candidates in (
        fiscal._META_TITULO_CANDIDATOS_FORTES, fiscal._META_TITULO_CANDIDATOS_FALLBACK,
        fiscal._META_FOTO_CANDIDATOS, fiscal._META_COR_LADO_CANDIDATOS,
        ["product description"], ["not present"], [],
    ):
        assert fiscal._pick_cadastro_meta(indexed, candidates) == fiscal._pick_cadastro_meta(row, candidates)
    assert fiscal._pick_cadastro_oem(indexed) == fiscal._pick_cadastro_oem(row)
    assert fiscal._pick_cadastro_link(indexed) == fiscal._pick_cadastro_link(row)
    def photo(_client, _sku, ref, _store):
        return ref
    for fallback in (False, True):
        assert fiscal._meta_payload_cadastro("tenant", "001", indexed, fallback, "store", resolver_foto=photo) == fiscal._meta_payload_cadastro(
            "tenant", "001", row, fallback, "store", resolver_foto=photo,
        )
    assert row == original


@pytest.mark.parametrize("preservar_historico", [False, True])
def test_complete_enrichment_matches_plain_rows_with_wide_ordered_history(ambiente, monkeypatch, tmp_path, preservar_historico):
    ambiente["catalog"] = []
    files = []
    for source in range(4):
        path = tmp_path / f"history-{source}.csv"
        rows = []
        for number in range(1, 78):
            row = {"sku": f"{number:03}", "ncm": "87089990", "cest": "0100100",
                   "titulo": f"First title {number}" if source == 0 else "Later title",
                   "OEM/ Model": f"OEM-{number}", "color/side": "Left", "m3 individual": "0,125",
                   "m3_individual": "", "foto": "", "descrição": "Description fallback",
                   "link": f"https://{'aliexpress' if source == 3 else 'example'}.test/{number}"}
            row.update({f"unused column {index}": f"literal {index}" for index in range(80)})
            rows.append(row)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        files.append(str(path))
    monkeypatch.setattr(fiscal, "_caminhos_cadastros_meta", lambda _client: files)
    calls = []
    normalize = fiscal._normalizar_coluna_cadastro_meta
    def count(value):
        calls.append(1)
        return normalize(value)
    monkeypatch.setattr(fiscal, "_normalizar_coluna_cadastro_meta", count)
    items = [{"SKU": f"{number:03}", "Quantidade": 2, "M3": 0.8, "M3 individual": 0.4,
              "Valor unidade": 13.7, "campo privado": "literal"} for number in range(1, 78)]
    items[0]["OEM"] = "Literal item OEM"
    original = copy.deepcopy(items)
    with monkeypatch.context() as unindexed:
        # Exercise the original public helper paths: plain rows reconstruct
        # their text indexes for each lookup, as before this optimization.
        unindexed.setattr(fiscal, "_indexar_linha_cadastro_meta", lambda row, _normalizer: row)
        expected = fiscal._enriquecer_itens_lista_pedido_com_impostos(
            "cliente-a", items, loja="store-a", preservar_cubagem=preservar_historico,
        )
    old_normalizations = len(calls)
    calls.clear()
    actual = fiscal._enriquecer_itens_lista_pedido_com_impostos(
        "cliente-a", items, loja="store-a", preservar_cubagem=preservar_historico,
    )
    assert actual == expected
    assert items == original
    assert [item["SKU"] for item in actual] == [item["SKU"] for item in items]
    assert actual[0]["OEM"] == "Literal item OEM"
    assert actual[1]["Link"] == "https://aliexpress.test/2"
    assert actual[1][fiscal.TITULO_PRODUTO_INGLES_KEY] == "First title 2"
    if preservar_historico:
        assert all(item["M3"] == 0.8 and item["M3 individual"] == 0.4 for item in actual)
    # A deterministic operation budget, not a timing assertion on this host.
    assert len(calls) < old_normalizations / 4
