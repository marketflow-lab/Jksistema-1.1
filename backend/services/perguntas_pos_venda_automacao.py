"""Internal slice for perguntas_pos_venda_core."""

from __future__ import annotations

from __future__ import annotations
import base64
import copy
import csv
import functools
import hashlib
import html as html_lib
import io
import json
import logging
import math
import os
import random
import re
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.services import perguntas_pos_venda_store
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.vendas_sync_progress import _corrigir_texto_mojibake


def configure_perguntas_pos_venda_automacao_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    if peers:
        globals().update(peers)
    return runtime


configure_perguntas_pos_venda_automacao_runtime()


def _perguntas_automacao_bg_tenants() -> list[str]:
    tenants = []
    try:
        if not os.path.isdir(PASTA_INFO):
            return tenants
        for nome in os.listdir(PASTA_INFO):
            pasta = os.path.join(PASTA_INFO, nome)
            if not os.path.isdir(pasta):
                continue
            if os.path.exists(os.path.join(pasta, "perguntas_pos_venda_lojas_config.json")):
                tenants.append(nome)
    except Exception as exc:
        logger.warning("[ML PERGUNTAS AUTO BG] Falha ao listar clientes com automacao: %s", exc)
    return tenants


def _perguntas_automacao_bg_key(client_id: str, loja: str, tipo: str) -> str:
    return f"{str(client_id or '').strip()}::{str(loja or '').strip()}::{str(tipo or '').strip()}"


_PERGUNTAS_AUTOMACAO_ERRO_SEGREDO_RE = re.compile(
    r'''(?ix)
    (?P<prefix>
        ["']?
        (?:
            access[_\s-]?token
            | refresh[_\s-]?token
            | authorization
            | client[_\s-]?secret
            | api[_\s-]?key
            | password
            | senha
            | token
        )
        ["']?\s*[:=]\s*
    )
    (?:"[^"]*"|'[^']*'|[^\s,;}\]]+)
    '''
)
_PERGUNTAS_AUTOMACAO_ERRO_CREDENCIAL_RE = re.compile(
    r"(?i)\b(Bearer|Basic)\s+[^\s,;\"'}\]]+"
)
_PERGUNTAS_AUTOMACAO_ERRO_URL_QUERY_RE = re.compile(r"(?i)(https?://[^\s?#]+)[?#][^\s]+")
_PERGUNTAS_AUTOMACAO_QUESTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PERGUNTAS_AUTOMACAO_CHANGE_TOKEN_RE = re.compile(r"^[a-f0-9]{64}$")
_PERGUNTAS_AUTOMACAO_CACHE_RECHECK_SECONDS = 15


def _perguntas_automacao_bg_erro_publico(erro: Any) -> str:
    texto = re.sub(r"\s+", " ", str(erro or "")).strip()
    if not texto:
        return ""
    texto = _PERGUNTAS_AUTOMACAO_ERRO_CREDENCIAL_RE.sub(r"\1 [redacted]", texto)
    texto = _PERGUNTAS_AUTOMACAO_ERRO_SEGREDO_RE.sub(
        lambda match: f'{match.group("prefix")}[redacted]',
        texto,
    )
    texto = _PERGUNTAS_AUTOMACAO_ERRO_URL_QUERY_RE.sub(r"\1", texto)
    return texto[:500]


