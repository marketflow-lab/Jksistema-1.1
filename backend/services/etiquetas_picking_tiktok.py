"""Leitura confirmada de itens e resumo de separação do TikTok."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz


_CABECALHO = re.compile(r"\bProduct[ \t]+Name\s+Seller[ \t]+SKU\s+Qty\b[ \t]*\n", re.IGNORECASE)
_RODAPE = re.compile(r"^Qty[ \t]+Total(?:(?:[ \t]*:[ \t]*|[ \t]+)([0-9]{1,8}))?[ \t]*$", re.IGNORECASE | re.MULTILINE)
_SKU = re.compile(r"[A-Z0-9][A-Z0-9_./-]{0,59}", re.IGNORECASE | re.ASCII)
_QTD = re.compile(r"[0-9]{1,4}")
_LINHA_COMPACTA = re.compile(r"(.+?)\s+(\S+)\s+([0-9]{1,4})")

_CINZA = (219 / 255,) * 3
_PRETO = (0.13,) * 3
_SEPARADOR = (0.65,) * 3
_BRANCO = (1,) * 3
_TOPO_TABELA = 137
_ALTURA_GRUPO = 26
_ALTURA_ITEM = 27
_FIM_TABELA = 794
_FONTES = {nome: fitz.Font(fontname=nome) for nome in ("helv", "hebo")}


@dataclass(frozen=True)
class ItemPickingTikTok:
    sku: str
    produto: str
    quantidade: int


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
    _texto(pagina, "Nº do pedido", 40, 127, fonte="hebo", cor=_BRANCO)
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


def _desenhar_item(pagina, y, pedido, item, fundo):
    pagina.draw_rect((28, y, 567, y + _ALTURA_ITEM), color=None, fill=fundo)
    pagina.draw_line((28, y), (567, y), color=_SEPARADOR, width=0.5)
    _texto(pagina, pedido, 40, y + 17, fonte="hebo", limite=118)
    _texto(pagina, item.produto, 297, y + 17, alinhar="centro", limite=270)
    if item.quantidade > 1:
        texto = f"{item.quantidade} UNIDADES"
        largura = fitz.get_text_length(texto, fontname="hebo", fontsize=8)
        pagina.draw_rect((538 - largura, y + 7.5, 550, y + 20.5), color=None, fill=(0.19,) * 3, radius=0.2)
        _texto(pagina, texto, 544, y + 17, tamanho=8, fonte="hebo", cor=_BRANCO, alinhar="direita")
    else:
        _texto(pagina, str(item.quantidade), 550, y + 17, fonte="hebo", alinhar="direita")


def inserir_resumo_picking_tiktok(documento, pedidos):
    """Adiciona o resumo antes das páginas originais sem modificar seu conteúdo."""
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
            _desenhar_item(pagina, y, pedido, item, fundo)
            y += _ALTURA_ITEM
            pagina.draw_line((28, y), (567, y), color=_SEPARADOR, width=0.5)
