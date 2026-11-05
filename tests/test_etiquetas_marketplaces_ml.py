from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_layout import layout_padrao
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



@pytest.mark.parametrize("personalizado", [False, True])
def test_ordenacao_preserva_pedido_sku_e_chave_de_cada_etiqueta(personalizado) -> None:
    pedidos = [
        ("ABC100001", "10-A", 1),
        ("ABC100002", "20-B", 3),
        ("ABC100003", "10-A", 5),
        ("ABC100004", "2-C", 3),
        ("ABC100005", "100-D", 1),
        ("ABC100006", "100-D", 3),
    ]
    chaves = [str(indice) * 44 for indice in range(1, len(pedidos) + 1)]
    with fitz.open() as fonte:
        for indice, ((pedido, sku, qtd), chave) in enumerate(zip(pedidos, chaves)):
            etiqueta = fonte.new_page(width=283, height=390)
            etiqueta.insert_text((20, 30), f"DESTINATARIO\nPEDIDO {pedido}\nCLIENTE {indice}", fontsize=8)
            if indice == 1:
                etiqueta.insert_text((20, 80), f"SKU {sku}\nQTD {qtd}", fontsize=8)
            danfe = fonte.new_page(width=283, height=390)
            danfe.insert_text((20, 30), f"DANFE\nPEDIDO {pedido}\nCHAVE DE ACESSO\n{chave}", fontsize=7)

        # A lista vem em outra sequência e um registro diverge do SKU na etiqueta.
        ordem_manifesto = [3, 2, 5, 1, 0]
        manifesto = fonte.new_page(width=283, height=390)
        linhas = ["MANIFESTO"]
        for indice in ordem_manifesto:
            pedido, sku, qtd = pedidos[indice]
            if indice == 1:
                sku, qtd = "40-Z", 9
            linhas.append(f"PEDIDO {pedido}")
            if indice == 2:
                linhas.append("PACK ID 200000000000")
            linhas.extend((sku if indice == 5 else f"SKU {sku}", f"QTD {qtd}"))
        manifesto.insert_text((20, 30), "\n".join(linhas), fontsize=8)
        manifesto_inverso = fonte.new_page(width=283, height=390)
        pedido, sku, qtd = pedidos[4]
        linhas_inversas = ["MANIFESTO", f"SKU {sku}", f"QTD {qtd}", f"PEDIDO {pedido}"]
        manifesto_inverso.insert_text((20, 30), "\n".join(linhas_inversas), fontsize=8)
        resultado = processar_etiquetas_loja(
            "Mercado Livre", fonte.tobytes(), None, None,
            layout=layout_padrao("Mercado Livre") if personalizado else None,
        )

    assert resultado["qtd"] == len(pedidos)
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas:
        assert etiquetas.page_count == len(pedidos)
        for ordem, indice in enumerate((2, 0, 5, 4, 3, 1), start=1):
            pedido, sku, qtd = pedidos[indice]
            pagina = etiquetas[ordem - 1]
            texto = pagina.get_text()
            assert f"PEDIDO {pedido}" in texto
            assert f"CLIENTE {indice}" in texto
            assert f"SKU {sku}  x  {qtd}" in texto
            assert [chave for chave in chaves if chave in texto.replace(" ", "").replace("\n", "")] == [chaves[indice]]
            assert str(ordem) in pagina.get_text(clip=fitz.Rect(252, 0, 283, 20)).splitlines()
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as lista:
        assert [pagina.get_text().splitlines() for pagina in lista] == [linhas_inversas, linhas]



