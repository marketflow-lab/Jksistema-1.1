from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_layout import layout_padrao
from backend.services.etiquetas_marketplaces import processar_etiquetas_loja
from backend.services.etiquetas_marketplaces_tiktok import _extrair_destinatario_tiktok


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


def _picking_tabular_tiktok(linhas):
    with fitz.open() as pdf:
        pagina = pdf.new_page(width=595, height=842)
        pagina.insert_text((24, 39), "Picking List", fontsize=12)
        pagina.insert_text((24, 88), f"Total Orders: {len(linhas)}", fontsize=9)
        colunas = (24, 132, 270, 342, 409, 456)
        cabecalhos = ("No", "Product Name", "SKU", "Seller SKU", "Qty", "Order ID")
        for x, nome in zip(colunas, cabecalhos):
            pagina.insert_text((x, 147), nome, fontsize=9)
        for numero, (pedido, produto, sku_plataforma, sku_vendedor, quantidade) in enumerate(linhas, 1):
            y = 184 + 48 * (numero - 1)
            valores = (str(numero), produto, sku_plataforma, sku_vendedor, str(quantidade), pedido)
            for x, valor in zip(colunas, valores):
                pagina.insert_text((x, y), valor, fontsize=8)
        return pdf.tobytes()


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
            ("PEDIDO ALTO", "SKU: 10-A 5 UN."),
            ("PEDIDO ALTO SEGUNDA ETIQUETA", "SKU: 10-A 5 UN."),
            ("PEDIDO BAIXO", "SKU: 10-A (x1)"),
            ("PEDIDO MEDIO", "SKU: 20-B 3 UN."),
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
        resumo = pdf[0]
        texto = resumo.get_text()
        assert "Resumo de separação" in texto
        assert "Total: 6 unidades" in texto
        assert "2 pedidos" in texto
        assert texto.count("323456789012345") == 1
        assert "PEDIDOS NA MESMA PAGINA" not in texto
        assert "5 UNIDADES" in texto
        pedido = resumo.search_for("323456789012345")[0]
        produto = next(area for area in resumo.search_for("Produto exemplo") if abs(area.y0 - pedido.y0) < 1)
        unidades = resumo.search_for("5 UNIDADES")[0]
        assert pedido.x1 < produto.x0 < produto.x1 < unidades.x0
        assert pedido.y0 < unidades.y1 and unidades.y0 < pedido.y1
        assert pdf.page_count == picking.page_count + 1 == len(ordem_esperada) + 1
        for pagina, indice_origem in zip(pdf.pages(start=1), ordem_esperada):
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
        assert [pagina.get_text().splitlines()[1] for pagina in pdf.pages(start=1)] == [
            "Order ID: 323456789012345",
            "Order ID: 123456789012345",
            "Order ID: 423456789012345",
            "Order ID: 223456789012345",
        ]


def test_resumo_tiktok_retem_varios_produtos_sem_contar_copias_nem_quantidades_conflitantes():
    pedido = "123456789012345"
    tabela = (
        f"PICKING LIST\nOrder ID: {pedido}\nProduct Name\nSeller SKU\nQty\n"
        "Par de lâmpadas\nA-01\n2\nPar de lâmpadas\nA-01\n2\nRoda livre\nB-02\n3\nQty Total: 7"
    )
    conflitante = tabela.replace("Roda livre\nB-02\n3\nQty Total: 7", "Roda livre\nB-02\n4\nQty Total: 8")
    etiquetas = _pdf_tiktok([f"RECEBEDOR\nOrder ID: {pedido}"])
    lista = _pdf_tiktok([tabela, tabela, conflitante])

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None)

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
        assert pdf.page_count == 4
        resumo = pdf[0].get_text()
        assert "Total: 4 unidades" in resumo
        assert resumo.count("2 UNIDADES") == 2
        assert "B-02" not in resumo
        assert "B-02" in pdf[1].get_text()
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        assert "SKU N/D" in pdf[0].get_text()
        assert "UN." not in pdf[0].get_text()


