from copy import deepcopy

from backend.services import favoritos_planilhas_colar as sut


class WorksheetFake:
    def __init__(self):
        self.calls = []

    def col_values(self, column):
        assert column == 1
        return ["SKU", "001"]

    def batch_update(self, updates, **kwargs):
        self.calls.append((deepcopy(updates), deepcopy(kwargs)))


class SpreadsheetFake:
    def __init__(self, worksheet):
        self.worksheet = worksheet

    def get_worksheet(self, index):
        assert index == 0
        return self.worksheet


class GoogleClientFake:
    def __init__(self, worksheet):
        self.worksheet = worksheet

    def open_by_key(self, spreadsheet_id):
        assert spreadsheet_id == "sheet-test"
        return SpreadsheetFake(self.worksheet)


def test_colar_historico_grava_apenas_valores_raw_sem_formatacao(monkeypatch):
    worksheet = WorksheetFake()

    monkeypatch.setattr(
        sut.favoritos_service,
        "_chave_loja_favoritos",
        lambda _loja: "jk-pecas",
    )
    monkeypatch.setattr(
        sut.favoritos_service,
        "_favoritos_carregar_planilhas_lojas",
        lambda _client_id: {
            "planilhas": {
                "jk-pecas": {
                    "url": "https://docs.google.com/spreadsheets/d/sheet-test/edit"
                }
            }
        },
    )
    monkeypatch.setattr(
        sut,
        "_autenticar_google_sheets_favoritos",
        lambda: GoogleClientFake(worksheet),
    )

    resultado = sut.favoritos_colar_historico_planilha(
        "tenant-test",
        "JK Pecas",
        {
            "sku": "001",
            "data_iso": "2026-07-21T10:00:00-03:00",
            "vinculos": [
                {
                    "ordem": 1,
                    "nosso": {
                        "url": "https://produto.mercadolivre.com.br/MLB-111",
                        "price": 100,
                        "custo_produto": 42,
                    },
                    "base": {
                        "url": "https://produto.mercadolivre.com.br/MLB-222",
                        "price": 99,
                    },
                }
            ],
        },
    )

    assert resultado["success"] is True
    assert len(worksheet.calls) == 1
    updates, kwargs = worksheet.calls[0]

    assert kwargs == {"value_input_option": "RAW"}
    assert [item["range"] for item in updates] == [
        "C2:Z2",
        "AA2:AB2",
        "AC2",
        "AE2:AF2",
        "AI2",
    ]
    assert all(set(item) == {"range", "values"} for item in updates)

    valores_c_z = updates[0]["values"][0]
    assert len(valores_c_z) == 24
    assert valores_c_z[:4] == [
        "https://produto.mercadolivre.com.br/MLB-111",
        100.0,
        "https://produto.mercadolivre.com.br/MLB-222",
        99.0,
    ]
    assert valores_c_z[4:] == [""] * 20
    assert updates[1]["values"] == [["21/07/2026", "BJ"]]
    assert updates[2]["values"] == [[""]]
    assert updates[3]["values"] == [["", 42.0]]
