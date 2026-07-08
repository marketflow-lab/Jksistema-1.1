"""QR Code label PDF generation for Etiquetas."""

from __future__ import annotations

import io
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from backend.services.etiquetas_pdf_common import (
    _fontes_etiqueta_avulsa,
    _normalizar_link_qrcode,
    _quebrar_texto_continuo_pdf,
    _quebrar_texto_pdf,
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


def _desenhar_etiqueta_qrcode(c, link, titulo, x0, y0, label_w, label_h, idx, total, fonte_normal, fonte_negrito):
    c.saveState()
    c.translate(x0, y0)

    pequena = label_h <= 32 * mm
    pad = 2.5 * mm if pequena else 6 * mm
    raio = 3.5 if pequena else 7

    c.setFillColorRGB(1, 1, 1)
    if pequena:
        pad = 0.05 * mm
        qr_size = label_h - (2 * pad)
        qr_x = pad
        qr_y = (label_h - qr_size) / 2
        text_x = qr_x + qr_size + (0.8 * mm)
        text_w = label_w - text_x - pad

        _desenhar_qrcode_widget(c, link, qr_x, qr_y, qr_size)

        titulo_final = str(titulo or "").strip()
        y = label_h - pad - 3.2 * mm
        if titulo_final:
            c.setFillColorRGB(0.04, 0.12, 0.24)
            c.setFont(fonte_negrito, 7.2)
            for linha in _quebrar_texto_pdf(titulo_final, fonte_negrito, 7.2, text_w, 1):
                c.drawString(text_x, y, linha)
                y -= 3.6 * mm
            y -= 0.8 * mm
        c.setFillColorRGB(0.20, 0.25, 0.32)
        c.setFont(fonte_normal, 5.3)
        for linha in _quebrar_texto_continuo_pdf(link, fonte_normal, 5.3, text_w, 3):
            if y < pad:
                break
            c.drawString(text_x, y, linha)
            y -= 2.9 * mm
    else:
        qr_box = min(label_h - (2.2 * pad), label_w * 0.45)
        qr_x = pad
        qr_y = (label_h - qr_box) / 2
        qr_size = qr_box - (5 * mm)
        _desenhar_qrcode_widget(c, link, qr_x + 2.5 * mm, qr_y + 2.5 * mm, qr_size)

        text_x = qr_x + qr_box + 6 * mm
        text_w = label_w - text_x - pad
        if text_w > 20 * mm:
            icone_tamanho = 11 * mm
            icon_x = text_x
            icon_y = label_h - pad - icone_tamanho - 1 * mm
            _desenhar_icone_celular_leitor(c, icon_x, icon_y, icone_tamanho)
            text_x += icone_tamanho + 2.0 * mm
            text_w = text_w - (icone_tamanho + 2.0 * mm)
        y = label_h - pad - 10 * mm
        titulo_final = str(titulo or "").strip()
        if titulo_final:
            c.setFillColorRGB(0.04, 0.12, 0.24)
            c.setFont(fonte_negrito, 27)
            for linha in _quebrar_texto_pdf(titulo_final, fonte_negrito, 27, text_w, 2):
                c.drawString(text_x, y, linha)
                y -= 11 * mm
            y -= 4 * mm

        c.setFillColorRGB(0.20, 0.25, 0.32)
        c.setFont(fonte_normal, 14)
        for linha in _quebrar_texto_continuo_pdf(link, fonte_normal, 14, text_w, 4):
            if y < pad + 6 * mm:
                break
            c.drawString(text_x, y, linha)
            y -= 6.5 * mm

    c.setFillColorRGB(0, 0, 0)
    c.restoreState()


def _desenhar_qrcode_compacto(c, link, titulo, x0, y0, w, h, numero, total, fonte_normal, fonte_negrito):
    c.saveState()
    c.translate(x0, y0)
    pad = 2 * mm

    qr_size = min(w - (2 * pad), h - (20 * mm))
    qr_size = max(16 * mm, qr_size)
    qr_x = (w - qr_size) / 2
    qr_y = h - pad - qr_size

    _desenhar_qrcode_widget(c, link, qr_x, qr_y, qr_size)

    texto = str(titulo or "").strip()
    if texto:
        c.setFillColorRGB(0.04, 0.12, 0.24)
        fonte_titulo = 12
        icon_size = 8 * mm
        icon_gap = 2 * mm
        y = qr_y - 5.5 * mm
        linhas = _quebrar_texto_pdf(texto, fonte_negrito, fonte_titulo, w - (2 * pad) - icon_size - icon_gap, 2)
        largura_texto = max((c.stringWidth(linha, fonte_negrito, fonte_titulo) for linha in linhas), default=0)
        largura_total = icon_size + icon_gap + largura_texto
        start_x = max(pad, (w - largura_total) / 2)
        _desenhar_icone_celular_leitor(c, start_x, y - (icon_size * 0.45), icon_size)
        text_x = start_x + icon_size + icon_gap
        for linha in linhas:
            c.setFillColorRGB(0.04, 0.12, 0.24)
            c.setFont(fonte_negrito, fonte_titulo)
            c.drawString(text_x, y, linha)
            y -= 7 * mm
    c.setFillColorRGB(0, 0, 0)
    c.restoreState()


def _gerar_pdf_qrcode_grade_15x8(link, titulo, quantidade, fonte_normal, fonte_negrito):
    page_w, page_h = 150 * mm, 100 * mm
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
    c.saveState()
    c.translate(x0, y0)
    pad = 0.05 * mm
    titulo_txt = str(titulo or "").strip()

    qr_size = min(h - (2 * pad), w * 0.82)
    qr_x = pad
    qr_y = (h - qr_size) / 2

    _desenhar_qrcode_widget(c, link, qr_x, qr_y, qr_size)

    text_x = qr_x + qr_size + (0.35 * mm)
    text_w = w - text_x - pad
    y = h - pad - 3.6 * mm
    c.setFillColorRGB(0.04, 0.12, 0.24)
    c.setFont(fonte_negrito, 5.0)
    texto_base = titulo_txt or "QR Code"
    for linha in _quebrar_texto_pdf(texto_base, fonte_negrito, 5.0, text_w, 3):
        if y < pad + 5 * mm:
            break
        c.drawString(text_x, y, linha)
        y -= 3 * mm

    c.setFillColorRGB(0.20, 0.25, 0.32)
    c.setFont(fonte_normal, 3.9)
    for linha in _quebrar_texto_continuo_pdf(link, fonte_normal, 3.9, text_w, 2):
        if y < pad + 2 * mm:
            break
        c.drawString(text_x, y, linha)
        y -= 2.2 * mm

    c.setFillColorRGB(0, 0, 0)
    c.restoreState()


def _gerar_pdf_qrcode_grade_pequena(link, titulo, quantidade, fonte_normal, fonte_negrito):
    page_w, page_h = 80 * mm, 25 * mm
    margem = 0.5 * mm
    gap = 1 * mm
    max_por_etiqueta = 2
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for inicio in range(0, quantidade, max_por_etiqueta):
        if inicio:
            c.showPage()
        qtd_pagina = min(max_por_etiqueta, quantidade - inicio)
        cols = max(1, qtd_pagina)
        cell_w = (page_w - (2 * margem) - ((cols - 1) * gap)) / cols
        cell_h = page_h - (2 * margem)

        for pos in range(qtd_pagina):
            x = margem + (pos * (cell_w + gap))
            y = margem
            numero = inicio + pos + 1
            _desenhar_qrcode_mini(c, link, titulo, x, y, cell_w, cell_h, numero, quantidade, fonte_normal, fonte_negrito)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), quantidade


