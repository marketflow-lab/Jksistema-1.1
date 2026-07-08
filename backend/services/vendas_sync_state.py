"""Durable synchronization-state helpers for Vendas."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException


_get_tenant_path: Callable[[str], str] | None = None
_sync_state_lock = threading.RLock()


def configure_vendas_context(
    *,
    get_tenant_path: Callable[[str], str],
    sync_state_lock=None,
) -> None:
    global _get_tenant_path, _sync_state_lock
    _get_tenant_path = get_tenant_path
    if sync_state_lock is not None:
        _sync_state_lock = sync_state_lock


def _tenant_path(client_id: str) -> str:
    if not callable(_get_tenant_path):
        raise RuntimeError("Vendas service context was not configured.")
    return _get_tenant_path(client_id)


def _vendas_sync_parse_date(valor: str, campo: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(valor or "").strip()[:10])
    except Exception:
        raise HTTPException(status_code=400, detail=f"Data invalida em {campo}. Use YYYY-MM-DD.")


def _vendas_sync_dias_periodo(data_inicio: str, data_fim: str) -> list[str]:
    inicio = _vendas_sync_parse_date(data_inicio, "data_inicio")
    fim = _vendas_sync_parse_date(data_fim, "data_fim")
    if inicio > fim:
        raise HTTPException(status_code=400, detail="Data inicial nao pode ser maior que a data final.")
    total = (fim - inicio).days + 1
    return [(inicio + dt.timedelta(days=i)).isoformat() for i in range(total)]


def _vendas_sync_state_path(client_id: str) -> str:
    return os.path.join(_tenant_path(client_id), "vendas_sync_state.json")


def _vendas_sync_load_state(client_id: str) -> dict:
    path = _vendas_sync_state_path(client_id)
    if not os.path.exists(path):
        return {"jobs": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return {"jobs": {}}
        if not isinstance(state.get("jobs"), dict):
            state["jobs"] = {}
        return state
    except Exception:
        return {"jobs": {}}


def _vendas_sync_save_state(client_id: str, state: dict):
    path = _vendas_sync_state_path(client_id)
    pasta = os.path.dirname(path)
    os.makedirs(pasta, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="vendas_sync_state_", suffix=".tmp", dir=pasta, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state or {"jobs": {}}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        ultimo_erro = None
        for tentativa in range(6):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError as exc:
                ultimo_erro = exc
                time.sleep(0.05 * (tentativa + 1))
        if ultimo_erro:
            raise ultimo_erro
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _vendas_sync_job_key(loja: str, data_inicio: str, data_fim: str, forcar_resync: bool) -> str:
    base = "|".join([
        str(loja or "").strip().lower(),
        str(data_inicio or "").strip()[:10],
        str(data_fim or "").strip()[:10],
        "1" if forcar_resync else "0",
    ])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _vendas_sync_prepare_job(client_id: str, req: Any, dias: list[str]) -> tuple[str, dict]:
    with _sync_state_lock:
        state = _vendas_sync_load_state(client_id)
        jobs = state.setdefault("jobs", {})
        key = _vendas_sync_job_key(req.loja, req.data_inicio, req.data_fim, req.forcar_resync)
        job = jobs.get(key)
        precisa_novo = (
            not isinstance(job, dict)
            or job.get("status") == "complete"
            or job.get("loja") != req.loja
            or job.get("data_inicio") != req.data_inicio
            or job.get("data_fim") != req.data_fim
            or bool(job.get("forcar_resync")) != bool(req.forcar_resync)
        )
        if precisa_novo:
            job = {
                "id": key,
                "loja": req.loja,
                "data_inicio": req.data_inicio,
                "data_fim": req.data_fim,
                "forcar_resync": bool(req.forcar_resync),
                "dias_total": len(dias),
                "dias_concluidos": [],
                "dia_atual": None,
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        else:
            job["status"] = "running"
            job["dias_total"] = len(dias)
            job["updated_at"] = datetime.now().isoformat()
            job.pop("ultimo_erro", None)
            job.pop("interrupted_at", None)
        jobs[key] = job
        state["active_key"] = key
        _vendas_sync_save_state(client_id, state)
        return key, job


def _vendas_sync_update_job(client_id: str, key: str, updates: dict) -> dict:
    with _sync_state_lock:
        state = _vendas_sync_load_state(client_id)
        jobs = state.setdefault("jobs", {})
        job = jobs.get(key) if isinstance(jobs.get(key), dict) else {"id": key}
        job.update(updates or {})
        job["updated_at"] = datetime.now().isoformat()
        jobs[key] = job
        state["active_key"] = key

        if len(jobs) > 25:
            ordenados = sorted(
                jobs.items(),
                key=lambda item: str((item[1] or {}).get("updated_at") or ""),
                reverse=True,
            )
            state["jobs"] = dict(ordenados[:25])

        _vendas_sync_save_state(client_id, state)
        return job
