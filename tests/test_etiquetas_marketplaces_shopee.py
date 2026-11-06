from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_layout import layout_padrao
from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


PEDIDOS = (
    ("261101ABC123", "20-A", 3, "Produto Azul", "Ana Júlia Azul 7"),
    ("261101ABC1234", "10-VERDE", 5, "Produto Verde", "Bia Capelotto Verde"),
    ("261101ABC125", "10-VERDE", 1, "Produto Verde", "Caio Verde"),
    ("261101ABC126", "2-A", 3, "Produto Branco", "Duda Branca"),
)


ORDEM_ESPERADA = (1, 2, 3, 0)
IDS_SEM_DIVISORIA = ("261101ABC777", "261101ABC778")


def _pdf_shopee(*, checklist_em_blocos=False, multiproduto=False):
    with fitz.open() as doc:
        etiquetas = doc.new_page(width=566, height=850)
        for i, (pedido, _, _, _, comprador) in enumerate(PEDIDOS):
            x, y = (i % 2) * 283 + 20, (i // 2) * 425 + 30
            if i < 2:
                etiquetas.insert_text((x, y), f"Pedido {pedido}\nDestinatário:", fontsize=9)
                # Na Shopee, este campo lateral pode vir antes do nome no texto extraído.
                tipo_endereco = "RESIDENCIAL" if i == 0 else "COMERCIAL"
                etiquetas.insert_text((x + 120, y + 62), tipo_endereco, fontsize=9)
                nome = f"Nome: {comprador}" if i == 0 else comprador
                etiquetas.insert_text((x, y + 27), nome, fontsize=9)
            else:
                destinatario = f"Destinatário: {comprador}" if i == 2 else f"04/11/2026\n{comprador}"
                etiquetas.insert_text((x, y), f"Pedido {pedido}\n{destinatario}", fontsize=9)
        doc.new_page(width=283, height=425).insert_text(
            (20, 30), "Pedido 261101\nABC127\n04/11/2026\nEva Sem SKU", fontsize=9,
        )
        doc.new_page(width=283, height=425).insert_text(
            (20, 30), "CHAVE DE ACESSO\n26110112345678901234567890123456789012345678\nDANFE", fontsize=9,
        )
        if checklist_em_blocos:
            # Página sem divisão verificável deve permanecer inteira.
            doc.new_page(width=283, height=425).insert_text(
                (20, 30), "Manifesto\n" + "\n".join(IDS_SEM_DIVISORIA), fontsize=9,
            )
            checklist = doc.new_page(width=283, height=760)
            checklist.insert_text((20, 15), "Cabecalho do checklist", fontsize=9)
            checklist.insert_text((20, 30), "----- Corte aqui -----", fontsize=8)
            checklist.insert_text((20, 742), "Rodape do checklist", fontsize=9)
        for i, (pedido, sku, quantidade, produto, _) in enumerate(PEDIDOS):
            bloco = f"{pedido}\n1\n{produto}\n{quantidade}\n{sku}\nChecklist"
            if i == 0:
                segundo = "\n2\nProduto extra\n8\nEXTRA-08" if multiproduto else ""
                bloco = (
                    f"ID Pedido:\n{pedido}\nProduto\nVariação\nQnt\nSKU\n"
                    f"1\n{produto}\nAzul claro\n{quantidade}\n{sku}{segundo}\nChecklist de carregamento"
                )
            if checklist_em_blocos:
                y = 50 + i * 165
                checklist.insert_text((20, y), bloco, fontsize=9)
                checklist.insert_text((20, y + 148), "----- Corte aqui -----", fontsize=8)
            else:
                doc.new_page(width=283, height=425).insert_text((20, 30), bloco, fontsize=9)
        return doc.tobytes()


@pytest.mark.parametrize("usar_layout", [False, True])
def test_ordena_recortes_da_shopee_mantendo_dados_de_cada_pedido(usar_layout):
    layout = layout_padrao("Shopee") if usar_layout else None
    resultado = processar_etiquetas_loja("Shopee", _pdf_shopee(), None, None, layout=layout)
    assert resultado["qtd"] == 5

    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas:
        assert len(etiquetas) == 5
        for numero, i in enumerate(ORDEM_ESPERADA, start=1):
            pedido, sku, quantidade, produto, comprador = PEDIDOS[i]
            pagina = etiquetas[numero - 1]
            linhas = pagina.get_text().splitlines()
            assert f"Pedido {pedido}" in linhas
            if quantidade > 1:
                assert f"SKU: {sku}" in linhas
                selo = pagina.search_for(f"{quantidade} UNIDADES")[0]
                assert pagina.rect.contains(selo)
                assert selo.x0 >= 266
            else:
                assert f"SKU: {sku} (x1)" in linhas
                assert not any(linha.endswith("UNIDADES") for linha in linhas)
            assert produto not in linhas
            assert sum(comprador in linha for linha in linhas) == 1
            assert f"#{numero}" in linhas
            assert sum(linha.startswith("Pedido ") for linha in linhas) == 1
        assert "Pedido 261101\nABC127" in etiquetas[4].get_text()
        assert "SKU N/D" in etiquetas[4].get_text()

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        assert len(manifesto) == 1
        _verificar_checklist(manifesto[0])


def _verificar_checklist(pagina):
    texto = pagina.get_text().splitlines()
    assert not {"RESIDENCIAL", "COMERCIAL"}.intersection(texto)
    assert [linha for linha in texto if linha.startswith("Pedido ")] == [
        f"Pedido {PEDIDOS[i][0]}" for i in ORDEM_ESPERADA
    ]
    assert [linha for linha in texto if linha.startswith("Total:")] == [
        "Total: 6 itens", "Total: 3 itens", "Total: 3 itens"
    ]
    assert [linha for linha in texto if linha.startswith("SKU ")] == ["SKU 10-VERDE", "SKU 2-A", "SKU 20-A"]
    selos = [fitz.Rect(imagem["bbox"]) for imagem in pagina.get_image_info()]
    assert len(selos) == 3
    spans = {
        span["text"]: fitz.Rect(span["bbox"])
        for bloco in pagina.get_text("dict")["blocks"]
        for linha in bloco.get("lines", ())
        for span in linha["spans"]
    }
    for pedido, _, quantidade, produto, comprador in PEDIDOS:
        caixa_pedido = spans[f"Pedido {pedido}"]
        caixa_comprador = spans[comprador]
        assert abs(caixa_pedido.y0 - caixa_comprador.y0) < 0.1
        assert abs((caixa_comprador.x0 + caixa_comprador.x1) / 2 - pagina.rect.width / 2) < 0.1
        assert any(0 < caixa.y0 - caixa_pedido.y0 < 20 for caixa in pagina.search_for(produto))
        assert any(caixa.y0 <= caixa_pedido.y0 + 3 < caixa.y1 for caixa in selos) == (quantidade > 1)
    assert "Azul claro" in texto


def test_ordena_blocos_do_checklist_sem_separar_dados_do_pedido():
    resultado = processar_etiquetas_loja(
        "Shopee", _pdf_shopee(checklist_em_blocos=True), None, None,
    )
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        assert len(manifesto) == 2
        _verificar_checklist(manifesto[0])
        linhas = manifesto[0].get_text().splitlines()
        assert "Cabecalho do checklist" in linhas
        assert "Rodape do checklist" in linhas
        assert manifesto[1].get_text().splitlines() == ["Manifesto", *IDS_SEM_DIVISORIA]


def test_grade_girada_da_shopee_exibe_quantidades_e_checklist_por_sku():
    with fitz.open() as doc, fitz.open(stream=_pdf_shopee(), filetype="pdf") as fonte, fitz.open() as blocos:
        doc.insert_pdf(fonte, from_page=0, to_page=0)
        checklist = doc.new_page(width=595, height=842)
        imagem = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
        imagem.clear_with(70)
        for indice, (pedido, sku, quantidade, produto, _) in enumerate(PEDIDOS):
            bloco = blocos.new_page(width=421, height=297.5)
            for x, rotulo in zip((16, 193, 336, 357), ("Produto", "Variação", "Qnt", "SKU")):
                bloco.insert_text((x, 97), rotulo, fontsize=8)
            variacao = "Azul claro" if indice == 0 else "-"
            for x, valor in zip((5, 16, 193, 336, 357), ("1", produto, variacao, str(quantidade), sku)):
                bloco.insert_text((x, 117), valor, fontsize=8)
            bloco.insert_text((186, 40), "Checklist de carregamento", fontsize=8)
            bloco.insert_text((16, 70), "ID Pedido", fontsize=8)
            bloco.insert_text((65, 70), f"{pedido} PACKAGE", fontsize=8)
            bloco.insert_text((200, 8), "Corte aqui", fontsize=7)
            bloco.insert_image((70, 15, 115, 35), pixmap=imagem)
        for indice in range(len(blocos)):
            x, y = (indice % 2) * 297.5, (indice // 2) * 421
            checklist.show_pdf_page(fitz.Rect(x, y, x + 297.5, y + 421), blocos, indice, rotate=90)
        resultado = processar_etiquetas_loja("Shopee", doc.tobytes(), None, None)

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        assert len(manifesto) == 1
        _verificar_checklist(manifesto[0])
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas:
        assert len(etiquetas) == len(PEDIDOS)
        assert etiquetas[0].search_for("5 UNIDADES")
        assert etiquetas[1].search_for("SKU: 10-VERDE (x1)")


def test_pedido_com_varios_produtos_preserva_checklist_sem_selo_inferido():
    original = _pdf_shopee(multiproduto=True)
    resultado = processar_etiquetas_loja("Shopee", original, None, None)
    with (
        fitz.open(stream=original, filetype="pdf") as fonte,
        fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto,
        fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as etiquetas,
    ):
        pedido = PEDIDOS[0][0]
        pagina = next(pagina for pagina in manifesto if "Produto extra" in pagina.get_text())
        assert pagina.get_pixmap().samples == fonte[3].get_pixmap().samples
        assert "Total:" not in pagina.get_text()
        etiqueta = next(pagina for pagina in etiquetas if f"Pedido {pedido}" in pagina.get_text().splitlines())
        assert "UNIDADES" not in etiqueta.get_text()
