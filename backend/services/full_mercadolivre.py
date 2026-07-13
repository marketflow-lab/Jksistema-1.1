"""Mercado Livre helpers for the Full module."""

from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from backend.services.full import _full_numero


logger = logging.getLogger("jk_sistema")
_carregar_lojas: Callable[[str], list] = lambda client_id: []
_ml_oauth_status: Callable[[dict | None], dict] = lambda cfg: {}
_ml_cache_get: Callable[..., object] = lambda key, **kwargs: None
_ml_cache_set: Callable[..., object] = lambda key, value, **kwargs: None
_ml_favoritos_api_request: Callable[..., tuple[object, dict]] | None = None
_ml_parse_error_detail: Callable[[object, str], str] = lambda resp, fallback: fallback
_ml_extrair_variacoes_resumo: Callable[..., list[dict]] = lambda item, **kwargs: []
_ml_extrair_sku: Callable[[dict], str] = lambda item: ""
_obter_cfg_ml: Callable[[str, str], dict] = lambda client_id, loja: {}
_ml_favoritos_listar_todos_itens_ativos_loja: Callable[..., tuple[list[dict], dict]] | None = None


def configure_full_mercadolivre_context(
    *,
    logger_ref=None,
    carregar_lojas_fn: Callable[[str], list] | None = None,
    ml_oauth_status: Callable[[dict | None], dict] | None = None,
    ml_cache_get: Callable[..., object] | None = None,
    ml_cache_set: Callable[..., object] | None = None,
    ml_favoritos_api_request: Callable[..., tuple[object, dict]] | None = None,
    ml_parse_error_detail: Callable[[object, str], str] | None = None,
    ml_extrair_variacoes_resumo: Callable[..., list[dict]] | None = None,
    ml_extrair_sku: Callable[[dict], str] | None = None,
    obter_cfg_ml: Callable[[str, str], dict] | None = None,
    ml_favoritos_listar_todos_itens_ativos_loja: Callable[..., tuple[list[dict], dict]] | None = None,
) -> None:
    global logger, _carregar_lojas, _ml_oauth_status, _ml_cache_get, _ml_cache_set
    global _ml_favoritos_api_request, _ml_parse_error_detail, _ml_extrair_variacoes_resumo
    global _ml_extrair_sku, _obter_cfg_ml, _ml_favoritos_listar_todos_itens_ativos_loja

    if logger_ref is not None:
        logger = logger_ref
    if carregar_lojas_fn is not None:
        _carregar_lojas = carregar_lojas_fn
    if ml_oauth_status is not None:
        _ml_oauth_status = ml_oauth_status
    if ml_cache_get is not None:
        _ml_cache_get = ml_cache_get
    if ml_cache_set is not None:
        _ml_cache_set = ml_cache_set
    if ml_favoritos_api_request is not None:
        _ml_favoritos_api_request = ml_favoritos_api_request
    if ml_parse_error_detail is not None:
        _ml_parse_error_detail = ml_parse_error_detail
    if ml_extrair_variacoes_resumo is not None:
        _ml_extrair_variacoes_resumo = ml_extrair_variacoes_resumo
    if ml_extrair_sku is not None:
        _ml_extrair_sku = ml_extrair_sku
    if obter_cfg_ml is not None:
        _obter_cfg_ml = obter_cfg_ml
    if ml_favoritos_listar_todos_itens_ativos_loja is not None:
        _ml_favoritos_listar_todos_itens_ativos_loja = ml_favoritos_listar_todos_itens_ativos_loja


def listar_lojas_full_mercadolivre_payload(client_id: str) -> dict:
    """Lista lojas com Mercado Livre autenticado em Integracoes."""
    lojas = _carregar_lojas(client_id) or []
    conectadas = []
    for loja in lojas:
        if not isinstance(loja, dict):
            continue
        nome = str(loja.get("nome") or "").strip()
        if not nome:
            continue
        integracoes = loja.get("integracoes") or {}
        cfg_ml = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
        status_ml = _ml_oauth_status(cfg_ml if isinstance(cfg_ml, dict) else {})
        if not status_ml.get("conectado"):
            continue
        conectadas.append({
            "nome": nome,
            "conectado": True,
            "status": status_ml.get("status") or "conectado",
            "user_id": str((cfg_ml or {}).get("user_id") or "").strip(),
        })
    conectadas.sort(key=lambda item: item.get("nome", "").lower())
    return {"success": True, "total": len(conectadas), "lojas": conectadas}


