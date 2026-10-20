"""Normalize legacy aliases once per item while preserving their precedence."""

import copy

import pytest

from backend.services import medias_compras_common as common


@pytest.mark.parametrize("item,expected", [
    ({" Quantidade ": "", "Qúantidade": 0, "quantidade": 7, "Qtd": 9}, {"Quantidade": 0}),
    ({"Quantidade": None, "quantidade": "4", "Qtd": "9"}, {"Quantidade": 4}),
    ({"Qtd": "4", "Quantidade": "7"}, {"Quantidade": 7}),
    ({"SKU": "001-JAN", "sku": "1-1"}, {"SKU": "001-JAN"}),
    ({"SKU": "", "sku": "375-1"}, {"SKU": "375-1"}),
    ({"SKU": "3751", "Qtd": 3, "Cost": "2,50", "Subtotal": "7,50"},
     {"SKU": "3751", "Quantidade": 3, "Valor unidade": 2.5, "Valor total": 7.5}),
    ({"Descrição": "Título preservado", "Description": "First title alias"},
     {common.TITULO_PRODUTO_INGLES_KEY: "First title alias"}),
    ({"Cor/Lado": "Esquerdo", "OEM": False}, {common.COR_LADO_LISTA_PEDIDO_KEY: "Esquerdo", "OEM": "False"}),
    ({"Quantidade": "-2", "Preço": "-4", "MOQ": -1, "package_multiple": 0},
     {"Quantidade": 0, "Valor unidade": 0.0, "MOQ": 0.0, "package_multiple": 1.0}),
    ({"M³ individual": "0,25", "Estimated CBM": "1,00", "Peso": "3"},
     {"M3 individual": "0,25", common.CBM_LISTA_PEDIDO_KEY: "1,00", common.PESO_LISTA_PEDIDO_KEY: "3"}),
    ({"NCM": "87089990", "ii": 0, "IPI": False, "Descrição NCM": "Descrição fiscal"},
     {"NCM": "87089990", "II": "0", "IPI": "False", "Descricao do NCM": "Descrição fiscal"}),
])
def test_alias_precedence_blanks_zeros_and_original_sku(item, expected):
    before = copy.deepcopy(item)
    normalized = common._normalizar_item_lista_pedido(item)
    assert {key: normalized[key] for key in expected} == expected
    assert item == before


@pytest.mark.parametrize("item", [None, [], "invalid", 0])
def test_invalid_item_preserves_empty_defaults(item):
    assert common._normalizar_item_lista_pedido(item) == common._normalizar_item_lista_pedido({})


def test_normalization_preserves_custom_data_and_refreshes_next_call():
    item = {"SKU": "ABC-01", "Observacoes": "Manter literalmente",
            "custom": {"flags": [False, 0, None]}, "Quantidade": 2}
    first = common._normalizar_item_lista_pedido(item)
    item["Quantidade"] = 5
    second = common._normalizar_item_lista_pedido(item)
    assert first["Quantidade"] == 2
    assert second["Quantidade"] == 5
    for result in (first, second):
        assert result["Observacoes"] == "Manter literalmente"
        assert result["custom"] == {"flags": [False, 0, None]}


def test_item_fields_are_scanned_once_for_all_aliases():
    class CountedItem(dict):
        scans = 0

        def items(self):
            self.scans += 1
            return super().items()

    item = CountedItem({"SKU": "375-1", "Quantidade": 3, "Cost": "5",
                        "NCM": "87089990", "Descrição NCM": "Fiscal"})
    result = common._normalizar_item_lista_pedido(item)
    assert result["SKU"] == "375-1" and result["Valor unidade"] == 5
    # One alias index, one copy of preserved fields and one description search.
    assert item.scans == 3


def test_public_text_helper_keeps_first_nonempty_value_without_stale_cache():
    item = {" Qúantidade ": "", "quantidade": 0, "Qtd": 7}
    assert common._primeiro_texto_item(item, ["Quantidade", "Qtd"]) == "0"
    item["quantidade"] = 8
    assert common._primeiro_texto_item(item, ["Quantidade", "Qtd"]) == "8"
    assert common._primeiro_texto_item(None, ["Quantidade"]) == ""
