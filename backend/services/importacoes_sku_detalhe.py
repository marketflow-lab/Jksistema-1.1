"""Read-only, SKU-sized projection of an import list and its shared freight basis."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi import Depends, HTTPException

from backend.services import cadastro_compatibilidade, impostos_regras, medias_compras_common as common
from backend.services.blocking_workers import run_blocking
from backend.services.cadastro_cubagem import normalizar_sku_cubagem, resolver_cubagem_produtos
from backend.services.importacoes_sku_anuncios import resolver_escopo_anuncios_sku
from backend.services.medias_compras_cache import ResultadoCache, digest_json
from backend.services.medias_compras_fiscal import (
    _ContextoCadastroLista,
    _aplicar_cubagem_item_lista,
    _enriquecer_itens_lista_pedido_com_impostos,
    _pick_cadastro_meta,
)
from backend.services.medias_compras_fontes import capturar_fontes, extras_runtime
from backend.services.medias_compras_leitura import chave_arquivo, consulta_medias
from backend.services.medias_compras_listas import _fonte_concorrentes_lista, _itens_salvos_lista_pedido_no_escopo


_rateios = ResultadoCache()
_projecoes = ResultadoCache(reter_resultado=lambda detalhe: detalhe["projecao"]["fiscal_pronto"])
_MESES = dict(zip("JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ".split(), range(1, 13)))
_CAMPOS_CUBAGEM = (
    "M3", "m3", common.CBM_LISTA_PEDIDO_KEY, "M3 individual",
    "Cubagem status", "Cubagem fonte", "Cubagem unidade", "Cubagem data", "Cubagem loja",
)
_CAMPOS_FISCAIS = ("convenio_mg_st_minas", "convenio_mg_st_paraiba", "convenio_mg_mva")


def _sku_exato(valor):
    return str(valor or "").strip().upper()


def _alias_fiscal(valor):
    texto = re.sub(r"\s+", "", _sku_exato(valor))
    texto = re.sub(r"\.0+$", "", texto)
    return re.sub(r"-([A-Z]{3})\b", lambda match: f"-{_MESES.get(match[1], match[1])}", texto)


def _sem_analises(item):
    return {chave: valor for chave, valor in item.items()
            if chave not in {"analise_concorrentes", "analise_simulacao_importacao_sku"}
            and not chave.startswith("compra_aprovada")}


def _lista(client_id, lista_id):
    for lista in common._carregar_listas_pedidos(client_id):
        if str(lista.get("id")) == lista_id:
            return lista
    raise HTTPException(404, "Lista de pedidos não encontrada.")


def _item(lista, sku):
    for item in lista.get("itens") or []:
        if isinstance(item, dict) and _sku_exato(item.get("SKU") or item.get("sku")) == sku:
            return item
    raise HTTPException(404, "SKU não encontrado nesta lista.")


def _referencia_loja(lista):
    return str(lista.get("store_id") or lista.get("loja") or "").strip()


def _contexto_cadastro(client_id, lista, skus):
    referencia = _referencia_loja(lista)
    if referencia and referencia != "__todas":
        contexto = cadastro_compatibilidade.visao_produtos_cadastro_contexto_loja(client_id, referencia, skus=skus)
        store_id = str(lista.get("store_id") or "").strip()
        if contexto.get("scope") == "store" and (not store_id or str(contexto.get("store_id")) == store_id):
            return contexto
    return {"scope": "unresolved", "produtos": [], "store_id": ""}


def _cotacao(lista):
    return _numero(lista.get("exchange_rate") or lista.get("dolar_hoje")
                   or lista.get("cotacao_dolar") or lista.get("dolar")) or 0.0


def _revisao_rateio(lista):
    # Atualizar a análise ou aprovar a compra não muda a base de frete.
    return digest_json((
        _referencia_loja(lista), common._cubagem_lista_historica(lista), _cotacao(lista),
        [_sem_analises(item) for item in lista.get("itens") or [] if isinstance(item, dict)],
    ))


def _numero(valor):
    numero = common._to_float(valor, math.nan)
    return numero if math.isfinite(numero) else None


def _calcular_rateio(client_id, lista, revisao):
    itens = [common._normalizar_item_lista_pedido(item) for item in lista.get("itens") or []
             if isinstance(item, dict)]
    historica = common._cubagem_lista_historica(lista)
    skus = list(dict.fromkeys(item["SKU"] for item in itens if item["SKU"] and item["Quantidade"] > 0))
    cubagens = {}
    store_id = str(lista.get("store_id") or "").strip()
    loja_resolvida = ""
    if not historica and skus:
        contexto = _contexto_cadastro(client_id, lista, skus)
        if contexto.get("scope") == "store":
            store_id = str(contexto["store_id"])
            loja_resolvida = store_id
            cubagens = resolver_cubagem_produtos(contexto["produtos"], store_id)

    total_usd = total_m3 = 0.0
    completo = bool(itens)
    por_sku = {}
    for item in itens:
        qtd = item["Quantidade"]
        sku = item["SKU"]
        total = _numero(item["Valor total"])
        unitario = _numero(item["Valor unidade"])
        valor = total if total is not None else (qtd * unitario if unitario is not None else 0.0)
        total_usd += valor
        cubagem = _aplicar_cubagem_item_lista(
            item, cubagens.get(normalizar_sku_cubagem(sku)), store_id, preservar_historico=historica,
        )
        volume = _numero(cubagem["M3"])
        if volume is not None and math.isfinite(total_m3 + volume):
            total_m3 += volume
        else:
            completo = False
        por_sku.setdefault(_sku_exato(sku), {campo: cubagem[campo] for campo in _CAMPOS_CUBAGEM})

    total_usd = total_usd if math.isfinite(total_usd) else None
    valor_por_m3 = total_usd / total_m3 if completo and total_usd and total_m3 > 0 else None
    if valor_por_m3 is not None and not math.isfinite(valor_por_m3):
        valor_por_m3 = None
    return {
        "revisao": revisao, "store_id": loja_resolvida,
        "rateio": {
            "total_usd": total_usd, "total_m3": total_m3, "m3_completo": completo,
            "valor_por_m3": valor_por_m3,
        },
        "por_sku": por_sku,
    }


def _impostos_sku(client_id, sku):
    alias = _alias_fiscal(sku)

    def corresponde(valor):
        return _sku_exato(valor) == sku or _alias_fiscal(valor) == alias

    resultado = impostos_regras._montar_simulacao_impostos(client_id, filtro_sku=corresponde)
    produtos = resultado["produtos"]
    produto = next((produto for produto in produtos if _sku_exato(produto["sku"]) == sku),
                   produtos[0] if produtos else None)
    return {"sku": sku, **{campo: produto[campo] for campo in _CAMPOS_FISCAIS}} if produto else None


def _cadastro_sku(contexto, sku):
    if contexto.get("scope") != "store":
        return None
    candidatos = [produto for produto in contexto["produtos"] if produto.get("store_id") == contexto["store_id"]]
    produto = next((produto for produto in candidatos if _sku_exato(produto.get("sku")) == sku), None)
    exato = produto is not None
    if not exato:
        produto = next((produto for produto in candidatos if _sku_exato(produto.get("cubagem_sku")) == sku), None)
    if produto is None:
        return None
    return {
        "sku": produto["sku"], "store_id": produto["store_id"],
        "nome": _pick_cadastro_meta(produto, ["produto bling", "nome bling", "produtos blig", "produto blig",
                                            "produtos bling", "nome", "produto"]),
        "ncm": _pick_cadastro_meta(produto, ["ncm"]),
        "cest": _pick_cadastro_meta(produto, ["cest"]),
        "descricao_ncm": _pick_cadastro_meta(produto, ["descricao ncm", "ncm descricao", "desc ncm", "ncm desc"]),
        "custo": produto.get("custo") if exato else None,
    }


def _identidade_anuncios(client_id, lista):
    try:
        escopo = resolver_escopo_anuncios_sku(client_id, lista["store_id"])
        _, fontes_versao = _fonte_concorrentes_lista(client_id, lista)
    except (HTTPException, OSError, RuntimeError, ValueError):
        return None
    return {"seller_id": escopo["seller_id"], "site_id": escopo["site_id"], "fontes_versao": fontes_versao}


def _capturar_fontes_sku(raiz, lista, *, inventariar_fotos=False):
    raiz = Path(raiz)
    fontes_runtime = extras_runtime(impostos_regras)
    if inventariar_fotos and common.PASTA_INFO:
        fontes_runtime.append(Path(common.PASTA_INFO) / "default" / "cadastro_fotos")
    return capturar_fontes(
        raiz, detalhe=True,
        extras=(
            *fontes_runtime,
            raiz / "impostos_regras.json",
            raiz / "impostos_convenio_mg.db",
            *raiz.glob("cadastro_produtos.backup_classificacao*.csv"),
        ),
        alvo=_revisao_rateio(lista), snapshot=lista,
        escopo=(str(lista["id"]), _referencia_loja(lista)),
        inventariar_fotos=inventariar_fotos,
    )


def _selo_fontes(client_id, fonte):
    if fonte.pendente:
        return None
    return digest_json((client_id, fonte.assinatura, fonte.autoridade, fonte.alvo))


def _selo_atual(client_id, lista):
    try:
        return _selo_fontes(client_id, _capturar_fontes_sku(common.get_tenant_path(client_id), lista))
    except OSError:
        return None


@consulta_medias
def _validar_margem_salva(client_id, lista_id, sku):
    lista = _lista(client_id, lista_id)
    _item(lista, sku)
    escopo = common._resolver_escopo_loja_medias(client_id, lista.get("loja"), lista.get("store_id"))
    identidade = _identidade_anuncios(client_id, escopo) if escopo.get("scope") == "store" else None
    selo = _selo_atual(client_id, lista) if identidade else None
    return {"revisao_fontes_margem": digest_json((selo, sku)) if selo else None, "anuncios_identidade": identidade}


@consulta_medias
def _projetar_sku(client_id, lista_id, sku, somente_salvos, *, lista=None):
    if lista is None:
        lista = _lista(client_id, lista_id)
    salvo = _item(lista, sku)
    item = _itens_salvos_lista_pedido_no_escopo(client_id, {**lista, "itens": [salvo]})[0]
    resumo = {
        "id": lista_id, "nome_lista": str(lista.get("nome_lista") or ""),
        "loja": str(lista.get("loja") or "").strip() or "__todas",
        "store_id": str(lista.get("store_id") or "").strip(),
        "status": common._normalizar_status_lista_pedido(lista.get("status")),
        "updated_at": str(lista.get("updated_at") or ""),
        "exchange_rate": _cotacao(lista),
    }
    for campo in ("unida_em_lista_id", "unida_em_lista_nome", "unida_em"):
        if lista.get(campo):
            resumo[campo] = str(lista[campo])
    projecao = {"success": True, "lista": resumo, "item": item, "cadastro": None,
                "fiscal": None, "fiscal_pronto": False, "anuncios_identidade": None}
    if somente_salvos:
        return projecao, digest_json((resumo, _sem_analises(item))), ""
    selo = _selo_atual(client_id, lista)
    referencia = _referencia_loja(lista)
    contexto = _contexto_cadastro(client_id, lista, [sku])
    loja_resolvida = str(contexto["store_id"])
    if loja_resolvida:
        resumo["store_id"] = loja_resolvida
        projecao["anuncios_identidade"] = _identidade_anuncios(client_id, resumo)
    projecao["cadastro"] = _cadastro_sku(contexto, sku)
    if not lista.get("unida_em_lista_id"):
        # Preserve the existing metadata, NCM and aliquot precedence for the selected item.
        projecao["item"] = _enriquecer_itens_lista_pedido_com_impostos(
            client_id, [common._normalizar_item_lista_pedido(item)], loja=referencia,
            contexto_cadastro=_ContextoCadastroLista(client_id, referencia, contexto),
            preservar_cubagem=common._cubagem_lista_historica(lista),
            filtrar_cadastro_fiscal=True,
        )[0]
    try:
        projecao["fiscal"] = _impostos_sku(client_id, sku)
        projecao["fiscal_pronto"] = True
    except (HTTPException, OSError, RuntimeError, sqlite3.Error):
        # Preserve the basic projection when the optional fiscal source is unavailable.
        pass
    if selo and projecao["fiscal_pronto"] and projecao["anuncios_identidade"] and selo == _selo_atual(client_id, lista):
        projecao["_selo_fontes"] = selo
    return projecao, _revisao_rateio(lista), loja_resolvida


async def api_importacoes_sku_detalhe(
    lista_id: str, sku: str, client_id: str = Depends(common.get_tenant_id), somente_salvos: bool = False,
    validar_margem: bool = False,
):
    sku = _sku_exato(sku)
    if validar_margem:
        return await run_blocking(_validar_margem_salva, client_id, lista_id, sku)
    if somente_salvos:
        projecao, revisao, _ = await run_blocking(_projetar_sku, client_id, lista_id, sku, True)
        return {**projecao, "revisao": revisao,
                "rateio": {"total_usd": None, "total_m3": None, "m3_completo": False, "valor_por_m3": None}}

    raiz = common.get_tenant_path(client_id)
    raiz_chave = chave_arquivo(raiz)

    def ler_lista_atual():
        if chave_arquivo(common.get_tenant_path(client_id)) != raiz_chave:
            raise HTTPException(409, {"code": "sources_changing", "message": "A origem dos dados mudou durante a consulta."})
        return _lista(client_id, lista_id)

    def probe_rateio():
        lista = ler_lista_atual()
        _item(lista, sku)
        return _capturar_fontes_sku(raiz, lista)

    def probe_projecao():
        lista = ler_lista_atual()
        item = _item(lista, sku)
        fonte = _capturar_fontes_sku(raiz, lista, inventariar_fotos=True)
        cabecalho = {campo: valor for campo, valor in lista.items() if campo != "itens"}
        return replace(fonte, alvo=digest_json((fonte.alvo, cabecalho, item)))

    def calcular_projecao(fonte):
        projecao, revisao, loja = _projetar_sku(client_id, lista_id, sku, False, lista=fonte.snapshot)
        return {"projecao": projecao, "revisao": revisao, "store_id": loja}

    def calcular_agregado(fonte):
        return {**_calcular_rateio(client_id, fonte.snapshot, fonte.alvo), "_selo_fontes": _selo_fontes(client_id, fonte)}

    agregado, detalhe = await asyncio.gather(
        _rateios.consultar(
            client_id, (raiz_chave, "rateio_importacoes_sku", lista_id), probe_rateio,
            calcular_agregado,
            stale=False, preferir_fresco=True,
        ),
        _projecoes.consultar(
            client_id, (raiz_chave, "projecao_importacoes_sku", lista_id, sku), probe_projecao,
            calcular_projecao, stale=False, preferir_fresco=True,
        ),
    )
    projecao, revisao, loja_projetada = detalhe["projecao"], detalhe["revisao"], detalhe["store_id"]
    if agregado["revisao"] != revisao or (
        agregado["store_id"] and agregado["store_id"] != loja_projetada
    ):
        raise HTTPException(409, {"code": "sources_changing", "message": "A lista mudou durante o cálculo. Tente novamente."})
    projecao["item"].update(agregado["por_sku"].get(sku, {}))
    projecao["rateio"] = agregado["rateio"]
    selo = projecao.pop("_selo_fontes", None)
    if selo and selo == agregado["_selo_fontes"]:
        projecao["revisao_fontes_margem"] = digest_json((selo, sku))
    projecao["revisao"] = digest_json((
        revisao, _sem_analises(projecao["item"]), projecao["rateio"],
        projecao["lista"]["store_id"], projecao["cadastro"], projecao["fiscal"], projecao["fiscal_pronto"],
    ))
    return projecao
