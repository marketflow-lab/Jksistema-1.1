"""Render external carton labels from already resolved text and image bytes."""

from __future__ import annotations

import hashlib
import io
import math
import re
import warnings
from xml.sax.saxutils import escape

from fastapi import HTTPException
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import getFont, stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from backend.services.etiquetas_pdf_common import _fontes_etiqueta_avulsa


MAX_FOTO_BYTES = 20 * 1024 * 1024
MAX_FOTO_PIXELS = 25_000_000
ITENS_POR_FOLHA = 18
MAX_FOLHAS = 5000
NORMAL, NEGRITO = _fontes_etiqueta_avulsa()
TINTA = colors.HexColor("#111827")
SECUNDARIA = colors.HexColor("#4b5563")
BORDA = colors.HexColor("#69717c")


def grade_etiquetas(quantidade):
    """Use at most six columns and three rows for one sheet's contents."""
    if isinstance(quantidade, bool) or not isinstance(quantidade, int) or not 1 <= quantidade <= ITENS_POR_FOLHA:
        raise HTTPException(400, "Cada folha de etiquetas deve conter de 1 a 18 itens.")
    linhas = math.ceil(quantidade / 6)
    return math.ceil(quantidade / linhas), linhas


def _validar_glifos(texto, fonte):
    """Refuse unsupported characters instead of allowing font substitution."""
    caracteres = [caractere for caractere in texto if caractere not in "\n\r\t"]
    mapa = getattr(getFont(fonte).face, "charToGlyph", None)
    if mapa is not None:
        valido = all(mapa.get(ord(caractere), 0) for caractere in caracteres)
    else:
        try:
            "".join(caracteres).encode("cp1252", errors="strict")
            valido = all(ord(caractere) >= 32 and not 127 <= ord(caractere) < 160 for caractere in caracteres)
        except UnicodeEncodeError:
            valido = False
    if not valido:
        raise HTTPException(400, "A fonte disponível não suporta todos os caracteres da etiqueta.")


def _paragrafo(texto, largura, altura, *, tamanho=10, minimo=9, fonte=None,
               cor=TINTA, entrelinhas=1.22, centralizado=False):
    """Measure all literal text without truncating or replacing its contents."""
    fonte = fonte or NORMAL
    texto = str(texto or "")
    _validar_glifos(texto, fonte)
    for passo in range(max(0, round((tamanho - minimo) * 4)) + 1):
        atual = max(minimo, tamanho - passo / 4)
        estilo = ParagraphStyle(
            "etiqueta", fontName=fonte, fontSize=atual, leading=atual * entrelinhas,
            textColor=cor, alignment=1 if centralizado else 0, splitLongWords=True,
        )
        bloco = Paragraph(escape(texto).replace("\n", "<br/>"), estilo)
        _, altura_real = bloco.wrap(largura, altura)
        if altura_real <= altura:
            return bloco, altura_real
    raise HTTPException(400, "O texto excede o espaço da etiqueta. Revise os dados antes de gerar o PDF.")


def _texto(pdf, texto, x, topo, largura, altura, **opcoes):
    bloco, altura_real = _paragrafo(texto, largura, altura, **opcoes)
    bloco.drawOn(pdf, x, topo - altura_real)
    return altura_real


def _rotulo(pdf, texto, x, y):
    pdf.setFillColor(SECUNDARIA)
    pdf.setFont(NEGRITO, 7.8)
    pdf.drawString(x, y, texto)


def _campos(texto):
    """Rejoin line wrapping while retaining every supplied field and value."""
    campos = []
    for linha in str(texto or "").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        novo_campo = re.match(r"^[\w .()/\-]+:", linha) or linha.startswith("CNP.J.")
        if not campos or novo_campo:
            campos.append(linha)
        else:
            campos[-1] += " " + linha
    return campos


