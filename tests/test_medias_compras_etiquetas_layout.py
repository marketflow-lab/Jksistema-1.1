"""Adaptive product cards in external carton labels, using synthetic images."""

import copy
import io
import math
import re

import fitz
import pytest
from fastapi import HTTPException
from PIL import Image

from backend.services import medias_compras_etiquetas_pdf as layout


def _foto(indice, tamanho=(120, 60)):
    buffer = io.BytesIO()
    cor = ((indice * 47 + 31) % 256, (indice * 61 + 53) % 256, (indice * 37 + 79) % 256)
    Image.new("RGB", tamanho, cor).save(buffer, format="PNG")
    return buffer.getvalue()


def _itens(quantidade):
    return [{"sku": f"ITEM-{indice:03}", "quantidade": indice + 1,
             "foto": _foto(indice), "descricao": f"Peça sintética {indice}"}
            for indice in range(quantidade)]


def _pdf(itens, *, caixas=None, **overrides):
    kwargs = {"cabecalho": "EXPORTADOR SINTETICO", "importador": "IMPORTER SINTETICO",
              "comprador": "BUYER SINTETICO", "origem": "PORTUGAL", "data": "October 23, 2026"}
    kwargs.update(overrides)
    if caixas is None:
        caixas = [{"numero": 1, "itens": itens}]
    return layout.gerar_etiquetas_pdf({"numero_invoice": "SYNTHETIC-2026"}, caixas, **kwargs)


def _spans(pagina):
    return [span for bloco in pagina.get_text("dict")["blocks"] if "lines" in bloco
            for linha in bloco["lines"] for span in linha["spans"] if span["text"].strip()]


def _imagens(pagina):
    return [fitz.Rect(image["bbox"]) for image in pagina.get_image_info()]


def _texto_continuo(pagina):
    return "".join(pagina.get_text().split())


@pytest.mark.parametrize("numero", range(1, 19))
def test_grade_adapta_de_um_a_dezoito_com_fotos_e_todos_skus_legiveis(numero):
    itens = _itens(numero)
    antes = copy.deepcopy(itens)
    colunas, linhas = layout.grade_etiquetas(numero)
    assert linhas == math.ceil(numero / 6)
    assert colunas == math.ceil(numero / linhas)
    assert colunas <= 6 and linhas <= 3
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert len(documento) == 1
        pagina = documento[0]
        assert pagina.rect.width == pytest.approx(841.89, abs=.1)
        assert pagina.rect.height == pytest.approx(595.28, abs=.1)
        imagens = _imagens(pagina)
        assert len(imagens) == numero
        assert len({round(rect.y0, 2) for rect in imagens}) == linhas
        assert max(sum(abs(outro.y0 - rect.y0) < .1 for outro in imagens) for rect in imagens) == colunas
        for indice, imagem in enumerate(imagens):
            assert pagina.rect.contains(imagem)
            assert imagem.width / imagem.height == pytest.approx(2, rel=.01)
            for outra in imagens[indice + 1:]:
                assert (imagem & outra).get_area() < .1
        texto = _texto_continuo(pagina)
        for item in itens:
            assert item["sku"] in texto
        spans = _spans(pagina)
        for span in spans:
            assert pagina.rect.contains(fitz.Rect(span["bbox"]))
        skus = [span for span in spans if "ITEM-" in span["text"]]
        assert len(skus) == numero
        assert all(span["size"] >= 9 for span in skus)
    assert itens == antes


def test_produto_unico_recebe_foto_maior_que_grade_cheia():
    areas = []
    for numero in (1, 18):
        with fitz.open(stream=_pdf(_itens(numero)), filetype="pdf") as documento:
            areas.append(_imagens(documento[0])[0].get_area())
    assert areas[0] > areas[1] * 3


@pytest.mark.parametrize("numero,por_pagina", [(19, [18, 1]), (30, [18, 12]),
                                               (36, [18, 18]), (37, [18, 18, 1])])
def test_paginas_continuam_mesmo_volume_em_blocos_de_dezoito_e_preservam_ordem(numero, por_pagina):
    itens = _itens(numero)
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert len(documento) == len(por_pagina)
        inicio = 0
        for folha, (pagina, total) in enumerate(zip(documento, por_pagina), 1):
            assert len(_imagens(pagina)) == total
            texto = pagina.get_text()
            assert re.search(r"\b0*1\s*/\s*0*1\b", texto)
            assert f"FOLHA {folha} DE {len(por_pagina)}" in texto
            encontrados = re.findall(r"ITEM-\d{3}", texto)
            assert encontrados == [item["sku"] for item in itens[inicio:inicio + total]]
            inicio += total


