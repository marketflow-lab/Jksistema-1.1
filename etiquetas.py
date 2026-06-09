import streamlit as st
import fitz  # PyMuPDF
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
import re
import os
import tempfile
import platform
import webbrowser
import time
import sys

# ==============================================================================
# --- 1. FUNÇÕES UTILITÁRIAS (BARCODE, REGEX, IMPRESSÃO) ---
# ==============================================================================

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

def enviar_para_impressora_local(pdf_bytes, nome_arquivo="temp_print.pdf"):
    """
    Salva o arquivo temporariamente e abre com o visualizador padrão do SO para impressão.
    """
    try:
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, nome_arquivo)
        
        with open(temp_path, "wb") as f:
            f.write(pdf_bytes)
            
        if platform.system() == "Windows":
            os.startfile(temp_path) 
        else:
            webbrowser.open(temp_path)
            
        st.toast(f"🖨️ Arquivo enviado: {nome_arquivo}", icon="✅")
        time.sleep(1)
        return True
    except Exception as e:
        st.error(f"Erro ao abrir arquivo para impressão: {e}")
        return False

# ==============================================================================
# --- 2. LÓGICA TIKTOK ---
# ==============================================================================

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
    progress_bar = st.progress(0, text="Processando arquivo do TikTok...")
    
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
            st.warning("Lista de picking do TikTok nao foi encontrada; SKUs ficarao como N/D.")
    
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

# ==============================================================================
# --- 3. LÓGICA SHOPEE ---
# ==============================================================================

def extract_shopee_data(doc):
    mapa_pedidos = {}
    full_text = ""
    for page in doc:
        # Apenas páginas de checklist ou corte aqui para garantir performance e precisão
        text = page.get_text("text")
        if "checklist" in text.lower() or "corte aqui" in text.lower():
            full_text += page.get_text("text") + "\n"

    # Separação por blocos de pedido usando "Corte aqui" como delimitador
    blocos = re.split(r'Corte[\s_]*aqui', full_text, flags=re.IGNORECASE)

    for bloco in blocos:
        if not bloco.strip():
            continue

        # 1. Busca ID do Pedido (Shopee IDs começam com 2 e têm data YYMMDD)
        match_id = re.search(r'(2[0-9]{5}[A-Z0-9]{6,})', bloco, re.IGNORECASE)
        if not match_id:
            continue
        
        raw_id = match_id.group(1).upper()
        # Correção para IDs capturados com sufixo 'PACKAGE' que quebram o vínculo
        if "PACKAGE" in raw_id:
            pedido_id = raw_id.split("PACKAGE")[0]
        else:
            pedido_id = raw_id
        
        sku = "N/D"
        qty = "1"
        product_name = ""
        
        linhas = [l.strip() for l in bloco.split('\n') if l.strip()]

        # Lógica Posicional para SKU (Baseada no backend_api.py)
        idx_checklist = -1
        for i, linha in enumerate(linhas):
            if "checklist" in linha.lower():
                idx_checklist = i
                break
        
        if idx_checklist > 0:
            cand_sku = linhas[idx_checklist - 1]
            is_qty_numeric = False
            if idx_checklist > 1:
                cand_qty = linhas[idx_checklist - 2]
                
                # Tenta extrair o Nome do Produto (linhas antes da Qtd)
                # Estrutura típica: [Index] [Produto] [Variação] [Qtd] [SKU] [Checklist]
                idx_qty_pos = idx_checklist - 2
                if linhas[idx_qty_pos].isdigit():
                    parts_prod = []
                    # Pega até 3 linhas antes da quantidade
                    for k in range(idx_qty_pos - 1, max(-1, idx_qty_pos - 4), -1):
                        line_k = linhas[k]
                        # Se encontrar um número pequeno isolado, é o índice do item, paramos
                        if line_k.isdigit() and len(line_k) < 4:
                            break
                        parts_prod.insert(0, line_k)
                    if parts_prod:
                        product_name = " ".join(parts_prod)

                if cand_qty.isdigit() and len(cand_qty) < 5:
                    qty = cand_qty
                    is_qty_numeric = True
            
            min_len = 1 if is_qty_numeric else 3
            if len(cand_sku) >= min_len and not any(x in cand_sku.lower() for x in ["corte", "shopee", "pedido", "checklist", "total"]):
                sku = cand_sku

        # Fallback Regex
        if sku == "N/D":
            bloco_sem_quebras = bloco.replace('\n', ' ')
            match_sku = re.search(r'(?:^|\s)(\d{1,3})\s+([A-Z0-9\-\.]{1,30})\s+Checklist', bloco_sem_quebras, re.IGNORECASE)
            if match_sku:
                qty = match_sku.group(1).strip()
                sku = match_sku.group(2).strip()
            else:
                match_sku_no_qty = re.search(r'\s([A-Z0-9\-\.]{1,30})\s+Checklist', bloco_sem_quebras, re.IGNORECASE)
                if match_sku_no_qty:
                    sku = match_sku_no_qty.group(1).strip()

        if sku != "N/D" and sku.upper() in ["UNIVERSAL", "GERADOR", "DIRIGIR", "PRODUTO", "VARIAÇÃO", "SHOPEE"]:
            sku = "N/D"

        mapa_pedidos[pedido_id] = (sku, qty, product_name)
        
    return mapa_pedidos