def _campos_comprador_compactos(campos):
    grupos = (("tax id:", "phone:"), ("e-mail:", "contact personal:"), ("address:",),
              ("neighborhood:", "city:"), ("zip code:", "state:", "country:"))
    usados = set()
    linhas = []
    for grupo in grupos:
        indices = [indice for indice, campo in enumerate(campos)
                   if any(campo.casefold().startswith(prefixo) for prefixo in grupo)]
        if indices:
            linhas.append("  |  ".join(campos[indice] for indice in indices))
            usados.update(indices)
    linhas.extend(campo for indice, campo in enumerate(campos) if indice not in usados)
    return linhas


def _destinatario(pdf, titulo, texto, x, topo, largura, base, *, comprador=False):
    _rotulo(pdf, titulo, x, topo)
    campos = _campos(texto)
    if not campos:
        return
    empresa = re.sub(r"^(?:IMPORTER|Buyer):\s*", "", campos[0], flags=re.IGNORECASE)
    altura_nome = _texto(pdf, empresa, x, topo - 10, largura, 32,
                        tamanho=11.3, minimo=10.8, fonte=NEGRITO)
    cursor = topo - 10 - altura_nome - 5
    linhas = _campos_comprador_compactos(campos[1:]) if comprador else campos[1:]
    _texto(pdf, "\n".join(linhas), x, cursor, largura, cursor - base - 9,
           tamanho=10, minimo=9)


def _foto_validada(conteudo, cache):
    """Decode bounded in-memory images; never interpret a path or URL."""
    if not isinstance(conteudo, bytes) or not conteudo or len(conteudo) > MAX_FOTO_BYTES:
        return None
    chave = hashlib.sha256(conteudo).digest()
    if chave in cache:
        return cache[chave]
    foto = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(conteudo)) as original:
                if original.width <= 0 or original.height <= 0 or original.width * original.height > MAX_FOTO_PIXELS:
                    cache[chave] = None
                    return None
                if original.format not in {"PNG", "JPEG", "WEBP", "BMP", "GIF", "TIFF", "ICO"}:
                    cache[chave] = None
                    return None
                imagem = ImageOps.exif_transpose(original)
                imagem = imagem.convert("RGBA" if "A" in imagem.getbands() or "transparency" in imagem.info else "RGB")
                imagem.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                foto = ImageReader(imagem)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    cache[chave] = foto
    return foto


def _desenhar_foto(pdf, conteudo, x, y, largura, altura, cache):
    foto = _foto_validada(conteudo, cache)
    if foto is None:
        _texto(pdf, "Sem foto", x, y + altura / 2 + 6, largura, 16,
               tamanho=9, minimo=9, cor=SECUNDARIA, centralizado=True)
        return
    foto_largura, foto_altura = foto.getSize()
    escala = min(largura / foto_largura, altura / foto_altura)
    nova_largura, nova_altura = foto_largura * escala, foto_altura * escala
    pdf.drawImage(foto, x + (largura - nova_largura) / 2, y + (altura - nova_altura) / 2,
                  width=nova_largura, height=nova_altura, mask="auto")


def _quantidade(item):
    quantidade = item.get("quantidade")
    if quantidade is None:
        return "Qtd./caixa:"
    if isinstance(quantidade, bool) or not isinstance(quantidade, (int, float)) or not math.isfinite(quantidade) or quantidade < 0:
        raise HTTPException(400, "A quantidade por caixa é inválida.")
    valor = str(int(quantidade)) if quantidade == int(quantidade) else str(quantidade)
    return f"Qtd./caixa: {valor}"


def _item_unico(pdf, item, esquerda, direita, topo, base, cache):
    texto_x = esquerda + 18
    foto_x, foto_direita = direita - 220, direita - 16
    texto_largura = foto_x - texto_x - 26
    _rotulo(pdf, "CONTENTS / CONTEÚDO", texto_x, topo - 24)
    quantidade = _quantidade(item)
    altura_sku = _texto(pdf, "SKU " + str(item.get("sku") or ""), texto_x, topo - 36,
                        texto_largura, 87, tamanho=29, minimo=9, fonte=NEGRITO)
    descricao_topo = topo - 36 - altura_sku - 10
    _texto(pdf, item.get("descricao") or "", texto_x, descricao_topo, texto_largura,
           descricao_topo - base - 49, tamanho=11.2, minimo=9)
    _texto(pdf, quantidade, texto_x, base + 35, texto_largura, 21,
           tamanho=12, minimo=9, fonte=NEGRITO)
    _desenhar_foto(pdf, item.get("foto"), foto_x, base + 12,
                  foto_direita - foto_x, topo - base - 24, cache)