@pytest.mark.parametrize(
    ("titulo_identificacao", "offset_produtos", "tem_titulo"),
    [
        pytest.param("Identificação", 0, True, id="cabecalho-original"),
        pytest.param("Identifiicação", 0, False, id="texto-extraido-sem-titulo"),
        pytest.param("IDENTIFICAÇÃO", 0, True, id="caixa-alta"),
        pytest.param("Identificação", 6, True, id="alinhamento-vertical"),
    ],
)
def test_manifesto_tabular_segue_etiquetas_e_preserva_a_linha_completa(
    titulo_identificacao: str, offset_produtos: int, tem_titulo: bool,
) -> None:
    pedidos = [
        ("ABC200001", "200000000001", "20-B", 3, 68),
        ("ABC200002", "200000000002", "10-A", 1, 91),
        ("ABC200003", "200000000003", "2-C", 2, 75),
        ("ABC200004", "200000000004", "10-A", 5, 103),
    ]
    with fitz.open() as fonte:
        for indice, (venda, pack, *_resto) in enumerate(pedidos):
            pagina = fonte.new_page(width=283, height=390)
            if indice == 1:
                identificacao = f"MEL{indice:06}"
            elif indice == 3:
                identificacao = f"PACK ID: {pack}"
            else:
                identificacao = f"Venda: {venda}"
            pagina.insert_text((20, 30), f"DESTINATARIO\n{identificacao}\nORIGINAL {indice}", fontsize=9)

        for indices in ((0, 1), (2, 3)):
            pagina = fonte.new_page(width=420, height=320)
            if tem_titulo:
                pagina.insert_text((18, 25), "MANIFESTO", fontsize=14)
            pagina.draw_rect(fitz.Rect(18, 44, 402, 68), color=None, fill=(0.83, 0.83, 0.83))
            pagina.insert_text((24, 60), titulo_identificacao, fontsize=10)
            pagina.insert_text((163, 60 + offset_produtos), "Produtos", fontsize=10)
            topo = 68
            for indice in indices:
                venda, pack, sku, qtd, altura = pedidos[indice]
                pagina.insert_text((24, topo + 13), f"MEL{indice:06}", fontsize=9)
                pagina.insert_text((24, topo + 25), f"Pack ID: {pack}", fontsize=9)
                pagina.insert_text((24, topo + 37), f"Venda: {venda}", fontsize=9)
                pagina.insert_text((24, topo + 49), f"COMPRADOR {indice}", fontsize=9)
                pagina.draw_rect(fitz.Rect(154, topo + 6, 160, topo + 12), color=(0, 0, 0))
                pagina.insert_text((163, topo + 13), f"PRODUTO TESTE {indice}", fontsize=9)
                pagina.insert_text((163, topo + 25), f"SKU: {sku}", fontsize=9)
                pagina.insert_text((163, topo + 37), f"Quantidade: {qtd}", fontsize=9)
                pagina.insert_text((163, topo + 49), f"ATRIBUTO TESTE {indice}", fontsize=9)
                topo += altura
                if indice != 3:
                    pagina.draw_line((18, topo), (154, topo), width=0.5)
                    pagina.draw_line((155, topo), (402, topo), width=0.5)
            pagina.insert_text((24, 300), "RODAPE ORIGINAL", fontsize=8)

        resultado = processar_etiquetas_loja("Mercado Livre", fonte.tobytes(), None, None)

    ordem_esperada = [3, 1, 0, 2]
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas:
        assert len(etiquetas) == len(pedidos)
        for pagina, indice in zip(etiquetas, ordem_esperada):
            assert f"ORIGINAL {indice}" in pagina.get_text()
            _, _, sku, qtd, _ = pedidos[indice]
            assert f"SKU {sku}  x  {qtd}" in pagina.get_text()

    ordem_manifesto = []
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        for pagina in manifesto:
            assert pagina.search_for(titulo_identificacao) and pagina.search_for("Produtos")
            assert pagina.search_for("RODAPE ORIGINAL")
            pedidos_na_pagina = []
            for indice, (venda, pack, sku, qtd, _) in enumerate(pedidos):
                vendas = pagina.search_for(f"Venda: {venda}")
                if not vendas:
                    continue
                assert len(vendas) == 1
                linha = vendas[0].y0
                assert any(abs(rect.y0 - (linha - 24)) < 0.1 for rect in pagina.search_for(f"MEL{indice:06}"))
                assert any(abs(rect.y0 - (linha - 24)) < 0.1 for rect in pagina.search_for(f"PRODUTO TESTE {indice}"))
                assert any(abs(rect.y0 - (linha - 12)) < 0.1 for rect in pagina.search_for(f"Pack ID: {pack}"))
                assert any(abs(rect.y0 - (linha - 12)) < 0.1 for rect in pagina.search_for(f"SKU: {sku}"))
                assert any(abs(rect.y0 - linha) < 0.1 for rect in pagina.search_for(f"Quantidade: {qtd}"))
                assert any(abs(rect.y0 - (linha + 12)) < 0.1 for rect in pagina.search_for(f"COMPRADOR {indice}"))
                assert any(abs(rect.y0 - (linha + 12)) < 0.1 for rect in pagina.search_for(f"ATRIBUTO TESTE {indice}"))
                pedidos_na_pagina.append((linha, indice))
            ordem_manifesto.extend(indice for _, indice in sorted(pedidos_na_pagina))
    assert ordem_manifesto == ordem_esperada



def test_manifesto_tabular_sem_divisoria_segura_preserva_pagina_original() -> None:
    with fitz.open() as fonte:
        for venda, sku, qtd in (("ABC300001", "10-A", 1), ("ABC300002", "20-B", 4)):
            pagina = fonte.new_page(width=283, height=390)
            pagina.insert_text((20, 30), f"DESTINATARIO\nVenda: {venda}\nSKU {sku}\nQTD {qtd}", fontsize=9)

        pagina = fonte.new_page(width=420, height=320)
        pagina.insert_text((18, 25), "MANIFESTO", fontsize=14)
        pagina.draw_rect(fitz.Rect(18, 44, 402, 68), color=None, fill=(0.83, 0.83, 0.83))
        pagina.insert_text((24, 60), "Identificação", fontsize=10)
        pagina.insert_text((163, 60), "Produtos", fontsize=10)
        pagina.insert_text((24, 95), "Venda: ABC300001", fontsize=9)
        pagina.insert_text((163, 95), "SKU: 10-A\nQuantidade: 1", fontsize=9)
        pagina.insert_text((24, 140), "Venda: ABC300002", fontsize=9)
        pagina.insert_text((163, 140), "SKU: 20-B\nQuantidade: 4", fontsize=9)
        pagina.draw_line((18, 175), (402, 175), width=0.5)
        original = pagina.get_pixmap().samples
        resultado = processar_etiquetas_loja("Mercado Livre", fonte.tobytes(), None, None)

    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas:
        assert "Venda: ABC300002" in etiquetas[0].get_text()
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        assert len(manifesto) == 1
        assert manifesto[0].get_pixmap().samples == original
