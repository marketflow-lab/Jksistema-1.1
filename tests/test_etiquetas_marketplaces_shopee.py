from __future__ import annotations

import fitz
import pytest

from backend.services.etiquetas_layout import layout_padrao
from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


PEDIDOS = (
    ("261101ABC123", "20-A", 3, "Produto Azul", "Ana Azul"),
    ("261101ABC1234", "10-VERDE", 5, "Produto Verde", "Bia Verde"),
    ("261101ABC125", "10-VERDE", 1, "Produto Verde", "Caio Verde"),
    ("261101ABC126", "2-A", 3, "Produto Branco", "Duda Branca"),
)


ORDEM_ESPERADA = (1, 2, 3, 0)
IDS_SEM_DIVISORIA = ("261101ABC777", "261101ABC778")


def _pdf_shopee(*, checklist_em_blocos=False):
    with fitz.open() as doc:
        etiquetas = doc.new_page(width=566, height=850)
        for i, (pedido, _, _, _, comprador) in enumerate(PEDIDOS):
            x, y = (i % 2) * 283 + 20, (i // 2) * 425 + 30
            etiquetas.insert_text((x, y), f"Pedido {pedido}\n04/11/2026\n{comprador}", fontsize=9)
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
            checklist = doc.new_page(width=283, height=480)
            checklist.insert_text((20, 15), "Cabecalho do checklist", fontsize=9)
            checklist.insert_text((20, 30), "----- Corte aqui -----", fontsize=8)
            checklist.insert_text((20, 462), "Rodape do checklist", fontsize=9)
        for i, (pedido, sku, quantidade, produto, _) in enumerate(PEDIDOS):
            bloco = f"{pedido}\n1\n{produto}\n{quantidade}\n{sku}\nChecklist"
            if checklist_em_blocos:
                y = 50 + i * 100
                checklist.insert_text((20, y), bloco, fontsize=9)
                checklist.insert_text((20, y + 80), "----- Corte aqui -----", fontsize=8)
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
            linhas = etiquetas[numero - 1].get_text().splitlines()
            assert f"Pedido {pedido}" in linhas
            assert f"SKU: {sku} (x{quantidade})" in linhas
            assert produto in linhas
            assert linhas.count(comprador) == 2
            assert f"#{numero}" in linhas
            assert sum(linha.startswith("Pedido ") for linha in linhas) == 1
        assert "Pedido 261101\nABC127" in etiquetas[4].get_text()
        assert "SKU N/D" in etiquetas[4].get_text()

    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        assert len(manifesto) == len(PEDIDOS)
        assert [pagina.get_text().splitlines()[0] for pagina in manifesto] == [
            PEDIDOS[i][0] for i in ORDEM_ESPERADA
        ]


def test_ordena_blocos_do_checklist_sem_separar_dados_do_pedido():
    resultado = processar_etiquetas_loja(
        "Shopee", _pdf_shopee(checklist_em_blocos=True), None, None,
    )
    with fitz.open(stream=resultado["out_lista"], filetype="pdf") as manifesto:
        lista = "\n".join(pagina.get_text() for pagina in manifesto)
        posicoes = []
        for i in ORDEM_ESPERADA:
            pedido, sku, quantidade, produto, _ = PEDIDOS[i]
            bloco = f"{pedido}\n1\n{produto}\n{quantidade}\n{sku}\nChecklist"
            assert lista.count(bloco) == 1
            posicoes.append(lista.index(bloco))
        assert posicoes == sorted(posicoes)
        for pagina in list(manifesto)[:-1]:
            linhas = pagina.get_text().splitlines()
            assert "Cabecalho do checklist" in linhas
            assert "Rodape do checklist" in linhas
        assert manifesto[-1].get_text().splitlines() == ["Manifesto", *IDS_SEM_DIVISORIA]
