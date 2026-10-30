"""Immediate, tenant-scoped saved summaries with bounded background refreshes.

The synchronous query remains the source of calculations. A saved response is
published only after its complete read, source and invalidation fences pass.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
import copy
import math
from pathlib import Path
import re
import threading
import time
from typing import Callable

from . import performance, persistent_cache as disk
from .dependencies import get_tenant_path, get_vendas_dependencies


MAX_WORKERS = 2
MAX_PENDING = 16
MAX_JOB_RECORDS = 128
ERROR_RETRY_SECONDS = 15
_POOL = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="vendas-summary")
_SLOTS = threading.BoundedSemaphore(MAX_WORKERS + MAX_PENDING)
_LOCK = threading.RLock()
_JOBS: OrderedDict[tuple, "_Job"] = OrderedDict()
_FILTERS = ("data_inicio", "data_fim", "sku", "loja", "unidade_negocio", "incluir_ebazar")
_SAVED_META = "_saved_summary"
_ERROR = "Não foi possível atualizar as vendas agora. Os últimos dados salvos foram mantidos."


@dataclass
class _Job:
    state: str = "processando"
    finished_at: float = 0


def _arguments(arguments: dict) -> dict:
    return {"client_id": arguments["client_id"], **{
        name: arguments.get(name, False if name == "incluir_ebazar" else None) for name in _FILTERS
    }}


def _sources(arguments: dict) -> tuple:
    from .repository import _vendas_resumo_cache_paths

    return performance.vendas_sources_signature(_vendas_resumo_cache_paths(arguments))


def _generation(arguments: dict, *, create: bool):
    return disk.capture(str(get_vendas_dependencies().paths.info_dir),
                        get_tenant_path(arguments["client_id"]), create=create)


def _key(generation, arguments: dict) -> str:
    filters = tuple((name, performance._freeze(arguments[name])) for name in _FILTERS)
    # Store names can be reused after account deletion/rebinding. Saved-only
    # reads fence identity configuration without loading the product catalog.
    identity_sources = performance.vendas_sources_signature([
        Path(generation.tenant_root) / name for name in (
            "lojas_config.json", "lojas_config.json.bak", "lojas_sync_tombstones.json",
        )
    ])
    return disk.identity(generation, "resumo-saved", arguments["client_id"], filters, identity_sources)


def _revision(generation, sources: tuple) -> str:
    # Sources contain physical paths; only the opaque digest is serialized.
    return disk._digest([generation.global_revision, generation.tenant_revision, sources])


def _load(generation, key: str) -> dict | None:
    payload = disk.load(generation, "resumo", key)
    marker = payload.get("meta", {}).get(_SAVED_META) if payload else None
    if not isinstance(marker, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(marker.get("revisao", ""))):
        return None
    timestamp = marker.get("salvo_em")
    if (not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
            or timestamp < 0 or timestamp > time.time() + 60):
        return None
    return payload


def _response(saved: dict | None, state: str, *, updating=False, revision="", error=False):
    payload = copy.deepcopy(saved) if saved else {"success": True, "rows": [], "totals": {}, "meta": {}}
    meta = payload.setdefault("meta", {})
    marker = meta.pop(_SAVED_META, {})
    meta["leitura"] = {"estado": state, "possui_salvo": saved is not None,
                       "atualizando": bool(updating), "revisao": marker.get("revisao", revision),
                       "salvo_em": marker.get("salvo_em")}
    if error:
        meta["leitura"]["mensagem"] = _ERROR
    return payload, 200 if saved is not None or state == "atualizado" else 202


def _run(job_key, job, calculate, arguments, generation, key, sources, revision):
    confidence = [True]
    token = performance._READ_CONFIDENCE.set(confidence)
    try:
        def unchanged():
            return (disk.current(generation) and _key(generation, arguments) == key
                    and _sources(arguments) == sources)

        if not unchanged():
            raise ValueError("Read context changed")
        result = calculate(**arguments)
        if not confidence[0] or not disk.valid_payload("resumo", result) or not unchanged():
            raise ValueError("Incomplete summary read")
        payload = copy.deepcopy(result)
        meta = payload.setdefault("meta", {})
        meta.pop("leitura", None)
        meta[_SAVED_META] = {"revisao": revision, "salvo_em": time.time()}
        if not disk.save(generation, "resumo", key, payload, unchanged):
            raise ValueError("Summary snapshot unavailable")
        with _LOCK:
            job.state = "atualizado"
    except Exception:
        # Domain/transport errors and partial reads cannot replace the last valid
        # snapshot or disclose internal exceptions in the polling response.
        with _LOCK:
            job.state = "erro"
    finally:
        performance._READ_CONFIDENCE.reset(token)
        with _LOCK:
            job.finished_at = time.monotonic()
        _SLOTS.release()


def _schedule(job_key, calculate, arguments, generation, key, sources, revision):
    with _LOCK:
        previous = _JOBS.get(job_key)
        if previous is not None:
            reusable = previous.state == "processando"
            if previous.state == "atualizado":
                published = _load(generation, key)
                reusable = bool(published and published["meta"][_SAVED_META]["revisao"] == revision)
            elif previous.state == "erro":
                reusable = time.monotonic() - previous.finished_at < ERROR_RETRY_SECONDS
            if reusable:
                _JOBS.move_to_end(job_key)
                return previous
        if not _SLOTS.acquire(blocking=False):
            return None
        while len(_JOBS) >= MAX_JOB_RECORDS:
            removable = next((item for item, value in _JOBS.items() if value.state != "processando"), None)
            if removable is None:
                _SLOTS.release()
                return None
            _JOBS.pop(removable)
        job = _Job()
        _JOBS[job_key] = job
        context = copy_context()
        try:
            _POOL.submit(context.run, _run, job_key, job, calculate, dict(arguments),
                         generation, key, sources, revision)
        except Exception:
            _JOBS.pop(job_key, None)
            _SLOTS.release()
            return None
        return job


def read_summary(calculate: Callable, arguments: dict, *, saved_only: bool = False):
    """Return immediately; only the opted-in stale mode may schedule work."""
    arguments = _arguments(arguments)
    try:
        generation = _generation(arguments, create=not saved_only)
        if generation is None:
            return _response(None, "processando" if saved_only else "erro", error=not saved_only)
        key = _key(generation, arguments)

        def respond(saved, state, **options):
            if saved is not None and (not disk.current(generation) or _key(generation, arguments) != key):
                return _response(None, "processando")
            return _response(saved, state, **options)

        saved = _load(generation, key)
        if saved_only:
            with _LOCK:
                updating = any(item[0] == key and item[1] == generation and value.state == "processando"
                               for item, value in _JOBS.items())
            return respond(saved, "salvo" if saved else "processando", updating=updating)
        try:
            sources = _sources(arguments)
            revision = _revision(generation, sources)
        except Exception:
            return respond(saved, "erro", error=True)
        marker = saved.get("meta", {}).get(_SAVED_META) if saved else None
        if marker and marker.get("revisao") == revision:
            return respond(saved, "atualizado")
        job_key = (key, generation, revision)
        job = _schedule(job_key, calculate, arguments, generation, key, sources, revision)
        # A short calculation may have published since the initial disk read.
        if job and job.state == "atualizado":
            published = _load(generation, key)
            if published is not None:
                saved = published
            if saved and saved["meta"][_SAVED_META]["revisao"] == revision:
                return respond(saved, "atualizado")
            return respond(saved, "erro", error=True)
        if job and job.state == "erro":
            return respond(saved, "erro", error=True)
        return respond(saved, "processando", updating=job is not None, revision=revision)
    except Exception:
        return _response(None, "erro", error=True)
