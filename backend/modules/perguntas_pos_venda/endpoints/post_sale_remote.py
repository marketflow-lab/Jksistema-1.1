"""Remote post-sale conversation collection."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.contracts import _ML_POS_VENDA_MAX_MESSAGE_WORKERS
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id

_ml_api_request = runtime_adapter("_ml_api_request")
_ml_buscar_itens_batch = runtime_adapter("_ml_buscar_itens_batch")
_ml_parse_error_detail = runtime_adapter("_ml_parse_error_detail")
_ml_pos_venda_conversa_corresponde_busca = runtime_adapter("_ml_pos_venda_conversa_corresponde_busca")
_ml_pos_venda_conversa_nao_lida = runtime_adapter("_ml_pos_venda_conversa_nao_lida")
_ml_pos_venda_data_iso = runtime_adapter("_ml_pos_venda_data_iso")
_ml_pos_venda_normalizar_pedido = runtime_adapter("_ml_pos_venda_normalizar_pedido")
_ml_pos_venda_normalizar_termo_busca = runtime_adapter("_ml_pos_venda_normalizar_termo_busca")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
logger = runtime_adapter("logger")


def _ml_pos_venda_consultar_mensagens(
    order: dict,
    *,
    client_id: str,
    nome_loja: str,
    cfg: dict,
    seller_id: str,
    filtro_nao_lidas: bool,
    erros: list[dict],
):
    order_id = str((order or {}).get("id") or "").strip()
    pack_id = str((order or {}).get("pack_id") or order_id).strip()
    if not pack_id:
        return None
    url = f"https://api.mercadolibre.com/messages/packs/{pack_id}/sellers/{seller_id}"
    try:
        response, _ = _ml_api_request(
            client_id,
            nome_loja,
            dict(cfg),
            "GET",
            url,
            params={"tag": "post_sale", "mark_as_read": "false", "limit": 10, "offset": 0},
            timeout=18,
        )
        if response.status_code in {403, 404}:
            return None
        if response.status_code != 200:
            erros.append({"order_id": order_id, "status": response.status_code})
            if response.status_code == 429 or response.status_code >= 500:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=_ml_parse_error_detail(
                        response, "Erro temporario ao buscar mensagens do Mercado Livre"
                    ),
                )
            return None
        mensagens_data = response.json() or {}
        mensagens = mensagens_data.get("messages")
        if not isinstance(mensagens, list):
            mensagens = []
        paging = mensagens_data.get("paging") if isinstance(mensagens_data.get("paging"), dict) else {}
        if not mensagens and int(paging.get("total") or 0) <= 0:
            return None
        if filtro_nao_lidas and not _ml_pos_venda_conversa_nao_lida(mensagens_data, seller_id):
            return None
        return {"order": order, "mensagens_data": mensagens_data}
    except HTTPException:
        raise
    except Exception as exc:
        erros.append({"order_id": order_id, "erro": str(exc)})
        raise


def _ml_pos_venda_remote_response(
    *,
    conversas_raw: list[dict],
    client_id: str,
    nome_loja: str,
    cfg: dict,
    seller_id: str,
    busca_ativa: bool,
    busca_texto: str,
    filtro_nao_lidas: bool,
    dias: int,
    offset_inicial: int,
    limit: int,
    offset: int,
    total: int,
    orders_avaliadas: int,
    erros: list[dict],
):
    item_ids = []
    item_ids_vistos = set()
    for conversa in conversas_raw:
        order = conversa.get("order") if isinstance(conversa, dict) else {}
        for entry in (order or {}).get("order_items") or []:
            item = entry.get("item") if isinstance(entry, dict) and isinstance(entry.get("item"), dict) else {}
            item_id = str(item.get("id") or item.get("item_id") or entry.get("item_id") or "").strip() if isinstance(entry, dict) else ""
            if item_id and item_id not in item_ids_vistos:
                item_ids_vistos.add(item_id)
                item_ids.append(item_id)
    itens, _ = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
    item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
    conversas = [
        _ml_pos_venda_normalizar_pedido(
            conversa.get("order") or {}, conversa.get("mensagens_data") or {}, seller_id, item_por_id
        )
        for conversa in conversas_raw
        if isinstance(conversa, dict)
    ]
    if busca_ativa:
        conversas = [item for item in conversas if _ml_pos_venda_conversa_corresponde_busca(item, busca_texto)]
    if filtro_nao_lidas:
        conversas = [item for item in conversas if bool(item.get("nao_lida") or item.get("unread"))]
    conversas.sort(key=lambda item: item.get("last_message_date") or item.get("date_created") or "", reverse=True)
    conversas_total = len(conversas)
    if busca_ativa:
        conversas = conversas[:limit]
    next_offset = None if busca_ativa else (offset if offset < total else None)
    return jsonable_encoder({
        "success": True, "loja": nome_loja, "seller_id": seller_id, "dias": dias,
        "offset": offset_inicial, "limit": limit, "busca": busca_texto,
        "nao_lidas": filtro_nao_lidas, "next_offset": next_offset,
        "has_next": bool(next_offset is not None), "orders_total": total,
        "orders_avaliadas": orders_avaliadas, "conversas_total": conversas_total,
        "conversas_nao_lidas_total": sum(1 for item in conversas if item.get("nao_lida") or item.get("unread")),
        "interrompido": offset < total, "erros": erros[:10], "conversas": conversas,
    })


def _ml_pos_venda_listar_conversas_remoto(
    loja: str,
    dias: int = 365,
    offset: int = 0,
    limit: int = 20,
    max_orders: int = 10000,
    busca: Optional[str] = None,
    nao_lidas: bool = False,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar o pÃƒÂ³s venda.")

        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(status_code=400, detail="ID do usuÃƒÂ¡rio do Mercado Livre nÃ£o encontrado para esta loja.")

        dias = max(1, min(int(dias or 365), 365))
        offset_inicial = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 20), 20))
        max_orders = max(limit, min(int(max_orders or 10000), 10000))
        busca_texto = str(busca or "").strip()
        busca_ativa = bool(_ml_pos_venda_normalizar_termo_busca(busca_texto))
        filtro_nao_lidas = bool(nao_lidas)
        agora = dt.datetime.now()
        data_inicio = agora - dt.timedelta(days=dias)
        orders_url = "https://api.mercadolibre.com/orders/search"
        offset = offset_inicial
        page_limit = limit
        total = 0
        orders_avaliadas = 0
        conversas_raw = []
        packs_avaliados = set()
        erros = []

        consultar_mensagens = partial(
            _ml_pos_venda_consultar_mensagens,
            client_id=client_id,
            nome_loja=nome_loja,
            cfg=cfg,
            seller_id=seller_id,
            filtro_nao_lidas=filtro_nao_lidas,
            erros=erros,
        )

        limite_coleta = max_orders if busca_ativa else limit

        while len(conversas_raw) < limite_coleta and orders_avaliadas < max_orders:
            pedidos_restantes = max_orders - orders_avaliadas
            params = {
                "seller": seller_id,
                "order.date_created.from": _ml_pos_venda_data_iso(data_inicio),
                "order.date_created.to": _ml_pos_venda_data_iso(agora),
                "sort": "date_desc",
                "offset": offset,
                "limit": min(page_limit, pedidos_restantes),
            }
            resp, cfg = _ml_api_request(client_id, nome_loja, cfg, "GET", orders_url, params=params, timeout=25)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao buscar vendas do Mercado Livre"))
            data = resp.json() or {}
            lote = data.get("results") or []
            if not isinstance(lote, list) or not lote:
                break
            orders = [order for order in lote if isinstance(order, dict)]
            orders_avaliadas += len(orders)
            paging = data.get("paging") or {}
            total = int(paging.get("total") or total or 0)
            offset += len(lote)

            orders_para_consultar = []
            for order in orders:
                order_id = str((order or {}).get("id") or "").strip()
                pack_id = str((order or {}).get("pack_id") or order_id).strip()
                if pack_id and pack_id in packs_avaliados:
                    continue
                if pack_id:
                    packs_avaliados.add(pack_id)
                orders_para_consultar.append(order)

            if not orders_para_consultar:
                if offset >= total:
                    break
                continue

            max_workers = min(_ML_POS_VENDA_MAX_MESSAGE_WORKERS, max(1, len(orders_para_consultar)))
            if max_workers <= 1:
                for order in orders_para_consultar:
                    conversa = consultar_mensagens(order)
                    if conversa:
                        conversas_raw.append(conversa)
                        if len(conversas_raw) >= limite_coleta:
                            break
            else:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futuros = [executor.submit(consultar_mensagens, order) for order in orders_para_consultar]
                    for futuro in as_completed(futuros):
                        if len(conversas_raw) >= limite_coleta:
                            continue
                        conversa = futuro.result()
                        if conversa:
                            conversas_raw.append(conversa)
            if offset >= total:
                break

        return _ml_pos_venda_remote_response(
            conversas_raw=conversas_raw, client_id=client_id, nome_loja=nome_loja,
            cfg=cfg, seller_id=seller_id, busca_ativa=busca_ativa, busca_texto=busca_texto,
            filtro_nao_lidas=filtro_nao_lidas, dias=dias, offset_inicial=offset_inicial,
            limit=limit, offset=offset, total=total, orders_avaliadas=orders_avaliadas,
            erros=erros,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML POS VENDA] Falha inesperada ao listar conversas da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar conversas do pÃƒÂ³s venda: {str(e)}")
