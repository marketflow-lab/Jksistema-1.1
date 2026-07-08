"""Etiquetas service facade.

Keep public imports stable while implementation is split by responsibility.
"""

from __future__ import annotations

from backend.services.etiquetas_marketplaces import (
    _processar_mercado_livre_backend,
    _processar_shopee_backend,
    _processar_tiktok_backend,
    processar_etiquetas_loja,
    processar_mercado_livre,
    processar_shopee,
    processar_tiktok,
)
from backend.services.etiquetas_pdf_avulso import (
    gerar_pdf_editor_livre,
    gerar_pdf_impressao_avulsa,
)
from backend.services.etiquetas_preview import gerar_preview_pdf_bytes, obter_pdf_temporario
from backend.services.etiquetas_qrcode import gerar_pdf_qrcode_link

__all__ = [
    "_processar_mercado_livre_backend",
    "_processar_shopee_backend",
    "_processar_tiktok_backend",
    "gerar_pdf_editor_livre",
    "gerar_pdf_impressao_avulsa",
    "gerar_pdf_qrcode_link",
    "gerar_preview_pdf_bytes",
    "obter_pdf_temporario",
    "processar_etiquetas_loja",
    "processar_mercado_livre",
    "processar_shopee",
    "processar_tiktok",
]