def _full_item_logistic_type(item: dict) -> str:
    shipping = item.get("shipping") if isinstance(item, dict) else {}
    shipping = shipping if isinstance(shipping, dict) else {}
    return str(
        item.get("logistic_type")
        or item.get("shipping_logistic_type")
        or shipping.get("logistic_type")
        or ""
    ).strip()


def _full_item_eh_full(item: dict) -> bool:
    return _full_item_logistic_type(item).lower() == "fulfillment"


def _full_obter_estoque_fulfillment(
    client_id: str,
    loja: str,
    cfg: dict,
    inventory_id: str,
    *,
    force_refresh: bool = False,
) -> tuple[dict, dict]:
    inventory_id = str(inventory_id or "").strip()
    if not inventory_id:
        return {"inventory_id": "", "available_quantity": 0, "source": "sem_inventory_id"}, cfg
    if _ml_favoritos_api_request is None:
        raise RuntimeError("Full Mercado Livre context was not configured.")

    cache_key = f"full:inventory_stock:{client_id}:{loja}:{inventory_id}"
    if not force_refresh:
        cached = _ml_cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached, cfg

    url = f"https://api.mercadolibre.com/inventories/{quote(inventory_id)}/stock/fulfillment"
    resp, cfg_local = _ml_favoritos_api_request(client_id, loja, cfg, "GET", url, timeout=15)
    if resp.status_code != 200:
        erro = _ml_parse_error_detail(resp, "Erro ao consultar estoque Full do Mercado Livre")
        logger.warning("[FULL ML] Falha ao consultar inventory_id=%s loja=%s: %s", inventory_id, loja, erro)
        payload = {
            "inventory_id": inventory_id,
            "available_quantity": 0,
            "not_available_quantity": 0,
            "total_quantity": 0,
            "source": "fulfillment_stock_error",
            "error": erro,
        }
        _ml_cache_set(cache_key, payload)
        return payload, cfg_local

    data = resp.json() or {}
    disponivel = _full_numero(
        data.get("available_quantity")
        if data.get("available_quantity") is not None
        else data.get("saleable_quantity")
    )
    indisponivel = _full_numero(data.get("not_available_quantity"))
    total = _full_numero(data.get("total")) or _full_numero(data.get("total_quantity")) or (disponivel + indisponivel)
    payload = {
        "inventory_id": inventory_id,
        "available_quantity": disponivel,
        "not_available_quantity": indisponivel,
        "total_quantity": total,
        "source": "fulfillment_stock",
        "raw": data,
    }
    _ml_cache_set(cache_key, payload)
    return payload, cfg_local


