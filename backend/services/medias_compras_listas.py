"""Pedido-list endpoints and Excel imports for Medias Compras."""

from __future__ import annotations

from backend.services.medias_compras_leitura import consulta_medias, fotos_da_consulta, leitura_medias, medir_etapa

import asyncio
import copy
from contextlib import nullcontext
from hashlib import sha256
import io
import json
import math
import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Literal

import requests
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from backend.schemas import (
    ListaPedidoAddSkuRequest,
    ListaPedidoPreferenciasColunasRequest,
    ListaPedidoSkuAnaliseConcorrentesRequest,
    ListaPedidoSkuAprovacaoRequest,
    ListaPedidoStatusRequest,
    ListaPedidoUpdateRequest,
    MediasComprasSkusOcultosRequest,
)
from backend.services import medias_compras_common as medias_common
from backend.services.importacoes_consultas_cache import CacheConsultas
from backend.services.importacoes_financeiro import (
    contexto_financeiro, dependencias_com_cache, propagar_falha_financeira,
)
from backend.services.path_coordination import path_lock_for
from backend.services.medias_compras_common import *
from backend.services.medias_compras_excel import *
from backend.services.medias_compras_fiscal import *
from backend.services.medias_compras_fiscal import _ContextoCadastroLista, _pick_cadastro_link
from backend.services.runtime_bridge import bind_runtime_globals


_RUNTIME_NAMES = (
    "logger",
    "get_tenant_path",
    "TEMP_FILES_STORAGE",
    "TEMP_FILES_META",
    "LISTA_PEDIDO_XLSX_CACHE",
    "LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS",
)


def _sync_common_names() -> None:
    for name in medias_common.COMMON_EXPORTS:
        globals()[name] = getattr(medias_common, name)
    for name in _RUNTIME_NAMES:
        globals()[name] = getattr(medias_common, name)


