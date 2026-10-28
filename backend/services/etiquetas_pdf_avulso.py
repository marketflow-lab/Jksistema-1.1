"""Avulso and free-editor label PDF generation for Etiquetas."""

from __future__ import annotations

import io
import re

from bs4 import BeautifulSoup, NavigableString
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import getAscentDescent, stringWidth
from reportlab.pdfgen import canvas

from backend.services.etiquetas_pdf_common import (
    _ajustar_texto_pdf,
    _dimensoes_pagina_pdf,
    _fontes_etiqueta_avulsa,
    _texto_item_avulso,
)


def _desenhar_bloco_avulso(c, texto, x, y, largura, altura, fonte, tamanho,
                          sublinhar=False, uma_linha=False):
    if uma_linha:
        texto = _texto_item_avulso(texto)
        if not texto:
            return
        tamanho = min(tamanho, largura / stringWidth(texto, fonte, 1), altura / 1.4)
        linhas = [texto]
    else:
        linhas, tamanho = _ajustar_texto_pdf(texto, fonte, tamanho, largura, altura, entrelinha=1.4)
    if not linhas:
        return
    entrelinha = tamanho * 1.4
    ascendente, descendente = getAscentDescent(fonte, tamanho)
    altura_texto = ascendente - descendente + (len(linhas) - 1) * entrelinha
    baseline = y + (altura + altura_texto) / 2 - ascendente
    c.setFont(fonte, tamanho)
    for linha in linhas:
        c.drawCentredString(x + largura / 2, baseline, linha)
        if sublinhar:
            texto_w = stringWidth(linha, fonte, tamanho)
            c.setLineWidth(max(0.2, tamanho * 0.035))
            c.line(x + (largura - texto_w) / 2, baseline - tamanho * 0.08,
                   x + (largura + texto_w) / 2, baseline - tamanho * 0.08)
        baseline -= entrelinha


def _dados_etiquetas_avulsas(itens):
    campos = ("sequencia", "sku", "quantidade", "caixa", "torre", "paletes", "corredor", "conta")
    etiquetas = []
    for item in itens:
        dados = {campo: _texto_item_avulso(getattr(item, campo)) for campo in campos}
        if any(dados.values()):
            etiquetas.append(dados)
    if len(etiquetas) > 1000:
        raise ValueError("Limite máximo de 1000 etiquetas por PDF avulso.")
    return etiquetas


def _geometria_avulsa(pequena, orientacao):
    dimensoes = (80 * mm, 25 * mm) if pequena else (150 * mm, 80 * mm)
    page_w, page_h = _dimensoes_pagina_pdf(*dimensoes, orientacao)
    empilhadas = pequena and page_h > page_w
    label_w = page_w if empilhadas or not pequena else page_w / 2
    label_h = page_h / 2 if empilhadas else page_h
    return page_w, page_h, label_w, label_h, empilhadas


def _posicao_avulsa(indice, label_w, label_h, empilhadas):
    if empilhadas:
        return 0, (1 - indice % 2) * label_h
    return (indice % 2) * label_w, 0


def _desenhar_borda_avulsa(c, x, y, largura, altura):
    c.setStrokeColorRGB(0.86, 0.86, 0.86)
    c.rect(x, y, largura, altura, stroke=1, fill=0)


def _gerar_pdf_campos_avulsos(itens, pequena=False, orientacao=None):
    page_w, page_h, label_w, label_h, empilhadas = _geometria_avulsa(pequena, orientacao)
    etiquetas = _dados_etiquetas_avulsas(itens)
    if not etiquetas:
        return None, 0
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()
    por_pagina = 2 if pequena else 1
    pad = (0.8 if pequena else 4) * mm
    gap = (0.6 if pequena else 2) * mm
    largura = label_w - 2 * pad
    altura_linha = (label_h - 2 * pad - 3 * gap) / 4

    for idx, etiqueta in enumerate(etiquetas):
        if idx and idx % por_pagina == 0:
            c.showPage()
        x0, y0 = _posicao_avulsa(idx, label_w, label_h, empilhadas) if pequena else (0, 0)
        if pequena:
            _desenhar_borda_avulsa(c, x0, y0, label_w, label_h)
        tamanho_sku = 10.6 if pequena else 34
        tamanho_texto = 9.8 if pequena else 32

        def bloco(texto, linha, fonte=fonte_normal, tamanho=tamanho_texto,
                  x=0, w=largura, sublinhar=False, uma_linha=False):
            y = y0 + label_h - pad - (linha + 1) * altura_linha - linha * gap
            _desenhar_bloco_avulso(c, texto, x0 + pad + x, y, w, altura_linha,
                                   fonte, tamanho, sublinhar, uma_linha)

        bloco(f"SKU: {etiqueta['sku']}", 0, fonte_negrito, tamanho_sku)
        metade = (largura - gap) / 2
        bloco(etiqueta["sequencia"], 1, w=metade, sublinhar=True, uma_linha=True)
        bloco(f"QTD:{etiqueta['quantidade']}", 1, x=metade + gap, w=metade, uma_linha=True)
        partes_local = [f"{prefixo}{etiqueta[campo]}"
                        for campo, prefixo in (("torre", "T"), ("paletes", "P"),
                                               ("corredor", "C"), ("caixa", "CX"))
                        if etiqueta[campo]]
        bloco(" ".join(partes_local), 2)
        bloco(etiqueta["conta"], 3)

    c.save()
    return buffer.getvalue(), len(etiquetas)


