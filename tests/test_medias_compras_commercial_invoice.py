import asyncio
import base64
import copy
import io

import pytest
from openpyxl import load_workbook

from backend.routers.medias_compras import create_medias_compras_router
from backend.schemas.medias_compras import ListaPedidoUpdateRequest
from backend.services import medias_compras_excel as excel_service
from backend.services import medias_compras_listas as listas_service


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zc1sAAAAASUVORK5CYII="
)


def _lista_aprovada():
    return {
        "id": "lista-1",
        "nome_lista": "JK 48",
        "loja": "JK Pecas",
        "numero_invoice": "JK48-18082026",
        "supplier": "Fornecedor Exemplo Ltd.",
        "currency": "USD",
        "incoterm": "EXW",
        "itens": [
            {
                "SKU": "001",
                "NCM": "85365090",
                "Título do produto em inglês": "Temperature sensor switch",
                "Quantidade": 2,
                "Valor unidade": 3.5,
                "Foto": "001.png",
                "compra_aprovada": True,
                "compra_aprovada_em": "2026-08-18T09:00:00",
            },
            {
                "SKU": "SKU 002",
                "Código NCM": "8413.70.10",
                "Título em português": "=HYPERLINK(\"https://example.invalid\",\"unsafe\")",
                "Quantidade": 3,
                "Valor unidade": 0,
                "Valor total": 12,
                "compra_aprovada": True,
                "compra_aprovada_em": "2026-08-18T14:30:00",
            },
        ],
    }


def _carregar_commercial_invoice(conteudo: bytes):
    assert conteudo.startswith(b"PK")
    workbook = load_workbook(io.BytesIO(conteudo), data_only=False)
    assert workbook.sheetnames == ["Commercial"]
    return workbook["Commercial"]


def _formulas(sheet):
    return [
        cell.coordinate
        for row in sheet.iter_rows()
        for cell in row
        if cell.data_type == "f"
    ]


def test_gerador_commercial_invoice_reproduz_modelo_totais_e_foto(tmp_path, monkeypatch):
    raiz_tenant = tmp_path / "tenant-a"
    foto = raiz_tenant / "cadastro_fotos" / "001.png"
    foto.parent.mkdir(parents=True)
    foto.write_bytes(PNG_1X1)
    monkeypatch.setattr(excel_service, "get_tenant_path", lambda _client_id: str(raiz_tenant))
    monkeypatch.setattr(
        excel_service,
        "_resolver_foto_cadastro_sku",
        lambda _client_id, sku, _foto_ref: str(foto) if sku == "001" else "",
    )

    sheet = _carregar_commercial_invoice(
        excel_service._gerar_commercial_invoice_bytes(
            _lista_aprovada(),
            client_id="tenant-a",
            data_aprovacao="2026-08-18T14:30:00",
        )
    )

    assert sheet.freeze_panes is None
    assert sheet.sheet_view.showGridLines is True
    assert sheet.page_setup.orientation == "portrait"
    assert sheet.max_row == 15
    assert sheet.max_column == 9
    assert _formulas(sheet) == []
    assert sheet["A1"].value == "Fornecedor Exemplo Ltd."
    assert sheet["A2"].value == "COMMERCIAL INVOICE"
    assert sheet["A3"].value == "INV. No.:"
    assert sheet["C3"].value == "JK48-18082026"
    assert sheet["D3"].value == "DATE:"
    assert sheet["E3"].value == "August 18, 2026"
    assert sheet["F3"].value == "FROM:"
    assert sheet["H3"].value in (None, "")
    assert sheet["A4"].value == "LIST: JK 48"
    assert sheet["F4"].value == "STORE: JK Pecas"
    assert sheet["A7"].value == "Descriptions"
    assert sheet["C8"].value == "HSCODE"

    assert sheet["B9"].value == "SKU 001"
    assert sheet["C9"].value == "8536.50.90"
    assert sheet["D9"].value == "Temperature sensor switch"
    assert sheet["G9"].value == 2
    assert sheet["H9"].value == 3.5
    assert "US$" not in sheet["H9"].number_format
    assert sheet["I9"].value == 7
    assert "US$" in sheet["I9"].number_format
    assert sheet["B10"].value == "SKU 002"
    assert sheet["D10"].value.startswith("=HYPERLINK")
    assert sheet["D10"].data_type == "s"
    assert sheet["C10"].value == "8413.70.10"
    assert sheet["H10"].value == 4
    assert sheet["I10"].value == 12

    assert sheet["C11"].value == "TOTAL NET WEIGHT"
    assert sheet["F11"].value == "Units Total"
    assert sheet["G11"].value == 5
    assert sheet["H11"].value == "Subtotal"
    assert sheet["I11"].value == 19
    assert sheet["F12"].value == "Freight USD"
    assert sheet["I12"].value in (None, "")
    assert sheet["F13"].value == "Insurance USD"
    assert sheet["I13"].value in (None, "")
    assert sheet["F14"].value == "Shipping:"
    assert sheet["G14"].value in (None, "")
    assert sheet["H14"].value == "TOTAL:"
    assert sheet["I14"].value == 19
    assert "Incoterms: EXW" in sheet["A15"].value
    assert "Payment Currency: USD" in sheet["A15"].value
    assert sheet["E15"].value in (None, "")
    assert sheet["I15"].value == "Company stamp and signature"

    merges = {str(intervalo) for intervalo in sheet.merged_cells.ranges}
    assert {
        "A1:I1",
        "A2:I2",
        "A3:B3",
        "F3:G3",
        "H3:I3",
        "A4:E6",
        "F4:I6",
        "A7:H7",
        "D8:E8",
        "D9:E9",
        "D10:E10",
        "A15:D15",
        "E15:H15",
    } <= merges
    assert sheet.column_dimensions["A"].width == pytest.approx(6)
    assert sheet.column_dimensions["B"].width == pytest.approx(15.63)
    assert sheet.column_dimensions["E"].width == pytest.approx(27.75)
    assert sheet.column_dimensions["I"].width == pytest.approx(12.38)
    assert sheet.row_dimensions[1].height == pytest.approx(93)
    assert sheet.row_dimensions[6].height == pytest.approx(147)
    assert sheet.row_dimensions[9].height == pytest.approx(60)
    assert sheet.row_dimensions[15].height == pytest.approx(101.25)
    assert len(sheet._images) == 1
    assert sheet._images[0].anchor._from.col == 5
    assert sheet._images[0].anchor._from.row == 8


