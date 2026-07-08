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
        PERGUNTAS_AUTOMACAO_BG_RUNNING.discard(key)
        PERGUNTAS_AUTOMACAO_BG_NEXT_CHECKS[key] = agora + max(60, int(intervalo_segundos or 600))
        PERGUNTAS_AUTOMACAO_BG_LAST_RESULTS[key] = {
            "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "success": not bool(erro),
            "erro": erro,
            "enviadas": len((resultado or {}).get("enviadas") or []),
            "novas_pendentes": len((resultado or {}).get("novas_pendentes") or []),
            "erros": len((resultado or {}).get("erros") or []),
        }


def _perguntas_automacao_bg_executar(client_id: str, loja: str, tipo: str, intervalo_segundos: int) -> None:
    key = _perguntas_automacao_bg_key(client_id, loja, tipo)
    if not _perguntas_automacao_bg_marcar_inicio(key, time.time()):
        return
    try:
        if tipo == "pos_venda":
            resultado = ml_pos_venda_automacao_poll(loja=loja, max_per_store=2, client_id=client_id)
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

PEER_EXPORTS = ['_perguntas_automacao_bg_tenants', '_perguntas_automacao_bg_key', '_perguntas_automacao_bg_marcar_inicio', '_perguntas_automacao_bg_finalizar', '_perguntas_automacao_bg_executar', '_perguntas_automacao_bg_tick', '_perguntas_automacao_bg_worker', '_perguntas_automacao_iniciar_background']
__all__ = PEER_EXPORTS + ["configure_perguntas_pos_venda_automacao_runtime"]

configure_perguntas_pos_venda_automacao_runtime()
