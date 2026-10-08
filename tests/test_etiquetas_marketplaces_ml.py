from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_marketplaces_common import gerar_imagem_barcode

from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


CHAVE_ACESSO = "12345678901234567890123456789012345678901234"


def _pdf_mercado_livre(*, incluir_chave: bool, sku: str = "TESTE-ML") -> bytes:
    documento = fitz.open()
    etiqueta = documento.new_page(width=283, height=390)
    etiqueta.insert_textbox(
        fitz.Rect(20, 20, 263, 180),
        f"DESTINATARIO\nPEDIDO ABC123456\nSKU {sku}\nQTD 1",
        fontsize=4 if len(sku) > 30 else 7,
    )
    etiqueta.insert_text(fitz.Point(240, 380), "BORDA", fontsize=6)
    etiqueta.draw_rect(fitz.Rect(18, 8, 256, 382), width=0.5)

    if incluir_chave:
        declaracao = documento.new_page(width=283, height=390)
        chave_formatada = " ".join(
            CHAVE_ACESSO[indice : indice + 4]
            for indice in range(0, len(CHAVE_ACESSO), 4)
        )
        declaracao.insert_textbox(
            fitz.Rect(20, 20, 263, 100),
            f"CHAVE DE ACESSO\n{chave_formatada}",
            fontsize=7,
        )

    conteudo = documento.tobytes()
    documento.close()
    return conteudo


def _texto_pdf(conteudo: bytes) -> str:
    documento = fitz.open(stream=conteudo, filetype="pdf")
    try:
        return "\n".join(pagina.get_text() for pagina in documento)
    finally:
        documento.close()


def test_processa_etiqueta_ml_com_chave_de_acesso() -> None:
    resultado = processar_etiquetas_loja(
        "Mercado Livre",
        _pdf_mercado_livre(incluir_chave=True),
        None,
        None,
    )

    etiquetas = resultado["out_etiquetas"]
    assert resultado["success"] is True
    assert resultado["qtd"] == 1
    assert etiquetas is not None and etiquetas.startswith(b"%PDF")
    assert resultado["out_lista"] is None
    assert CHAVE_ACESSO in _texto_pdf(etiquetas).replace(" ", "").replace("\n", "")


def test_processa_etiqueta_ml_com_codigo_da_venda_sem_chave() -> None:
    resultado = processar_etiquetas_loja(
        "Mercado Livre",
        _pdf_mercado_livre(incluir_chave=False),
        None,
        None,
    )

    etiquetas = resultado["out_etiquetas"]
    assert resultado["success"] is True
    assert resultado["qtd"] == 1
    assert etiquetas is not None and etiquetas.startswith(b"%PDF")
    assert resultado["out_lista"] is None
    assert "VENDA ABC123456" in _texto_pdf(etiquetas)


def _linhas_pdf(pagina):
    return [
        linha
        for bloco in pagina.get_text("dict")["blocks"]
        if "lines" in bloco
        for linha in bloco["lines"]
    ]


