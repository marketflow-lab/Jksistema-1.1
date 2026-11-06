"""Compose verified Shopee order blocks in the grouped checklist."""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from backend.services.etiquetas_manifesto import (
    LinhaListaPDF,
    ProdutoListaPDF,
    SeloQuantidadeListaPDF,
    inserir_lista_ordenada,
)


_PAGINA_A4 = fitz.paper_rect("a4")
_MARGEM = 24
_CINZA = (0.30,) * 3
_FONTES = {nome: fitz.Font(fontname=nome) for nome in ("helv", "hebo")}


@dataclass(frozen=True)
class ItemChecklistShopee:
    pedido: str
    sku: str
    quantidade: int
    produto: tuple[str, ...]


def _texto_cabe(texto, largura, fonte="helv", minimo=7):
    try:
        texto.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return _FONTES[fonte].text_length(texto, fontsize=minimo) <= largura


def _desenhar_texto(pagina, texto, x, base, largura, *, fonte="helv", tamanho=9, cor=(0, 0, 0), centralizado=False):
    largura_texto = _FONTES[fonte].text_length(texto, fontsize=1)
    tamanho = min(tamanho, largura / largura_texto) if largura_texto else tamanho
    if centralizado:
        x -= largura_texto * tamanho / 2
    pagina.insert_text((x, base), texto, fontname=fonte, fontsize=tamanho, color=cor)


def _quebrar_produto(partes, largura):
    linhas = []
    for parte in partes:
        atual = ""
        for palavra in parte.split():
            candidata = f"{atual} {palavra}" if atual else palavra
            if atual and not _texto_cabe(candidata, largura, minimo=9):
                linhas.append(atual)
                atual = palavra
            else:
                atual = candidata
        if atual:
            linhas.append(atual)
    return linhas


def _inserir_bloco(documento, item, comprador, contexto, produto):
    pagina = documento.new_page(width=_PAGINA_A4.width, height=_PAGINA_A4.height)
    largura = pagina.rect.width - _MARGEM * 2
    cabecalho, rodape = contexto
    pagina.insert_text((_MARGEM, 25), "Shopee", fontname="hebo", fontsize=9, color=_CINZA)
    pagina.insert_text((_MARGEM, 51), "Checklist por SKU", fontname="hebo", fontsize=20)
    pagina.insert_text(
        (_MARGEM, 69), "Os blocos de pedido continuam juntos, com o total de unidades acima.",
        fontsize=8, color=_CINZA,
    )
    for indice, trecho in enumerate(cabecalho):
        _desenhar_texto(pagina, trecho, _MARGEM, 82 + 10 * indice, largura, tamanho=8, cor=_CINZA)
    for indice, trecho in enumerate(rodape):
        base = pagina.rect.height - _MARGEM - 10 * (len(rodape) - indice - 1)
        _desenhar_texto(pagina, trecho, _MARGEM, base, largura, tamanho=8, cor=_CINZA)

    topo = 83 + 10 * len(cabecalho)
    base = topo + 36 + 12 * (len(produto) - 1)
    esquerda, direita = _MARGEM, pagina.rect.x1 - _MARGEM
    primeira_linha = topo + 14
    _desenhar_texto(pagina, f"Pedido {item.pedido}", esquerda + 8, primeira_linha, 165, fonte="hebo")
    _desenhar_texto(
        pagina, comprador, pagina.rect.width / 2, primeira_linha, 190, fonte="hebo", centralizado=True,
    )
    for indice, trecho in enumerate(produto):
        _desenhar_texto(pagina, trecho, esquerda + 8, topo + 27 + 12 * indice, largura - 16, cor=_CINZA)
    pagina.insert_text((direita - 125, primeira_linha), "Quantidade", fontsize=8, color=_CINZA)
    x_quantidade = direita - 77
    pagina.insert_text((x_quantidade, primeira_linha), str(item.quantidade), fontname="hebo", fontsize=9)
    pagina.draw_line((esquerda, base), (direita, base), color=(0.80,) * 3, width=0.5)

    selos = ()
    if item.quantidade > 1:
        texto = f"{item.quantidade} UNIDADES"
        tamanho = 7.4
        largura_selo = fitz.get_text_length(texto, fontname="hebo", fontsize=tamanho) + 8
        caixa = fitz.Rect(
            x_quantidade - 0.5, primeira_linha - 8.5,
            x_quantidade - 0.5 + largura_selo, primeira_linha + 2.8,
        )
        selos = (SeloQuantidadeListaPDF(texto, caixa, primeira_linha, tamanho),)
    return LinhaListaPDF(
        (item.pedido,), topo, base, ProdutoListaPDF(item.sku, item.quantidade, (esquerda, direita), selos),
    )


def inserir_checklist_shopee(destino, origem, paginas, posicoes, checklists, linhas_originais):
    if not checklists:
        inserir_lista_ordenada(destino, origem, paginas, posicoes, linhas_por_pagina=linhas_originais)
        return
    # Só reúna páginas de um mesmo modelo, para conservar cabeçalhos e rodapés distintos.
    contextos = {contexto for _, contexto in checklists.values()}
    if len(contextos) != 1:
        inserir_lista_ordenada(destino, origem, paginas, posicoes, linhas_por_pagina=linhas_originais)
        return

    contexto = contextos.pop()
    largura = _PAGINA_A4.width - _MARGEM * 2
    if any(len(trechos) > 5 or any(not _texto_cabe(trecho, largura) for trecho in trechos) for trechos in contexto):
        inserir_lista_ordenada(destino, origem, paginas, posicoes, linhas_por_pagina=linhas_originais)
        return

    with fitz.open() as normalizado:
        paginas_normalizadas = {}
        recortes = {}
        for indice, ids in paginas.items():
            pedidos = [
                (item, comprador, _quebrar_produto(item.produto, largura - 16))
                for item, comprador in checklists.get(indice, ((), ()))[0]
            ]
            if not pedidos or any(
                not _texto_cabe(f"Pedido {item.pedido}", 165, "hebo")
                or not _texto_cabe(comprador, 190, "hebo")
                or not _texto_cabe(item.sku, largura - 155, "hebo")
                or len(produto) > 20
                or any(not _texto_cabe(trecho, largura - 16) for trecho in produto)
                for item, comprador, produto in pedidos
            ):
                paginas_normalizadas[len(normalizado)] = ids
                normalizado.insert_pdf(origem, from_page=indice, to_page=indice)
                continue
            for item, comprador, produto in pedidos:
                indice_normalizado = len(normalizado)
                linha = _inserir_bloco(normalizado, item, comprador, contexto, produto)
                paginas_normalizadas[indice_normalizado] = linha.pedidos
                recortes[indice_normalizado] = (linha,)
        inserir_lista_ordenada(destino, normalizado, paginas_normalizadas, posicoes, linhas_por_pagina=recortes)
