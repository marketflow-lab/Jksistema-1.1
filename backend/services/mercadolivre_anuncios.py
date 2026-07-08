"""Mercado Livre listing, visits and item detail services."""

from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from backend.services.mercadolivre_cache import _ml_cache_get, _ml_cache_set
from backend.services.mercadolivre_context import _ctx


def listar_anuncios_mercado_livre(
    client_id: str,
    loja: str,
    offset: int = 0,
    limit: int = 50,
    sku: Optional[str] = None,
) -> dict:
    ctx = _ctx()
    logger = ctx.logger
    try:
        sku_norm = str(sku or "").strip()
        chave_cache = f"anuncios:{client_id}:{loja}:{offset}:{limit}:{sku_norm}"
        cached = _ml_cache_get(chave_cache)
        if cached is not None:
            logger.info(f"[ML CACHE] Retornando anuncios do cache para loja={loja} offset={offset} sku={sku_norm}")
            return cached

        cfg = ctx.obter_cfg_ml(client_id, loja)
        user_id = cfg.get("user_id")
        if not user_id:
            raise HTTPException(status_code=400, detail="ID do usuario Mercado Livre nao encontrado")

        limit = max(1, min(int(limit or 20), 20))
        offset = max(0, int(offset or 0))
        sku_filtro = str(sku or "").strip()
        sku_filtro_lower = sku_filtro.lower()
        url = f"https://api.mercadolibre.com/users/{user_id}/items/search"
        params = {"offset": offset, "limit": limit, "status": "active"}
        if sku_filtro:
            params = {"offset": 0, "limit": 100, "status": "active", "seller_sku": sku_filtro}

        logger.info(f"[ML API] Buscando anuncios para user_id={user_id}, loja={loja}, offset={offset}, limit={limit}, sku={sku_filtro}")

        resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, params=params, timeout=15)
        logger.info(f"[ML API] Status da busca de anuncios: {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"[ML API] Erro ao buscar anuncios: {resp.status_code} - {resp.text}")
            raise HTTPException(status_code=resp.status_code, detail=ctx.ml_parse_error_detail(resp, "Erro ao buscar anuncios"))

        data = resp.json() or {}
        results = data.get("results", []) or []
        total = data.get("paging", {}).get("total", 0)
        logger.info(f"[ML API] Total de anuncios: {total}, IDs retornados: {len(results)}")

        if sku_filtro and not results:
            logger.info(f"[ML API] Nenhum resultado exato para sku={sku_filtro}. Fazendo varredura paginada.")
            results = []
            scan_offset = 0
            scan_limit = 100
            while True:
                scan_params = {"offset": scan_offset, "limit": scan_limit, "status": "active"}
                scan_resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, params=scan_params, timeout=15)
                if scan_resp.status_code != 200:
                    logger.warning(f"[ML API] Falha na varredura por SKU: {scan_resp.status_code}")
                    break
                scan_data = scan_resp.json() or {}
                batch = scan_data.get("results", []) or []
                if not batch:
                    break
                results.extend(batch)
                total_scan = scan_data.get("paging", {}).get("total", len(results))
                scan_offset += scan_limit
                if scan_offset >= total_scan:
                    break
            total = len(results)

        item_ids = results if sku_filtro else results
        itens_detalhados, cfg = ctx.ml_buscar_itens_batch(client_id, loja, cfg, item_ids)

        detalhes_ordenados = [None] * len(itens_detalhados)
        if itens_detalhados:
            max_workers = min(6, max(2, len(itens_detalhados)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(ctx.ml_montar_detalhe_anuncio_listagem, client_id, loja, dict(cfg), item, sku_filtro_lower): idx
                    for idx, item in enumerate(itens_detalhados)
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    try:
                        detalhes_ordenados[idx] = future.result()
                    except Exception as e:
                        logger.error(f"[ML API] Excecao ao montar item da listagem: {e}")

        detalhes = [det for det in detalhes_ordenados if det]

        if sku_filtro:
            total = len(detalhes)
            detalhes = detalhes[offset: offset + limit]

        logger.info(f"[ML API] Detalhes carregados: {len(detalhes)} anuncios")
        resultado = jsonable_encoder({"total": total, "results": detalhes, "offset": offset, "limit": limit})
        _ml_cache_set(chave_cache, resultado)
        return resultado
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML API] Falha inesperada ao listar anuncios da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar anuncios do Mercado Livre: {str(e)}")


def _ml_parse_data_visita(valor: Any) -> Optional[dt.date]:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return dt.datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except Exception:
        pass
    try:
        return dt.datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def historico_visitas_anuncio_mercado_livre(
    client_id: str,
    item_id: str,
    loja: str,
    dias: int = 150,
) -> dict:
    ctx = _ctx()
    logger = ctx.logger
    try:
        item_id = str(item_id or "").strip().upper()
        if not re.match(r"^[A-Z]{3}\d+$", item_id):
            raise HTTPException(status_code=400, detail="ID do anuncio invalido.")

        dias = max(1, min(150, int(dias or 150)))
        ending = dt.datetime.now().strftime("%Y-%m-%d")
        chave_cache = f"visitas:{client_id}:{loja}:{item_id}:{dias}:{ending}"
        cached = _ml_cache_get(chave_cache, 900)
        if cached is not None:
            return cached

        cfg = ctx.obter_cfg_ml(client_id, loja)
        resp, cfg = ctx.ml_api_request(
            client_id,
            loja,
            cfg,
            "GET",
            f"https://api.mercadolibre.com/items/{item_id}/visits/time_window",
            params={"last": dias, "unit": "day", "ending": ending},
            timeout=15,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=ctx.ml_parse_error_detail(resp, "Erro ao consultar historico de visitas"),
            )

        data = resp.json() or {}
        pontos_map: dict[str, int] = {}
        for entry in data.get("results") or []:
            if not isinstance(entry, dict):
                continue
            dia = _ml_parse_data_visita(entry.get("date"))
            if not dia:
                continue
            try:
                total_dia = int(float(entry.get("total") or 0))
            except Exception:
                total_dia = 0
            pontos_map[dia.isoformat()] = max(0, total_dia)

        data_inicio = _ml_parse_data_visita(data.get("date_from"))
        data_fim = _ml_parse_data_visita(data.get("date_to"))
        if not data_fim:
            data_fim = dt.datetime.strptime(ending, "%Y-%m-%d").date()
        if not data_inicio:
            data_inicio = data_fim - dt.timedelta(days=dias)

        pontos = []
        dia_atual = data_inicio
        limite_dias = 0
        while dia_atual < data_fim and limite_dias < 160:
            data_iso = dia_atual.isoformat()
            pontos.append({"date": data_iso, "total": int(pontos_map.get(data_iso, 0))})
            dia_atual += dt.timedelta(days=1)
            limite_dias += 1
        if not pontos and pontos_map:
            pontos = [{"date": data_iso, "total": total} for data_iso, total in sorted(pontos_map.items())]

        total_visits = data.get("total_visits")
        try:
            total_visits = int(float(total_visits))
        except Exception:
            total_visits = sum(p["total"] for p in pontos)

        media = round((total_visits / len(pontos)), 2) if pontos else 0
        recorde = None
        if pontos:
            recorde = max(pontos, key=lambda p: p["total"])

        payload = jsonable_encoder({
            "success": True,
            "item_id": data.get("item_id") or item_id,
            "date_from": data.get("date_from"),
            "date_to": data.get("date_to"),
            "total_visits": total_visits,
            "average": media,
            "record": recorde,
            "last": dias,
            "unit": data.get("unit") or "day",
            "results": pontos,
            "raw_count": len(data.get("results") or []),
        })
        _ml_cache_set(chave_cache, payload)
        return payload
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ML API] Falha inesperada ao consultar visitas do anuncio %s: %s", item_id, e)
        raise HTTPException(status_code=500, detail=f"Erro ao consultar historico de visitas: {str(e)}")


