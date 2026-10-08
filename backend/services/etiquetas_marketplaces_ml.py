"""Mercado Livre label PDF processing."""

from __future__ import annotations

import re
from io import BytesIO

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_marketplaces_common import (
    extrair_chave_acesso,
    gerar_imagem_barcode,
)


def _normalizar_id_ml(valor):
    """Normaliza IDs de venda/pack para facilitar o vínculo entre lista e etiqueta."""
    if not valor:
        return None
    return re.sub(r'[^A-Z0-9]', '', str(valor).upper())


def _extrair_ids_ml(texto):
    """Extrai possíveis IDs de venda/pack em layouts diversos (inclui Flex)."""
    if not texto:
        return []

    padroes = [
        r'(?:\bVENDA\b|N[\u00ba\u00b0o]?\s*DA\s*VENDA|ID\s*DA\s*VENDA|PEDIDO)\s*[:#-]?\s*([A-Z0-9\-\.]{6,})',
        r'(?:\bPACK\s*ID\b|ID\s*DO\s*PACK|PACOTE|ID\s*DO\s*PACOTE)\s*[:#-]?\s*([A-Z0-9\-\.]{6,})',
    ]

    ids = []
    texto_up = texto.upper()
    for padrao in padroes:
        for m in re.finditer(padrao, texto_up, flags=re.IGNORECASE):
            valor = _normalizar_id_ml(m.group(1))
            if valor and valor not in ids:
                ids.append(valor)
    return ids


def _extrair_sku_do_segmento_ml(segmento):
    """Extrai SKU de trecho textual da lista do Mercado Livre com fallback para formatos flexíveis."""
    if not segmento:
        return None

    candidatos = re.findall(
        r'SKU\s*[:#-]?\s*([A-Z0-9_\-\./]{1,60})',
        segmento,
        flags=re.IGNORECASE
    )
    if candidatos:
        return candidatos[-1].strip()

    candidatos_genericos = re.findall(
        r'\b([A-Z0-9][A-Z0-9_\-\./]{2,40})\b',
        segmento,
        flags=re.IGNORECASE
    )
    palavras_bloqueadas = {
        "VENDA", "PACK", "ID", "QTD", "QUANT", "QUANTIDADE", "SKU", "ML", "MERCADOLIVRE"
    }
    for c in reversed(candidatos_genericos):
        if c.upper() not in palavras_bloqueadas:
            return c.strip()
    return None