def _configure_runtime_globals(runtime_module=None):
    runtime = medias_common.configure_medias_compras_common_runtime(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_common_names()
    return runtime


_CHAVES_CONCORRENTES = {f"concorrente_{numero}" for numero in range(1, 6)}


def _normalizar_item_id_mlb(valor: Any) -> str:
    texto = re.sub(r"[^A-Z0-9]", "", str(valor or "").strip().upper())
    return texto if re.fullmatch(r"MLB\d+", texto) else ""


def _numero_finito_margem(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _loja_lista_definida(valor: Any) -> bool:
    texto = unicodedata.normalize("NFKD", str(valor or "").strip())
    texto = "".join(char for char in texto if not unicodedata.combining(char)).lower()
    return texto not in {"", "__todas", "nao definida", "todas", "todas as lojas"}


def _dependencias_margens_concorrentes() -> dict[str, Any]:
    from backend.services import mercadolivre_legacy_core, promocoes_core
    from backend.services.integracoes import ler_lojas as carregar_lojas

    return {
        "carregar_lojas": carregar_lojas,
        "obter_cfg_ml": mercadolivre_legacy_core._obter_cfg_ml,
        "ml_api_request": mercadolivre_legacy_core._ml_api_request,
        "ml_contexto_frete_item": mercadolivre_legacy_core._ml_contexto_frete_item,
        "ml_obter_frete_detalhado": mercadolivre_legacy_core._ml_obter_frete_detalhado,
        "ml_obter_taxas_anuncio": mercadolivre_legacy_core._ml_obter_taxas_anuncio,
        "resolver_subsidio": mercadolivre_legacy_core._ml_ajustar_desconto_tarifa_recebivel_promocao,
        "carregar_custos_impostos": _carregar_custos_impostos_dono_anuncio,
        "resolver_custo": promocoes_core._resolver_custo_medio_por_skus,
        "resolver_imposto": promocoes_core._resolver_imposto_rate_por_sku,
        "calcular_margem": promocoes_core._calcular_margem_liquida_ml,
    }


def _carregar_custos_impostos_dono_anuncio(
    client_id: str, loja: str, store_id: str, sku: str,
) -> tuple[dict, dict]:
    from backend.services.cadastro_compatibilidade import visao_produtos_cadastro_contexto_loja
    from backend.services import cadastro_lojas_produtos as cadastro
    from backend.services.promocoes_core_custos import _to_float_safe, _to_rate_safe

    visao = visao_produtos_cadastro_contexto_loja(client_id, store_id, skus=[sku])
    if visao.get("scope") != "store" or str(visao.get("store_id") or "") != store_id:
        return {}, {}
    sku_key = _normalizar_sku_mes(sku).strip().upper()
    produtos = [produto for produto in visao.get("produtos") or []
                if isinstance(produto, dict) and str(produto.get("store_id") or "") == store_id
                and not produto.get("deleted_at_utc")
                and _normalizar_sku_mes(str(produto.get("sku") or "")).strip().upper() == sku_key]
    if len(produtos) != 1:
        return {}, {}
    # A legacy product shadow may inherit generic product fields. Financial
    # values require the canonical cost source's proven association to this ID
    # (or its unambiguous authorized legacy name), never that generic fallback.
    identidade = cadastro.resolver_loja_cadastro(client_id, store_id)
    contexto = cadastro._contexto_legado_de_tenant(
        client_id, identidade, cadastro.get_tenant_path(client_id), cadastro._lojas_atuais(client_id),
        fotos={}, sku_interessa=lambda row: cadastro._normalizar_sku_chave(row.get("sku")) == cadastro._normalizar_sku_chave(sku),
        chave_filtro=("importacoes_financeiro", sku_key),
    )
    financeiro = (contexto.get("custos") or {}).get(cadastro._normalizar_sku_chave(sku)) or {}
    custo = _to_float_safe(financeiro.get("custo"))
    imposto = _to_rate_safe(financeiro.get("imposto"))
    return ({sku: custo} if custo is not None else {}, {sku: imposto} if imposto is not None else {})


def _margem_concorrente_indisponivel(
    preco_venda: float,
    item_id_loja: str,
    motivo: str,
    *,
    loja: str = "",
) -> dict[str, Any]:
    return {
        "preco_venda": round(float(preco_venda), 2),
        "item_id_loja": item_id_loja or None,
        "loja": loja or None,
        "margem_percentual": None,
        "financeiro_exato": False,
        "motivo_indisponivel": str(motivo or "Dados financeiros insuficientes."),
    }


def _motivo_consulta_anuncio(status: int | None) -> str:
    if status in (401, 403):
        return f"Consulta do anúncio não autorizada (HTTP {status}). Confira a conexão e as permissões desta loja."
    if status == 404:
        return "O anúncio da loja não foi encontrado no Mercado Livre (HTTP 404)."
    if status == 429:
        return "O Mercado Livre limitou a consulta do anúncio (HTTP 429). Tente novamente em instantes."
    if status is not None and 500 <= status <= 599:
        return f"A consulta do anúncio no Mercado Livre está temporariamente indisponível (HTTP {status})."
    return "Não foi possível consultar o anúncio da loja no Mercado Livre."


def _cfg_confere_dono_anuncio(cfg: dict, dono: dict) -> bool:
    return bool(isinstance(cfg, dict) and str(cfg.get("access_token") or "").strip()
                and str(cfg.get("_store_id_context") or "").strip() == dono["store_id"]
                and str(cfg.get("user_id") or "").strip() == dono["seller_id"]
                and str(cfg.get("site_id") or "").strip() == dono["site_id"])


def _candidatos_donos_anuncios(
    client_id: str, deps: dict, cache_contextos: dict, *, store_id_alvo: str | None = None,
) -> tuple[list, str]:
    # This snapshot belongs only to this request's authenticated tenant. It is
    # never restored from an ad cache, audit record or historical store list.
    cache_key = ("__contas_autorizadas", store_id_alvo)
    if cache_key in cache_contextos:
        salvo = cache_contextos[cache_key]
        return salvo["candidatos"], salvo["motivo"]
    candidatos: list[dict[str, Any]] = []
    motivo = "Não foi possível confirmar uma conta autorizada e seu site para este anúncio."
    try:
        lojas = [row for row in (deps["carregar_lojas"](client_id) or []) if isinstance(row, dict)]
    except Exception:
        lojas = []
    identidades = [str(row.get("store_id") or "").strip() for row in lojas if row.get("store_id")]
    if len(identidades) != len(set(identidades)):
        motivo = "A identidade das contas autorizadas está ambígua. Atualize as conexões das lojas."
        lojas = []
    for row in lojas:
        nome = str(row.get("nome") or "").strip()
        store_id = str(row.get("store_id") or "").strip()
        if store_id_alvo is not None and store_id != store_id_alvo:
            continue
        integracoes = row.get("integracoes") or {}
        publico = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else None
        if (not nome or not store_id or not isinstance(publico, dict)
                or "mercadolivre" in (row.get("_unavailable_providers") or [])
                or publico.get("connected") is False or publico.get("oauth_invalid") is True
                or not str(publico.get("access_token") or "").strip()):
            continue
        dono = {"nome": nome, "store_id": store_id,
                "seller_id": str(publico.get("user_id") or "").strip(),
                "site_id": str(publico.get("site_id") or row.get("site_id") or "").strip()}
        # This endpoint accepts MLB IDs. An absent or different site cannot be
        # inferred from a store label or from some other account's credentials.
        if not dono["seller_id"] or dono["site_id"] != "MLB":
            continue
        try:
            cfg = dict(deps["obter_cfg_ml"](client_id, nome, store_id=store_id) or {})
        except Exception:
            continue
        if _cfg_confere_dono_anuncio(cfg, dono):
            candidatos.append({**dono, "cfg": cfg})
    cache_contextos[cache_key] = {"candidatos": candidatos, "motivo": motivo}
    return candidatos, motivo


def _consultar_anuncio_na_conta(client_id: str, item_id: str, conta: dict, deps: dict) -> tuple[dict | None, str]:
    try:
        resposta, cfg = deps["ml_api_request"](
            client_id, conta["nome"], conta["cfg"], "GET", f"https://api.mercadolibre.com/items/{item_id}",
            params={"include_attributes": "all"}, timeout=12,
        )
        if not _cfg_confere_dono_anuncio(cfg, conta):
            conta["indisponivel"] = True
            return None, "A identidade da conexão do anúncio mudou. Atualize as conexões das lojas."
        conta["cfg"] = dict(cfg)
        status = getattr(resposta, "status_code", None)
        if status != 200:
            return None, _motivo_consulta_anuncio(status)
        payload = resposta.json() or {}
        if (not isinstance(payload, dict) or str(payload.get("id") or "").strip() != item_id
                or str(payload.get("site_id") or "").strip() != "MLB"):
            return None, "O Mercado Livre não confirmou a identidade e o site do anúncio solicitado."
        return payload, ""
    except requests.Timeout as erro:
        propagar_falha_financeira(erro)
        return None, "Tempo limite ao consultar o anúncio da loja no Mercado Livre. Tente novamente."
    except requests.RequestException as erro:
        propagar_falha_financeira(erro)
        return None, "Falha de conexão ao consultar o anúncio da loja no Mercado Livre. Tente novamente."
    except HTTPException as erro:
        propagar_falha_financeira(erro)
        return None, _motivo_consulta_anuncio(erro.status_code)
    except Exception as erro:
        propagar_falha_financeira(erro)
        return None, _motivo_consulta_anuncio(None)


def _carregar_contexto_anuncio_loja(
    client_id: str,
    loja_lista: str,
    item_id: str,
    deps: dict[str, Any],
    cache_contextos: dict[str, dict[str, Any]],
    *, store_id_alvo: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if item_id in cache_contextos:
        contexto = cache_contextos[item_id]
        return (contexto, "") if contexto.get("ok") else (None, contexto.get("motivo") or "")
    candidatos, motivo = _candidatos_donos_anuncios(client_id, deps, cache_contextos, store_id_alvo=store_id_alvo)
    if not candidatos:
        cache_contextos[item_id] = {"ok": False, "motivo": motivo}
        return None, motivo

    item = None
    candidato_consulta = None
    motivo_falha = _motivo_consulta_anuncio(None)
    for candidato in candidatos:
        if candidato.get("indisponivel"):
            continue
        item, motivo_falha = _consultar_anuncio_na_conta(client_id, item_id, candidato, deps)
        if item is not None:
            candidato_consulta = candidato
            break

    if not isinstance(item, dict):
        motivo = motivo_falha
        cache_contextos[item_id] = {"ok": False, "motivo": motivo}
        return None, motivo

    seller_id = str(item.get("seller_id") or ((item.get("seller") or {}).get("id") if isinstance(item.get("seller"), dict) else "") or "").strip()
    correspondentes = [candidato for candidato in candidatos if seller_id and candidato["seller_id"] == seller_id
                       and candidato["site_id"] == item["site_id"] and not candidato.get("indisponivel")]
    if len(correspondentes) != 1:
        motivo = ("Há mais de uma conta autorizada para o mesmo vendedor e site. Confirme as conexões das lojas."
                  if len(correspondentes) > 1 else "O anúncio não pertence a uma conta autorizada neste cliente e site.")
        cache_contextos[item_id] = {"ok": False, "motivo": motivo}
        return None, motivo
    escolhido = correspondentes[0]
    if candidato_consulta is not escolhido:
        item, motivo = _consultar_anuncio_na_conta(client_id, item_id, escolhido, deps)
        seller_confirmado = str((item or {}).get("seller_id") or "").strip()
        if not item or seller_confirmado != escolhido["seller_id"]:
            motivo = motivo or "A conta proprietária não confirmou o anúncio solicitado."
            cache_contextos[item_id] = {"ok": False, "motivo": motivo}
            return None, motivo

    contexto = {
        "ok": True,
        "loja": escolhido["nome"],
        "store_id": escolhido["store_id"],
        "seller_id": escolhido["seller_id"],
        "site_id": escolhido["site_id"],
        "cfg": escolhido["cfg"],
        "item": item,
    }
    cache_contextos[item_id] = contexto
    return contexto, ""


def _consultar_subsidio_promocao_ativa(
    deps, client_id, dono, cfg, item_id, preco, tarifa, frete, frete_fonte,
) -> float | None:
    """Retorna zero sem promoção ativa, None sem evidência suficiente."""
    try:
        resposta, cfg_atual = deps["ml_api_request"](
            client_id, dono["loja"], cfg, "GET",
            f"https://api.mercadolibre.com/seller-promotions/items/{item_id}",
            params={"app_version": "v2"}, timeout=8,
        )
        if resposta.status_code != 200 or not _cfg_confere_dono_anuncio(cfg_atual, dono):
            return None
        ofertas = resposta.json()
        if isinstance(ofertas, dict):
            ofertas = ofertas.get("results")
        if not isinstance(ofertas, list) or any(not isinstance(oferta, dict) for oferta in ofertas):
            return None
        ativas = []
        for oferta in ofertas:
            status = str(oferta.get("status") or "").strip().lower()
            if status in {"candidate", "pending", "scheduled", "finished", "paused", "cancelled"}:
                continue
            if status not in {"started", "active"}:
                return None
            id_oferta = oferta.get("item_id")
            if id_oferta and _normalizar_item_id_mlb(id_oferta) != item_id:
                return None
            ativas.append(oferta)
        if not ativas:
            return 0.0
        correspondentes = []
        for oferta in ativas:
            preco_oferta = _numero_finito_margem(
                oferta.get("total_price_for_boosted_offer")
                if oferta.get("boosted_offer") is True else oferta.get("price"),
            )
            if preco_oferta is None:
                return None
            if abs(preco_oferta - preco) <= 0.005:
                correspondentes.append(oferta)
        if len(correspondentes) != 1:
            return None
        desconto = _numero_finito_margem(deps["resolver_subsidio"](
            raw_promocao=correspondentes[0], preco_promocional=preco, tarifa_ml=tarifa, frete_ml=frete,
            tarifa_exata=True, frete_exato=True, tarifa_preco_contexto=preco, frete_preco_contexto=preco,
            tarifa_fonte="sites/MLB/listing_prices", frete_fonte=frete_fonte,
        ))
        return round(desconto, 2) if desconto is not None and 0 <= desconto <= tarifa else None
    except Exception as erro:
        propagar_falha_financeira(erro)
        return None


def _consultar_tarifa_frete_margem(
    client_id: str, contexto: dict, item_id: str, preco: float, deps: dict,
) -> tuple[tuple[float, str, float, str] | None, str]:
    loja = contexto["loja"]
    item = contexto["item"]
    cfg = dict(contexto["cfg"])
    motivo_conta = "A conexão da conta do anúncio mudou durante a consulta financeira."
    try:
        tarifa_dados, cfg = deps["ml_obter_taxas_anuncio"](
            client_id, loja, cfg, {**item, "price": preco},
        )
        tarifa_dados = dict(tarifa_dados or {})
    except Exception as erro:
        propagar_falha_financeira(erro)
        tarifa_dados = {}
    if not _cfg_confere_dono_anuncio(cfg, contexto):
        return None, motivo_conta
    tarifa = _numero_finito_margem(tarifa_dados.get("ad_cost"))
    preco_tarifa = _numero_finito_margem(tarifa_dados.get("ad_cost_price_context"))
    if (tarifa is None or tarifa < 0 or not tarifa_dados.get("ad_cost_exact_for_price")
            or preco_tarifa is None or abs(preco_tarifa - preco) > 0.02):
        return None, "Tarifa exata do Mercado Livre não disponível para este preço."

    try:
        frete_dados, cfg = deps["ml_obter_frete_detalhado"](
            client_id, loja, cfg, item_id, item.get("shipping") or {},
            reconsultar_zero=True, contexto_frete=deps["ml_contexto_frete_item"](item, preco),
        )
        frete_dados = dict(frete_dados or {})
    except Exception as erro:
        propagar_falha_financeira(erro)
        frete_dados = {}
    contexto["cfg"] = cfg
    if not _cfg_confere_dono_anuncio(cfg, contexto):
        return None, motivo_conta
    frete = _numero_finito_margem(frete_dados.get("shipping_cost"))
    preco_frete = _numero_finito_margem(frete_dados.get("shipping_price_context"))
    if (frete is None or frete < 0 or not frete_dados.get("shipping_exact_for_price")
            or preco_frete is None or abs(preco_frete - preco) > 0.02):
        return None, "Frete exato do Mercado Livre não disponível para este preço."

    tarifa_fonte = str(tarifa_dados.get("ad_cost_source") or "")
    frete_fonte = str(frete_dados.get("shipping_cost_retry_source") or frete_dados.get("shipping_cost_source_path") or "")
    return (tarifa, tarifa_fonte, frete, frete_fonte), ""


def _item_da_variacao_anuncio(item: dict, variacao: dict) -> dict:
    """Keep item identity and use only the selected variation's product fields."""
    atributos = {str(attr.get("id")): dict(attr) for attr in item.get("attributes") or []
                 if isinstance(attr, dict) and attr.get("id")}
    embalagem_propria = False
    for campo in ("attributes", "attribute_combinations"):
        for atributo in variacao.get(campo) or []:
            if isinstance(atributo, dict) and atributo.get("id"):
                atributos[str(atributo["id"])] = dict(atributo)
                embalagem_propria |= str(atributo["id"]).startswith("SELLER_PACKAGE_")
    projetado = {**item, "attributes": list(atributos.values()),
                 "variations": [dict(variacao)], "variation_id": str(variacao["id"])}
    for campo in ("price", "available_quantity", "sold_quantity", "seller_custom_field"):
        if campo in variacao:
            projetado[campo] = variacao[campo]
    shipping_variacao = variacao.get("shipping") if isinstance(variacao.get("shipping"), dict) else {}
    if shipping_variacao:
        projetado["shipping"] = {**(item.get("shipping") or {}), **shipping_variacao}
    if embalagem_propria and not shipping_variacao.get("dimensions"):
        from backend.services.mercadolivre_legacy_pricing import _ml_extrair_dimensoes_embalagem_item

        # The shipping helper prioritizes shipping.dimensions. Replace the
        # parent's packed dimensions when the selected variation supplies its own.
        dimensoes = _ml_extrair_dimensoes_embalagem_item(projetado)
        projetado["shipping"] = {**(projetado.get("shipping") or {}), "dimensions": dimensoes}
        if not dimensoes:
            projetado["_variacao_embalagem_indisponivel"] = True
    return projetado


def _calcular_margens_concorrentes_promocoes(
    client_id: str,
    loja_lista: str,
    sku: str,
    precos_concorrentes: dict[str, float],
    anuncios_loja: dict[str, str],
    *,
    contexto_consulta: dict | None = None,
    custo_unitario_simulado: float | None = None,
    store_id_alvo: str | None = None,
    anuncios_proprios: bool = False,
    variacoes_anuncios_loja: dict[str, str | None] | None = None,
    store_id_anuncios_loja: str | None = None,
) -> dict[str, dict[str, Any]]:
    simulacao = custo_unitario_simulado is not None or store_id_alvo is not None
    custo_simulado = _numero_finito_margem(custo_unitario_simulado)
    if simulacao and (custo_simulado is None or custo_simulado < 0 or not str(store_id_alvo or "").strip()):
        return {
            chave: _margem_concorrente_indisponivel(
                preco, _normalizar_item_id_mlb(anuncios_loja.get(chave)),
                "O custo simulado e a loja da lista precisam estar disponíveis para analisar este SKU.",
            ) for chave, preco in precos_concorrentes.items()
        }
    deps = dependencias_com_cache(_dependencias_margens_concorrentes())
    margens: dict[str, dict[str, Any]] = {}
    # Both price series belong to one request. Never reuse an item's financial
    # context across a tenant, store or SKU, even if the MLB happens to match.
    cache_consulta = {} if contexto_consulta is None else contexto_consulta
    escopo = (client_id, loja_lista, sku, store_id_anuncios_loja)
    if simulacao:
        escopo = (*escopo, "simulacao_importacao_sku", store_id_alvo, custo_simulado)
    cache_escopo = cache_consulta.setdefault(escopo, {})
    cache_contextos = cache_escopo.setdefault("anuncios", {})
    cache_cadastro = cache_escopo.setdefault("cadastro", {})
    cache_cotacoes = cache_escopo.setdefault("cotacoes", {})
    cache_tarifa_frete = cache_escopo.setdefault("tarifa_frete", {})

    for chave in sorted(precos_concorrentes):
        preco_venda = float(precos_concorrentes[chave])
        item_id = _normalizar_item_id_mlb(anuncios_loja.get(chave))
        variation_id = (variacoes_anuncios_loja or {}).get(chave)
        if not item_id:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                "",
                "Informe o anúncio da loja correspondente a este concorrente.",
            )
            continue

        cotacao_chave = (item_id, variation_id, preco_venda, anuncios_proprios)
        cotacao_salva = cache_cotacoes.get(cotacao_chave)
        if cotacao_salva is not None:
            margens[chave] = dict(cotacao_salva)
            continue

        contexto, motivo = _carregar_contexto_anuncio_loja(
            client_id,
            loja_lista,
            item_id,
            deps,
            cache_contextos,
            **({"store_id_alvo": store_id_alvo} if simulacao else
               {"store_id_alvo": store_id_anuncios_loja} if store_id_anuncios_loja else {}),
        )
        if not contexto:
            margens[chave] = _margem_concorrente_indisponivel(preco_venda, item_id, motivo)
            continue

        loja = str(contexto.get("loja") or "").strip()
        store_id = str(contexto.get("store_id") or "").strip()
        item = dict(contexto.get("item") or {})
        if simulacao or variacoes_anuncios_loja is not None:
            from backend.services.importacoes_sku_anuncios import anuncio_corresponde_sku_exato, resolver_variacao_sku

            if not anuncio_corresponde_sku_exato(item, sku):
                margens[chave] = _margem_concorrente_indisponivel(
                    preco_venda, item_id, "O anúncio da loja da lista não confirmou o SKU solicitado.", loja=loja,
                )
                continue
            if item.get("variations"):
                variacao = resolver_variacao_sku(item, sku, variation_id)
                # New clients must identify the same variation they display.
                if not variacao or (variacoes_anuncios_loja is not None and not variation_id):
                    margens[chave] = _margem_concorrente_indisponivel(
                        preco_venda, item_id, "A variação do anúncio não confirmou o SKU solicitado.", loja=loja,
                    )
                    continue
                variation_id = str(variacao["id"])
                item = _item_da_variacao_anuncio(item, variacao)
                if item.get("_variacao_embalagem_indisponivel"):
                    margens[chave] = _margem_concorrente_indisponivel(
                        preco_venda, item_id, "As dimensões da embalagem desta variação não foram confirmadas.", loja=loja,
                    )
                    continue
            elif variation_id:
                margens[chave] = _margem_concorrente_indisponivel(
                    preco_venda, item_id, "A variação solicitada não pertence a este anúncio.", loja=loja,
                )
                continue
        cadastro_key = (store_id, sku)
        if cadastro_key not in cache_cadastro:
            try:
                custos, impostos = deps["carregar_custos_impostos"](client_id, loja, store_id, sku)
                cache_cadastro[cadastro_key] = (dict(custos or {}), dict(impostos or {}))
            except Exception:
                cache_cadastro[cadastro_key] = ({}, {})
        custos_cadastro, impostos_cadastro = cache_cadastro[cadastro_key]
        custo_unitario = custo_simulado if simulacao else deps["resolver_custo"](custos_cadastro, sku)
        custo_unitario = _numero_finito_margem(custo_unitario)
        if custo_unitario is None or custo_unitario < 0:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Custo do SKU não encontrado no cadastro desta loja.",
                loja=loja,
            )
            continue

        imposto_rate = deps["resolver_imposto"](impostos_cadastro, sku)
        imposto_rate = _numero_finito_margem(imposto_rate)
        if imposto_rate is None or imposto_rate < 0:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Imposto do SKU não encontrado para esta loja.",
                loja=loja,
            )
            continue

        # Próprio e concorrente usam a mesma tarifa/frete quando o anúncio,
        # a conta e o preço são iguais. O subsídio segue exclusivo do próprio.
        chave_tarifa_frete = (item_id, variation_id, preco_venda)
        insumos = cache_tarifa_frete.get(chave_tarifa_frete)
        if insumos is None:
            contexto_financeiro = {**contexto, "item": item}
            insumos, motivo = _consultar_tarifa_frete_margem(client_id, contexto_financeiro, item_id, preco_venda, deps)
            contexto["cfg"] = contexto_financeiro["cfg"]
            if insumos is None:
                margens[chave] = _margem_concorrente_indisponivel(preco_venda, item_id, motivo, loja=loja)
                continue
            cache_tarifa_frete[chave_tarifa_frete] = insumos
        tarifa_ml, tarifa_fonte, frete_ml, frete_fonte = insumos
        cfg = dict(contexto["cfg"])
        if not _cfg_confere_dono_anuncio(cfg, contexto):
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda, item_id, "A conexão da conta do anúncio mudou durante a consulta financeira.", loja=loja,
            )
            continue
        subsidio = None
        if anuncios_proprios and item.get("status") == "paused":
            subsidio = 0.0
        elif anuncios_proprios and tarifa_fonte == "sites/MLB/listing_prices":
            subsidio = _consultar_subsidio_promocao_ativa(
                deps, client_id, contexto, cfg, item_id, preco_venda, tarifa_ml, frete_ml, frete_fonte,
            )
        tarifa_efetiva = tarifa_ml - (subsidio or 0)
        financeiro = deps["calcular_margem"](
            preco_venda,
            custo_unitario,
            imposto_rate,
            tarifa_efetiva,
            frete_ml,
        )
        if not financeiro:
            margens[chave] = _margem_concorrente_indisponivel(
                preco_venda,
                item_id,
                "Não foi possível calcular a margem financeira.",
                loja=loja,
            )
            continue

        margens[chave] = {
            "preco_venda": round(preco_venda, 2),
            "item_id_loja": item_id,
            "loja": loja,
            "store_id": store_id,
            "seller_id": contexto["seller_id"],
            "site_id": contexto["site_id"],
            "custo_unitario": round(float(custo_unitario), 2),
            "imposto_percentual": round(float(imposto_rate) * 100.0, 4),
            "imposto_valor": round(float(financeiro["imposto"]), 2),
            "tarifa_ml": round(float(tarifa_ml), 2),
            "frete_ml": round(float(frete_ml), 2),
            "valor_liquido": round(float(financeiro["valor_liquido"]), 2),
            "margem_percentual": round(float(financeiro["margem_percentual"]), 2),
            "financeiro_exato": True,
            "tarifa_fonte": tarifa_fonte[:120],
            "frete_fonte": frete_fonte[:120],
        }
        if simulacao:
            margens[chave]["fonte_custo"] = "simulacao_importacao_sku"
        if variacoes_anuncios_loja is not None or variation_id:
            margens[chave]["variation_id"] = variation_id
        if anuncios_proprios:
            margens[chave].update(subsidio_ml=subsidio, tarifa_ml_efetiva=round(tarifa_efetiva, 2))
        cache_cotacoes[cotacao_chave] = dict(margens[chave])

    return margens


