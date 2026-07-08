"""Marketplace label PDF processors for Etiquetas."""

from __future__ import annotations

from io import BytesIO

from backend.services.etiquetas_marketplaces_common import extrair_chave_acesso, gerar_imagem_barcode
from backend.services.etiquetas_marketplaces_context import (
    _EtiquetasNoopProgress,
    _EtiquetasNoopStreamlit,
    set_streamlit_runtime,
)
from backend.services.etiquetas_marketplaces_ml import (
    _extrair_dados_ml_da_pagina,
    _extrair_ids_ml,
    _extrair_qtd_ml,
    _extrair_sku_do_segmento_ml,
    _normalizar_id_ml,
    processar_mercado_livre,
)
from backend.services.etiquetas_marketplaces_shopee import extract_shopee_data, processar_shopee
from backend.services.etiquetas_marketplaces_tiktok import (
    _eh_pagina_lista_tiktok,
    _extrair_order_id_tiktok,
    _extrair_sku_qtd_tiktok,
    _parse_tiktok_picking_doc,
    parse_tiktok_picking_list,
    processar_tiktok,
)


def _bytes_io(data: bytes | None):
    return BytesIO(data) if data is not None else None


def _processar_mercado_livre_backend(bytes_etiquetas: bytes):
    return processar_mercado_livre(_bytes_io(bytes_etiquetas))


def _processar_tiktok_backend(bytes_etiquetas: bytes, bytes_lista: bytes | None):
    return processar_tiktok(_bytes_io(bytes_etiquetas), _bytes_io(bytes_lista))


def _processar_shopee_backend(bytes_etiquetas: bytes):
    etiquetas_pdf, lista_pdf, qtd = processar_shopee(_bytes_io(bytes_etiquetas))
    return etiquetas_pdf, qtd, lista_pdf


def processar_etiquetas_loja(store: str, bytes_etiquetas: bytes | None, bytes_lista: bytes | None, bytes_nf: bytes | None) -> dict:
    out_etiquetas = None
    out_lista = None
    qtd = 0

    if store == "Mercado Livre":
        if not bytes_etiquetas:
            return {"success": False, "message": "Arquivo principal de etiquetas obrigatorio para Mercado Livre."}
        out_etiquetas, qtd, out_lista = _processar_mercado_livre_backend(bytes_etiquetas)
    elif store == "Shopee":
        if not bytes_etiquetas:
            return {"success": False, "message": "Arquivo principal de etiquetas obrigatorio para Shopee."}
        out_etiquetas, qtd, out_lista = _processar_shopee_backend(bytes_etiquetas)
    elif store == "TikTok":
        if not bytes_etiquetas:
            return {"success": False, "message": "Arquivo principal de etiquetas obrigatorio para TikTok."}
        out_etiquetas, qtd, out_lista = _processar_tiktok_backend(bytes_etiquetas, bytes_lista)
    elif store == "Amazon":
        if not bytes_nf:
            return {"success": False, "message": "Para Amazon, o arquivo de Nota Fiscal obrigatorio."}
        return {"success": False, "message": "Processamento Amazon ainda nao esta disponivel neste backend."}
    else:
        return {"success": False, "message": "Loja invalida."}

    return {
        "success": True,
        "qtd": qtd,
        "out_etiquetas": out_etiquetas,
        "out_lista": out_lista,
    }


__all__ = [
    "_EtiquetasNoopProgress",
    "_EtiquetasNoopStreamlit",
    "_eh_pagina_lista_tiktok",
    "_extrair_dados_ml_da_pagina",
    "_extrair_ids_ml",
    "_extrair_order_id_tiktok",
    "_extrair_qtd_ml",
    "_extrair_sku_do_segmento_ml",
    "_extrair_sku_qtd_tiktok",
    "_normalizar_id_ml",
    "_parse_tiktok_picking_doc",
    "_processar_mercado_livre_backend",
    "_processar_shopee_backend",
    "_processar_tiktok_backend",
    "extract_shopee_data",
    "extrair_chave_acesso",
    "gerar_imagem_barcode",
    "parse_tiktok_picking_list",
    "processar_etiquetas_loja",
    "processar_mercado_livre",
    "processar_shopee",
    "processar_tiktok",
    "set_streamlit_runtime",
]