def buscar_anuncio_mercado_livre(client_id: str, item_id: str, loja: str) -> dict:
    ctx = _ctx()
    cfg = ctx.obter_cfg_ml(client_id, loja)
    url = f"https://api.mercadolibre.com/items/{item_id}"
    resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", url, timeout=15)
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Anuncio nao encontrado")
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=ctx.ml_parse_error_detail(resp, "Erro ao buscar anuncio"))

    item = resp.json() or {}
    price_info, cfg = ctx.ml_obter_preco_detalhado(client_id, loja, cfg, item_id, fallback_price=item.get("price"))
    shipping_data, cfg = ctx.ml_obter_frete_detalhado(client_id, loja, cfg, item_id, item.get("shipping") or {})
    item_fee = dict(item)
    if price_info.get("price") is not None:
        item_fee["price"] = price_info.get("price")
    fee_data, cfg = ctx.ml_obter_taxas_anuncio(client_id, loja, cfg, item_fee)

    preco_ref = ctx.parse_float_flex(price_info.get("price"))
    if preco_ref is None:
        preco_ref = ctx.parse_float_flex(item.get("price"))
    taxa_fixa_formula = ctx.ml_estimar_taxa_fixa_por_preco(
        preco_ref,
        domain_id=item.get("domain_id") or "",
        category_id=item.get("category_id") or "",
        listing_type_id=item.get("listing_type_id") or "",
    )
    if taxa_fixa_formula is not None:
        fee_data["fixed_fee_amount"] = float(taxa_fixa_formula)
        fee_data["fixed_fee_text"] = "-" if float(taxa_fixa_formula) == 0 else ctx.formatar_moeda_br(taxa_fixa_formula)
        fee_data["fixed_fee_source"] = "formula_padrao"

    item["price_details"] = price_info
    item["shipping_details"] = shipping_data
    item["fee_details"] = fee_data
    desc_url = f"https://api.mercadolibre.com/items/{item_id}/description"
    desc_resp, cfg = ctx.ml_api_request(client_id, loja, cfg, "GET", desc_url, timeout=12)
    if desc_resp.status_code == 200:
        item["description_info"] = desc_resp.json()

    return item
