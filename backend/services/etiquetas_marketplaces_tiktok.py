"""TikTok label PDF processing."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from io import BytesIO

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_marketplaces_common import ordenar_etiquetas


_ORDER_ID = re.compile(r'\b(?:Order\s*ID|ID\s*do\s*pedido)\s*[:#-]?\s*(\d{10,})\b', re.IGNORECASE)
_PACKAGE_ID = re.compile(
    r'\b(?:Package\s*ID|ID\s*do\s*pacote)\s*[:#-]?\s*((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{9,})\b',
    re.IGNORECASE,
)


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
    match = _ORDER_ID.search(texto_pagina or "")
    return match.group(1) if match else None


def _extrair_ids_tiktok(texto):
    order_ids = set(_ORDER_ID.findall(texto))
    package_ids = {valor.upper() for valor in _PACKAGE_ID.findall(texto)}
    if len(order_ids) > 1 or len(package_ids) > 1:
        return None
    return next(iter(order_ids), None), next(iter(package_ids), None)


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


def _ler_picking_tiktok(doc):
    registros = []
    for page_index, page in enumerate(doc):
        texto = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        if _eh_pagina_lista_tiktok(texto):
            registros.append((page_index, _extrair_ids_tiktok(texto), _extrair_sku_qtd_tiktok(texto)))
    return registros


def _parse_tiktok_picking_doc(doc):
    registros = _ler_picking_tiktok(doc)
    mapa_tiktok = {ids[0]: dados for _, ids, dados in registros if ids and ids[0] and dados}
    lista_sequencial = [dados for _, _, dados in registros if dados]
    paginas_lista = [indice for indice, _, _ in registros]
    return mapa_tiktok, lista_sequencial, paginas_lista


def _indexar_picking_tiktok(registros):
    por_id = defaultdict(set)
    for indice, ids, dados in registros:
        if ids is not None:
            for tipo, valor in enumerate(ids):
                if valor:
                    por_id[tipo, valor].add((indice, ids, dados))
    return por_id


def _vincular_picking_tiktok(ids_etiqueta, por_id):
    if ids_etiqueta is None:
        return None, None, []

    order_id, package_id = ids_etiqueta
    # Um ID em comum precisa concordar com os outros IDs presentes nas duas fontes.
    correspondencias = {
        (indice, ids_lista, dados)
        for tipo, valor in enumerate(ids_etiqueta)
        if valor
        for indice, ids_lista, dados in por_id.get((tipo, valor), ())
        if all(not etiqueta or not lista or etiqueta == lista for etiqueta, lista in zip(ids_etiqueta, ids_lista))
    }
    pedidos_lista = {ids_lista[0] for _, ids_lista, _ in correspondencias if ids_lista[0]}
    paginas = sorted(indice for indice, _, _ in correspondencias) if len(pedidos_lista) <= 1 else []
    candidatos = {(ids_lista[0] or order_id, dados) for _, ids_lista, dados in correspondencias if dados}
    if len(candidatos) == 1:
        pedido, dados = candidatos.pop()
        return dados, pedido or package_id, paginas
    return None, order_id or package_id, paginas


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


def processar_tiktok(uploaded_file, uploaded_list=None, *, layout=None, component_collector=None, limite_etiquetas=None):
    progress_bar = marketplace_ctx.st.progress(0, text="Processando arquivo do TikTok...")
    
    file_bytes = uploaded_file.read()
    doc_origem = fitz.open(stream=file_bytes, filetype="pdf")
    doc_etiquetas = fitz.open()
    doc_lista = fitz.open()
    
    progress_bar.progress(10, text="Analisando Picking List do TikTok...")

    if uploaded_list:
        with fitz.open(stream=uploaded_list.read(), filetype="pdf") as doc_lista_origem:
            registros_picking = _ler_picking_tiktok(doc_lista_origem)
            doc_lista.insert_pdf(doc_lista_origem)
        indice_na_lista = {indice: indice for indice in range(len(doc_lista))}
    else:
        registros_picking = _ler_picking_tiktok(doc_origem)
        indice_na_lista = {}
        for pagina_lista, _, _ in registros_picking:
            indice_na_lista[pagina_lista] = len(doc_lista)
            doc_lista.insert_pdf(doc_origem, from_page=pagina_lista, to_page=pagina_lista)
        if not any(dados for _, _, dados in registros_picking):
            marketplace_ctx.st.warning("Lista de picking do TikTok nao foi encontrada; SKUs ficarao como N/D.")

    progress_bar.progress(40, text="Separando e formatando etiquetas...")

    paginas_etiquetas = []
    for i, page_atual in enumerate(doc_origem):
        texto = page_atual.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        texto_atual = texto.upper()

        # Filtros de páginas indesejadas
        eh_lista = (
            any(s in texto_atual for s in ["MANIFESTO", "RESUMO DO PEDIDO", "RELATÓRIO DE", "PICKING LIST", "PACKING LIST"])
            or _eh_pagina_lista_tiktok(texto_atual)
        )
        eh_danfe = any(s in texto_atual for s in ["DANFE", "DOCUMENTO AUXILIAR", "VALOR TOTAL DA NOTA"])
        eh_declaracao_conteudo_solta = "DECLARAÇÃO DE CONTEÚDO" in texto_atual and "REMETENTE" in texto_atual and len(re.findall(r'ETIQUETA', texto_atual)) == 0

        if eh_lista or eh_danfe or eh_declaracao_conteudo_solta:
            continue

        eh_etiqueta = "DESTINATÁRIO" in texto_atual or "RECEBEDOR" in texto_atual
        if not eh_etiqueta and "REMETENTE" in texto_atual and "ETIQUETA" in texto_atual:
            eh_etiqueta = True

        if eh_etiqueta:
            paginas_etiquetas.append((i, _extrair_ids_tiktok(texto)))

    por_id = _indexar_picking_tiktok(registros_picking)
    etiquetas = []
    for i, ids in paginas_etiquetas:
        dados_sku, pedido, paginas = _vincular_picking_tiktok(ids, por_id)
        # Preserva o vínculo unitário quando etiqueta e lista vieram no mesmo PDF.
        if (
            not uploaded_list
            and ids == (None, None)
            and len(paginas_etiquetas) == len(registros_picking) == 1
        ):
            indice_lista, ids_lista, dados_lista = registros_picking[0]
            if ids_lista is not None and dados_lista:
                dados_sku = dados_lista
                pedido = ids_lista[0] or ids_lista[1]
                paginas = [indice_lista]
        etiquetas.append((i, dados_sku, pedido, paginas))

    etiquetas = ordenar_etiquetas(
        etiquetas,
        sku=lambda etiqueta: etiqueta[1][0] if etiqueta[1] else None,
        quantidade=lambda etiqueta: etiqueta[1][1] if etiqueta[1] else None,
        pedido=lambda etiqueta: etiqueta[2],
    )

    # Move páginas inteiras de picking na sequência das etiquetas. Páginas sem
    # vínculo ficam ao final na ordem original, e cada página é copiada só uma vez.
    ordem_original = list(range(len(doc_lista)))
    ordem_lista = dict.fromkeys(indice_na_lista[indice] for _, _, _, paginas in etiquetas for indice in paginas)
    ordem_lista.update(dict.fromkeys(ordem_original))
    if list(ordem_lista) != ordem_original:
        doc_lista.select(list(ordem_lista))

    count_etiquetas = 0
    w_page = 283
    h_page = 425
    for i, dados_sku, _, _ in etiquetas:
        nova_pagina = doc_etiquetas.new_page(width=w_page, height=h_page)

        if layout is not None:
            from backend.services.etiquetas_layout import renderizar_etiqueta_layout
            renderizar_etiqueta_layout(
                nova_pagina, doc_origem, i, layout=layout, store='TikTok',
                sku_text=f"SKU: {dados_sku[0]}  (x{dados_sku[1]})" if dados_sku else "SKU N/D",
                counter=count_etiquetas + 1, embedded_danfe=True, collector=component_collector,
            )
            count_etiquetas += 1
            if limite_etiquetas and count_etiquetas >= limite_etiquetas:
                break
            continue

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
        
        if limite_etiquetas and count_etiquetas >= limite_etiquetas:
            break

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