def test_volume_seguinte_nao_mistura_produtos_com_continuacao_do_anterior():
    caixas = [{"numero": 1, "itens": _itens(19)},
              {"numero": 2, "itens": [{"sku": "OUTRA-CAIXA", "quantidade": 1, "foto": _foto(70)}]}]
    with fitz.open(stream=_pdf([], caixas=caixas), filetype="pdf") as documento:
        assert len(documento) == 3
        assert "ITEM-000" in documento[0].get_text()
        assert "ITEM-018" in documento[1].get_text()
        assert "OUTRA-CAIXA" not in documento[0].get_text() + documento[1].get_text()
        assert "OUTRA-CAIXA" in documento[2].get_text()
        assert re.search(r"\b0*2\s*/\s*0*2\b", documento[2].get_text())


@pytest.mark.parametrize("foto", [None, b"", b"synthetic not an image", "https://example.invalid/photo.png"])
def test_foto_ausente_ou_invalida_tem_espaco_reservado_sem_erro(foto):
    itens = [{"sku": "0001-Ab", "quantidade": None, "foto": foto, "descricao": "Peça"}]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        pagina = documento[0]
        assert "Sem foto" in pagina.get_text()
        assert "SKU 0001-Ab" in pagina.get_text()
        assert "Qtd./caixa:" in pagina.get_text()
        assert _imagens(pagina) == []


@pytest.mark.parametrize("tamanho", [(200, 40), (40, 200), (100, 100)])
def test_foto_preserva_proporcao_em_grade_dezoito(tamanho):
    itens = _itens(18)
    itens[0]["foto"] = _foto(90, tamanho)
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        imagens = _imagens(documento[0])
        assert len(imagens) == 18
        assert imagens[0].width / imagens[0].height == pytest.approx(tamanho[0] / tamanho[1], rel=.01)


def test_quantidade_desconhecida_fica_vazia_e_zero_permanece_visivel():
    itens = [{"sku": "SEM-QTD", "quantidade": None, "foto": None},
             {"sku": "QTD-ZERO", "quantidade": 0, "foto": None},
             {"sku": "QTD-DOIS", "quantidade": 2, "foto": None}]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        texto = documento[0].get_text()
        assert re.search(r"Qtd\./caixa:\s*0\b", texto)
        assert re.search(r"Qtd\./caixa:\s*2\b", texto)
        assert "None" not in texto


def test_skus_longos_zeros_e_case_nao_sofrem_cortes_ou_elipses():
    skus = ["0000001-Aa", "0000001-aa", "ABC<teste>&(1)-" + "LONGO-" * 12]
    itens = [{"sku": sku, "quantidade": 1, "foto": _foto(i)} for i, sku in enumerate(skus)]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        pagina = documento[0]
        texto = _texto_continuo(pagina)
        for sku in skus:
            assert sku in texto
        assert "..." not in texto and "…" not in texto
        for span in _spans(pagina):
            assert pagina.rect.contains(fitz.Rect(span["bbox"]))


def test_sku_impossivel_de_caber_retorna_erro_em_vez_de_truncar():
    itens = [{"sku": "SKU_EXTENSO_" * 2000, "quantidade": 1, "foto": None}]
    with pytest.raises(HTTPException) as erro:
        _pdf(itens)
    assert erro.value.status_code == 400


@pytest.mark.parametrize("numero", [0, 19, True, 2.5, None])
def test_grade_rejeita_quantidade_fora_do_contrato(numero):
    with pytest.raises(HTTPException) as erro:
        layout.grade_etiquetas(numero)
    assert erro.value.status_code == 400


def test_foto_maior_que_limite_de_bytes_nao_e_decodificada(monkeypatch):
    def inesperado(*_args, **_kwargs):
        pytest.fail("A imagem acima do limite deve ser rejeitada antes de decodificar.")

    monkeypatch.setattr(layout.Image, "open", inesperado)
    itens = [{"sku": "LIMITE-BYTES", "quantidade": 1, "foto": b"0" * (layout.MAX_FOTO_BYTES + 1)}]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert "Sem foto" in documento[0].get_text()
        assert _imagens(documento[0]) == []


