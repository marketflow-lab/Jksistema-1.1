"""Compose marketplace picking lists from whole pages and verified order rows."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import groupby

import fitz


_ALTURA_CABECALHO_SKU = 23
_CINZA_GRUPO = 219 / 255


@dataclass(frozen=True)
class SeloQuantidadeListaPDF:
    texto: str
    caixa: fitz.Rect
    linha_base: float
    tamanho: float


@dataclass(frozen=True)
class ProdutoListaPDF:
    sku: str
    quantidade: int | None
    bordas: tuple[float, float]
    selos: tuple[SeloQuantidadeListaPDF, ...] = ()


@dataclass(frozen=True)
class LinhaListaPDF:
    pedidos: tuple[str, ...]
    topo: float
    base: float
    produto: ProdutoListaPDF | None = None


@dataclass(frozen=True)
class _GrupoSKU:
    sku: str
    total: int | None
    cinza: bool


def _agrupar_skus(itens):
    def sku_do_item(item):
        _, linha = item
        return linha.produto.sku if linha and linha.produto else None

    cinza = False
    for sku, grupo in groupby(itens, key=sku_do_item):
        if sku is None:
            for item in grupo:
                yield item, None
            continue
        linhas = list(grupo)
        quantidades = [linha.produto.quantidade for _, linha in linhas]
        total = sum(quantidades) if None not in quantidades else None
        grupo_sku = _GrupoSKU(sku, total, cinza)
        for item in linhas:
            yield item, grupo_sku
        cinza = not cinza


def _desenhar_cabecalho_sku(pagina, topo, bordas, grupo):
    esquerda, direita = bordas
    base = topo + _ALTURA_CABECALHO_SKU
    if grupo.cinza:
        pagina.draw_rect((esquerda, topo, direita, base), color=None, fill=(_CINZA_GRUPO,) * 3)
    pagina.draw_line((esquerda, topo), (direita, topo), color=(0.4,) * 3, width=1.25)
    pagina.draw_line((esquerda, base), (direita, base), color=(0.66,) * 3, width=0.4)
    pagina.draw_circle((esquerda + 10, topo + 11.5), 3, color=None, fill=(0.13,) * 3)

    unidade = "item" if grupo.total == 1 else "itens"
    total = f"Total: {grupo.total} {unidade}" if grupo.total is not None else "Total indisponível"
    sku = f"SKU {grupo.sku}"
    largura_sku = fitz.get_text_length(sku, fontname="hebo", fontsize=1)
    largura_total = fitz.get_text_length(total, fontname="hebo", fontsize=1)
    tamanho = min(10, (direita - esquerda - 35) / (largura_sku + largura_total))
    pagina.insert_text((esquerda + 19, topo + 15), sku, fontname="hebo", fontsize=tamanho)
    pagina.insert_text(
        (direita - 8 - largura_total * tamanho, topo + 15),
        total, fontname="hebo", fontsize=tamanho,
    )


@lru_cache(maxsize=64)
def _imagem_selo_quantidade(texto, largura, altura, linha_base, tamanho):
    # A imagem destaca a quantidade sem duplicar o numeral pesquisável do PDF.
    with fitz.open() as selo:
        pagina = selo.new_page(width=largura, height=altura)
        pagina.draw_rect(pagina.rect, color=None, fill=(0.19,) * 3, radius=0.2)
        pagina.insert_text((4, linha_base), texto, fontname="hebo", fontsize=tamanho, color=(1, 1, 1))
        return pagina.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=True).tobytes("png")


def _desenhar_selos_quantidade(pagina, linha, y, altura):
    escala = altura / (linha.base - linha.topo)
    for selo in linha.produto.selos:
        caixa = fitz.Rect(
            selo.caixa.x0, y + (selo.caixa.y0 - linha.topo) * escala,
            selo.caixa.x1, y + (selo.caixa.y1 - linha.topo) * escala,
        )
        imagem = _imagem_selo_quantidade(
            selo.texto, round(selo.caixa.width, 2), round(selo.caixa.height, 2),
            round(selo.linha_base - selo.caixa.y0, 2), round(selo.tamanho, 2),
        )
        pagina.insert_image(caixa, stream=imagem, keep_proportion=False)


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


def _copiar_faixa(destino, origem, indice, topo, base, *, y, altura=None):
    largura = origem[indice].rect.width
    recorte = fitz.Rect(0, topo, largura, base)
    destino.show_pdf_page(
        fitz.Rect(0, y, largura, y + (altura if altura is not None else recorte.height)),
        origem, indice, clip=recorte, keep_proportion=altura is None,
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
    agrupar = any(linha.produto for linhas in recortes.values() for linha in linhas)
    recortar = agrupar or any(len(linhas) > 1 for linhas in recortes.values())
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
    if ordenados == itens and not agrupar:
        for indice in paginas:
            destino.insert_pdf(origem, from_page=indice, to_page=indice)
        return

    pagina_saida = None
    grupo_atual = None
    limites = {}
    for (indice, linha), grupo in _agrupar_skus(ordenados):
        if linha is None:
            destino.insert_pdf(origem, from_page=indice, to_page=indice)
            pagina_saida = None
            continue

        pagina = origem[indice]
        dimensoes = (pagina.rect.width, pagina.rect.height)
        altura = linha.base - linha.topo
        altura_cabecalho = _ALTURA_CABECALHO_SKU if grupo and grupo is not grupo_atual else 0
        if pagina_saida is None or dimensoes != dimensoes_atuais or cursor + altura + altura_cabecalho > limite + 0.01:
            pagina_saida = destino.new_page(width=dimensoes[0], height=dimensoes[1])
            if agrupar:
                # A fonte do cabeçalho deve pertencer à página, não aos recortes importados.
                pagina_saida.insert_font(fontname="hebo")
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
            grupo_atual = None

        altura_ajustada = None
        if grupo and grupo is not grupo_atual:
            disponivel = limite - cursor - _ALTURA_CABECALHO_SKU
            # Uma linha muito alta pode ceder até 10% de sua altura sem perder conteúdo.
            if disponivel >= altura * 0.9:
                _desenhar_cabecalho_sku(pagina_saida, cursor, linha.produto.bordas, grupo)
                cursor += _ALTURA_CABECALHO_SKU
                grupo_atual = grupo
                if disponivel < altura:
                    altura_ajustada = altura = disponivel
        _copiar_faixa(pagina_saida, origem, indice, linha.topo, linha.base, y=cursor, altura=altura_ajustada)
        if grupo and grupo.cinza:
            esquerda, direita = linha.produto.bordas
            # Escurece o fundo do PDF original preservando a tinta preta.
            pagina_saida.draw_rect(
                (esquerda, cursor, direita, cursor + altura),
                color=None, fill=(0, 0, 0), fill_opacity=1 - _CINZA_GRUPO,
            )
        if linha.produto and linha.produto.selos:
            _desenhar_selos_quantidade(pagina_saida, linha, cursor, altura)
        if grupo is None:
            grupo_atual = None
        cursor += altura