def _grade(pdf, itens, esquerda, direita, topo, base, inicio, total, cache):
    colunas, linhas = grade_etiquetas(len(itens))
    _rotulo(pdf, "CONTENTS / CONTEÚDO", esquerda + 15, topo - 17)
    pdf.setFillColor(SECUNDARIA)
    pdf.setFont(NEGRITO, 8)
    pdf.drawRightString(direita - 15, topo - 17, f"ITENS {inicio + 1}-{inicio + len(itens)} DE {total}")
    grade_esquerda, grade_direita = esquerda + 11, direita - 11
    grade_topo, grade_base = topo - 26, base + 10
    espacamento = 6
    largura = (grade_direita - grade_esquerda - (colunas - 1) * espacamento) / colunas
    altura = (grade_topo - grade_base - (linhas - 1) * espacamento) / linhas
    for indice, item in enumerate(itens):
        coluna, linha = indice % colunas, indice // colunas
        x = grade_esquerda + coluna * (largura + espacamento)
        y = grade_topo - (linha + 1) * altura - linha * espacamento
        pdf.setStrokeColor(colors.HexColor("#b9bec6"))
        pdf.setLineWidth(.45)
        pdf.rect(x, y, largura, altura)
        tamanho_sku = 10.4 if linhas == 3 else 14 if linhas == 2 else 18
        tamanho_qtd = 9 if linhas == 3 else 10 if linhas == 2 else 11
        qtd, altura_qtd = _paragrafo(_quantidade(item), largura - 12, 28,
                                    tamanho=tamanho_qtd, minimo=9, cor=SECUNDARIA, centralizado=True)
        sku, altura_sku = _paragrafo("SKU " + str(item.get("sku") or ""), largura - 12,
                                    altura - altura_qtd - 23, tamanho=tamanho_sku, minimo=9,
                                    fonte=NEGRITO, entrelinhas=1.12, centralizado=True)
        qtd.drawOn(pdf, x + 6, y + 3)
        sku_y = y + 3 + altura_qtd + 1
        sku.drawOn(pdf, x + 6, sku_y)
        foto_y = sku_y + altura_sku + 3
        _desenhar_foto(pdf, item.get("foto"), x + 8, foto_y,
                      largura - 16, y + altura - 4 - foto_y, cache)