def test_foto_maior_que_limite_de_pixels_nao_e_carregada(monkeypatch):
    class ImagemGrande:
        width = 5001
        height = 5000

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(layout.Image, "open", lambda *_args, **_kwargs: ImagemGrande())
    monkeypatch.setattr(layout.ImageOps, "exif_transpose",
                        lambda *_args, **_kwargs: pytest.fail("A imagem acima do limite nao deve ser carregada."))
    itens = [{"sku": "LIMITE-PIXELS", "quantidade": 1, "foto": b"synthetic image metadata"}]
    with fitz.open(stream=_pdf(itens), filetype="pdf") as documento:
        assert "Sem foto" in documento[0].get_text()
        assert _imagens(documento[0]) == []


def test_limite_soma_folhas_reais_das_caixas_mistas_antes_de_abrir_pdf(monkeypatch):
    monkeypatch.setattr(layout, "MAX_FOLHAS", 3)
    monkeypatch.setattr(layout, "Canvas",
                        lambda *_args, **_kwargs: pytest.fail("O limite deve ser validado antes de criar o PDF."))
    itens = [{"sku": f"ITEM-{indice:03}", "quantidade": 1, "foto": None} for indice in range(19)]
    caixas = [{"numero": 1, "itens": itens}, {"numero": 2, "itens": itens}]
    with pytest.raises(HTTPException) as erro:
        _pdf([], caixas=caixas)
    assert erro.value.status_code == 400


def test_unicode_cirilico_permanece_literal_em_sku_e_campos_normal_e_negrito():
    if not all(getattr(layout.getFont(fonte).face, "charToGlyph", {}).get(ord("Ж"))
               for fonte in (layout.NORMAL, layout.NEGRITO)):
        pytest.skip("O ambiente precisa de fonte TrueType com caracteres cirílicos para este caso positivo.")
    itens = [{"sku": "AB-Ж-01", "quantidade": 1, "foto": None}]
    pdf = _pdf(itens, cabecalho="SUPPLIER ООО\nContact Name: НОРМАЛЬНЫЙ Ж",
               importador="IMPORTER: ИМПОРТЕР ООО\nADDRESS: УЛИЦА Ж",
               comprador="Buyer: ПОКУПАТЕЛЬ ООО\nAddress: ГОРОД Ж")
    with fitz.open(stream=pdf, filetype="pdf") as documento:
        texto = documento[0].get_text()
        for esperado in ("AB-Ж-01", "SUPPLIER ООО", "НОРМАЛЬНЫЙ Ж", "ИМПОРТЕР ООО",
                         "УЛИЦА Ж", "ПОКУПАТЕЛЬ ООО", "ГОРОД Ж"):
            assert esperado in texto
        assert "■" not in texto and "�" not in texto


@pytest.mark.parametrize("sku,cabecalho", [
    ("AB-Ж-01", "SUPPLIER"),
    ("AB-01", "SUPPLIER ООО"),
    ("AB-01", "SUPPLIER\nContact Name: НОРМАЛЬНЫЙ Ж"),
])
def test_unicode_sem_suporte_na_fonte_fallback_retorna_erro_sem_substituir(monkeypatch, sku, cabecalho):
    monkeypatch.setattr(layout, "NORMAL", "Helvetica")
    monkeypatch.setattr(layout, "NEGRITO", "Helvetica-Bold")
    with pytest.raises(HTTPException) as erro:
        _pdf([{"sku": sku, "quantidade": 1, "foto": None}], cabecalho=cabecalho)
    assert erro.value.status_code == 400


def test_glifo_ausente_na_fonte_truetype_retorna_erro_sem_substituir():
    if not all(getattr(layout.getFont(fonte).face, "charToGlyph", None) is not None
               for fonte in (layout.NORMAL, layout.NEGRITO)):
        pytest.skip("O ambiente precisa de fonte TrueType para verificar o mapa de glifos.")
    ausente = next(chr(codigo) for codigo in range(0x1F680, 0x1F700)
                   if not layout.getFont(layout.NEGRITO).face.charToGlyph.get(codigo, 0))
    with pytest.raises(HTTPException) as erro:
        _pdf([{"sku": "AB-" + ausente + "-01", "quantidade": 1, "foto": None}])
    assert erro.value.status_code == 400