def test_tiktok_quantidade_sem_tabela_confirmada_nao_gera_selo_nem_resumo():
    pedido = "123456789012345"
    etiquetas = _pdf_tiktok([f"RECEBEDOR\nOrder ID: {pedido}"])
    lista = _pdf_tiktok([f"PICKING LIST\nOrder ID: {pedido}\nA-01\n3"])

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None)

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
        assert pdf.page_count == 1
        assert "Resumo de separação" not in pdf[0].get_text()
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        assert "SKU: A-01 (x3)" in " ".join(pdf[0].get_text().split())
        assert "UN." not in pdf[0].get_text()


def test_tiktok_soma_pacotes_distintos_e_preserva_selo_no_layout_personalizado():
    pedido = "123456789012345"
    pacotes = ("PK1234567890", "PK1234567891")
    etiquetas = _pdf_tiktok([f"RECEBEDOR\nOrder ID: {pedido}\nPackage ID: {pacote}" for pacote in pacotes])
    picking = [_pagina_picking(pedido, "81-K", 3, pacote) for pacote in pacotes]
    lista = _pdf_tiktok([*picking, picking[0], _pagina_picking(pedido, "81-K", 6)])
    layout = layout_padrao("TikTok")
    layout["items"]["sku"]["y"] = 5

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None, layout=layout)

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
        resumo = pdf[0].get_text()
        assert "Total: 6 unidades" in resumo
        assert resumo.count("3 UNIDADES") == 2
        assert pdf.page_count == 5
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        assert pdf.page_count == 2
        for pagina, pacote in zip(pdf, pacotes):
            texto = pagina.get_text()
            assert "3 UN." in texto
            assert pacote in texto
            assert pagina.rect.contains(pagina.search_for("3 UN.")[0])


@pytest.mark.parametrize(("fonte", "valor", "esperado"), [
    ("hebo", "Iara", "Iara"),
    ("helv", "Iara", None),
    ("hebo", "AB1234", None),
    ("hebo", "Rua", None),
])
def test_destinatario_tiktok_unitario_exige_negrito_e_valor_de_nome(fonte, valor, esperado):
    with fitz.open() as pdf:
        pagina = pdf.new_page(width=298, height=420)
        pagina.insert_text((19, 212), "Destinatário", fontsize=8)
        pagina.insert_text((17.2, 227), valor, fontsize=8, fontname=fonte)
        pagina.insert_text((17.2, 239), "Rua de Exemplo, 10", fontsize=7)
        assert _extrair_destinatario_tiktok(pagina) == esperado


def _verificar_nome_do_pedido(pagina, pedido, nome):
    [area_pedido] = pagina.search_for(pedido)
    [area_nome] = pagina.search_for(nome)
    assert 0 < area_nome.y0 - area_pedido.y0 < 2 * area_pedido.height
    assert area_pedido.x0 <= area_nome.x0 < area_pedido.x1


@pytest.mark.parametrize(("destinatario", "identificacao", "sku_esperado", "nome_esperado"), [
    ("Ana Exemplo", "", "SKU: 10-A 3 UN.", "Ana Exemplo"),
    ("maria12345678.", "", "SKU: 10-A 3 UN.", "Maria"),
    ("usuario12345678.", "", "SKU: 10-A 3 UN.", "Nome não informado"),
    ("JKB19Z45\nRua das Amostras, 10", "", "SKU: 10-A 3 UN.", "Nome não informado"),
    ("Nome Conflitante", "\nOrder ID: 200000000000000002", "SKU N/D", "Nome não informado"),
])
def test_picking_tabular_tiktok_externo_unitario(destinatario, identificacao, sku_esperado, nome_esperado):
    pedido = "100000000000000001"
    etiquetas = _pdf_tiktok([f"Destinatário:\n{destinatario}{identificacao}"])
    lista = _picking_tabular_tiktok([(pedido, "Kit de cabos\npara teste", "CAT-10", "10-A", 3)])

    resultado = processar_etiquetas_loja("TikTok", etiquetas, lista, None)

    assert resultado["qtd"] == 1
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        texto = " ".join(pdf[0].get_text().split())
        assert sku_esperado in texto
        assert "CAT-10" not in texto
    with (
        fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf,
        fitz.open(stream=lista, filetype="pdf") as original,
    ):
        assert pdf.page_count == 2
        resumo = pdf[0].get_text()
        assert "Resumo de separação" in resumo
        assert pedido in resumo
        assert "Kit de cabos para teste" in resumo
        assert nome_esperado in resumo.splitlines()
        _verificar_nome_do_pedido(pdf[0], pedido, nome_esperado)
        assert "12345678." not in resumo
        assert "JKB19Z45" not in resumo
        assert "Rua das Amostras" not in resumo
        assert "Nome Conflitante" not in resumo
        assert "SKU 10-A" in resumo
        assert "3 UNIDADES" in resumo
        assert "CAT-10" not in resumo
        assert pdf[1].get_text() == original[0].get_text()
        assert pdf[1].get_pixmap().samples == original[0].get_pixmap().samples


