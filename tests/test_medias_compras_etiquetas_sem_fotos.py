"""English labels without photos preserve data and avoid image work entirely."""

import copy
import re

import fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import cadastro_compatibilidade
from backend.services import medias_compras_common as common
from backend.services import medias_compras_etiquetas_pdf as layout
from backend.services import medias_compras_excel as excel
from backend.services import medias_compras_packing as packing

from test_medias_compras_etiquetas import _foto, _grupo, _lista, _salvo, _xlsx, tenants


ROTA = "/api/medias-compras/listas-pedidos/lista-1/etiquetas"


def _itens(numero):
    return [{"sku": f"ITEM-{indice:03}", "quantidade": indice + 1,
             "foto": b"invalid image must not be decoded", "descricao": f"Technical item {indice:03}"}
            for indice in range(numero)]


def _pdf(itens, *, modelo="sem_fotos", **overrides):
    kwargs = {
        "cabecalho": "SYNTHETIC SUPPLIER\nContact Name: SYNTHETIC CONTACT",
        "importador": "IMPORTER: SYNTHETIC IMPORTER\nCNP.J. TAX-IMP-001",
        "comprador": "Buyer: SYNTHETIC BUYER\nTAX ID: TAX-BUY-001",
        "origem": "PORTUGAL", "data": "October 26, 2026",
    }
    kwargs.update(overrides)
    return layout.gerar_etiquetas_pdf(
        {"numero_invoice": "SYNTHETIC-2026"}, [{"numero": 1, "itens": itens}],
        modelo=modelo, **kwargs,
    )


def _spans(page):
    return [span for block in page.get_text("dict")["blocks"] if "lines" in block
            for line in block["lines"] for span in line["spans"] if span["text"].strip()]


def _sem_fotos(page):
    assert page.get_images(full=True) == []
    assert page.get_image_info() == []
    assert "Sem foto" not in page.get_text()


def _geometria(page):
    spans = _spans(page)
    for span in spans:
        assert page.rect.contains(fitz.Rect(span["bbox"]))
    for index, first in enumerate(spans):
        first_box = fitz.Rect(first["bbox"])
        for second in spans[index + 1:]:
            intersection = first_box & fitz.Rect(second["bbox"])
            if intersection.is_empty:
                continue
            # Font ascenders/descenders may intersect between adjacent baselines.
            assert not (intersection.width > 1 and intersection.get_area() > 5
                        and intersection.height > min(first["size"], second["size"]) * .35)


def _valor_quantidade(page, esperado):
    texto = page.get_text()
    assert "QUANTITY PER CARTON" in texto
    quantidades = re.findall(r"QUANTITY PER CARTON\s*(\d+(?:[.,]\d+)?)", texto)
    assert quantidades == ([str(esperado)] if esperado is not None else [])
    assert "None" not in texto


def test_sem_fotos_item_unico_preserva_valores_literais_e_centraliza_conteudo():
    item = {"sku": "0001-Ab", "quantidade": 8, "foto": _foto("red"),
            "descricao": "Current registered technical description <A&B>"}
    antes = copy.deepcopy(item)
    with fitz.open(stream=_pdf([item]), filetype="pdf") as documento:
        assert len(documento) == 1
        page = documento[0]
        _sem_fotos(page)
        _geometria(page)
        texto = page.get_text()
        for esperado in ("SYNTHETIC-2026", "October 26, 2026", "SYNTHETIC SUPPLIER",
                         "SYNTHETIC CONTACT", "SYNTHETIC IMPORTER", "SYNTHETIC BUYER",
                         "TAX-IMP-001", "TAX-BUY-001", "PORTUGAL", item["descricao"], "SKU 0001-Ab"):
            assert esperado in texto
        for rotulo in ("CARTON", "SHEET 1 OF 1", "INVOICE", "DATE", "ORIGIN",
                       "CONTENTS", "QUANTITY PER CARTON", "IMPORTER", "BUYER"):
            assert rotulo in texto
        assert not re.search(r"CAIXA|FOLHA|MESMA|CONTE[ÚU]DO|IMPORTADOR|COMPRADOR|Qtd\./caixa", texto)
        _valor_quantidade(page, 8)
        centro = page.rect.width / 2
        for conteudo in ("SKU 0001-Ab", item["descricao"], "QUANTITY PER CARTON"):
            boxes = page.search_for(conteudo)
            assert boxes
            assert all(abs((box.x0 + box.x1) / 2 - centro) < 2 for box in boxes)
        for span in _spans(page):
            if span["text"] in ("SKU 0001-Ab", item["descricao"], "8"):
                assert span["size"] >= 9
    assert item == antes


