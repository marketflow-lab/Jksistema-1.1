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


def ordenar_etiquetas(registros, *, sku, quantidade, pedido):
    """Prioriza quantidade, número de pedidos por SKU e prefixo, nessa ordem."""
    pedidos = {}
    for indice, registro in enumerate(registros):
        identificador = pedido(registro)
        chave = ("pedido", identificador) if identificador else ("etiqueta", indice)
        pedidos.setdefault(chave, []).append(registro)

    grupos = {}
    sem_sku = []

    def qtd(registro):
        try:
            return max(1, int(quantidade(registro)))
        except (TypeError, ValueError):
            return 1

    for etiquetas in pedidos.values():
        skus = {str(sku(registro) or "").strip() for registro in etiquetas}
        skus = {valor for valor in skus if valor and valor.casefold() != "n/d"}
        if len(skus) != 1:
            # Sem um SKU inequívoco, mantém o pedido inteiro na ordem de entrada.
            sem_sku.extend(etiquetas)
            continue
        sku_pedido = skus.pop()
        maior_qtd = max(qtd(registro) for registro in etiquetas)
        grupos.setdefault(sku_pedido, []).append((maior_qtd, etiquetas))

    def prioridade(grupo):
        sku_completo, pedidos_sku = grupo
        prefixo = sku_completo.split("-", 1)[0].casefold()
        ordem_natural = tuple(
            int(parte) if indice % 2 else parte
            for indice, parte in enumerate(re.split(r"([0-9]+)", prefixo))
        )
        return -max(total for total, _ in pedidos_sku), -len(pedidos_sku), ordem_natural

    ordenadas = []
    for _, pedidos_sku in sorted(grupos.items(), key=prioridade):
        for _, etiquetas in sorted(pedidos_sku, key=lambda pedido_sku: -pedido_sku[0]):
            ordenadas.extend(etiquetas)
    return ordenadas + sem_sku