def processar_shopee(uploaded_file):
    try:
        file_bytes = uploaded_file.read()
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        mapa_global = extract_shopee_data(doc)
        
        output_labels = fitz.open()
        output_manifest = fitz.open()
        A6_W, A6_H = 283, 425
        count_labels = 0
        
        for page_index in range(len(doc)):
            page = doc[page_index]
            text = page.get_text("text").lower()
            
            # Verifica se é página de checklist/manifesto
            if "checklist" in text or "manifesto" in text or "corte aqui" in text:
                output_manifest.insert_pdf(doc, from_page=page_index, to_page=page_index)
            else:
                rect = page.rect
                is_large_page = rect.width > 400
                quads = [rect]
                if is_large_page:
                    mid_w, mid_h = rect.width/2, rect.height/2
                    quads = [
                        fitz.Rect(0, mid_h, mid_w, rect.height), fitz.Rect(mid_w, mid_h, rect.width, rect.height),
                        fitz.Rect(0, 0, mid_w, mid_h), fitz.Rect(mid_w, 0, rect.width, mid_h)
                    ]
                
                for q in quads:
                    q_text = page.get_text(clip=q)
                    q_text_clean = q_text.replace(" ", "").replace("\n", "").upper()
                    
                    pedido_id = None
                    
                    # 1. Tenta encontrar ID conhecido do checklist (Mais confiável)
                    if mapa_global:
                        for known_id in mapa_global:
                            if known_id in q_text_clean:
                                pedido_id = known_id
                                break
                    
                    # 2. Fallback Regex
                    if not pedido_id:
                        id_match = re.search(r'(2[0-9]{5}[A-Z0-9]{6,})', q_text_clean)
                        if id_match:
                            pedido_id = id_match.group(1)
                    
                    if pedido_id:
                        # Extrair Nome do Cliente (Geralmente após a data de envio/impressão)
                        nome_cliente = ""
                        lines_raw = [l.strip() for l in q_text.split('\n') if l.strip()]
                        for i, line in enumerate(lines_raw):
                            if re.search(r'\d{2}/\d{2}/\d{4}', line):
                                # Procura nas próximas 4 linhas por um nome válido
                                for offset in range(1, 5):
                                    if i + offset < len(lines_raw):
                                        candidate = lines_raw[i+offset]
                                        if len(candidate) > 2 and not any(x in candidate.upper() for x in ["NF:", "DANFE", "SÉRIE", "EMISSÃO", "DESTINATÁRIO", "REMETENTE", "RETIRADA", "PELO", "COMPRADOR"]):
                                            nome_cliente = candidate
                                            break
                                if nome_cliente: break

                        count_labels += 1
                        new_page = output_labels.new_page(width=A6_W, height=A6_H)
                        new_page.show_pdf_page(fitz.Rect(0, 0, A6_W, A6_H), doc, page_index, clip=q)
                        
                        sku_info = mapa_global.get(pedido_id)
                        prod_txt = ""
                        if sku_info and sku_info[0] != "N/D":
                            sku_txt = f"SKU: {sku_info[0]} (x{sku_info[1]})"
                            if len(sku_info) > 2: prod_txt = sku_info[2]
                            color = (0, 0, 0)
                        else:
                            sku_txt = "SKU N/D"
                            color = (1, 0, 0)
                        
                        new_page.insert_text(fitz.Point(279, 350), sku_txt, fontsize=12, color=color, rotate=90)
                        
                        # Nome do Produto (Abaixo do SKU)
                        if prod_txt:
                            new_page.insert_text(fitz.Point(265, 350), prod_txt[:45], fontsize=8, color=(0, 0, 0), rotate=90)

                        # Nome do Cliente (Abaixo do Produto)
                        if nome_cliente:
                            new_page.insert_text(fitz.Point(250, 350), nome_cliente[:35], fontsize=9, color=(0, 0, 0), rotate=90)

                        new_page.insert_text(fitz.Point(240, 20), f"#{count_labels}", fontsize=10, color=(0.5, 0.5, 0.5))

        labels_bytes = output_labels.tobytes() if count_labels > 0 else None
        manifest_bytes = output_manifest.tobytes() if len(output_manifest) > 0 else None
        
        output_labels.close(); output_manifest.close()
        return labels_bytes, manifest_bytes, count_labels

    except Exception as e:
        st.error(f"Erro no processamento Shopee: {str(e)}")
        return None, None, 0