def test_gerador_inclui_pendentes_e_mantem_numericos_ausentes_em_branco():
    lista = {
        "id": "rascunho",
        "itens": [
            {"SKU": "001", "Título em português": "+nao executar", "compra_aprovada": False},
            {"SKU": "SKU 002", "compra_aprovada": None},
        ],
    }
    sheet = _carregar_commercial_invoice(
        excel_service._gerar_commercial_invoice_bytes(lista, data_aprovacao="")
    )

    for endereco in ("A1", "C3", "E3", "H3", "A4", "F4"):
        assert sheet[endereco].value in (None, "")
    assert sheet["A15"].value in (None, "")
    assert sheet["E15"].value in (None, "")
    assert sheet["B9"].value == "SKU 001"
    assert sheet["D9"].value == "+nao executar"
    assert sheet["D9"].data_type == "s"
    assert sheet["B10"].value == "SKU 002"
    for endereco in ("G9", "H9", "I9", "G10", "H10", "I10"):
        assert sheet[endereco].value in (None, "")
    assert sheet["G11"].value == 0
    assert sheet["I11"].value == 0
    assert sheet["I14"].value == 0
    assert _formulas(sheet) == []


def test_gerador_commercial_invoice_aceita_lista_vazia():
    sheet = _carregar_commercial_invoice(
        excel_service._gerar_commercial_invoice_bytes({"id": "vazia", "itens": []})
    )

    assert sheet.max_row == 13
    assert sheet["C9"].value == "TOTAL NET WEIGHT"
    assert sheet["G9"].value == 0
    assert sheet["I9"].value == 0
    assert sheet["H12"].value == "TOTAL:"
    assert sheet["I12"].value == 0
    assert sheet["I13"].value == "Company stamp and signature"
    assert _formulas(sheet) == []


def test_gerador_posiciona_resumo_depois_de_93_itens():
    itens = [
        {
            "SKU": f"{indice:03d}",
            "NCM": "85365090",
            "Título em português": f"Produto {indice}",
            "Quantidade": 1,
            "Valor unidade": 2,
        }
        for indice in range(1, 94)
    ]

    sheet = _carregar_commercial_invoice(
        excel_service._gerar_commercial_invoice_bytes({"id": "jk-48", "itens": itens})
    )

    assert sheet.max_row == 106
    assert sheet["B101"].value == "SKU 093"
    assert sheet["C102"].value == "TOTAL NET WEIGHT"
    assert sheet["G102"].value == 93
    assert sheet["I102"].value == 186
    assert sheet["H105"].value == "TOTAL:"
    assert sheet["I105"].value == 186
    assert sheet["I106"].value == "Company stamp and signature"
    merges = {str(intervalo) for intervalo in sheet.merged_cells.ranges}
    assert "D101:E101" in merges
    assert "C102:D102" in merges
    assert "A106:D106" in merges
    assert "E106:H106" in merges


