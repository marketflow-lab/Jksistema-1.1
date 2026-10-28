"""QR Code label PDF generation for Etiquetas."""

from __future__ import annotations

import io
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from backend.services.etiquetas_pdf_common import (
    _ajustar_texto_pdf,
    _dimensoes_pagina_pdf,
    _fontes_etiqueta_avulsa,
    _normalizar_link_qrcode,
)

def _desenhar_qrcode_widget(c, link, x, y, tamanho):
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing

    qr_widget = qr.QrCodeWidget(link)
    bounds = qr_widget.getBounds()
    qr_w = bounds[2] - bounds[0]
    qr_h = bounds[3] - bounds[1]
    drawing = Drawing(tamanho, tamanho, transform=[tamanho / qr_w, 0, 0, tamanho / qr_h, 0, 0])
    drawing.add(qr_widget)
    renderPDF.draw(drawing, c, x, y)


def _desenhar_icone_celular_leitor(c, x, y, tamanho):
    largura = tamanho * 0.66
    altura = tamanho
    raio = min(1.5 * mm, tamanho * 0.18)
    brilho = tamanho * 0.08

    c.setStrokeColorRGB(0.16, 0.26, 0.40)
    c.setLineWidth(max(1.0, tamanho * 0.055))
    c.roundRect(x, y, largura, altura, raio, stroke=1, fill=0)

    c.setFillColorRGB(0.96, 0.99, 1.0)
    c.roundRect(
        x + brilho,
        y + brilho,
        largura - (2 * brilho),
        altura - (2 * brilho),
        raio / 1.5,
        stroke=0,
        fill=1,
    )

    c.setFillColorRGB(0.16, 0.26, 0.40)
    c.roundRect(
        x + brilho,
        y + brilho,
        largura - (2 * brilho),
        altura - (2 * brilho),
        raio / 1.5,
        stroke=0,
        fill=0,
    )

    c.setFillColorRGB(0.16, 0.26, 0.40)
    c.setLineWidth(max(0.8, tamanho * 0.038))
    c.circle(x + largura / 2, y + (altura * 0.5), tamanho * 0.08, stroke=1, fill=0)

    olho_x0 = x + (largura * 0.16)
    olho_x1 = x + (largura * 0.84)
    linha_y = y + (altura * 0.30)
    c.line(olho_x0, linha_y, olho_x1, linha_y)
    linha_y2 = y + (altura * 0.20)
    c.line(olho_x0 + (largura * 0.1), linha_y2, olho_x1 - (largura * 0.12), linha_y2)


def _desenhar_bloco_qrcode(c, texto, fonte, tamanho_max, x, y, w, h, centralizar=False):
    linhas, tamanho = _ajustar_texto_pdf(texto, fonte, tamanho_max, w, h, entrelinha=1.4)
    c.setFont(fonte, tamanho)
    baseline = y + h - (1.1 * tamanho)
    for linha in linhas:
        if centralizar:
            c.drawCentredString(x + w / 2, baseline, linha)
        else:
            c.drawString(x, baseline, linha)
        baseline -= tamanho * 1.4


