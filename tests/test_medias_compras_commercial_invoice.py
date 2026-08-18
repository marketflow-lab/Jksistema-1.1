import asyncio
import base64
import copy
import io

import pytest
from fastapi import HTTPException
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
                "SKU": "002",
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


def test_gerador_commercial_invoice_reproduz_campos_totais_e_foto(tmp_path, monkeypatch):
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

    conteudo = excel_service._gerar_commercial_invoice_bytes(
        _lista_aprovada(),
        client_id="tenant-a",
        data_aprovacao="2026-08-18T14:30:00",
    )

    assert conteudo.startswith(b"PK")
    workbook = load_workbook(io.BytesIO(conteudo), data_only=False)
    sheet = workbook["Commercial Invoice"]
    assert sheet["A1"].value == "SUPPLIER: Fornecedor Exemplo Ltd."
    assert sheet["A2"].value == "COMMERCIAL INVOICE"
    assert sheet["C3"].value == "JK48-18082026"
    assert sheet["H3"].value == "18/08/2026"
    assert sheet["A4"].value == "LIST: JK 48"
    assert sheet["F4"].value == "STORE: JK Pecas"
    assert sheet["C7"].value == "8536.50.90"
    assert sheet["D7"].value == "Temperature sensor switch"
    assert sheet["G7"].value == 2
    assert sheet["H7"].value == 3.5
    assert sheet["I7"].value == "=G7*H7"
    assert sheet["D8"].value.startswith("=HYPERLINK")
    assert sheet["D8"].data_type == "s"
    assert sheet["C8"].value == "8413.70.10"
    assert sheet["H8"].value == 4
    assert sheet["G9"].value == "=SUM(G7:G8)"
    assert sheet["I9"].value == "=SUM(I7:I8)"
    assert sheet["I10"].value == "=I9"
    assert len(sheet._images) == 1
    assert "D7:E7" in {str(intervalo) for intervalo in sheet.merged_cells.ranges}


def test_foto_commercial_invoice_nao_pode_sair_do_tenant(tmp_path, monkeypatch):
    raiz_tenant = tmp_path / "tenant-a"
    (raiz_tenant / "cadastro_fotos").mkdir(parents=True)
    foto_outro_tenant = tmp_path / "tenant-b" / "cadastro_fotos" / "001.png"
    foto_outro_tenant.parent.mkdir(parents=True)
    foto_outro_tenant.write_bytes(PNG_1X1)
    monkeypatch.setattr(excel_service, "get_tenant_path", lambda _client_id: str(raiz_tenant))
    monkeypatch.setattr(excel_service, "PASTA_INFO", str(tmp_path / "default-info"))
    monkeypatch.setattr(
        excel_service,
        "_resolver_foto_cadastro_sku",
        lambda *_args: str(foto_outro_tenant),
    )

    assert excel_service._resolver_caminho_foto_commercial_invoice("tenant-a", "", "001") is None


def test_endpoint_bloqueia_enquanto_houver_sku_pendente(monkeypatch):
    lista = _lista_aprovada()
    lista["itens"][1].pop("compra_aprovada")
    lista["itens"][1].pop("compra_aprovada_em")
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_commercial_invoice(
                "lista-1",
                client_id="tenant-a",
            )
        )

    assert exc_info.value.status_code == 409
    assert "Faltam 1 SKU pendente" in exc_info.value.detail


def test_endpoint_exige_invoice_e_fornecedor(monkeypatch):
    lista = _lista_aprovada()
    lista["numero_invoice"] = ""
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])
    with pytest.raises(HTTPException, match="Invoice"):
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_commercial_invoice(
                "lista-1",
                client_id="tenant-a",
            )
        )

    lista["numero_invoice"] = "JK48-18082026"
    lista["supplier"] = ""
    with pytest.raises(HTTPException, match="Fornecedor"):
        asyncio.run(
            listas_service.api_medias_compras_lista_pedido_commercial_invoice(
                "lista-1",
                client_id="tenant-a",
            )
        )


def test_endpoint_usa_aprovacao_mais_recente_e_nome_seguro(monkeypatch):
    lista = _lista_aprovada()
    recebido = {}
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: [lista])

    def gerar(lista_recebida, *, client_id, data_aprovacao):
        recebido.update(
            lista=copy.deepcopy(lista_recebida),
            client_id=client_id,
            data_aprovacao=data_aprovacao,
        )
        return b"PK\x03\x04xlsx"

    monkeypatch.setattr(listas_service, "_gerar_commercial_invoice_bytes", gerar)
    resposta = asyncio.run(
        listas_service.api_medias_compras_lista_pedido_commercial_invoice(
            "lista-1",
            client_id="tenant-a",
        )
    )

    assert recebido["client_id"] == "tenant-a"
    assert recebido["data_aprovacao"] == "2026-08-18T14:30:00"
    assert resposta.media_type.endswith("spreadsheetml.sheet")
    assert resposta.headers["content-disposition"] == 'attachment; filename="commercial_invoice_JK48-18082026.xlsx"'


def test_numero_invoice_passa_a_ser_persistido_no_backend(monkeypatch):
    lista = _lista_aprovada()
    lista["numero_invoice"] = ""
    estado = [lista]
    monkeypatch.setattr(listas_service, "_carregar_listas_pedidos", lambda _client_id: copy.deepcopy(estado))

    def salvar(_client_id, listas):
        estado[:] = copy.deepcopy(listas)

    monkeypatch.setattr(listas_service, "_salvar_listas_pedidos", salvar)
    monkeypatch.setattr(listas_service, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(listas_service, "_resumo_lista_pedido", lambda item, *_args: {"id": item["id"], "numero_invoice": item.get("numero_invoice")})

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