def gerar_pdf_qrcode_link(link, titulo="", quantidade=1, tamanho="grande", layout="a4"):
    link_normalizado = _normalizar_link_qrcode(link)
    titulo = re.sub(r"\s+", " ", str(titulo or "").strip())[:120]
    quantidade = max(1, min(int(quantidade or 1), 500))
    tamanho = str(tamanho or "grande").strip().lower()
    tamanho = "pequena" if tamanho.startswith("p") else "grande"
    layout = str(layout or "a4").strip().lower()
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    if tamanho == "grande" and layout in {"grade_15x8", "15x8", "varios_15x8", "multiplos_15x8"}:
        return _gerar_pdf_qrcode_grade_15x8(link_normalizado, titulo, quantidade, fonte_normal, fonte_negrito)
    if tamanho == "pequena" and layout in {"grade_8x25", "8x25", "varios_8x25", "multiplos_8x25"}:
        return _gerar_pdf_qrcode_grade_pequena(link_normalizado, titulo, quantidade, fonte_normal, fonte_negrito)

    if tamanho == "pequena":
        label_w, label_h = 80 * mm, 25 * mm
    else:
        label_w, label_h = 150 * mm, 100 * mm

    usa_a4 = layout in {"a4", "folha", "folha_a4", "varias", "multipla", "multiplas"}
    if usa_a4:
        page_w, page_h = A4
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
