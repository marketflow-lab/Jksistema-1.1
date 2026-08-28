"""Avulso and free-editor label PDF generation for Etiquetas."""

from __future__ import annotations

import io
import re

from bs4 import BeautifulSoup
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from backend.services.etiquetas_pdf_common import (
    _desenhar_texto_centralizado,
    _fontes_etiqueta_avulsa,
    _quebrar_texto_continuo_pdf,
    _quebrar_texto_pdf,
    _texto_item_avulso,
)

def _gerar_pdf_impressao_avulsa_grande(itens):
    etiquetas = []
    for item in itens:
        dados = {
            "sequencia": _texto_item_avulso(item.sequencia),
            "sku": _texto_item_avulso(item.sku),
            "quantidade": _texto_item_avulso(item.quantidade),
            "caixa": _texto_item_avulso(item.caixa),
            "torre": _texto_item_avulso(item.torre),
            "paletes": _texto_item_avulso(item.paletes),
            "corredor": _texto_item_avulso(item.corredor),
            "conta": _texto_item_avulso(item.conta),
        }
        if any(dados.values()):
            etiquetas.append(dados)

    if not etiquetas:
        return None, 0
    if len(etiquetas) > 1000:
        raise ValueError("Limite mÃƒÂ¡ximo de 1000 etiquetas por PDF avulso.")

    page_w = 150 * mm
    page_h = 80 * mm
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    for idx, etiqueta in enumerate(etiquetas):
        if idx:
            c.showPage()

        sku = etiqueta["sku"]
        sequencia = etiqueta["sequencia"]
        quantidade = etiqueta["quantidade"]
        torre = etiqueta["torre"]
        paletes = etiqueta["paletes"]
        corredor = etiqueta["corredor"]
        caixa = etiqueta["caixa"]
        conta = etiqueta["conta"]

        _desenhar_texto_centralizado(c, f"SKU: {sku}" if sku else "SKU:", 63 * mm, fonte_negrito, 34, page_w)

        c.setFont(fonte_normal, 32)
        linha_y = 42 * mm
        x_seq = page_w * 0.34
        x_qtd = page_w * 0.64
        c.drawCentredString(x_seq, linha_y, sequencia)
        if sequencia:
            seq_w = stringWidth(sequencia, fonte_normal, 32)
            c.setLineWidth(1.4)
            c.line(x_seq - seq_w / 2, linha_y - 2, x_seq + seq_w / 2, linha_y - 2)
        c.drawCentredString(x_qtd, linha_y, f"QTD:{quantidade}" if quantidade else "QTD:")

        partes_local = []
        if torre:
            partes_local.append(f"T{torre}")
        if paletes:
            partes_local.append(f"P{paletes}")
        if corredor:
            partes_local.append(f"C{corredor}")
        if caixa:
            partes_local.append(f"CX{caixa}")
        _desenhar_texto_centralizado(c, " ".join(partes_local), 24 * mm, fonte_normal, 32, page_w)
        _desenhar_texto_centralizado(c, conta, 9 * mm, fonte_normal, 32, page_w)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), len(etiquetas)


def _gerar_pdf_impressao_avulsa_pequena(itens):
    etiquetas = []
    for item in itens:
        dados = {
            "sequencia": _texto_item_avulso(item.sequencia),
            "sku": _texto_item_avulso(item.sku),
            "quantidade": _texto_item_avulso(item.quantidade),
            "caixa": _texto_item_avulso(item.caixa),
            "torre": _texto_item_avulso(item.torre),
            "paletes": _texto_item_avulso(item.paletes),
            "corredor": _texto_item_avulso(item.corredor),
            "conta": _texto_item_avulso(item.conta),
        }
        if any(dados.values()):
            etiquetas.append(dados)

    if not etiquetas:
        return None, 0
    if len(etiquetas) > 1000:
        raise ValueError("Limite mÃƒÂ¡ximo de 1000 etiquetas por PDF avulso.")

    page_w = 80 * mm
    page_h = 25 * mm
    label_w = 40 * mm
    label_h = 25 * mm
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    def draw_center(texto, x0, y, fonte, tamanho):
        c.setFont(fonte, tamanho)
        c.drawCentredString(x0 + (label_w / 2), y, str(texto or ""))

    for idx, etiqueta in enumerate(etiquetas):
        pos = idx % 2
        if idx and pos == 0:
            c.showPage()
        x0 = pos * label_w

        c.setStrokeColorRGB(0.86, 0.86, 0.86)
        c.rect(x0, 0, label_w, label_h, stroke=1, fill=0)
        if pos == 0:
            c.setDash(1, 2)
            c.line(label_w, 0, label_w, page_h)
            c.setDash()

        sku = etiqueta["sku"]
        sequencia = etiqueta["sequencia"]
        quantidade = etiqueta["quantidade"]
        torre = etiqueta["torre"]
        paletes = etiqueta["paletes"]
        corredor = etiqueta["corredor"]
        caixa = etiqueta["caixa"]
        conta = etiqueta["conta"]

        # Mantem a mesma hierarquia visual da etiqueta grande, reduzida para 4 x 2,5 cm.
        draw_center(f"SKU: {sku}" if sku else "SKU:", x0, 18.9 * mm, fonte_negrito, 10.6)

        c.setFont(fonte_normal, 9.8)
        linha_y = 12.9 * mm
        x_seq = x0 + (label_w * 0.34)
        x_qtd = x0 + (label_w * 0.64)
        c.drawCentredString(x_seq, linha_y, sequencia)
        if sequencia:
            seq_w = stringWidth(sequencia, fonte_normal, 9.8)
            c.setLineWidth(0.5)
            c.line(x_seq - seq_w / 2, linha_y - 1, x_seq + seq_w / 2, linha_y - 1)
        c.drawCentredString(x_qtd, linha_y, f"QTD:{quantidade}" if quantidade else "QTD:")

        partes_local = []
        if torre:
            partes_local.append(f"T{torre}")
        if paletes:
            partes_local.append(f"P{paletes}")
        if corredor:
            partes_local.append(f"C{corredor}")
        if caixa:
            partes_local.append(f"CX{caixa}")
        draw_center(" ".join(partes_local), x0, 7.2 * mm, fonte_normal, 9.8)
        draw_center(conta, x0, 2.4 * mm, fonte_normal, 9.8)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), len(etiquetas)


