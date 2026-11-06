"""Leitura confirmada de itens e resumo de separação do TikTok."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from itertools import groupby

import fitz


_CABECALHO = re.compile(r"\bProduct[ \t]+Name\s+Seller[ \t]+SKU\s+Qty\b[ \t]*\n", re.IGNORECASE)
_RODAPE = re.compile(r"^Qty[ \t]+Total(?:(?:[ \t]*:[ \t]*|[ \t]+)([0-9]{1,8}))?[ \t]*$", re.IGNORECASE | re.MULTILINE)
_SKU = re.compile(r"[A-Z0-9][A-Z0-9_./-]{0,59}", re.IGNORECASE | re.ASCII)
_QTD = re.compile(r"[0-9]{1,4}")
_LINHA_COMPACTA = re.compile(r"(.+?)\s+(\S+)\s+([0-9]{1,4})")

_COLUNAS_TABELA_PACKING = ("product name", "sku", "seller sku", "qty")
_COLUNAS_TABELA = (*_COLUNAS_TABELA_PACKING, "order id")
_TOTAL_PACKING = re.compile(r"Qty\s+Total\s*:?\s*([0-9]{1,8})?", re.IGNORECASE)
_PEDIDO_TABELA = re.compile(r"[0-9]{10,}")
_TOLERANCIA_TABELA = 2

_CINZA = (219 / 255,) * 3
_PRETO = (0.13,) * 3
_SEPARADOR = (0.65,) * 3
_BRANCO = (1,) * 3
_TOPO_TABELA = 137
_ALTURA_GRUPO = 26
_ALTURA_ITEM = 39
_FIM_TABELA = 794
_FONTES = {nome: fitz.Font(fontname=nome) for nome in ("helv", "hebo")}


@dataclass(frozen=True)
class ItemPickingTikTok:
    sku: str
    produto: str
    quantidade: int


def _rodape_tabela_packing(linhas, y_cabecalho, x_qtd):
    rodapes = [
        (x, y, total[1])
        for x, y, texto in linhas
        if y > y_cabecalho + _TOLERANCIA_TABELA and (total := _TOTAL_PACKING.fullmatch(texto))
    ]
    if len(rodapes) != 1:
        return None

    x_rodape, y_rodape, total = rodapes[0]
    if total is None:
        valores = [
            texto
            for x, y, texto in linhas
            if x > x_rodape and x >= x_qtd and abs(y - y_rodape) <= _TOLERANCIA_TABELA
            and re.fullmatch(r"[0-9]{1,8}", texto)
        ]
        if len(valores) != 1:
            return None
        total = valores[0]
    return y_rodape, int(total)


def _extrair_tabela_tiktok(pagina: fitz.Page, colunas_tabela: tuple[str, ...]):
    # get_text pode juntar células vizinhas. Separe os trechos também pela altura da linha.
    linhas = []
    for trecho in pagina.get_texttrace():
        for _, grupo in groupby(trecho["chars"], key=lambda caractere: round(caractere[2][1], 1)):
            caracteres = list(grupo)
            texto = " ".join("".join(chr(codigo) for codigo, _, _, _ in caracteres).split())
            if texto:
                x, y = caracteres[0][2]
                linhas.append((x, y, texto))

    nomes = [(x, y, texto.casefold()) for x, y, texto in linhas if texto.casefold() in _COLUNAS_TABELA]
    cabecalhos = []
    for _, y, nome in nomes:
        if nome != "product name":
            continue
        colunas = sorted((x, titulo) for x, yy, titulo in nomes if abs(yy - y) <= _TOLERANCIA_TABELA)
        if tuple(titulo for _, titulo in colunas) == colunas_tabela:
            cabecalhos.append((y, [x - _TOLERANCIA_TABELA for x, _ in colunas]))
    if not cabecalhos:
        return None
    if len(cabecalhos) != 1:
        return ()

    y_cabecalho, limites = cabecalhos[0]
    com_pedido_na_linha = colunas_tabela == _COLUNAS_TABELA
    fim_tabela = float("inf")
    total = None
    if not com_pedido_na_linha:
        rodape = _rodape_tabela_packing(linhas, y_cabecalho, limites[-1])
        if rodape is None:
            return ()
        y_rodape, total = rodape
        fim_tabela = y_rodape - _TOLERANCIA_TABELA

    celulas = [[] for _ in colunas_tabela]
    for x, y, texto in linhas:
        if y_cabecalho + _TOLERANCIA_TABELA < y < fim_tabela and x >= limites[0]:
            celulas[bisect_right(limites, x) - 1].append((y, x, texto))
    produtos, _, skus, quantidades, *coluna_pedidos = map(sorted, celulas)
    linhas_pedido = coluna_pedidos[0] if com_pedido_na_linha else [(y, x, "") for y, x, _ in quantidades]
    if not linhas_pedido or not (len(skus) == len(quantidades) == len(linhas_pedido)):
        return ()

    itens = []
    indice_produto = 0
    for indice, ((y_linha, _, pedido), (y_sku, _, sku), (y_qtd, _, qtd)) in enumerate(
        zip(linhas_pedido, skus, quantidades)
    ):
        if (
            max(y_linha, y_sku, y_qtd) - min(y_linha, y_sku, y_qtd) > _TOLERANCIA_TABELA
            or (com_pedido_na_linha and not _PEDIDO_TABELA.fullmatch(pedido))
            or not _SKU.fullmatch(sku)
            or not _QTD.fullmatch(qtd)
            or int(qtd) < 1
            or indice_produto == len(produtos)
            or abs(produtos[indice_produto][0] - y_linha) > _TOLERANCIA_TABELA
        ):
            return ()
        fim = linhas_pedido[indice + 1][0] - _TOLERANCIA_TABELA if indice + 1 < len(linhas_pedido) else fim_tabela
        partes = []
        while indice_produto < len(produtos) and produtos[indice_produto][0] < fim:
            partes.append(produtos[indice_produto][2])
            indice_produto += 1
        if not partes:
            return ()
        itens.append((pedido, ItemPickingTikTok(sku, " ".join(partes), int(qtd))))
    if total is not None and sum(item.quantidade for _, item in itens) != total:
        return ()
    return tuple(itens)


def extrair_pedidos_tabela_picking_tiktok(
    pagina: fitz.Page,
) -> tuple[tuple[str, tuple[ItemPickingTikTok, ...]], ...] | None:
    """Lê o formato que identifica o pedido na última coluna de cada linha."""
    itens = _extrair_tabela_tiktok(pagina, _COLUNAS_TABELA)
    if itens is None:
        return None
    por_pedido = {}
    for pedido, item in itens:
        por_pedido.setdefault(pedido, []).append(item)
    return tuple((pedido, tuple(itens)) for pedido, itens in por_pedido.items())


def extrair_itens_tabela_packing_tiktok(pagina: fitz.Page) -> tuple[ItemPickingTikTok, ...] | None:
    """Lê a grade de quatro colunas e confirma suas quantidades com o total impresso."""
    itens = _extrair_tabela_tiktok(pagina, _COLUNAS_TABELA_PACKING)
    return None if itens is None else tuple(item for _, item in itens)


def _campos_itens(linhas):
    compactos = [_LINHA_COMPACTA.fullmatch(linha) for linha in linhas]
    if compactos and all(compactos):
        return [match.groups() for match in compactos]
    if len(linhas) % 3 == 0:
        return list(zip(linhas[::3], linhas[1::3], linhas[2::3]))
    return ()


def extrair_itens_picking_tiktok(texto):
    """Aceita a tabela de produto, SKU e quantidade explícita por linha."""
    cabecalhos = list(_CABECALHO.finditer(texto))
    if len(cabecalhos) != 1:
        return ()
    cabecalho = cabecalhos[0]
    rodape = _RODAPE.search(texto, cabecalho.end())
    if rodape is None:
        return ()
    linhas = [linha.strip() for linha in texto[cabecalho.end():rodape.start()].splitlines() if linha.strip()]
    itens = []
    for produto, sku, quantidade in _campos_itens(linhas):
        if not _SKU.fullmatch(sku) or not _QTD.fullmatch(quantidade) or int(quantidade) < 1:
            return ()
        itens.append(ItemPickingTikTok(sku, " ".join(produto.split()), int(quantidade)))
    total = rodape[1]
    if total is None:
        restante = texto[rodape.end():].strip().splitlines()
        if restante and restante[0].strip().isascii() and restante[0].strip().isdigit():
            total = restante[0].strip()
    if total is not None and (len(total) > 8 or sum(item.quantidade for item in itens) != int(total)):
        return ()
    return tuple(itens)


def _texto(pagina, texto, x, y, *, tamanho=9, fonte="helv", alinhar="esquerda", limite=None, cor=_PRETO):
    medida = _FONTES[fonte].text_length
    largura = medida(texto, fontsize=tamanho)
    if limite is not None and largura > limite:
        texto = texto[:250]
        while medida(texto + "...", fontsize=tamanho) > limite:
            texto = texto[:-1]
        texto = texto.rstrip() + "..."
        largura = medida(texto, fontsize=tamanho)
    if alinhar == "centro":
        x -= largura / 2
    elif alinhar == "direita":
        x -= largura
    pagina.insert_text((x, y), texto, fontname=fonte, fontsize=tamanho, color=cor)


def _nova_pagina_resumo(documento, indice):
    pagina = documento.new_page(pno=indice, width=595.28, height=841.89)
    _texto(pagina, "TikTok", 28, 33, tamanho=10, fonte="hebo")
    _texto(pagina, "Resumo de separação", 28, 61, tamanho=22, fonte="hebo")
    _texto(pagina, "Páginas originais da lista de picking ao final do arquivo.", 28, 81)
    pagina.draw_line((28, 95), (567, 95), color=_SEPARADOR, width=0.5)
    pagina.draw_rect((28, 110, 567, _TOPO_TABELA), color=None, fill=(0.37,) * 3)
    _texto(pagina, "Nº do pedido / Destinatário", 40, 127, fonte="hebo", cor=_BRANCO)
    _texto(pagina, "Produto", 297, 127, fonte="hebo", cor=_BRANCO, alinhar="centro")
    _texto(pagina, "Unidades", 550, 127, fonte="hebo", cor=_BRANCO, alinhar="direita")
    _texto(pagina, "O resumo soma apenas quantidades confirmadas.", 28, 817, tamanho=8)
    return pagina


def _desenhar_grupo(pagina, y, sku, itens, fundo):
    quantidade = sum(item.quantidade for _, item in itens)
    pedidos = len({pedido for pedido, _ in itens})
    unidades = "unidade" if quantidade == 1 else "unidades"
    sufixo = "pedido" if pedidos == 1 else "pedidos"
    pagina.draw_rect((28, y, 567, y + _ALTURA_GRUPO), color=None, fill=fundo)
    pagina.draw_line((28, y), (567, y), color=(0.35,) * 3, width=0.8)
    pagina.draw_circle((40, y + 13), 3, color=None, fill=_PRETO)
    _texto(pagina, f"SKU {sku}", 49, y + 17, tamanho=11, fonte="hebo", limite=320)
    _texto(pagina, f"{pedidos} {sufixo}", 420, y + 17, tamanho=8, alinhar="direita")
    _texto(pagina, f"Total: {quantidade} {unidades}", 550, y + 17, tamanho=10, fonte="hebo", alinhar="direita")


def _desenhar_item(pagina, y, pedido, item, fundo, destinatario):
    pagina.draw_rect((28, y, 567, y + _ALTURA_ITEM), color=None, fill=fundo)
    pagina.draw_line((28, y), (567, y), color=_SEPARADOR, width=0.5)
    _texto(pagina, pedido, 40, y + 17, fonte="hebo", limite=118)
    _texto(pagina, destinatario or "Nome não informado", 40, y + 30, tamanho=8.5, limite=510)
    _texto(pagina, item.produto, 297, y + 17, alinhar="centro", limite=270)
    if item.quantidade > 1:
        texto = f"{item.quantidade} UNIDADES"
        largura = fitz.get_text_length(texto, fontname="hebo", fontsize=8)
        pagina.draw_rect((538 - largura, y + 7.5, 550, y + 20.5), color=None, fill=(0.19,) * 3, radius=0.2)
        _texto(pagina, texto, 544, y + 17, tamanho=8, fonte="hebo", cor=_BRANCO, alinhar="direita")
    else:
        _texto(pagina, str(item.quantidade), 550, y + 17, fonte="hebo", alinhar="direita")


def inserir_resumo_picking_tiktok(documento, pedidos, *, destinatarios=None):
    """Adiciona o resumo antes das páginas originais sem modificar seu conteúdo."""
    destinatarios = destinatarios or {}
    grupos = {}
    for pedido, item in pedidos:
        grupos.setdefault(item.sku, []).append((pedido, item))

    indice_pagina = 0
    y = _FIM_TABELA
    for indice_grupo, (sku, itens) in enumerate(grupos.items()):
        fundo = _CINZA if indice_grupo % 2 else _BRANCO
        for indice_item, (pedido, item) in enumerate(itens):
            cabecalho = indice_item == 0
            altura = _ALTURA_ITEM + (_ALTURA_GRUPO if cabecalho else 0)
            if y + altura > _FIM_TABELA:
                pagina = _nova_pagina_resumo(documento, indice_pagina)
                indice_pagina += 1
                y = _TOPO_TABELA
                cabecalho = True
            if cabecalho:
                _desenhar_grupo(pagina, y, sku, itens, fundo)
                y += _ALTURA_GRUPO
            _desenhar_item(pagina, y, pedido, item, fundo, destinatarios.get(pedido))
            y += _ALTURA_ITEM
            pagina.draw_line((28, y), (567, y), color=_SEPARADOR, width=0.5)