def _gerar_pdf_impressao_avulsa_grande(itens, orientacao=None):
    return _gerar_pdf_campos_avulsos(itens, orientacao=orientacao)


def _gerar_pdf_impressao_avulsa_pequena(itens, orientacao=None):
    return _gerar_pdf_campos_avulsos(itens, pequena=True, orientacao=orientacao)


def gerar_pdf_impressao_avulsa(itens, formato="grande", orientacao=None):
    formato = str(formato or "grande").strip().lower()
    usa_layout_planilha = any(bool(getattr(item, "layout_planilha", False)) for item in itens)
    if formato == "grande":
        return _gerar_pdf_impressao_avulsa_grande(itens, orientacao)
    if formato == "pequena" or usa_layout_planilha:
        return _gerar_pdf_impressao_avulsa_pequena(itens, orientacao)

    etiquetas = []
    for item in itens:
        sku = (item.sku or "").strip()
        descricao = (item.descricao or "").strip()
        quantidade = max(1, min(int(item.quantidade or 1), 500))
        if sku or descricao:
            etiquetas.extend([{"sku": sku, "descricao": descricao}] * quantidade)
    if not etiquetas:
        return None, 0
    if len(etiquetas) > 1000:
        raise ValueError("Limite máximo de 1000 etiquetas por PDF avulso.")

    etiqueta_w, etiqueta_h = _dimensoes_pagina_pdf(80 * mm, 150 * mm, orientacao)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(etiqueta_w, etiqueta_h))
    pad = 6 * mm
    largura = etiqueta_w - 2 * pad
    altura = etiqueta_h - 2 * pad
    for idx, etiqueta in enumerate(etiquetas):
        if idx:
            c.showPage()
        c.setStrokeColorRGB(0.80, 0.86, 0.92)
        c.roundRect(0, 0, etiqueta_w, etiqueta_h, 4, stroke=1, fill=0)
        altura_sku = altura * 0.25 if etiqueta["sku"] else 0
        if etiqueta["sku"]:
            _desenhar_bloco_avulso(c, etiqueta["sku"], pad,
                                   etiqueta_h - pad - altura_sku, largura, altura_sku,
                                   "Helvetica-Bold", 18)
        if etiqueta["descricao"]:
            _desenhar_bloco_avulso(c, etiqueta["descricao"], pad, pad,
                                   largura, altura - altura_sku, "Helvetica", 12)
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawRightString(etiqueta_w - pad, 2 * mm, f"#{idx + 1}")
        c.setFillColorRGB(0, 0, 0)
    c.save()
    return buffer.getvalue(), len(etiquetas)