def _desenhar_etiqueta_qrcode(c, link, titulo, x0, y0, label_w, label_h, idx, total, fonte_normal, fonte_negrito):
    c.saveState()
    c.translate(x0, y0)
    pad = min(6 * mm, min(label_w, label_h) * 0.055)
    pequena = min(label_w, label_h) <= 32 * mm
    if label_w >= label_h * 1.3:
        qr_size = min(label_h - 2 * pad, (label_w - 3 * pad) * 0.48)
        qr_x, qr_y = pad, (label_h - qr_size) / 2
        text_x, text_y = qr_x + qr_size + pad, pad
        text_w, text_h = label_w - text_x - pad, label_h - 2 * pad
    else:
        qr_size = min(label_w - 2 * pad, (label_h - 3 * pad) * 0.55)
        qr_x, qr_y = (label_w - qr_size) / 2, label_h - pad - qr_size
        text_x, text_y = pad, pad
        text_w, text_h = label_w - 2 * pad, qr_y - 2 * pad
    _desenhar_qrcode_widget(c, link, qr_x, qr_y, qr_size)

    titulo_final = str(titulo or "").strip()
    if titulo_final:
        gap = min(2 * mm, text_h * 0.06)
        titulo_h = (text_h - gap) * 0.48
        c.setFillColorRGB(0.04, 0.12, 0.24)
        _desenhar_bloco_qrcode(c, titulo_final, fonte_negrito, 7.2 if pequena else 27,
                              text_x, text_y + text_h - titulo_h, text_w, titulo_h)
        text_h -= titulo_h + gap
    c.setFillColorRGB(0.20, 0.25, 0.32)
    _desenhar_bloco_qrcode(c, link, fonte_normal, 5.3 if pequena else 14,
                          text_x, text_y, text_w, text_h)
    c.restoreState()


def _desenhar_qrcode_compacto(c, link, titulo, x0, y0, w, h, numero, total, fonte_normal, fonte_negrito):
    c.saveState()
    c.translate(x0, y0)
    pad = min(2 * mm, w * 0.05, h * 0.05)
    texto = str(titulo or "").strip()
    text_h = (h - 3 * pad) * 0.30 if texto else 0
    qr_size = min(w - 2 * pad, h - 2 * pad - text_h - (pad if texto else 0))
    qr_x, qr_y = (w - qr_size) / 2, h - pad - qr_size
    _desenhar_qrcode_widget(c, link, qr_x, qr_y, qr_size)
    if texto:
        c.setFillColorRGB(0.04, 0.12, 0.24)
        icon_size = min(8 * mm, text_h * 0.65, w * 0.15)
        text_x = pad + icon_size + pad
        _desenhar_icone_celular_leitor(c, pad, pad + (text_h - icon_size) / 2, icon_size)
        _desenhar_bloco_qrcode(c, texto, fonte_negrito, 12, text_x, pad,
                              w - text_x - pad, text_h, centralizar=True)
    c.restoreState()


def _gerar_pdf_qrcode_grade_15x8(link, titulo, quantidade, fonte_normal, fonte_negrito, orientacao=None):
    page_w, page_h = _dimensoes_pagina_pdf(150 * mm, 100 * mm, orientacao)
    margem = 5 * mm
    gap = 3 * mm
    max_por_etiqueta = 8
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for inicio in range(0, quantidade, max_por_etiqueta):
        if inicio:
            c.showPage()
        qtd_pagina = min(max_por_etiqueta, quantidade - inicio)
        if qtd_pagina <= 2:
            cols, rows = qtd_pagina, 1
        elif qtd_pagina <= 4:
            cols, rows = 2, 2
        elif qtd_pagina <= 6:
            cols, rows = 3, 2
        else:
            cols, rows = 4, 2
        if page_w < page_h:
            cols, rows = rows, cols

        area_x = margem
        area_y = margem
        area_w = page_w - (2 * margem)
        area_h = page_h - (2 * margem)
        cell_w = (area_w - ((cols - 1) * gap)) / cols
        cell_h = (area_h - ((rows - 1) * gap)) / rows

        for pos in range(qtd_pagina):
            row = pos // cols
            col = pos % cols
            x = area_x + (col * (cell_w + gap))
            y = area_y + ((rows - 1 - row) * (cell_h + gap))
            numero = inicio + pos + 1
            _desenhar_qrcode_compacto(c, link, titulo, x, y, cell_w, cell_h, numero, quantidade, fonte_normal, fonte_negrito)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), quantidade


def _desenhar_qrcode_mini(c, link, titulo, x0, y0, w, h, numero, total, fonte_normal, fonte_negrito):
    _desenhar_etiqueta_qrcode(c, link, titulo or "QR Code", x0, y0, w, h,
                             numero, total, fonte_normal, fonte_negrito)