@pytest.mark.parametrize("quantidade", [None, 0, 10])
def test_sem_fotos_quantidade_desconhecida_permanece_vazia_sem_virar_zero(quantidade):
    item = {"sku": "0001-Ab", "quantidade": quantidade, "foto": b"invalid", "descricao": "Sensor"}
    with fitz.open(stream=_pdf([item]), filetype="pdf") as documento:
        _valor_quantidade(documento[0], quantidade)


@pytest.mark.parametrize("numero", range(1, 19))
def test_sem_fotos_grade_adaptativa_preserva_skus_quantidades_e_limites(numero):
    itens = _itens(numero)
    antes = copy.deepcopy(itens)
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert len(documento) == 1
        page = documento[0]
        assert page.rect.width == pytest.approx(841.89, abs=.1)
        assert page.rect.height == pytest.approx(595.28, abs=.1)
        _sem_fotos(page)
        _geometria(page)
        texto = page.get_text()
        encontrados = re.findall(r"ITEM-\d{3}", "".join(texto.split()))
        assert encontrados == [item["sku"] for item in itens]
        assert re.search(r"\b0*1\s*/\s*0*1\b", texto)
        assert "SHEET 1 OF 1" in texto
        if numero == 1:
            _valor_quantidade(page, 1)
            assert itens[0]["descricao"] in texto
        else:
            numeros = re.findall(r"QUANTITYPERCARTON:?(\d+(?:[.,]\d+)?)", "".join(texto.split()))
            assert numeros == [str(item["quantidade"]) for item in itens]
        if numero > 1:
            rects = [fitz.Rect(drawing["rect"]) for drawing in page.get_drawings()
                     if drawing["rect"].width > 0 and drawing["rect"].height > 0]
            cards = []
            for item in itens:
                containing = [rect for rect in rects
                              if item["sku"] in "".join(page.get_text(clip=rect).split())]
                assert containing
                card = min(containing, key=lambda rect: rect.get_area())
                cards.append(card)
                content = [span for block in page.get_text("dict", clip=card)["blocks"] if "lines" in block
                           for line in block["lines"] for span in line["spans"] if span["text"].strip()]
                assert content
                assert all(span["size"] >= 9 for span in content)
                assert all(card.contains(fitz.Rect(span["bbox"])) for span in content)
            assert len({tuple(card) for card in cards}) == numero
            for index, card in enumerate(cards):
                for other in cards[index + 1:]:
                    assert (card & other).get_area() < .1
    assert itens == antes


def test_sem_fotos_trinta_itens_continuam_mesma_caixa_sem_perder_ordem():
    itens = _itens(30)
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert len(documento) == 2
        for numero, (page, inicio, fim) in enumerate(zip(documento, (0, 18), (18, 30)), 1):
            _sem_fotos(page)
            _geometria(page)
            texto = page.get_text()
            assert f"SHEET {numero} OF 2" in texto
            assert re.search(r"\b0*1\s*/\s*0*1\b", texto)
            encontrados = re.findall(r"ITEM-\d{3}", "".join(texto.split()))
            assert encontrados == [item["sku"] for item in itens[inicio:fim]]


def test_sem_fotos_nao_decodifica_imagens_mesmo_com_bytes_validos_ou_invalidos(monkeypatch):
    monkeypatch.setattr(layout, "_foto_validada",
                        lambda *_args, **_kwargs: pytest.fail("O modelo sem fotos nao deve decodificar imagens."))
    itens = [{"sku": "001", "quantidade": 1, "foto": _foto("red")},
             {"sku": "002", "quantidade": None, "foto": b"invalid image"}]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        _sem_fotos(documento[0])


def test_com_fotos_permanece_padrao_e_preserva_modelo_existente():
    itens = [{"sku": "001", "quantidade": 2, "foto": _foto("red"), "descricao": "Sensor"}]
    kwargs = {"cabecalho": "SYNTHETIC SUPPLIER", "importador": "", "comprador": "",
              "origem": "PORTUGAL", "data": "October 26, 2026"}
    # Omitting modelo must retain the existing public call behavior.
    pdf = layout.gerar_etiquetas_pdf({"numero_invoice": "DEFAULT-2026"},
                                    [{"numero": 1, "itens": itens}], **kwargs)
    with fitz.open(stream=pdf, filetype="pdf") as documento:
        assert len(documento[0].get_image_info()) == 1
        assert "FOLHA 1 DE 1" in documento[0].get_text()
        assert "Qtd./caixa: 2" in documento[0].get_text()