# ==============================================================================
# --- 4. LÓGICA MERCADO LIVRE ---
# ==============================================================================

def processar_mercado_livre(uploaded_file):
    progress_bar = st.progress(0, text="Processando arquivo do Mercado Livre...")
    
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
        
        nova_pagina.show_pdf_page(fitz.Rect(0, 0, 283, 390), doc_origem, i)
        
        if dados_sku:
            sku_text = f"SKU {dados_sku[0]}  x  {dados_sku[1]}"
            color_sku = (0, 0, 0)
        else:
            sku_text = "SKU N/D"
            color_sku = (1, 0, 0)

        nova_pagina.insert_text(fitz.Point(281, 300), sku_text, fontsize=8, color=color_sku, rotate=90)
        nova_pagina.draw_line(fitz.Point(0, 390), fitz.Point(283, 390), color=(0,0,0), width=1)
        nova_pagina.insert_text(fitz.Point(95, 396), "CHAVE DE ACESSO / VENDA", fontsize=5, color=(0, 0, 0))

        chave_numeros = None
        if page_declaracao:
            chave_numeros = extrair_chave_acesso(page_declaracao.get_text())
        elif "CHAVE DE ACESSO" in texto_atual:
             chave_numeros = extrair_chave_acesso(page_atual.get_text())

        codigo_barra_base = chave_numeros or id_correspondente

        if codigo_barra_base:
            texto_codigo = chave_numeros if chave_numeros else f"VENDA {id_correspondente}"
            nova_pagina.insert_text(fitz.Point(45, 402), texto_codigo, fontsize=5.5, color=(0, 0, 0))
            img_bytes = gerar_imagem_barcode(codigo_barra_base)
            if img_bytes:
                nova_pagina.insert_image(fitz.Rect(0, 403, 283, 425), stream=img_bytes)
        else:
            nova_pagina.insert_text(fitz.Point(70, 410), "CODIGO NAO ENCONTRADO", fontsize=5, color=(0.5, 0.5, 0.5))

        count_etiquetas += 1
        num_pag_text = f"{count_etiquetas}"
        nova_pagina.draw_rect(fitz.Rect(250, 0, 283, 15), color=None, fill=(1,1,1)) 
        nova_pagina.insert_text(fitz.Point(255, 12), num_pag_text, fontsize=12, color=(0,0,0))
        
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

# ==============================================================================
# --- 5. INTERFACE PRINCIPAL (RENDER PAGE) ---
# ==============================================================================

