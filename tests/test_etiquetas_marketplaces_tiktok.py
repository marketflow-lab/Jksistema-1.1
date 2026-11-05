from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


def _pdf_tiktok(paginas):
    with fitz.open() as doc:
        for texto in paginas:
            pagina = doc.new_page(width=283, height=425)
            pagina.insert_textbox(fitz.Rect(20, 20, 260, 390), texto, fontsize=8)
        return doc.tobytes()


def _pagina_picking(order_id, sku, qtd, package_id=""):
    return (
        f"PICKING LIST\nOrder ID: {order_id}\nPackage ID: {package_id}\n"
        f"Product Name\nSeller SKU\nQty\nProduto exemplo\n{sku}\n{qtd}\nQty Total"
    )

@pytest.mark.parametrize("lista_embutida", [False, True])
def test_ordena_etiquetas_e_lista_tiktok_sem_trocar_pedidos(lista_embutida):
    etiquetas = _pdf_tiktok([
        "RECEBEDOR\nPEDIDO BAIXO\nOrder ID: 123456789012345",
        "RECEBEDOR\nSEM IDENTIFICADOR",
        "RECEBEDOR\nPEDIDO MEDIO\nOrder ID: 223456789012345",
        "RECEBEDOR\nPEDIDO ALTO\nPackage ID: PK3456789012345",
        "RECEBEDOR\nPEDIDO ALTO SEGUNDA ETIQUETA\nPackage ID: PK3456789012345",
        "RECEBEDOR\nPEDIDO AUSENTE\nOrder ID: 423456789012345",
    ])
    lista = _pdf_tiktok([
        _pagina_picking("223456789012345", "20-B", 3),
        _pagina_picking("323456789012345", "10-A", 5, "PK3456789012345"),
        _pagina_picking("523456789012345", "30-C", 9),
        _pagina_picking("123456789012345", "10-A", 1),
        "PICKING LIST\nOrder ID: 223456789012345\nOrder ID: 323456789012345\nPEDIDOS NA MESMA PAGINA",
    ])
    if lista_embutida:
        with fitz.open(stream=etiquetas, filetype="pdf") as doc, fitz.open(stream=lista, filetype="pdf") as picking:
            doc.insert_pdf(picking)
            etiquetas = doc.tobytes()

    resultado = processar_etiquetas_loja("TikTok", etiquetas, None if lista_embutida else lista, None)

    assert resultado["qtd"] == 6
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        esperadas = [
            ("PEDIDO ALTO", "SKU: 10-A (x5)"),
            ("PEDIDO ALTO SEGUNDA ETIQUETA", "SKU: 10-A (x5)"),
            ("PEDIDO BAIXO", "SKU: 10-A (x1)"),
            ("PEDIDO MEDIO", "SKU: 20-B (x3)"),
            ("SEM IDENTIFICADOR", "SKU N/D"),
            ("PEDIDO AUSENTE", "SKU N/D"),
        ]
        assert pdf.page_count == len(esperadas)
        for indice, (pagina, (marcador, sku)) in enumerate(zip(pdf, esperadas), 1):
            texto = pagina.get_text()
            assert marcador in texto.splitlines()
            assert sku in " ".join(texto.split())
            assert texto.splitlines()[-1] == str(indice)
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf, fitz.open(stream=lista, filetype="pdf") as picking:
        # A lista segue ALTO, BAIXO e MEDIO, seguida pelas páginas sem vínculo único.
        ordem_esperada = (1, 3, 0, 2, 4)
        assert pdf.page_count == picking.page_count == len(ordem_esperada)
        for pagina, indice_origem in zip(pdf, ordem_esperada):
            original = picking[indice_origem]
            assert pagina.get_text() == original.get_text()
            assert pagina.get_pixmap().samples == original.get_pixmap().samples


@pytest.mark.parametrize("identificacao", [
    "",
    "\nOrder ID: 223456789012345\nPackage ID: PK3456789012345",
])
def test_lista_externa_unica_exige_identificadores_compativeis(identificacao):
    etiquetas = _pdf_tiktok([f"RECEBEDOR{identificacao}"])
    lista = _pdf_tiktok([_pagina_picking("123456789012345", "10-A", 5, "PK3456789012345")])

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None)

    assert resultado["qtd"] == 1
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        assert "SKU N/D" in pdf[0].get_text()


def test_package_id_compartilhado_por_pedidos_distintos_nao_reordena_paginas_ambiguas():
    etiquetas = _pdf_tiktok([
        "RECEBEDOR\nPackage ID: PK3456789012345",
        "RECEBEDOR\nOrder ID: 323456789012345",
    ])
    lista = _pdf_tiktok([
        _pagina_picking("123456789012345", "10-A", 5, "PK3456789012345"),
        _pagina_picking("323456789012345", "20-B", 3),
        _pagina_picking("423456789012345", "30-C", 2),
        _pagina_picking("223456789012345", "40-D", 1, "PK3456789012345"),
    ])

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None)

    assert resultado["qtd"] == 2
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        assert "SKU N/D" in pdf[1].get_text()
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
        assert [pagina.get_text().splitlines()[1] for pagina in pdf] == [
            "Order ID: 323456789012345",
            "Order ID: 123456789012345",
            "Order ID: 423456789012345",
            "Order ID: 223456789012345",
        ]