@pytest.mark.parametrize("lista_embutida", [False, True])
def test_picking_tabular_tiktok_separa_pedidos_na_mesma_pagina(lista_embutida):
    primeiro = "100000000000000001"
    segundo = "200000000000000002"
    etiquetas = _pdf_tiktok([
        f"ETIQUETA A\nDestinatário:\nAna Exemplo\nOrder ID: {primeiro}",
        "ETIQUETA SEM ID\nDestinatário:\nCris Exemplo",
        f"ETIQUETA B\nDestinatário:\nBruno Exemplo\nOrder ID: {segundo}",
    ])
    lista = _picking_tabular_tiktok([
        (segundo, "Jogo de juntas", "CAT-20", "20-B", 2),
        (primeiro, "Kit de cabos", "CAT-10", "10-A", 1),
    ])

    if lista_embutida:
        with fitz.open(stream=etiquetas, filetype="pdf") as doc, fitz.open(stream=lista, filetype="pdf") as picking:
            doc.insert_pdf(picking)
            etiquetas = doc.tobytes()

    resultado = processar_etiquetas_loja("TikTok", etiquetas, None if lista_embutida else lista, None)

    assert resultado["qtd"] == 3
    with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
        esperados = (
            ("ETIQUETA B", "SKU: 20-B 2 UN."),
            ("ETIQUETA A", "SKU: 10-A (x1)"),
            ("ETIQUETA SEM ID", "SKU N/D"),
        )
        for pagina, (marcador, sku) in zip(pdf, esperados, strict=True):
            texto = " ".join(pagina.get_text().split())
            assert marcador in texto
            assert sku in texto
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
        assert pdf.page_count == 2
        resumo = pdf[0]
        _verificar_nome_do_pedido(resumo, primeiro, "Ana Exemplo")
        _verificar_nome_do_pedido(resumo, segundo, "Bruno Exemplo")
        texto = resumo.get_text()
        assert "Resumo de separação" in texto
        assert "Cris Exemplo" not in texto
        texto_sem_nomes = texto.replace("Ana Exemplo\n", "").replace("Bruno Exemplo\n", "")
        assert f"{primeiro}\nKit de cabos\n1\n" in texto_sem_nomes
        assert f"{segundo}\nJogo de juntas\n2 UNIDADES\n" in texto_sem_nomes


def _etiqueta_com_rastreio_tiktok(pdf, marcador, destinatario, rastreio):
    pagina = pdf.new_page(width=298, height=420)
    pagina.insert_text((14, 36), marcador, fontsize=9)
    pagina.insert_text((14, 68), rastreio, fontsize=9)
    pagina.insert_text((14, 142), f"Destinatário:\n{destinatario}", fontsize=8)