def gerar_pdf_impressao_avulsa(itens, formato="grande"):
    formato = str(formato or "grande").strip().lower()
    usa_layout_planilha = any(bool(getattr(item, "layout_planilha", False)) for item in itens)
    if formato == "grande":
        return _gerar_pdf_impressao_avulsa_grande(itens)
    if formato == "pequena" or usa_layout_planilha:
        return _gerar_pdf_impressao_avulsa_pequena(itens)

    etiquetas = []
    for item in itens:
        sku = (item.sku or "").strip()
        descricao = (item.descricao or "").strip()
        quantidade = max(1, min(int(item.quantidade or 1), 500))
        if not sku and not descricao:
            continue
        etiquetas.extend([{"sku": sku, "descricao": descricao}] * quantidade)

    if not etiquetas:
        return None, 0
    if len(etiquetas) > 1000:
        raise ValueError("Limite mÃƒÂ¡ximo de 1000 etiquetas por PDF avulso.")

    formato = str(formato or "grande").strip().lower()
    if formato not in {"grande", "pequena"}:
        formato = "grande"

    etiqueta_w = 80 * mm
    etiqueta_h = 25 * mm if formato == "pequena" else 150 * mm
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(etiqueta_w, etiqueta_h))

    for idx, etiqueta in enumerate(etiquetas):
        if idx:
            c.showPage()
        x = 0
        y = 0

        c.setStrokeColorRGB(0.80, 0.86, 0.92)
        c.roundRect(x, y, etiqueta_w, etiqueta_h, 4, stroke=1, fill=0)

        pad = 3 * mm if formato == "pequena" else 6 * mm
        text_x = x + pad
        top_y = y + etiqueta_h - pad
        largura_texto = etiqueta_w - (2 * pad)

        sku = etiqueta["sku"]
        descricao = etiqueta["descricao"]
        if sku:
            fonte_sku = 13 if formato == "pequena" else 18
            max_linhas_sku = 1 if formato == "pequena" else 3
            c.setFont("Helvetica-Bold", fonte_sku)
            for linha in _quebrar_texto_pdf(sku, "Helvetica-Bold", fonte_sku, largura_texto, max_linhas_sku):
                c.drawString(text_x, top_y, linha)
                top_y -= (5 * mm if formato == "pequena" else 8 * mm)

        if descricao:
            top_y -= 1 * mm
            fonte_desc = 8 if formato == "pequena" else 12
            max_linhas_desc = 2 if formato == "pequena" else 12
            c.setFont("Helvetica", fonte_desc)
            for linha in _quebrar_texto_pdf(descricao, "Helvetica", fonte_desc, largura_texto, max_linhas_desc):
                if top_y <= (5 * mm if formato == "pequena" else 12 * mm):
                    break
                c.drawString(text_x, top_y, linha)
                top_y -= (3.5 * mm if formato == "pequena" else 5.5 * mm)

        c.setFont("Helvetica", 6 if formato == "pequena" else 8)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawRightString(x + etiqueta_w - pad, y + (2 * mm if formato == "pequena" else 4 * mm), f"#{idx + 1}")
        c.setFillColorRGB(0, 0, 0)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), len(etiquetas)


