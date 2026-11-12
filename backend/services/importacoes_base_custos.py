"""Revision-fenced cost inputs shared by the import list and SKU detail.

This projection supplies the existing browser calculation; it contains no
second implementation of the cost formula and never reads sales or photos.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from fastapi import Depends, HTTPException

from backend.services import cadastro_compatibilidade, importacoes_sku_detalhe as sku_service
from backend.services import medias_compras_common as common
from backend.services.blocking_workers import run_blocking
from backend.services.medias_compras_cache import Fontes, ResultadoCache, digest_json
from backend.services.medias_compras_cache_disco import calculation_revision
from backend.services.medias_compras_fiscal import _ContextoCadastroLista, _enriquecer_itens_lista_pedido_com_impostos
from backend.services.medias_compras_fontes import assinatura_arquivo, extras_runtime
from backend.services.medias_compras_leitura import consulta_medias, chave_arquivo
from backend.services.source_file_revision import _RevisionBusy


VERSAO = 1
TIPO = "importacoes_base_custos"
_bases = ResultadoCache()


def _nome_campo(value):
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    return re.sub(r"[^a-z0-9]", "", value)


_CAMPOS_CUSTO = frozenset(_nome_campo(name) for name in (
    "SKU", "Quantidade", "Valor unidade", "Valor total", "Valor em dólar", "Valor total em dólar",
    "M3", "M3 individual", "CBM", common.CBM_LISTA_PEDIDO_KEY,
    "Cubagem status", "Cubagem fonte", "Cubagem unidade", "Cubagem loja", "Cubagem data",
    "NCM", "CEST", "II", "IPI", "PIS", "COFINS", "ICMS", "ST Minas", "ST MG", "ST Paraíba", "ST PB",
    "MVA Minas", "MVA (Conv. MG)", "convenio_mg_st_minas", "convenio_mg_st_paraiba", "convenio_mg_mva",
    "Valor por M³", "Valor por M?", "Valor/M³", "Valor/M?", "Base ICMS", "Base de ICMS",
    "B. de C.ST MG", "Base de C.ST MG", "Base C.ST MG", "B. de C.ST PB", "Base de C.ST PB", "Base C.ST PB",
))


def _campos_custo(row):
    return {key: value for key, value in row.items() if _nome_campo(key) in _CAMPOS_CUSTO}


def _lista_custo(lista, escopo):
    itens = []
    for row in lista.get("itens") or []:
        if isinstance(row, dict):
            item = _campos_custo(common._normalizar_item_lista_pedido(row))
            item["SKU"] = sku_service._sku_exato(item["SKU"])
            itens.append(item)
    # Ordering, approval and saved analysis are presentation/derived data.
    itens.sort(key=lambda item: (item["SKU"], digest_json(item)))
    return {
        **_campos_custo(lista),
        "id": str(lista["id"]), "loja": escopo["loja"], "store_id": escopo["store_id"],
        "exchange_rate": sku_service._cotacao(lista),
        "status": "Recebido" if common._cubagem_lista_historica(lista) else "Analisando orçamento",
        "unida_em_lista_id": bool(lista.get("unida_em_lista_id")),
        "itens": itens,
    }


@consulta_medias
def capturar_base_custos(client_id, lista_id, *, lista=None):
    lista = sku_service._lista(client_id, str(lista_id)) if lista is None else lista
    escopo = common._resolver_escopo_loja_medias(
        client_id, lista.get("loja"), lista.get("store_id"), exigir_especifica=True,
    )
    raiz = Path(common.get_tenant_path(client_id))
    fonte_lista = _lista_custo(lista, escopo)
    arquivos = {raiz / name for name in (
        "cadastro_produtos.csv", "cadastro_produtos_lojas.csv", "cadastro_custos_lojas.csv",
        "impostos_ncm.db", "impostos_aliquotas_importacao.db", "impostos_convenio_mg.db", "impostos_regras.json",
        "lojas_public_snapshot.json", "lojas_sync_tombstones.json",
    )}
    arquivos.update(raiz.glob("cadastro_produtos*.csv"))
    arquivos.update(Path(path) for path in extras_runtime(sku_service.impostos_regras)
                    if path and Path(path).name not in {"cadastro_fotos", "produtos_compilado.csv"})
    autoridade = {raiz / "lojas_public_snapshot.json", raiz / "lojas_sync_tombstones.json",
                  raiz.parent / ".cadastro_tenant_trust.json"}
    if not (raiz / "lojas_public_snapshot.json").exists():
        autoridade.add(raiz / "lojas_config.json")
    journals = {raiz / "_stores_publication" / "transaction.json",
                raiz / "_shared_sync_backups" / "pending_lojas_transaction.json"}
    autoridade.update(journals)
    arquivos.update(autoridade)
    for path in tuple(arquivos):
        if path.suffix.casefold() == ".db":
            arquivos.update((Path(str(path) + "-wal"), Path(str(path) + "-journal")))
    try:
        revisoes = {path: assinatura_arquivo(path) for path in arquivos}
    except _RevisionBusy as error:
        raise HTTPException(409, "As fontes do custo estão sendo atualizadas. Tente novamente.") from error
    pendente = any(path.exists() for path in journals) or any(
        path.name.endswith("-journal") and path.exists() for path in arquivos
    )
    identidade = (str(client_id), os.path.normcase(os.path.realpath(raiz)), escopo["store_id"])
    hard = tuple(sorted(revisoes[path] for path in autoridade))
    return Fontes(
        (calculation_revision(), tuple(sorted(revisoes.values()))), (*identidade, hard), digest_json(fonte_lista),
        snapshot=fonte_lista, pendente=pendente, politica_fotos=identidade,
    )


def metadados_base_custos(fonte):
    lista = fonte.snapshot
    revisao = digest_json((TIPO, VERSAO, fonte.assinatura, fonte.autoridade, fonte.alvo))
    return {
        "success": True, "tipo": TIPO, "versao": VERSAO,
        "lista": {key: value for key, value in lista.items() if key not in {"itens", "status", "unida_em_lista_id"}},
        "revisao": revisao,
        "revisoes_sku": {item["SKU"]: digest_json((revisao, item["SKU"])) for item in lista["itens"]},
    }


def exigir_revisao_custo(client_id, lista_id, sku, revisao, *, lista=None):
    fonte = capturar_base_custos(client_id, lista_id, lista=lista)
    atual = metadados_base_custos(fonte)["revisoes_sku"].get(sku_service._sku_exato(sku))
    if fonte.pendente or atual != revisao:
        raise HTTPException(409, "Os dados do custo mudaram. Atualize o SKU antes de calcular as margens.")


def _fiscal_lote(client_id, skus):
    aliases = {sku_service._alias_fiscal(sku) for sku in skus}
    resultado = sku_service.impostos_regras._montar_simulacao_impostos(
        client_id, filtro_sku=lambda sku: sku_service._sku_exato(sku) in skus or sku_service._alias_fiscal(sku) in aliases,
    )
    produtos = resultado["produtos"]
    exatos = {}
    por_alias = {}
    for row in produtos:
        exatos.setdefault(sku_service._sku_exato(row["sku"]), row)
        por_alias.setdefault(sku_service._alias_fiscal(row["sku"]), row)
    return {sku: {campo: row.get(campo) for campo in sku_service._CAMPOS_FISCAIS}
            for sku in skus if (row := exatos.get(sku) or por_alias.get(sku_service._alias_fiscal(sku))) is not None}


def _calcular_base(client_id, fonte):
    lista = fonte.snapshot
    skus = [item["SKU"] for item in lista["itens"]]
    contexto = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(
        client_id, lista["store_id"], skus=skus, _incluir_fotos=False,
    )
    if contexto.get("scope") != "store" or contexto.get("store_id") != lista["store_id"]:
        raise HTTPException(409, "O cadastro da loja está sendo atualizado. Tente novamente.")
    agregado = sku_service._calcular_rateio(client_id, lista, fonte.alvo, contexto_cadastro=contexto)
    itens = lista["itens"]
    if not lista["unida_em_lista_id"]:
        itens = _enriquecer_itens_lista_pedido_com_impostos(
            client_id, itens, loja=lista["store_id"],
            contexto_cadastro=_ContextoCadastroLista(client_id, lista["store_id"], contexto),
            preservar_cubagem=common._cubagem_lista_historica(lista), filtrar_cadastro_fiscal=True, incluir_fotos=False,
        )
    fiscal = _fiscal_lote(client_id, set(skus))
    return {
        **metadados_base_custos(fonte),
        "itens": [{**_campos_custo(item), **agregado["por_sku"].get(item["SKU"], {}),
                   "fiscal": fiscal.get(item["SKU"], {}), "fiscal_pronto": True} for item in itens],
        "rateio": agregado["rateio"],
    }


async def api_importacoes_base_custos(
    lista_id: str, client_id: str = Depends(common.get_tenant_id), somente_revisao: bool = False,
):
    raiz = common.get_tenant_path(client_id)
    raiz_chave = chave_arquivo(raiz)

    def probe():
        if chave_arquivo(common.get_tenant_path(client_id)) != raiz_chave:
            raise HTTPException(409, "A origem dos dados mudou. Tente novamente.")
        return capturar_base_custos(client_id, lista_id)

    if somente_revisao:
        fonte = await run_blocking(probe)
        if fonte.pendente:
            raise HTTPException(409, "As fontes do custo estão sendo atualizadas. Tente novamente.")
        return metadados_base_custos(fonte)
    return await _bases.consultar(
        client_id, (raiz_chave, TIPO, VERSAO, str(lista_id)), probe,
        lambda fonte: _calcular_base(client_id, fonte), stale=False, preferir_fresco=True, persistir_em=raiz,
    )
