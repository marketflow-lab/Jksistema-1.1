"""Shopee label PDF processing."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import fitz

from backend.services import etiquetas_marketplaces_context as marketplace_ctx
from backend.services.etiquetas_checklist_shopee import ItemChecklistShopee, inserir_checklist_shopee
from backend.services.etiquetas_manifesto import LinhaListaPDF
from backend.services.etiquetas_marketplaces_common import desenhar_sku_lateral, ordenar_etiquetas


_PADRAO_ID_PEDIDO = r"2[0-9]{5}[A-Z0-9]{6,}"
_ID_PEDIDO = re.compile(r"(?<![A-Z0-9])" + _PADRAO_ID_PEDIDO, re.IGNORECASE)
_POSSIVEL_ID_PEDIDO = re.compile(r"(?<![0-9])" + _PADRAO_ID_PEDIDO, re.IGNORECASE)
_CHAVE_FISCAL = re.compile(r"(?<!\d)(?:\d[ \t]*){44}(?![ \t]*\d)")
_LINHA_CORTE = re.compile(r"[\s._\-–—]*corte[\s_]*aqui[\s._\-–—]*", re.IGNORECASE)
_PEDIDO_CHECKLIST = re.compile(r"(?:(?:ID\s+(?:DO\s+)?)?PEDIDO\s*:?\s*)?(" + _PADRAO_ID_PEDIDO + r")", re.IGNORECASE)
_CABECALHO_ITENS = re.compile(r"produto\s+varia[çc][ãa]o\s+(?:qnt|qtd|quantidade)\s+sku", re.IGNORECASE)
_TITULO_CHECKLIST = re.compile(r"checklist(?:\s+de\s+carregamento)?", re.IGNORECASE)


def _ids_pedido(texto, *, padrao=_ID_PEDIDO):
    ids = {match[0].upper().split("PACKAGE", 1)[0] for match in padrao.finditer(texto)}
    return {pedido_id for pedido_id in ids if len(pedido_id) != 44 or not pedido_id.isdigit()}


def _blocos_checklist(textos):
    for texto in textos:
        if "checklist" in texto.casefold() or "corte aqui" in texto.casefold():
            yield from re.split(r"Corte[\s_]*aqui", texto, flags=re.IGNORECASE)


def extract_shopee_data(doc):
    return _extrair_dados_checklist(page.get_text("text") for page in doc)


def _extrair_dados_checklist(textos):
    mapa_pedidos = {}
    ambiguos = set()
    for bloco in _blocos_checklist(textos):
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


def _item_checklist(texto):
    linhas = [
        linha.strip() for linha in texto.splitlines()
        if linha.strip() and not _LINHA_CORTE.fullmatch(linha) and not re.fullmatch(r"[-_.–—\s]+", linha)
    ]
    if linhas and re.fullmatch(r"ID\s+(?:DO\s+)?PEDIDO\s*:?", linhas[0], re.IGNORECASE):
        linhas = linhas[1:]
    if len(linhas) < 5 or not _TITULO_CHECKLIST.fullmatch(linhas[-1]):
        return None
    pedido = _PEDIDO_CHECKLIST.fullmatch(linhas[0])
    quantidade, sku = linhas[-3:-1]
    if not pedido or not re.fullmatch(r"[0-9]{1,4}", quantidade) or int(quantidade) < 1:
        return None
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{0,59}", sku, re.IGNORECASE):
        return None
    if sku.upper() in {"N/D", "SKU", "UNIVERSAL", "GERADOR", "DIRIGIR", "PRODUTO", "SHOPEE"}:
        return None
    produto = linhas[1:-3]
    for tamanho in (1, 4):
        if _CABECALHO_ITENS.fullmatch(" ".join(produto[:tamanho])):
            produto = produto[tamanho:]
            break
    if produto and produto[0] == "1":
        produto = produto[1:]
    # Um segundo índice ou quantidade revela mais de um item na seção.
    if not produto or any(
        parte.isdigit() or _TITULO_CHECKLIST.fullmatch(parte) or _ids_pedido(parte)
        for parte in produto
    ):
        return None
    return ItemChecklistShopee(pedido[1].upper(), sku, int(quantidade), tuple(produto))


def _texto_secao(page, recorte):
    return page.get_text(clip=(page.rect.x0, recorte.topo, page.rect.x1, recorte.base))


def _itens_confirmados(doc, secoes, textos_paginas):
    itens = {}
    ambiguos = set()
    for indice, (_, recortes) in secoes.items():
        page = doc[indice]
        blocos = (
            (_texto_secao(page, recorte) for recorte in recortes)
            if recortes else (textos_paginas[indice],)
        )
        for bloco in blocos:
            ids = _ids_pedido(bloco)
            if not ids:
                continue
            item = _item_checklist(bloco)
            if len(ids) != 1 or not item or item.pedido not in ids:
                ambiguos.update(ids)
                continue
            if item.pedido in itens and itens[item.pedido] != item:
                ambiguos.add(item.pedido)
            itens[item.pedido] = item
    return {pedido: item for pedido, item in itens.items() if pedido not in ambiguos}


@dataclass(frozen=True)
class _EtiquetaShopee:
    pagina: int
    recorte: fitz.Rect
    pedido_id: str | None
    sku: str
    quantidade: str
    comprador: str
    quantidade_confirmada: int | None


def _extrair_comprador(texto):
    linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
    termos_ignorados = (
        "NF:", "DANFE", "SÉRIE", "EMISSÃO", "DESTINATÁRIO", "REMETENTE", "RETIRADA", "PELO", "COMPRADOR",
        "PEDIDO", "ENDEREÇO", "CEP", "RUA ", "AVENIDA ", "SHOPEE",
    )
    for linha in linhas:
        nome = re.fullmatch(r"(?:destinat[áa]rio|recebedor|comprador)\s*:\s*(.+)", linha, re.IGNORECASE)
        if nome and any(letra.isalpha() for letra in nome[1]) and not any(letra.isdigit() for letra in nome[1]):
            return nome[1].strip()
    for i, linha in enumerate(linhas):
        if re.search(r'\d{2}/\d{2}/\d{4}', linha):
            for candidata in linhas[i + 1 : i + 5]:
                if (
                    len(candidata.split()) >= 2 and all(letra.isalpha() or letra in " '-." for letra in candidata)
                    and not any(termo in candidata.upper() for termo in termos_ignorados)
                ):
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


def _checklist_seguro(page, ids, recortes, itens, compradores):
    if page.rotation or page.first_annot or page.first_widget or page.get_links():
        return None
    if any(tipo in {"fill-image", "fill-shade"} for tipo, _ in page.get_bboxlog()):
        return None
    # Uma forma pequena não identificada pode ser código, marca de conferência ou símbolo do PDF original.
    for desenho in page.get_drawings():
        caixa = desenho["rect"]
        if caixa.width > 2 and caixa.height > 2:
            itens_graficos = desenho["items"]
            if len(itens_graficos) != 1 or itens_graficos[0][0] != "re" or caixa.width < page.rect.width / 2:
                return None
            if desenho.get("fill") and min(desenho["fill"]) < 0.7:
                return None

    if len(ids) == 1 and not recortes:
        recortes = (LinhaListaPDF(tuple(ids), page.rect.y0, page.rect.y1),)
    if not recortes:
        return None
    linhas = []
    for recorte in recortes:
        pedido = recorte.pedidos[0]
        item = _item_checklist(_texto_secao(page, recorte))
        if not item or item != itens.get(pedido) or not compradores.get(pedido):
            return None
        linhas.append((item, compradores[pedido]))
    cabecalho = page.get_text(clip=(page.rect.x0, page.rect.y0, page.rect.x1, recortes[0].topo))
    rodape = page.get_text(clip=(page.rect.x0, recortes[-1].base, page.rect.x1, page.rect.y1))
    contexto = tuple(
        tuple(linha.strip() for linha in trecho.splitlines() if linha.strip() and not _LINHA_CORTE.fullmatch(linha))
        for trecho in (cabecalho, rodape)
    )
    return linhas, contexto


def processar_shopee(uploaded_file, *, layout=None, component_collector=None, limite_etiquetas=None):
    try:
        with (
            fitz.open(stream=uploaded_file.read(), filetype="pdf") as doc,
            fitz.open() as output_labels,
            fitz.open() as output_manifest,
        ):
            textos_paginas = [page.get_text("text") for page in doc]
            mapa_global = _extrair_dados_checklist(textos_paginas)
            paginas_manifesto = {}
            secoes_manifesto = {}
            contagem_pedidos = Counter()
            for page_index, texto_pagina in enumerate(textos_paginas):
                text = texto_pagina.casefold()
                if any(termo in text for termo in ("checklist", "manifesto", "corte aqui")):
                    ids = _ids_pedido(text)
                    paginas_manifesto[page_index] = tuple(ids) if len(ids) == 1 else ()
                    linhas = _linhas_checklist(doc[page_index], ids)
                    secoes_manifesto[page_index] = (ids, linhas)
                    contagem_pedidos.update(ids)
                    contagem_pedidos.update(
                        pedido for pedido, total in Counter(linha.pedidos[0] for linha in linhas).items()
                        if total > 1
                    )
            itens_confirmados = _itens_confirmados(doc, secoes_manifesto, textos_paginas)
            etiquetas = []
            for page_index, page in enumerate(doc):
                if page_index in paginas_manifesto:
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
                    sku, quantidade, _ = mapa_global.get(pedido_id, ("N/D", "1", ""))
                    item = itens_confirmados.get(pedido_id)
                    etiquetas.append(_EtiquetaShopee(
                        page_index, recorte, pedido_id, item.sku if item else sku,
                        str(item.quantidade) if item else quantidade, _extrair_comprador(texto),
                        item.quantidade if item else None,
                    ))

            etiquetas = ordenar_etiquetas(
                etiquetas,
                sku=lambda etiqueta: etiqueta.sku,
                quantidade=lambda etiqueta: etiqueta.quantidade,
                pedido=lambda etiqueta: etiqueta.pedido_id,
            )
            posicoes = {}
            nomes = {}
            for posicao, etiqueta in enumerate(etiquetas):
                if etiqueta.pedido_id:
                    posicoes.setdefault(etiqueta.pedido_id, posicao)
                    if etiqueta.comprador:
                        nomes.setdefault(etiqueta.pedido_id, set()).add(etiqueta.comprador)
            compradores = {
                pedido: next(iter(nomes_pedido)) for pedido, nomes_pedido in nomes.items() if len(nomes_pedido) == 1
            }
            checklists = {}
            for indice, (ids, linhas) in secoes_manifesto.items():
                if ids and all(contagem_pedidos[pedido] == 1 for pedido in ids):
                    checklist = _checklist_seguro(doc[indice], ids, linhas, itens_confirmados, compradores)
                    if checklist:
                        checklists[indice] = checklist
            linhas_originais = {indice: linhas for indice, (_, linhas) in secoes_manifesto.items() if linhas}
            inserir_checklist_shopee(output_manifest, doc, paginas_manifesto, posicoes, checklists, linhas_originais)

            if limite_etiquetas:
                etiquetas = etiquetas[:limite_etiquetas]

            for numero, etiqueta in enumerate(etiquetas, start=1):
                new_page = output_labels.new_page(width=283, height=425)
                tem_sku = etiqueta.sku != "N/D"
                sku_text = f"SKU: {etiqueta.sku}" if tem_sku else "SKU N/D"
                if tem_sku and etiqueta.quantidade_confirmada:
                    sku_text += f" (x{etiqueta.quantidade_confirmada})"
                if layout is not None:
                    from backend.services.etiquetas_layout import renderizar_etiqueta_layout

                    renderizar_etiqueta_layout(
                        new_page, doc, etiqueta.pagina, clip=etiqueta.recorte, layout=layout,
                        sku_text=sku_text, store="Shopee", quantidade_sku=etiqueta.quantidade_confirmada,
                        counter=numero, embedded_danfe=True, collector=component_collector,
                    )
                    continue

                new_page.insert_font(fontname="hebo")
                new_page.insert_font(fontname="helv")
                new_page.show_pdf_page(new_page.rect, doc, etiqueta.pagina, clip=etiqueta.recorte)
                desenhar_sku_lateral(new_page, sku_text, loja="Shopee", quantidade=etiqueta.quantidade_confirmada)
                new_page.insert_text(fitz.Point(240, 20), f"#{numero}", fontsize=10, color=(0.5, 0.5, 0.5))

            labels_bytes = output_labels.tobytes() if etiquetas else None
            manifest_bytes = output_manifest.tobytes() if len(output_manifest) > 0 else None
            return labels_bytes, manifest_bytes, len(etiquetas)

    except Exception as e:
        marketplace_ctx.st.error(f"Erro no processamento Shopee: {str(e)}")
        return None, None, 0
