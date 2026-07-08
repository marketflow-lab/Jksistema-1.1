"""Mercado Livre promotion listing and count services."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException

from backend.services.mercadolivre_cache import (
    _ML_CACHE_TTL_CAMPANHA,
    _ML_CACHE_TTL_CAMPANHA_STALE,
    _ML_CACHE_TTL_CONTAGENS,
    _ml_cache_get,
    _ml_cache_get_stale,
    _ml_cache_set,
)
from backend.services.mercadolivre_context import _ctx


def _ml_listar_promocoes_ativas_payload(client_id: str, loja: str) -> dict:
    ctx = _ctx()
    logger = ctx.logger
    nome_loja = str(loja or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja do Mercado Livre.")

    cache_key = f"promocoes:{client_id}:{nome_loja}"
    cached = _ml_cache_get(cache_key, _ML_CACHE_TTL_CAMPANHA)
    if isinstance(cached, dict):
        return cached
    stale_cache = _ml_cache_get_stale(cache_key, _ML_CACHE_TTL_CAMPANHA_STALE)

    cfg = ctx.obter_cfg_ml(client_id, nome_loja)
    user_id = str(cfg.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="ID do usuario do Mercado Livre nao encontrado para esta loja.")

    try:
        timeout_promocoes = int(float(str(os.getenv("ML_PROMO_TIMEOUT", "18") or "18").replace(",", ".")))
    except Exception:
        timeout_promocoes = 18
    timeout_promocoes = max(8, min(timeout_promocoes, 45))
    try:
        tentativas_promocoes = int(float(str(os.getenv("ML_PROMO_MAX_ATTEMPTS", "3") or "3").replace(",", ".")))
    except Exception:
        tentativas_promocoes = 3
    tentativas_promocoes = max(1, min(tentativas_promocoes, 6))

    try:
        resp, cfg = ctx.ml_api_request_com_retry(
            client_id,
            nome_loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/seller-promotions/users/{user_id}",
            params={"app_version": "v2"},
            timeout=timeout_promocoes,
            max_attempts=tentativas_promocoes,
        )
    except Exception as exc:
        if isinstance(stale_cache, dict):
            logger.warning(
                "[FAVORITOS PROMO] Usando cache stale de promocoes para %s/%s apos erro na API: %s",
                client_id,
                nome_loja,
                exc,
            )
            return stale_cache
        raise HTTPException(
            status_code=504,
            detail="Mercado Livre demorou para responder ao carregar promocoes desta loja. Tente novamente em instantes.",
        )

    if resp.status_code != 200:
        if isinstance(stale_cache, dict) and resp.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "[FAVORITOS PROMO] Usando cache stale de promocoes para %s/%s apos HTTP %s",
                client_id,
                nome_loja,
                resp.status_code,
            )
            return stale_cache
        raise HTTPException(status_code=resp.status_code, detail=ctx.ml_parse_error_detail(resp, "Erro ao listar promocoes do Mercado Livre"))

    try:
        payload = resp.json() or {}
    except Exception:
        payload = {}

    if isinstance(payload, list):
        campanhas_raw = payload
    elif isinstance(payload, dict):
        campanhas_raw = payload.get("results") or payload.get("campaigns") or payload.get("promotions") or []
    else:
        campanhas_raw = []

    def _grupo_promocao(campanha: dict) -> str:
        tipo = str(campanha.get("type") or campanha.get("promotion_type") or "").strip().upper()
        nome = ctx.normalizar_texto(str(campanha.get("name") or campanha.get("title") or ""))
        if tipo in {"SELLER_CAMPAIGN", "SELLER_COUPON_CAMPAIGN"}:
            return "usuario"
        if tipo in {"SMART", "PRICE_MATCHING", "PRICE_MATCHING_MELI_ALL", "MARKETPLACE_CAMPAIGN", "PRE_NEGOTIATED"}:
            return "mercado_livre"
        if any(chave in nome for chave in ["aceler", "tarifa", "menos tarifa", "reduzimos", "aumente suas vendas"]):
            return "mercado_livre"
        return "outras"

    campanhas = []
    status_inativos = {"finished", "ended", "closed", "paused", "cancelled", "canceled", "inactive", "expired"}
    for item in campanhas_raw:
        if not isinstance(item, dict):
            continue
        campanha_id = str(item.get("id") or item.get("promotion_id") or "").strip()
        if not campanha_id:
            continue
        status = str(item.get("status") or item.get("state") or "").strip()
        if status.lower() in status_inativos:
            continue
        tipo = str(item.get("type") or item.get("promotion_type") or "").strip()
        active_count = _ml_extrair_contagem_campanha(item, [
            "active_count", "active_items_count", "active_item_count", "started_items_count",
            "participating_items_count", "items_active_count",
        ])
        eligible_count = _ml_extrair_contagem_campanha(item, [
            "eligible_count", "eligible_items_count", "eligible_item_count",
            "candidate_items_count", "candidate_count", "items_count", "item_count",
            "total_items", "products_count", "offers_count",
        ])
        campanhas.append({
            "id": campanha_id,
            "name": str(item.get("name") or item.get("title") or campanha_id).strip(),
            "title": str(item.get("title") or item.get("name") or campanha_id).strip(),
            "status": status,
            "type": tipo,
            "promotion_type": tipo,
            "selection_group": _grupo_promocao(item),
            "start_date": item.get("start_date") or item.get("date_start") or item.get("begin_date"),
            "finish_date": item.get("finish_date") or item.get("end_date") or item.get("date_end"),
            "active_count": active_count,
            "eligible_count": eligible_count,
        })

    payload = {
        "success": True,
        "loja": nome_loja,
        "campaigns": campanhas,
        "total": len(campanhas),
    }
    _ml_cache_set(cache_key, payload)
    return payload


def _ml_extrair_contagem_campanha(campanha: dict, chaves: list[str]):
    if not isinstance(campanha, dict):
        return None

    candidatos = []
    for chave in chaves:
        candidatos.append(campanha.get(chave))
    for grupo in ("summary", "totals", "paging", "items"):
        obj = campanha.get(grupo)
        if isinstance(obj, dict):
            for chave in chaves:
                candidatos.append(obj.get(chave))

    for valor in candidatos:
        try:
            if valor in (None, ""):
                continue
            numero = int(float(str(valor).replace(",", ".")))
            if numero >= 0:
                return numero
        except Exception:
            continue
    return None


def _ml_contar_itens_promocao_status(
    client_id: str,
    loja: str,
    cfg: dict,
    campaign_id: str,
    promotion_type: str = "",
    status_item: str = "",
) -> tuple[Optional[int], dict]:
    ctx = _ctx()
    logger = ctx.logger
    campaign_id = str(campaign_id or "").strip()
    promotion_type = str(promotion_type or "").strip()
    status_item = str(status_item or "").strip()
    if not campaign_id:
        return None, cfg

    urls = []
    if promotion_type:
        urls.append(f"https://api.mercadolibre.com/seller-promotions/promotions/{campaign_id}/items")
    urls.extend([
        f"https://api.mercadolibre.com/seller-promotions/{campaign_id}/items",
    ])

    for url in urls:
        offset = 0
        limit = 50
        total_contado = 0
        paginas_lidas = 0
        endpoint_ok = False
        vistos = set()
        while True:
            paginas_lidas += 1
            if paginas_lidas > 120:
                logger.warning(f"[PROMO CONTAGENS] Interrompendo contagem da promocao {campaign_id}: limite de paginas atingido")
                break

            params = {"app_version": "v2", "offset": offset, "limit": limit}
            if promotion_type and "/promotions/" in url:
                params["promotion_type"] = promotion_type
            if status_item:
                status_norm = status_item.lower()
                if status_norm in {"active", "started"}:
                    params["status"] = "started"
                elif status_norm in {"programmed", "scheduled", "pending"}:
                    params["status"] = "pending"
                elif status_norm in {"eligible", "candidate"}:
                    params["status"] = "candidate"
                elif status_norm == "paused":
                    params["status_item"] = "paused"
                else:
                    params["status"] = status_item
            try:
                resp, cfg = ctx.ml_api_request_com_retry(
                    client_id,
                    loja,
                    cfg,
                    "GET",
                    url,
                    params=params,
                    timeout=30,
                    max_attempts=3,
                )
            except Exception as exc:
                logger.warning(
                    "[PROMO CONTAGENS] Mercado Livre demorou para responder ao contar %s da promocao %s: %s",
                    status_item or "total",
                    campaign_id,
                    exc,
                )
                break
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                logger.warning(f"[PROMO CONTAGENS] Falha ao contar {status_item or 'total'} da promocao {campaign_id}: {resp.status_code}")
                break
            endpoint_ok = True
            try:
                data = resp.json() or {}
            except Exception:
                data = {}
            paging = data.get("paging") if isinstance(data, dict) else {}
            total = ctx.parse_float_flex((paging or {}).get("total")) if isinstance(paging, dict) else None
            if total is not None:
                return max(0, int(total)), cfg

            entries = (data.get("results") or data.get("items") or []) if isinstance(data, dict) else []
            for entry in entries:
                if isinstance(entry, dict):
                    raw_item = entry.get("item")
                    item_obj_id = raw_item.get("id") if isinstance(raw_item, dict) else raw_item
                    item_id = str(entry.get("item_id") or entry.get("itemId") or item_obj_id or entry.get("id") or "").strip()
                else:
                    item_id = str(entry or "").strip()
                chave = item_id or f"row:{offset}:{total_contado}"
                if chave not in vistos:
                    vistos.add(chave)
                    total_contado += 1

            offset += limit
            if not entries or len(entries) < limit:
                break
        if endpoint_ok:
            return total_contado, cfg
    return None, cfg


def listar_promocoes_ativas_mercado_livre(client_id: str, loja: str) -> dict:
    return _ml_listar_promocoes_ativas_payload(client_id, loja)


def listar_promocoes_contagens_mercado_livre(client_id: str, loja: str) -> dict:
    payload = _ml_listar_promocoes_ativas_payload(client_id, loja)
    loja_nome = payload.get("loja") or str(loja or "").strip()
    cache_key = f"camp_contagens:{client_id}:{loja_nome}:ativos_elegiveis_v3"
    cached = _ml_cache_get(cache_key, _ML_CACHE_TTL_CONTAGENS)
    if isinstance(cached, dict):
        return cached

    ctx = _ctx()
    cfg = ctx.obter_cfg_ml(client_id, loja_nome)
    counts = {}
    for campanha in payload.get("campaigns") or []:
        if not isinstance(campanha, dict):
            continue
        campanha_id = str(campanha.get("id") or "").strip()
        if not campanha_id:
            continue
        promotion_type = str(campanha.get("type") or campanha.get("promotion_type") or "").strip()
        active_count = _ml_extrair_contagem_campanha(campanha, ["active_count", "active_items_count", "active_item_count"])
        eligible_count = _ml_extrair_contagem_campanha(campanha, ["eligible_count", "eligible_items_count", "eligible_item_count", "candidate_items_count", "candidate_count", "items_count", "item_count", "total_items"])

        if active_count is None:
            active_count, cfg = _ml_contar_itens_promocao_status(client_id, loja_nome, cfg, campanha_id, promotion_type, "active")

        if eligible_count is None:
            for status_item in ("candidate", "pending", "eligible", ""):
                eligible_count, cfg = _ml_contar_itens_promocao_status(client_id, loja_nome, cfg, campanha_id, promotion_type, status_item)
                if eligible_count is not None:
                    break

        counts[campanha_id] = {
            "active": active_count,
            "active_count": active_count,
            "eligible": eligible_count,
            "eligible_count": eligible_count,
        }

    resultado = {
        "success": True,
        "loja": loja_nome,
        "counts": counts,
    }
    _ml_cache_set(cache_key, resultado)
    return resultado
