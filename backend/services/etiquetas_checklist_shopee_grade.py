"""Read the rotated orders in a four-quadrant Shopee picking-list page."""

from __future__ import annotations

from dataclasses import dataclass
import re
from unicodedata import combining, normalize

import fitz

from backend.services.etiquetas_checklist_shopee import ItemChecklistShopee


_DIRECAO = (0.0, -1.0)
_TOLERANCIA = 1.5
_SKU = re.compile(r"[A-Z0-9][A-Z0-9._/-]{0,59}", re.IGNORECASE)
_QUANTIDADE = re.compile(r"[0-9]{1,4}")
_CORTE = re.compile(r"[\s._\-–—]*corte[\s_]*aqui[\s._\-–—]*", re.IGNORECASE)
_ROTULOS = ("produto", "variacao", "qnt", "sku")


@dataclass(frozen=True)
class _Linha:
    texto: str
    x: float
    y: float
    caixa: fitz.Rect


def _normalizar_rotulo(texto):
    return "".join(letra for letra in normalize("NFKD", texto.casefold()) if not combining(letra))


def _linhas_do_quadrante(page, recorte):
    blocos = page.get_text("dict", clip=recorte, flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
    linhas = []
    for bloco in blocos:
        for linha in bloco.get("lines", ()):
            spans = linha["spans"]
            texto = "".join(span["text"] for span in spans).strip()
            if texto:
                if linha["dir"] != _DIRECAO:
                    return None
                x, y = spans[0]["origin"]
                linhas.append(_Linha(texto, x, y, fitz.Rect(linha["bbox"])))
    return linhas


def _alinhadas(linhas, eixo):
    posicoes = [getattr(linha, eixo) for linha in linhas]
    return max(posicoes) - min(posicoes) <= _TOLERANCIA


def _item_do_quadrante(linhas, padrao_pedidos):
    rotulos = {}
    for linha in linhas:
        rotulo = _normalizar_rotulo(linha.texto).rstrip(":").rstrip()
        if rotulo in ("quantidade", "qtd"):
            rotulo = "qnt"
        if rotulo == "id do pedido":
            rotulo = "id pedido"
        if _CORTE.fullmatch(rotulo):
            rotulo = "corte"
        if rotulo in (*_ROTULOS, "id pedido", "checklist de carregamento", "corte"):
            if rotulo in rotulos:
                return None
            rotulos[rotulo] = linha
    if not all(chave in rotulos for chave in (*_ROTULOS, "id pedido", "checklist de carregamento", "corte")):
        return None

    colunas = [rotulos[rotulo] for rotulo in _ROTULOS]
    if not _alinhadas(colunas, "x") or any(
        a.y - b.y <= _TOLERANCIA * 2 for a, b in zip(colunas, colunas[1:])
    ):
        return None

    rotulo_id = rotulos["id pedido"]
    identificacoes = [
        (linha, match.group().upper())
        for linha in linhas
        for match in padrao_pedidos.finditer(linha.texto)
    ]
    ids = {pedido for _, pedido in identificacoes}
    if len(ids) != 1 or not any(
        abs(linha.x - rotulo_id.x) <= _TOLERANCIA and linha.y < rotulo_id.y
        for linha, _ in identificacoes
    ):
        return None

    # Com texto girado em 90°, o eixo x representa a linha da tabela e y representa a coluna.
    corpo = [linha for linha in linhas if linha.x > max(c.x for c in colunas) + _TOLERANCIA]
    celulas = [
        sorted((linha for linha in corpo if abs(linha.y - cabecalho.y) <= _TOLERANCIA), key=lambda linha: linha.x)
        for cabecalho in colunas
    ]
    produtos, variacoes, quantidades, skus = celulas
    if not produtos or len(quantidades) != 1 or len(skus) != 1:
        return None

    quantidade, sku = quantidades[0], skus[0]
    if not _alinhadas([produtos[0], quantidade, sku, *variacoes[:1]], "x"):
        return None
    if not _QUANTIDADE.fullmatch(quantidade.texto) or int(quantidade.texto) < 1:
        return None
    if not _SKU.fullmatch(sku.texto) or sku.texto.upper() in {"N/D", "SKU", "PRODUTO", "SHOPEE"}:
        return None

    # Linhas de um segundo item, campos fora da tabela e colunas sobrepostas exigem o original.
    for coluna, limite in zip(celulas, colunas[1:]):
        if any(linha.caixa.y0 < limite.y - _TOLERANCIA for linha in coluna):
            return None
    linhas_das_celulas = {id(linha) for coluna in celulas for linha in coluna}
    indices = [linha for linha in corpo if id(linha) not in linhas_das_celulas]
    if indices and (
        len(indices) != 1 or indices[0].texto != "1" or indices[0].y <= colunas[0].y
        or not _alinhadas([indices[0], quantidade], "x")
    ):
        return None

    produto = " ".join(linha.texto for linha in produtos)
    variacao = " ".join(linha.texto for linha in variacoes)
    partes = (produto, variacao) if variacao and variacao not in {"-", "–", "—"} else (produto,)
    return ItemChecklistShopee(next(iter(ids)), sku.texto, int(quantidade.texto), partes)


def ler_grade_shopee(page: fitz.Page, ids: set[str]) -> tuple[ItemChecklistShopee, ...]:
    """Return items only when every occupied quadrant identifies one complete, distinct order."""
    if page.rotation or not 1 <= len(ids) <= 4:
        return ()
    area = page.rect
    meio_x, meio_y = (area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2
    padrao_pedidos = re.compile(
        r"(?<![A-Z0-9])(?:" + "|".join(re.escape(pedido) for pedido in sorted(ids, key=len, reverse=True))
        + r")(?=PACKAGE|[^A-Z0-9]|$)",
        re.IGNORECASE,
    )
    itens = []
    for topo, base in ((area.y0, meio_y), (meio_y, area.y1)):
        for esquerda, direita in ((area.x0, meio_x), (meio_x, area.x1)):
            recorte = fitz.Rect(esquerda, topo, direita, base)
            linhas = _linhas_do_quadrante(page, recorte)
            if linhas is None:
                return ()
            if all(_CORTE.fullmatch(linha.texto) for linha in linhas):
                continue
            item = _item_do_quadrante(linhas, padrao_pedidos)
            if not item:
                return ()
            itens.append(item)
    return tuple(itens) if len(itens) == len(ids) and {item.pedido for item in itens} == ids else ()