def gerar_pdf_editor_livre(etiquetas_html, tamanho="grande", orientacao=None):
    etiquetas = [str(html or "").strip() for html in etiquetas_html if str(html or "").strip()]
    if not etiquetas:
        return None, 0
    if len(etiquetas) > 100:
        raise ValueError("Limite máximo de 100 etiquetas livres por PDF.")
    tamanho = str(tamanho or "grande").strip().lower()
    pequena = tamanho.startswith("p")
    page_w, page_h, label_w, label_h, empilhadas = _geometria_avulsa(pequena, orientacao)
    fonte_normal, fonte_negrito = _fontes_etiqueta_avulsa()

    def _style_value_px(tag, prop, default_px):
        for node in [tag] + list(tag.find_all(True)):
            match = re.search(rf"{prop}\s*:\s*([\d.]+)px", str(node.get("style") or ""), re.I)
            if match:
                return max(6, min(96, float(match.group(1))))
        return default_px

    def _blocos_editor(html):
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.find_all(["script", "style"]):
            node.decompose()
        blocos = []
        inline = []

        def flush_inline():
            if inline and any(str(node).strip() for node in inline):
                bloco = soup.new_tag("div")
                for node in inline:
                    bloco.append(node)
                blocos.append(bloco)
            inline.clear()

        for node in list(soup.contents):
            if not isinstance(node, NavigableString) and node.name in {"div", "p"}:
                flush_inline()
                blocos.append(node)
            else:
                inline.append(node)
        flush_inline()
        resultado = []
        for bloco in blocos:
            for br in bloco.find_all("br"):
                br.replace_with("\n")
            texto = bloco.get_text("", strip=False)
            texto = "\n".join(re.sub(r"[^\S\n]+", " ", linha).strip() for linha in texto.split("\n"))
            fonte_px = _style_value_px(bloco, "font-size", 36)
            altura_px = max(fonte_px * 1.35, _style_value_px(bloco, "line-height", fonte_px * 1.35))
            estilo = str(bloco).lower()
            negrito = bool(bloco.find(["b", "strong"])) or bool(re.search(r"font-weight\s*:\s*(bold|[7-9]00)", estilo))
            sublinhar = bool(bloco.find("u")) or bool(re.search(r"text-decoration(?:-line)?\s*:\s*underline", estilo))
            alinhamento = re.search(r"text-align\s*:\s*(left|center|right)", str(bloco.get("style") or ""), re.I)
            resultado.append((texto, fonte_negrito if negrito else fonte_normal, fonte_px,
                              altura_px / fonte_px, sublinhar,
                              alinhamento.group(1).lower() if alinhamento else "center"))
        return resultado

    def _draw_editor_label(c, html, x0, y0, w, h):
        blocos = _blocos_editor(html)
        point_per_px = min(w / 720, h / 384)
        pad = 18 * point_per_px
        largura = w - 2 * pad
        altura = h - 2 * pad

        def compor(escala):
            linhas = []
            for texto, fonte, fonte_px, espacamento, sublinhar, align in blocos:
                tamanho_fonte = fonte_px * point_per_px * escala
                for paragrafo in texto.split("\n"):
                    quebradas, fonte_real = _ajustar_texto_pdf(
                        paragrafo, fonte, tamanho_fonte, largura,
                        max(altura, (len(paragrafo) + 1) * tamanho_fonte * 2))
                    for linha in quebradas or [""]:
                        linhas.append((linha, fonte, fonte_real, fonte_real * espacamento, sublinhar, align))
            return linhas

        escala = 1.0
        linhas = compor(escala)
        for _ in range(40):
            altura_total = sum(linha[3] for linha in linhas)
            if altura_total <= altura + 0.0001:
                break
            escala *= min(0.95, altura / altura_total)
            linhas = compor(escala)

        y = y0 + h - pad
        for texto, fonte, fonte_tamanho, entrelinha, sublinhar, align in linhas:
            ascendente, descendente = getAscentDescent(fonte, fonte_tamanho)
            y_baseline = y - (entrelinha - ascendente + descendente) / 2 - ascendente
            texto_w = stringWidth(texto, fonte, fonte_tamanho)
            if align == "left":
                x = x0 + pad
            elif align == "right":
                x = x0 + w - pad - texto_w
            else:
                x = x0 + (w - texto_w) / 2
            c.setFont(fonte, fonte_tamanho)
            c.drawString(x, y_baseline, texto)
            if sublinhar and texto:
                c.setLineWidth(max(0.15, fonte_tamanho * 0.035))
                c.line(x, y_baseline - fonte_tamanho * 0.08,
                       x + texto_w, y_baseline - fonte_tamanho * 0.08)
            y -= entrelinha

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    por_pagina = 2 if pequena else 1
    for idx, html in enumerate(etiquetas):
        if idx and idx % por_pagina == 0:
            c.showPage()
        x0, y0 = _posicao_avulsa(idx, label_w, label_h, empilhadas) if pequena else (0, 0)
        if pequena:
            _desenhar_borda_avulsa(c, x0, y0, label_w, label_h)
        _draw_editor_label(c, html, x0, y0, label_w, label_h)
    c.save()
    return buffer.getvalue(), len(etiquetas)
