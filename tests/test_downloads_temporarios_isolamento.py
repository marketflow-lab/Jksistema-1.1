"""Regressões HTTP para a leitura de arquivos temporários por módulo e cliente."""

import io
import json

import fitz
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.routers.etiquetas import EtiquetasRouterConfig, create_etiquetas_router
from backend.routers.medias_compras import create_medias_compras_router
from backend.services import cadastro_compatibilidade
from backend.services import medias_compras_common as common
from backend.services import medias_compras_excel as excel
from backend.services import medias_compras_listas as listas


CLIENTE_A = {"Authorization": "Bearer token-sintetico-a"}
CLIENTE_B = {"Authorization": "Bearer token-sintetico-b"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MEDIAS_DOWNLOAD = "/api/medias-compras/download"
ETIQUETAS_DOWNLOAD = "/api/etiquetas/download"
ETIQUETAS_PREVIEW = "/api/etiquetas/preview"


@pytest.fixture
def http_downloads(monkeypatch):
    def tenant(authorization: str = Header(default="")):
        cliente = {
            CLIENTE_A["Authorization"]: "cliente-a",
            CLIENTE_B["Authorization"]: "cliente-b",
        }.get(authorization)
        if cliente is None:
            raise HTTPException(status_code=401, detail="Sessão sintética inválida")
        return cliente

    # Simula uma configuração acidental que volta a compartilhar os bytes.
    storage = {}
    meta_medias = {}
    monkeypatch.setattr(listas, "TEMP_FILES_STORAGE", storage)
    monkeypatch.setattr(listas, "TEMP_FILES_META", meta_medias)
    monkeypatch.setattr(listas, "LISTA_PEDIDO_XLSX_CACHE", {})

    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.include_router(create_etiquetas_router(EtiquetasRouterConfig(tenant, storage)))
    app.dependency_overrides[common.get_tenant_id] = tenant
    with TestClient(app) as client:
        yield client, storage, meta_medias


def _pdf_origem_ml():
    with fitz.open() as doc:
        etiqueta = doc.new_page(width=283, height=390)
        etiqueta.insert_text((20, 30), "DESTINATARIO", fontsize=9)
        etiqueta.insert_text((20, 55), "PEDIDO ABC123456", fontsize=9)
        etiqueta.draw_rect(fitz.Rect(10, 10, 250, 380), width=0.4)
        declaracao = doc.new_page(width=283, height=390)
        declaracao.insert_text((20, 30), "CHAVE DE ACESSO", fontsize=7)
        declaracao.insert_text((20, 55), "1234567890" * 4 + "1234", fontsize=5)
        manifesto = doc.new_page(width=283, height=390)
        manifesto.insert_textbox(
            fitz.Rect(20, 20, 260, 200),
            "MANIFESTO\nPEDIDO ABC123456\nSKU TESTE-001\nQTD 2",
            fontsize=9,
        )
        return doc.tobytes()


def _indisponivel(client, rota, file_id, headers):
    resposta = client.get(f"{rota}/{file_id}", headers=headers)
    assert resposta.status_code == 404
    assert resposta.json() == client.get(f"{rota}/inexistente", headers=headers).json()


def test_pdf_de_etiquetas_isola_titular_e_modulo(http_downloads):
    client, storage, _ = http_downloads
    processamento = client.post(
        "/api/etiquetas/processar",
        data={"store": "Mercado Livre"},
        files={"file_etiquetas": ("origem.pdf", _pdf_origem_ml(), "application/pdf")},
        headers=CLIENTE_A,
    )
    assert processamento.status_code == 200
    resultado = processamento.json()
    assert resultado["success"] is True, resultado

    for chave in ("id_etiquetas", "id_lista"):
        file_id = resultado[chave]
        assert file_id
        download = client.get(f"{ETIQUETAS_DOWNLOAD}/{file_id}", headers=CLIENTE_A)
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["content-length"] == str(len(download.content))
        with fitz.open(stream=download.content, filetype="pdf") as pdf:
            assert pdf.page_count > 0

        preview = client.get(f"{ETIQUETAS_PREVIEW}/{file_id}", headers=CLIENTE_A)
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert preview.headers["cache-control"] == "no-store"
        assert preview.content.startswith(b"\x89PNG\r\n\x1a\n")

        for rota in (ETIQUETAS_DOWNLOAD, ETIQUETAS_PREVIEW, MEDIAS_DOWNLOAD):
            _indisponivel(client, rota, file_id, CLIENTE_B)
        _indisponivel(client, MEDIAS_DOWNLOAD, file_id, CLIENTE_A)

    storage["pdf-sem-titular"] = download.content
    for rota in (ETIQUETAS_DOWNLOAD, ETIQUETAS_PREVIEW, MEDIAS_DOWNLOAD):
        _indisponivel(client, rota, "pdf-sem-titular", CLIENTE_A)


def test_lista_medias_isola_xlsx_temporario_e_atalho_salvo(http_downloads, tmp_path, monkeypatch):
    client, storage, meta_medias = http_downloads
    lista_id = "lista-cliente-a"
    lista = {
        "id": lista_id,
        "nome_lista": "Pedido teste",
        "updated_at": "2026-01-01T00:00:00",
        "itens": [{"SKU": "TESTE-001", "Quantidade": 7}],
    }

    def tenant_path(cliente):
        assert cliente in ("cliente-a", "cliente-b")
        return str(tmp_path / cliente)

    for cliente, pedidos in (("cliente-a", [lista]), ("cliente-b", [])):
        pasta = tmp_path / cliente
        pasta.mkdir()
        (pasta / "listas_pedidos.json").write_text(json.dumps(pedidos), encoding="utf-8")

    monkeypatch.setattr(common, "get_tenant_path", tenant_path)
    monkeypatch.setattr(
        cadastro_compatibilidade,
        "visao_produtos_cadastro_contexto_loja",
        lambda *_args, **_kwargs: {"scope": "unavailable"},
    )
    monkeypatch.setattr(excel, "_resolver_caminho_foto_cadastro_seguro", lambda *_args: None)

    base = f"/api/medias-compras/listas-pedidos/{lista_id}"
    geracao = client.post(f"{base}/gerar-download", headers=CLIENTE_A)
    assert geracao.status_code == 200
    resultado = geracao.json()
    assert resultado["success"] is True
    assert resultado["lista_id"] == lista_id
    file_id = resultado["file_id"]
    download = client.get(f"{MEDIAS_DOWNLOAD}/{file_id}", headers=CLIENTE_A)
    assert download.status_code == 200
    assert download.headers["content-type"] == XLSX
    assert download.headers["content-disposition"] == f"attachment; filename={resultado['filename']}"
    assert download.headers["cache-control"] == "no-store"
    planilha = load_workbook(io.BytesIO(download.content), read_only=True)
    try:
        assert planilha.active["A2"].value == "TESTE-001"
        assert planilha.active["G2"].value == 7
    finally:
        planilha.close()

    _indisponivel(client, MEDIAS_DOWNLOAD, file_id, CLIENTE_B)
    for rota in (ETIQUETAS_DOWNLOAD, ETIQUETAS_PREVIEW):
        _indisponivel(client, rota, file_id, CLIENTE_A)
    assert client.get(f"{MEDIAS_DOWNLOAD}/{file_id}").status_code == 401

    storage["xlsx-sem-titular"] = download.content
    meta_medias["xlsx-sem-titular"] = {"mime": XLSX, "filename": "legado.xlsx"}
    _indisponivel(client, MEDIAS_DOWNLOAD, "xlsx-sem-titular", CLIENTE_A)

    cache = client.post(f"{base}/gerar-download", headers=CLIENTE_A)
    assert cache.status_code == 200
    atalho = cache.json()["download_url"]
    assert atalho == f"{base}/download"
    assert "file_id" not in cache.json()
    salvo = client.get(atalho, headers=CLIENTE_A)
    assert salvo.status_code == 200
    assert salvo.headers["cache-control"] == "no-store"
    assert salvo.content == download.content
    assert client.get(atalho, headers=CLIENTE_B).status_code == 404
