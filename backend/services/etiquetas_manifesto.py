"""Compose marketplace picking lists from whole pages and verified order rows."""

from __future__ import annotations

from dataclasses import dataclass

import fitz


@dataclass(frozen=True)
class LinhaListaPDF:
    pedidos: tuple[str, ...]
    topo: float
    base: float


def _linhas_validas(pagina, linhas):
    # Clipped PDF forms do not retain annotations, links or widgets.
    if pagina.rotation or pagina.first_annot or pagina.first_widget or pagina.get_links():
        return False
    return all(
        linha.pedidos and 0 <= linha.topo < linha.base <= pagina.rect.height
        and (indice == 0 or abs(linhas[indice - 1].base - linha.topo) <= 0.1)
        for indice, linha in enumerate(linhas)
    )


def _inicio_rodape(pagina, base_ultima):
    """Reserve the visible footer while allowing rows to use the blank area above it."""
    marcas = (
        fitz.Rect(caixa)
        for tipo, caixa in pagina.get_bboxlog()
        if tipo != "ignore-text"
    )
    topos = [
        marca.y0 for marca in marcas
        if marca.y0 >= base_ultima and marca.height > 0.5
        and marca.intersects(pagina.rect)
    ]
    return max(base_ultima, min(topos) - 1) if topos else None


def _copiar_faixa(destino, origem, indice, topo, base, *, y):
    largura = origem[indice].rect.width
    recorte = fitz.Rect(0, topo, largura, base)
    destino.show_pdf_page(
        fitz.Rect(0, y, largura, y + recorte.height), origem, indice, clip=recorte,
    )


def inserir_lista_ordenada(
    destino: fitz.Document,
    origem: fitz.Document,
    paginas: dict[int, tuple[str, ...]],
    posicoes: dict[str, int],
    *,
    linhas_por_pagina: dict[int, tuple[LinhaListaPDF, ...]] | None = None,
) -> None:
    """Use label positions for the list and keep unmatched records in source order."""
    linhas_por_pagina = linhas_por_pagina or {}
    recortes = {
        indice: linhas for indice, linhas in linhas_por_pagina.items()
        if indice in paginas and linhas and _linhas_validas(origem[indice], linhas)
    }
    recortar = any(len(linhas) > 1 for linhas in recortes.values())
    itens = [
        (indice, linha)
        for indice in paginas
        for linha in (recortes.get(indice, (None,)) if recortar else (None,))
    ]

    def prioridade(item):
        indice, linha = item
        ids = linha.pedidos if linha else paginas[indice]
        ranks = {posicoes[pedido] for pedido in ids if pedido in posicoes}
        if linha:
            # O mesmo pedido pode ter uma etiqueta de venda e outra de pacote.
            return min(ranks, default=float("inf"))
        return min(ranks) if len(ranks) == 1 else float("inf")

    ordenados = sorted(itens, key=prioridade)
    if ordenados == itens:
        for indice in paginas:
            destino.insert_pdf(origem, from_page=indice, to_page=indice)
        return

    pagina_saida = None
    limites = {}
    for indice, linha in ordenados:
        if linha is None:
            destino.insert_pdf(origem, from_page=indice, to_page=indice)
            pagina_saida = None
            continue

        pagina = origem[indice]
        dimensoes = (pagina.rect.width, pagina.rect.height)
        altura = linha.base - linha.topo
        if pagina_saida is None or dimensoes != dimensoes_atuais or cursor + altura > limite + 0.01:
            pagina_saida = destino.new_page(width=dimensoes[0], height=dimensoes[1])
            dimensoes_atuais = dimensoes
            cursor = recortes[indice][0].topo
            if indice not in limites:
                ultima_base = recortes[indice][-1].base
                limites[indice] = _inicio_rodape(pagina, ultima_base)
            rodape = limites[indice]
            limite = rodape if rodape is not None else max(recortes[indice][-1].base, dimensoes[1] - 16)
            if cursor > 0:
                _copiar_faixa(pagina_saida, origem, indice, 0, cursor, y=0)
            if rodape is not None:
                _copiar_faixa(pagina_saida, origem, indice, rodape, dimensoes[1], y=rodape)

        _copiar_faixa(pagina_saida, origem, indice, linha.topo, linha.base, y=cursor)
        cursor += altura
