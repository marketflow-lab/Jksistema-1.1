"""Current Cadastro metadata for import documents, scoped to tenant/store."""

from __future__ import annotations

import csv
import os
import re
import unicodedata

from backend.services import medias_compras_common as common
from backend.services.cadastro_common import _normalizar_sku_mes


_DESCRICAO_TECNICA = (
    "cg_product description", "Product description", "Descrição em inglês",
    "Descricao em ingles", "Descrição do produto em inglês",
)
_TITULO_INGLES = (
    common.TITULO_PRODUTO_INGLES_KEY, "Título em inglês",
    "cg_product name", "Product name",
)


def _chave_sku_documento(valor):
    texto = str(valor or "").strip()
    rotulado = re.match(r"(?i)^sku[\s:#]+([^\s]+)", texto)
    if rotulado:
        texto = rotulado.group(1)
    texto = _normalizar_sku_mes(texto).casefold()
    return str(int(texto)) if re.fullmatch(r"\d+", texto) else texto


def _chaves_sku_documento(valor):
    """Prefer the literal SKU, then a labeled SKU before a supplier OE code."""
    chave = _chave_sku_documento(valor)
    if not chave:
        return ()
    chaves = [chave]
    if re.match(r"(?i)^sku[\s:#]+", str(valor or "").strip()):
        prefixo, separador, _codigo_peca = chave.partition(":")
        if separador and prefixo:
            base = _chave_sku_documento(prefixo)
            if base not in chaves:
                chaves.append(base)
    return tuple(chaves)


def _nome_loja(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(letra for letra in texto if not unicodedata.combining(letra))
    return re.sub(r"[^a-z0-9]+", " ", texto.casefold()).strip()


def _descricao_cadastro(produto):
    return common._primeiro_texto_item(produto, (*_DESCRICAO_TECNICA, *_TITULO_INGLES))


def _descricoes_legadas(client_id, produtos, store_id):
    """Recover technical fields from the tenant's existing legacy metadata.

    Only unmaterialized products with a proven current store association may
    use this history. A current store record, including an intentionally empty
    field, is authoritative. No other tenant or default directory is read.
    """
    faltantes = {chave: produto for chave, produto in produtos.items()
                 if not common._primeiro_texto_item(produto, _DESCRICAO_TECNICA)}
    if not faltantes:
        return {}
    try:
        pasta = common.get_tenant_path(client_id)
        nomes = [nome for nome in os.listdir(pasta)
                 if nome.lower().startswith("cadastro_produtos")
                 and nome.lower().endswith(".csv")
                 and nome.lower() != "cadastro_produtos_lojas.csv"]
    except (OSError, RuntimeError):
        return {}

    def ordem(nome):
        texto = nome.lower()
        if texto == "cadastro_produtos.csv":
            return (0, texto)
        if "classificacao_unicode" in texto:
            return (1, texto)
        if "classificacao" in texto:
            return (2, texto)
        return (3, texto)

    descricoes = {}
    for nome in sorted(nomes, key=ordem):
        caminho = os.path.join(pasta, nome)
        if os.path.islink(caminho):
            continue
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                with open(caminho, newline="", encoding=encoding) as stream:
                    rows = list(csv.DictReader(stream))
                break
            except UnicodeDecodeError:
                rows = []
            except (OSError, csv.Error):
                rows = []
                break
        for row in rows:
            chave = _chave_sku_documento(row.get("sku") or row.get("SKU"))
            if chave not in faltantes or chave in descricoes:
                continue
            identidade = str(row.get("store_id") or "").strip()
            if identidade and identidade != store_id:
                continue
            loja = str(row.get("loja_sync") or "").strip()
            if not identidade and loja and _nome_loja(loja) != _nome_loja(faltantes[chave].get("loja_sync")):
                continue
            descricao = common._primeiro_texto_item(row, _DESCRICAO_TECNICA)
            if descricao:
                descricoes[chave] = descricao
        if len(descricoes) == len(faltantes):
            break
    return descricoes


def _contexto_cadastro_documentos(lista, client_id, *, skus=None, incluir_fotos=True):
    if not client_id:
        return {"scope": "legacy", "store_id": "", "por_sku": {}}
    if skus is None:
        skus = [item.get("SKU") or item.get("sku") for item in lista.get("itens") or []
                if isinstance(item, dict)]
    interesses = {chave for sku in skus for chave in _chaves_sku_documento(sku)}
    interesses.discard("")
    from backend.services.cadastro_compatibilidade import visao_produtos_cadastro_contexto_loja
    try:
        contexto = visao_produtos_cadastro_contexto_loja(
            client_id, lista.get("store_id") or lista.get("loja") or "",
            skus=tuple(sorted(interesses)),
            _normalizar_sku_consulta=_chave_sku_documento,
        )
    except RuntimeError:
        contexto = {"scope": "unavailable", "store_id": "", "produtos": []}
    contexto = dict(contexto)
    contexto["por_sku"] = {}
    if contexto.get("scope") not in {"store", "global_safe"}:
        return contexto
    store_id = str(contexto.get("store_id") or "").strip()
    controlados = {_chave_sku_documento(sku) for sku in contexto.get("skus_controlados") or []}
    produtos = {}
    for produto in contexto.get("produtos") or []:
        if not isinstance(produto, dict):
            continue
        if store_id and str(produto.get("store_id") or store_id).strip() != store_id:
            continue
        chave = _chave_sku_documento(produto.get("sku") or produto.get("SKU"))
        if chave and chave in interesses:
            produtos[chave] = produto
    legados = {chave: produto for chave, produto in produtos.items()
               if produto.get("scope_source") == "legacy_shadow"
               or (contexto.get("scope") == "global_safe" and chave not in controlados)}
    descricoes_legadas = _descricoes_legadas(client_id, legados, store_id)
    mapas_fotos = {}
    for chave, produto in produtos.items():
        sku = str(produto.get("sku") or produto.get("SKU") or "").strip()
        foto = ""
        if incluir_fotos:
            foto_ref = common._primeiro_texto_item(produto, ["foto", "Foto", "cg_foto", "Picture", "imagem"])
            foto = common._resolver_foto_cadastro_sku(
                client_id, sku, foto_ref, store_id or None, mapas_fotos=mapas_fotos,
            )
        unidade = common._primeiro_texto_item(produto, ["Unit/Pair", "Unit Pair", "unidade_bling", "Unidade"])
        if not unidade or unidade.casefold() in {"un", "und", "unidade", "pcs", "pc"}:
            unidade = "Unit"
        contexto["por_sku"][chave] = {
            "sku": sku, "descricao": (
                common._primeiro_texto_item(produto, _DESCRICAO_TECNICA)
                or descricoes_legadas.get(chave, "") or _descricao_cadastro(produto)
            ),
            "foto": foto, "unidade": unidade,
        }
    return contexto


def _dados_produto_documento(contexto, sku):
    por_sku = contexto.get("por_sku", {})
    for chave in _chaves_sku_documento(sku):
        if chave in por_sku:
            return por_sku[chave]
    return {"sku": "", "descricao": "", "foto": "", "unidade": ""}
