"""Compatibility shim for the legacy Etiquetas module."""

from __future__ import annotations

from backend.services.etiquetas_marketplaces import (
    _extrair_dados_ml_da_pagina,
    _extrair_ids_ml,
    _extrair_order_id_tiktok,
    _extrair_qtd_ml,
    _extrair_sku_do_segmento_ml,
    _extrair_sku_qtd_tiktok,
    _eh_pagina_lista_tiktok,
    _normalizar_id_ml,
    _parse_tiktok_picking_doc,
    extract_shopee_data,
    extrair_chave_acesso,
    gerar_imagem_barcode,
    parse_tiktok_picking_list,
    processar_mercado_livre,
    processar_shopee,
    processar_tiktok,
    set_streamlit_runtime,
)
from backend.services.etiquetas_streamlit import enviar_para_impressora_local, render_page

__all__ = [
    "_extrair_dados_ml_da_pagina",
    "_extrair_ids_ml",
    "_extrair_order_id_tiktok",
    "_extrair_qtd_ml",
    "_extrair_sku_do_segmento_ml",
    "_extrair_sku_qtd_tiktok",
    "_eh_pagina_lista_tiktok",
    "_normalizar_id_ml",
    "_parse_tiktok_picking_doc",
    "enviar_para_impressora_local",
    "extract_shopee_data",
    "extrair_chave_acesso",
    "gerar_imagem_barcode",
    "parse_tiktok_picking_list",
    "processar_mercado_livre",
    "processar_shopee",
    "processar_tiktok",
    "render_page",
    "set_streamlit_runtime",
]