def _perguntas_automacao_bg_timestamp_iso(valor: Any) -> str | None:
    try:
        timestamp = float(valor or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _perguntas_automacao_bg_contagem(valor: Any) -> int:
    try:
        return max(0, int(valor or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _perguntas_automacao_bg_question_ids(valor: Any) -> list[str]:
    if not isinstance(valor, (list, tuple, set)):
        return []
    ids = {
        str(question_id).strip()
        for question_id in valor
        if _PERGUNTAS_AUTOMACAO_QUESTION_ID_RE.fullmatch(str(question_id).strip())
    }
    return sorted(ids)


def _perguntas_automacao_bg_change_token(key: str, question_ids: Any) -> str:
    payload = json.dumps(
        {
            "scope": str(key or ""),
            "question_ids": _perguntas_automacao_bg_question_ids(question_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _perguntas_automacao_bg_snapshot(resultado: Any) -> tuple[bool, list[str]]:
    snapshots = resultado.get("question_snapshots") if isinstance(resultado, dict) else None
    if not isinstance(snapshots, list):
        return False, []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        if snapshot.get("question_snapshot_complete") is True:
            return True, _perguntas_automacao_bg_question_ids(snapshot.get("question_ids"))
    return False, []


def _perguntas_automacao_pos_venda_sync_context(client_id: str, loja: str) -> dict[str, Any] | None:
    obter_cfg = globals().get("_obter_cfg_ml")
    obter_tenant_path = globals().get("get_tenant_path")
    if not callable(obter_cfg) or not callable(obter_tenant_path):
        return None
    try:
        cfg = obter_cfg(client_id, loja)
        seller_id = str((cfg or {}).get("user_id") or "").strip()
        tenant_path = str(obter_tenant_path(client_id) or "").strip()
    except Exception:
        return None
    if not seller_id or not tenant_path:
        return None
    return {"tenant_path": tenant_path, "seller_id": seller_id}


def _perguntas_automacao_pos_venda_sync_running(client_id: str, loja: str, seller_id: str) -> bool:
    try:
        from backend.services import perguntas_pos_venda_endpoints

        return bool(perguntas_pos_venda_endpoints._ml_pos_venda_sync_running(client_id, loja, seller_id))
    except Exception:
        return False


def _perguntas_automacao_pos_venda_preparar_sync(client_id: str, loja: str) -> dict[str, Any] | None:
    context = _perguntas_automacao_pos_venda_sync_context(client_id, loja)
    if not context:
        return None
    active = _perguntas_automacao_pos_venda_sync_running(client_id, loja, context["seller_id"])
    state = perguntas_pos_venda_store.recover_interrupted_sync(
        context["tenant_path"],
        loja,
        context["seller_id"],
        active=active,
    )
    return {**context, "state": state, "running": active}


def _perguntas_automacao_pos_venda_anotar_sync(
    resultado: dict,
    *,
    client_id: str,
    loja: str,
    context: dict[str, Any] | None,
) -> dict:
    if not context:
        return resultado
    seller_id = str(context.get("seller_id") or "").strip()
    tenant_path = str(context.get("tenant_path") or "").strip()
    running = _perguntas_automacao_pos_venda_sync_running(client_id, loja, seller_id)
    state = perguntas_pos_venda_store.get_state(tenant_path, loja, seller_id)
    if state.get("status") == "running" and not running:
        state = perguntas_pos_venda_store.recover_interrupted_sync(
            tenant_path,
            loja,
            seller_id,
            active=False,
        )

    resultado = dict(resultado or {})
    resultado["_cache_sync_status"] = "running" if running else str(state.get("status") or "idle")
    resultado["_cache_sync_cursor"] = max(0, int(state.get("cursor") or 0))
    has_output = bool(resultado.get("novas_pendentes") or resultado.get("enviadas"))
    if running and not has_output:
        resultado["_cache_sync_pending"] = True
        resultado["_retry_after_seconds"] = _PERGUNTAS_AUTOMACAO_CACHE_RECHECK_SECONDS
        return resultado

    sync_error = _perguntas_automacao_bg_erro_publico(state.get("error"))
    if str(state.get("status") or "") == "failed" and sync_error and not has_output:
        erros = list(resultado.get("erros") or [])
        if not any(
            isinstance(item, dict)
            and str(item.get("etapa") or "") == "cache_pos_venda"
            for item in erros
        ):
            erros.append({"loja": loja, "etapa": "cache_pos_venda", "erro": sync_error})
        resultado["erros"] = erros
    return resultado


def _perguntas_automacao_bg_worker_iniciado() -> bool:
    with PERGUNTAS_AUTOMACAO_BG_LOCK:
        return bool(PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED)


def _perguntas_automacao_bg_status(client_id: str, loja: str, tipo: str = "perguntas") -> dict:
    """Return a tenant-scoped, secret-free snapshot of one automation worker."""
    key = _perguntas_automacao_bg_key(client_id, loja, tipo)
    with PERGUNTAS_AUTOMACAO_BG_LOCK:
        executando = key in PERGUNTAS_AUTOMACAO_BG_RUNNING
        proxima_timestamp = PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS.get(key)
        resultado = dict(PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS.get(key) or {})

    contagens = {
        "enviadas": _perguntas_automacao_bg_contagem(resultado.get("enviadas")),
        "novas_pendentes": _perguntas_automacao_bg_contagem(resultado.get("novas_pendentes")),
        "erros": _perguntas_automacao_bg_contagem(resultado.get("erros")),
    }
    sucesso = None
    if resultado:
        sucesso = bool(resultado.get("success")) and contagens["erros"] == 0

    change_token = str(resultado.get("change_token") or "").strip().lower()
    if not _PERGUNTAS_AUTOMACAO_CHANGE_TOKEN_RE.fullmatch(change_token):
        change_token = None
    question_ids = _perguntas_automacao_bg_question_ids(resultado.get("question_ids"))
    new_question_ids = _perguntas_automacao_bg_question_ids(resultado.get("new_question_ids"))

    return {
        "executando": executando,
        "ultima_checagem": str(resultado.get("updated_at") or "").strip() or None,
        "proxima_checagem": None if executando else _perguntas_automacao_bg_timestamp_iso(proxima_timestamp),
        "sucesso": sucesso,
        "erro": _perguntas_automacao_bg_erro_publico(resultado.get("erro")),
        "contagens": contagens,
        "change_token": change_token,
        "question_ids": question_ids,
        "new_question_ids": new_question_ids,
        "new_questions_count": len(new_question_ids),
        "question_snapshot_complete": bool(resultado.get("question_snapshot_complete")),
    }


def _perguntas_automacao_bg_marcar_inicio(key: str, agora: float) -> bool:
    with PERGUNTAS_AUTOMACAO_BG_LOCK:
        if key in PERGUNTAS_AUTOMACAO_BG_RUNNING:
            return False
        proxima = float(PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS.get(key) or 0)
        if proxima and agora < proxima:
            return False
        PERGUNTAS_AUTOMACAO_BG_RUNNING.add(key)
        return True


def _perguntas_automacao_bg_finalizar(
    key: str,
    intervalo_segundos: int,
    resultado: dict | None = None,
    erro: str = "",
) -> None:
    agora = time.time()
    with PERGUNTAS_AUTOMACAO_BG_LOCK:
        anterior = dict(PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS.get(key) or {})
        snapshot_inicializado = bool(anterior.get("question_snapshot_initialized"))
        question_ids_anteriores = _perguntas_automacao_bg_question_ids(anterior.get("question_ids"))
        change_token_anterior = str(anterior.get("change_token") or "").strip().lower()
        if not _PERGUNTAS_AUTOMACAO_CHANGE_TOKEN_RE.fullmatch(change_token_anterior):
            change_token_anterior = ""

        snapshot_completo, question_ids_atuais = _perguntas_automacao_bg_snapshot(resultado)
        if erro or not snapshot_completo:
            question_ids_finais = question_ids_anteriores
            change_token = change_token_anterior
            new_question_ids = []
            snapshot_completo = False
        else:
            question_ids_finais = question_ids_atuais
            change_token = _perguntas_automacao_bg_change_token(key, question_ids_finais)
            new_question_ids = (
                sorted(set(question_ids_finais) - set(question_ids_anteriores))
                if snapshot_inicializado
                else []
            )
            snapshot_inicializado = True

        retry_after_seconds = _perguntas_automacao_bg_contagem(
            (resultado or {}).get("_retry_after_seconds")
        )
        if retry_after_seconds:
            next_delay = max(5, min(int(intervalo_segundos or 600), retry_after_seconds))
        else:
            next_delay = max(60, int(intervalo_segundos or 600))

        PERGUNTAS_AUTOMACAO_BG_RUNNING.discard(key)
        PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS[key] = agora + next_delay
        PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key] = {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "success": not bool(erro),
            "erro": erro,
            "enviadas": len((resultado or {}).get("enviadas") or []),
            "novas_pendentes": len((resultado or {}).get("novas_pendentes") or []),
            "erros": len((resultado or {}).get("erros") or []),
            "question_snapshot_initialized": snapshot_inicializado,
            "question_snapshot_complete": snapshot_completo,
            "question_ids": question_ids_finais,
            "change_token": change_token,
            "new_question_ids": new_question_ids,
            "cache_sync_status": str((resultado or {}).get("_cache_sync_status") or ""),
            "cache_sync_pending": bool((resultado or {}).get("_cache_sync_pending")),
            "cache_sync_cursor": _perguntas_automacao_bg_contagem(
                (resultado or {}).get("_cache_sync_cursor")
            ),
        }


def _perguntas_automacao_bg_executar(client_id: str, loja: str, tipo: str, intervalo_segundos: int) -> None:
    key = _perguntas_automacao_bg_key(client_id, loja, tipo)
    if not _perguntas_automacao_bg_marcar_inicio(key, time.time()):
        return
    try:
        if tipo == "pos_venda":
            sync_context = _perguntas_automacao_pos_venda_preparar_sync(client_id, loja)
            resultado = ml_pos_venda_automacao_poll(loja=loja, max_per_store=2, client_id=client_id)
            if not isinstance(resultado, dict):
                resultado = {}
            resultado = _perguntas_automacao_pos_venda_anotar_sync(
                resultado,
                client_id=client_id,
                loja=loja,
                context=sync_context,
            )
        else:
            resultado = ml_perguntas_automacao_poll(loja=loja, max_per_store=3, client_id=client_id)
        if not isinstance(resultado, dict):
            resultado = {}
        logger.info(
            "[ML PERGUNTAS AUTO BG] tenant=%s loja=%s tipo=%s enviadas=%s pendentes=%s erros=%s",
            client_id,
            loja,
            tipo,
            len(resultado.get("enviadas") or []),
            len(resultado.get("novas_pendentes") or []),
            len(resultado.get("erros") or []),
        )
        _perguntas_automacao_bg_finalizar(key, intervalo_segundos, resultado=resultado)
    except Exception as exc:
        logger.exception("[ML PERGUNTAS AUTO BG] Falha tenant=%s loja=%s tipo=%s", client_id, loja, tipo)
        _perguntas_automacao_bg_finalizar(key, intervalo_segundos, erro=str(getattr(exc, "detail", None) or exc))


def _perguntas_automacao_bg_tick() -> None:
    for client_id in _perguntas_automacao_bg_tenants():
        configs_lojas = _perguntas_loja_configs_carregar(client_id)
        for loja_cfg in carregar_lojas(client_id) or []:
            if not isinstance(loja_cfg, dict):
                continue
            nome_loja = str(loja_cfg.get("nome") or "").strip()
            if not nome_loja:
                continue

            config = _perguntas_loja_config_normalizar(configs_lojas.get(nome_loja))
            if not config.get("responder_automaticamente"):
                continue

            integracoes = loja_cfg.get("integracoes") or {}
            ml_cfg = integracoes.get("mercadolivre") if isinstance(integracoes, dict) else {}
            if not _ml_oauth_status(ml_cfg).get("conectado"):
                continue

            intervalo_segundos = max(
                15,
                int(float(config.get("intervalo_minutos") or PERGUNTAS_AUTOMACAO_INTERVALO_PADRAO_MIN) * 60),
            )
            _perguntas_automacao_bg_executar(client_id, nome_loja, "perguntas", intervalo_segundos)
            if config.get("habilitar_pos_venda_automatico"):
                _perguntas_automacao_bg_executar(client_id, nome_loja, "pos_venda", intervalo_segundos)


def _perguntas_automacao_bg_worker() -> None:
    time.sleep(4)
    while True:
        try:
            _perguntas_automacao_bg_tick()
        except Exception:
            logger.exception("[ML PERGUNTAS AUTO BG] Falha inesperada no verificador")
        time.sleep(10)


def _perguntas_automacao_iniciar_background() -> None:
    if _agent_service_only():
        logger.info("[AGENT SERVICE] Automacao de perguntas/pos-venda desativada neste servico.")
        return
    global PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED
    with PERGUNTAS_AUTOMACAO_BG_LOCK:
        if PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED:
            return
        PERGUNTAS_AUTOMACAO_BG_THREAD_STARTED = True
    threading.Thread(
        target=_perguntas_automacao_bg_worker,
        name="ml-perguntas-pos-venda-automacao",
        daemon=True,
    ).start()

PEER_EXPORTS = ['_perguntas_automacao_bg_tenants', '_perguntas_automacao_bg_key', '_perguntas_automacao_bg_erro_publico', '_perguntas_automacao_bg_timestamp_iso', '_perguntas_automacao_bg_contagem', '_perguntas_automacao_bg_question_ids', '_perguntas_automacao_bg_change_token', '_perguntas_automacao_bg_snapshot', '_perguntas_automacao_bg_worker_iniciado', '_perguntas_automacao_bg_status', '_perguntas_automacao_bg_marcar_inicio', '_perguntas_automacao_bg_finalizar', '_perguntas_automacao_bg_executar', '_perguntas_automacao_bg_tick', '_perguntas_automacao_bg_worker', '_perguntas_automacao_iniciar_background']
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_automacao_runtime"]

configure_perguntas_pos_venda_automacao_runtime()
