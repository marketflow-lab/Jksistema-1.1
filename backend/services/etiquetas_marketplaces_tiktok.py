"""TikTok label PDF processing."""

from __future__ import annotations

import re
import time
from io import BytesIO

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx

def _eh_pagina_lista_tiktok(texto_pagina):
    """Identifica paginas de lista/packing do TikTok dentro do PDF principal."""
    texto_up = (texto_pagina or "").upper()
    if "PICKING LIST" in texto_up or "PACKING LIST" in texto_up:
        return True

    indicadores = ("ORDER ID", "PACKAGE ID", "PRODUCT NAME", "SELLER SKU", "QTY")
    total_indicadores = sum(1 for item in indicadores if item in texto_up)
    eh_etiqueta = "DESTINAT" in texto_up or "RECEBEDOR" in texto_up or "REMETENTE" in texto_up
    return total_indicadores >= 4 and not eh_etiqueta


def _extrair_order_id_tiktok(texto_pagina):
    match = re.search(r'\bOrder\s*ID\s*:\s*(\d{10,})', texto_pagina or "", flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extrair_sku_qtd_tiktok(texto_pagina):
    linhas = [linha.strip() for linha in (texto_pagina or "").splitlines() if linha.strip()]

    for idx, linha in enumerate(linhas):
        if not re.search(r'\bQty\s*Total\b', linha, flags=re.IGNORECASE):
            continue

        inicio_tabela = -1
        for j in range(idx - 1, -1, -1):
            if linhas[j].strip().upper() == "QTY":
                inicio_tabela = j + 1
                break

        bloco = linhas[inicio_tabela:idx] if inicio_tabela >= 0 else linhas[max(0, idx - 8):idx]
        qtd_idx = None
        for j in range(len(bloco) - 1, -1, -1):
            if re.fullmatch(r'\d{1,4}', bloco[j]):
                qtd_idx = j
                break

        if qtd_idx is None:
            continue

        qtd = bloco[qtd_idx]
        for j in range(qtd_idx - 1, -1, -1):
            sku = bloco[j].strip()
            if sku and not re.fullmatch(r'(?:SKU|SELLER SKU|PRODUCT NAME|QTY)', sku, flags=re.IGNORECASE):
                return sku, qtd

    texto_compacto = re.sub(r'\s+', ' ', texto_pagina or "").strip()
    match = re.search(
        r'Seller\s+SKU\s+Qty\s+.+?\s+([A-Z0-9][A-Z0-9_\-./]{0,59})\s+(\d{1,4})\s+Qty\s+Total',
        texto_compacto,
        flags=re.IGNORECASE
    )
    if match:
        return match.group(1), match.group(2)

    termos_bloqueados = (
        "PICKING", "PACKING", "TIK TOK", "TIKTOK", "ORDER", "PACKAGE", "CREATED",
        "TRANSIT", "TRACKING", "PRODUCT", "SELLER", "QTY", "TOTAL"
    )
    order_id_pattern = re.compile(r'^(?:Order\s*ID\s*:?\s*)?\d{15,}', flags=re.IGNORECASE)
    for i, linha in enumerate(linhas):
        if not linha or linha.isdigit() or ":" in linha:
            continue
        linha_up = linha.upper()
        if any(termo in linha_up for termo in termos_bloqueados):
            continue
        if len(linha) > 60:
            continue

        proxima = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
        if re.fullmatch(r'\d{1,4}', proxima):
            return linha, proxima
        if order_id_pattern.search(proxima):
            return linha, "1"

    return None


def _parse_tiktok_picking_doc(doc):
    mapa_tiktok = {}
    lista_sequencial = []
    paginas_lista = []

    for page_index, page in enumerate(doc):
        texto = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        if not _eh_pagina_lista_tiktok(texto):
            continue

        paginas_lista.append(page_index)
        dados_sku = _extrair_sku_qtd_tiktok(texto)
        if not dados_sku:
            continue

        order_id = _extrair_order_id_tiktok(texto)
        if order_id:
            mapa_tiktok[order_id] = dados_sku
        lista_sequencial.append(dados_sku)

    return mapa_tiktok, lista_sequencial, paginas_lista


def parse_tiktok_picking_list(file_obj):
    """Lê o PDF 'Picking List' do TikTok e extrai a relação {Order_ID: (SKU, Qtd)}"""
    try:
        doc = fitz.open(stream=file_obj.read(), filetype="pdf")
        mapa_tiktok, lista_sequencial, _ = _parse_tiktok_picking_doc(doc)
        doc.close()
        file_obj.seek(0)
        return mapa_tiktok, lista_sequencial
    except Exception as e:
        print(f"Erro parsing TikTok: {e}")
        file_obj.seek(0)
        return {}, []


def processar_tiktok(uploaded_file, uploaded_list=None):
    progress_bar = marketplace_ctx.st.progress(0, text="Processando arquivo do TikTok...")
    
    file_bytes = uploaded_file.read()
    doc_origem = fitz.open(stream=file_bytes, filetype="pdf")
    doc_etiquetas = fitz.open()
    doc_lista = fitz.open()
    
    lista_sequencial_fallback = []
    
    progress_bar.progress(10, text="Analisando Picking List do TikTok...")
    
    if uploaded_list:
        mapa_tiktok, lista_seq = parse_tiktok_picking_list(uploaded_list)
        lista_sequencial_fallback = lista_seq

        # Salva a lista original para impressão
        uploaded_list.seek(0)
        doc_lista_origem = fitz.open(stream=uploaded_list.read(), filetype="pdf")
        doc_lista.insert_pdf(doc_lista_origem)
        doc_lista_origem.close()
    else:
        mapa_tiktok, lista_seq, paginas_lista = _parse_tiktok_picking_doc(doc_origem)
        lista_sequencial_fallback = lista_seq
        for pagina_lista in paginas_lista:
            doc_lista.insert_pdf(doc_origem, from_page=pagina_lista, to_page=pagina_lista)
        if not lista_sequencial_fallback:
            marketplace_ctx.st.warning("Lista de picking do TikTok nao foi encontrada; SKUs ficarao como N/D.")
    
    progress_bar.progress(40, text="Separando e formatando etiquetas...")

    count_etiquetas = 0
    total_paginas = len(doc_origem)
    w_page = 283
    h_page = 425
    i = 0
    fallback_seq_index = 0
    
    while i < total_paginas:
        page_atual = doc_origem[i]
        texto_atual = page_atual.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE).upper()
        
        # Filtros de páginas indesejadas
        eh_lista = (
            any(s in texto_atual for s in ["MANIFESTO", "RESUMO DO PEDIDO", "RELATÓRIO DE", "PICKING LIST", "PACKING LIST"])
            or _eh_pagina_lista_tiktok(texto_atual)
        )
        eh_danfe = any(s in texto_atual for s in ["DANFE", "DOCUMENTO AUXILIAR", "VALOR TOTAL DA NOTA"])
        eh_declaracao_conteudo_solta = "DECLARAÇÃO DE CONTEÚDO" in texto_atual and "REMETENTE" in texto_atual and len(re.findall(r'ETIQUETA', texto_atual)) == 0
        
        if eh_lista or eh_danfe or eh_declaracao_conteudo_solta:
            i += 1
            continue

        eh_etiqueta = "DESTINATÁRIO" in texto_atual or "RECEBEDOR" in texto_atual
        if not eh_etiqueta and "REMETENTE" in texto_atual and "ETIQUETA" in texto_atual:
             eh_etiqueta = True

        if not eh_etiqueta:
            i += 1
            continue

        # Processamento da Etiqueta
        nova_pagina = doc_etiquetas.new_page(width=w_page, height=h_page)
        
        dados_sku = None
        if fallback_seq_index < len(lista_sequencial_fallback):
            dados_sku = lista_sequencial_fallback[fallback_seq_index]
            fallback_seq_index += 1
        
        nova_pagina.show_pdf_page(nova_pagina.rect, doc_origem, i)
        
        if dados_sku:
            color_sku = (0, 0, 0)
            nome_sku = dados_sku[0]
            if len(nome_sku) > 25: nome_sku = nome_sku[:25] + "..."
            sku_text = f"SKU: {nome_sku}  (x{dados_sku[1]})"
        else:
            sku_text = "SKU N/D"
            color_sku = (1, 0, 0)

        nova_pagina.insert_text(fitz.Point(281, 300), sku_text, fontsize=8, color=color_sku, rotate=90)
        # O Módulo Tiktok não é necessário colocar numero da chave de acesso abaixo. 
        # Pois a etiqueta original já vai com ela.
        # nova_pagina.insert_text(fitz.Point(110, 396), "CHAVE DE ACESSO / NOTA", fontsize=5, color=(0, 0, 0))

        # chave_numeros = extrair_chave_acesso(page_atual.get_text())

        # if chave_numeros:
        #     nova_pagina.insert_text(fitz.Point(60, 402), chave_numeros, fontsize=5.5, color=(0, 0, 0))
        #     img_bytes = gerar_imagem_barcode(chave_numeros)
        #     if img_bytes:
        #         nova_pagina.insert_image(fitz.Rect(0, 403, 283, 425), stream=img_bytes)
        # else:
        #     nova_pagina.insert_text(fitz.Point(90, 410), "SEM CHAVE VISÍVEL", fontsize=5, color=(0.5, 0.5, 0.5))

        count_etiquetas += 1
        num_pag_text = f"{count_etiquetas}"
        nova_pagina.draw_rect(fitz.Rect(250, 0, 283, 15), color=None, fill=(1,1,1)) 
        nova_pagina.insert_text(fitz.Point(255, 12), num_pag_text, fontsize=12, color=(0,0,0))
        
        i += 1 

    progress_bar.progress(100, text="Finalizado!")
    time.sleep(0.3)
    progress_bar.empty()

    bytes_etiquetas = None; bytes_lista = None
    if count_etiquetas > 0:
        buffer_et = BytesIO(); doc_etiquetas.save(buffer_et); bytes_etiquetas = buffer_et.getvalue()
    
    if len(doc_lista) > 0:
        buffer_li = BytesIO(); doc_lista.save(buffer_li); bytes_lista = buffer_li.getvalue()

    doc_etiquetas.close(); doc_lista.close(); doc_origem.close()
    return bytes_etiquetas, count_etiquetas, bytes_lista
