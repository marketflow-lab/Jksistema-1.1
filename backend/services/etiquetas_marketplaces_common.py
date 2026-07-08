"""Common helpers for marketplace label processors."""

from __future__ import annotations

import re
from io import BytesIO

import barcode
from barcode.writer import ImageWriter

def extrair_chave_acesso(texto_pagina):
    """Extrai os 44 dígitos da chave de acesso da NF-e."""
    match = re.search(r'(?:\d[\W_]?){44}', texto_pagina)
    if match:
        chave_raw = match.group(0)
        return re.sub(r'\D', '', chave_raw)
    return None


def gerar_imagem_barcode(numero_chave):
    """Gera um código de barras Code128 em memória."""
    if not numero_chave: return None
    try:
        code128 = barcode.get('code128', numero_chave, writer=ImageWriter())
        buffer = BytesIO()
        code128.write(buffer, options={"write_text": False, "quiet_zone": 1, "module_height": 4.0})
        return buffer.getvalue()
    except Exception: return None
