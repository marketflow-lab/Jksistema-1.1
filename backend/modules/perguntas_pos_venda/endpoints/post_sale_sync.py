"""Post-sale cache synchronization state machine."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.encoders import jsonable_encoder

from backend.modules.perguntas_pos_venda.endpoints.contracts import _ML_POS_VENDA_REMOTE_CURSOR_LIMIT, _ML_POS_VENDA_SYNC_TTL_SECONDS
from backend.modules.perguntas_pos_venda.endpoints.runtime import runtime_adapter
from backend.modules.perguntas_pos_venda.endpoints.state import ENDPOINTS_STATE
from backend.services import perguntas_pos_venda_store
from backend.modules.perguntas_pos_venda.endpoints.diagnostics import (
    _perguntas_ia_diagnostico_texto,
)
from backend.modules.perguntas_pos_venda.endpoints.post_sale_remote import (
    _ml_pos_venda_listar_conversas_remoto,
)

_integracoes_nome_normalizado = runtime_adapter("_integracoes_nome_normalizado")
logger = runtime_adapter("logger")


def _ml_pos_venda_sync_key(client_id: str, loja: str, seller_id: str) -> str:
    return "::".join((
        str(client_id or "").strip(),
        _integracoes_nome_normalizado(loja),
        str(seller_id or "").strip(),
    ))


def _ml_pos_venda_sync_running(client_id: str, loja: str, seller_id: str) -> bool:
    key = _ml_pos_venda_sync_key(client_id, loja, seller_id)
    with ENDPOINTS_STATE.pos_sale_sync_lock:
        thread = ENDPOINTS_STATE.pos_sale_sync_threads.get(key)
        return bool(thread and thread.is_alive())


def is_running(client_id: str, loja: str, seller_id: str) -> bool:
    return _ml_pos_venda_sync_running(client_id, loja, seller_id)


def _ml_pos_venda_sync_fresh(state: dict, ttl_seconds: int = _ML_POS_VENDA_SYNC_TTL_SECONDS) -> bool:
    value = str((state or {}).get("finished_at") or "").strip()
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return 0 <= age < max(0, int(ttl_seconds or 0))
    except (TypeError, ValueError, OverflowError):
        return False


def _ml_pos_venda_sync_error(error: Exception | str) -> str:
    detail = getattr(error, "detail", None)
    text = detail if detail not in (None, "") else str(error or "")
    return _perguntas_ia_diagnostico_texto(text, 500) or "Falha ao atualizar o historico de pos-venda."


@dataclass
class _SyncProgress:
    cursor: int
    scanned_run: int
    scanned_total: int
    conversations_saved: int
    orders_total: int
    complete: bool = False
    cursor_limit_reached: bool = False


def _ml_pos_venda_collect_pages(
    *,
    progress: _SyncProgress,
    client_id: str,
    tenant_path: str,
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    max_orders: int,
) -> None:
    with ENDPOINTS_STATE.pos_sale_sync_semaphore:
        while progress.scanned_run < max_orders:
            if progress.cursor >= _ML_POS_VENDA_REMOTE_CURSOR_LIMIT:
                if mode == "bootstrap":
                    progress.cursor_limit_reached = True
                    return
                raise RuntimeError(
                    "Atualizacao incremental interrompida antes de exceder o limite de cursor remoto."
                )
            remaining = max(1, max_orders - progress.scanned_run)
            payload = _ml_pos_venda_listar_conversas_remoto(
                loja=loja,
                dias=coverage_days,
                offset=progress.cursor,
                limit=20,
                max_orders=remaining,
                busca=None,
                nao_lidas=False,
                client_id=client_id,
            )
            conversations = payload.get("conversas") if isinstance(payload, dict) else []
            perguntas_pos_venda_store.upsert_conversations(
                tenant_path, loja, seller_id, conversations or []
            )
            page_scanned = max(0, int((payload or {}).get("orders_avaliadas") or 0))
            progress.scanned_run += page_scanned
            progress.scanned_total += page_scanned
            progress.conversations_saved += len(conversations or [])
            progress.orders_total = max(
                progress.orders_total, int((payload or {}).get("orders_total") or 0)
            )
            next_offset = (payload or {}).get("next_offset")
            next_cursor = (
                int(next_offset) if next_offset is not None else progress.cursor + page_scanned
            )
            perguntas_pos_venda_store.update_sync_progress(
                tenant_path,
                loja,
                seller_id,
                cursor=next_cursor,
                conversations_saved=progress.conversations_saved,
                orders_scanned=progress.scanned_total,
            )
            if next_offset is None:
                progress.cursor = next_cursor
                progress.complete = True
                return
            if next_cursor <= progress.cursor:
                raise RuntimeError("A sincronizacao de pos-venda nao avancou o cursor.")
            progress.cursor = next_cursor
            time.sleep(0.15)


def _ml_pos_venda_finish_cursor_limited(
    tenant_path: str,
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    progress: _SyncProgress,
) -> None:
    diagnostic = (
        "Cobertura parcial: o Mercado Livre limita a busca de pedidos antes do cursor 10000; "
        "bootstrap encerrado com o cache coletado preservado e atualizacoes incrementais habilitadas."
    )
    perguntas_pos_venda_store.finish_sync(
        tenant_path,
        loja,
        seller_id,
        mode=mode,
        coverage_days=coverage_days,
        bootstrap_complete=True,
        cursor=progress.cursor,
        orders_total=progress.orders_total,
    )
    perguntas_pos_venda_store.fail_sync(
        tenant_path, loja, seller_id, error=diagnostic, cursor=progress.cursor
    )
    logger.warning("[ML POS VENDA CACHE] %s loja=%s cursor=%s", diagnostic, loja, progress.cursor)


def _ml_pos_venda_sync_worker(
    *,
    client_id: str,
    tenant_path: str,
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    max_orders: int,
) -> None:
    key = _ml_pos_venda_sync_key(client_id, loja, seller_id)
    state = perguntas_pos_venda_store.get_state(tenant_path, loja, seller_id)
    progress = _SyncProgress(
        cursor=int(state.get("cursor") or 0) if mode == "bootstrap" else 0,
        scanned_run=0,
        scanned_total=int(state.get("orders_scanned") or 0) if mode == "bootstrap" else 0,
        conversations_saved=int(state.get("conversations_saved") or 0) if mode == "bootstrap" else 0,
        orders_total=int(state.get("orders_total") or 0),
    )
    try:
        perguntas_pos_venda_store.begin_sync(
            tenant_path,
            loja,
            seller_id,
            mode=mode,
            coverage_days=coverage_days,
            cursor=progress.cursor,
        )
        _ml_pos_venda_collect_pages(
            progress=progress,
            client_id=client_id,
            tenant_path=tenant_path,
            loja=loja,
            seller_id=seller_id,
            mode=mode,
            coverage_days=coverage_days,
            max_orders=max_orders,
        )
        if progress.cursor_limit_reached:
            _ml_pos_venda_finish_cursor_limited(
                tenant_path, loja, seller_id, mode, coverage_days, progress
            )
            return
        if not progress.complete:
            raise RuntimeError(
                f"Atualizacao parcial: limite de {max_orders} pedidos atingido antes do fim do periodo."
            )
        perguntas_pos_venda_store.finish_sync(
            tenant_path,
            loja,
            seller_id,
            mode=mode,
            coverage_days=coverage_days,
            bootstrap_complete=mode == "bootstrap",
            cursor=progress.cursor,
            orders_total=progress.orders_total,
        )
    except Exception as error:
        perguntas_pos_venda_store.fail_sync(
            tenant_path,
            loja,
            seller_id,
            error=_ml_pos_venda_sync_error(error),
            cursor=progress.cursor,
        )
        logger.warning(
            "[ML POS VENDA CACHE] Falha na sincronizacao loja=%s modo=%s cursor=%s: %s",
            loja,
            mode,
            progress.cursor,
            _ml_pos_venda_sync_error(error),
        )
    finally:
        with ENDPOINTS_STATE.pos_sale_sync_lock:
            current = ENDPOINTS_STATE.pos_sale_sync_threads.get(key)
            if current is threading.current_thread():
                ENDPOINTS_STATE.pos_sale_sync_threads.pop(key, None)


def _ml_pos_venda_schedule_sync(
    *,
    client_id: str,
    tenant_path: str,
    loja: str,
    seller_id: str,
    mode: str,
    coverage_days: int,
    max_orders: int,
) -> bool:
    key = _ml_pos_venda_sync_key(client_id, loja, seller_id)
    with ENDPOINTS_STATE.pos_sale_sync_lock:
        current = ENDPOINTS_STATE.pos_sale_sync_threads.get(key)
        if current and current.is_alive():
            return False
        thread = threading.Thread(
            target=_ml_pos_venda_sync_worker,
            kwargs={
                "client_id": client_id,
                "tenant_path": tenant_path,
                "loja": loja,
                "seller_id": seller_id,
                "mode": mode,
                "coverage_days": coverage_days,
                "max_orders": max_orders,
            },
            name=f"ml-pos-venda-{mode}-{seller_id}",
            daemon=True,
        )
        ENDPOINTS_STATE.pos_sale_sync_threads[key] = thread
        thread.start()
        return True


def _ml_pos_venda_cache_response(
    *,
    tenant_path: str,
    client_id: str,
    loja: str,
    seller_id: str,
    dias: int,
    offset: int,
    limit: int,
    busca: str,
    nao_lidas: bool,
    summary_only: bool,
) -> dict:
    state = perguntas_pos_venda_store.get_state(tenant_path, loja, seller_id)
    running = _ml_pos_venda_sync_running(client_id, loja, seller_id)
    summary = perguntas_pos_venda_store.summary(tenant_path, loja, seller_id, days=dias)
    if summary_only:
        page = {
            "conversations": [],
            "total": int(summary.get("total") or 0),
            "next_offset": None,
            "has_next": False,
        }
    else:
        page = perguntas_pos_venda_store.list_conversations(
            tenant_path,
            loja,
            seller_id,
            days=dias,
            offset=offset,
            limit=limit,
            search=busca,
            unread_only=nao_lidas,
        )
    last_error = str(state.get("error") or "").strip()
    sync = {
        "source": "local_cache",
        "status": "running" if running else str(state.get("status") or "idle"),
        "running": running,
        "mode": state.get("mode") or "",
        "bootstrap_complete": bool(state.get("bootstrap_complete")),
        "bootstrap_cursor": int(state.get("cursor") or 0),
        "coverage_days": int(state.get("coverage_days") or 0),
        "last_synced_at": state.get("finished_at") or None,
        "last_attempt_at": state.get("updated_at") or None,
        "last_error": last_error or None,
        "cache_version": 1,
        "stale": bool(last_error) and not running,
    }
    conversations = page.get("conversations") or []
    return jsonable_encoder({
        "success": True,
        "loja": loja,
        "seller_id": seller_id,
        "dias": dias,
        "offset": offset,
        "limit": limit,
        "busca": busca,
        "nao_lidas": bool(nao_lidas),
        "next_offset": page.get("next_offset"),
        "has_next": bool(page.get("has_next")),
        "orders_total": int(state.get("orders_total") or 0),
        "orders_avaliadas": int(state.get("orders_scanned") or 0),
        "conversas_total": int(page.get("total") or 0),
        "conversas_nao_lidas_total": int(summary.get("unread_total") or 0),
        "interrompido": running or not bool(state.get("bootstrap_complete")),
        "erros": ([{"erro": last_error}] if last_error else []),
        "conversas": conversations,
        "source": "local_cache",
        "sync": sync,
    })


__all__ = ["is_running"]
