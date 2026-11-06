"""Common helpers for marketplace label processors."""

from __future__ import annotations

import re
from io import BytesIO

import barcode
import fitz
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


def _linhas_rodape_sku(texto, tamanho, largura):
    linhas = [""]
    for caractere in texto:
        candidato = linhas[-1] + caractere
        if fitz.get_text_length(candidato, fontname="hebo", fontsize=tamanho) > largura:
            linhas.append(caractere)
        else:
            linhas[-1] = candidato
    return [linha.strip() for linha in linhas]


def desenhar_rodape_sku(pagina, sku_text, *, cor, quantidade=None):
    pagina.draw_line((0, 390), (283, 390), color=(0, 0, 0), width=1)
    if quantidade is None or int(quantidade) <= 1:
        for tamanho in (12, 11, 10, 9, 8, 7):
            linhas = _linhas_rodape_sku(sku_text, tamanho, 271)
            if pagina.insert_textbox(
                fitz.Rect(6, 397, 277, 424), "\n".join(linhas),
                fontsize=tamanho, fontname="hebo", color=cor, align=1,
            ) >= 0:
                break
        return

    sku = sku_text.removesuffix(f"  x  {quantidade}")
    selo = f"{int(quantidade)} UNIDADES"
    largura_selo = fitz.get_text_length(selo, fontname="hebo", fontsize=11) + 20
    largura_disponivel = 271 - 12 - largura_selo
    for tamanho in (12, 11, 10, 9, 8, 7):
        linhas = _linhas_rodape_sku(sku, tamanho, largura_disponivel)
        entrelinha = tamanho + 1
        if (len(linhas) - 1) * entrelinha + tamanho * 1.38 <= 32:
            break

    larguras = [fitz.get_text_length(linha, fontname="hebo", fontsize=tamanho) for linha in linhas]
    largura_sku = max(larguras)
    x_sku = (283 - largura_sku - 12 - largura_selo) / 2
    x_selo = x_sku + largura_sku + 12
    y_sku = 411 - (len(linhas) - 1) * entrelinha / 2
    for indice, (linha, largura) in enumerate(zip(linhas, larguras)):
        pagina.insert_text(
            (x_sku + (largura_sku - largura) / 2, y_sku + indice * entrelinha),
            linha, fontname="hebo", fontsize=tamanho, color=cor,
        )
    pagina.draw_rect((x_selo, 396, x_selo + largura_selo, 418), color=None, fill=(0.19,) * 3, radius=0.2)
    pagina.insert_text((x_selo + 10, 411), selo, fontname="hebo", fontsize=11, color=(1, 1, 1))


def desenhar_sku_lateral(pagina, sku_text, *, loja, quantidade=None):
    shopee = loja == "Shopee"
    x, y, tamanho = (278.5, 350, 12) if shopee else (280.5, 300, 7.8)
    prefixo, separador, sufixo = sku_text.rpartition(" (x")
    sku = prefixo.rstrip() if separador else sku_text
    if not shopee and len(sku) > 30:
        sku = sku[:30] + "..."

    selo = None
    espaco = y - 4
    fonte = "helv"
    if quantidade is not None and int(quantidade) > 1:
        fonte = "hebo"
        selo = f"{int(quantidade)} {'UNIDADES' if shopee else 'UN.'}"
        tamanho_selo, margem = (10, 6) if shopee else (7.4, 5)
        comprimento_selo = fitz.get_text_length(selo, fontname=fonte, fontsize=tamanho_selo) + 2 * margem
        intervalo = 10 if shopee else 8
        espaco -= comprimento_selo + intervalo
    elif separador:
        sku += (" " if shopee else "  ") + "(x" + sufixo

    medida = fitz.Font(fontname=fonte).text_length
    largura = medida(sku, fontsize=tamanho)
    if largura > espaco:
        tamanho = espaco * tamanho / largura
        largura = espaco
    cor = (1, 0, 0) if sku_text == "SKU N/D" else (0, 0, 0)
    pagina.insert_text((x, y), sku, fontsize=tamanho, fontname=fonte, color=cor, rotate=90)

    if selo is not None:
        fim = y - largura - intervalo
        esquerda, direita = (268, 282) if shopee else (272, 282.7)
        pagina.draw_rect((esquerda, fim - comprimento_selo, direita, fim),
                         color=None, fill=(0.19,) * 3, radius=0.22)
        pagina.insert_text((278.8 if shopee else 280.1, fim - margem), selo, fontsize=tamanho_selo,
                           fontname=fonte, color=(1, 1, 1), rotate=90)


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
