from __future__ import annotations

import fitz

from backend.services.etiquetas_marketplaces import processar_etiquetas_loja


CHAVE_ACESSO = "12345678901234567890123456789012345678901234"


def _pdf_mercado_livre(*, incluir_chave: bool) -> bytes:
    documento = fitz.open()
    etiqueta = documento.new_page(width=283, height=390)
    etiqueta.insert_text(
        fitz.Point(20, 30),
        "DESTINATARIO PEDIDO ABC123456 SKU TESTE-ML QTD 1",
    )

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
