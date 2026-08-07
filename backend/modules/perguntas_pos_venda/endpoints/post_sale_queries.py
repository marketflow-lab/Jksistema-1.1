"""Post-sale conversations, claims and attachment queries."""

from __future__ import annotations

import datetime as dt
import io
from typing import Optional
from urllib.parse import quote_plus

from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from backend.modules.perguntas_pos_venda.endpoints.contracts import _ML_POS_VENDA_RECENT_DAYS
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.security import get_tenant_id
from backend.services import perguntas_pos_venda_store
from backend.services.perguntas_pos_venda_state import ML_POS_VENDA_DEFAULT_MAX_CHARS
from backend.modules.perguntas_pos_venda.endpoints.post_sale_sync import (
    _ml_pos_venda_cache_response,
    _ml_pos_venda_schedule_sync,
    _ml_pos_venda_sync_fresh,
)

_ml_api_request = runtime_adapter("_ml_api_request")
_ml_buscar_itens_batch = runtime_adapter("_ml_buscar_itens_batch")
_ml_mediacao_buscar_motivos_claims = runtime_adapter("_ml_mediacao_buscar_motivos_claims")
_ml_mediacao_normalizar_claim = runtime_adapter("_ml_mediacao_normalizar_claim")
_ml_mediacao_order_id = runtime_adapter("_ml_mediacao_order_id")
_ml_parse_error_detail = runtime_adapter("_ml_parse_error_detail")
_ml_pos_venda_buscar_pedido = runtime_adapter("_ml_pos_venda_buscar_pedido")
_ml_pos_venda_data_iso = runtime_adapter("_ml_pos_venda_data_iso")
_ml_pos_venda_item_ids_pedido = runtime_adapter("_ml_pos_venda_item_ids_pedido")
_ml_pos_venda_montar_conversa_normalizada = runtime_adapter("_ml_pos_venda_montar_conversa_normalizada")
_ml_pos_venda_normalizar_termo_busca = runtime_adapter("_ml_pos_venda_normalizar_termo_busca")
_ml_pos_venda_pedido_corresponde_busca = runtime_adapter("_ml_pos_venda_pedido_corresponde_busca")
_obter_cfg_ml = runtime_adapter("_obter_cfg_ml")
get_tenant_path = runtime_adapter("get_tenant_path")
logger = runtime_adapter("logger")


def ml_pos_venda_listar_conversas(
    loja: str,
    dias: int = 365,
    offset: int = 0,
    limit: int = 20,
    max_orders: int = 10000,
    busca: Optional[str] = None,
    nao_lidas: bool = False,
    sync_mode: str = "auto",
    summary_only: bool = False,
    force_refresh: bool = False,
    client_id: str = Depends(get_tenant_id),
):
    nome_loja = str(loja or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja para buscar o pos-venda.")

    cfg = _obter_cfg_ml(client_id, nome_loja)
    seller_id = str(cfg.get("user_id") or "").strip()
    if not seller_id:
        raise HTTPException(status_code=400, detail="ID do usuario do Mercado Livre nao encontrado para esta loja.")

    dias = max(1, min(int(dias or 365), 365))
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 20), 20))
    max_orders = max(limit, min(int(max_orders or 10000), 10000))
    busca_texto = str(busca or "").strip()
    mode_requested = str(sync_mode or "auto").strip().lower()
    if mode_requested not in {"auto", "cache", "bootstrap", "incremental"}:
        raise HTTPException(status_code=400, detail="Modo de sincronizacao de pos-venda invalido.")

    tenant_path = get_tenant_path(client_id)
    state = perguntas_pos_venda_store.get_state(tenant_path, nome_loja, seller_id)
    bootstrap_complete = bool(state.get("bootstrap_complete"))
    selected_mode = ""

    if offset == 0 and not busca_texto:
        if mode_requested == "bootstrap":
            selected_mode = "bootstrap"
        elif mode_requested == "incremental":
            selected_mode = "incremental"
        elif mode_requested == "auto" and not summary_only:
            selected_mode = "bootstrap" if dias >= 365 and not bootstrap_complete else "incremental"

    if selected_mode == "bootstrap" and bootstrap_complete and not force_refresh:
        selected_mode = "incremental"

    should_schedule = bool(selected_mode)
    if selected_mode == "incremental" and not force_refresh:
        should_schedule = not _ml_pos_venda_sync_fresh(state)

    if should_schedule:
        _ml_pos_venda_schedule_sync(
            client_id=client_id,
            tenant_path=tenant_path,
            loja=nome_loja,
            seller_id=seller_id,
            mode=selected_mode,
            coverage_days=365 if selected_mode == "bootstrap" else _ML_POS_VENDA_RECENT_DAYS,
            max_orders=max_orders,
        )

    return _ml_pos_venda_cache_response(
        tenant_path=tenant_path,
        client_id=client_id,
        loja=nome_loja,
        seller_id=seller_id,
        dias=dias,
        offset=offset,
        limit=limit,
        busca=busca_texto,
        nao_lidas=bool(nao_lidas),
        summary_only=bool(summary_only),
    )