def test_foto_commercial_invoice_nao_pode_sair_do_tenant(tmp_path, monkeypatch):
    raiz_tenant = tmp_path / "tenant-a"
    (raiz_tenant / "cadastro_fotos").mkdir(parents=True)
    foto_outro_tenant = tmp_path / "tenant-b" / "cadastro_fotos" / "001.png"
    foto_outro_tenant.parent.mkdir(parents=True)
    foto_outro_tenant.write_bytes(PNG_1X1)
    monkeypatch.setattr(excel_service, "get_tenant_path", lambda _client_id: str(raiz_tenant))
    monkeypatch.setattr(excel_service, "PASTA_INFO", str(tmp_path / "default-info"))
    monkeypatch.setattr(excel_service, "_resolver_foto_cadastro_sku", lambda *_args: str(foto_outro_tenant))

    assert excel_service._resolver_caminho_foto_commercial_invoice("tenant-a", "", "001") is None


def test_endpoint_inclui_pendentes_e_usa_aprovacao_mais_recente(monkeypatch):
    lista = _lista_aprovada()
    lista["itens"][1]["compra_aprovada"] = False
    recebido = {}
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])

    def gerar(lista_recebida, *, client_id, data_aprovacao):
        recebido.update(lista=copy.deepcopy(lista_recebida), client_id=client_id, data_aprovacao=data_aprovacao)
        return b"PK\x03\x04xlsx"

    monkeypatch.setattr(listas_service, "_gerar_commercial_invoice_bytes", gerar)
    resposta = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_commercial_invoice("lista-1", client_id="tenant-a")
    )

    assert recebido["client_id"] == "tenant-a"
    assert recebido["data_aprovacao"] == "2026-08-18T14:30:00"
    assert len(recebido["lista"]["itens"]) == 2
    assert recebido["lista"]["itens"][1]["compra_aprovada"] is False
    assert resposta.media_type.endswith("spreadsheetml.sheet")
    assert resposta.headers["content-disposition"] == 'attachment; filename="commercial_invoice_JK48-18082026.xlsx"'


def test_endpoint_aceita_campos_datas_aprovacoes_e_lista_vazios(monkeypatch):
    lista = {"id": "lista-1", "nome_lista": "JK 48", "numero_invoice": "", "supplier": "", "itens": []}
    recebido = {}
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])

    def gerar(lista_recebida, *, client_id, data_aprovacao):
        recebido.update(lista=copy.deepcopy(lista_recebida), data_aprovacao=data_aprovacao)
        return b"PK\x03\x04xlsx"

    monkeypatch.setattr(listas_service, "_gerar_commercial_invoice_bytes", gerar)
    resposta = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_commercial_invoice("lista-1", client_id="tenant-a")
    )

    assert recebido["lista"]["itens"] == []
    assert recebido["data_aprovacao"] == ""
    assert resposta.headers["content-disposition"] == 'attachment; filename="commercial_invoice_JK_48.xlsx"'


@pytest.mark.parametrize(
    ("numero_invoice", "nome_lista", "lista_id", "esperado"),
    [
        ("INV 01", "JK 48", "lista-1", "commercial_invoice_INV_01.xlsx"),
        ("", "JK 48", "lista-1", "commercial_invoice_JK_48.xlsx"),
        ("", "", "lista-1", "commercial_invoice_lista-1.xlsx"),
    ],
)
def test_endpoint_nome_arquivo_prioriza_invoice_lista_e_id(monkeypatch, numero_invoice, nome_lista, lista_id, esperado):
    lista = {"id": lista_id, "numero_invoice": numero_invoice, "nome_lista": nome_lista, "itens": []}
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])
    monkeypatch.setattr(listas_service, "_gerar_commercial_invoice_bytes", lambda *_args, **_kwargs: b"PK\x03\x04xlsx")

    resposta = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_commercial_invoice(lista_id, client_id="tenant-a")
    )

    assert resposta.headers["content-disposition"] == f'attachment; filename="{esperado}"'


def test_numero_invoice_passa_a_ser_persistido_no_backend(monkeypatch):
    lista = _lista_aprovada()
    lista["numero_invoice"] = ""
    estado = [lista]
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: copy.deepcopy(estado))

    def salvar(_client_id, listas):
        estado[:] = copy.deepcopy(listas)

    monkeypatch.setattr(listas_service, "_salvar_listas_pedidos", salvar)
    monkeypatch.setattr(listas_service, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        listas_service,
        "_resumo_lista_pedido",
        lambda item, *_args: {"id": item["id"], "numero_invoice": item.get("numero_invoice")},
    )

    resultado = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_editar(
            "lista-1",
            ListaPedidoUpdateRequest(numero_invoice="JK48-18082026"),
            client_id="tenant-a",
        )
    )

    assert estado[0]["numero_invoice"] == "JK48-18082026"
    assert resultado["lista"]["numero_invoice"] == "JK48-18082026"


def test_router_expoe_download_do_commercial_invoice():
    rota = next(
        (
            item
            for item in create_medias_compras_router().routes
            if item.path == "/api/medias-compras/listas-pedidos/{lista_id}/commercial-invoice"
        ),
        None,
    )
    assert rota is not None
    assert rota.methods == {"GET"}