async def api_medias_compras_listas_pedidos(
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = Depends(medias_common.get_tenant_id)
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_listas_pedidos_sync, loja=loja, store_id=store_id, client_id=client_id)


@consulta_medias
def _api_medias_compras_listas_pedidos_sync(
    loja: str = "__todas",
    store_id: str = "",
    client_id: str = Depends(medias_common.get_tenant_id)
):
    listas = _carregar_listas_pedidos(client_id)
    escopo = _resolver_escopo_loja_medias(client_id, loja, store_id)
    store_id_alvo = escopo["store_id"]
    if store_id_alvo:
        filtradas = []
        for lista in listas:
            lista_store_id = str((lista or {}).get("store_id") or "").strip()
            if lista_store_id:
                if lista_store_id == store_id_alvo:
                    filtradas.append(lista)
                continue
            try:
                legado = _resolver_escopo_loja_medias(
                    client_id,
                    str((lista or {}).get("loja") or ""),
                    "",
                    exigir_especifica=True,
                )
            except HTTPException:
                continue
            if legado["store_id"] == store_id_alvo:
                filtradas.append(lista)
        listas = filtradas
    skus_por_referencia: dict[str, set[str]] = {}
    for lista in listas:
        if _cubagem_lista_historica(lista):
            continue
        referencia = str(lista.get("store_id") or lista.get("loja") or "").strip()
        skus = skus_por_referencia.setdefault(referencia, set())
        for item in lista.get("itens") or []:
            sku = _primeiro_texto_item(item, ("SKU", "sku"))
            if sku:
                skus.add(sku)
    mapas_m3 = {
        referencia: _construir_mapa_m3_sku(client_id, loja=referencia, skus=sorted(skus))
        for referencia, skus in skus_por_referencia.items()
    }
    resumos = []
    for lista in listas:
        referencia = str(lista.get("store_id") or lista.get("loja") or "").strip()
        resumos.append(_resumo_lista_pedido(lista, mapas_m3.get(referencia, {}), client_id=client_id))
    return {"success": True, "listas": resumos}


async def api_medias_compras_skus_ocultos_get(
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_skus_ocultos_get_sync, request=request, client_id=client_id)