def _ml_mediacao_enrich_claims(
    *,
    claims_raw: list[dict],
    client_id: str,
    nome_loja: str,
    cfg: dict,
    seller_id: str,
    busca_ativa: bool,
    busca_texto: str,
) -> tuple[list[dict], dict, int]:
    reason_ids = [str(item.get("reason_id") or "").strip() for item in claims_raw]
    motivos, cfg = _ml_mediacao_buscar_motivos_claims(
        client_id, nome_loja, cfg, reason_ids
    )
    order_ids = list(dict.fromkeys(filter(None, (_ml_mediacao_order_id(item) for item in claims_raw))))
    orders_por_id = {}
    for order_id in order_ids:
        try:
            order, cfg = _ml_pos_venda_buscar_pedido(client_id, nome_loja, cfg, order_id)
            if order:
                orders_por_id[str(order.get("id") or order_id)] = order
        except Exception as exc:
            logger.warning("[ML MEDIACAO] Falha ao buscar pedido %s loja=%s: %s", order_id, nome_loja, exc)
    item_ids = list(dict.fromkeys(
        item_id
        for order in orders_por_id.values()
        for item_id in _ml_pos_venda_item_ids_pedido(order)
        if item_id
    ))
    itens, cfg = _ml_buscar_itens_batch(client_id, nome_loja, cfg, item_ids)
    item_por_id = {str(item.get("id") or "").strip(): item for item in itens if isinstance(item, dict)}
    mediacoes = []
    for claim_raw in claims_raw:
        claim = dict(claim_raw)
        order_id = _ml_mediacao_order_id(claim)
        reason_id = str(claim.get("reason_id") or "").strip()
        if reason_id and motivos.get(reason_id):
            claim["reason"] = motivos[reason_id]
        venda = _ml_mediacao_normalizar_claim(
            claim, orders_por_id.get(order_id) or {"id": order_id}, seller_id, item_por_id
        )
        if not busca_ativa or _ml_pos_venda_pedido_corresponde_busca(venda, busca_texto):
            mediacoes.append(venda)
    return mediacoes, cfg, len(order_ids)


def _ml_mediacao_response(
    mediacoes: list[dict],
    *,
    nome_loja: str,
    seller_id: str,
    dias: int,
    limit: int,
    busca_texto: str,
    total: int,
    orders_avaliadas: int,
    interrompido: bool,
):
    mediacoes.sort(
        key=lambda item: item.get("claim_last_updated") or item.get("claim_date_created") or "",
        reverse=True,
    )
    total_filtrado = len(mediacoes)
    claim_kind = lambda item: str(
        item.get("claim_kind") or item.get("claim_type") or ""
    ).strip().lower()
    total_mediacoes = sum(1 for item in mediacoes if claim_kind(item) != "devolucao")
    total_devolucoes = sum(
        1 for item in mediacoes if claim_kind(item) in {"devolucao", "return", "returns"}
    )
    return jsonable_encoder({
        "success": True, "loja": nome_loja, "seller_id": seller_id,
        "dias": dias, "limit": limit, "busca": busca_texto,
        "orders_total": total, "orders_avaliadas": orders_avaliadas,
        "conversas_total": total_filtrado,
        "mediacoes_total": total_mediacoes,
        "devolucoes_total": total_devolucoes, "interrompido": interrompido,
        "conversas": mediacoes[:limit],
    })


