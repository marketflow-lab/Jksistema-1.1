"""Endpoint implementations for Favoritos.

The heavy helper graph lives in dedicated service modules. During startup,
``backend_api`` injects the assembled runtime globals here so the router can bind
stable endpoint callables without registering monolith-local functions.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse


FAVORITOS_ENDPOINTS: tuple[str, ...] = ('favoritos_listar_skus', 'favoritos_skus_ocultos_get', 'favoritos_skus_ocultos_put', 'favoritos_vendedores_ignorados_get', 'favoritos_vendedores_ignorados_put', 'favoritos_anuncios_ignorados_get', 'favoritos_anuncios_ignorados_put', 'favoritos_historico_get', 'favoritos_historico_put', 'favoritos_historico_realtime_sync', 'favoritos_planilhas_lojas_get', 'favoritos_planilhas_lojas_put', 'favoritos_planilhas_colar_historico', 'favoritos_ml_listar_skus_anuncios', 'favoritos_ml_listar_promocoes_ativas', 'favoritos_ml_validar_efetivacao', 'favoritos_ml_efetivar_promocao', 'favoritos_ml_listar_anuncios_sku', 'favoritos_salvar_pesquisas_sku', 'favoritos_gerar_pesquisas_sku_ia', 'favoritos_filtrar_ranking_ia', 'favoritos_buscar_descricao_sku', 'favoritos_buscar_descricoes_skus', 'favoritos_ml_primeira_pagina', 'favoritos_ml_enriquecer_datas', 'favoritos_pesquisar')

_TENANT_DEPENDENCY = None
FAVORITOS_DESCRICAO_CACHE_LOCK = threading.RLock()
FAVORITOS_DESCRICAO_CACHE: dict[tuple[str, str, str], dict] = {}
FAVORITOS_DESCRICAO_INFLIGHT: dict[tuple[str, str, str], dict] = {}
FAVORITOS_DESCRICAO_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {}
FAVORITOS_DESCRICAO_CACHE_TTL_OK_S = 30 * 60
FAVORITOS_DESCRICAO_CACHE_TTL_EMPTY_S = 5 * 60
FAVORITOS_DESCRICAO_CACHE_MAX = 5000
_PROTECTED_GLOBALS = {
    "_TENANT_DEPENDENCY",
    "_PROTECTED_GLOBALS",
    "FAVORITOS_ENDPOINTS",
    "configure_favoritos_endpoints_runtime",
    "get_tenant_id",
}


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if _TENANT_DEPENDENCY is None:
        raise RuntimeError("favoritos_endpoints runtime was not configured")
    result = _TENANT_DEPENDENCY(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


def _extrair_username_do_request(request: Request) -> str:
    """Return only the identity already verified by the auth dependency."""
    try:
        value = getattr(getattr(request, "state", None), "username", "")
        if value:
            return str(value).strip()
    except Exception:
        pass
    raise HTTPException(status_code=401, detail="Identidade autenticada indisponivel. Faca o login novamente.")


def _copy_runtime_globals(legacy_module) -> None:
    endpoint_names = set(FAVORITOS_ENDPOINTS)
    for name in dir(legacy_module):
        if name.startswith("__") or name in endpoint_names or name in _PROTECTED_GLOBALS:
            continue
        globals()[name] = getattr(legacy_module, name)


def configure_favoritos_endpoints_runtime(legacy_module) -> None:
    global _TENANT_DEPENDENCY
    _TENANT_DEPENDENCY = getattr(legacy_module, "get_tenant_id")
    _copy_runtime_globals(legacy_module)


def _favoritos_descricao_cache_key(
    client_id: str,
    loja: str,
    item_id: str,
) -> tuple[str, str, str]:
    return (
        str(client_id or "").strip(),
        str(loja or "").strip().lower(),
        str(item_id or "").strip().upper(),
    )


def _favoritos_descricao_sem(client_id: str) -> threading.BoundedSemaphore:
    client_key = str(client_id or "").strip()
    with FAVORITOS_DESCRICAO_CACHE_LOCK:
        semaphore = FAVORITOS_DESCRICAO_SEMAPHORES.get(client_key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(8)
            FAVORITOS_DESCRICAO_SEMAPHORES[client_key] = semaphore
        return semaphore


def _favoritos_descricao_prune_cache(now: float | None = None) -> None:
    timestamp = float(now or time.time())
    with FAVORITOS_DESCRICAO_CACHE_LOCK:
        for key, item in list(FAVORITOS_DESCRICAO_CACHE.items()):
            if float((item or {}).get("expires_at") or 0) <= timestamp:
                FAVORITOS_DESCRICAO_CACHE.pop(key, None)
        if len(FAVORITOS_DESCRICAO_CACHE) <= FAVORITOS_DESCRICAO_CACHE_MAX:
            return
        excedentes = sorted(
            FAVORITOS_DESCRICAO_CACHE.items(),
            key=lambda pair: float((pair[1] or {}).get("stored_at") or 0),
        )[: len(FAVORITOS_DESCRICAO_CACHE) - FAVORITOS_DESCRICAO_CACHE_MAX]
        for key, _item in excedentes:
            FAVORITOS_DESCRICAO_CACHE.pop(key, None)


def _favoritos_obter_descricao_item_controlada(
    client_id: str,
    loja: str,
    cfg: dict,
    item_id: str,
    item: dict | None = None,
    *,
    rapida: bool = False,
    force_refresh: bool = False,
) -> dict:
    modo = "rapida" if rapida else "completa"
    cache_key = _favoritos_descricao_cache_key(client_id, loja, item_id)
    now = time.time()
    _favoritos_descricao_prune_cache(now)
    with FAVORITOS_DESCRICAO_CACHE_LOCK:
        cached = FAVORITOS_DESCRICAO_CACHE.get(cache_key)
        if (
            not force_refresh
            and cached
            and float(cached.get("expires_at") or 0) > now
        ):
            payload = dict(cached.get("payload") or {})
            payload["cache_hit"] = True
            payload["cache"] = {
                "hit": True,
                "ttl_seconds": max(0, int(float(cached.get("expires_at") or now) - now)),
                "mode": modo,
            }
            return payload
        inflight = FAVORITOS_DESCRICAO_INFLIGHT.get(cache_key)
        if inflight is None:
            inflight = {"event": threading.Event(), "started_at": now}
            FAVORITOS_DESCRICAO_INFLIGHT[cache_key] = inflight
            owner = True
        else:
            owner = False

    if not owner:
        inflight["event"].wait(timeout=45)
        with FAVORITOS_DESCRICAO_CACHE_LOCK:
            cached = FAVORITOS_DESCRICAO_CACHE.get(cache_key)
            if cached and float(cached.get("expires_at") or 0) > time.time():
                payload = dict(cached.get("payload") or {})
                payload["cache_hit"] = True
                payload["cache"] = {"hit": True, "shared_inflight": True, "mode": modo}
                return payload
        return {
            "descricao": "",
            "description_info": {},
            "erro": "Tempo limite aguardando consulta de descricao ja em andamento.",
            "cache_hit": False,
        }

    try:
        with _favoritos_descricao_sem(client_id):
            if rapida:
                payload = _ml_favoritos_obter_descricao_item_rapida(
                    client_id,
                    loja,
                    dict(cfg or {}),
                    item_id,
                    item or {},
                ) or {}
            else:
                payload = _ml_favoritos_obter_descricao_item(
                    client_id,
                    loja,
                    dict(cfg or {}),
                    item_id,
                    item or {},
                ) or {}
        payload = dict(payload)
        payload["cache_hit"] = False
        descricao = str(payload.get("descricao") or "").strip()
        erro = str(payload.get("erro") or "").strip()
        ttl = FAVORITOS_DESCRICAO_CACHE_TTL_OK_S if descricao else FAVORITOS_DESCRICAO_CACHE_TTL_EMPTY_S
        if not erro:
            stored_at = time.time()
            with FAVORITOS_DESCRICAO_CACHE_LOCK:
                FAVORITOS_DESCRICAO_CACHE[cache_key] = {
                    "payload": dict(payload),
                    "stored_at": stored_at,
                    "expires_at": stored_at + ttl,
                }
            payload["cache"] = {"hit": False, "ttl_seconds": ttl, "mode": modo}
        return payload
    finally:
        with FAVORITOS_DESCRICAO_CACHE_LOCK:
            current = FAVORITOS_DESCRICAO_INFLIGHT.pop(cache_key, None)
            if current:
                current["event"].set()


def favoritos_listar_skus(client_id: str = Depends(get_tenant_id)):
    return _favoritos_listar_skus_payload(client_id)


def favoritos_skus_ocultos_get(request: Request, client_id: str = Depends(get_tenant_id)):
    username = _extrair_username_do_request(request)
    prefs = _favoritos_carregar_skus_ocultos(client_id, username)
    return {
        "success": True,
        "skus_ocultos": prefs.get("skus_ocultos") or [],
        "updated_at": prefs.get("updated_at"),
    }


def favoritos_skus_ocultos_put(
    req: FavoritosSkusOcultosRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _favoritos_salvar_skus_ocultos(client_id, username, req.skus_ocultos or [])
    return {
        "success": True,
        "skus_ocultos": payload.get("skus_ocultos") or [],
        "updated_at": payload.get("updated_at"),
    }


def favoritos_vendedores_ignorados_get(request: Request, client_id: str = Depends(get_tenant_id)):
    username = _extrair_username_do_request(request)
    prefs = _favoritos_carregar_vendedores_ignorados(client_id, username)
    return {
        "success": True,
        "vendedores_ignorados": prefs.get("vendedores_ignorados") or [],
        "updated_at": prefs.get("updated_at"),
    }


def favoritos_vendedores_ignorados_put(
    req: FavoritosVendedoresIgnoradosRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _favoritos_salvar_vendedores_ignorados(client_id, username, req.vendedores_ignorados or [])
    return {
        "success": True,
        "vendedores_ignorados": payload.get("vendedores_ignorados") or [],
        "updated_at": payload.get("updated_at"),
    }


def favoritos_anuncios_ignorados_get(request: Request, client_id: str = Depends(get_tenant_id)):
    username = _extrair_username_do_request(request)
    prefs = _favoritos_carregar_anuncios_ignorados(client_id, username)
    return {
        "success": True,
        "anuncios_ignorados": prefs.get("anuncios_ignorados") or {},
        "updated_at": prefs.get("updated_at"),
    }


def favoritos_anuncios_ignorados_put(
    req: FavoritosAnunciosIgnoradosRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _favoritos_salvar_anuncios_ignorados(client_id, username, req.anuncios_ignorados or {})
    return {
        "success": True,
        "anuncios_ignorados": payload.get("anuncios_ignorados") or {},
        "updated_at": payload.get("updated_at"),
    }


def favoritos_historico_get(request: Request, client_id: str = Depends(get_tenant_id)):
    username = _extrair_username_do_request(request)
    payload = _favoritos_carregar_historico(client_id, username)
    return {
        "success": True,
        "historico": payload.get("historico") or [],
        "updated_at": payload.get("updated_at"),
    }


def favoritos_historico_put(
    req: FavoritosHistoricoRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    username = _extrair_username_do_request(request)
    payload = _favoritos_salvar_historico(
        client_id,
        username,
        req.historico or [],
        finalizar_ids=req.finalizar_ids or [],
        inicio_execucao_ms=req.inicio_execucao_ms,
    )
    evento_publicado = False
    if _shared_sync_auto_enabled():
        realtime_sync = _favoritos_propagar_historico_para_usuarios(client_id, username, payload)
        if not realtime_sync.get("skipped"):
            evento_publicado = _favoritos_historico_publicar_evento_realtime(
                client_id,
                username,
                {
                    "updated_users": realtime_sync.get("updated_users") or [],
                    "eligible_count": len(realtime_sync.get("eligible_users") or []),
                    "item_count": len(payload.get("historico") or []),
                },
            )
    else:
        realtime_sync = _shared_sync_manual_only_payload("favoritos-historico")
    return {
        "success": True,
        "historico": payload.get("historico") or [],
        "updated_at": payload.get("updated_at"),
        "duracao_execucao_ms": payload.get("duracao_execucao_ms"),
        "finalizados_ids": payload.get("finalizados_ids") or [],
        "finalizado_em_ms": payload.get("finalizado_em_ms"),
        "realtime_sync": {
            **realtime_sync,
            "event_published": bool(evento_publicado),
        },
    }


def favoritos_historico_realtime_sync(
    req: FavoritosHistoricoRealtimeSyncRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    username = _extrair_username_do_request(request)
    if not _shared_sync_auto_enabled():
        payload = _favoritos_carregar_historico(client_id, username)
        return {
            **_shared_sync_manual_only_payload("favoritos-historico-realtime"),
            "historico": payload.get("historico") or [],
            "updated_at": payload.get("updated_at"),
        }
    resultado = _favoritos_reconciliar_historico_usuario(client_id, username)
    payload = resultado.get("payload") or _favoritos_carregar_historico(client_id, username)
    result_item = {
        "scope": FAVORITOS_HISTORICO_REALTIME_SCOPE,
        "success": True,
        "direction": "pull",
        "changed": bool(resultado.get("changed")),
        "source_count": int(resultado.get("source_count") or 0),
        "item_count": int(resultado.get("item_count") or len(payload.get("historico") or [])),
        "reason": str(req.reason or ""),
        "machine_id": str(req.machine_id or ""),
    }
    return {
        "success": True,
        "results": [result_item],
        "historico": payload.get("historico") or [],
        "updated_at": payload.get("updated_at"),
    }


def favoritos_planilhas_lojas_get(client_id: str = Depends(get_tenant_id)):
    payload = _favoritos_carregar_planilhas_lojas(client_id)
    return {
        "success": True,
        "planilhas": payload.get("planilhas") or {},
        "updated_at": payload.get("updated_at"),
    }


def favoritos_planilhas_lojas_put(
    req: FavoritosPlanilhasLojasRequest,
    client_id: str = Depends(get_tenant_id),
):
    payload = _favoritos_salvar_planilhas_lojas(client_id, req.planilhas or [])
    return {
        "success": True,
        "planilhas": payload.get("planilhas") or {},
        "atualizadas": payload.get("atualizadas") or [],
        "updated_at": payload.get("updated_at"),
    }


def favoritos_planilhas_colar_historico(
    req: FavoritosPlanilhaColarHistoricoRequest,
    client_id: str = Depends(get_tenant_id),
):
    return favoritos_colar_historico_planilha(client_id, req.loja, req.historico or {})


def favoritos_ml_listar_skus_anuncios(
    request: Request,
    loja: Optional[str] = None,
    sku: Optional[str] = None,
    apenas_lojas: bool = False,
    todas_lojas: bool = False,
    atualizar: bool = False,
    client_id: str = Depends(get_tenant_id),
):
    lojas_validas = _lojas_favoritos_com_bling_ml(client_id)
    lojas_payload = [
        {"nome": str(item.get("nome") or "").strip()}
        for item in lojas_validas
        if str(item.get("nome") or "").strip()
    ]
    if not lojas_payload:
        return {
            "success": True,
            "lojas": [],
            "loja": "",
            "skus": [],
            "total": 0,
            "total_anuncios": 0,
            "warning": "Nenhuma loja com Bling e Mercado Livre conectados foi encontrada em Integrações.",
        }

    if apenas_lojas:
        return {
            "success": True,
            "lojas": lojas_payload,
            "loja": "",
            "skus": [],
            "total": 0,
            "total_anuncios": 0,
        }

    username = _extrair_username_do_request(request)
    sku_busca = str(sku or "").strip()
    inicio_endpoint = time.perf_counter()
    cache_categoria_endpoint = "skus_anuncios"
    cache_id_endpoint = ""
    cache_key_endpoint = ""
    loja_info_cache = None
    nome_loja_cache = ""
    if not sku_busca:
        if todas_lojas:
            cache_id_endpoint = _favoritos_ml_skus_anuncios_cache_id("", True)
        else:
            loja_chave_cache = _chave_loja_favoritos(loja)
            if loja_chave_cache:
                for candidata in lojas_validas:
                    if _chave_loja_favoritos(candidata.get("nome")) == loja_chave_cache:
                        loja_info_cache = candidata
                        break
            if loja_info_cache is None:
                loja_info_cache = lojas_validas[0]
            nome_loja_cache = str((loja_info_cache or {}).get("nome") or "").strip()
            cache_id_endpoint = _favoritos_ml_skus_anuncios_cache_id(nome_loja_cache, False)
        cache_key_endpoint = f"favoritos:skus_anuncios:persist:v1:{client_id}:{username}:{cache_id_endpoint}"
        if not atualizar:
            cached_endpoint = _ml_cache_get(cache_key_endpoint, ttl=60)
            if isinstance(cached_endpoint, dict):
                logger.info(
                    "[Favoritos ML] skus-anuncios cache memoria loja=%s todas=%s skus=%s ms=%d",
                    loja or nome_loja_cache or "",
                    bool(todas_lojas),
                    len(cached_endpoint.get("skus") or []),
                    int((time.perf_counter() - inicio_endpoint) * 1000),
                )
                return cached_endpoint
            persistente = _favoritos_ml_persist_cache_read(client_id, cache_categoria_endpoint, cache_id_endpoint)
            if persistente is not None:
                payload_cache, meta_cache = persistente
                payload_cache = dict(payload_cache)
                payload_cache["lojas"] = lojas_payload
                if todas_lojas:
                    payload_cache["loja"] = "__todas"
                    payload_cache["todas_lojas"] = True
                elif nome_loja_cache:
                    payload_cache["loja"] = nome_loja_cache
                    payload_cache["todas_lojas"] = False
                payload_cache = _favoritos_ml_persist_cache_apply(payload_cache, meta_cache, hit=True)
                _ml_cache_set(cache_key_endpoint, payload_cache)
                logger.info(
                    "[Favoritos ML] skus-anuncios cache persistente loja=%s todas=%s skus=%s ms=%d",
                    payload_cache.get("loja") or "",
                    bool(payload_cache.get("todas_lojas")),
                    len(payload_cache.get("skus") or []),
                    int((time.perf_counter() - inicio_endpoint) * 1000),
                )
                return payload_cache

    def _finalizar_payload_skus_anuncios(payload: dict, etapa: str) -> dict:
        if (
            cache_key_endpoint
            and cache_id_endpoint
            and isinstance(payload, dict)
            and payload.get("success", True)
            and etapa != "stale"
            and not payload.get("stale_cache")
        ):
            meta_cache = _favoritos_ml_persist_cache_write(client_id, cache_categoria_endpoint, cache_id_endpoint, payload)
            payload = _favoritos_ml_persist_cache_apply(payload, meta_cache, hit=False, refreshed=True)
            _ml_cache_set(cache_key_endpoint, payload)
        logger.info(
            "[Favoritos ML] skus-anuncios %s loja=%s todas=%s skus=%s anuncios=%s ms=%d",
            etapa,
            payload.get("loja") if isinstance(payload, dict) else "",
            bool(payload.get("todas_lojas")) if isinstance(payload, dict) else bool(todas_lojas),
            len(payload.get("skus") or []) if isinstance(payload, dict) else 0,
            payload.get("total_anuncios") if isinstance(payload, dict) else 0,
            int((time.perf_counter() - inicio_endpoint) * 1000),
        )
        return payload

    def _payload_stale_skus_anuncios(motivo: str) -> dict | None:
        if not cache_id_endpoint:
            return None
        persistente = _favoritos_ml_persist_cache_read(client_id, cache_categoria_endpoint, cache_id_endpoint)
        if persistente is None:
            return None
        payload, meta_cache = persistente
        if not isinstance(payload, dict):
            return None
        payload = dict(payload)
        payload["lojas"] = lojas_payload
        aviso = f"Mostrando SKUs salvos porque o Mercado Livre nao respondeu agora: {motivo}"
        payload = _favoritos_ml_persist_cache_apply(payload, meta_cache, hit=True, warning=aviso)
        logger.warning("[Favoritos ML] skus-anuncios usando cache persistente loja=%s motivo=%s", payload.get("loja"), motivo)
        return payload

    if todas_lojas and not sku_busca:
        todos_skus: list[dict] = []
        total_anuncios = 0
        avisos: list[str] = []

        def _carregar_loja_favoritos(loja_info_item: dict) -> dict:
            inicio_loja = time.perf_counter()
            nome_loja_item = str(loja_info_item.get("nome") or "").strip()
            if not nome_loja_item:
                return {"loja": "", "skus": [], "total_anuncios": 0, "warning": ""}
            cfg = _obter_cfg_ml(client_id, nome_loja_item)
            t0 = time.perf_counter()
            itens, _cfg = _ml_favoritos_listar_todos_itens_ativos_loja(client_id, nome_loja_item, cfg)
            ms_itens = int((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            skus_loja = _favoritos_ml_skus_unicos_itens(itens)
            ms_skus = int((time.perf_counter() - t0) * 1000)
            for item_sku in skus_loja:
                if isinstance(item_sku, dict):
                    item_sku["loja"] = nome_loja_item
                    item_sku["loja_sync"] = nome_loja_item
            t0 = time.perf_counter()
            skus_loja = _favoritos_ml_enriquecer_estoque_cadastro(client_id, nome_loja_item, skus_loja)
            skus_loja = _favoritos_enriquecer_pesquisas_usuario(
                client_id,
                username,
                nome_loja_item,
                skus_loja,
            )
            ms_enriq = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "[Favoritos ML] skus-anuncios loja=%s anuncios=%s skus=%s ms_itens=%s ms_skus=%s ms_enriq=%s ms_total=%s",
                nome_loja_item,
                len(itens),
                len(skus_loja),
                ms_itens,
                ms_skus,
                ms_enriq,
                int((time.perf_counter() - inicio_loja) * 1000),
            )
            return {"loja": nome_loja_item, "skus": skus_loja, "total_anuncios": len(itens), "warning": ""}

        lojas_para_carregar = [item for item in lojas_validas if str(item.get("nome") or "").strip()]
        max_workers = min(4, max(1, len(lojas_para_carregar)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = {executor.submit(_carregar_loja_favoritos, loja_info_item): loja_info_item for loja_info_item in lojas_para_carregar}
            for futuro in as_completed(futuros):
                nome_loja_item = str((futuros[futuro] or {}).get("nome") or "").strip()
                try:
                    resultado = futuro.result() or {}
                    todos_skus.extend(resultado.get("skus") or [])
                    total_anuncios += int(resultado.get("total_anuncios") or 0)
                except HTTPException as exc:
                    detalhe = str(getattr(exc, "detail", "") or exc)
                    avisos.append(f"{nome_loja_item}: {detalhe}")
                    logger.warning("[Favoritos ML] Loja ignorada ao listar todos os SKUs (%s): %s", nome_loja_item, detalhe)
                except Exception as exc:
                    avisos.append(f"{nome_loja_item}: {exc}")
                    logger.exception("[Favoritos ML] Falha ao listar SKUs da loja %s no carregamento geral: %s", nome_loja_item, exc)

        todos_skus.sort(key=lambda item: (
            str(item.get("loja") or item.get("loja_sync") or "").lower(),
            str(item.get("sku") or "").lower(),
        ))
        return _finalizar_payload_skus_anuncios({
            "success": True,
            "lojas": lojas_payload,
            "loja": "__todas",
            "todas_lojas": True,
            "skus": todos_skus,
            "total": len(todos_skus),
            "total_anuncios": total_anuncios,
            "warnings": avisos,
            "warning": " | ".join(avisos[:3]) if avisos else "",
        }, "ok")

    loja_chave = _chave_loja_favoritos(loja)
    loja_info = loja_info_cache
    if loja_info is None and loja_chave:
        for candidata in lojas_validas:
            if _chave_loja_favoritos(candidata.get("nome")) == loja_chave:
                loja_info = candidata
                break
    if loja_info is None:
        loja_info = lojas_validas[0]

    nome_loja = str(loja_info.get("nome") or "").strip()
    try:
        cfg = _obter_cfg_ml(client_id, nome_loja)
        t0 = time.perf_counter()
        if sku_busca:
            itens, _cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku_busca)
        else:
            itens, _cfg = _ml_favoritos_listar_todos_itens_ativos_loja(client_id, nome_loja, cfg)
        ms_itens = int((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        skus = _favoritos_ml_skus_unicos_itens(itens)
        ms_skus = int((time.perf_counter() - t0) * 1000)
        for item_sku in skus:
            if isinstance(item_sku, dict):
                item_sku["loja"] = nome_loja
                item_sku["loja_sync"] = nome_loja
        if sku_busca:
            skus = _favoritos_ml_garantir_sku_busca(skus, sku_busca, itens)
            for item_sku in skus:
                if isinstance(item_sku, dict):
                    item_sku["loja"] = nome_loja
                    item_sku["loja_sync"] = nome_loja
        t0 = time.perf_counter()
        skus = _favoritos_ml_enriquecer_estoque_cadastro(client_id, nome_loja, skus)
        skus = _favoritos_enriquecer_pesquisas_usuario(
            client_id,
            username,
            nome_loja,
            skus,
        )
        ms_enriq = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "[Favoritos ML] skus-anuncios loja=%s busca_sku=%s anuncios=%s skus=%s ms_itens=%s ms_skus=%s ms_enriq=%s",
            nome_loja,
            bool(sku_busca),
            len(itens),
            len(skus),
            ms_itens,
            ms_skus,
            ms_enriq,
        )
        return _finalizar_payload_skus_anuncios({
            "success": True,
            "lojas": lojas_payload,
            "loja": nome_loja,
            "skus": skus,
            "total": len(skus),
            "total_anuncios": len(itens),
            "busca_sku": sku_busca,
        }, "ok")
    except HTTPException as exc:
        stale = _payload_stale_skus_anuncios(str(getattr(exc, "detail", "") or exc))
        if stale is not None:
            return _finalizar_payload_skus_anuncios(stale, "stale")
        raise
    except Exception as exc:
        stale = _payload_stale_skus_anuncios(str(exc))
        if stale is not None:
            return _finalizar_payload_skus_anuncios(stale, "stale")
        logger.exception("[Favoritos ML] Falha ao listar SKUs de anuncios da loja %s: %s", nome_loja, exc)
        raise HTTPException(status_code=500, detail=f"Erro ao listar SKUs do Mercado Livre: {exc}")


def favoritos_ml_listar_promocoes_ativas(
    loja: str,
    client_id: str = Depends(get_tenant_id),
):
    return _ml_listar_promocoes_ativas_payload(client_id, loja)


def favoritos_ml_validar_efetivacao(
    req: FavoritosValidarEfetivacaoRequest,
    client_id: str = Depends(get_tenant_id),
):
    resultados = []
    for item in req.itens or []:
        loja = str(item.loja or "").strip()
        item_id = _promo_normalizar_mlb(item.item_id)
        alvo = _favoritos_ml_listing_type_id(item.listing_type_id_alvo or item.tipo_anuncio_alvo or "")
        if not loja or not item_id:
            resultados.append({
                "ok": False,
                "item_id": item_id,
                "loja": loja,
                "message": "Loja ou MLB ausente para validar a alteracao.",
            })
            continue
        if not alvo:
            resultados.append({
                "ok": True,
                "item_id": item_id,
                "loja": loja,
                "message": "Sem troca de tipo para validar.",
            })
            continue
        try:
            cfg = _obter_cfg_ml(client_id, loja)
            validacao, _cfg = _favoritos_ml_validar_listing_type_disponivel(
                client_id,
                loja,
                cfg,
                item_id,
                alvo,
            )
            resultados.append(validacao)
        except HTTPException as exc:
            resultados.append({
                "ok": False,
                "item_id": item_id,
                "loja": loja,
                "target": alvo,
                "target_name": _favoritos_ml_nome_listing_type(alvo),
                "message": str(exc.detail or exc),
                "status_code": exc.status_code,
            })
        except Exception as exc:
            resultados.append({
                "ok": False,
                "item_id": item_id,
                "loja": loja,
                "target": alvo,
                "target_name": _favoritos_ml_nome_listing_type(alvo),
                "message": f"Erro ao validar troca Premium/Classico: {exc}",
            })
    return {"itens": resultados}


def favoritos_ml_efetivar_promocao(
    req: FavoritosEfetivarPromocaoRequest,
    client_id: str = Depends(get_tenant_id),
):
    loja = str(req.loja or "").strip()
    item_id = _promo_normalizar_mlb(req.item_id)
    campanha_id = str(req.campanha_id or "").strip()
    promotion_type = str(req.promotion_type or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN"
    preco_anuncio = _parse_float_flex(req.preco_anuncio)
    preco_promocional = _parse_float_flex(req.preco_promocional)
    if preco_promocional is None:
        preco_promocional = _parse_float_flex(req.preco_competitivo)
    percentual = _parse_float_flex(req.percentual_promocao)

    if not loja:
        raise HTTPException(status_code=400, detail="Informe a loja do Mercado Livre.")
    if not item_id:
        raise HTTPException(status_code=400, detail="Informe o MLB do anuncio.")
    if not campanha_id:
        raise HTTPException(status_code=400, detail="Informe a campanha de promocao selecionada.")
    if preco_anuncio is None or preco_anuncio <= 0:
        raise HTTPException(status_code=400, detail="Preco cheio do anuncio invalido.")
    if preco_promocional is None or preco_promocional <= 0:
        raise HTTPException(status_code=400, detail="Preco promocional simulado invalido.")

    cfg = _obter_cfg_ml(client_id, loja)
    listing_type_update = None
    listing_type_alvo = _favoritos_ml_listing_type_alvo_req(req)

    removidas, cfg = _favoritos_ml_remover_promocoes_atuais(client_id, loja, cfg, item_id, req)
    if removidas:
        time.sleep(3.0)

    if listing_type_alvo:
        listing_type_update, cfg = _favoritos_ml_atualizar_tipo_listing_item(
            client_id,
            loja,
            cfg,
            item_id,
            listing_type_alvo,
        )
        if listing_type_update and listing_type_update.get("changed"):
            time.sleep(1.0)

    preco_update, cfg = _favoritos_ml_atualizar_preco_item(client_id, loja, cfg, item_id, preco_anuncio)
    preco_confirmacao, cfg = _favoritos_ml_aguardar_preco_anuncio(
        client_id,
        loja,
        cfg,
        item_id,
        float(preco_anuncio),
        tentativas=8,
    )
    if not preco_confirmacao.get("success"):
        raise HTTPException(
            status_code=409,
            detail=(
                "O preco cheio do anuncio ainda nao ficou igual ao simulado no Mercado Livre. "
                "A promocao nao foi aplicada para evitar margem errada. "
                f"Conferencia: {preco_confirmacao}"
            ),
        )

    ok, erro, cfg = _promo_aplicar_item_participacao_ml(
        client_id,
        loja,
        cfg,
        item_id=item_id,
        promotion_id=campanha_id,
        promotion_type=promotion_type,
        deal_price=preco_promocional,
        discount_percentage=percentual,
    )
    if not ok:
        motivo = f"Preco atualizado, mas falhou ao aplicar a promocao: {erro}"
        if _favoritos_ml_falha_por_percentual_promocao(motivo):
            fallback, cfg = _favoritos_ml_aplicar_contingencia_sem_promocao(
                client_id,
                loja,
                cfg,
                item_id,
                req,
                motivo,
            )
            _cache_invalidar_loja(client_id, loja)
            return {
                "success": True,
                "message": (
                    "Campanha recusada pelo Mercado Livre; aplicado menor preco seguro com margem minima, sem competir com a base."
                    if fallback.get("fallback_sem_competir")
                    else "Campanha recusada pelo Mercado Livre; aplicado fallback sem campanha."
                ),
                "loja": loja,
                "sku": req.sku,
                "item_id": item_id,
                "promotion_id": campanha_id,
                "promotion_type": promotion_type,
                "campanha_nome": req.campanha_nome,
                "campanha_aplicada": False,
                "preco_anuncio": round(float(fallback.get("preco_anuncio_contingencia")), 2),
                "preco_promocional": None,
                "percentual_promocao": percentual,
                "promocoes_removidas": removidas,
                "listing_type_update": listing_type_update,
                "preco_update": preco_update,
                "preco_confirmacao": preco_confirmacao,
                "verificacao": None,
                **fallback,
            }
        raise HTTPException(status_code=409, detail=motivo)

    verificacao, cfg = _favoritos_ml_verificar_efetivacao(
        client_id,
        loja,
        cfg,
        item_id,
        campanha_id,
        promotion_type,
        float(preco_anuncio),
        float(preco_promocional),
    )
    if not verificacao.get("success"):
        motivo_verificacao = (
            "Mercado Livre recebeu as alteracoes, mas a conferencia ainda nao bateu com o simulado. "
            f"Verificacao: {verificacao}"
        )
        exige_fallback_margem, motivo_margem, detalhes_margem = _favoritos_ml_verificacao_exige_contingencia_por_margem(req, verificacao)
        if _favoritos_ml_falha_por_percentual_promocao(motivo_verificacao, verificacao) or exige_fallback_margem:
            fallback, cfg = _favoritos_ml_aplicar_contingencia_sem_promocao(
                client_id,
                loja,
                cfg,
                item_id,
                req,
                motivo_margem or motivo_verificacao,
            )
            _cache_invalidar_loja(client_id, loja)
            return {
                "success": True,
                "message": (
                    "Campanha recusada/nao conferida pelo Mercado Livre; aplicado menor preco seguro com margem minima, sem competir com a base."
                    if fallback.get("fallback_sem_competir")
                    else "Campanha recusada/nao conferida pelo Mercado Livre; aplicado fallback sem campanha e com margem segura."
                ),
                "loja": loja,
                "sku": req.sku,
                "item_id": item_id,
                "promotion_id": campanha_id,
                "promotion_type": promotion_type,
                "campanha_nome": req.campanha_nome,
                "campanha_aplicada": False,
                "preco_anuncio": round(float(fallback.get("preco_anuncio_contingencia")), 2),
                "preco_promocional": None,
                "percentual_promocao": percentual,
                "promocoes_removidas": removidas,
                "listing_type_update": listing_type_update,
                "preco_update": preco_update,
                "preco_confirmacao": preco_confirmacao,
                "verificacao": verificacao,
                "verificacao_margem": detalhes_margem,
                **fallback,
            }
        raise HTTPException(status_code=409, detail=motivo_verificacao)

    exige_fallback_margem, motivo_margem, detalhes_margem = _favoritos_ml_verificacao_exige_contingencia_por_margem(req, verificacao)
    if exige_fallback_margem:
        fallback, cfg = _favoritos_ml_aplicar_contingencia_sem_promocao(
            client_id,
            loja,
            cfg,
            item_id,
            req,
            motivo_margem,
        )
        _cache_invalidar_loja(client_id, loja)
        return {
            "success": True,
            "message": (
                "Preco promocional aplicado ficou abaixo da margem minima; aplicado menor preco seguro, sem competir com a base."
                if fallback.get("fallback_sem_competir")
                else "Preco promocional aplicado ficou abaixo da margem minima; aplicado fallback sem campanha e com margem segura."
            ),
            "loja": loja,
            "sku": req.sku,
            "item_id": item_id,
            "promotion_id": campanha_id,
            "promotion_type": promotion_type,
            "campanha_nome": req.campanha_nome,
            "campanha_aplicada": False,
            "preco_anuncio": round(float(fallback.get("preco_anuncio_contingencia")), 2),
            "preco_promocional": None,
            "percentual_promocao": percentual,
            "promocoes_removidas": removidas,
            "listing_type_update": listing_type_update,
            "preco_update": preco_update,
            "preco_confirmacao": preco_confirmacao,
            "verificacao": verificacao,
            "verificacao_margem": detalhes_margem,
            **fallback,
        }

    _cache_invalidar_loja(client_id, loja)
    return {
        "success": True,
        "message": "Favorito feito no Mercado Livre.",
        "loja": loja,
        "sku": req.sku,
        "item_id": item_id,
        "promotion_id": campanha_id,
        "promotion_type": promotion_type,
        "campanha_nome": req.campanha_nome,
        "campanha_aplicada": True,
        "preco_anuncio": round(float(preco_anuncio), 2),
        "preco_promocional": round(float(preco_promocional), 2),
        "percentual_promocao": percentual,
        "promocoes_removidas": removidas,
        "listing_type_update": listing_type_update,
        "preco_update": preco_update,
        "preco_confirmacao": preco_confirmacao,
        "verificacao": verificacao,
        "verificacao_margem": detalhes_margem,
    }


def favoritos_ml_listar_anuncios_sku(
    sku: str,
    loja: Optional[str] = None,
    compartilhar_sku: bool = False,
    todas_contas: bool = False,
    mlbs: Optional[str] = None,
    atualizar: bool = False,
    preco_atual: bool = True,
    client_id: str = Depends(get_tenant_id),
):
    sku_norm = _normalizar_sku_match_favoritos(sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido.")

    lojas_validas = _lojas_favoritos_com_ml(client_id)
    lojas_payload = [
        {"nome": str(item.get("nome") or "").strip()}
        for item in lojas_validas
        if str(item.get("nome") or "").strip()
    ]
    if not lojas_payload:
        return {
            "success": True,
            "lojas": [],
            "loja": "",
            "sku": sku_norm,
            "anuncios": [],
            "total": 0,
            "warning": "Nenhuma loja com Mercado Livre conectado foi encontrada em Integrações.",
        }

    loja_chave = _chave_loja_favoritos(loja)
    loja_info = None
    if loja_chave:
        for candidata in lojas_validas:
            if _chave_loja_favoritos(candidata.get("nome")) == loja_chave:
                loja_info = candidata
                break
    if loja_info is None:
        loja_info = lojas_validas[0]

    nome_loja = str(loja_info.get("nome") or "").strip()
    ids_forcados = []
    for parte in re.split(r"[,;|\s]+", str(mlbs or "")):
        item_id = re.sub(r"[^A-Z0-9]", "", str(parte or "").upper())
        if item_id.startswith("MLB") and item_id not in ids_forcados:
            ids_forcados.append(item_id)
        if len(ids_forcados) >= 80:
            break
    ids_forcados_key = ",".join(ids_forcados)
    compartilhar_efetivo = bool(compartilhar_sku and todas_contas)
    cache_categoria_endpoint = "anuncios_sku"
    cache_id_endpoint = _favoritos_ml_anuncios_sku_cache_id(nome_loja, sku_norm, compartilhar_efetivo, ids_forcados_key)
    cache_key_endpoint = (
        f"favoritos:anuncios_sku:v3:{client_id}:"
        f"{_chave_loja_favoritos(nome_loja)}:{sku_norm}:{int(compartilhar_efetivo)}:{ids_forcados_key}"
    )
    exigir_preco_atual = bool(preco_atual)
    if not atualizar and not exigir_preco_atual:
        cached_endpoint = _ml_cache_get(cache_key_endpoint, ttl=90)
        if cached_endpoint is not None:
            return cached_endpoint
        persistente = _favoritos_ml_persist_cache_read(client_id, cache_categoria_endpoint, cache_id_endpoint)
        if persistente is not None:
            payload_cache, meta_cache = persistente
            payload_cache = dict(payload_cache)
            payload_cache["lojas"] = lojas_payload
            payload_cache["loja"] = nome_loja
            payload_cache["sku"] = sku_norm
            payload_cache = _favoritos_ml_persist_cache_apply(payload_cache, meta_cache, hit=True)
            _ml_cache_set(cache_key_endpoint, payload_cache)
            return payload_cache

    def _resumir_item_ml(
        item: dict,
        price_info: Optional[dict] = None,
        shipping_data: Optional[dict] = None,
        fee_data: Optional[dict] = None,
    ) -> dict:
        item = item or {}
        price_info = price_info or _ml_montar_preco_listagem(item)
        shipping_data = shipping_data or {}
        fee_data = fee_data or {}
        item_id = str(item.get("id") or "").strip()
        permalink = str(item.get("permalink") or "").strip()
        preco_atual = price_info.get("price")
        if preco_atual in (None, ""):
            preco_atual = item.get("price")
        preco_original = price_info.get("original_price")
        standard_price = price_info.get("standard_price") or item.get("base_price") or item.get("price")
        has_promotion = bool(
            price_info.get("has_promotion")
            or price_info.get("promotion_id")
            or fee_data.get("promotion_id")
            or fee_data.get("promotion_fee_discount_applied")
            or item.get("deal_ids")
        )
        if not has_promotion:
            sale_price = item.get("sale_price")
            if isinstance(sale_price, dict):
                preco_original = preco_original or sale_price.get("regular_amount")
                preco_atual = sale_price.get("amount") or sale_price.get("price") or preco_atual
                has_promotion = bool(preco_original)
            elif sale_price not in (None, ""):
                preco_atual = sale_price
                has_promotion = True
        preco_promocional = preco_atual if has_promotion else None
        try:
            if preco_original not in (None, "", 0) and preco_atual not in (None, "") and float(preco_original) > float(preco_atual):
                has_promotion = True
                preco_promocional = preco_atual
            elif not has_promotion:
                preco_original = None
                preco_promocional = None
        except Exception:
            pass
        listing_type_id = item.get("listing_type_id") or ""
        listing_type_name = fee_data.get("listing_type_name") or _ml_nome_tipo_anuncio(listing_type_id)
        shipping_info = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
        shipping_info = dict(shipping_info or {})
        shipping_seller_cost = _to_float_safe(shipping_data.get("shipping_seller_cost"))
        shipping_cost = shipping_seller_cost
        if shipping_cost is None:
            shipping_cost = _to_float_safe(shipping_data.get("shipping_cost"))
        if shipping_cost is not None:
            shipping_info.setdefault("seller_cost", shipping_cost)
            shipping_info.setdefault("shipping_cost", shipping_cost)
        shipping_list_cost = _to_float_safe(shipping_data.get("shipping_list_cost"))
        if shipping_list_cost is not None:
            shipping_info.setdefault("list_cost", shipping_list_cost)
        elif shipping_cost is not None:
            shipping_info.setdefault("list_cost", shipping_cost)
        logistic_type = str(shipping_data.get("logistic_type") or shipping_info.get("logistic_type") or "").strip()
        shipping_mode = str(shipping_data.get("shipping_mode") or shipping_info.get("mode") or "").strip()
        desconto_pct = price_info.get("discount_pct", 0) or 0
        try:
            preco_final = preco_promocional if preco_promocional not in (None, "") else preco_atual
            if not desconto_pct and preco_original not in (None, "", 0) and preco_final not in (None, "") and float(preco_original) > float(preco_final):
                desconto_pct = round(((float(preco_original) - float(preco_final)) / float(preco_original)) * 100, 1)
        except Exception:
            desconto_pct = 0
        preco_base_exibicao = preco_original if has_promotion and preco_original not in (None, "") else (standard_price or preco_atual)
        deal_ids_raw = item.get("deal_ids") or []
        if isinstance(deal_ids_raw, str):
            deal_ids = [deal_ids_raw]
        elif isinstance(deal_ids_raw, (list, tuple, set)):
            deal_ids = [str(valor).strip() for valor in deal_ids_raw if str(valor or "").strip()]
        else:
            deal_ids = []
        promotion_id = price_info.get("promotion_id") or fee_data.get("promotion_id") or (deal_ids[0] if deal_ids else None)
        promotion_type = price_info.get("promotion_type") or fee_data.get("promotion_type")
        return {
            "id": item_id,
            "mlb": item_id,
            "titulo": str(item.get("title") or "").strip(),
            "status": str(item.get("status") or "").strip(),
            "category_id": item.get("category_id"),
            "domain_id": item.get("domain_id"),
            "preco": preco_base_exibicao,
            "price": preco_atual,
            "preco_original": preco_original,
            "original_price": preco_original,
            "standard_price": standard_price or preco_base_exibicao,
            "preco_promocional": preco_promocional,
            "promotional_price": preco_promocional,
            "sale_price": preco_promocional,
            "discount_pct": desconto_pct,
            "has_promotion": has_promotion,
            "promotion_id": promotion_id,
            "promotion_type": promotion_type,
            "promotion_name": fee_data.get("promotion_name"),
            "deal_ids": deal_ids,
            "fonte_preco": price_info.get("price_source") or "api_item",
            "estoque": item.get("available_quantity"),
            "vendidos": item.get("sold_quantity"),
            "data_criacao": item.get("date_created"),
            "link": permalink,
            "url": permalink,
            "imagem": item.get("secure_thumbnail") or item.get("thumbnail"),
            "thumbnail": item.get("secure_thumbnail") or item.get("thumbnail"),
            "seller_id": item.get("seller_id"),
            "listing_type_id": listing_type_id,
            "listing_type_name": listing_type_name,
            "tipo_anuncio": listing_type_name,
            "shipping": shipping_info,
            "shipping_cost": shipping_cost,
            "shipping_text": shipping_data.get("shipping_text"),
            "shipping_buyer_cost": shipping_data.get("shipping_buyer_cost"),
            "shipping_buyer_text": shipping_data.get("shipping_buyer_text"),
            "shipping_list_cost": shipping_data.get("shipping_list_cost"),
            "shipping_base_cost": shipping_data.get("shipping_base_cost"),
            "shipping_seller_cost": shipping_seller_cost,
            "shipping_breakdown": shipping_data.get("shipping_breakdown"),
            "free_shipping": shipping_data.get("free_shipping", shipping_info.get("free_shipping")),
            "frete_ml": shipping_cost,
            "frete_ml_text": shipping_data.get("shipping_text") if shipping_cost is not None else None,
            "ad_cost": fee_data.get("ad_cost"),
            "ad_cost_text": fee_data.get("ad_cost_text"),
            "tarifa_ml": fee_data.get("ad_cost"),
            "tarifa_ml_text": fee_data.get("ad_cost_text"),
            "ad_cost_original": fee_data.get("ad_cost_original"),
            "ad_cost_original_text": fee_data.get("ad_cost_original_text"),
            "promotion_fee_discount": fee_data.get("promotion_fee_discount"),
            "promotion_fee_discount_text": fee_data.get("promotion_fee_discount_text"),
            "promotion_fee_discount_applied": fee_data.get("promotion_fee_discount_applied"),
            "promotion_fee_charged": fee_data.get("promotion_fee_charged"),
            "promotion_fee_charged_text": fee_data.get("promotion_fee_charged_text"),
            "promotion_fee_discount_source": fee_data.get("promotion_fee_discount_source"),
            "promotion_fee_base": fee_data.get("promotion_fee_base"),
            "promotion_fee_base_text": fee_data.get("promotion_fee_base_text"),
            "promotion_fee_ml": fee_data.get("promotion_fee_ml"),
            "promotion_fee_ml_text": fee_data.get("promotion_fee_ml_text"),
            "fixed_fee_amount": fee_data.get("fixed_fee_amount"),
            "fixed_fee_text": fee_data.get("fixed_fee_text"),
            "listing_fee_amount": fee_data.get("listing_fee_amount"),
            "listing_fee_text": fee_data.get("listing_fee_text"),
            "sale_fee_pct": fee_data.get("sale_fee_pct"),
            "meli_fee_pct": fee_data.get("meli_fee_pct"),
            "financing_fee_pct": fee_data.get("financing_fee_pct"),
            "fee_breakdown": fee_data.get("fee_breakdown"),
            "logistic_type": logistic_type,
            "shipping_mode": shipping_mode,
            "is_full": logistic_type.lower() == "fulfillment",
            "sku": ", ".join(sorted(_ml_favoritos_extrair_skus_item(item))) or sku_norm,
        }

    def _carregar_anuncios_sku_loja(nome_loja_consulta: str, ids_diretos: Optional[list[str]] = None) -> dict:
        cfg = _obter_cfg_ml(client_id, nome_loja_consulta)
        ids_diretos = list(dict.fromkeys([
            str(item_id or "").strip().upper()
            for item_id in (ids_diretos or [])
            if str(item_id or "").strip().upper().startswith("MLB")
        ]))
        if ids_diretos:
            itens, cfg = _ml_favoritos_buscar_itens_batch(client_id, nome_loja_consulta, cfg, ids_diretos)
            if not itens:
                itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja_consulta, cfg, sku_norm)
        else:
            itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja_consulta, cfg, sku_norm)
        custos_por_sku, impostos_por_sku = _carregar_custos_impostos_cadastro_por_sku_loja(client_id, nome_loja_consulta)

        def _montar_anuncio_item(item: dict) -> dict | None:
            if not isinstance(item, dict):
                return None
            item_id = str(item.get("id") or "").strip()
            price_info = _ml_montar_preco_listagem(item)
            shipping_data = {}
            fee_data = {}
            cfg_item = dict(cfg or {})
            if item_id:
                def _buscar_preco_item():
                    cfg_preco = dict(cfg_item or {})
                    return _ml_obter_preco_detalhado(
                        client_id,
                        nome_loja_consulta,
                        cfg_preco,
                        item_id,
                        fallback_price=item.get("price"),
                        request_fn=_ml_favoritos_api_request,
                        consultar_sale_price_sempre=True,
                    )

                def _buscar_frete_item():
                    cfg_frete = dict(cfg_item or {})
                    return _ml_obter_frete_detalhado(
                        client_id,
                        nome_loja_consulta,
                        cfg_frete,
                        item_id,
                        item.get("shipping") or {},
                        request_fn=_ml_favoritos_api_request,
                    )

                with ThreadPoolExecutor(max_workers=2) as executor_detalhes:
                    futuro_preco = executor_detalhes.submit(_buscar_preco_item)
                    futuro_frete = executor_detalhes.submit(_buscar_frete_item)
                    try:
                        price_info, _cfg_preco = futuro_preco.result(timeout=18)
                    except Exception as exc:
                        logger.warning("[Favoritos ML] Falha ao buscar preco detalhado do item %s: %s", item_id, exc)
                    try:
                        shipping_data, _cfg_frete = futuro_frete.result(timeout=18)
                    except Exception as exc:
                        logger.warning("[Favoritos ML] Falha ao buscar frete detalhado do item %s: %s", item_id, exc)
                try:
                    item_fee = dict(item)
                    if price_info.get("price") not in (None, ""):
                        item_fee["price"] = price_info.get("price")
                    fee_data, cfg_item = _ml_obter_taxas_anuncio(
                        client_id,
                        nome_loja_consulta,
                        cfg_item,
                        item_fee,
                        request_fn=_ml_favoritos_api_request,
                    )
                    promo_fee_info, cfg_item = _ml_obter_desconto_taxa_promocao_item(
                        client_id,
                        nome_loja_consulta,
                        cfg_item,
                        item_id,
                        item=item_fee,
                        price_info=price_info,
                        fee_data=fee_data,
                        deal_ids=item.get("deal_ids") or [],
                        request_fn=_ml_favoritos_api_request,
                    )
                    fee_data = _ml_aplicar_desconto_taxa_promocao_fee_data(fee_data, promo_fee_info)
                except Exception as exc:
                    logger.warning("[Favoritos ML] Falha ao buscar tarifa do item %s: %s", item_id, exc)
            anuncio = _resumir_item_ml(item, price_info=price_info, shipping_data=shipping_data, fee_data=fee_data)
            anuncio["loja"] = nome_loja_consulta
            anuncio["loja_sync"] = nome_loja_consulta
            anuncio["loja_conta"] = nome_loja_consulta
            return anuncio

        anuncios = []
        if len(itens) > 1:
            try:
                max_workers_itens = int(float(str(os.getenv("ML_FAVORITOS_ITEM_DETAIL_WORKERS", "8") or "8").replace(",", ".")))
            except Exception:
                max_workers_itens = 8
            max_workers_itens = min(max(1, max_workers_itens), len(itens))
            with ThreadPoolExecutor(max_workers=max_workers_itens) as executor:
                futuros_itens = [executor.submit(_montar_anuncio_item, item) for item in itens]
                for futuro in as_completed(futuros_itens):
                    try:
                        anuncio = futuro.result()
                        if anuncio:
                            anuncios.append(anuncio)
                    except Exception as exc:
                        logger.warning("[Favoritos ML] Falha ao montar detalhe do SKU %s na loja %s: %s", sku_norm, nome_loja_consulta, exc)
        else:
            for item in itens:
                anuncio = _montar_anuncio_item(item)
                if anuncio:
                    anuncios.append(anuncio)

        _favoritos_completar_frete_pausados_por_sku(anuncios, sku_norm)
        for anuncio in anuncios:
            _favoritos_aplicar_margem_anuncio_ml(anuncio, sku_norm, custos_por_sku, impostos_por_sku)
        return {
            "loja": nome_loja_consulta,
            "anuncios": anuncios,
            "total_itens": len(itens),
        }

    try:
        lojas_para_consulta: list[str] = []
        if compartilhar_efetivo:
            candidatos = [loja_info] + [item for item in lojas_validas if item is not loja_info]
        else:
            candidatos = [loja_info]
        vistos_lojas: set[str] = set()
        for candidata in candidatos:
            nome_candidata = str((candidata or {}).get("nome") or "").strip()
            chave_candidata = _chave_loja_favoritos(nome_candidata)
            if not nome_candidata or chave_candidata in vistos_lojas:
                continue
            vistos_lojas.add(chave_candidata)
            lojas_para_consulta.append(nome_candidata)

        resultados: list[dict] = []
        avisos: list[str] = []
        loja_forcada_chave = _chave_loja_favoritos(nome_loja)
        if len(lojas_para_consulta) <= 1:
            nome_consulta = lojas_para_consulta[0] if lojas_para_consulta else nome_loja
            ids_consulta = ids_forcados if _chave_loja_favoritos(nome_consulta) == loja_forcada_chave else []
            resultados.append(_carregar_anuncios_sku_loja(nome_consulta, ids_consulta))
        else:
            max_workers = min(4, len(lojas_para_consulta))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futuros = {
                    executor.submit(
                        _carregar_anuncios_sku_loja,
                        nome,
                        ids_forcados if _chave_loja_favoritos(nome) == loja_forcada_chave else [],
                    ): nome
                    for nome in lojas_para_consulta
                }
                for futuro in as_completed(futuros):
                    nome_consulta = futuros[futuro]
                    try:
                        resultados.append(futuro.result() or {"loja": nome_consulta, "anuncios": [], "total_itens": 0})
                    except Exception as exc:
                        avisos.append(f"{nome_consulta}: {exc}")
                        logger.warning("[Favoritos ML] Falha ao compartilhar SKU %s com loja %s: %s", sku_norm, nome_consulta, exc)

        anuncios = []
        lojas_com_sku = []
        total_itens = 0
        for resultado in resultados:
            total_loja = int(resultado.get("total_itens") or 0)
            total_itens += total_loja
            anuncios_loja = [item for item in (resultado.get("anuncios") or []) if isinstance(item, dict)]
            if total_loja or anuncios_loja:
                loja_resultado = str(resultado.get("loja") or "").strip()
                if loja_resultado:
                    lojas_com_sku.append(loja_resultado)
            anuncios.extend(anuncios_loja)

        ordem_lojas = {nome: idx for idx, nome in enumerate(lojas_para_consulta)}
        lojas_com_sku = list(dict.fromkeys(sorted(lojas_com_sku, key=lambda nome: ordem_lojas.get(nome, 9999))))
        anuncios.sort(key=lambda item: (
            ordem_lojas.get(str(item.get("loja") or ""), 9999),
            str(item.get("titulo") or "").lower(),
            str(item.get("id") or ""),
        ))
        payload = {
            "success": True,
            "lojas": lojas_payload,
            "loja": nome_loja,
            "sku": sku_norm,
            "compartilhado_por_sku": bool(compartilhar_efetivo and len(lojas_com_sku) > 1),
            "lojas_com_sku": lojas_com_sku,
            "anuncios": anuncios,
            "total": len(anuncios),
            "total_itens_ml": total_itens,
            "warnings": avisos,
            "warning": " | ".join(avisos[:3]) if avisos else "",
            "preco_atual_obrigatorio": exigir_preco_atual,
        }
        meta_cache = _favoritos_ml_persist_cache_write(client_id, cache_categoria_endpoint, cache_id_endpoint, payload)
        payload = _favoritos_ml_persist_cache_apply(payload, meta_cache, hit=False, refreshed=True)
        _ml_cache_set(cache_key_endpoint, payload)
        return payload
    except HTTPException as exc:
        if exigir_preco_atual:
            raise
        persistente = _favoritos_ml_persist_cache_read(client_id, cache_categoria_endpoint, cache_id_endpoint)
        if persistente is not None:
            payload_cache, meta_cache = persistente
            aviso = f"Mostrando anuncios salvos porque o Mercado Livre nao respondeu agora: {getattr(exc, 'detail', '') or exc}"
            payload_cache = dict(payload_cache)
            payload_cache["lojas"] = lojas_payload
            payload_cache["loja"] = nome_loja
            payload_cache["sku"] = sku_norm
            return _favoritos_ml_persist_cache_apply(payload_cache, meta_cache, hit=True, warning=aviso)
        raise
    except Exception as exc:
        if exigir_preco_atual:
            logger.exception("[Favoritos ML] Falha ao listar anuncios atuais do SKU %s loja %s: %s", sku_norm, nome_loja, exc)
            raise HTTPException(status_code=500, detail=f"Erro ao consultar preco atual dos anuncios do SKU no Mercado Livre: {exc}")
        persistente = _favoritos_ml_persist_cache_read(client_id, cache_categoria_endpoint, cache_id_endpoint)
        if persistente is not None:
            payload_cache, meta_cache = persistente
            payload_cache = dict(payload_cache)
            payload_cache["lojas"] = lojas_payload
            payload_cache["loja"] = nome_loja
            payload_cache["sku"] = sku_norm
            aviso = f"Mostrando anuncios salvos porque o Mercado Livre nao respondeu agora: {exc}"
            return _favoritos_ml_persist_cache_apply(payload_cache, meta_cache, hit=True, warning=aviso)
        logger.exception("[Favoritos ML] Falha ao listar anuncios do SKU %s loja %s: %s", sku_norm, nome_loja, exc)
        raise HTTPException(status_code=500, detail=f"Erro ao listar anúncios do SKU no Mercado Livre: {exc}")


def favoritos_salvar_pesquisas_sku(
    req: FavoritosSkuPesquisaRequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    sku_norm = _normalizar_sku_mes(req.sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido.")

    loja_nome = str(req.loja or "").strip() or "Todas as lojas"

    try:
        atualizados = _favoritos_salvar_pesquisas_usuario_batch(
            client_id,
            _extrair_username_do_request(request),
            loja_nome,
            [{
                "sku": sku_norm,
                "produto": str(req.produto or "").strip(),
                "pesquisa_1": str(req.pesquisa_1 or "").strip(),
                "pesquisa_2": str(req.pesquisa_2 or "").strip(),
                "pesquisa_3": str(req.pesquisa_3 or "").strip(),
            }],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar pesquisas por SKU: {exc}")

    retorno = atualizados[0] if atualizados else {
        "sku": sku_norm,
        "pesquisa_1": str(req.pesquisa_1 or "").strip(),
        "pesquisa_2": str(req.pesquisa_2 or "").strip(),
        "pesquisa_3": str(req.pesquisa_3 or "").strip(),
    }

    return {
        "success": True,
        "sku": retorno.get("sku") or sku_norm,
        "loja": retorno.get("loja") or loja_nome,
        "pesquisa_1": retorno.get("pesquisa_1") or "",
        "pesquisa_2": retorno.get("pesquisa_2") or "",
        "pesquisa_3": retorno.get("pesquisa_3") or "",
    }


def favoritos_gerar_pesquisas_sku_ia(
    req: FavoritosSkusPesquisaIARequest,
    request: Request,
    client_id: str = Depends(get_tenant_id),
):
    if not req.itens:
        raise HTTPException(status_code=400, detail="Lista de itens vazia.")
    loja_nome = str(req.loja or "").strip() or "Todas as lojas"

    payload: list[dict] = []
    vistos: set[str] = set()
    for item in req.itens:
        item_dict = item.model_dump() if hasattr(item, "model_dump") else (item.dict() if hasattr(item, "dict") else (item or {}))
        sku_norm = _normalizar_sku_match_favoritos(str((item_dict or {}).get("sku") or "")).strip()
        if not sku_norm or sku_norm.lower() in vistos:
            continue
        vistos.add(sku_norm.lower())
        produto = str((item_dict or {}).get("produto") or "").strip()
        titulo = str((item_dict or {}).get("titulo") or produto).strip()
        descricao = str((item_dict or {}).get("descricao") or "").strip()
        payload.append({
            "sku": sku_norm,
            "titulo": titulo,
            "produto": titulo or produto,
            "descricao": descricao,
            "pesquisa_1": str((item_dict or {}).get("pesquisa_1") or "").strip(),
            "pesquisa_2": str((item_dict or {}).get("pesquisa_2") or "").strip(),
            "pesquisa_3": str((item_dict or {}).get("pesquisa_3") or "").strip(),
        })

    if not payload:
        raise HTTPException(status_code=400, detail="Nenhum SKU válido encontrado.")

    try:
        sugestoes = _favoritos_gerar_pesquisas_ia(
            client_id=client_id,
            itens=payload,
            limite_por_chamada=(req.chunk_tamanho or 24),
            model=_ia_modelo_favoritos_configurado(),
        )
    except Exception as exc:
        logger.exception("[Favoritos IA] Falha ao gerar pesquisas para lotes: %s", exc)
        raise HTTPException(status_code=500, detail=f"Erro ao gerar pesquisas com IA: {exc}")

    username = _extrair_username_do_request(request)
    pesquisas_salvas = (_favoritos_carregar_pesquisas_usuario(client_id, username).get("pesquisas") or {})
    for item in sugestoes:
        sku_norm = _normalizar_sku_match_favoritos(str(item.get("sku") or "")).strip()
        if not sku_norm:
            continue
        atual = pesquisas_salvas.get(_favoritos_chave_pesquisa_usuario("", sku_norm)) or {}
        pesquisa1_existente = str(atual.get("pesquisa_1") or "").strip()
        pesquisa2_existente = str(atual.get("pesquisa_2") or "").strip()
        pesquisa3_existente = str(atual.get("pesquisa_3") or "").strip()
        if pesquisa1_existente and (not req.sobrescrever or not str(item.get("pesquisa_1") or "").strip()):
            item["pesquisa_1"] = pesquisa1_existente
        if pesquisa2_existente and (not req.sobrescrever or not str(item.get("pesquisa_2") or "").strip()):
            item["pesquisa_2"] = pesquisa2_existente
        if pesquisa3_existente and (not req.sobrescrever or not str(item.get("pesquisa_3") or "").strip()):
            item["pesquisa_3"] = pesquisa3_existente

    sugestoes_para_salvar = [
        item for item in sugestoes
        if str(item.get("pesquisa_1") or "").strip()
        or str(item.get("pesquisa_2") or "").strip()
        or str(item.get("pesquisa_3") or "").strip()
    ]

    try:
        atualizados = _favoritos_salvar_pesquisas_usuario_batch(
            client_id=client_id,
            username=username,
            loja=loja_nome,
            itens=sugestoes_para_salvar,
        ) if sugestoes_para_salvar else []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar pesquisas por SKU: {exc}")

    return {
        "success": True,
        "loja": str(req.loja or "").strip() or None,
        "total": len(sugestoes),
        "resultados": atualizados,
        "erros": [
            {"sku": item.get("sku"), "erro": item.get("erro")}
            for item in sugestoes
            if str(item.get("erro") or "").strip()
        ],
    }


def favoritos_filtrar_ranking_ia(
    req: FavoritosRankingIARequest,
    client_id: str = Depends(get_tenant_id),
):
    sku_norm = _normalizar_sku_match_favoritos(str(req.sku or "")).strip()
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido.")

    anuncios = [item for item in (req.anuncios or []) if isinstance(item, dict)]
    if not anuncios:
        return {
            "success": True,
            "sku": sku_norm,
            "manter_ids": [],
            "remover_ids": [],
            "removidos": [],
        }

    limite = max(1, min(int(req.max_anuncios or 160), 220))
    try:
        resultado = _favoritos_ranking_filtrar_com_ia(
            client_id=client_id,
            sku=sku_norm,
            titulo=str(req.titulo or "").strip(),
            descricao=str(req.descricao or "").strip(),
            pesquisas=req.pesquisas or [],
            anuncios=anuncios[:limite],
            meus_anuncios=req.meus_anuncios or [],
            max_confirmados=req.max_confirmados,
            usar_imagem=req.usar_imagem,
            model=_ia_modelo_favoritos_configurado(),
        )
    except Exception as exc:
        logger.exception("[Favoritos IA] Falha ao filtrar ranking do SKU %s: %s", sku_norm, exc)
        raise HTTPException(status_code=500, detail=f"Erro ao filtrar ranking com IA: {exc}")

    return {
        "success": True,
        "sku": sku_norm,
        "max_confirmados": req.max_confirmados,
        **resultado,
    }


def favoritos_buscar_descricao_sku(
    sku: str,
    loja: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    sku_norm = _normalizar_sku_match_favoritos(sku)
    if not sku_norm:
        raise HTTPException(status_code=400, detail="SKU invalido.")

    lojas_validas = _lojas_favoritos_com_bling_ml(client_id)
    if not lojas_validas:
        raise HTTPException(status_code=400, detail="Nenhuma loja com Bling e Mercado Livre conectados foi encontrada.")

    loja_chave = _chave_loja_favoritos(loja)
    if loja_chave and loja_chave not in {"todas", "todasaslojas", "__todas"}:
        lojas_busca = [loja_info for loja_info in lojas_validas if _chave_loja_favoritos(loja_info.get("nome")) == loja_chave]
        if not lojas_busca:
            raise HTTPException(status_code=400, detail="A loja informada não está com Bling e Mercado Livre conectados.")
    else:
        lojas_busca = lojas_validas

    melhor_resultado: dict | None = None
    encontrou_item = False
    erros: list[str] = []

    for loja_info in lojas_busca:
        nome_loja = str(loja_info.get("nome") or "").strip()
        if not nome_loja:
            continue
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            itens, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku_norm)
            for item in itens:
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                descricao_info = _ml_favoritos_obter_descricao_item(client_id, nome_loja, cfg, item_id, item)
                descricao = str(descricao_info.get("descricao") or "").strip()
                erro = str(descricao_info.get("erro") or "").strip()
                if erro:
                    erros.append(erro)
                encontrou_item = True
                resultado_base = {
                    "success": bool(descricao) and not erro,
                    "sku": sku_norm,
                    "status": "ok" if descricao else ("erro" if erro else "sem_descricao"),
                    "loja": nome_loja,
                    "item_id": item_id,
                    "titulo": item.get("title") or "",
                    "permalink": item.get("permalink") or f"https://produto.mercadolivre.com.br/{item_id}",
                    "descricao": descricao,
                    "description_info": descricao_info.get("description_info", {}),
                    "erro": erro,
                    "fonte": "mercadolivre_api",
                }
                if descricao:
                    _favoritos_salvar_descricao_cadastro(client_id, sku_norm, descricao)
                    return resultado_base
                if melhor_resultado is None:
                    melhor_resultado = resultado_base
        except HTTPException as exc:
            erros.append(str(exc.detail or exc))
        except Exception as exc:
            logger.exception(f"[FAVORITOS SKU] Falha ao buscar descrição do SKU {sku_norm} na loja {nome_loja}: {exc}")
            erros.append(str(exc))

    if melhor_resultado is not None:
        erro_resultado = str(melhor_resultado.get("erro") or "").strip()
        if erro_resultado:
            melhor_resultado["success"] = False
            melhor_resultado["status"] = "erro"
            melhor_resultado["message"] = "Nao consegui consultar a descricao do Mercado Livre agora."
        else:
            melhor_resultado["message"] = "Anuncio encontrado, mas sem descricao cadastrada no Mercado Livre."
        return melhor_resultado

    if encontrou_item:
        detalhe = f"Nenhum anuncio com descricao foi encontrado para o SKU {sku_norm} nas lojas conectadas."
    else:
        detalhe = f"Nenhum anuncio ativo com o SKU {sku_norm} foi encontrado nas lojas conectadas."
    if erros:
        detalhe += " Detalhes: " + " | ".join(erros[:3])
    raise HTTPException(status_code=404, detail=detalhe)


def favoritos_buscar_descricoes_skus(req: FavoritosSkuDescricoesRequest, client_id: str = Depends(get_tenant_id)):
    skus_originais = [str(sku or "").strip() for sku in (req.skus or []) if str(sku or "").strip()]
    skus_originais = list(dict.fromkeys(skus_originais))[:1000]
    if not skus_originais:
        raise HTTPException(status_code=400, detail="Nenhum SKU informado.")

    sku_original_por_norm: dict[str, str] = {}
    for sku in skus_originais:
        sku_norm = _normalizar_sku_match_favoritos(sku)
        if sku_norm and sku_norm not in sku_original_por_norm:
            sku_original_por_norm[sku_norm] = sku
    alvos_norm = set(sku_original_por_norm.keys())
    if not alvos_norm:
        raise HTTPException(status_code=400, detail="Nenhum SKU valido informado.")

    item_ids_por_sku_norm: dict[str, list[str]] = {}
    for sku_raw, ids_raw in (req.item_ids_por_sku or {}).items():
        sku_norm = _normalizar_sku_match_favoritos(str(sku_raw or ""))
        if not sku_norm or sku_norm not in alvos_norm:
            continue
        ids_limpos: list[str] = []
        for item_raw in ids_raw or []:
            item_id = _extrair_item_id(str(item_raw or "")) or str(item_raw or "").strip()
            if item_id and item_id not in ids_limpos:
                ids_limpos.append(item_id)
            if len(ids_limpos) >= 8:
                break
        if ids_limpos:
            item_ids_por_sku_norm[sku_norm] = ids_limpos

    lojas_validas = _lojas_favoritos_com_bling_ml(client_id)
    loja_chave = _chave_loja_favoritos(req.loja)
    if loja_chave and loja_chave not in {"todas", "todasaslojas", "__todas"}:
        lojas_busca = [loja_info for loja_info in lojas_validas if _chave_loja_favoritos(loja_info.get("nome")) == loja_chave]
    else:
        lojas_busca = lojas_validas
    if not lojas_busca:
        raise HTTPException(status_code=400, detail="Nenhuma loja conectada ao Mercado Livre encontrada para buscar descrições.")

    resultados: dict[str, dict] = {
        sku_original: {
            "success": False,
            "sku": sku_original,
            "status": "pendente",
            "descricao": "",
        }
        for sku_original in skus_originais
    }
    pendentes_norm = set(alvos_norm)
    candidatos_por_sku: dict[str, int] = {sku_norm: 0 for sku_norm in alvos_norm}
    erros_por_sku: dict[str, list[str]] = {sku_norm: [] for sku_norm in alvos_norm}
    erros: list[str] = []

    for loja_info in lojas_busca:
        if not pendentes_norm:
            break
        nome_loja = str(loja_info.get("nome") or "").strip()
        if not nome_loja:
            continue
        try:
            cfg = _obter_cfg_ml(client_id, nome_loja)
            item_para_skus_direto: dict[str, list[str]] = {}
            for sku_norm in list(pendentes_norm):
                for item_id in item_ids_por_sku_norm.get(sku_norm, []):
                    vinculados = item_para_skus_direto.setdefault(item_id, [])
                    if sku_norm not in vinculados:
                        vinculados.append(sku_norm)
                        candidatos_por_sku[sku_norm] = candidatos_por_sku.get(sku_norm, 0) + 1

            if item_para_skus_direto:
                with ThreadPoolExecutor(max_workers=min(8, max(1, len(item_para_skus_direto)))) as executor:
                    future_map = {
                        executor.submit(
                            _favoritos_obter_descricao_item_controlada,
                            client_id,
                            nome_loja,
                            dict(cfg),
                            item_id,
                            None,
                            force_refresh=bool(req.force_refresh),
                        ): (item_id, skus_vinculados)
                        for item_id, skus_vinculados in item_para_skus_direto.items()
                    }
                    for future in as_completed(future_map):
                        item_id, skus_vinculados = future_map[future]
                        try:
                            desc_info = future.result() or {}
                        except Exception as exc:
                            desc_info = {"descricao": "", "description_info": {}, "erro": str(exc)}

                        descricao = str(desc_info.get("descricao") or "").strip()
                        erro = str(desc_info.get("erro") or "").strip()
                        payload_base = {
                            "success": bool(descricao) and not erro,
                            "status": "ok" if descricao else ("erro" if erro else "sem_descricao"),
                            "loja": nome_loja,
                            "item_id": item_id,
                            "titulo": "",
                            "permalink": f"https://produto.mercadolivre.com.br/{item_id}",
                            "descricao": descricao,
                            "erro": erro,
                            "fonte": "mercadolivre_api_item_id",
                            "cache_hit": bool(desc_info.get("cache_hit")),
                            "cache": desc_info.get("cache") or {},
                        }

                        for sku_norm in skus_vinculados:
                            if sku_norm not in pendentes_norm:
                                continue
                            sku_original = sku_original_por_norm.get(sku_norm)
                            if not sku_original:
                                continue
                            if erro:
                                bucket = erros_por_sku.setdefault(sku_norm, [])
                                if erro not in bucket:
                                    bucket.append(erro)
                            if descricao:
                                _favoritos_salvar_descricao_cadastro(client_id, sku_norm, descricao)
                                resultados[sku_original] = {"sku": sku_original, **payload_base}
                                pendentes_norm.discard(sku_norm)

            if not pendentes_norm:
                break

            usou_mapa_ativo = len(pendentes_norm) >= 20
            if usou_mapa_ativo:
                itens_por_sku, cfg = _ml_favoritos_mapear_itens_ativos_por_skus(
                    client_id,
                    nome_loja,
                    cfg,
                    pendentes_norm,
                    max_por_sku=3,
                )
            else:
                itens_por_sku, cfg = _ml_favoritos_buscar_primeiros_itens_por_skus(
                    client_id,
                    nome_loja,
                    cfg,
                    pendentes_norm,
                    max_por_sku=3,
                )

            # A varredura em lote é rápida, mas nem sempre cobre todos os resultados
            # (o ML limita a janela de busca). Para não marcar falso "não encontrado",
            # completa os SKUs restantes com busca direta por seller_sku.
            if usou_mapa_ativo:
                sem_match = {sku_norm for sku_norm in pendentes_norm if not itens_por_sku.get(sku_norm)}
                if sem_match:
                    itens_diretos, cfg = _ml_favoritos_buscar_primeiros_itens_por_skus(
                        client_id,
                        nome_loja,
                        cfg,
                        sem_match,
                        max_por_sku=3,
                    )
                    for sku_norm, itens in (itens_diretos or {}).items():
                        if itens and not itens_por_sku.get(sku_norm):
                            itens_por_sku[sku_norm] = itens
            if not itens_por_sku:
                continue

            item_para_skus: dict[str, list[str]] = {}
            item_por_id: dict[str, dict] = {}
            for sku_norm, itens in itens_por_sku.items():
                if sku_norm not in pendentes_norm:
                    continue
                for item in itens:
                    item_id = str(item.get("id") or "").strip()
                    if not item_id:
                        continue
                    skus_vinculados = item_para_skus.setdefault(item_id, [])
                    if sku_norm not in skus_vinculados:
                        skus_vinculados.append(sku_norm)
                        item_por_id[item_id] = item
                        candidatos_por_sku[sku_norm] = candidatos_por_sku.get(sku_norm, 0) + 1

            # Completa skus sem match inicial com busca direcionada por SKU.
            if not usou_mapa_ativo:
                for sku_norm in list(pendentes_norm):
                    if sku_norm in itens_por_sku:
                        continue
                    try:
                        itens_direto, cfg = _ml_favoritos_buscar_itens_por_sku(client_id, nome_loja, cfg, sku_norm)
                    except HTTPException:
                        continue
                    for item in itens_direto:
                        item_id = str(item.get("id") or "").strip()
                        if not item_id:
                            continue
                        item_para_skus.setdefault(item_id, [])
                        if sku_norm in item_para_skus[item_id]:
                            continue
                        item_para_skus[item_id].append(sku_norm)
                        item_por_id[item_id] = item
                        candidatos_por_sku[sku_norm] = candidatos_por_sku.get(sku_norm, 0) + 1
                        break

            if not item_para_skus:
                continue

            with ThreadPoolExecutor(max_workers=min(8, max(1, len(item_para_skus)))) as executor:
                future_map = {
                    executor.submit(
                        _favoritos_obter_descricao_item_controlada,
                        client_id,
                        nome_loja,
                        dict(cfg),
                        item_id,
                        item_por_id.get(item_id, {}),
                        rapida=True,
                        force_refresh=bool(req.force_refresh),
                    ): (item_id, skus_vinculados)
                    for item_id, skus_vinculados in item_para_skus.items()
                }
                for future in as_completed(future_map):
                    item_id, skus_vinculados = future_map[future]
                    try:
                        desc_info = future.result() or {}
                    except Exception as exc:
                        desc_info = {"descricao": "", "description_info": {}, "erro": str(exc)}

                    descricao = str(desc_info.get("descricao") or "").strip()
                    erro = str(desc_info.get("erro") or "").strip()
                    item = item_por_id.get(item_id, {})
                    item_titulo = item.get("title") or item.get("name") or ""
                    item_permalink = item.get("permalink") or f"https://produto.mercadolivre.com.br/{item_id}"
                    payload_base = {
                        "success": bool(descricao) and not erro,
                        "status": "ok" if descricao else ("erro" if erro else "sem_descricao"),
                        "loja": nome_loja,
                        "item_id": item_id,
                        "titulo": item_titulo,
                        "permalink": item_permalink,
                        "descricao": descricao,
                        "erro": erro,
                        "fonte": "mercadolivre_api",
                        "cache_hit": bool(desc_info.get("cache_hit")),
                        "cache": desc_info.get("cache") or {},
                    }

                    for sku_norm in skus_vinculados:
                        if sku_norm not in pendentes_norm:
                            continue
                        sku_original = sku_original_por_norm.get(sku_norm)
                        if not sku_original:
                            continue
                        if erro:
                            bucket = erros_por_sku.setdefault(sku_norm, [])
                            if erro not in bucket:
                                bucket.append(erro)
                        if descricao:
                            _favoritos_salvar_descricao_cadastro(client_id, sku_norm, descricao)
                            resultados[sku_original] = {"sku": sku_original, **payload_base}
                            pendentes_norm.discard(sku_norm)
        except HTTPException as exc:
            erros.append(f"{nome_loja}: {exc.detail}")
        except Exception as exc:
            logger.exception(f"[FAVORITOS SKU] Falha ao buscar descrições em lote na loja {nome_loja}: {exc}")
            erros.append(f"{nome_loja}: {exc}")
    for sku_norm in pendentes_norm:
        sku_original = sku_original_por_norm.get(sku_norm)
        if sku_original and resultados.get(sku_original, {}).get("status") == "pendente":
            erros_sku = [str(erro or "").strip() for erro in erros_por_sku.get(sku_norm, []) if str(erro or "").strip()]
            if erros_sku:
                resultados[sku_original] = {
                    "success": False,
                    "sku": sku_original,
                    "status": "erro",
                    "descricao": "",
                    "erro": "Nao consegui consultar a descricao do Mercado Livre agora. " + " | ".join(erros_sku[:2]),
                }
            elif candidatos_por_sku.get(sku_norm, 0) > 0:
                resultados[sku_original] = {
                    "success": False,
                    "sku": sku_original,
                    "status": "sem_descricao",
                    "descricao": "",
                    "erro": "Anuncio encontrado, mas sem descricao cadastrada no Mercado Livre.",
                }
            else:
                resultados[sku_original] = {
                    "success": False,
                    "sku": sku_original,
                    "status": "nao_encontrado",
                    "descricao": "",
                    "erro": "Nenhum anuncio ativo encontrado para este SKU.",
                }

    return {
        "success": True,
        "results": list(resultados.values()),
        "total": len(resultados),
        "cache_hits": sum(1 for item in resultados.values() if item.get("cache_hit")),
        "erros": erros[:5],
    }


def _favoritos_ml_primeira_pagina_sync(req: FavoritosPrimeiraPaginaRequest, client_id: str):
    termo = (req.termo or "").strip()
    if not termo:
        raise HTTPException(status_code=400, detail="Termo de pesquisa vazio.")
    termos_consulta = _termos_busca_ml(termo, req.termos_busca)
    termo_usado = termos_consulta[0] if termos_consulta else termo

    def _parse_vendas(v):
        return _parse_vendas_ml(v)

    try:
        # Usa primeiro a API oficial (ordem da página de busca).
        limite = min(max(int(req.max_anuncios or 60), 1), 100)
        base_resultados = []
        for termo_consulta in termos_consulta:
            base_resultados = _ml_api_search_paginated(termo_consulta, limit=limite)
            termo_usado = termo_consulta
            if base_resultados:
                break

        # Fallback para HTML se API falhar ou o Mercado Livre bloquear a API.
        if not base_resultados and bool(req.usar_automatico):
            url_busca = f"https://lista.mercadolivre.com.br/?q={quote_plus(termo_usado)}"
            html_resultados = _buscar_anuncios_mercadolivre_html(url_busca, max_retries=2, delay=1) or []
            for r in html_resultados:
                base_resultados.append({
                    "id": _extrair_item_id(r.get("url") or r.get("link") or ""),
                    "title": r.get("titulo") or "",
                    "permalink": r.get("url") or r.get("link") or "",
                    "sold_quantity": _parse_vendas(r.get("vendas")),
                    "date_created": r.get("data_criacao")
                })

        if not base_resultados:
            for termo_consulta in termos_consulta:
                url_busca = f"https://lista.mercadolivre.com.br/?q={quote_plus(termo_consulta)}"
                automaticos = _buscar_anuncios_mercadolivre_automatico(url_busca, max_anuncios=limite) or []
                termo_usado = termo_consulta
                for r in automaticos:
                    base_resultados.append({
                        "id": _extrair_item_id(r.get("url") or r.get("link") or ""),
                        "title": r.get("titulo") or "",
                        "permalink": r.get("url") or r.get("link") or "",
                        "sold_quantity": _parse_vendas(r.get("vendas")),
                        "date_created": r.get("data_criacao")
                    })
                if base_resultados:
                    break

        anuncios = []
        for idx, item in enumerate(base_resultados, start=1):
            seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
            seller_id = item.get("seller_id") or seller.get("id")
            vendedor = _normalizar_nome_vendedor_ml(item)
            sem_juros = _ml_parcelamento_sem_juros_api(item)
            sem_juros = bool(sem_juros) if sem_juros is not None else False
            item_condition = _ml_extrair_item_condition(item)
            anuncios.append({
                "posicao": idx,
                "id": item.get("id"),
                "sku": _ml_extrair_sku(item),
                "titulo": item.get("title") or item.get("titulo") or "",
                "url": item.get("permalink") or item.get("url") or item.get("link") or "",
                "vendas": _parse_vendas_ml(item.get("sold_quantity") if "sold_quantity" in item else item.get("vendas")),
                "data_criacao": item.get("date_created") or item.get("start_time"),
                "vendedor": vendedor or "",
                "seller_id": seller_id,
                "installments": item.get("installments") if isinstance(item.get("installments"), dict) else None,
                "parcelamento_sem_juros": sem_juros,
                "tipo_anuncio": "Premium" if sem_juros else "Classico",
                "condicao": item_condition,
                "condition": item_condition,
                "item_condition": item_condition,
            })

        if anuncios:
            _enriquecer_vendas_com_api(anuncios, max_workers=min(10, len(anuncios)), client_id=client_id)

        return {
            "success": True,
            "termo": termo,
            "termo_usado": termo_usado,
            "total": len(anuncios),
            "anuncios": anuncios,
            "warning": None if anuncios else "O Mercado Livre nao retornou anuncios para esta consulta ou bloqueou a consulta automatica."
        }
    except Exception as e:
        logger.exception("[Favoritos][PrimeiraPagina] Erro ao consultar ML: %s", e)
        return {
            "success": True,
            "termo": termo,
            "termo_usado": termo_usado,
            "total": 0,
            "anuncios": [],
            "warning": "Não foi possível consultar anuncios agora."
        }


async def favoritos_ml_primeira_pagina(req: FavoritosPrimeiraPaginaRequest, client_id: str = Depends(get_tenant_id)):
    return await asyncio.to_thread(_favoritos_ml_primeira_pagina_sync, req, client_id)


async def favoritos_ml_enriquecer_datas(req: FavoritosEnriquecerDatasRequest, client_id: str = Depends(get_tenant_id)):
    anuncios_req = req.anuncios or []
    limite = req.max_anuncios or len(anuncios_req) or 50
    limite = min(max(limite, 1), 200)

    tarefas = []
    vistos = set()

    for anuncio in anuncios_req[:limite]:
        if not isinstance(anuncio, dict):
            continue
        if (
            anuncio.get("historico_estatico")
            or anuncio.get("ranking_historico_estatico")
            or anuncio.get("bloquear_atualizacao_historico")
        ):
            continue
        url = str(anuncio.get("url") or "").strip()
        raw_item_id = str(anuncio.get("id") or "").strip()
        item_id = _extrair_item_id(raw_item_id) or _extrair_item_id(url) or raw_item_id.upper().replace("-", "")
        imagem = str(
            anuncio.get("imagem")
            or anuncio.get("thumbnail")
            or anuncio.get("image")
            or anuncio.get("picture")
            or ""
        ).strip()
        chave = item_id or url
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        tarefas.append({
            "url": url,
            "item_id": item_id,
            "imagem": imagem,
            "vendedor": anuncio.get("vendedor"),
            "fonte_vendedor": anuncio.get("fonte_vendedor") or anuncio.get("vendedorFonte") or anuncio.get("vendedor_fonte"),
            "vendas": anuncio.get("vendas"),
            "fonte_vendas": anuncio.get("fonte_vendas") or anuncio.get("vendasFonte") or anuncio.get("vendas_fonte"),
            "visitas": anuncio.get("visitas"),
            "fonte_visitas": anuncio.get("fonte_visitas") or anuncio.get("visitasFonte") or anuncio.get("visitas_fonte"),
            "data_criacao": anuncio.get("data_criacao"),
            "fonte": anuncio.get("fonte"),
            "fonte_data_criacao": anuncio.get("fonte_data_criacao"),
            "data_criacao_confianca": anuncio.get("data_criacao_confianca"),
            "sku": anuncio.get("sku"),
            "listing_type_id": anuncio.get("listing_type_id") or anuncio.get("listingTypeId"),
            "listing_type_name": anuncio.get("listing_type_name"),
            "tipo_anuncio": anuncio.get("tipo_anuncio"),
            "parcelamento_sem_juros": anuncio.get("parcelamento_sem_juros"),
            "shipping": anuncio.get("shipping"),
            "logistic_type": anuncio.get("logistic_type") or anuncio.get("logisticType"),
            "shipping_mode": anuncio.get("shipping_mode") or anuncio.get("shippingMode"),
            "is_full": anuncio.get("is_full"),
            "condicao": anuncio.get("condicao"),
            "condition": anuncio.get("condition"),
            "item_condition": anuncio.get("item_condition"),
        })

    cache_inicio = time.perf_counter()
    datas_cache_local = _ml_datas_cache_local(client_id) if tarefas else {}
    cache_ms = int((time.perf_counter() - cache_inicio) * 1000)
    itens_api_precarregados = _ml_api_items_multiget_tenant(
        client_id,
        [tarefa.get("item_id") for tarefa in tarefas if tarefa.get("item_id")],
    ) if tarefas else {}
    logger.info(
        "[Favoritos][Datas] cache_local_carregado tenant=%s entradas=%s ms=%s tarefas=%s",
        client_id,
        len(datas_cache_local),
        cache_ms,
        len(tarefas),
    )

    def _processar(tarefa: dict):
        url = tarefa.get("url") or ""
        item_id = tarefa.get("item_id") or None
        info = _extrair_info_anuncio(
            url,
            item_id,
            client_id=client_id,
            imagem=tarefa.get("imagem"),
            dados_base=tarefa,
            datas_cache_local=datas_cache_local,
            api_item_precarregado=itens_api_precarregados.get(item_id),
            api_item_precarregado_tentado=True,
        )
        return {
            "url": url,
            "id": item_id,
            "data_criacao": info.get("data_criacao"),
            "fonte": info.get("fonte"),
            "fonte_data_criacao": info.get("fonte_data_criacao"),
            "data_criacao_confianca": info.get("data_criacao_confianca"),
            "vendedor": info.get("vendedor"),
            "fonte_vendedor": info.get("fonte_vendedor"),
            "vendas": info.get("vendas"),
            "fonte_vendas": info.get("fonte_vendas"),
            "visitas": info.get("visitas"),
            "fonte_visitas": info.get("fonte_visitas"),
            "listing_type_id": info.get("listing_type_id"),
            "listing_type_name": info.get("listing_type_name"),
            "tipo_anuncio": info.get("tipo_anuncio"),
            "parcelamento_sem_juros": info.get("parcelamento_sem_juros"),
            "shipping": info.get("shipping"),
            "logistic_type": info.get("logistic_type"),
            "shipping_mode": info.get("shipping_mode"),
            "is_full": info.get("is_full"),
            "condicao": info.get("condicao"),
            "condition": info.get("condition"),
            "item_condition": info.get("item_condition"),
        }

    resultados = []
    if tarefas:
        max_workers = min(18, len(tarefas))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = [executor.submit(_processar, tarefa) for tarefa in tarefas]
            for futuro in as_completed(futuros):
                try:
                    resultados.append(futuro.result(timeout=20))
                except Exception:
                    logger.exception("[Favoritos][Datas] Falha ao enriquecer anuncio")

    return {
        "success": True,
        "total": len(resultados),
        "resultados": resultados
    }


def _favoritos_pesquisar_sync(req: FavoritosSearchRequest):
    termo = (req.termo or "").strip()
    if not termo:
        raise HTTPException(status_code=400, detail="Termo de pesquisa vazio.")

    max_anuncios = req.max_anuncios or 60
    max_anuncios = min(max(max_anuncios, 1), 60)

    url_busca = termo if termo.startswith("http") else f"https://www.mercadolivre.com.br/jm/search?q={quote_plus(termo)}"
    logger.info(f"[Favoritos] Buscando em paralelo: {termo[:80]}")
    anuncios = _buscar_anuncios_mercadolivre(termo, max_retries=3, delay=2)
    if not anuncios:
        logger.warning("[Favoritos] Busca paralela sem resultados. Usando fallback com Selenium/HTML.")
        anuncios = _buscar_anuncios_mercadolivre_automatico(url_busca, max_anuncios)

    anuncios = _deduplicar_anuncios(anuncios or [])
    anuncios = anuncios[:max_anuncios]
    if anuncios:
        _enriquecer_vendas_com_api(anuncios, max_workers=8)

    if not anuncios:
        return {
            "success": True,
            "tipo": "busca_termo",
            "total": 0,
            "resultados": [],
            "top": []
        }

    # Processar anuncios para calcular métricas
    resultados = []
    for anuncio in anuncios:
        anuncio = _normalizar_anuncio_favoritos(anuncio)
        url = anuncio.get("url", "")
        vendas_text = anuncio.get("vendas")
        vendas_num = _parse_vendas_ml(vendas_text)

        # Estimativa de meses
        meses = _estimar_meses_anuncio(url) if url else None
        media = vendas_num / meses if vendas_num and meses and meses > 0 else None

        resultados.append({
            "titulo": anuncio.get("titulo", "N/A"),
            "preco": anuncio.get("preco", "N/A"),
            "vendas": vendas_num,
            "meses": meses,
            "media_vendas": round(media, 2) if media else None,
            "url": url
        })

    # Top 7 por média de vendas
    resultados_ordenados = sorted(
        [r for r in resultados if r["media_vendas"] is not None],
        key=lambda x: x["media_vendas"],
        reverse=True
    )
    top7 = resultados_ordenados[:7]

    logger.info(f"[Favoritos] Total de anuncios encontrados: {len(anuncios)}")

    return {
        "success": True,
        "tipo": "busca_termo",
        "total": len(anuncios),
        "resultados": resultados,
        "top": top7
    }


async def favoritos_pesquisar(req: FavoritosSearchRequest):
    return await asyncio.to_thread(_favoritos_pesquisar_sync, req)



__all__ = [
    "FAVORITOS_ENDPOINTS",
    "configure_favoritos_endpoints_runtime",
    *FAVORITOS_ENDPOINTS,
]