def render_page(navegar_para_callback):
    """
    Renderiza a página consolidada de etiquetas.
    """
    if st.button("⬅️ Voltar ao Menu", key="btn_voltar_etiquetas"):
        navegar_para_callback('menu')

    st.markdown("## Gerador de Etiquetas Inteligente (V2.0)")
    st.markdown("### Automação Logística Trend Minas")
    
    # Inicialização de Estado
    if 'etiquetas_geradas' not in st.session_state:
        st.session_state.update({
            'etiquetas_geradas': None, 
            'lista_gerada': None, 
            'qtd': 0, 
            'processed': False,
            'loja': None
        })

    st.subheader("1. Selecione a Loja")
    loja_selecionada = st.radio(
        "Escolha a plataforma:",
        ("Mercado Livre", "Shopee", "TikTok"),
        key='loja_radio',
        horizontal=True,
        index=None
    )

    if loja_selecionada:
        st.markdown("---")
        st.subheader("2. Faça o Upload dos Arquivos")
        
        uploaded_file = None
        uploaded_list = None

        if loja_selecionada == "Mercado Livre" or loja_selecionada == "Shopee":
            uploaded_file = st.file_uploader(
                "Importar arquivo PDF", 
                type="pdf", 
                help=f"Arraste o PDF da {loja_selecionada} aqui."
            )
        else: # TikTok
            col1, col2 = st.columns(2)
            with col1:
                uploaded_file = st.file_uploader("Arquivo de Etiquetas (PDF)", type="pdf")
            with col2:
                uploaded_list = st.file_uploader("Lista de Picking (PDF)", type="pdf", help="Obrigatório para TikTok")

        if uploaded_file:
            if st.button("🚀 INICIAR PROCESSAMENTO", type="primary", use_container_width=True):
                try:
                    etiquetas, qtd, lista = None, 0, None
                    
                    if loja_selecionada == "Mercado Livre":
                        etiquetas, qtd, lista = processar_mercado_livre(uploaded_file)
                    elif loja_selecionada == "Shopee":
                        etiquetas, lista, qtd = processar_shopee(uploaded_file)
                    else:
                        etiquetas, qtd, lista = processar_tiktok(uploaded_file, uploaded_list)
                    
                    st.session_state['etiquetas_geradas'] = etiquetas
                    st.session_state['lista_gerada'] = lista
                    st.session_state['qtd'] = qtd
                    st.session_state['processed'] = True
                    st.session_state['loja'] = loja_selecionada
                    
                    if qtd > 0:
                        st.toast(f"Sucesso! {qtd} etiquetas geradas.", icon="🎉")
                    else:
                        st.warning("Nenhuma etiqueta detectada.")
                except Exception as e:
                    st.error(f"Erro crítico: {e}")

    # Exibição dos Resultados
    if st.session_state.get('processed'):
        st.markdown("---")
        st.subheader("📊 Resultados")
        
        col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
        tem_etiquetas = st.session_state['etiquetas_geradas'] is not None
        tem_lista = st.session_state['lista_gerada'] is not None
        
        with col_kpi1: st.metric("Etiquetas", st.session_state['qtd'])
        with col_kpi2: st.metric("Lista", "Pronta" if tem_lista else "-")
        with col_kpi3: st.metric("Loja", st.session_state.get('loja', '-'))

        st.markdown("<br>", unsafe_allow_html=True)
        col_act1, col_act2 = st.columns(2)

        with col_act1:
            if tem_etiquetas:
                # Botão de Impressão Direta (Local)
                if st.button("📦 ABRIR P/ IMPRESSÃO", type="primary", use_container_width=True):
                    enviar_para_impressora_local(st.session_state['etiquetas_geradas'], "etiquetas_prontas.pdf")
                
                # Download
                st.download_button(
                    label="⬇️ Baixar PDF Etiquetas",
                    data=st.session_state['etiquetas_geradas'],
                    file_name="etiquetas_processadas.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                
        with col_act2:
            if tem_lista:
                # Botão de Impressão Direta (Local)
                if st.button("📋 ABRIR LISTA", type="secondary", use_container_width=True):
                    enviar_para_impressora_local(st.session_state['lista_gerada'], "lista_controle.pdf")
                
                # Download
                st.download_button(
                    label="⬇️ Baixar Lista Controle",
                    data=st.session_state['lista_gerada'],
                    file_name="lista_controle.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            elif st.session_state.get('loja') == "Shopee":
                 st.info("Shopee: Manifesto não encontrado no arquivo.")
