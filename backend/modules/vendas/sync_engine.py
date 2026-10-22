"""Durable, resumable day runner over the existing Bling collectors."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
import asyncio
from urllib.parse import urlsplit

from backend.schemas import VendasSyncRequest
from backend.services.bling import _bling_get_with_adaptive_limit
from backend.services.bling_oauth import requires_bling_reauthorization
from backend.services.vendas_collection_checkpoint import (
    collection_checkpoint, collection_day_summary, finish_collection_day,
)

from .dependencies import logger
from .errors import VendasDomainError as HTTPException
from .legacy import buscar_loja, _listar_bancos_vendas_tenant, _bling_executar_com_refresh
from .performance import invalidate_vendas_cache, prepare_vendas_database
from .progress import (
    _criar_progresso, _set_progresso, _sync_context_key, _sync_log,
    _sync_unregister_active, _verificar_cancelamento,
)
from .state import SYNC_DAY_CONTEXT, SYNC_META, SYNC_THREAD_CONTEXT
from .sync_period import _sincronizar_vendas_periodo_impl
from .sync_persistence import (
    _vendas_sync_dias_periodo, _vendas_sync_prepare_job, _vendas_sync_update_job,
)
from .sync_queue import SyncQueue


def _resolve_store(req: VendasSyncRequest, client_id: str):
    kwargs = {"store_id": req.store_id} if req.store_id else {}
    loja = buscar_loja(client_id, req.loja, **kwargs)
    if not loja:
        raise HTTPException(status_code=404, detail="Loja não encontrada para sincronização.")
    store_id = str(loja.get("store_id") or "").strip()
    if not store_id or (req.store_id and store_id != req.store_id):
        raise HTTPException(status_code=409, detail="Identidade da loja indisponível.")
    return req.model_copy(update={"loja": str(loja.get("nome") or req.loja), "store_id": store_id})


def sincronizar_vendas(req: VendasSyncRequest, client_id: str):
    if not req.loja or req.loja == "__todas":
        raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
    _vendas_sync_dias_periodo(req.data_inicio, req.data_fim)
    req = _resolve_store(req, client_id)
    from backend.services.central_accounts_client import current
    from . import sync_automation

    queue = SyncQueue(client_id)
    job = queue.enqueue(
        req.store_id, req.loja, req.data_inicio, req.data_fim,
        forcar_resync=req.forcar_resync, automatico=req.automatico,
        requires_session=current(client_id) is not None,
        verify_connections=True,
    )
    sync_automation.remember_authorization(client_id, req.store_id)
    try:
        sync_automation.start()
        sync_automation.dispatch(client_id)
    except Exception:
        logger.warning("Período salvo; o trabalhador retomará a fila na próxima tentativa.")
    current_job = queue.get_job(job["id"]) or job
    running = current_job["status"] == "running"
    return {
        "accepted": True,
        "status": current_job["status"],
        "started": running,
        "queued": current_job["status"] in {"queued", "retry"},
        "already_running": False,
        "job_id": job["id"],
        "store_id": req.store_id,
        "data_inicio": req.data_inicio,
        "data_fim": req.data_fim,
        "limit": 2,
        "message": "Período salvo para sincronização e retomada.",
    }


def _safe_failure(exc):
    status = getattr(exc, "status_code", 500)
    if status == 401 and getattr(exc, "code", None) == "bling_reconnect_required":
        return "A conexão Bling precisa de nova autorização. Reconecte a loja em Lojas e APIs para retomar as pendências."
    if status == 401 and "central" in str(getattr(exc, "detail", "")).lower():
        return "Sessão da Central expirada. Faça login novamente para continuar as pendências."
    messages = {
        400: "Configuração da integração incompleta. Verifique a conexão da loja.",
        401: "Sessão expirada. Faça login novamente para continuar as pendências.",
        403: "A integração não autorizou a consulta. Verifique o acesso da loja.",
        404: "A loja não está disponível. Verifique a configuração antes de retomar.",
        409: "Sincronização interrompida; os registros pendentes foram preservados.",
        429: "Limite da integração atingido. A sincronização tentará novamente.",
        502: "A integração retornou dados incompletos. O período continua pendente.",
        503: "A integração está indisponível. A sincronização tentará novamente.",
    }
    return messages.get(status, "Não foi possível concluir a sincronização. Os registros continuam pendentes.")


def _verificar_conexao_bling(req, client_id):
    """Verify the authorized store with a minimal GET, never collecting data."""
    def config_atual():
        loja = buscar_loja(client_id, req.loja, store_id=req.store_id)
        if not loja or str(loja.get("store_id") or "") != req.store_id:
            raise HTTPException(409, "A configuração da loja mudou durante a verificação.")
        return dict((loja.get("integracoes") or {}).get("bling") or {})

    cfg = config_atual()
    if requires_bling_reauthorization(cfg):
        raise HTTPException(401, "Reconecte esta conta Bling em Lojas e APIs.", code="bling_reconnect_required")
    if not cfg.get("access_token") or not (cfg.get("central") or (cfg.get("id") and cfg.get("secret"))):
        raise HTTPException(400, "Loja sem conexão Bling disponível para verificação.")

    def consultar(token):
        _verificar_cancelamento(client_id)
        response = _bling_get_with_adaptive_limit(
            "https://api.bling.com.br/Api/v3/pedidos/vendas",
            headers={"Authorization": f"Bearer {token}"},
            params={"pagina": 1, "limite": 1, "dataInicial": req.data_inicio, "dataFinal": req.data_fim},
            timeout=(3.05, 8), max_attempts=1, total_timeout=12,
            cancel_callback=lambda: _verificar_cancelamento(client_id),
        )
        if response is None:
            raise HTTPException(503, "Não foi possível confirmar a conexão Bling.")
        final_url = urlsplit(str(getattr(response, "url", "")))
        if (final_url.scheme != "https" or final_url.hostname != "api.bling.com.br"
                or final_url.path != "/Api/v3/pedidos/vendas"
                or final_url.username or final_url.password or final_url.port not in (None, 443)):
            raise HTTPException(502, "Destino inesperado na verificação da conexão Bling.")
        status = int(response.status_code)
        if status == 401:
            return False, status
        if status != 200:
            retry_after = (getattr(response, "headers", {}) or {}).get("Retry-After")
            raise HTTPException(status if status in (400, 403, 429, 503) else 502,
                                "A conexão Bling não confirmou a consulta.",
                                headers={"Retry-After": str(retry_after)} if retry_after else None)
        try:
            body = response.json()
        except (ValueError, TypeError):
            raise HTTPException(502, "Resposta inválida na verificação da conexão Bling.") from None
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise HTTPException(502, "Resposta incompleta na verificação da conexão Bling.")
        return True, status

    from backend.services.central_accounts_client import is_marker
    if cfg.get("central") or is_marker(cfg["access_token"]):
        # Central owns provider renewal. Never renew or invalidate an opaque
        # credential through the local OAuth/configuration path.
        confirmed, status = consultar(cfg["access_token"])
        checked_cfg = cfg
        if status == 401:
            raise HTTPException(401, "Sessão da Central expirada. Faça login novamente.")
    else:
        confirmed, status, checked_cfg = _bling_executar_com_refresh(
            client_id, req.loja, cfg, consultar, store_id=req.store_id,
        )
    if status == 401:
        raise HTTPException(401, "Reconecte esta conta Bling em Lojas e APIs.", code="bling_reconnect_required")
    _verificar_cancelamento(client_id)
    if confirmed is not True or status != 200 or config_atual() != checked_cfg:
        raise HTTPException(409, "A conexão Bling mudou; a verificação anterior foi descartada.")


def _verificar_alertas_antes_da_coleta(req, client_id, queue, job_id, owner):
    alerts = queue.connection_alerts(req.store_id)
    if not alerts:
        return
    _verificar_cancelamento(client_id)
    job = queue.get_job(job_id)
    if not job or not queue.owns(job_id, owner):
        raise HTTPException(409, "Verificação interrompida; a tentativa não possui mais a concessão.")
    _set_progresso(client_id, _criar_progresso(
        "Conexão Bling", 0, 0, 0, "Verificando conexão Bling antes de iniciar a coleta...",
    ))
    _verificar_conexao_bling(req, client_id)
    _verificar_cancelamento(client_id)
    queue.acknowledge_connection_alerts(
        job_id, owner, req.store_id, alerts, expected_attempts=job["attempts"],
    )
    if not queue.owns(job_id, owner):
        raise HTTPException(409, "Verificação interrompida; a tentativa não possui mais a concessão.")
    _verificar_cancelamento(client_id)


def _sincronizar_vendas_thread_worker(req: VendasSyncRequest, client_id: str, job_id: str, owner: str | None = None):
    queue = SyncQueue(client_id)
    job = queue.get_job(job_id)
    if not job or not owner:
        return
    SYNC_THREAD_CONTEXT.job_id = job_id
    SYNC_THREAD_CONTEXT.loja = req.loja
    SYNC_THREAD_CONTEXT.queue_job = job
    SYNC_THREAD_CONTEXT.lease_check = lambda: queue.owns(job_id, owner)
    SYNC_THREAD_CONTEXT.queue_owner = owner
    try:
        if job.get("requires_session"):
            from backend.services.central_accounts_client import current
            central = current(client_id)
            if central is None:
                raise HTTPException(status_code=401, detail="Sessão necessária.")
        req = _resolve_store(req, client_id)
        SYNC_THREAD_CONTEXT.loja = req.loja
        _verificar_alertas_antes_da_coleta(req, client_id, queue, job_id, owner)
        asyncio.run(_sincronizar_vendas_impl(req, client_id))
        for db_path in _listar_bancos_vendas_tenant(client_id, req.loja):
            try:
                prepare_vendas_database(db_path)
            except Exception:
                logger.warning("Não foi possível preparar os índices após a sincronização.")
        invalidate_vendas_cache(client_id)
        SYNC_META[job_id] = {**(SYNC_META.get(job_id) or {}), "status": "complete"}
    except Exception as exc:
        if queue.owns(job_id, owner):
            retry_after = None
            headers = getattr(exc, "headers", None) or {}
            try:
                retry_after = min(900, max(1, float(headers.get("Retry-After"))))
            except (TypeError, ValueError):
                pass
            queue.fail_job(job_id, owner, _safe_failure(exc),
                           retryable=getattr(exc, "status_code", 500) != 404,
                           retry_after=retry_after)
            _set_progresso(client_id, _criar_progresso("Pendente", 0, 0, 0, _safe_failure(exc)))
            SYNC_META[job_id] = {**(SYNC_META.get(job_id) or {}), "status": "retry"}
        else:
            _set_progresso(client_id, _criar_progresso(
                "Pausado", 0, 0, 0, "Sincronização pausada; o progresso foi preservado."
            ))
    finally:
        _sync_unregister_active(client_id, job_id)
        for attr in ("job_id", "loja", "queue_job", "lease_check", "queue_owner"):
            try:
                delattr(SYNC_THREAD_CONTEXT, attr)
            except AttributeError:
                pass
        from .sync_automation import wake
        wake()


async def _sincronizar_vendas_impl(req: VendasSyncRequest, client_id: str):
    """Preserve the legacy coroutine, using the durable job when run by the queue."""
    dias = _vendas_sync_dias_periodo(req.data_inicio, req.data_fim)
    job_context_key = _sync_context_key(client_id)
    job = getattr(SYNC_THREAD_CONTEXT, "queue_job", None)
    owner = getattr(SYNC_THREAD_CONTEXT, "queue_owner", None)
    queue = SyncQueue(client_id) if job else None
    if job is None:
        job_key, job = _vendas_sync_prepare_job(client_id, req, dias)
    else:
        job_key = job["id"]
    done = set(job.get("dias_concluidos") or [])
    totals = {key: 0 for key in (
        "total", "notas_entrada_total", "nf_enriquecidas", "nf_pendentes_restantes",
        "vendas_processadas", "notas_processadas",
    )}
    try:
        for index, day in enumerate(dias, start=1):
            _verificar_cancelamento(client_id)
            if day in done:
                continue
            meta = {
                "loja": req.loja, "store_id": req.store_id,
                "data_inicio": req.data_inicio, "data_fim": req.data_fim,
                "forcar_resync": req.forcar_resync, "modo": "diario",
                "dia_atual": day, "dia_indice": index, "dias_total": len(dias),
            }
            SYNC_META[job_context_key] = {**(SYNC_META.get(job_context_key) or {}), **meta}
            if queue is None:
                _vendas_sync_update_job(client_id, job_key, {"status": "running", **meta})
            _set_progresso(client_id, _criar_progresso(
                "Dia", index, len(dias), int(len(done) / len(dias) * 100),
                f"Conferindo dia {index}/{len(dias)} ({day}).",
            ))
            day_req = req.model_copy(update={"data_inicio": day, "data_fim": day})
            SYNC_DAY_CONTEXT[job_context_key] = {
                "dia_atual": day, "dia_indice": index, "total_dias": len(dias),
            }
            checkpoint = (
                collection_checkpoint(client_id, req.store_id, job_key, day)
                if queue is not None else nullcontext()
            )
            try:
                with checkpoint:
                    result = await _sincronizar_vendas_periodo_impl(day_req, client_id, reset_estado=False)
                if not isinstance(result, dict) or result.get("success") is not True:
                    raise HTTPException(status_code=502, detail="Dia não confirmado.")
                _verificar_cancelamento(client_id)
                if queue is not None:
                    result["checkpoint"] = collection_day_summary(client_id, req.store_id, job_key, day)
                    queue.complete_day(job_key, owner, day, result)
                else:
                    _vendas_sync_update_job(client_id, job_key, {
                        "status": "running", "dias_concluidos": [d for d in dias if d in done or d == day],
                        "ultimo_resultado": result,
                    })
                done.add(day)
                if queue is not None:
                    try:
                        finish_collection_day(client_id, req.store_id, job_key, day)
                    except Exception:
                        # Business data and completion are durable; an orphan staging
                        # row cannot be used by a different cycle.
                        logger.warning("Limpeza da preparação ficará pendente após confirmação do dia.")
                for key in totals:
                    totals[key] += int(result.get(key) or 0)
            finally:
                SYNC_DAY_CONTEXT.pop(job_context_key, None)
        _verificar_cancelamento(client_id)
        composition_progress = _criar_progresso(
            "Composições", len(dias), len(dias), 99,
            "Conferindo as composições dos produtos vendidos.",
        )
        composition_progress["concluido"] = False
        _set_progresso(client_id, composition_progress)
        try:
            from .composition_sync import sincronizar_composicoes_vendas

            # The collector owns its bounded budget and runs on this worker so
            # cancellation keeps the same thread-local job and lease context.
            compositions = sincronizar_composicoes_vendas(
                client_id, req.store_id,
                cancel_callback=lambda: _verificar_cancelamento(client_id),
            )
            totals["composicoes"] = {
                "updated": max(0, int(compositions.get("updated") or 0)),
                "pending": bool(compositions.get("pending", True)),
            }
        except HTTPException:
            raise
        except Exception:
            totals["composicoes"] = {"updated": 0, "pending": True}
            logger.warning("A conferência de composições ficará pendente para a próxima sincronização.")
        _verificar_cancelamento(client_id)
        if queue is not None:
            queue.complete_job(job_key, owner)
        else:
            _vendas_sync_update_job(client_id, job_key, {
                "status": "complete", "dias_concluidos": dias,
                "completed_at": datetime.now().isoformat(), "resultado_total": totals,
            })
    except Exception as exc:
        if queue is None:
            _vendas_sync_update_job(client_id, job_key, {
                "status": "interrompido" if getattr(exc, "status_code", 0) == 409 else "erro",
                "ultimo_erro": _safe_failure(exc), "dias_concluidos": [d for d in dias if d in done],
            })
        raise
    message = (
        "Período conferido; composições pendentes serão retomadas na próxima sincronização."
        if totals["composicoes"]["pending"] else "Período e composições conferidos."
    )
    progress = _criar_progresso("Finalizado", len(dias), len(dias), 100, message)
    progress.update(concluido=True, result=totals)
    _set_progresso(client_id, progress)
    return {"success": True, **totals}


__all__ = ["_sincronizar_vendas_thread_worker", "sincronizar_vendas", "_sincronizar_vendas_impl"]