def gerar_pdf_editor_livre(etiquetas_html, tamanho="grande"):
    etiquetas = [str(html or "").strip() for html in etiquetas_html if str(html or "").strip()]
    if not etiquetas:
        return None, 0
    if len(etiquetas) > 100:
        raise ValueError("Limite mÃƒÂ¡ximo de 100 etiquetas livres por PDF.")

    tamanho = str(tamanho or "grande").strip().lower()
    tamanho = "pequena" if tamanho.startswith("p") else "grande"

    def _sanitize_editor_html(html):
        html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"\son\w+\s*=\s*(['\"]).*?\1", "", html, flags=re.IGNORECASE)
        return html

    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    def _style_value_px(tag, prop, default_px):
        for node in [tag] + list(tag.find_all(True)):
            style = str(node.get("style") or "")
            match = re.search(rf"{prop}\s*:\s*([\d.]+)px", style, flags=re.IGNORECASE)
            if match:
                return max(6, min(96, float(match.group(1))))
        return default_px

    def _line_align(tag):
        style = str(tag.get("style") or "")
        match = re.search(r"text-align\s*:\s*(left|center|right)", style, flags=re.IGNORECASE)
        return (match.group(1).lower() if match else "center")

    def _draw_editor_label(c, html, x0, y0, w, h, scale=1.0):
        soup = BeautifulSoup(_sanitize_editor_html(html), "html.parser")
        linhas = [node for node in soup.find_all(["div", "p"], recursive=False)]
        if not linhas:
            linhas = [soup]

        css_w = 720.0
        css_h = 384.0
        css_padding = 18.0
        point_per_px = min(w / css_w, h / css_h)
        draw_w = css_w * point_per_px
        draw_h = css_h * point_per_px
        offset_x = (w - draw_w) / 2
        offset_y = (h - draw_h) / 2

        c.saveState()
        c.translate(x0, y0)
        c.scale(scale, scale)
        default_px = 36.0
        cursor_y_px = css_padding
        for idx, linha in enumerate(linhas[:40]):
            font_size_px = _style_value_px(linha, "font-size", default_px)
            line_height_px = max(font_size_px * 1.35, _style_value_px(linha, "line-height", font_size_px * 1.35))
            texto = linha.get_text(" ", strip=True)
            if not texto:
                cursor_y_px += line_height_px
                continue
            bold = bool(linha.find(["b", "strong"])) or "font-weight: bold" in str(linha).lower()
            underline = bool(linha.find("u")) or "text-decoration-line: underline" in str(linha).lower() or "text-decoration: underline" in str(linha).lower()
            font_name = fonte_negrito if bold else fonte_normal
            font_size = font_size_px * point_per_px
            baseline_px = cursor_y_px + ((line_height_px - font_size_px) / 2) + (font_size_px * 0.82)
            y = offset_y + draw_h - (baseline_px * point_per_px)
            align = _line_align(linha)
            c.setFont(font_name, font_size)
            text_w = stringWidth(texto, font_name, font_size)
            if align == "left":
                x = offset_x + (css_padding * point_per_px)
                c.drawString(x, y, texto)
            elif align == "right":
                x = offset_x + draw_w - (css_padding * point_per_px)
                c.drawRightString(x, y, texto)
                x = x - text_w
            else:
                x_center = offset_x + (draw_w / 2)
                c.drawCentredString(x_center, y, texto)
                x = x_center - (text_w / 2)
            if underline:
                c.setLineWidth(max(0.5, 1.2 * point_per_px))
                c.line(x, y - (3 * point_per_px), x + text_w, y - (3 * point_per_px))
            cursor_y_px += line_height_px
        c.restoreState()

    if tamanho == "pequena":
        page_w, page_h = 80 * mm, 25 * mm
        label_w, label_h = 40 * mm, 25 * mm
    else:
        page_w, page_h = 150 * mm, 80 * mm
        label_w, label_h = page_w, page_h

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))

    for idx, html in enumerate(etiquetas):
        if tamanho == "pequena":
            pos = idx % 2
            if idx and pos == 0:
                c.showPage()
            x0 = pos * label_w
            c.setStrokeColorRGB(0.86, 0.86, 0.86)
            c.rect(x0, 0, label_w, label_h, stroke=1, fill=0)
            if pos == 0:
                c.setDash(1, 2)
                c.line(label_w, 0, label_w, page_h)
                c.setDash()
            scale = min(label_w / (150 * mm), label_h / (80 * mm))
            _draw_editor_label(c, html, x0, 0, 150 * mm, 80 * mm, scale)
        else:
            if idx:
                c.showPage()
            _draw_editor_label(c, html, 0, 0, page_w, page_h, 1.0)

    c.save()
    buffer.seek(0)
    return buffer.getvalue(), len(etiquetas)
