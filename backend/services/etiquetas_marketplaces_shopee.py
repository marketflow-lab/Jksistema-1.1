"""Shopee label PDF processing."""

from __future__ import annotations

import re
from io import BytesIO

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_marketplaces_common import extrair_chave_acesso, gerar_imagem_barcode

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
        marketplace_ctx.st.error(f"Erro no processamento Shopee: {str(e)}")
        return None, None, 0