def _packing_compacto_tiktok(pdf, pedido, pacote, rastreio, produto, sku, qtd, *, total=None):
    pagina = pdf.new_page(width=298, height=420)
    pagina.insert_text((11, 17), "Packing List", fontsize=12)
    identificacao = f"Order ID: {pedido}\nPackage ID: {pacote}"
    pagina.insert_text((11, 33), identificacao, fontsize=8)
    pagina.insert_text((11, 59), f"Tracking number: {rastreio}", fontsize=8)
    colunas = (11, 130, 177, 270)
    for x, nome in zip(colunas, ("Product Name", "SKU", "Seller SKU", "Qty")):
        pagina.insert_text((x, 75), nome, fontsize=7)
    for x, texto in zip(colunas, (produto, f"PLAT-{sku}", sku, str(qtd))):
        pagina.insert_text((x, 93), texto, fontsize=7)
    pagina.insert_text((251, 118), "Qty Total:", fontsize=6)
    pagina.insert_text((281, 118), str(qtd if total is None else total), fontsize=7)
    pagina.insert_text((11, 135), identificacao, fontsize=8)


def test_tiktok_pdf_unico_vincula_packing_compacto_por_rastreio():
    pedido_a = "100000000000000001"
    pedido_b = "200000000000000002"
    with fitz.open() as origem:
        _etiqueta_com_rastreio_tiktok(origem, "ETIQUETA B", "Bruno Exemplo", "BB 400 500 602 BR")
        _packing_compacto_tiktok(origem, pedido_a, "PK100000000001", "AA100200301BR", "Kit de cabos", "10-A", 1)
        _etiqueta_com_rastreio_tiktok(origem, "ETIQUETA A", "Ana Exemplo", "AA 100 200 301 BR")
        _packing_compacto_tiktok(origem, pedido_b, "PK200000000002", "BB400500602BR", "Jogo de juntas\npara teste", "20-B", 3)

        resultado = processar_etiquetas_loja("TikTok", origem.tobytes(), None, None)

        assert resultado["qtd"] == 2
        with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
            assert pdf.page_count == 2
            etiqueta_b, etiqueta_a = (" ".join(pagina.get_text().split()) for pagina in pdf)
            assert "ETIQUETA B" in etiqueta_b and "SKU: 20-B 3 UN." in etiqueta_b
            assert "ETIQUETA A" in etiqueta_a and "SKU: 10-A (x1)" in etiqueta_a

        with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
            assert pdf.page_count == 3
            resumo = pdf[0].get_text()
            assert "Resumo de separação" in resumo
            assert "SKU 20-B" in resumo and "SKU 10-A" in resumo
            assert f"{pedido_b}\nBruno Exemplo\nJogo de juntas para teste\n3 UNIDADES\n" in resumo
            assert f"{pedido_a}\nAna Exemplo\nKit de cabos\n1\n" in resumo
            assert "PLAT-" not in resumo
            for pagina, indice_origem in zip(pdf.pages(start=1), (3, 1), strict=True):
                original = origem[indice_origem]
                assert pagina.get_text() == original.get_text()
                assert pagina.get_pixmap().samples == original.get_pixmap().samples


@pytest.mark.parametrize(("rastreio_packing", "total", "paginas_lista"), [
    ("CC700800903BR", 2, 2),
    ("AA100200301BR", 4, 1),
])
def test_tiktok_pdf_unico_nao_confirma_rastreio_ou_total_conflitante(rastreio_packing, total, paginas_lista):
    with fitz.open() as origem:
        _etiqueta_com_rastreio_tiktok(origem, "ETIQUETA A", "Ana Exemplo", "AA 100 200 301 BR")
        _packing_compacto_tiktok(
            origem, "100000000000000001", "PK100000000001", rastreio_packing, "Kit de cabos", "10-A", 2, total=total
        )
        resultado = processar_etiquetas_loja("TikTok", origem.tobytes(), None, None)

        assert resultado["qtd"] == 1
        with fitz.open(stream=resultado["out_etiquetas"], filetype="pdf") as pdf:
            assert "SKU N/D" in pdf[0].get_text()
        with fitz.open(stream=resultado["out_lista"], filetype="pdf") as pdf:
            assert pdf.page_count == paginas_lista
            if paginas_lista == 2:
                resumo = pdf[0].get_text()
                assert "SKU 10-A" in resumo
                assert "Nome não informado" in resumo
                assert "Ana Exemplo" not in resumo
            assert pdf[-1].get_pixmap().samples == origem[1].get_pixmap().samples