def ml_pos_venda_listar_mediacoes(
    loja: str,
    dias: int = 365,
    limit: int = 20,
    max_claims: int = 300,
    busca: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    try:
        nome_loja = str(loja or "").strip()
        if not nome_loja:
            raise HTTPException(status_code=400, detail="Informe a loja para buscar mediaÃ§Ãµes.")

        cfg = _obter_cfg_ml(client_id, nome_loja)
        seller_id = str(cfg.get("user_id") or "").strip()
        if not seller_id:
            raise HTTPException(status_code=400, detail="ID do usuÃ¡rio do Mercado Livre nÃ£o encontrado para esta loja.")

        dias = max(1, min(int(dias or 365), 365))
        limit = max(1, min(int(limit or 20), 50))
        max_claims = max(limit, min(int(max_claims or 300), 1000))
        busca_texto = str(busca or "").strip()
        busca_ativa = bool(_ml_pos_venda_normalizar_termo_busca(busca_texto))
        agora = dt.datetime.now()
        data_inicio = agora - dt.timedelta(days=dias)
        claims_raw = []
        claims_vistos = set()
        total = 0
        interrompido = False
        consultas_claims = [
            {
                "tipo": "mediacao",
                "label": "MediaÃ§Ã£o",
                "obrigatoria": True,
                "params": {"type": "mediations", "stage": "dispute", "status": "opened"},
            },
            {
                "tipo": "devolucao",
                "label": "DevoluÃ§Ã£o",
                "obrigatoria": False,
                "params": {"type": "return", "status": "opened"},
            },
        ]

        for consulta in consultas_claims:
            offset = 0
            total_consulta = 0
            coletados_consulta = 0
            while coletados_consulta < max_claims:
                params = {
                    **consulta["params"],
                    "sort": "last_updated:desc",
                    "offset": offset,
                    "limit": min(50, max_claims - coletados_consulta),
                    "range": f"last_updated:after:{_ml_pos_venda_data_iso(data_inicio)},before:{_ml_pos_venda_data_iso(agora)}",
                }
                resp, cfg = _ml_api_request(
                    client_id,
                    nome_loja,
                    cfg,
                    "GET",
                    "https://api.mercadolibre.com/post-purchase/v1/claims/search",
                    params=params,
                    timeout=25,
                )
                if resp.status_code != 200:
                    detalhe = _ml_parse_error_detail(resp, f"Erro ao buscar {consulta['label'].lower()} do Mercado Livre")
                    if consulta["obrigatoria"]:
                        raise HTTPException(status_code=resp.status_code, detail=detalhe)
                    logger.warning("[ML MEDIACAO] Falha ao buscar %s loja=%s: %s", consulta["label"].lower(), nome_loja, detalhe)
                    break
                data = resp.json() or {}
                lote = data.get("data") or data.get("results") or []
                if not isinstance(lote, list) or not lote:
                    break
                paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
                total_consulta = int(paging.get("total") or total_consulta or len(lote))
                total += total_consulta if offset == 0 else 0
                for claim in lote:
                    if not isinstance(claim, dict):
                        continue
                    claim_id = str(claim.get("id") or "").strip()
                    chave_claim = claim_id or f"{consulta['tipo']}:{_ml_mediacao_order_id(claim)}:{claim.get('reason_id') or ''}"
                    if chave_claim and chave_claim in claims_vistos:
                        continue
                    if chave_claim:
                        claims_vistos.add(chave_claim)
                    claim = dict(claim)
                    claim["_jk_claim_tipo"] = consulta["tipo"]
                    claim["_jk_claim_tipo_label"] = consulta["label"]
                    claims_raw.append(claim)
                offset += len(lote)
                coletados_consulta += len(lote)
                if offset >= total_consulta:
                    break
            if offset < total_consulta:
                interrompido = True

        mediacoes, cfg, orders_avaliadas = _ml_mediacao_enrich_claims(
            claims_raw=claims_raw,
            client_id=client_id,
            nome_loja=nome_loja,
            cfg=cfg,
            seller_id=seller_id,
            busca_ativa=busca_ativa,
            busca_texto=busca_texto,
        )

        return _ml_mediacao_response(
            mediacoes, nome_loja=nome_loja, seller_id=seller_id, dias=dias,
            limit=limit, busca_texto=busca_texto, total=total,
            orders_avaliadas=orders_avaliadas, interrompido=interrompido,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[ML MEDIACAO] Falha inesperada ao listar mediaÃ§Ãµes da loja {loja}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao listar mediaÃ§Ãµes: {str(e)}")


def ml_pos_venda_detalhe_conversa(
    loja: str,
    pack_id: str,
    order_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    nome_loja = str(loja or "").strip()
    pack = str(pack_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not pack:
        raise HTTPException(status_code=400, detail="Informe a conversa do pos venda.")
    cfg = _obter_cfg_ml(client_id, nome_loja)
    conversa, cfg = _ml_pos_venda_montar_conversa_normalizada(
        client_id,
        nome_loja,
        cfg,
        pack,
        str(order_id or "").strip(),
    )
    return jsonable_encoder({
        "success": True,
        "loja": nome_loja,
        "conversa": conversa,
        "mensagens": conversa.get("messages") or [],
        "seller_max_message_length": conversa.get("seller_max_message_length") or ML_POS_VENDA_DEFAULT_MAX_CHARS,
    })


def ml_pos_venda_obter_anexo(
    attachment_id: str,
    loja: str,
    client_id: str = Depends(get_tenant_id),
):
    nome_loja = str(loja or "").strip()
    anexo_id = str(attachment_id or "").strip()
    if not nome_loja:
        raise HTTPException(status_code=400, detail="Informe a loja.")
    if not anexo_id:
        raise HTTPException(status_code=400, detail="Informe o anexo.")
    cfg = _obter_cfg_ml(client_id, nome_loja)
    resp, cfg = _ml_api_request(
        client_id,
        nome_loja,
        cfg,
        "GET",
        f"https://api.mercadolibre.com/messages/attachments/{quote_plus(anexo_id)}",
        params={"tag": "post_sale"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=_ml_parse_error_detail(resp, "Erro ao carregar anexo do pÃ³s venda"))
    media_type = str(resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    headers = {"Cache-Control": "private, max-age=300"}
    return StreamingResponse(io.BytesIO(resp.content), media_type=media_type, headers=headers)


__all__ = [
    "ml_pos_venda_listar_conversas",
    "ml_pos_venda_listar_mediacoes",
    "ml_pos_venda_detalhe_conversa",
    "ml_pos_venda_obter_anexo",
]