def _extrair_qtd_ml(texto, default="1"):
    """Extrai quantidade em textos do Mercado Livre (QTD/QUANTIDADE/QTY)."""
    if not texto:
        return default
    m = re.search(r'(?:QTD|QTY|QUANTIDADE|QUANT)\s*[:#-]?\s*(\d{1,3})', texto, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'\bX\s*(\d{1,3})\b', texto, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return default


def _extrair_dados_ml_da_pagina(texto_pagina):
    """Fallback para capturar SKU/QTD direto da etiqueta quando a lista falha."""
    if not texto_pagina:
        return None

    sku = None
    candidatos = re.findall(
        r'(?:SELLER\s*SKU|SKU\s*DO\s*VENDEDOR|SKU)\s*[:#-]?\s*([A-Z0-9_\-\./]{1,60})',
        texto_pagina,
        flags=re.IGNORECASE
    )
    if candidatos:
        sku = candidatos[-1].strip()

    if not sku:
        sku = _extrair_sku_do_segmento_ml(texto_pagina)

    if not sku:
        return None

    return (sku, _extrair_qtd_ml(texto_pagina))


def processar_mercado_livre(uploaded_file):
    progress_bar = marketplace_ctx.st.progress(0, text="Processando arquivo do Mercado Livre...")
    
    file_bytes = uploaded_file.read()
    doc_origem = fitz.open(stream=file_bytes, filetype="pdf")
    doc_etiquetas = fitz.open()
    doc_lista = fitz.open()
    mapa_ids_dados = {}
    
    progress_bar.progress(20, text="Mapeando SKUs...")
    
    texto_completo = ""
    for page in doc_origem: texto_completo += page.get_text() + "\n"
    texto_linear = texto_completo.replace('\n', ' ').replace('  ', ' ')
    
    # Estratégia 1: mapeamento centrado no ID (mais estável para layouts Flex).
    for m_id in re.finditer(
        r'(?:\bVENDA\b|N[\u00ba\u00b0o]?\s*DA\s*VENDA|ID\s*DA\s*VENDA|PEDIDO|\bPACK\s*ID\b|ID\s*DO\s*PACK|PACOTE|ID\s*DO\s*PACOTE)\s*[:#-]?\s*([A-Z0-9\-\.]{6,})',
        texto_linear,
        flags=re.IGNORECASE
    ):
        identificador = _normalizar_id_ml(m_id.group(1))
        if not identificador:
            continue

        ini = max(0, m_id.start() - 240)
        fim = min(len(texto_linear), m_id.end() + 240)
        janela = texto_linear[ini:fim]
        sku = _extrair_sku_do_segmento_ml(janela)
        qtd = _extrair_qtd_ml(janela)
        if sku:
            mapa_ids_dados[identificador] = (sku, qtd)

    # Estratégia 2 (complementar): parsing por segmentos delimitados por quantidade.
    last_pos = 0
    for match in re.finditer(r'(?:QUAN|QTD)[A-Z\s]*[:#-]?\s*(\d+)', texto_linear, re.IGNORECASE):
        qtd = match.group(1)
        end_pos = match.end()
        segmento = texto_linear[last_pos:end_pos]
        sku = _extrair_sku_do_segmento_ml(segmento)
        ids_encontrados = _extrair_ids_ml(segmento)
        if sku:
            for identificador in ids_encontrados:
                mapa_ids_dados[identificador] = (sku, qtd)
        last_pos = end_pos

    progress_bar.progress(50, text="Separando e formatando etiquetas...")

    count_etiquetas = 0
    total_paginas = len(doc_origem)
    w_page = 283
    h_page = 425
    i = 0
    
    while i < total_paginas:
        page_atual = doc_origem[i]
        texto_atual = page_atual.get_text().upper()
        
        frase_lista = "DESPACHEM AS SUAS VENDAS O QUANTO ANTES"
        tem_titulo_lista = "MANIFESTO" in texto_atual or "RESUMO DO PEDIDO" in texto_atual or "RELATÓRIO DE" in texto_atual
        count_pack = len(re.findall(r'PACK ID', texto_atual, re.IGNORECASE))
        eh_lista = (frase_lista in texto_atual) or tem_titulo_lista or (count_pack >= 6 and "DESTINATÁRIO" not in texto_atual)

        if eh_lista:
            doc_lista.insert_pdf(doc_origem, from_page=i, to_page=i)
            i += 1
            continue

        tem_financeiro = any(t in texto_atual for t in ["VALOR TOTAL DA NOTA", "BASE DE CÁLCULO", "VALOR DO ICMS"])
        eh_danfe = "DANFE" in texto_atual or "DOCUMENTO AUXILIAR" in texto_atual or tem_financeiro
        
        if eh_danfe or ("DECLARAÇÃO DE CONTEÚDO" in texto_atual and "DESTINATÁRIO" in texto_atual and "VALOR TOTAL" in texto_atual):
            i += 1
            continue

        page_declaracao = None
        salto = 1 
        if i + 1 < total_paginas:
            prox_page = doc_origem[i+1]
            texto_prox = prox_page.get_text().upper()
            prox_tem_chave = "CHAVE DE ACESSO" in texto_prox or "DANFE" in texto_prox or "DECLARAÇÃO" in texto_prox
            prox_eh_etiqueta = "DESTINATÁRIO" in texto_prox or "RECEBEDOR" in texto_prox
            if prox_tem_chave and not prox_eh_etiqueta and frase_lista not in texto_prox:
                page_declaracao = prox_page
                salto = 2 

        nova_pagina = doc_etiquetas.new_page(width=w_page, height=h_page)
        texto_pagina = page_atual.get_text()
        texto_limpo = texto_pagina.replace(' ', '').replace('\n', '')
        dados_sku = None
        id_correspondente = None

        ids_pagina = _extrair_ids_ml(texto_pagina)
        for identificador in ids_pagina:
            if identificador in mapa_ids_dados:
                id_correspondente = identificador
                dados_sku = mapa_ids_dados[identificador]
                break

        if not dados_sku:
            texto_limpo_up = texto_limpo.upper()
            for k, v in mapa_ids_dados.items():
                if k in texto_limpo_up:
                    id_correspondente = k
                    dados_sku = v
                    break

        if not dados_sku:
            dados_sku = _extrair_dados_ml_da_pagina(texto_pagina)
        
        # Mantem a escala original; a DANFE usa a margem lateral existente.
        nova_pagina.show_pdf_page(fitz.Rect(0, 0, 283, 390), doc_origem, i)
        
        if dados_sku:
            sku_text = f"SKU {dados_sku[0]}  x  {dados_sku[1]}"
            color_sku = (0, 0, 0)
        else:
            sku_text = "SKU N/D"
            color_sku = (1, 0, 0)

        nova_pagina.draw_line(fitz.Point(0, 390), fitz.Point(283, 390), color=(0,0,0), width=1)
        for tamanho_sku in (12, 11, 10, 9, 8, 7):
            linhas_sku = [""]
            for caractere in sku_text:
                candidato = linhas_sku[-1] + caractere
                if fitz.get_text_length(candidato, fontname="hebo", fontsize=tamanho_sku) > 271:
                    linhas_sku.append(caractere)
                else:
                    linhas_sku[-1] = candidato
            espaco_restante = nova_pagina.insert_textbox(
                fitz.Rect(6, 397, 277, 424), "\n".join(linha.strip() for linha in linhas_sku),
                fontsize=tamanho_sku, fontname="hebo", color=color_sku, align=1,
            )
            if espaco_restante >= 0:
                break

        chave_numeros = None
        if page_declaracao:
            chave_numeros = extrair_chave_acesso(page_declaracao.get_text())
        elif "CHAVE DE ACESSO" in texto_atual:
            chave_numeros = extrair_chave_acesso(page_atual.get_text())

        codigo_barra_base = chave_numeros or id_correspondente
        titulo_codigo = "CHAVE DE ACESSO / VENDA"
        if codigo_barra_base:
            texto_codigo = chave_numeros if chave_numeros else f"VENDA {id_correspondente}"
            tamanho_codigo = 5.5
            cor_codigo = (0, 0, 0)
        else:
            texto_codigo = "CODIGO NAO ENCONTRADO"
            tamanho_codigo = 5
            cor_codigo = (0.5, 0.5, 0.5)

        comprimento_titulo = fitz.get_text_length(titulo_codigo, fontsize=5)
        comprimento_codigo = fitz.get_text_length(texto_codigo, fontsize=tamanho_codigo)
        fim_titulo = (390 - comprimento_titulo - 8 - comprimento_codigo) / 2 + comprimento_titulo
        nova_pagina.insert_text(
            fitz.Point(262.5, fim_titulo), titulo_codigo,
            fontsize=5, color=(0, 0, 0), rotate=90,
        )
        nova_pagina.insert_text(
            fitz.Point(262.5, fim_titulo + 8 + comprimento_codigo), texto_codigo,
            fontsize=tamanho_codigo, color=cor_codigo, rotate=90,
        )
        if codigo_barra_base:
            img_bytes = gerar_imagem_barcode(codigo_barra_base)
            if img_bytes:
                # Mantem amplo o eixo dos modulos; apenas encurta a altura das barras.
                nova_pagina.insert_image(
                    fitz.Rect(264.2, 20, 283, 385), stream=img_bytes,
                    rotate=90, keep_proportion=False,
                )

        count_etiquetas += 1
        num_pag_text = f"{count_etiquetas}"
        tamanho_contador = min(12, 21 / fitz.get_text_length(num_pag_text, fontsize=1))
        nova_pagina.insert_text(
            fitz.Point(260, 13), num_pag_text, fontsize=tamanho_contador, color=(0,0,0),
        )
        
        i += salto

    progress_bar.progress(100, text="Finalizado!")
    progress_bar.empty()

    bytes_etiquetas = None; bytes_lista = None
    if count_etiquetas > 0:
        buffer_et = BytesIO(); doc_etiquetas.save(buffer_et); bytes_etiquetas = buffer_et.getvalue()
    if len(doc_lista) > 0:
        buffer_li = BytesIO(); doc_lista.save(buffer_li); bytes_lista = buffer_li.getvalue()

    doc_etiquetas.close(); doc_lista.close(); doc_origem.close()
    return bytes_etiquetas, count_etiquetas, bytes_lista
