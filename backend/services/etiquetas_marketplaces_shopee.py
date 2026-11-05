"""Shopee label PDF processing."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_manifesto import LinhaListaPDF, inserir_lista_ordenada
from backend.services.etiquetas_marketplaces_common import ordenar_etiquetas


_PADRAO_ID_PEDIDO = r"2[0-9]{5}[A-Z0-9]{6,}"
_ID_PEDIDO = re.compile(r"(?<![A-Z0-9])" + _PADRAO_ID_PEDIDO, re.IGNORECASE)
_POSSIVEL_ID_PEDIDO = re.compile(r"(?<![0-9])" + _PADRAO_ID_PEDIDO, re.IGNORECASE)
_CHAVE_FISCAL = re.compile(r"(?<!\d)(?:\d[ \t]*){44}(?![ \t]*\d)")
_LINHA_CORTE = re.compile(r"[\s._\-–—]*corte[\s_]*aqui[\s._\-–—]*", re.IGNORECASE)


def _ids_pedido(texto, *, padrao=_ID_PEDIDO):
    ids = {match[0].upper().split("PACKAGE", 1)[0] for match in padrao.finditer(texto)}
    return {pedido_id for pedido_id in ids if len(pedido_id) != 44 or not pedido_id.isdigit()}


def extract_shopee_data(doc):
    mapa_pedidos = {}
    ambiguos = set()
    blocos = []
    for page in doc:
        text = page.get_text("text")
        if "checklist" in text.lower() or "corte aqui" in text.lower():
            blocos.extend(re.split(r'Corte[\s_]*aqui', text, flags=re.IGNORECASE))

    for bloco in blocos:
        ids = _ids_pedido(bloco)
        if len(ids) != 1:
            continue
        pedido_id = ids.pop()

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

        dados = (sku, qty, product_name)
        if pedido_id in mapa_pedidos and mapa_pedidos[pedido_id] != dados:
            ambiguos.add(pedido_id)
        mapa_pedidos[pedido_id] = dados

    return {pedido_id: dados for pedido_id, dados in mapa_pedidos.items() if pedido_id not in ambiguos}


@dataclass(frozen=True)
class _EtiquetaShopee:
    pagina: int
    recorte: fitz.Rect
    pedido_id: str | None
    sku: str
    quantidade: str
    produto: str
    comprador: str


def _extrair_comprador(texto):
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    termos_ignorados = (
        "NF:", "DANFE", "SÉRIE", "EMISSÃO", "DESTINATÁRIO", "REMETENTE", "RETIRADA", "PELO", "COMPRADOR",
    )
    for i, linha in enumerate(linhas):
        if re.search(r'\d{2}/\d{2}/\d{4}', linha):
            for candidata in linhas[i + 1 : i + 5]:
                if len(candidata) > 2 and not any(termo in candidata.upper() for termo in termos_ignorados):
                    return candidata
    return ""


def _recortes_etiquetas(page):
    rect = page.rect
    if rect.width <= 400:
        return [rect]

    mid_w, mid_h = rect.width / 2, rect.height / 2
    return [
        fitz.Rect(0, mid_h, mid_w, rect.height),
        fitz.Rect(mid_w, mid_h, rect.width, rect.height),
        fitz.Rect(0, 0, mid_w, mid_h),
        fitz.Rect(mid_w, 0, rect.width, mid_h),
    ]


def _linhas_checklist(page, ids_pagina):
    # Só recorta quando há divisórias horizontais e um pedido por seção.
    if page.rotation or len(ids_pagina) < 2:
        return ()

    cortes = set()
    blocos = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
    for bloco in blocos:
        for linha in bloco.get("lines", ()):
            texto = "".join(span["text"] for span in linha["spans"])
            if linha["dir"] == (1.0, 0.0) and _LINHA_CORTE.fullmatch(texto):
                cortes.add(linha["bbox"][3])
    if not cortes:
        return ()

    area = page.rect
    limites = [area.y0, *sorted(corte for corte in cortes if area.y0 < corte < area.y1), area.y1]
    secoes = [
        _ids_pedido(page.get_text(clip=fitz.Rect(area.x0, topo, area.x1, base)))
        for topo, base in zip(limites, limites[1:])
    ]
    com_pedido = [indice for indice, ids in enumerate(secoes) if ids]
    if not com_pedido:
        return ()

    primeiro, ultimo = com_pedido[0], com_pedido[-1]
    linhas = []
    for indice in range(primeiro, ultimo + 1):
        ids = secoes[indice]
        if len(ids) != 1:
            return ()
        linhas.append(LinhaListaPDF(
            pedidos=tuple(ids), topo=limites[indice], base=limites[indice + 1],
        ))

    return tuple(linhas) if {linha.pedidos[0] for linha in linhas} == ids_pagina else ()


def processar_shopee(uploaded_file, *, layout=None, component_collector=None, limite_etiquetas=None):
    try:
        with (
            fitz.open(stream=uploaded_file.read(), filetype="pdf") as doc,
            fitz.open() as output_labels,
            fitz.open() as output_manifest,
        ):
            mapa_global = extract_shopee_data(doc)
            etiquetas = []
            paginas_manifesto = {}
            linhas_manifesto = {}
            for page_index, page in enumerate(doc):
                text = page.get_text("text").lower()
                if any(termo in text for termo in ("checklist", "manifesto", "corte aqui")):
                    ids = _ids_pedido(text)
                    paginas_manifesto[page_index] = tuple(ids) if len(ids) == 1 else ()
                    linhas = _linhas_checklist(page, ids)
                    if linhas:
                        linhas_manifesto[page_index] = linhas
                    continue

                for recorte in _recortes_etiquetas(page):
                    texto = page.get_text(clip=recorte)
                    ids = _ids_pedido(texto)
                    if not ids:
                        texto_sem_chave = _CHAVE_FISCAL.sub(" ", texto)
                        texto_compacto = re.sub(r"\s+", "", texto_sem_chave)
                        if not _ids_pedido(texto_compacto, padrao=_POSSIVEL_ID_PEDIDO):
                            continue

                    conhecidos = ids.intersection(mapa_global)
                    candidatos = conhecidos or ids
                    pedido_id = next(iter(candidatos)) if len(candidatos) == 1 else None
                    sku, quantidade, produto = mapa_global.get(pedido_id, ("N/D", "1", ""))
                    etiquetas.append(_EtiquetaShopee(
                        page_index, recorte, pedido_id, sku, quantidade, produto, _extrair_comprador(texto),
                    ))

            etiquetas = ordenar_etiquetas(
                etiquetas,
                sku=lambda etiqueta: etiqueta.sku,
                quantidade=lambda etiqueta: etiqueta.quantidade,
                pedido=lambda etiqueta: etiqueta.pedido_id,
            )
            posicoes = {}
            for posicao, etiqueta in enumerate(etiquetas):
                if etiqueta.pedido_id:
                    posicoes.setdefault(etiqueta.pedido_id, posicao)
            inserir_lista_ordenada(
                output_manifest, doc, paginas_manifesto, posicoes, linhas_por_pagina=linhas_manifesto,
            )

            if limite_etiquetas:
                etiquetas = etiquetas[:limite_etiquetas]

            for numero, etiqueta in enumerate(etiquetas, start=1):
                new_page = output_labels.new_page(width=283, height=425)
                tem_sku = etiqueta.sku != "N/D"
                sku_text = f"SKU: {etiqueta.sku} (x{etiqueta.quantidade})" if tem_sku else "SKU N/D"
                if layout is not None:
                    from backend.services.etiquetas_layout import renderizar_etiqueta_layout

                    renderizar_etiqueta_layout(
                        new_page, doc, etiqueta.pagina, clip=etiqueta.recorte, layout=layout,
                        sku_text=sku_text, store="Shopee", extra_text=f"{etiqueta.produto}\n{etiqueta.comprador}",
                        counter=numero, embedded_danfe=True, collector=component_collector,
                    )
                    continue

                new_page.show_pdf_page(new_page.rect, doc, etiqueta.pagina, clip=etiqueta.recorte)
                color = (0, 0, 0) if tem_sku else (1, 0, 0)
                new_page.insert_text(fitz.Point(279, 350), sku_text, fontsize=12, color=color, rotate=90)
                if tem_sku and etiqueta.produto:
                    new_page.insert_text(
                        fitz.Point(265, 350), etiqueta.produto[:45], fontsize=8, color=(0, 0, 0), rotate=90,
                    )
                if etiqueta.comprador:
                    new_page.insert_text(
                        fitz.Point(250, 350), etiqueta.comprador[:35], fontsize=9, color=(0, 0, 0), rotate=90,
                    )
                new_page.insert_text(fitz.Point(240, 20), f"#{numero}", fontsize=10, color=(0.5, 0.5, 0.5))

            labels_bytes = output_labels.tobytes() if etiquetas else None
            manifest_bytes = output_manifest.tobytes() if len(output_manifest) > 0 else None
            return labels_bytes, manifest_bytes, len(etiquetas)

    except Exception as e:
        marketplace_ctx.st.error(f"Erro no processamento Shopee: {str(e)}")
        return None, None, 0
