"""Shared PDF drawing helpers for Etiquetas."""

from __future__ import annotations

import os
import re

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont


def _dimensoes_pagina_pdf(largura, altura, orientacao=None):
    """Resolve the selected page orientation, preserving legacy defaults."""
    if orientacao is None:
        return largura, altura
    if orientacao == "retrato":
        return min(largura, altura), max(largura, altura)
    if orientacao == "paisagem":
        return max(largura, altura), min(largura, altura)
    raise ValueError("Escolha a orientação da página: retrato ou paisagem.")


def _ajustar_texto_pdf(texto, fonte, tamanho_max, largura, altura, entrelinha=1.2):
    """Wrap complete text and find the largest font that fits its text box."""
    texto = str(texto or "").strip()
    if not texto:
        return [], tamanho_max
    if min(tamanho_max, largura, altura, entrelinha) <= 0:
        raise ValueError("A área de texto da etiqueta deve ser positiva.")
    paragrafos = [re.sub(r"[^\S\n]+", " ", linha).strip() for linha in texto.splitlines()]
    larguras = {char: stringWidth(char, fonte, 1) for char in set(texto + " ")}

    def quebrar(tamanho):
        limite = largura / tamanho
        linhas = []
        for paragrafo in paragrafos:
            atual = ""
            atual_w = 0.0
            for palavra in paragrafo.split():
                palavra_w = sum(larguras[char] for char in palavra)
                espaco_w = larguras[" "] if atual else 0
                if atual and atual_w + espaco_w + palavra_w > limite:
                    linhas.append(atual)
                    atual, atual_w = "", 0.0
                if palavra_w <= limite:
                    if atual:
                        atual += " "
                        atual_w += larguras[" "]
                    atual += palavra
                    atual_w += palavra_w
                else:
                    for char in palavra:
                        char_w = larguras[char]
                        if char_w > limite:
                            return None
                        if atual and atual_w + char_w > limite:
                            linhas.append(atual)
                            atual, atual_w = "", 0.0
                        atual += char
                        atual_w += char_w
            linhas.append(atual)
        return linhas

    teto = min(float(tamanho_max), altura / entrelinha)
    linhas = quebrar(teto)
    if linhas is not None and len(linhas) * teto * entrelinha <= altura:
        return linhas, teto
    minimo, maximo = 0.0, teto
    for _ in range(24):
        tamanho = (minimo + maximo) / 2
        linhas = quebrar(tamanho)
        if linhas is not None and len(linhas) * tamanho * entrelinha <= altura:
            minimo = tamanho
        else:
            maximo = tamanho
    return quebrar(minimo), minimo


def _quebrar_texto_pdf(texto, fonte, tamanho, largura_max, max_linhas):
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not texto:
        return []
    palavras = texto.split(" ")
    linhas = []
    atual = ""
    for palavra in palavras:
        tentativa = f"{atual} {palavra}".strip()
        if stringWidth(tentativa, fonte, tamanho) <= largura_max:
            atual = tentativa
            continue
        if atual:
            linhas.append(atual)
        atual = palavra
        if len(linhas) >= max_linhas:
            break
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    if len(linhas) == max_linhas and stringWidth(linhas[-1], fonte, tamanho) > largura_max:
        linha = linhas[-1]
        while linha and stringWidth(f"{linha}...", fonte, tamanho) > largura_max:
            linha = linha[:-1]
        linhas[-1] = f"{linha}..."
    return linhas


def _texto_item_avulso(valor):
    return re.sub(r"\s+", " ", str(valor or "")).strip()


def _normalizar_link_qrcode(valor):
    link = re.sub(r"\s+", "", str(valor or "").strip())
    if not link:
        raise ValueError("Informe o link para gerar o QR Code.")
    if len(link) > 2000:
        raise ValueError("O link informado e muito longo.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", link):
        link = f"https://{link}"
    return link


def _quebrar_texto_continuo_pdf(texto, fonte, tamanho, largura_max, max_linhas):
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not texto:
        return []
    linhas = []
    atual = ""
    for char in texto:
        tentativa = f"{atual}{char}"
        if stringWidth(tentativa, fonte, tamanho) <= largura_max or not atual:
            atual = tentativa
            continue
        linhas.append(atual)
        atual = char
        if len(linhas) >= max_linhas:
            break
    if atual and len(linhas) < max_linhas:
        linhas.append(atual)
    if len(linhas) == max_linhas and sum(len(linha) for linha in linhas) < len(texto):
        linha = linhas[-1]
        while linha and stringWidth(f"{linha}...", fonte, tamanho) > largura_max:
            linha = linha[:-1]
        linhas[-1] = f"{linha}..."
    return linhas


def _fontes_etiqueta_avulsa():
    normal = "Helvetica"
    negrito = "Helvetica-Bold"
    try:
        arial = r"C:\Windows\Fonts\arial.ttf"
        arial_bold = r"C:\Windows\Fonts\arialbd.ttf"
        if os.path.exists(arial):
            if "JKArial" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("JKArial", arial))
            normal = "JKArial"
        if os.path.exists(arial_bold):
            if "JKArial-Bold" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("JKArial-Bold", arial_bold))
            negrito = "JKArial-Bold"
    except Exception:
        pass
    return normal, negrito


def _desenhar_texto_centralizado(c, texto, y, fonte="Helvetica", tamanho=28, largura=0, sublinhar=False):
    texto = str(texto or "")
    c.setFont(fonte, tamanho)
    x = largura / 2
    c.drawCentredString(x, y, texto)
    if sublinhar and texto:
        texto_w = stringWidth(texto, fonte, tamanho)
        c.setLineWidth(1.4)
        c.line(x - (texto_w / 2), y - 2, x + (texto_w / 2), y - 2)
