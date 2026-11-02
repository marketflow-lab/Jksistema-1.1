"""Behavioral coverage for marketplace label layout composition and HTTP access."""

from __future__ import annotations

import base64
import copy
import json

import fitz
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from backend.routers.etiquetas import EtiquetasRouterConfig, create_etiquetas_router
from backend.services.etiquetas_layout import (
    gerar_preview_layout,
    layout_padrao,
    validar_layout,
)
from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


CHAVE = "12345678901234567890123456789012345678901234"
SKU = "000123-TESTE"
MM = 72 / 25.4


def _fonte_ml() -> bytes:
    """The shipment label intentionally has no SKU or fiscal barcode baked in."""
    with fitz.open() as doc:
        etiqueta = doc.new_page(width=283, height=390)
        etiqueta.insert_text((20, 30), "DESTINATARIO", fontsize=9)
        etiqueta.insert_text((20, 55), "PEDIDO ABC123456", fontsize=9)
        etiqueta.insert_text((20, 90), "SOURCE MARKER", fontsize=9)
        etiqueta.draw_rect(fitz.Rect(10, 10, 250, 380), width=0.4)
        declaracao = doc.new_page(width=283, height=390)
        declaracao.insert_text((20, 30), "CHAVE DE ACESSO", fontsize=7)
        declaracao.insert_text((20, 55), CHAVE, fontsize=5)
        lista = doc.new_page(width=283, height=390)
        lista.insert_textbox(
            fitz.Rect(20, 20, 260, 200),
            f"MANIFESTO\nPEDIDO ABC123456\nSKU {SKU}\nQTD 2",
            fontsize=9,
        )
        return doc.tobytes()


def _layout_livre():
    layout = copy.deepcopy(layout_padrao("Mercado Livre"))
    layout["page"] = {"width": 100, "height": 150}
    layout["items"] = {
        "etiqueta": {"x": 5, "y": 5, "width": 70, "height": 100, "rotation": 0, "visible": True},
        "sku": {"x": 5, "y": 115, "width": 85, "height": 10, "rotation": 0, "visible": True},
        "danfe": {"x": 5, "y": 130, "width": 85, "height": 15, "rotation": 0, "visible": True},
    }
    return layout