def _full_normalizar_anuncio_ml(
    client_id: str,
    loja: str,
    cfg: dict,
    item: dict,
    *,
    force_refresh: bool = False,
) -> tuple[dict, dict]:
    cfg_local = dict(cfg or {})
    variacoes = _ml_extrair_variacoes_resumo(item, client_id=client_id, loja=loja)
    skus_variacoes = [
        str(var.get("sku") or "").strip()
        for var in variacoes
        if isinstance(var, dict) and str(var.get("sku") or "").strip() and str(var.get("sku") or "").strip() != "-"
    ]
    sku_item = _ml_extrair_sku(item)
    sku_display = " / ".join(list(dict.fromkeys(skus_variacoes))[:4]) if skus_variacoes else (sku_item or "N/D")
    vendidos_variacoes = sum(_full_numero(var.get("sold_quantity")) for var in variacoes if isinstance(var, dict))
    vendidos = vendidos_variacoes if variacoes else _full_numero(item.get("sold_quantity"))
    shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
    inventory_id = str(item.get("inventory_id") or "").strip()
    estoque_total = 0.0
    estoque_total_geral = 0.0
    indisponivel_total = 0.0
    fontes_estoque = set()

    if variacoes:
        variacoes_full = []
        for var in variacoes:
            var_payload = dict(var or {})
            estoque_info, cfg_local = _full_obter_estoque_fulfillment(
                client_id,
                loja,
                cfg_local,
                str(var_payload.get("inventory_id") or "").strip(),
                force_refresh=force_refresh,
            )
            disponivel = _full_numero(estoque_info.get("available_quantity"))
            indisponivel = _full_numero(estoque_info.get("not_available_quantity"))
            total_var = _full_numero(estoque_info.get("total_quantity")) or (disponivel + indisponivel)
            var_payload["available_quantity"] = disponivel
            var_payload["full_available_quantity"] = disponivel
            var_payload["full_not_available_quantity"] = indisponivel
            var_payload["full_total_quantity"] = total_var
            var_payload["stock_source"] = estoque_info.get("source")
            if estoque_info.get("error"):
                var_payload["stock_error"] = estoque_info.get("error")
            estoque_total += disponivel
            indisponivel_total += indisponivel
            estoque_total_geral += total_var
            fontes_estoque.add(str(estoque_info.get("source") or ""))
            variacoes_full.append(var_payload)
        variacoes = variacoes_full
    else:
        estoque_info, cfg_local = _full_obter_estoque_fulfillment(
            client_id,
            loja,
            cfg_local,
            inventory_id,
            force_refresh=force_refresh,
        )
        estoque_total = _full_numero(estoque_info.get("available_quantity"))
        indisponivel_total = _full_numero(estoque_info.get("not_available_quantity"))
        estoque_total_geral = _full_numero(estoque_info.get("total_quantity")) or (estoque_total + indisponivel_total)
        fontes_estoque.add(str(estoque_info.get("source") or ""))

    anuncio = {
        "id": item.get("id"),
        "loja": loja,
        "title": item.get("title"),
        "thumbnail": item.get("secure_thumbnail") or item.get("thumbnail") or ((item.get("pictures") or [{}])[0].get("secure_url") if (item.get("pictures") or []) else None),
        "sku": sku_item or "N/D",
        "sku_display": sku_display,
        "inventory_id": inventory_id,
        "price": _full_numero(item.get("price")),
        "available_quantity": estoque_total,
        "full_available_quantity": estoque_total,
        "full_not_available_quantity": indisponivel_total,
        "full_total_quantity": estoque_total_geral,
        "stock_source": "fulfillment_stock" if "fulfillment_stock" in fontes_estoque else next(iter(fontes_estoque), ""),
        "sold_quantity": vendidos,
        "status": item.get("status"),
        "listing_type_id": item.get("listing_type_id"),
        "condition": item.get("condition"),
        "logistic_type": _full_item_logistic_type(item),
        "shipping_mode": shipping.get("mode") or item.get("shipping_mode"),
        "permalink": item.get("permalink") or f"https://produto.mercadolivre.com.br/{item.get('id', '')}",
        "has_variations": bool(variacoes),
        "variations": variacoes,
    }
    return anuncio, cfg_local


def listar_anuncios_full_mercadolivre_payload(
    client_id: str,
    loja: str,
    limite: int = 10000,
    *,
    force_refresh: bool = False,
) -> dict:
    """Busca no Mercado Livre todos os anuncios Full da loja selecionada."""
    if _ml_favoritos_listar_todos_itens_ativos_loja is None:
        raise RuntimeError("Full Mercado Livre context was not configured.")
    nome_loja = str(loja or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja para consultar os anuncios Full.")

    limite = max(100, min(int(limite or 10000), 20000))
    cache_key = f"full:v2:anuncios_ml:{client_id}:{nome_loja}:{limite}"
    if not force_refresh:
        cached = _ml_cache_get(cache_key, ttl=600)
        if cached is not None:
            return cached

    cfg = _obter_cfg_ml(client_id, nome_loja)
    itens, _cfg = _ml_favoritos_listar_todos_itens_ativos_loja(client_id, nome_loja, cfg, limite=limite)
    cfg_local = dict(_cfg or cfg or {})
    anuncios = []
    for item in (itens or []):
        if not isinstance(item, dict) or not _full_item_eh_full(item):
            continue
        anuncio, cfg_local = _full_normalizar_anuncio_ml(
            client_id,
            nome_loja,
            cfg_local,
            item,
            force_refresh=force_refresh,
        )
        anuncios.append(anuncio)
    anuncios.sort(key=lambda item: (str(item.get("title") or "").lower(), str(item.get("id") or "")))
    resultado = jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "total": len(anuncios),
        "results": anuncios,
    })
    _ml_cache_set(cache_key, resultado)
    return resultado


__all__ = [
    "configure_full_mercadolivre_context",
    "listar_lojas_full_mercadolivre_payload",
    "listar_anuncios_full_mercadolivre_payload",
    "_full_item_logistic_type",
    "_full_item_eh_full",
]
