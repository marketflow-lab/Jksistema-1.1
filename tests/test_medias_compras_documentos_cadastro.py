import copy
import io

import pytest
from openpyxl import load_workbook
from PIL import Image

from backend.services import cadastro_importador_loja
from backend.services import medias_compras_documentos_cadastro as cadastro_documentos
from backend.services import medias_compras_excel as excel


def _lista():
    return {
        "id": "lista-documentos",
        "store_id": "store-a",
        "itens": [{
            "SKU": "SKU:375-2",
            "Título do produto em inglês": "Supplier description",
            "Foto": "supplier.png",
            "NCM": "85365090",
            "Quantidade": 3,
            "Valor unidade": 2.5,
            "Valor total": 7.5,
        }],
    }


def _sheet(conteudo):
    return load_workbook(io.BytesIO(conteudo))["Commercial"]


def _catalogo_invoice(monkeypatch, produto, *, store_id="store-a", scope="store"):
    chamadas = []
    monkeypatch.setattr(cadastro_importador_loja, "ler_importador_loja_por_store_id", lambda *_args: None)

    def contexto(lista, client_id):
        chamadas.append((client_id, lista.get("store_id")))
        return {"store_id": store_id, "scope": scope}

    monkeypatch.setattr(cadastro_documentos, "_contexto_cadastro_documentos", contexto)
    monkeypatch.setattr(
        cadastro_documentos, "_dados_produto_documento",
        lambda contexto, sku: copy.deepcopy(produto),
    )
    return chamadas


def test_invoice_cadastro_substitui_descricao_foto_e_preserva_valores(tmp_path, monkeypatch):
    foto = tmp_path / "cadastro.png"
    Image.new("RGB", (120, 60), "green").save(foto)
    descricao = "LED Signal Lights, Material: Plastic and metal, cardboard box"
    contexto = _catalogo_invoice(monkeypatch, {
        "sku": "375-2", "descricao": descricao,
        "foto": "cadastro_fotos/375-2.png", "unidade": "Unit",
    })
    fotos = []

    def resolver(client_id, referencia, sku, store_id=None):
        fotos.append((client_id, referencia, sku, store_id))
        return str(foto)

    monkeypatch.setattr(excel, "_resolver_caminho_foto_commercial_invoice", resolver)
    lista = _lista()
    original = copy.deepcopy(lista)
    sheet = _sheet(excel._gerar_commercial_invoice_bytes(lista, client_id="tenant-a"))

    assert sheet["D9"].value == descricao
    assert sheet["C9"].value == "8536.50.90"
    assert sheet["G9"].value == 3
    assert sheet["H9"].value == 2.5
    assert sheet["I9"].value == 7.5
    assert sheet["I10"].value == 7.5
    assert len(sheet._images) == 1
    assert Image.open(io.BytesIO(sheet._images[0]._data())).getpixel((0, 0)) == (0, 128, 0)
    assert fotos == [("tenant-a", "cadastro_fotos/375-2.png", "375-2", "store-a")]
    assert contexto == [("tenant-a", "store-a")]
    assert lista == original


@pytest.mark.parametrize("scope", ["store", "unresolved", "unavailable"])
def test_invoice_sem_cadastro_nao_reutiliza_descricao_ou_foto_externa(monkeypatch, scope):
    _catalogo_invoice(monkeypatch, {
        "sku": "", "descricao": "", "foto": "", "unidade": "",
    }, store_id="" if scope != "store" else "store-a", scope=scope)
    monkeypatch.setattr(
        excel, "_resolver_caminho_foto_commercial_invoice",
        lambda *_args: pytest.fail("Foto ausente no cadastro deve permanecer vazia"),
    )
    sheet = _sheet(excel._gerar_commercial_invoice_bytes(_lista(), client_id="tenant-a"))

    assert sheet["D9"].value is None
    assert not sheet._images
    assert sheet["G9"].value == 3
    assert sheet["I9"].value == 7.5


def test_nova_invoice_consulta_metadados_atuais_e_preserva_texto_literal(monkeypatch):
    produto = {"sku": "375-2", "descricao": "First registered description", "foto": "", "unidade": "Unit"}
    chamadas = _catalogo_invoice(monkeypatch, produto)
    lista = _lista()
    primeira = _sheet(excel._gerar_commercial_invoice_bytes(lista, client_id="tenant-a"))
    produto["descricao"] = "=New registered description"
    segunda = _sheet(excel._gerar_commercial_invoice_bytes(lista, client_id="tenant-a"))

    assert primeira["D9"].value == "First registered description"
    assert segunda["D9"].value == "=New registered description"
    assert segunda["D9"].data_type == "s"
    assert chamadas == [("tenant-a", "store-a"), ("tenant-a", "store-a")]


def test_invoice_interna_sem_client_id_preserva_comportamento_legado(monkeypatch):
    monkeypatch.setattr(
        cadastro_documentos, "_contexto_cadastro_documentos",
        lambda *_args: pytest.fail("Uso interno sem cliente preserva a linha recebida"),
    )
    monkeypatch.setattr(excel, "_resolver_caminho_foto_commercial_invoice", lambda *_args: None)
    sheet = _sheet(excel._gerar_commercial_invoice_bytes(_lista()))

    assert sheet["D9"].value == "Supplier description"
    assert sheet["G9"].value == 3
    assert sheet["I9"].value == 7.5


def test_invoice_nao_embute_foto_default_quando_arquivo_falta_no_tenant(tmp_path, monkeypatch):
    info = tmp_path / "info"
    tenant = info / "tenant-a"
    (tenant / "cadastro_fotos").mkdir(parents=True)
    foto_default = info / "default" / "cadastro_fotos" / "cadastro.png"
    foto_default.parent.mkdir(parents=True)
    Image.new("RGB", (120, 60), "red").save(foto_default)
    _catalogo_invoice(monkeypatch, {
        "sku": "375-2", "descricao": "Registered technical description",
        "foto": "cadastro_fotos/cadastro.png", "unidade": "Unit",
    })
    monkeypatch.setattr(excel, "get_tenant_path", lambda _client_id: str(tenant))
    monkeypatch.setattr(excel, "PASTA_INFO", str(info))
    monkeypatch.setattr(
        excel, "_resolver_foto_cadastro_sku",
        lambda _client_id, _sku, foto_ref, _store_id=None: foto_ref,
    )

    sheet = _sheet(excel._gerar_commercial_invoice_bytes(_lista(), client_id="tenant-a"))

    assert foto_default.is_file()
    assert not (tenant / "cadastro_fotos" / "cadastro.png").exists()
    assert sheet["D9"].value == "Registered technical description"
    assert not sheet._images
    assert sheet["G9"].value == 3
    assert sheet["I9"].value == 7.5