def _png(data_url):
    assert data_url.startswith("data:image/png;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    return fitz.Pixmap(raw)


@pytest.mark.parametrize("store", ["Mercado Livre", "Shopee", "TikTok", "Amazon"])
def test_preview_amostra_tem_componentes_reais_e_nao_grava_pdfs(store):
    result = gerar_preview_layout(store)
    assert result["success"] is True
    assert result["sample"] is True
    assert set(result["components"]) == {"etiqueta", "sku", "danfe"}
    for name in ("etiqueta", "sku", "danfe"):
        pix = _png(result["components"][name])
        assert pix.width > 0 and pix.height > 0
    preview = _png(result["preview"])
    assert preview.height > preview.width
    assert result["layout"] == validar_layout(result["layout"], store)
    if store == "Amazon":
        assert result.get("warnings") or result.get("message")


@pytest.mark.parametrize("case", ["nan", "outside", "rotation", "unknown", "zero"])
def test_layout_rejeita_geometria_invalida_ou_campos_desconhecidos(case):
    layout = _layout_livre()
    if case == "nan":
        layout["items"]["sku"]["x"] = float("nan")
    elif case == "outside":
        layout["items"]["sku"]["x"] = 50
    elif case == "rotation":
        layout["items"]["sku"]["rotation"] = 45
    elif case == "unknown":
        layout["items"]["sku"]["script"] = "arbitrary input"
    else:
        layout["items"]["etiqueta"]["width"] = 0
    with pytest.raises(ValueError):
        validar_layout(layout, "Mercado Livre")


def test_preview_real_e_composicao_preservam_sku_chave_e_posicoes_sem_duplicar():
    source = _fonte_ml()
    layout = _layout_livre()
    result = processar_etiquetas_loja("Mercado Livre", source, None, None, layout=layout)
    assert result["success"] is True
    assert result["qtd"] == 1
    with fitz.open(stream=result["out_etiquetas"], filetype="pdf") as doc:
        page = doc[0]
        assert page.rect.width == pytest.approx(100 * MM)
        assert page.rect.height == pytest.approx(150 * MM)
        text = page.get_text()
        assert text.count(SKU) == 1
        assert text.replace(" ", "").replace("\n", "").count(CHAVE) == 1
        assert text.count("SOURCE MARKER") == 1
        assert all(rect.y0 >= 114 * MM for rect in page.search_for(SKU))
        assert all(rect.y0 >= 129 * MM for rect in page.search_for(CHAVE))
        assert len(page.get_images()) == 1
        image_rects = page.get_image_rects(page.get_images()[0][0])
        assert image_rects and all(rect.y0 >= 129 * MM for rect in image_rects)
    preview = gerar_preview_layout("Mercado Livre", bytes_etiquetas=source, layout=layout)
    assert preview["success"] is True
    assert preview["sample"] is False
    assert preview["availability"] == {"etiqueta": True, "sku": True, "danfe": True}
    for data_url in preview["components"].values():
        _png(data_url)
    _png(preview["preview"])


def test_mover_etiqueta_altera_coordenadas_do_original_sem_mudar_texto():
    source = _fonte_ml()
    first = _layout_livre()
    second = copy.deepcopy(first)
    second["items"]["etiqueta"]["x"] += 10
    second["items"]["etiqueta"]["y"] += 5
    outputs = [processar_etiquetas_loja("Mercado Livre", source, None, None, layout=x) for x in (first, second)]
    with fitz.open(stream=outputs[0]["out_etiquetas"], filetype="pdf") as a, fitz.open(
        stream=outputs[1]["out_etiquetas"], filetype="pdf"
    ) as b:
        before, after = a[0].search_for("SOURCE MARKER")[0], b[0].search_for("SOURCE MARKER")[0]
        assert after.x0 - before.x0 == pytest.approx(10 * MM, abs=0.01)
        assert after.y0 - before.y0 == pytest.approx(5 * MM, abs=0.01)
        assert a[0].get_text() == b[0].get_text()


def test_sem_layout_mantem_dimensoes_e_posicoes_legadas():
    result = processar_etiquetas_loja("Mercado Livre", _fonte_ml(), None, None)
    with fitz.open(stream=result["out_etiquetas"], filetype="pdf") as doc:
        page = doc[0]
        assert page.rect == fitz.Rect(0, 0, 283, 425)
        assert all(rect.y0 >= 390 for rect in page.search_for(SKU))
        assert "SOURCE MARKER" in page.get_text()


@pytest.fixture
def http_client():
    storage = {}

    def tenant(x_tenant: str = Header("cliente-a")):
        return x_tenant

    app = FastAPI()
    app.include_router(create_etiquetas_router(EtiquetasRouterConfig(tenant, storage)))
    with TestClient(app) as client:
        yield client, storage


def test_http_preview_amostra_e_real_nao_persistem_documentos(http_client):
    client, storage = http_client
    response = client.post("/api/etiquetas/layout/preview", data={"store": "Mercado Livre"})
    assert response.status_code == 200, response.text
    assert response.json()["sample"] is True
    response = client.post(
        "/api/etiquetas/layout/preview",
        data={"store": "Mercado Livre", "layout": json.dumps(_layout_livre())},
        files={"file_etiquetas": ("source.pdf", _fonte_ml(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sample"] is False
    assert storage == {}


def test_http_layout_invalido_retorna_422_sem_criar_arquivo(http_client):
    client, storage = http_client
    invalid = _layout_livre()
    invalid["items"]["sku"]["x"] = 200
    for endpoint in ("layout/preview", "processar"):
        response = client.post(
            f"/api/etiquetas/{endpoint}",
            data={"store": "Mercado Livre", "layout": json.dumps(invalid)},
            files={"file_etiquetas": ("source.pdf", _fonte_ml(), "application/pdf")},
        )
        assert response.status_code == 422, response.text
    assert storage == {}


def test_download_e_preview_de_arquivo_gerado_exigem_tenant_proprietario(http_client):
    client, _ = http_client
    response = client.post(
        "/api/etiquetas/processar",
        data={"store": "Mercado Livre", "layout": json.dumps(_layout_livre())},
        files={"file_etiquetas": ("source.pdf", _fonte_ml(), "application/pdf")},
        headers={"X-Tenant": "cliente-a"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True, payload
    for file_id in (payload["id_etiquetas"], payload["id_lista"]):
        assert file_id
        for operation in ("download", "preview"):
            url = f"/api/etiquetas/{operation}/{file_id}"
            assert client.get(url, headers={"X-Tenant": "cliente-a"}).status_code == 200
            assert client.get(url, headers={"X-Tenant": "cliente-b"}).status_code == 404


def _fonte_embutida(store):
    with fitz.open() as doc:
        page = doc.new_page(width=283, height=425)
        page.insert_text((20, 40), "RECEBEDOR\nREMETENTE", fontsize=10)
        page.insert_text((20, 80), "Pedido 261101ABC123" if store == "Shopee" else "ETIQUETA", fontsize=9)
        page.insert_text((20, 360), "FISCAL MARKER", fontsize=11)
        page.draw_rect(fitz.Rect(20, 370, 230, 395), fill=(0, 0, 0))
        picking = doc.new_page(width=283, height=425)
        if store == "Shopee":
            text = "261101ABC123\nProduto exemplo\n2\nSKU-SHOPEE\nChecklist"
        else:
            text = "PICKING LIST\nOrder ID: 123456789012345\nProduct Name\nSeller SKU\nQty\nProduto exemplo\nSKU-TIKTOK\n2\nQty Total"
        picking.insert_textbox(fitz.Rect(20, 20, 270, 400), text, fontsize=9)
        return doc.tobytes()


@pytest.mark.parametrize("store", ["Shopee", "TikTok"])
def test_danfe_embutido_exige_selecao_explicita_e_preserva_fonte(store):
    source = _fonte_embutida(store)
    preview = gerar_preview_layout(store, bytes_etiquetas=source)
    assert preview["success"] and not preview["sample"]
    assert preview["availability"] == {"etiqueta": True, "sku": True, "danfe": False}
    assert preview["components"]["danfe"] is None
    assert preview["warnings"]

    layout = _layout_livre()
    layout["danfe_source"] = {"x": 0.05, "y": 0.75, "width": 0.85, "height": 0.2}
    preview = gerar_preview_layout(store, bytes_etiquetas=source, layout=layout)
    assert preview["success"] and preview["availability"]["danfe"]
    _png(preview["components"]["danfe"])
    original = _png(preview["source"])
    remainder = _png(preview["components"]["etiqueta"])
    assert original.samples != remainder.samples
    # The explicitly selected black rectangle is removed from the original layer.
    x, y = int(original.width * 0.4), int(original.height * 0.9)
    assert original.pixel(x, y)[:3] == (0, 0, 0)
    assert remainder.pixel(x, y)[3] == 0 or min(remainder.pixel(x, y)[:3]) > 240
    output = processar_etiquetas_loja(store, source, None, None, layout=layout)
    with fitz.open(stream=output["out_etiquetas"], filetype="pdf") as doc:
        assert doc.page_count == 1
        assert doc[0].get_text().count("FISCAL MARKER") == 1
    with fitz.open(stream=source, filetype="pdf") as doc:
        assert "FISCAL MARKER" in doc[0].get_text()


@pytest.mark.parametrize("crop", [
    {"x": float("inf"), "y": 0, "width": 0.2, "height": 0.2},
    {"x": 0.9, "y": 0, "width": 0.2, "height": 0.2},
    {"x": 0, "y": 0, "width": 0, "height": 0.2},
    {"x": 0, "y": 0, "width": 0.2, "height": 0.2, "extra": True},
])
def test_area_danfe_rejeita_coordenadas_invalidas(crop):
    layout = _layout_livre()
    layout["danfe_source"] = crop
    with pytest.raises(ValueError):
        validar_layout(layout, "Shopee")


def test_preview_final_e_pdf_usam_mesma_composicao_com_rotacao_e_proporcao():
    layout = _layout_livre()
    layout["items"]["etiqueta"]["rotation"] = 90
    layout["items"]["danfe"]["rotation"] = 270
    source = _fonte_ml()
    preview = gerar_preview_layout("Mercado Livre", bytes_etiquetas=source, layout=layout)
    output = processar_etiquetas_loja("Mercado Livre", source, None, None, layout=layout)
    with fitz.open(stream=output["out_etiquetas"], filetype="pdf") as doc:
        expected = doc[0].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    actual = _png(preview["preview"])
    assert (actual.width, actual.height, actual.samples) == (expected.width, expected.height, expected.samples)


def test_crop_explicito_ml_substitui_codigo_gerado_e_nao_depende_de_chave_textual():
    with fitz.open() as source:
        page = source.new_page(width=283, height=425)
        page.insert_text((20, 40), "DESTINATARIO\nPEDIDO ABC123456\nSKU 000123-TESTE", fontsize=9)
        page.insert_text((20, 360), "FISCAL SELECIONADO", fontsize=11)
        data = source.tobytes()
    layout = _layout_livre()
    layout["danfe_source"] = {"x": 0.05, "y": 0.75, "width": 0.85, "height": 0.2}
    preview = gerar_preview_layout("Mercado Livre", bytes_etiquetas=data, layout=layout)
    assert preview["success"] and preview["availability"]["danfe"]
    output = processar_etiquetas_loja("Mercado Livre", data, None, None, layout=layout)
    with fitz.open(stream=output["out_etiquetas"], filetype="pdf") as doc:
        text = doc[0].get_text()
        assert text.count("FISCAL SELECIONADO") == 1
        assert "VENDA ABC123456" not in text
        assert all(rect.y0 >= 130 * MM for rect in doc[0].search_for("FISCAL SELECIONADO"))