def _gerar_pdf_qrcode_grade_pequena(link, titulo, quantidade, fonte_normal, fonte_negrito, orientacao=None):
    page_w, page_h = _dimensoes_pagina_pdf(80 * mm, 25 * mm, orientacao)
    margem = 0.5 * mm
    gap = 1 * mm
    max_por_etiqueta = 2
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for inicio in range(0, quantidade, max_por_etiqueta):
        if inicio:
            c.showPage()
        qtd_pagina = min(max_por_etiqueta, quantidade - inicio)
        cols, rows = (qtd_pagina, 1) if page_w > page_h else (1, qtd_pagina)
        cell_w = (page_w - (2 * margem) - ((cols - 1) * gap)) / cols
        cell_h = (page_h - (2 * margem) - ((rows - 1) * gap)) / rows

        for pos in range(qtd_pagina):
            x = margem + ((pos % cols) * (cell_w + gap))
            y = margem + ((rows - 1 - pos // cols) * (cell_h + gap))
            numero = inicio + pos + 1
            _desenhar_qrcode_mini(c, link, titulo, x, y, cell_w, cell_h, numero, quantidade, fonte_normal, fonte_negrito)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), quantidade


def gerar_pdf_qrcode_link(link, titulo="", quantidade=1, tamanho="grande", layout="a4", orientacao=None):
    link_normalizado = _normalizar_link_qrcode(link)
    titulo = re.sub(r"\s+", " ", str(titulo or "").strip())[:120]
    quantidade = max(1, min(int(quantidade or 1), 500))
    tamanho = str(tamanho or "grande").strip().lower()
    tamanho = "pequena" if tamanho.startswith("p") else "grande"
    layout = str(layout or "a4").strip().lower()
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    if tamanho == "grande" and layout in {"grade_15x8", "15x8", "varios_15x8", "multiplos_15x8"}:
        return _gerar_pdf_qrcode_grade_15x8(link_normalizado, titulo, quantidade, fonte_normal, fonte_negrito, orientacao)
    if tamanho == "pequena" and layout in {"grade_8x25", "8x25", "varios_8x25", "multiplos_8x25"}:
        return _gerar_pdf_qrcode_grade_pequena(link_normalizado, titulo, quantidade, fonte_normal, fonte_negrito, orientacao)

    if tamanho == "pequena":
        label_w, label_h = 80 * mm, 25 * mm
    else:
        label_w, label_h = 150 * mm, 100 * mm

    usa_a4 = layout in {"a4", "folha", "folha_a4", "varias", "multipla", "multiplas"}
    if usa_a4:
        page_w, page_h = _dimensoes_pagina_pdf(*A4, orientacao)
        margem = 8 * mm
        gap = 3 * mm
        cols = max(1, int((page_w - (2 * margem) + gap) // (label_w + gap)))
        rows = max(1, int((page_h - (2 * margem) + gap) // (label_h + gap)))
        por_pagina = max(1, cols * rows)
        total_w = (cols * label_w) + ((cols - 1) * gap)
        total_h = (rows * label_h) + ((rows - 1) * gap)
        start_x = (page_w - total_w) / 2
        start_y = (page_h - total_h) / 2
    else:
        label_w, label_h = _dimensoes_pagina_pdf(label_w, label_h, orientacao)
        page_w, page_h = label_w, label_h
        cols = rows = por_pagina = 1
        gap = 0
        start_x = start_y = 0

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for idx in range(quantidade):
        if idx and idx % por_pagina == 0:
            c.showPage()
        pos = idx % por_pagina
        row = pos // cols
        col = pos % cols
        x = start_x + (col * (label_w + gap))
        y = start_y + ((rows - 1 - row) * (label_h + gap))
        _desenhar_etiqueta_qrcode(
            c,
            link_normalizado,
            titulo,
            x,
            y,
            label_w,
            label_h,
            idx,
            quantidade,
            fonte_normal,
            fonte_negrito,
        )

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), quantidade