@pytest.mark.parametrize("incluir_chave", [True, False])
def test_sku_no_rodape_e_codigo_lateral_preservam_conteudo(incluir_chave) -> None:
    fonte = _pdf_mercado_livre(incluir_chave=incluir_chave)
    resultado = processar_etiquetas_loja("Mercado Livre", fonte, None, None)
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as documento:
        pagina = documento[0]
        assert pagina.rect == fitz.Rect(0, 0, 283, 425)
        linhas = _linhas_pdf(pagina)
        sku = next(linha for linha in linhas if any(
            span["text"] == "SKU TESTE-ML  x  1" for span in linha["spans"]
        ))
        assert sku["dir"] == (1, 0)
        assert fitz.Rect(sku["bbox"]).y0 >= 390

        texto_codigo = CHAVE_ACESSO if incluir_chave else "VENDA ABC123456"
        codigo = next(linha for linha in linhas if any(
            span["text"] == texto_codigo for span in linha["spans"]
        ))
        assert codigo["dir"] == (0, -1)
        assert fitz.Rect(codigo["bbox"]).x0 >= 256
        assert fitz.Rect(codigo["bbox"]).y1 < 390

        # O conteudo junto a borda direita da fonte deve continuar completo.
        borda = next(span for linha in linhas for span in linha["spans"] if span["text"] == "BORDA")
        with fitz.open(stream=fonte, filetype="pdf") as original:
            linhas_originais = _linhas_pdf(original[0])
            borda_original = next(span for linha in linhas_originais for span in linha["spans"] if span["text"] == "BORDA")
            assert borda["bbox"] == pytest.approx(borda_original["bbox"])
            for linha in linhas_originais:
                for span in linha["spans"]:
                    preservado = next(s for atual in linhas for s in atual["spans"] if s["text"] == span["text"])
                    assert preservado["bbox"] == pytest.approx(span["bbox"])
                    assert preservado["size"] == pytest.approx(span["size"])
            # Compara os pixels da etiqueta original na mesma escala, sem a margem livre.
            # Exclui apenas o traco separador ja existente no rodape (y=390).
            area_original = fitz.Rect(0, 0, 256, 389)
            antes = original[0].get_pixmap(matrix=fitz.Matrix(2, 2), clip=area_original)
            depois = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), clip=area_original)
            assert depois.samples == antes.samples
        for linha in linhas:
            assert pagina.rect.contains(fitz.Rect(linha["bbox"]))

        imagens = pagina.get_images()
        assert len(imagens) == 1
        xref = imagens[0][0]
        retangulo = pagina.get_image_rects(xref)[0]
        assert retangulo.x0 >= 256 and retangulo.x1 <= 283
        assert retangulo.x0 >= fitz.Rect(codigo["bbox"]).x1
        assert retangulo.width > 17
        assert retangulo.y0 >= 15 and retangulo.y1 <= 390
        assert retangulo.height >= 360
        assert retangulo.height > retangulo.width
        original = fitz.Pixmap(gerar_imagem_barcode(CHAVE_ACESSO if incluir_chave else "ABC123456"))
        comprimento_anterior = min(283, original.width * 22 / original.height)
        assert retangulo.height >= comprimento_anterior
        embutida = fitz.Pixmap(documento, xref)
        assert (embutida.width, embutida.height, embutida.samples) == (
            original.width, original.height, original.samples,
        )


def test_sku_longo_permanece_completo_no_rodape() -> None:
    sku_longo = "W" * 60
    resultado = processar_etiquetas_loja(
        "Mercado Livre", _pdf_mercado_livre(incluir_chave=True, sku=sku_longo), None, None,
    )
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as documento:
        pagina = documento[0]
        linhas = [linha for linha in _linhas_pdf(pagina) if fitz.Rect(linha["bbox"]).y0 >= 390]
        texto = "".join(span["text"] for linha in linhas for span in linha["spans"])
        assert texto.replace(" ", "") == f"SKU{sku_longo}x1"
        for linha in linhas:
            assert linha["dir"] == (1, 0)
            assert pagina.rect.contains(fitz.Rect(linha["bbox"]))


def test_etiqueta_sem_sku_ou_codigo_mantem_avisos() -> None:
    with fitz.open() as fonte:
        fonte.new_page(width=283, height=390).insert_text(fitz.Point(20, 30), "12")
        resultado = processar_etiquetas_loja("Mercado Livre", fonte.tobytes(), None, None)
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as documento:
        pagina = documento[0]
        assert "SKU N/D" in pagina.get_text()
        assert "CODIGO NAO ENCONTRADO" in pagina.get_text()
        for linha in _linhas_pdf(pagina):
            assert pagina.rect.contains(fitz.Rect(linha["bbox"]))


def test_contador_de_mil_etiquetas_cabe_na_margem() -> None:
    with fitz.open() as fonte:
        for _ in range(1000):
            fonte.new_page(width=283, height=390).insert_text(fitz.Point(20, 30), "12")
        resultado = processar_etiquetas_loja("Mercado Livre", fonte.tobytes(), None, None)
    assert resultado["qtd"] == 1000
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as documento:
        ultima = documento[-1]
        contador = next(span for linha in _linhas_pdf(ultima) for span in linha["spans"] if span["text"] == "1000")
        assert ultima.rect.contains(fitz.Rect(contador["bbox"]))
        assert fitz.Rect(contador["bbox"]).x0 >= 256