def gerar_etiquetas_pdf(lista, caixas, *, cabecalho, importador, comprador, origem, data):
    """Render resolved carton contents, splitting each carton into 18-item sheets."""
    if not caixas or any(not caixa.get("itens") for caixa in caixas):
        raise HTTPException(400, "Inclua produtos nos volumes para gerar etiquetas.")
    if sum(math.ceil(len(caixa["itens"]) / ITENS_POR_FOLHA) for caixa in caixas) > MAX_FOLHAS:
        raise HTTPException(400, "Limite máximo de 5.000 folhas de etiquetas por Packing List.")
    largura_pagina, altura_pagina = landscape(A4)
    esquerda, direita, topo, base = 24, largura_pagina - 24, altura_pagina - 24, 24
    largura = direita - esquerda
    cabecalho_base, faixa_base, conteudo_base = topo - 122, topo - 166, base + 126
    divisor = esquerda + largura / 2
    contador_x = direita - 204
    invoice = str(lista.get("numero_invoice") or lista.get("invoice") or "").strip()
    output = io.BytesIO()
    pdf = Canvas(output, pagesize=(largura_pagina, altura_pagina), pageCompression=1)
    pdf.setTitle(f"Etiquetas externas {invoice}".strip())
    pdf.setAuthor("JK Sistema")
    cache_fotos = {}
    campos_fornecedor = _campos(cabecalho)
    for caixa in caixas:
        itens = caixa["itens"]
        folhas = math.ceil(len(itens) / ITENS_POR_FOLHA)
        for folha in range(folhas):
            inicio = folha * ITENS_POR_FOLHA
            itens_folha = itens[inicio:inicio + ITENS_POR_FOLHA]
            pdf.setStrokeColor(TINTA)
            pdf.setLineWidth(1.1)
            pdf.rect(esquerda, base, largura, topo - base)
            pdf.setFillColor(colors.HexColor("#f3f4f6"))
            pdf.rect(esquerda + .6, faixa_base, largura - 1.2, cabecalho_base - faixa_base, fill=1, stroke=0)
            pdf.setStrokeColor(BORDA)
            pdf.setLineWidth(.6)
            for y in (cabecalho_base, faixa_base, conteudo_base):
                pdf.line(esquerda, y, direita, y)
            pdf.line(contador_x, cabecalho_base, contador_x, topo)
            pdf.line(divisor, base, divisor, conteudo_base)
            x, y = esquerda + 15, topo - 16
            if campos_fornecedor:
                altura_nome = _texto(pdf, campos_fornecedor[0], x, y, contador_x - x - 14, 35,
                                    tamanho=16, minimo=9, fonte=NEGRITO)
                cursor = y - altura_nome - 6
                _texto(pdf, "\n".join(campos_fornecedor[1:]), x, cursor, contador_x - x - 15,
                       cursor - cabecalho_base - 9, tamanho=9.6, minimo=9)
            centro_contador = (contador_x + direita) / 2
            pdf.setFillColor(SECUNDARIA)
            pdf.setFont(NEGRITO, 9)
            pdf.drawCentredString(centro_contador, topo - 24, "CARTON / CAIXA")
            pdf.setFillColor(TINTA)
            contador = f'{caixa["numero"]:02d} / {len(caixas):02d}'
            tamanho_contador = 40
            while stringWidth(contador, NEGRITO, tamanho_contador) > direita - contador_x - 20 and tamanho_contador > 20:
                tamanho_contador -= .5
            if stringWidth(contador, NEGRITO, tamanho_contador) > direita - contador_x - 20:
                raise HTTPException(400, "A numeração excede o espaço da etiqueta.")
            pdf.setFont(NEGRITO, tamanho_contador)
            pdf.drawCentredString(centro_contador, topo - 73, contador)
            pdf.setFont(NEGRITO, 11.8)
            pdf.drawCentredString(centro_contador, cabecalho_base + 23, f"FOLHA {folha + 1} DE {folhas}")
            pdf.setFillColor(SECUNDARIA)
            pdf.setFont(NORMAL, 8.3)
            pdf.drawCentredString(centro_contador, cabecalho_base + 10, "SHEET / MESMA CAIXA")
            for titulo, valor, campo_x, campo_largura, tamanho in (
                ("INVOICE", invoice, esquerda + 15, 388, 14.2),
                ("DATE", data, esquerda + 424, 220, 11.5),
                ("ORIGIN / FROM", origem, direita - 116, 100, 12),
            ):
                _rotulo(pdf, titulo, campo_x, cabecalho_base - 13)
                _texto(pdf, valor, campo_x, cabecalho_base - 20, campo_largura, 20,
                       tamanho=tamanho, minimo=9, fonte=NEGRITO)
            if len(itens_folha) == 1:
                _item_unico(pdf, itens_folha[0], esquerda, direita, faixa_base, conteudo_base, cache_fotos)
            else:
                _grade(pdf, itens_folha, esquerda, direita, faixa_base, conteudo_base, inicio, len(itens), cache_fotos)
            _destinatario(pdf, "IMPORTER / IMPORTADOR", importador, esquerda + 15, conteudo_base - 13,
                          largura / 2 - 30, base)
            _destinatario(pdf, "BUYER / COMPRADOR", comprador, divisor + 15, conteudo_base - 13,
                          largura / 2 - 30, base, comprador=True)
            pdf.showPage()
    pdf.save()
    return output.getvalue()


__all__ = ["gerar_etiquetas_pdf", "grade_etiquetas"]
