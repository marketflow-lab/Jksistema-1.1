import asyncio

from backend.services import medias_compras_listas


class _WorksheetFake:
    def __init__(self, rows):
        self._rows = rows

    def get_all_values(self):
        return self._rows


class _SpreadsheetFake:
    def __init__(self, rows):
        self._rows = rows

    def get_worksheet(self, index):
        assert index == 0
        return _WorksheetFake(self._rows)


class _GoogleClientFake:
    def __init__(self, rows):
        self._rows = rows

    def open_by_key(self, spreadsheet_id):
        assert spreadsheet_id == "sheet-concorrentes"
        return _SpreadsheetFake(self._rows)


def test_concorrentes_endpoint_separa_anuncio_da_loja_e_concorrente(monkeypatch):
    header = ["SKU"] + [""] * 21
    row = [""] * 22
    row[0] = "37"

    for numero in range(5):
        base = 2 + (numero * 4)
        row[base] = f"https://produto.mercadolivre.com.br/MLB-{1000000000 + numero}"
        row[base + 1] = f"R$ {90 + numero},00"
        row[base + 2] = f"https://produto.mercadolivre.com.br/MLB-{2000000000 + numero}"
        row[base + 3] = f"R$ {120 + numero},00"

    monkeypatch.setattr(
        medias_compras_listas,
        "autenticar_google_sheets",
        lambda: _GoogleClientFake([header, row]),
        raising=False,
    )
    monkeypatch.setattr(
        medias_compras_listas,
        "SPREADSHEET_ID_CONCORRENTES",
        "sheet-concorrentes",
        raising=False,
    )

    payload = asyncio.run(
        medias_compras_listas.api_medias_compras_concorrentes_links(
            sku="37",
            client_id="tenant-test",
        )
    )

    assert payload["concorrentes_mlb"]["concorrente_1"].endswith("MLB-1000000000")
    assert payload["concorrentes_preco"]["concorrente_1"] == "R$ 90,00"
    assert payload["concorrentes"]["concorrente_1"].endswith("MLB-2000000000")
    assert payload["concorrentes_valores"]["concorrente_1"] == "R$ 120,00"
    assert payload["concorrentes_mlb"]["concorrente_5"].endswith("MLB-1000000004")
    assert payload["concorrentes"]["concorrente_5"].endswith("MLB-2000000004")