def test_sem_fotos_obtem_descricao_da_loja_sem_resolver_nem_abrir_fotos(tenants, monkeypatch):
    consultas = []

    def contexto(client_id, store_id, **_kwargs):
        consultas.append((client_id, store_id))
        return {"scope": "store", "store_id": store_id, "produtos": [
            {"sku": "001", "store_id": store_id, "foto": "do-not-open.png",
             "cg_product description": f"Current technical description {client_id} {store_id}"}
        ]}

    def proibido(*_args, **_kwargs):
        pytest.fail("O modelo sem fotos nao deve resolver ou ler a foto do Cadastro.")

    monkeypatch.setattr(cadastro_compatibilidade, "visao_produtos_cadastro_contexto_loja", contexto)
    monkeypatch.setattr(common, "_resolver_foto_cadastro_sku", proibido)
    monkeypatch.setattr(excel, "_resolver_caminho_foto_cadastro_seguro", proibido)
    dados = {"grupos": [_grupo(skus=("SKU:001",))]}
    dados["grupos"][0]["itens"][0]["foto"] = _foto("magenta")
    antes = copy.deepcopy(dados)
    for tenant in ("cliente-a", "cliente-b"):
        pdf = packing._gerar_etiquetas_packing_bytes(_lista(), dados, client_id=tenant, modelo="sem_fotos")
        with fitz.open(stream=pdf, filetype="pdf") as documento:
            _sem_fotos(documento[0])
            assert f"Current technical description {tenant} store-a" in documento[0].get_text()
            outro = "cliente-b" if tenant == "cliente-a" else "cliente-a"
            assert outro not in documento[0].get_text()
        assert (tenant, "store-a") in consultas
    assert dados == antes


@pytest.mark.parametrize("consulta,esperado", [("", "com_fotos"), ("?modelo=com_fotos", "com_fotos"),
                                               ("?modelo=sem_fotos", "sem_fotos")])
def test_api_propaga_modelo_validado_e_tenant_da_sessao(monkeypatch, consulta, esperado):
    chamadas = []

    def baixar(lista_id, client_id, *, modelo="com_fotos"):
        chamadas.append((lista_id, client_id, modelo))
        return Response(b"%PDF-synthetic", media_type="application/pdf")

    monkeypatch.setattr(packing, "_baixar_etiquetas_packing_sync", baixar)
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    with TestClient(app) as client:
        resposta = client.get(ROTA + consulta)
    assert resposta.status_code == 200
    assert chamadas == [("lista-1", "cliente-a", esperado)]


@pytest.mark.parametrize("modelo", ["invalid", "SEM_FOTOS", "../sem_fotos", ""])
def test_api_rejeita_modelo_invalido_antes_de_gerar_pdf(monkeypatch, modelo):
    monkeypatch.setattr(packing, "_baixar_etiquetas_packing_sync",
                        lambda *_args, **_kwargs: pytest.fail("Modelo invalido nao deve gerar PDF."))
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    with TestClient(app) as client:
        resposta = client.get(ROTA, params={"modelo": modelo})
    assert resposta.status_code == 422


def test_api_sem_fotos_exige_autenticacao_preserva_tenant_e_original(tenants, monkeypatch):
    original = _xlsx({"grupos": [_grupo(skus=("SKU:001",))]})
    packing._importar_packing_list_sync("lista-1", original, "synthetic.xlsx", "cliente-a")
    arquivo = tenants / "cliente-a" / "listas_pedidos.json"
    antes = arquivo.read_bytes()
    lista_antes = copy.deepcopy(_salvo(tenants))

    def autenticar(_request, authorization):
        tenant = {"Bearer synthetic-a": "cliente-a", "Bearer synthetic-b": "cliente-b"}.get(authorization)
        if not tenant:
            raise HTTPException(401, "Autenticacao necessaria.")
        return tenant

    monkeypatch.setattr(common, "_get_tenant_id_fn", autenticar)
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    with TestClient(app) as client:
        assert client.get(ROTA, params={"modelo": "sem_fotos"}).status_code == 401
        assert client.get(ROTA, params={"modelo": "sem_fotos", "client_id": "cliente-a"},
                          headers={"Authorization": "Bearer synthetic-b"}).status_code == 404
        resposta = client.get(ROTA, params={"modelo": "sem_fotos", "client_id": "cliente-b"},
                              headers={"Authorization": "Bearer synthetic-a"})
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/pdf"
    assert 'attachment;' in resposta.headers["content-disposition"]
    with fitz.open(stream=resposta.content, filetype="pdf") as documento:
        assert len(documento) == 1
        _sem_fotos(documento[0])
        assert "QUANTITY PER CARTON" in documento[0].get_text()
    assert arquivo.read_bytes() == antes
    assert _salvo(tenants) == lista_antes
    assert next((arquivo.parent / "packing_lists").glob("*.xlsx")).read_bytes() == original
    assert "packing_list" not in _salvo(tenants, "cliente-b")
