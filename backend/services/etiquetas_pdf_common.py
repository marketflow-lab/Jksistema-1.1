"""Shared PDF drawing helpers for Etiquetas."""

from __future__ import annotations

import os
import re

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont

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