def _api_medias_compras_skus_ocultos_get_sync(
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    prefs = _carregar_preferencias_skus_ocultos_medias(client_id, username)
    return {
        "success": True,
        "skus_ocultos": prefs.get("skus_ocultos") or [],
        "updated_at": prefs.get("updated_at"),
    }


async def api_medias_compras_skus_ocultos_put(
    req: MediasComprasSkusOcultosRequest,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_skus_ocultos_put_sync, req=req, request=request, client_id=client_id)


def _api_medias_compras_skus_ocultos_put_sync(
    req: MediasComprasSkusOcultosRequest,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _salvar_preferencias_skus_ocultos_medias(client_id, username, req.skus_ocultos or [])
    return {
        "success": True,
        "skus_ocultos": payload.get("skus_ocultos") or [],
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_preferencias_colunas_get(client_id: str = Depends(medias_common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_preferencias_colunas_get_sync, client_id=client_id)


def _api_medias_compras_preferencias_colunas_get_sync(client_id: str = Depends(medias_common.get_tenant_id)):
    prefs = _carregar_preferencias_colunas_importacoes(client_id)
    return {
        "success": True,
        "ordem_colunas": prefs.get("ordem_colunas") or [],
        "larguras_colunas": prefs.get("larguras_colunas") or {},
        "colunas_ocultas": prefs.get("colunas_ocultas") or [],
    }


async def api_medias_compras_preferencias_colunas_put(
    req: ListaPedidoPreferenciasColunasRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_preferencias_colunas_put_sync, req=req, client_id=client_id)


def _api_medias_compras_preferencias_colunas_put_sync(
    req: ListaPedidoPreferenciasColunasRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    payload = _salvar_preferencias_colunas_importacoes(client_id, {
        "ordem_colunas": req.ordem_colunas or [],
        "larguras_colunas": req.larguras_colunas or {},
        "colunas_ocultas": req.colunas_ocultas,
    })
    return {
        "success": True,
        "ordem_colunas": payload.get("ordem_colunas") or [],
        "larguras_colunas": payload.get("larguras_colunas") or {},
        "colunas_ocultas": payload.get("colunas_ocultas") or [],
        "updated_at": payload.get("updated_at"),
    }


async def api_medias_compras_concorrentes_links(
    sku: str,
    client_id: str = Depends(medias_common.get_tenant_id),
    lista_id: str | None = None,
    anuncios_da_lista: bool = False,
    somente_proprios: bool = False,
):
    from backend.services.blocking_workers import run_blocking, run_heavy, run_import_offer_source
    from backend.services.importacoes_sku_anuncios import (
        buscar_anuncios_sku_da_lista, confirmar_escopo_anuncios_sku,
    )

    sku_in = str(sku or "").strip()
    if not sku_in:
        raise HTTPException(400, "SKU é obrigatório.")
    if somente_proprios and not anuncios_da_lista:
        raise HTTPException(400, "Informe anuncios_da_lista para consultar somente os anúncios próprios.")
    if anuncios_da_lista and not str(lista_id or "").strip():
        raise HTTPException(400, "Informe a lista para consultar os anúncios deste SKU na loja vinculada.")

    try:
        if not anuncios_da_lista:
            return await run_heavy(_links_concorrentes_planilha, client_id, lista_id, sku_in)

        lista, cache_scope = await run_blocking(_preparar_anuncios_lista, client_id, lista_id, sku_in)
        consulta_anuncios = lambda: run_import_offer_source(
            "anuncios", buscar_anuncios_sku_da_lista, client_id, lista, sku_in, cache_scope=cache_scope,
        )
        if somente_proprios:
            _, fontes_versao = await run_blocking(_fonte_concorrentes_lista, client_id, lista)
            indice, consultado_planilha = {}, None
            anuncios = await consulta_anuncios()
        else:
            (indice, fontes_versao, consultado_planilha), anuncios = await asyncio.gather(
                run_import_offer_source("planilha", _indice_concorrentes_sku_cache, client_id, lista, cache_scope),
                consulta_anuncios(),
            )
            # The sheet may have finished after the ad lookup checked its credentials.
            await run_blocking(confirmar_escopo_anuncios_sku, client_id, anuncios)

        dados = _payload_concorrentes_sku(sku_in, indice, fontes_versao)
        return _aplicar_anuncios_proprios(dados, anuncios, consultado_planilha)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Erro ao buscar fontes de anúncios do SKU")
        raise HTTPException(500, "Erro ao buscar links de concorrentes")


def _carregar_lista_links_concorrentes(client_id: str, lista_id: str | None, sku: str) -> dict | None:
    if lista_id is None:
        return None
    listas = _carregar_listas_pedidos(client_id)
    lista = next((item for item in listas if str(item.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(404, "Lista de pedidos nao encontrada")
    sku_norm = _normalizar_sku_planilha_concorrentes(_normalizar_sku_mes(sku))
    if not any(
        _normalizar_sku_planilha_concorrentes(_sku_item_lista_pedido(item)) == sku_norm
        for item in (lista.get("itens") or []) if isinstance(item, dict)
    ):
        raise HTTPException(404, "SKU nao encontrado na lista de pedidos")
    return lista


def _payload_concorrentes_sku(sku: str, indice: dict, versao: int) -> dict:
    return {
        **_payload_concorrentes_planilha(sku, indice.get(_normalizar_sku_planilha_concorrentes(sku))),
        "fontes_versao": versao,
    }


def _links_concorrentes_planilha(client_id: str, lista_id: str | None, sku: str) -> dict:
    lista = _carregar_lista_links_concorrentes(client_id, lista_id, sku)
    indice, versao = _indice_concorrentes_lista(client_id, lista)
    return _payload_concorrentes_sku(sku, indice, versao)


def _preparar_anuncios_lista(client_id: str, lista_id: str, sku: str) -> tuple[dict, str]:
    from backend.services.medias_compras_leitura import chave_arquivo

    lista = _carregar_lista_links_concorrentes(client_id, lista_id, sku)
    if not str(lista.get("store_id") or "").strip():
        escopo = medias_common._resolver_escopo_loja_medias(client_id, lista.get("loja"), None)
        if escopo["scope"] == "store" and escopo["store_id"]:
            # A list opened through the compact detail need not have a persisted store ID.
            lista = {**lista, "store_id": escopo["store_id"], "loja": escopo["loja"]}
    return lista, chave_arquivo(get_tenant_path(client_id))


def _aplicar_anuncios_proprios(payload: dict, anuncios: dict, consultado_planilha: int | None) -> dict:
    consultado_anuncios = anuncios["consultado_em_ms"]
    consultado_em = min(consultado_planilha, consultado_anuncios) if consultado_planilha is not None else consultado_anuncios
    payload["concorrentes_mlb"] = {f"concorrente_{numero}": "" for numero in range(1, 6)}
    payload["concorrentes_preco"] = {f"concorrente_{numero}": "" for numero in range(1, 6)}
    payload["anuncios_loja_status"] = {}
    payload["anuncios_loja_variacoes"] = {}
    payload["anuncios_loja"] = []
    for numero, anuncio in enumerate(anuncios.get("anuncios") or [], start=1):
        chave = f"concorrente_{numero}"
        payload["concorrentes_mlb"][chave] = anuncio["url"]
        payload["anuncios_loja_status"][chave] = anuncio.get("status")
        payload["anuncios_loja_variacoes"][chave] = anuncio.get("variation_id")
        payload["concorrentes_preco"][chave] = anuncio["preco"] if anuncio.get("preco") is not None else ""
        payload["anuncios_loja"].append({**copy.deepcopy(anuncio), "chave": chave})
    payload.update({
        "anuncios_loja_store_id": anuncios["store_id"],
        "anuncios_loja_nome": anuncios["loja"],
        "anuncios_loja_seller_id": anuncios["seller_id"],
        "anuncios_loja_site_id": anuncios["site_id"],
        "consultado_em_ms": consultado_em,
        "anuncios_loja_fonte": "loja_lista_sku",
        "anuncios_loja_total": anuncios.get("total_encontrado", len(anuncios.get("anuncios") or [])),
        "anuncios_loja_truncado": bool(anuncios.get("truncado")),
        "anuncios_loja_limite": anuncios.get("limite"),
        "anuncios_versao": anuncios.get("anuncios_versao", 2),
    })
    return payload


def _normalizar_sku_planilha_concorrentes(valor: Any) -> str:
    texto = str(valor or "").strip().upper().replace(" ", "")
    return re.sub(r"\.0+$", "", texto)


def _carregar_linhas_planilha_concorrentes(spreadsheet_id: str | None = None) -> list[list[str]]:
    gc = autenticar_google_sheets()
    if not gc:
        raise HTTPException(status_code=500, detail="Não foi possível autenticar no Google Sheets")

    sh = gc.open_by_key(spreadsheet_id or SPREADSHEET_ID_CONCORRENTES)
    ws = sh.get_worksheet(0)
    if ws is None:
        raise HTTPException(status_code=404, detail="Aba da planilha não encontrada")
    return list(ws.get_all_values() or [])


def _fonte_concorrentes_lista(client_id: str, lista: dict) -> tuple[str, int]:
    from backend.services.favoritos_planilhas_colar import id_planilha_favoritos_loja

    escopo = medias_common._resolver_escopo_loja_medias(client_id, lista.get("loja"), lista.get("store_id"))
    store_id = escopo["store_id"]
    planilha = id_planilha_favoritos_loja(client_id, escopo["loja"]) if store_id else ""
    # Versões 1 e 2 pertencem às antigas fontes fixas. A troca de link invalida a análise salva.
    identidade = json.dumps([client_id, store_id, planilha])
    versao = 3 + int(sha256(identidade.encode()).hexdigest()[:12], 16)
    return planilha, versao


def _indice_concorrentes_lista(client_id: str, lista: dict | None) -> tuple[dict[str, list[str]], int]:
    if lista is None:
        return _indexar_linhas_planilha_concorrentes(_carregar_linhas_planilha_concorrentes()), 1
    planilha, versao = _fonte_concorrentes_lista(client_id, lista)
    linhas = _carregar_linhas_planilha_concorrentes(planilha) if planilha else []
    return _indexar_linhas_planilha_concorrentes(linhas), versao


_CACHE_INDICES_CONCORRENTES = CacheConsultas(16)


def _indice_concorrentes_sku_cache(client_id: str, lista: dict, cache_scope: str):
    planilha, versao = _fonte_concorrentes_lista(client_id, lista)
    if not planilha:
        return {}, versao, None
    consulta = _CACHE_INDICES_CONCORRENTES.consultar(
        (client_id, cache_scope, planilha, versao),
        lambda: _indexar_linhas_planilha_concorrentes(_carregar_linhas_planilha_concorrentes(planilha)),
    )
    return consulta.valor, versao, consulta.consultado_em_ms


def _indexar_linhas_planilha_concorrentes(rows: list[list[str]]) -> dict[str, list[str]]:
    if not rows:
        return {}

    header = rows[0]
    sku_col_idx = 0
    for indice, cabecalho in enumerate(header):
        cabecalho_norm = str(cabecalho or "").strip().lower()
        if cabecalho_norm in {
            "sku",
            "código",
            "codigo",
            "cã³digo",
            "cãƒâ³digo",
            "codigo sku",
            "sku code",
        }:
            sku_col_idx = indice
            break

    resultado: dict[str, list[str]] = {}
    for row in rows[1:]:
        if sku_col_idx >= len(row):
            continue
        sku_norm = _normalizar_sku_planilha_concorrentes(row[sku_col_idx])
        if sku_norm and sku_norm not in resultado:
            resultado[sku_norm] = row
    return resultado


def _payload_concorrentes_planilha(sku: str, row: list[str] | None) -> dict[str, Any]:
    linha = row or []

    def _get_col(indice: int) -> str:
        if indice < 0 or indice >= len(linha):
            return ""
        return str(linha[indice] or "").strip()

    return {
        "success": True,
        "sku": str(sku or "").strip(),
        "concorrentes": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((4, 8, 12, 16, 20), start=1)
        },
        "concorrentes_valores": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((5, 9, 13, 17, 21), start=1)
        },
        "concorrentes_mlb": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((2, 6, 10, 14, 18), start=1)
        },
        "concorrentes_preco": {
            f"concorrente_{numero}": _get_col(indice)
            for numero, indice in enumerate((3, 7, 11, 15, 19), start=1)
        },
    }


async def api_medias_compras_lista_pedido_concorrentes_links_lote(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_concorrentes_links_lote_sync, lista_id=lista_id, client_id=client_id)


def _api_medias_compras_lista_pedido_concorrentes_links_lote_sync(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((item for item in listas if str(item.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    skus: list[str] = []
    for item in (lista.get("itens") or []):
        sku = _sku_item_lista_pedido(item)
        if sku and sku not in skus:
            skus.append(sku)

    try:
        indice, fontes_versao = _indice_concorrentes_lista(client_id, lista)
        resultados = {
            sku: {
                **_payload_concorrentes_planilha(
                    sku,
                    indice.get(_normalizar_sku_planilha_concorrentes(sku)),
                ),
                "fontes_versao": fontes_versao,
            }
            for sku in skus
        }
        return {
            "success": True,
            "lista_id": str(lista_id),
            "total": len(resultados),
            "resultados": resultados,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao buscar links de concorrentes da lista '{lista_id}': {e}")
        raise HTTPException(status_code=500, detail="Erro ao buscar links de concorrentes da lista")


async def api_medias_compras_lista_pedido_detalhe(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
    somente_salvos: bool = False,
    cache_mode: Literal["stale"] | None = None,
):
    detalhe = await _consultar_detalhe_lista_pedido(lista_id, client_id, somente_salvos, cache_mode)
    if isinstance(detalhe, dict):
        detalhe.pop("_revisao_lista_custo_posto", None)
    return detalhe


async def _consultar_detalhe_lista_pedido(
    lista_id: str, client_id: str, somente_salvos: bool = False,
    cache_mode: Literal["stale"] | None = None, *, preferir_fresco: bool = False,
):
    from backend.services.blocking_workers import run_blocking

    if somente_salvos and cache_mode is None:
        return await run_blocking(
            _api_medias_compras_lista_pedido_detalhe_sync,
            lista_id=lista_id,
            client_id=client_id,
            somente_salvos=True,
        )
    from backend.services.medias_compras_cache import resultados_medias
    from backend.services.medias_compras_fontes import capturar_fontes, digest_lista, extras_runtime
    from backend.services.medias_compras_leitura import chave_arquivo
    import sys

    if cache_mode not in (None, "stale"):
        raise HTTPException(422, "cache_mode deve ser stale")
    tenant = get_tenant_path(client_id)
    tenant_key = chave_arquivo(tenant)

    def probe():
        if chave_arquivo(get_tenant_path(client_id)) != tenant_key:
            raise HTTPException(409, "A origem dos dados mudou durante a consulta.")
        snapshot = _carregar_listas_pedidos(client_id)
        target = next((row for row in snapshot if str(row.get("id", "")) == str(lista_id)), None)
        if target is None:
            raise HTTPException(404, "Lista de pedidos nao encontrada")
        extras = list(extras_runtime(sys.modules[__name__]))
        if isinstance(target.get("packing_list"), dict):
            from backend.services.medias_compras_packing import _arquivo_packing, PACKING_SCHEMA
            if target["packing_list"].get("schema") == PACKING_SCHEMA:
                try:
                    extras.append(_arquivo_packing(client_id, target["packing_list"].get("documento_id")))
                except HTTPException:
                    pass
        return capturar_fontes(
            tenant, detalhe=True, extras=extras,
            alvo=digest_lista(target), snapshot=snapshot,
            escopo=(str(lista_id),),
        )

    if somente_salvos:
        from backend.services.medias_compras_cache import consultar_snapshot_salvo
        return await consultar_snapshot_salvo(
            probe, lambda source: _api_medias_compras_lista_pedido_detalhe_sync(
                lista_id=lista_id, client_id=client_id, somente_salvos=True, _snapshot=source.snapshot,
            ),
        )

    # The owner controls the two-attempt budget, including transaction conflicts.
    core = _api_medias_compras_lista_pedido_detalhe_sync.__wrapped__

    def carregar(source):
        detalhe = core(lista_id=lista_id, client_id=client_id, _snapshot=source.snapshot)
        # O backfill do detalhe pode atualizar a lista antes de publicar o resultado.
        alvo = next(row for row in source.snapshot if str(row.get("id", "")) == str(lista_id))
        detalhe["_revisao_lista_custo_posto"] = digest_lista(alvo)
        return detalhe

    return await resultados_medias.consultar(
        client_id, (tenant_key, "lista", str(lista_id)), probe, carregar,
        stale=cache_mode == "stale", preferir_fresco=preferir_fresco,
    )


def _itens_salvos_lista_pedido_no_escopo(client_id: str, lista: dict) -> list[dict]:
    from backend.services import cadastro_fotos

    itens = copy.deepcopy(lista.get("itens") or [])
    store_id = str(lista.get("store_id") or "").strip()
    campos_editor = (
        "SKU", "Foto", TITULO_PRODUTO_INGLES_KEY, "OEM", COR_LADO_LISTA_PEDIDO_KEY,
        "Link", "Quantidade", "Valor unidade", "Valor total", CBM_LISTA_PEDIDO_KEY,
        PESO_LISTA_PEDIDO_KEY, EMBALAGEM_LISTA_PEDIDO_KEY, "M3 individual",
    )
    aliases_numericos = {
        "Quantidade": ("Quantidade", "quantity", "Qtd", "Qtde", "Qty"),
        "Valor unidade": ("Valor unidade", "Valor unitario", "Valor unitário", "Cost", "Custo", "Preco", "Preço"),
        "Valor total": ("Valor total", "Sub-total(USD)", "Subtotal USD", "Sub total", "Subtotal"),
    }
    referencias_permitidas: dict[str, bool] = {}
    for item in itens:
        if not isinstance(item, dict):
            continue
        campos_ausentes = [
            campo for campo in campos_editor
            if item.get(campo) is None
            or (isinstance(item.get(campo), str) and not item[campo].strip())
        ]
        if campos_ausentes:
            normalizado = _normalizar_item_lista_pedido(item)
            for campo in campos_ausentes:
                if item.get("Cubagem status") == "pendente" and campo in {CBM_LISTA_PEDIDO_KEY, "M3 individual"}:
                    # Uma cubagem explicitamente pendente ja descartou estes
                    # aliases; a abertura rapida nao pode reanima-los.
                    continue
                valor = normalizado.get(campo)
                if valor is None or (isinstance(valor, str) and not valor.strip()):
                    continue
                # Defaults numericos do normalizador nao substituem aliases
                # inexistentes; zeros explicitamente salvos sao preservados.
                if campo in aliases_numericos and not _primeiro_texto_item(item, aliases_numericos[campo]):
                    continue
                item[campo] = valor
        for campo, referencia in cadastro_fotos._cadastro_foto_referencias_candidatas(item):
            if not cadastro_fotos._cadastro_foto_referencia_local_cadastro(referencia):
                continue
            texto = str(referencia or "")
            if texto not in referencias_permitidas:
                # O snapshot nao resolve aliases de lojas nem procura fotos.
                # Uma foto local exige identidade explicita da loja e tenant.
                partes = cadastro_fotos._cadastro_foto_partes_loja_referencia_local(texto, client_id)
                permitida = False
                if store_id and partes is not None and len(partes) == 3:
                    try:
                        permitida = cadastro_fotos._cadastro_foto_referencia_pertence_loja(
                            client_id, store_id, texto,
                        )
                    except (OSError, RuntimeError, ValueError):
                        pass
                referencias_permitidas[texto] = permitida
            if not referencias_permitidas[texto]:
                item[campo] = ""
    return itens


@_retry_listas_pedidos
@consulta_medias
def _api_medias_compras_lista_pedido_detalhe_sync(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
    somente_salvos: bool = False,
    *, _snapshot=None,
):
    listas = _snapshot if _snapshot is not None else _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    alvo = listas[idx] if idx >= 0 else None
    if not alvo:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    if alvo.get("unida_em_lista_id"):
        return {
            "success": True,
            "lista": {**_resumo_lista_pedido(alvo, client_id=client_id), "itens": copy.deepcopy(alvo.get("itens") or [])},
        }

    if somente_salvos:
        itens_salvos = _itens_salvos_lista_pedido_no_escopo(client_id, alvo)
        return {
            "success": True,
            "lista": {
                **_resumo_lista_pedido({**alvo, "itens": itens_salvos}, client_id=client_id),
                "itens": itens_salvos,
            },
        }

    # Backfill: garante coluna persistida de frete internacional para listas antigas.
    # Listas novas guardam o store_id imutavel. As antigas ainda podem trazer o
    # nome atual ou um nome historico unico da loja.
    store_id_antes = str(alvo.get("store_id") or "").strip()
    referencia_loja_cadastro = str(
        store_id_antes or alvo.get("loja") or ""
    ).strip()
    escopo_cadastro_nao_resolvido = False
    contexto_preparado = None
    if referencia_loja_cadastro and referencia_loja_cadastro != "__todas":
        try:
            from backend.services.cadastro_compatibilidade import (
                visao_produtos_cadastro_contexto_loja,
            )

            with medir_etapa("cadastro_contexto"):
                contexto_cadastro = visao_produtos_cadastro_contexto_loja(
                    client_id,
                    referencia_loja_cadastro,
                    skus=[_primeiro_texto_item(item, ("SKU", "sku")) for item in alvo.get("itens") or [] if isinstance(item, dict)],
                )
            escopo_cadastro_nao_resolvido = (
                contexto_cadastro.get("scope") == "unresolved"
            )
            store_id_resolvido = str(
                contexto_cadastro.get("store_id") or ""
            ).strip()
            if store_id_resolvido and not store_id_antes:
                alvo["store_id"] = store_id_resolvido
                referencia_loja_cadastro = store_id_resolvido
        except RuntimeError:
            escopo_cadastro_nao_resolvido = True
            contexto_cadastro = {
                "produtos": [],
                "store_id": "",
                "loja_resolvida": False,
                "scope": "unavailable",
            }
        contexto_preparado = _ContextoCadastroLista(
            client_id, referencia_loja_cadastro, contexto_cadastro,
        )

    opcoes_contexto = {"contexto_cadastro": contexto_preparado} if contexto_preparado is not None else {}
    itens_recalculados = _recalcular_frete_internacional_itens_lista(
        client_id,
        alvo.get("itens") or [],
        loja=referencia_loja_cadastro,
        preservar_cubagem=_cubagem_lista_historica(alvo),
        **opcoes_contexto,
    )
    itens_persistidos = itens_recalculados
    if escopo_cadastro_nao_resolvido:
        # A resposta permanece fail-closed, mas um GET nunca apaga do JSON a
        # referencia anterior enquanto a identidade da loja estiver ambigua.
        itens_persistidos = [dict(item) for item in itens_recalculados]
        for indice, original in enumerate(alvo.get("itens") or []):
            if indice >= len(itens_persistidos) or not isinstance(original, dict):
                continue
            foto_original = str(original.get("Foto") or "").strip()
            if foto_original:
                itens_persistidos[indice]["Foto"] = foto_original
    with medir_etapa("comparacao"):
        mudou_itens = json.dumps(
            itens_persistidos,
            ensure_ascii=False,
            sort_keys=True,
        ) != json.dumps(alvo.get("itens") or [], ensure_ascii=False, sort_keys=True)
    mudou_store_id = str(alvo.get("store_id") or "").strip() != store_id_antes
    if mudou_itens or mudou_store_id:
        alvo["itens"] = itens_persistidos
        alvo["updated_at"] = datetime.now().isoformat(timespec="seconds")
        listas[idx] = alvo
        with medir_etapa("persistencia"):
            _salvar_listas_pedidos(client_id, listas)
        from backend.services.medias_compras_cache import registrar_commit_lista
        from backend.services.medias_compras_fontes import digest_lista
        committed = next(row for row in listas if str(row.get("id", "")) == str(lista_id))
        registrar_commit_lista(digest_lista(committed))
        _limpar_cache_lista_pedido(client_id, str(alvo.get("id", "") or ""), manter_versao=alvo.get("updated_at"))

    with medir_etapa("resumo"):
        resumo = _resumo_lista_pedido(alvo, client_id=client_id)
    # Apenas o detalhe completo resolve a loja; restaurar uma copia salva
    # continua sem consultas complementares. Nenhuma planilha e lida aqui.
    try:
        resumo["analise_concorrentes_fontes_versao"] = _fonte_concorrentes_lista(client_id, alvo)[1]
    except (HTTPException, RuntimeError):
        resumo["analise_concorrentes_fontes_versao"] = 0
    return {
        "success": True,
        "lista": {
            **resumo,
            "itens": itens_recalculados,
        }
    }


async def api_medias_compras_lista_pedido_custo_posto(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    """Return the backend-authoritative landed-cost calculation for one import list."""
    from backend.services.blocking_workers import run_heavy

    detalhe = await _consultar_detalhe_lista_pedido(lista_id, client_id, preferir_fresco=True)
    return await run_heavy(
        _api_medias_compras_lista_pedido_custo_posto_sync, lista_id=lista_id, client_id=client_id, detalhe=detalhe,
    )


def _api_medias_compras_lista_pedido_custo_posto_sync(
    lista_id: str,
    client_id: str,
    detalhe: dict,
):
    """Return landed cost only after the order cubagem has been resolved."""
    from backend.services import codex_reports_advanced
    from backend.services.medias_compras_fontes import digest_lista

    if not (detalhe.get("lista") or {}).get("m3_completo", False):
        raise HTTPException(
            status_code=409,
            detail="Cubagem pendente: complete os m³ dos produtos antes de calcular o custo posto.",
        )

    def validar_lista(alvo):
        if digest_lista(alvo) != detalhe["_revisao_lista_custo_posto"]:
            raise HTTPException(
                status_code=409,
                detail={"code": "sources_changing", "message": "A lista mudou durante o cálculo do custo posto. Tente novamente."},
            )

    context = codex_reports_advanced.build_profile_context(
        info_base=str(medias_common.PASTA_INFO or ""),
        client_id=str(client_id or "default"),
        profile="import_order",
        import_list_id=str(lista_id or ""),
        validate_import_order=validar_lista,
        margin_rows=[],
    )
    analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else None
    if not analysis:
        raise HTTPException(status_code=404, detail="Lista de importacao nao encontrada")
    return {
        "success": True,
        "analysis": analysis,
        "supplier_performance": context.get("supplier_performance"),
        "scope": context.get("scope"),
        "data_quality": context.get("data_quality"),
    }


def _item_lista_pedido_compra_aprovada(item: dict[str, Any] | None) -> bool:
    item = item if isinstance(item, dict) else {}
    valor = item.get("compra_aprovada", item.get("Compra aprovada"))
    if valor is True or valor == 1:
        return True
    return str(valor or "").strip().lower() in {"1", "true", "sim", "aprovada", "aprovado"}


def _sku_item_lista_pedido(item: dict[str, Any] | None) -> str:
    item = item if isinstance(item, dict) else {}
    return _normalizar_sku_mes(str(item.get("SKU") or item.get("sku") or "").strip())


def _proteger_itens_aprovados_lista(
    itens_atuais: list[dict] | None,
    itens_novos: list[dict] | None,
) -> list[dict]:
    novos = [dict(item) for item in (itens_novos or []) if isinstance(item, dict)]
    novos_por_sku = {
        _sku_item_lista_pedido(item): item
        for item in novos
        if _sku_item_lista_pedido(item)
    }

    for item_atual in itens_atuais or []:
        sku = _sku_item_lista_pedido(item_atual)
        item_novo = novos_por_sku.get(sku)
        if item_novo:
            for campo in ("analise_concorrentes", "analise_simulacao_importacao_sku"):
                if campo in item_atual:
                    item_novo[campo] = copy.deepcopy(item_atual[campo])

        if not _item_lista_pedido_compra_aprovada(item_atual):
            continue
        if not item_novo:
            raise HTTPException(
                status_code=409,
                detail=f"O SKU {sku} esta aprovado e nao pode ser excluido. Desfaca a aprovacao dentro do SKU.",
            )
        if not _item_lista_pedido_compra_aprovada(item_novo):
            raise HTTPException(
                status_code=409,
                detail=f"A aprovacao do SKU {sku} so pode ser desfeita dentro do SKU.",
            )

        item_novo.pop("Compra aprovada", None)
        item_novo.pop("Compra aprovada em", None)
        item_novo["compra_aprovada"] = True
        aprovado_em = str(
            item_atual.get("compra_aprovada_em")
            or item_atual.get("Compra aprovada em")
            or ""
        ).strip()
        if aprovado_em:
            item_novo["compra_aprovada_em"] = aprovado_em

    return novos


async def api_medias_compras_lista_pedido_editar(
    lista_id: str,
    req: ListaPedidoUpdateRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_editar_sync, lista_id=lista_id, req=req, client_id=client_id)


@_retry_listas_pedidos
def _api_medias_compras_lista_pedido_editar_sync(
    lista_id: str,
    req: ListaPedidoUpdateRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")
    lista = dict(listas[idx])
    medias_common._exigir_lista_pedido_editavel(lista)
    cubagem_historica_antes = _cubagem_lista_historica(lista)
    campos_informados = (
        getattr(req, "model_fields_set", None)
        or getattr(req, "__fields_set__", set())
        or set()
    )
    alterou = False
    alterou_loja = False
    if "fornecedor_id" in campos_informados:
        lista.update(_resolver_fornecedor_lista(client_id, req.fornecedor_id))
        alterou = True
    elif lista.get("fornecedor_id") and "supplier" in campos_informados:
        nome_salvo = str(lista.get("supplier") or lista.get("fornecedor") or "").strip()
        if str(req.supplier or "").strip() != nome_salvo:
            raise HTTPException(
                status_code=400,
                detail="Selecione um fornecedor cadastrado para alterar o fornecedor da lista.",
            )
    if "nacionalizador_id" in campos_informados:
        lista.update(_resolver_nacionalizador_lista(client_id, req.nacionalizador_id))
        alterou = True
    elif "nacionalizador" in campos_informados:
        nome_salvo = str(lista.get("nacionalizador") or "").strip()
        if str(req.nacionalizador or "").strip() != nome_salvo:
            raise HTTPException(
                status_code=400,
                detail="Selecione um nacionalizador cadastrado para alterar o nacionalizador da lista.",
            )
    if req.nome_lista is not None:
        nome_lista = str(req.nome_lista or "").strip()
        if nome_lista:
            lista["nome_lista"] = nome_lista
            alterou = True
    if req.status is not None:
        lista["status"] = _normalizar_status_lista_pedido(req.status)
        alterou = True
    if "loja" in campos_informados or "store_id" in campos_informados:
        escopo = _resolver_escopo_loja_medias(
            client_id,
            req.loja if "loja" in campos_informados else lista.get("loja"),
            req.store_id if "store_id" in campos_informados else "",
        )
        lista["loja"] = escopo["loja"]
        lista["store_id"] = escopo["store_id"]
        alterou = True
        alterou_loja = True

    campos_logisticos = (
        "numero_invoice",
        "supplier", "currency", "incoterm", "exchange_rate", "lead_time_days",
        "moq_default", "package_multiple_default", "order_date", "promised_ship_date",
        "actual_ship_date", "eta_date", "customs_clearance_date", "received_at",
        "promised_delivery_date",
    )
    for campo in campos_logisticos:
        if campo not in campos_informados:
            continue
        if campo == "supplier" and lista.get("fornecedor_id"):
            continue
        valor = getattr(req, campo, None)
        if campo in {"exchange_rate", "lead_time_days", "moq_default", "package_multiple_default"}:
            valor = max(0.0, float(valor or 0))
            if campo == "package_multiple_default" and valor <= 0:
                valor = 1.0
        elif valor is not None:
            valor = str(valor).strip()
        lista[campo] = valor
        if campo == "numero_invoice":
            lista.pop("invoice", None)
        alterou = True

    if "itens" in campos_informados:
        itens_protegidos = _proteger_itens_aprovados_lista(lista.get("itens") or [], req.itens or [])
        itens_norm = _recalcular_frete_internacional_itens_lista(
            client_id,
            itens_protegidos,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
            preservar_cubagem=cubagem_historica_antes and not alterou_loja,
        )
        lista["itens"] = itens_norm
        alterou = True
    elif alterou_loja:
        # A identidade da loja e parte da identidade da Foto. A troca de loja
        # precisa limpar/reidratar as referencias no mesmo commit, mesmo quando
        # o PATCH nao reenviar a colecao de itens.
        lista["itens"] = _recalcular_frete_internacional_itens_lista(
            client_id,
            lista.get("itens") or [],
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
            preservar_cubagem=cubagem_historica_antes and not alterou_loja,
        )

    if alterou:
        lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    if alterou:
        _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista, client_id=client_id),
            "itens": lista.get("itens") or [],
        }
    }


async def api_medias_compras_lista_pedido_atualizar_aprovacao_sku(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAprovacaoRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_atualizar_aprovacao_sku_sync, lista_id=lista_id, sku=sku, req=req, client_id=client_id)


@_retry_listas_pedidos
def _api_medias_compras_lista_pedido_atualizar_aprovacao_sku_sync(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAprovacaoRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx_lista = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx_lista < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    lista = listas[idx_lista]
    medias_common._exigir_lista_pedido_editavel(lista)
    sku_ref = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_ref:
        raise HTTPException(status_code=400, detail="SKU invalido para aprovacao")
    itens = [dict(item) for item in (lista.get("itens") or []) if isinstance(item, dict)]
    idx_item = next((i for i, item in enumerate(itens) if _sku_item_lista_pedido(item) == sku_ref), -1)
    if idx_item < 0:
        raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")

    item = itens[idx_item]
    ja_aprovada = _item_lista_pedido_compra_aprovada(item)
    item.pop("Compra aprovada", None)
    item.pop("Compra aprovada em", None)
    item["compra_aprovada"] = bool(req.aprovada)
    if req.aprovada:
        aprovado_em = str(item.get("compra_aprovada_em") or "").strip() if ja_aprovada else ""
        item["compra_aprovada_em"] = aprovado_em or datetime.now().isoformat(timespec="seconds")
    else:
        item.pop("compra_aprovada_em", None)

    itens[idx_item] = item
    lista["itens"] = itens
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx_lista] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "sku": str(item.get("SKU") or sku),
        "compra_aprovada": bool(req.aprovada),
        "item": item,
        "lista": {
            **_resumo_lista_pedido(lista, client_id=client_id),
            "itens": itens,
        },
    }


async def api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAnaliseConcorrentesRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_financial, run_import_financial

    executar = run_import_financial if req.revisao_fontes_custo is not None else run_financial
    return await executar(_api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync, lista_id=lista_id, sku=sku, req=req, client_id=client_id)


async def api_medias_compras_lista_pedido_previa_analise_concorrentes_sku(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAnaliseConcorrentesRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_financial, run_import_financial

    if (req.fonte_custo != "simulacao_importacao_sku"
            or req.precos_concorrentes or not req.precos_anuncios_loja):
        raise HTTPException(400, "A prévia exige apenas os preços dos anúncios próprios do SKU.")
    executar = run_import_financial if req.revisao_fontes_custo is not None else run_financial
    return await executar(
        _api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync,
        lista_id=lista_id, sku=sku, req=req, client_id=client_id, somente_previa=True,
    )


def _api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku_sync(
    lista_id: str, sku: str, req: ListaPedidoSkuAnaliseConcorrentesRequest,
    client_id: str = Depends(medias_common.get_tenant_id), *, somente_previa: bool = False,
):
    contexto = (contexto_financeiro(client_id, medias_common.get_tenant_path(client_id))
                if req.revisao_fontes_custo is not None else nullcontext())
    with contexto:
        return _calcular_analise_concorrentes_sku_sync(lista_id, sku, req, client_id, somente_previa=somente_previa)


@_retry_listas_pedidos
def _calcular_analise_concorrentes_sku_sync(
    lista_id: str,
    sku: str,
    req: ListaPedidoSkuAnaliseConcorrentesRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
    *, somente_previa: bool = False,
):
    listas = _carregar_listas_pedidos(client_id)
    idx_lista = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx_lista < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    lista = listas[idx_lista]
    medias_common._exigir_lista_pedido_editavel(lista)
    sku_ref = _normalizar_sku_mes(str(sku or "").strip())
    if not sku_ref:
        raise HTTPException(status_code=400, detail="SKU invalido para analise de concorrentes")

    itens = [dict(item) for item in (lista.get("itens") or []) if isinstance(item, dict)]
    idx_item = next((i for i, item in enumerate(itens) if _sku_item_lista_pedido(item) == sku_ref), -1)
    if idx_item < 0:
        raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")
    item = itens[idx_item]

    custo_unitario_informado = req.custo_unitario
    if custo_unitario_informado is not None:
        custo_unitario_informado = float(custo_unitario_informado)
        if not math.isfinite(custo_unitario_informado) or custo_unitario_informado < 0:
            raise HTTPException(status_code=400, detail="Custo unitario invalido para analise de concorrentes")
    simulacao = req.fonte_custo == "simulacao_importacao_sku"
    if (req.revisao_fontes_custo is None) != (req.assinatura_calculo is None):
        raise HTTPException(400, "Informe a revisão das fontes e a assinatura do cálculo juntas.")
    if req.revisao_fontes_custo is not None:
        if not simulacao:
            raise HTTPException(400, "A revisão do custo pertence à simulação da importação.")
        from backend.services.importacoes_base_custos import exigir_revisao_custo

        exigir_revisao_custo(client_id, lista_id, sku, req.revisao_fontes_custo, lista=lista)
    analise_variacoes = req.variacoes_anuncios_loja is not None
    store_id_alvo = None
    store_id_anuncios_loja = None
    campo_analise = "analise_simulacao_importacao_sku" if simulacao else "analise_concorrentes"
    if simulacao:
        if custo_unitario_informado is None:
            raise HTTPException(status_code=400, detail="Aguarde o custo unitário simulado deste SKU antes de analisar as margens.")
        store_id_alvo = str(lista.get("store_id") or "").strip()
        if not store_id_alvo:
            escopo = medias_common._resolver_escopo_loja_medias(
                client_id, lista.get("loja"), exigir_especifica=True,
            )
            store_id_alvo = str(escopo.get("store_id") or "").strip()
        if not store_id_alvo:
            raise HTTPException(status_code=409, detail="Defina uma loja específica nesta lista para analisar a simulação do SKU.")
    anuncios_verificados = None
    if analise_variacoes:
        from backend.services.importacoes_sku_anuncios import buscar_anuncios_sku_da_lista
        from backend.services.medias_compras_leitura import chave_arquivo

        store_id_anuncios_loja = store_id_alvo or str(lista.get("store_id") or "").strip()
        if not store_id_anuncios_loja:
            escopo = medias_common._resolver_escopo_loja_medias(
                client_id, lista.get("loja"), exigir_especifica=True,
            )
            store_id_anuncios_loja = str(escopo.get("store_id") or "").strip()

    precos_recebidos = dict(req.precos_concorrentes or {})
    precos_loja_recebidos = dict(req.precos_anuncios_loja or {})
    anuncios_recebidos = dict(req.anuncios_loja or {})
    variacoes_recebidas = dict(req.variacoes_anuncios_loja or {})
    chaves_invalidas = sorted(set(precos_recebidos) - _CHAVES_CONCORRENTES)
    chaves_proprias = set(precos_loja_recebidos) | set(anuncios_recebidos) | set(variacoes_recebidas)
    chaves_invalidas.extend(chave for chave in chaves_proprias
                            if not re.fullmatch(r"concorrente_[1-9]\d*", chave)
                            or (not analise_variacoes and chave not in _CHAVES_CONCORRENTES))
    if chaves_invalidas:
        raise HTTPException(status_code=400, detail="Concorrente invalido para analise")

    precos_normalizados: dict[str, float] = {}
    for chave in sorted(precos_recebidos):
        preco_venda = float(precos_recebidos[chave])
        if not math.isfinite(preco_venda) or preco_venda <= 0:
            raise HTTPException(status_code=400, detail="Preco de concorrente invalido para analise")
        precos_normalizados[chave] = preco_venda

    precos_loja_normalizados: dict[str, float] = {}
    for chave in sorted(precos_loja_recebidos):
        preco_venda = float(precos_loja_recebidos[chave])
        if not math.isfinite(preco_venda) or preco_venda <= 0:
            raise HTTPException(status_code=400, detail="Preco de anuncio da loja invalido para analise")
        precos_loja_normalizados[chave] = preco_venda

    anuncios_normalizados: dict[str, str] = {}
    for chave, item_id_raw in anuncios_recebidos.items():
        item_id_texto = str(item_id_raw or "").strip()
        if not item_id_texto:
            continue
        item_id = _normalizar_item_id_mlb(item_id_texto)
        if not item_id:
            raise HTTPException(status_code=400, detail="Anuncio da loja invalido para analise")
        anuncios_normalizados[chave] = item_id
    variacoes_normalizadas: dict[str, str | None] = {}
    if analise_variacoes:
        for chave, valor in variacoes_recebidas.items():
            if valor is not None and not re.fullmatch(r"[1-9]\d*", str(valor)):
                raise HTTPException(status_code=400, detail="Variação do anúncio da loja inválida para análise")
            variacoes_normalizadas[chave] = str(valor) if valor is not None else None
        if any(chave not in anuncios_normalizados for chave in variacoes_normalizadas):
            raise HTTPException(status_code=400, detail="Informe o anúncio correspondente à variação para análise")
        if any(chave not in anuncios_normalizados for chave in precos_loja_normalizados):
            raise HTTPException(status_code=400, detail="Informe o anúncio da loja para analisar seu preço")
        anuncios_verificados = buscar_anuncios_sku_da_lista(
            client_id, {**lista, "store_id": store_id_anuncios_loja},
            str(item.get("SKU") or sku), cache_scope=chave_arquivo(get_tenant_path(client_id)),
        )
        pares_verificados = {(anuncio["item_id"], anuncio.get("variation_id"))
                             for anuncio in anuncios_verificados.get("anuncios") or []}
        if any(chave not in variacoes_normalizadas
               or (item_id, variacoes_normalizadas[chave]) not in pares_verificados
               for chave, item_id in anuncios_normalizados.items()):
            raise HTTPException(status_code=409, detail="Os anúncios ou suas variações mudaram. Atualize o SKU para analisar novamente.")
        por_chave = {f"concorrente_{numero}": anuncio
                     for numero, anuncio in enumerate(anuncios_verificados.get("anuncios") or [], start=1)}
        for chave in list(precos_loja_normalizados):
            anuncio = por_chave.get(chave) or {}
            if (anuncio.get("item_id"), anuncio.get("variation_id")) != (
                    anuncios_normalizados[chave], variacoes_normalizadas[chave]):
                raise HTTPException(status_code=409, detail="A ordem dos anúncios mudou. Atualize o SKU para analisar novamente.")
            preco_confirmado = _numero_finito_margem(anuncio.get("preco"))
            if preco_confirmado is None or preco_confirmado <= 0:
                # An ad remains visible even when ML cannot prove its variant price.
                precos_loja_normalizados.pop(chave)
            else:
                precos_loja_normalizados[chave] = preco_confirmado

    contexto_consulta = {} if req.precos_anuncios_loja is not None or simulacao else None
    opcoes_simulacao = ({"custo_unitario_simulado": custo_unitario_informado, "store_id_alvo": store_id_alvo}
                        if simulacao else {})

    def calcular_precos(precos: dict[str, float], *, anuncios_proprios: bool = False) -> dict[str, dict[str, Any]]:
        if not precos:
            return {}
        try:
            return _calcular_margens_concorrentes_promocoes(
                client_id,
                str(lista.get("loja") or ""),
                str(item.get("SKU") or sku),
                precos,
                anuncios_normalizados,
                **({"contexto_consulta": contexto_consulta} if contexto_consulta is not None else {}),
                **opcoes_simulacao,
                **({"anuncios_proprios": True} if (simulacao or analise_variacoes) and anuncios_proprios else {}),
                **({"variacoes_anuncios_loja": variacoes_normalizadas,
                    "store_id_anuncios_loja": store_id_anuncios_loja} if analise_variacoes else {}),
            )
        except Exception:
            logger.warning("[IMPORTACOES] Calculo financeiro exato das margens concorrentes indisponivel")
            if req.revisao_fontes_custo is not None:
                # A transient failure is not a new financial analysis. Let the
                # revision-aware caller retry without overwriting saved margins.
                raise HTTPException(
                    status_code=503,
                    detail="A atualização das margens está temporariamente indisponível. Tente novamente.",
                ) from None
            return {
                chave: _margem_concorrente_indisponivel(
                    preco_venda,
                    anuncios_normalizados.get(chave, ""),
                    "Cálculo financeiro temporariamente indisponível.",
                )
                for chave, preco_venda in precos.items()
            }

    if simulacao:
        margens_anuncios_loja = calcular_precos(precos_loja_normalizados, anuncios_proprios=True)
        margens = calcular_precos(precos_normalizados)
    else:
        margens = calcular_precos(precos_normalizados)
        margens_anuncios_loja = calcular_precos(precos_loja_normalizados, anuncios_proprios=True)
    custos_cadastro = {
        float(dados["custo_unitario"])
        for dados in [*margens.values(), *margens_anuncios_loja.values()]
        if _numero_finito_margem(dados.get("custo_unitario")) is not None
    }
    custo_unitario_cadastro = next(iter(custos_cadastro)) if len(custos_cadastro) == 1 else None

    atualizado_em = datetime.now().isoformat(timespec="seconds")
    analise_concorrentes = {
        "custo_unitario": round(custo_unitario_cadastro, 2) if custo_unitario_cadastro is not None else None,
        "fonte_custo": req.fonte_custo,
        "metodo": "analise_promocoes_ml_liquida",
        "fontes_versao": req.fontes_versao,
        "margens": margens,
        "atualizado_em": atualizado_em,
    }
    if simulacao:
        analise_concorrentes["custo_unitario"] = round(custo_unitario_informado, 2)
        analise_concorrentes["store_id"] = store_id_alvo
        if req.revisao_fontes_custo is not None:
            analise_concorrentes["revisao_fontes_custo"] = req.revisao_fontes_custo
            analise_concorrentes["assinatura_calculo"] = req.assinatura_calculo
    if analise_variacoes:
        analise_concorrentes["anuncios_versao"] = 2
        analise_concorrentes["store_id"] = store_id_anuncios_loja
        analise_concorrentes["anuncios_loja_store_id"] = anuncios_verificados["store_id"]
        analise_concorrentes["anuncios_loja_seller_id"] = anuncios_verificados["seller_id"]
        analise_concorrentes["anuncios_loja_site_id"] = anuncios_verificados["site_id"]
        analise_concorrentes["anuncios_consultado_em_ms"] = anuncios_verificados.get("consultado_em_ms")
        analise_concorrentes["anuncios_loja"] = [
            {**copy.deepcopy(anuncio), "chave": f"concorrente_{numero}"}
            for numero, anuncio in enumerate(anuncios_verificados.get("anuncios") or [], start=1)
        ]

    # Only the revisioned SKU commit is serialized; provider waits happen above.
    # Legacy callers retain their whole-list optimistic concurrency contract.
    mutex = (path_lock_for(medias_common._arquivo_listas_pedidos(client_id))
             if req.revisao_fontes_custo is not None else nullcontext())
    with mutex:
        # O cálculo consulta serviços externos e pode levar alguns segundos. Recarregue
        # a lista antes de persistir para não desfazer uma aprovação, exclusão ou edição
        # que tenha ocorrido enquanto a margem era calculada.
        listas_atuais = _carregar_listas_pedidos(client_id)
        if req.revisao_fontes_custo is None:
            _exigir_lista_pedido_snapshot_atual(listas, listas_atuais, lista_id)
        idx_lista_atual = next(
            (i for i, atual in enumerate(listas_atuais) if str(atual.get("id", "")) == str(lista_id)),
            -1,
        )
        if idx_lista_atual < 0:
            raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

        lista_atual = listas_atuais[idx_lista_atual]
        medias_common._exigir_lista_pedido_editavel(lista_atual)
        if any(
            str(lista_atual.get(campo) or "").strip() != str(lista.get(campo) or "").strip()
            for campo in ("loja", "store_id")
        ):
            raise HTTPException(
                status_code=409,
                detail="A loja da lista mudou durante a análise. Recarregue para calcular novamente.",
            )

        itens_atuais = [dict(atual) for atual in (lista_atual.get("itens") or []) if isinstance(atual, dict)]
        idx_item_atual = next(
            (i for i, atual in enumerate(itens_atuais) if _sku_item_lista_pedido(atual) == sku_ref),
            -1,
        )
        if idx_item_atual < 0:
            raise HTTPException(status_code=404, detail="SKU nao encontrado na lista de pedidos")

        item_atual = itens_atuais[idx_item_atual]
        if req.revisao_fontes_custo is not None:
            from backend.services.importacoes_financeiro import escopo_financeiro
            from backend.services.path_coordination import canonical_path_key

            if escopo_financeiro() != (str(client_id), canonical_path_key(medias_common.get_tenant_path(client_id))):
                raise HTTPException(409, "A origem dos dados mudou durante a análise.")
            exigir_revisao_custo(client_id, lista_id, sku, req.revisao_fontes_custo, lista=lista_atual)
            if item_atual.get(campo_analise) != item.get(campo_analise):
                # Another SKU may commit freely. The same SKU cannot overwrite a
                # newer analysis computed from a different local cost or quote.
                raise HTTPException(409, "A análise deste SKU mudou. Atualize antes de salvar novamente.")
        if somente_previa:
            analise_concorrentes["margens_anuncios_loja"] = margens_anuncios_loja
            return {
                "success": True,
                "sku": str(item_atual.get("SKU") or sku),
                "analise_concorrentes": analise_concorrentes,
            }
        if req.precos_anuncios_loja is not None:
            analise_concorrentes["margens_anuncios_loja"] = margens_anuncios_loja
        else:
            analise_atual = item_atual.get(campo_analise)
            if isinstance(analise_atual, dict) and "margens_anuncios_loja" in analise_atual:
                if not simulacao and not analise_variacoes:
                    analise_concorrentes["margens_anuncios_loja"] = copy.deepcopy(analise_atual["margens_anuncios_loja"])
                elif (analise_atual.get("fonte_custo") == req.fonte_custo
                      and _numero_finito_margem(analise_atual.get("custo_unitario"))
                      == (custo_unitario_informado if simulacao else custo_unitario_cadastro)
                      and analise_atual.get("store_id") == (store_id_alvo if simulacao else store_id_anuncios_loja)
                      and (req.revisao_fontes_custo is None or (
                          analise_atual.get("revisao_fontes_custo") == req.revisao_fontes_custo
                          and analise_atual.get("assinatura_calculo") == req.assinatura_calculo
                      ))):
                    analise_concorrentes["margens_anuncios_loja"] = {
                        chave: copy.deepcopy(dados)
                        for chave, dados in (analise_atual["margens_anuncios_loja"] or {}).items()
                        if isinstance(dados, dict)
                        and dados.get("store_id") == (store_id_alvo if simulacao else store_id_anuncios_loja)
                        and dados.get("item_id_loja") == anuncios_normalizados.get(chave)
                        and (not analise_variacoes or dados.get("variation_id") == variacoes_normalizadas.get(chave))
                        and (not analise_variacoes or _numero_finito_margem(dados.get("preco_venda"))
                             == _numero_finito_margem((por_chave.get(chave) or {}).get("preco")))
                    }
            if simulacao or analise_variacoes:
                analise_concorrentes.setdefault("margens_anuncios_loja", {})
        item_atual[campo_analise] = analise_concorrentes
        itens_atuais[idx_item_atual] = item_atual
        lista_atual["itens"] = itens_atuais
        lista_atual["updated_at"] = atualizado_em
        listas_atuais[idx_lista_atual] = lista_atual
        _salvar_listas_pedidos(client_id, listas_atuais)
    _limpar_cache_lista_pedido(
        client_id,
        str(lista_atual.get("id", "") or ""),
        manter_versao=lista_atual.get("updated_at"),
    )

    return {
        "success": True,
        "sku": str(item_atual.get("SKU") or sku),
        "analise_concorrentes": analise_concorrentes,
        "item": item_atual,
        "lista": {
            **_resumo_lista_pedido(lista_atual, client_id=client_id),
            "itens": itens_atuais,
        },
    }


async def api_medias_compras_lista_pedido_adicionar_sku(
    lista_id: str,
    req: ListaPedidoAddSkuRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_adicionar_sku_sync, lista_id=lista_id, req=req, client_id=client_id)


@_retry_listas_pedidos
def _api_medias_compras_lista_pedido_adicionar_sku_sync(
    lista_id: str,
    req: ListaPedidoAddSkuRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.cadastro_produtos import _obter_produto_cadastro_sync

    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")
    lista = listas[idx]

    medias_common._exigir_lista_pedido_editavel(lista)
    sku_input = str(req.sku or "").strip()
    from backend.services.cadastro_cubagem import normalizar_sku_cubagem
    sku_in = normalizar_sku_cubagem(sku_input)
    if not sku_in:
        raise HTTPException(status_code=400, detail="SKU invalido")

    qtd = max(0, int(round(float(req.quantidade or 0))))
    if qtd <= 0:
        raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

    vu = round(float(req.valor_unitario or 0), 2)
    if vu <= 0:
        raise HTTPException(status_code=400, detail="Valor unitÃƒÂ¡rio deve ser maior que zero")

    # Each transaction attempt owns fresh read inputs; release them before commit.
    with leitura_medias():
        from backend.services.cadastro_compatibilidade import (
            mesclar_produtos_legados_com_contexto_loja,
        )

        referencia_loja = str(lista.get("store_id") or lista.get("loja") or "")
        skus_contexto = [
            _primeiro_texto_item(item, ("SKU", "sku"))
            for item in lista.get("itens") or [] if isinstance(item, dict)
        ] + [sku_in]
        contexto_cadastro = mesclar_produtos_legados_com_contexto_loja(
            client_id,
            [],
            referencia_loja,
            skus=skus_contexto,
        )
        produto = next(
            (
                dict(item)
                for item in contexto_cadastro.get("produtos") or []
                if normalizar_sku_cubagem(item.get("cubagem_sku") or item.get("sku")) == sku_in
            ),
            None,
        )
        # Resolve store membership before consulting legacy global rows. A
        # scoped legacy shadow need not exist in the global product lookup.
        # Missing products never require listing or repairing the full catalog.
        if produto is None and contexto_cadastro.get("scope") == "global_safe":
            prod_resp = _obter_produto_cadastro_sync(sku=sku_in, client_id=client_id)
            produto_legado = (prod_resp or {}).get("produto") or {}
            if (
                isinstance(produto_legado, dict)
                and normalizar_sku_cubagem(
                    produto_legado.get("cubagem_sku") or produto_legado.get("sku")
                ) == sku_in
            ):
                produto = dict(produto_legado)
        if not produto:
            raise HTTPException(status_code=404, detail="SKU nÃ£o encontrado no cadastro desta loja")
        store_id_cadastro = str(contexto_cadastro.get("store_id") or "").strip()

        def _norm_prod_key(chave: str) -> str:
            txt = unicodedata.normalize("NFKD", str(chave or ""))
            txt = "".join(ch for ch in txt if not unicodedata.combining(ch))
            txt = txt.lower().replace("_", " ").strip()
            txt = re.sub(r"\s+", " ", txt)
            txt = re.sub(r"[^a-z0-9 ]", "", txt)
            txt = txt.strip()
            if txt.startswith("cg "):
                txt = txt[3:].strip()
            return txt

        def _pick_prod(cands: list[str]) -> str:
            if not isinstance(produto, dict):
                return ""

            mapa = {}
            for k, v in produto.items():
                if v is None or not str(v).strip():
                    continue
                nk = _norm_prod_key(str(k or ""))
                if nk and nk not in mapa:
                    mapa[nk] = str(v).strip()

            for c in cands:
                key = _norm_prod_key(str(c or ""))
                if not key:
                    continue
                val = mapa.get(key)
                if val:
                    return val

                # Fallback por inclusÃƒÂ£o para colunas longas/variantes importadas de planilha.
                for mk, mv in mapa.items():
                    if mk == key or key in mk or mk in key:
                        if mv:
                            return mv
            return ""

        sku_final = normalizar_sku_cubagem(produto.get("cubagem_sku") or _pick_prod(["sku"]) or sku_in)
        titulo = _pick_prod([
            "produto bling", "produtos bling", "produto blig", "produtos blig",
            "titulo do produto em ingles", "titulo do produto em ingles",
            "titulo em ingles", "titulo", "product name", "nome", "produto",
            "traducao ptbr ou nome na bling"
        ])
        foto = fotos_da_consulta(_resolver_foto_cadastro_sku)(
            client_id,
            sku_final,
            _pick_prod(["foto", "imagem", "url foto", "link foto"]),
            store_id_cadastro or None,
        )
        oem = _pick_prod(["oem", "codigo oem", "part number", "oem model", "oem model"])
        cor_lado = _pick_prod(["color side", "color/side", "cor lado", "cor/lado", "lado cor", "lado/cor", "lado", "cor", "color", "side"])
        link = _pick_prod(["link", "url", "link aliexpress", "url aliexpress", "mlb principal", "url ml"])

        itens = [_normalizar_item_lista_pedido(i) for i in (lista.get("itens") or [])]
        idx_existente = next((i for i, it in enumerate(itens) if normalizar_sku_cubagem(it.get("SKU", "")) == sku_final), -1)

        item_novo = {
            "SKU": sku_final,
            "Foto": foto,
            TITULO_PRODUTO_INGLES_KEY: titulo,
            "OEM": oem,
            COR_LADO_LISTA_PEDIDO_KEY: cor_lado,
            "Link": link,
            "Quantidade": qtd,
            "Valor unidade": vu,
            "Valor total": round(qtd * vu, 2),
        }

        acao = "adicionado"
        if idx_existente >= 0:
            itens[idx_existente] = _normalizar_item_lista_pedido({**itens[idx_existente], **item_novo})
            acao = "atualizado"
        else:
            itens.append(_normalizar_item_lista_pedido(item_novo))

        # The store projection covers every list SKU, including public aliases.
        # Global compatibility also merges the legacy lookup row; that row was
        # never part of the fiscal global projection, so retain its original path.
        opcoes_contexto = {}
        if contexto_cadastro.get("scope") == "store":
            opcoes_contexto["contexto_cadastro"] = _ContextoCadastroLista(
                client_id, referencia_loja, contexto_cadastro,
            )
        lista["itens"] = _recalcular_frete_internacional_itens_lista(
            client_id,
            itens,
            loja=referencia_loja,
            preservar_cubagem=_cubagem_lista_historica(lista),
            **opcoes_contexto,
        )
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "acao": acao,
        "sku": sku_final,
        "lista": {
            **_resumo_lista_pedido(lista, client_id=client_id),
            "itens": lista.get("itens") or [],
        },
    }


async def api_medias_compras_lista_pedido_atualizar_status(
    lista_id: str,
    req: ListaPedidoStatusRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_atualizar_status_sync, lista_id=lista_id, req=req, client_id=client_id)


@_retry_listas_pedidos
def _api_medias_compras_lista_pedido_atualizar_status_sync(
    lista_id: str,
    req: ListaPedidoStatusRequest,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    medias_common._exigir_lista_pedido_editavel(lista)
    lista["status"] = _normalizar_status_lista_pedido(req.status)
    lista["updated_at"] = datetime.now().isoformat(timespec="seconds")
    listas[idx] = lista
    _salvar_listas_pedidos(client_id, listas)
    _limpar_cache_lista_pedido(client_id, str(lista.get("id", "") or ""), manter_versao=lista.get("updated_at"))

    return {
        "success": True,
        "lista": {
            **_resumo_lista_pedido(lista, client_id=client_id),
            "itens": lista.get("itens") or [],
        }
    }

def _lista_pedido_esta_em_importacoes(lista: dict[str, Any] | None) -> bool:
    status = unicodedata.normalize("NFD", str((lista or {}).get("status", "") or ""))
    status = "".join(ch for ch in status if unicodedata.category(ch) != "Mn")
    return status.strip().lower() == "analisando orcamento"


async def api_medias_compras_lista_pedido_excluir(
    lista_id: str,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_excluir_sync, lista_id=lista_id, request=request, client_id=client_id)


@_retry_listas_pedidos
def _api_medias_compras_lista_pedido_excluir_sync(
    lista_id: str,
    request: Request,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    idx = next((i for i, l in enumerate(listas) if str(l.get("id", "")) == str(lista_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = listas[idx]
    medias_common._exigir_lista_pedido_editavel(lista)
    origem_delete = str(request.headers.get("x-jk-delete-origin", "") or "").strip().lower()
    if _lista_pedido_esta_em_importacoes(lista) and origem_delete != "importacoes":
        raise HTTPException(
            status_code=409,
            detail="Listas em Importacoes so podem ser excluidas pelo modulo Importacoes.",
        )

    removida = listas.pop(idx)
    _salvar_listas_pedidos(client_id, listas)
    from backend.services.medias_compras_packing import limpar_documento_packing_lista
    limpar_documento_packing_lista(client_id, removida)
    _limpar_cache_lista_pedido(client_id, str(removida.get("id", "") or ""))
    return {
        "success": True,
        "lista_removida": {
            "id": str(removida.get("id", "") or ""),
            "nome_lista": str(removida.get("nome_lista", "") or ""),
        }
    }


def _lista_pedido_com_links_atuais(lista: dict, client_id: str) -> dict:
    """Refresh only export links from the current, exact store's Cadastro.

    Work on copies so downloading never rewrites the saved order or its
    quantities, prices and approvals. The refreshed links also enter the
    existing content-based XLSX cache key.
    """
    if lista.get("unida_em_lista_id"):
        return lista
    itens = lista.get("itens") or []
    referencia = str(lista.get("store_id") or lista.get("loja") or "").strip()
    if not client_id or not itens or not _loja_lista_definida(referencia):
        return lista

    def chave_sku(valor):
        return _normalizar_sku_mes(str(valor or "").strip()).upper()

    def chave_item(item):
        return chave_sku(medias_common._primeiro_texto_item(item, ("SKU", "sku")))

    skus = tuple(sorted({chave_item(item) for item in itens
                         if isinstance(item, dict) and chave_item(item)}))
    if not skus:
        return lista
    from backend.services.cadastro_compatibilidade import visao_produtos_cadastro_contexto_loja

    try:
        contexto = visao_produtos_cadastro_contexto_loja(client_id, referencia, skus=skus)
    except (RuntimeError, OSError, HTTPException):
        return lista
    store_id = str(contexto.get("store_id") or "").strip()
    esperado = str(lista.get("store_id") or "").strip()
    if (contexto.get("scope") != "store" or not contexto.get("loja_resolvida")
            or not store_id or (esperado and store_id != esperado)):
        return lista

    links = {}
    for produto in contexto.get("produtos") or []:
        if (not isinstance(produto, dict)
                or str(produto.get("store_id") or "").strip() != store_id
                or produto.get("deleted_at_utc")):
            continue
        # The canonical field takes precedence over historical link aliases.
        link = _pick_cadastro_link({"link": produto.get("link")}) or _pick_cadastro_link(produto)
        if link:
            links[chave_sku(produto.get("sku") or produto.get("sku_normalizado"))] = link

    exportacao = dict(lista)
    exportacao["itens"] = []
    for item in itens:
        if isinstance(item, dict):
            item = dict(item)
            link = links.get(chave_item(item))
            if link:
                # The renderer indexes normalized names in insertion order.
                # A legacy "link"/"LINK" must not precede the refreshed field.
                item = {"Link": link, **{key: value for key, value in item.items() if key != "Link"}}
        exportacao["itens"].append(item)
    return exportacao


def _versao_cache_lista_pedido(lista: dict) -> str:
    """Distinguish list edits and refreshed links, even within the same second."""
    import hashlib

    payload = json.dumps(lista, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    version = str(lista.get("updated_at") or lista.get("created_at") or "")
    return f"{version}_{digest}"


async def api_medias_compras_lista_pedido_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_download_sync, lista_id=lista_id, client_id=client_id)


def _api_medias_compras_lista_pedido_download_sync(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = _lista_pedido_com_links_atuais(lista, client_id)
    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = _versao_cache_lista_pedido(lista)
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)
    headers = {
        "Content-Disposition": f"attachment; filename={nome_arquivo}",
        "Cache-Control": "no-store",
    }

    # Capture complete bytes in the worker: a later list edit may remove the
    # cache before FileResponse would open it in the response phase.
    try:
        with open(caminho_cache, "rb") as cached:
            cached_bytes = cached.read()
    except FileNotFoundError:
        cached_bytes = None
    if cached_bytes:
        return StreamingResponse(
            io.BytesIO(cached_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers,
        )

    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)
    if not file_bytes:
        file_bytes = _gerar_excel_lista_pedido_bytes(
            nome_lista,
            lista.get("itens") or [],
            client_id=client_id,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        # MantÃ©m cache pequeno para evitar crescimento sem controle.
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break

    _salvar_bytes_cache_lista_pedido(client_id, str(lista_id), versao_lista, file_bytes)

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


def _data_aprovacao_commercial_invoice(lista: dict[str, Any]) -> str:
    itens = [item for item in (lista.get("itens") or []) if isinstance(item, dict)]
    datas = [
        str(item.get("compra_aprovada_em") or item.get("Compra aprovada em") or "").strip()
        for item in itens
    ]
    datas = [data for data in datas if data]
    return max(datas, default="")


async def api_medias_compras_lista_pedido_commercial_invoice(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_commercial_invoice_sync, lista_id=lista_id, client_id=client_id)


def _api_medias_compras_lista_pedido_commercial_invoice_sync(
    lista_id: str,
    client_id: str = Depends(medias_common.get_tenant_id),
):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((item for item in listas if str(item.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nao encontrada")

    data_aprovacao = _data_aprovacao_commercial_invoice(lista)
    file_bytes = _gerar_commercial_invoice_bytes(
        lista,
        client_id=client_id,
        data_aprovacao=data_aprovacao,
    )
    nome_base = (
        str(lista.get("numero_invoice") or lista.get("invoice") or "").strip()
        or str(lista.get("nome_lista") or "").strip()
        or str(lista_id).strip()
    )
    nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_base).strip("_")
    if not nome_seguro:
        nome_seguro = re.sub(r"[^A-Za-z0-9_-]+", "_", str(lista_id)).strip("_") or "lista"
    nome_arquivo = f"commercial_invoice_{nome_seguro}.xlsx"
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nome_arquivo}"',
            "Cache-Control": "no-store",
        },
    )


async def api_medias_compras_lista_pedido_gerar_download(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_lista_pedido_gerar_download_sync, lista_id=lista_id, client_id=client_id)


def _api_medias_compras_lista_pedido_gerar_download_sync(lista_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    listas = _carregar_listas_pedidos(client_id)
    lista = next((l for l in listas if str(l.get("id", "")) == str(lista_id)), None)
    if not lista:
        raise HTTPException(status_code=404, detail="Lista de pedidos nÃ£o encontrada")

    lista = _lista_pedido_com_links_atuais(lista, client_id)
    nome_lista = str(lista.get("nome_lista", "lista_pedido") or "lista_pedido").strip()
    nome_base = re.sub(r"[^A-Za-z0-9_-]+", "_", nome_lista).strip("_") or "lista_pedido"
    nome_arquivo = f"{nome_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    versao_lista = _versao_cache_lista_pedido(lista)
    cache_key = f"{client_id}:{lista_id}:{versao_lista}"
    caminho_cache = _arquivo_cache_lista_pedido(client_id, str(lista_id), versao_lista)

    if os.path.exists(caminho_cache):
        return {
            "success": True,
            "download_url": f"/api/medias-compras/listas-pedidos/{lista_id}/download",
            "filename": nome_arquivo,
            "lista_id": str(lista.get("id", "") or ""),
        }

    file_bytes = None
    file_bytes = LISTA_PEDIDO_XLSX_CACHE.get(cache_key)

    if file_bytes is None:
        file_bytes = _gerar_excel_lista_pedido_bytes(
            nome_lista,
            lista.get("itens") or [],
            client_id=client_id,
            loja=str(lista.get("store_id") or lista.get("loja") or ""),
        )
        LISTA_PEDIDO_XLSX_CACHE[cache_key] = file_bytes
        while len(LISTA_PEDIDO_XLSX_CACHE) > LISTA_PEDIDO_XLSX_CACHE_MAX_ITENS:
            try:
                LISTA_PEDIDO_XLSX_CACHE.pop(next(iter(LISTA_PEDIDO_XLSX_CACHE)))
            except Exception:
                break
        _salvar_bytes_cache_lista_pedido(client_id, str(lista_id), versao_lista, file_bytes)

    file_id = str(uuid.uuid4())
    TEMP_FILES_STORAGE[file_id] = file_bytes
    TEMP_FILES_META[file_id] = {
        "client_id": client_id,
        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "filename": nome_arquivo,
    }
    return {
        "success": True,
        "file_id": file_id,
        "filename": nome_arquivo,
        "lista_id": str(lista.get("id", "") or ""),
    }




async def api_medias_compras_download(file_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_api_medias_compras_download_sync, file_id=file_id, client_id=client_id)


def _api_medias_compras_download_sync(file_id: str, client_id: str = Depends(medias_common.get_tenant_id)):
    headers = {"Cache-Control": "no-store"}
    meta = TEMP_FILES_META.get(file_id, {})
    file_bytes = None
    if client_id and meta.get("client_id") == client_id:
        file_bytes = TEMP_FILES_STORAGE.get(file_id)
    if not file_bytes:
        raise HTTPException(status_code=404, detail="Arquivo nÃ£o encontrado ou expirado", headers=headers)

    media_type = meta.get("mime") or "application/octet-stream"
    filename = meta.get("filename") or f"arquivo_{file_id}.xlsx"

    headers["Content-Disposition"] = f"attachment; filename={filename}"
    return StreamingResponse(io.BytesIO(file_bytes), media_type=media_type, headers=headers)



def configure_medias_compras_listas_runtime(runtime_module=None):
    return _configure_runtime_globals(runtime_module)


configure_medias_compras_listas_runtime()

__all__ = [
    "configure_medias_compras_listas_runtime",
    "api_medias_compras_listas_pedidos",
    "api_medias_compras_skus_ocultos_get",
    "api_medias_compras_skus_ocultos_put",
    "api_medias_compras_preferencias_colunas_get",
    "api_medias_compras_preferencias_colunas_put",
    "api_medias_compras_concorrentes_links",
    "api_medias_compras_lista_pedido_concorrentes_links_lote",
    "api_medias_compras_lista_pedido_detalhe",
    "api_medias_compras_lista_pedido_custo_posto",
    "api_medias_compras_lista_pedido_editar",
    "api_medias_compras_lista_pedido_atualizar_analise_concorrentes_sku",
    "api_medias_compras_lista_pedido_previa_analise_concorrentes_sku",
    "api_medias_compras_lista_pedido_atualizar_aprovacao_sku",
    "api_medias_compras_lista_pedido_adicionar_sku",
    "api_medias_compras_lista_pedido_atualizar_status",
    "api_medias_compras_lista_pedido_excluir",
    "api_medias_compras_lista_pedido_download",
    "api_medias_compras_lista_pedido_commercial_invoice",
    "api_medias_compras_lista_pedido_gerar_download",
    "api_medias_compras_download",
]
