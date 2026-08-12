"""Internal slice for promocoes_api."""

from __future__ import annotations

from __future__ import annotations
import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
import pandas as pd
import requests
from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from backend.schemas import (
    PromoAnaliseApiRequest,
    PromoAplicarParticipacaoRequest,
    PromoAutomacaoConfigRequest,
)
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
from backend.services.promocoes_common import *
from backend.services.promocoes_core import *

logger = logging.getLogger("jk_sistema")
_PROMOCOES_RUNTIME_GET_TENANT_ID = None


def configure_promocoes_api_jobs_runtime(runtime_module=None, peers=None):
    _configure_common = globals().get("configure_promocoes_common_runtime")
    if callable(_configure_common):
        try:
            _configure_common(runtime_module)
        except TypeError:
            _configure_common()
    runtime = bind_runtime_globals(globals(), runtime_module)
    runtime_get_tenant_id = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if callable(runtime_get_tenant_id):
        globals()["_PROMOCOES_RUNTIME_GET_TENANT_ID"] = runtime_get_tenant_id
    if peers:
        globals().update({name: value for name, value in peers.items() if name != "get_tenant_id"})
    return runtime


configure_promocoes_api_jobs_runtime()


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    resolver = globals().get("_PROMOCOES_RUNTIME_GET_TENANT_ID")
    if resolver is None:
        runtime = globals().get("_runtime")
        resolver = getattr(runtime, "get_tenant_id", None) if runtime is not None else None
    if not callable(resolver) or resolver is globals().get("_PROMOCOES_PLACEHOLDER_GET_TENANT_ID"):
        raise RuntimeError("Promocoes API runtime was not configured.")
    result = resolver(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


_PROMOCOES_PLACEHOLDER_GET_TENANT_ID = get_tenant_id


def _promo_analise_background_worker(
    *,
    job_id: str,
    loja: str,
    promocao_a_id: str,
    promocao_a_type: str,
    margem_minima: float,
    margem_tolerancia: float,
    promocoes_b_meta: str,
    files_payload: list[dict],
    client_id: str,
):
    try:
        _promo_job_set(
            job_id,
            status="running",
            progress=15,
            message="Processando Promocao 1 e arquivos da Promocao 2 em segundo plano...",
        )
        uploads = [
            UploadFile(file=io.BytesIO(item["content"]), filename=item["filename"])
            for item in files_payload
        ]
        resultado = asyncio.run(
            analisar_promo_via_api_com_arquivos(
                loja=loja,
                promocao_a_id=promocao_a_id,
                promocao_a_type=promocao_a_type,
                margem_minima=margem_minima,
                margem_tolerancia=margem_tolerancia,
                promocoes_b_meta=promocoes_b_meta,
                files=uploads,
                client_id=client_id,
            )
        )
        _promo_job_set(
            job_id,
            status="completed",
            progress=100,
            message="Analise concluida.",
            result=resultado,
        )
    except Exception as e:
        logger.exception("[PROMO API BG] Falha no job %s", job_id)
        _promo_job_set(
            job_id,
            status="error",
            progress=100,
            message="Falha ao processar a analise.",
            error=str(e),
        )


PROMO_AUTOMACAO_INTERVAL_UNITS = {
    "minutes": 1,
    "hours": 60,
    "days": 1440,
    "weeks": 10080,
}


def _promo_automacao_path(client_id: str) -> str:
    return os.path.join(get_tenant_path(str(client_id or "default")), "promo_automacao_api.json")


def _promo_automacao_carregar(client_id: str) -> dict:
    caminho = _promo_automacao_path(client_id)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.exception("[PROMO AUTO] Falha ao carregar configuracao tenant=%s", client_id)
        return {}


def _promo_automacao_salvar(client_id: str, cfg: dict) -> dict:
    payload = dict(cfg or {})
    payload["updated_at"] = time.time()
    caminho = _promo_automacao_path(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)
    return payload


def _promo_automacao_intervalo(payload: dict) -> tuple[str, int, int]:
    unit = str(payload.get("interval_unit") or payload.get("intervalUnit") or "minutes").strip().lower()
    if unit not in PROMO_AUTOMACAO_INTERVAL_UNITS:
        unit = "minutes"
    fator = PROMO_AUTOMACAO_INTERVAL_UNITS[unit]
    try:
        valor = int(round(float(str(payload.get("interval_value") or payload.get("intervalValue") or 1).replace(",", "."))))
    except Exception:
        valor = 1
    valor = max(1, valor)
    minutos = int(payload.get("interval_minutes") or payload.get("intervalMinutes") or 0)
    if minutos <= 0:
        minutos = max(1, valor * fator)
    minutos = max(1, min(minutos, 12 * 10080))
    return unit, valor, minutos


def _promo_automacao_sanitizar(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    unit, valor, minutos = _promo_automacao_intervalo(payload)
    promos = payload.get("promocoes_b_meta") or payload.get("promocoesBMeta") or []
    promos_out = []
    if isinstance(promos, list):
        for meta in promos:
            if not isinstance(meta, dict):
                continue
            promo_id = str(meta.get("promo_b_id") or meta.get("value") or meta.get("id") or "").strip()
            if not promo_id:
                continue
            promo_type = str(meta.get("promo_b_type") or meta.get("promoType") or meta.get("promotion_type") or "").strip()
            if promo_type in {"-", "None", "null"}:
                promo_type = ""
            promos_out.append({
                "promo_b_id": promo_id,
                "promo_b_type": promo_type,
                "promo_texto": str(meta.get("promo_texto") or meta.get("text") or meta.get("displayText") or promo_id).strip(),
                "active_count": _promo_meta_contagem(meta, "active_count", "activeCount", "active", "ativos"),
                "eligible_count": _promo_meta_contagem(meta, "eligible_count", "eligibleCount", "eligible", "elegiveis"),
            })
    next_raw = _parse_float_flex(payload.get("next_run_at") or payload.get("nextRunAt"))
    tolerancia_raw = _parse_float_flex(payload.get("margem_tolerancia") or payload.get("margemTolerancia"))
    return {
        "enabled": bool(payload.get("enabled")),
        "approval_required": payload.get("approval_required", payload.get("approvalRequired", True)) is not False,
        "interval_unit": unit,
        "interval_value": valor,
        "interval_minutes": minutos,
        "next_run_at": float(next_raw or 0),
        "loja": str(payload.get("loja") or "").strip(),
        "promocao_a_id": str(payload.get("promocao_a_id") or payload.get("promocaoAId") or "").strip(),
        "promocao_a_type": str(payload.get("promocao_a_type") or payload.get("promocaoAType") or "").strip(),
        "margem_minima": _parse_float_flex(payload.get("margem_minima") or payload.get("margemMinima")) or 15.0,
        "margem_tolerancia": max(0.0, min(100.0, float(tolerancia_raw or 0.0))),
        "promocoes_b_meta": promos_out,
    }


def _promo_automacao_public_payload(client_id: str, cfg: dict) -> dict:
    due_flag = bool((cfg or {}).get("due"))
    cfg = _promo_automacao_sanitizar({**(cfg or {}), "enabled": bool((cfg or {}).get("enabled"))})
    bruto = _promo_automacao_carregar(client_id)
    merged = {**bruto, **cfg}
    return {
        "success": True,
        "config": {
            **cfg,
            "next_run_at": float(merged.get("next_run_at") or 0),
            "last_job_id": str(merged.get("last_job_id") or ""),
            "last_job_status": str(merged.get("last_job_status") or ""),
            "last_job_message": str(merged.get("last_job_message") or ""),
            "last_run_at": float(merged.get("last_run_at") or 0),
            "last_completed_at": float(merged.get("last_completed_at") or 0),
            "last_error": str(merged.get("last_error") or ""),
            "last_apply_result": merged.get("last_apply_result") if isinstance(merged.get("last_apply_result"), dict) else None,
            "due": due_flag or bool(merged.get("due")),
            "updated_at": float(merged.get("updated_at") or 0),
        },
        "server_time": time.time(),
    }


def _promo_start_api_worker_job(
    *,
    client_id: str,
    loja: str,
    promocao_a_id: str,
    promocao_a_type: str = "",
    margem_minima: float = 15.0,
    margem_tolerancia: float = 0.0,
    promocoes_b_meta: str = "[]",
) -> dict:
    if not _ensure_promo_worker_running():
        raise HTTPException(status_code=503, detail="Worker dedicado de promocoes indisponivel.")

    data = {
        "loja": loja,
        "promocao_a_id": promocao_a_id,
        "promocao_a_type": promocao_a_type or "",
        "margem_minima": str(margem_minima),
        "margem_tolerancia": str(max(0.0, min(100.0, float(margem_tolerancia or 0.0)))),
        "promocoes_b_meta": promocoes_b_meta,
        "client_id": client_id,
    }
    try:
        resp = requests.post(
            f"{PROMO_WORKER_URL}/api/promo/jobs/start-api",
            data=data,
            timeout=60,
        )
        payload = _safe_json_response(resp)
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha ao encaminhar job API")
        raise HTTPException(status_code=503, detail=f"Falha ao acionar worker dedicado: {e}")

    if not resp.ok:
        detalhe = payload.get("detail") or payload.get("error") or resp.reason or "Falha no worker dedicado de promocoes."
        raise HTTPException(status_code=resp.status_code, detail=f"Worker promocoes respondeu HTTP {resp.status_code}: {detalhe}")
    return payload


def _promo_consultar_worker_job(job_id: str, client_id: str) -> dict:
    if not job_id:
        return {}
    if not _ensure_promo_worker_running():
        return {}
    try:
        resp = requests.get(
            f"{PROMO_WORKER_URL}/api/promo/jobs/{job_id}",
            params={"client_id": client_id},
            timeout=20,
        )
        payload = _safe_json_response(resp)
        return payload if resp.ok and isinstance(payload, dict) else {}
    except Exception:
        logger.exception("[PROMO AUTO] Falha ao consultar job %s", job_id)
        return {}


def _promo_automacao_linha_valor(row: dict, aliases: list[str]) -> Any:
    aliases_norm = {_normalizar_coluna_exportacao(alias) for alias in aliases}
    for chave, valor in (row or {}).items():
        if _normalizar_coluna_exportacao(chave) in aliases_norm:
            return valor
    return ""


def _promo_automacao_montar_participacoes(resultado: dict, loja: str) -> dict:
    analises = resultado.get("analises") if isinstance(resultado, dict) else []
    if not isinstance(analises, list) or not analises:
        analises = [{
            "promo_b_id": resultado.get("promocao_b_id") or "",
            "promo_b_type": "",
            "promo_b_nome": "Promocao analisada",
            "data": resultado.get("data") if isinstance(resultado, dict) else [],
        }]
    promocoes = []
    for idx, analise in enumerate(analises):
        if not isinstance(analise, dict):
            continue
        promotion_id = str(analise.get("promo_b_id") or analise.get("promotion_id") or "").strip()
        promotion_type = str(analise.get("promo_b_type") or analise.get("promotion_type") or "").strip()
        nome = str(analise.get("promo_b_nome") or analise.get("tab_label") or f"Promocao {idx + 1}").strip()
        rows = analise.get("data") if isinstance(analise.get("data"), list) else []
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            decisao = _normalizar_decisao_planilha(
                _promo_automacao_linha_valor(row, ["AÃ§Ã£o", "Acao", "Participar ou nÃ£o", "Participar ou nao"])
            )
            if decisao != "Participar":
                continue
            if "action_financeiro_exato" in row:
                contexto_exato = row.get("action_financeiro_exato")
                if not (
                    contexto_exato is True
                    or str(contexto_exato or "").strip().lower() in {"1", "true", "sim", "yes"}
                ):
                    continue
            item_id = _promo_normalizar_mlb(_promo_automacao_linha_valor(row, ["MLB", "mlb", "Item ID", "item_id", "AnÃºncio", "Anuncio"]))
            if not item_id:
                continue
            items.append({
                "item_id": item_id,
                "offer_id": str(_promo_automacao_linha_valor(row, ["action_offer_id", "offer_id", "offerId", "ref_id", "refId"]) or "").strip(),
                "deal_price": _parse_float_flex(_promo_automacao_linha_valor(row, ["action_deal_price", "deal_price", "preco_promocional_ml", "PreÃ§o Promocional ML", "Preco Promocional ML", "PreÃ§o Final ML", "Preco Final ML", "PreÃ§o Final PromoÃ§Ã£o 2", "Preco Final Promocao 2"])),
                "discount_percentage": _parse_float_flex(_promo_automacao_linha_valor(row, ["action_discount_percentage", "ML % Campanha", "% Fixa", "Desconto ML %", "discount_percentage", "percentual"])),
                "sku": str(_promo_automacao_linha_valor(row, ["SKU", "sku"]) or "").strip(),
                "titulo": str(_promo_automacao_linha_valor(row, ["TÃ­tulo", "Titulo", "title"]) or "").strip(),
            })
        if promotion_id and items:
            promocoes.append({
                "promotion_id": promotion_id,
                "promotion_type": promotion_type,
                "nome": nome,
                "items": items,
            })
    return {"loja": loja, "promocoes": promocoes}


def _promo_automacao_refresh_job_state(client_id: str, cfg: dict, *, aplicar: bool = False) -> tuple[dict, bool]:
    job_id = str((cfg or {}).get("last_job_id") or "").strip()
    if not job_id:
        return cfg, False
    payload = _promo_consultar_worker_job(job_id, client_id)
    if not payload:
        return cfg, False
    status = str(payload.get("status") or "").lower()
    mudou = False
    if status:
        cfg["last_job_status"] = status
        cfg["last_job_message"] = str(payload.get("message") or "")
        cfg["last_job_progress"] = int(payload.get("progress") or 0)
        mudou = True
    if status == "completed":
        cfg["last_completed_at"] = float(payload.get("updated_at") or time.time())
        cfg["last_error"] = ""
        if aplicar and not bool(cfg.get("approval_required")) and cfg.get("last_applied_job_id") != job_id:
            try:
                result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                participacoes = _promo_automacao_montar_participacoes(result, str(cfg.get("loja") or ""))
                total = sum(len(p.get("items") or []) for p in participacoes.get("promocoes", []))
                if total > 0:
                    apply_result = aplicar_participacoes_promocoes(
                        PromoAplicarParticipacaoRequest(**participacoes),
                        client_id=client_id,
                    )
                    cfg["last_apply_result"] = apply_result
                else:
                    cfg["last_apply_result"] = {"success": True, "message": "Nenhum item com acao Participar."}
                cfg["last_applied_job_id"] = job_id
                mudou = True
            except Exception as exc:
                logger.exception("[PROMO AUTO] Falha ao aplicar participacoes tenant=%s job=%s", client_id, job_id)
                cfg["last_error"] = str(getattr(exc, "detail", None) or exc)
                mudou = True
    elif status in {"error", "canceled", "cancelled"}:
        cfg["last_error"] = str(payload.get("error") or payload.get("message") or "")
        mudou = True
    return cfg, mudou


def _promo_automacao_config_pronta(cfg: dict) -> bool:
    return bool(
        cfg.get("enabled")
        and str(cfg.get("loja") or "").strip()
        and str(cfg.get("promocao_a_id") or "").strip()
        and isinstance(cfg.get("promocoes_b_meta"), list)
        and len(cfg.get("promocoes_b_meta") or []) > 0
    )


def _promo_automacao_processar_tenant(client_id: str) -> None:
    with PROMO_AUTOMACAO_LOCK:
        cfg = _promo_automacao_carregar(client_id)
        if not cfg:
            return
        cfg = {**cfg, **_promo_automacao_sanitizar(cfg)}
        cfg, mudou = _promo_automacao_refresh_job_state(client_id, cfg, aplicar=True)
        status_atual = str(cfg.get("last_job_status") or "").lower()
        if status_atual in {"queued", "running"}:
            if mudou:
                _promo_automacao_salvar(client_id, cfg)
            return
        if not _promo_automacao_config_pronta(cfg):
            if mudou:
                _promo_automacao_salvar(client_id, cfg)
            return
        agora = time.time()
        next_run_at = float(cfg.get("next_run_at") or 0)
        if next_run_at <= 0:
            cfg["next_run_at"] = agora + int(cfg.get("interval_minutes") or 60) * 60
            _promo_automacao_salvar(client_id, cfg)
            return
        if next_run_at > agora:
            if mudou:
                _promo_automacao_salvar(client_id, cfg)
            return
        chave_execucao = str(client_id)
        if chave_execucao in PROMO_AUTOMACAO_RUNNING:
            return
        PROMO_AUTOMACAO_RUNNING.add(chave_execucao)
    try:
        logger.info("[PROMO AUTO] Iniciando analise automatica tenant=%s loja=%s", client_id, cfg.get("loja"))
        payload = _promo_start_api_worker_job(
            client_id=client_id,
            loja=str(cfg.get("loja") or ""),
            promocao_a_id=str(cfg.get("promocao_a_id") or ""),
            promocao_a_type=str(cfg.get("promocao_a_type") or ""),
            margem_minima=float(cfg.get("margem_minima") or 15.0),
            margem_tolerancia=float(cfg.get("margem_tolerancia") or 0.0),
            promocoes_b_meta=json.dumps(cfg.get("promocoes_b_meta") or [], ensure_ascii=False),
        )
        cfg["last_job_id"] = str(payload.get("job_id") or "")
        cfg["last_job_status"] = str(payload.get("status") or "queued")
        cfg["last_job_message"] = str(payload.get("message") or "Analise automatica iniciada pelo servidor.")
        cfg["last_run_at"] = time.time()
        cfg["last_error"] = ""
        cfg["next_run_at"] = time.time() + int(cfg.get("interval_minutes") or 60) * 60
    except Exception as exc:
        logger.exception("[PROMO AUTO] Falha ao iniciar analise automatica tenant=%s", client_id)
        cfg["last_error"] = str(getattr(exc, "detail", None) or exc)
        cfg["last_job_status"] = "error"
        cfg["next_run_at"] = time.time() + min(int(cfg.get("interval_minutes") or 60) * 60, 15 * 60)
    finally:
        with PROMO_AUTOMACAO_LOCK:
            PROMO_AUTOMACAO_RUNNING.discard(str(client_id))
            _promo_automacao_salvar(client_id, cfg)


def _promo_automacao_tenants() -> list[str]:
    tenants = []
    try:
        for nome in os.listdir(PASTA_INFO):
            pasta = os.path.join(PASTA_INFO, nome)
            if os.path.isdir(pasta) and os.path.exists(os.path.join(pasta, "promo_automacao_api.json")):
                tenants.append(nome)
    except Exception as exc:
        logger.warning("[PROMO AUTO] Falha ao listar tenants: %s", exc)
    return tenants


def _promo_automacao_worker() -> None:
    time.sleep(6)
    while True:
        for client_id in _promo_automacao_tenants():
            try:
                _promo_automacao_processar_tenant(client_id)
            except Exception:
                logger.exception("[PROMO AUTO] Falha inesperada tenant=%s", client_id)
        time.sleep(30)


def _promo_automacao_iniciar_background():
    if _agent_service_only():
        logger.info("[AGENT SERVICE] Automacao de promocoes desativada neste servico.")
        return
    global PROMO_AUTOMACAO_THREAD_STARTED
    with PROMO_AUTOMACAO_LOCK:
        if PROMO_AUTOMACAO_THREAD_STARTED:
            return
        PROMO_AUTOMACAO_THREAD_STARTED = True
    threading.Thread(
        target=_promo_automacao_worker,
        name="promo-automacao-servidor",
        daemon=True,
    ).start()


async def iniciar_analise_promo_via_api(
    loja: str = Form(...),
    promocao_a_id: str = Form(...),
    promocao_a_type: str = Form(""),
    margem_minima: float = Form(15.0),
    margem_tolerancia: float = Form(0.0),
    promocoes_b_meta: str = Form(...),
    client_id: str = Depends(get_tenant_id),
):
    try:
        return await asyncio.to_thread(
            _promo_start_api_worker_job,
            client_id=client_id,
            loja=loja,
            promocao_a_id=promocao_a_id,
            promocao_a_type=promocao_a_type,
            margem_minima=margem_minima,
            margem_tolerancia=margem_tolerancia,
            promocoes_b_meta=promocoes_b_meta,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[PROMO WORKER] Falha inesperada ao iniciar job API")
        raise HTTPException(
            status_code=503,
            detail=f"Nao foi possivel iniciar a analise de promocoes: {exc}",
        ) from exc


async def iniciar_analise_promo_via_api_com_arquivos(
    loja: str = Form(...),
    promocao_a_id: str = Form(...),
    promocao_a_type: str = Form(""),
    margem_minima: float = Form(15.0),
    margem_tolerancia: float = Form(0.0),
    promocoes_b_meta: str = Form(...),
    files: list[UploadFile] = File(...),
    client_id: str = Depends(get_tenant_id),
):
    if not files:
        raise HTTPException(status_code=400, detail="Envie os arquivos das Promocoes 2.")
    if not _ensure_promo_worker_running():
        raise HTTPException(status_code=503, detail="Worker dedicado de promocoes indisponivel.")

    data = {
        "loja": loja,
        "promocao_a_id": promocao_a_id,
        "promocao_a_type": promocao_a_type,
        "margem_minima": str(margem_minima),
        "margem_tolerancia": str(max(0.0, min(100.0, float(margem_tolerancia or 0.0)))),
        "promocoes_b_meta": promocoes_b_meta,
        "client_id": client_id,
    }
    files_payload = []
    try:
        for upload in files:
            filename = str(getattr(upload, "filename", "") or "").strip()
            if not filename:
                continue
            content = await upload.read()
            files_payload.append(("files", (filename, content, upload.content_type or "application/octet-stream")))
        if not files_payload:
            raise HTTPException(status_code=400, detail="Nenhum arquivo valido foi enviado.")

        resp = await asyncio.to_thread(
            requests.post,
            f"{PROMO_WORKER_URL}/api/promo/jobs/start",
            data=data,
            files=files_payload,
            timeout=90,
        )
        payload = _safe_json_response(resp)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha ao encaminhar job")
        raise HTTPException(status_code=503, detail=f"Falha ao acionar worker dedicado: {e}")

    if not resp.ok:
        detalhe = payload.get("detail") or payload.get("error") or resp.reason or "Falha no worker dedicado de promocoes."
        raise HTTPException(status_code=resp.status_code, detail=f"Worker promocoes respondeu HTTP {resp.status_code}: {detalhe}")
    try:
        _salvar_arquivos_originais_promo_job(client_id, str(payload.get("job_id") or ""), files_payload)
    except Exception:
        logger.exception("[PROMO EXPORT] Falha ao persistir arquivos originais para exportaÃƒÂ§ÃƒÂ£o")
    return payload


async def progresso_analise_promo_via_api_com_arquivos(job_id: str, client_id: str = Depends(get_tenant_id)):
    if not _ensure_promo_worker_running():
        raise HTTPException(status_code=503, detail="Worker dedicado de promocoes indisponivel.")
    payload = {}
    ultimo_erro = None
    resp = None
    for tentativa in range(1, 4):
        try:
            resp = await asyncio.to_thread(
                requests.get,
                f"{PROMO_WORKER_URL}/api/promo/jobs/{job_id}",
                params={"client_id": client_id},
                timeout=45,
            )
            payload = _safe_json_response(resp)
            if resp.status_code >= 500 and tentativa < 3:
                await asyncio.sleep(0.7 * tentativa)
                continue
            break
        except Exception as e:
            ultimo_erro = e
            if tentativa < 3:
                await asyncio.sleep(0.7 * tentativa)
                continue
            logger.exception("[PROMO WORKER] Falha ao consultar job %s", job_id)
            raise HTTPException(status_code=503, detail=f"Falha ao consultar worker dedicado: {e}")
    if resp is None:
        raise HTTPException(status_code=503, detail=f"Falha ao consultar worker dedicado: {ultimo_erro or 'sem resposta'}")
    if not resp.ok:
        detalhe = payload.get("detail") or payload.get("error") or resp.reason or "Falha ao consultar job no worker dedicado."
        raise HTTPException(status_code=resp.status_code, detail=f"Worker promocoes respondeu HTTP {resp.status_code}: {detalhe}")
    return payload


async def cancelar_analise_promo_via_api_com_arquivos(job_id: str, client_id: str = Depends(get_tenant_id)):
    if not _ensure_promo_worker_running():
        raise HTTPException(status_code=503, detail="Worker dedicado de promocoes indisponivel.")
    try:
        resp = await asyncio.to_thread(
            requests.post,
            f"{PROMO_WORKER_URL}/api/promo/jobs/{job_id}/cancel",
            params={"client_id": client_id},
            timeout=20,
        )
        payload = _safe_json_response(resp)
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha ao cancelar job %s", job_id)
        raise HTTPException(status_code=503, detail=f"Falha ao cancelar worker dedicado: {e}")
    if not resp.ok:
        detalhe = payload.get("detail") or payload.get("error") or resp.reason or "Falha ao cancelar job no worker dedicado."
        raise HTTPException(status_code=resp.status_code, detail=f"Worker promocoes respondeu HTTP {resp.status_code}: {detalhe}")
    return payload


def promo_automacao_obter(client_id: str = Depends(get_tenant_id)):
    deve_processar = False
    with PROMO_AUTOMACAO_LOCK:
        cfg = _promo_automacao_carregar(client_id)
        if cfg:
            cfg = {**cfg, **_promo_automacao_sanitizar(cfg)}
            cfg, mudou = _promo_automacao_refresh_job_state(client_id, cfg, aplicar=False)
            if cfg.get("enabled") and _promo_automacao_config_pronta(cfg) and float(cfg.get("next_run_at") or 0) <= time.time():
                # A tela nao reinicia o tempo; se venceu, quem dispara e o verificador do servidor.
                cfg["due"] = True
                deve_processar = True
            if mudou:
                _promo_automacao_salvar(client_id, cfg)
    if deve_processar:
        threading.Thread(
            target=_promo_automacao_processar_tenant,
            args=(client_id,),
            name=f"promo-automacao-on-demand-{client_id}",
            daemon=True,
        ).start()
    return _promo_automacao_public_payload(client_id, cfg or {})


def promo_automacao_salvar(req: PromoAutomacaoConfigRequest, client_id: str = Depends(get_tenant_id)):
    entrada = req.dict()
    with PROMO_AUTOMACAO_LOCK:
        anterior = _promo_automacao_carregar(client_id)
        cfg = _promo_automacao_sanitizar(entrada)
        agora = time.time()
        old_next = float(anterior.get("next_run_at") or 0)
        old_interval = int(anterior.get("interval_minutes") or 0)
        requested_next = float(cfg.get("next_run_at") or 0)
        if cfg.get("enabled"):
            if bool(anterior.get("enabled")) and old_next > 0 and old_interval == int(cfg.get("interval_minutes") or 0):
                cfg["next_run_at"] = old_next
            elif requested_next > agora:
                cfg["next_run_at"] = requested_next
            else:
                cfg["next_run_at"] = agora + int(cfg.get("interval_minutes") or 60) * 60
        else:
            cfg["next_run_at"] = 0

        for key in (
            "last_job_id",
            "last_job_status",
            "last_job_message",
            "last_job_progress",
            "last_run_at",
            "last_completed_at",
            "last_error",
            "last_applied_job_id",
            "last_apply_result",
        ):
            if key in anterior:
                cfg[key] = anterior.get(key)

        cfg = _promo_automacao_salvar(client_id, cfg)

    return _promo_automacao_public_payload(client_id, cfg)


def _promo_aplicar_participacoes_job_worker(job_id: str, payload_req: dict, client_id: str) -> None:
    def _progress(payload: dict):
        _promo_job_set(
            job_id,
            client_id=client_id,
            status="running",
            progress=int(payload.get("progress") or 0),
            message=str(payload.get("message") or "Entrando nas promocoes..."),
            result=None,
            error="",
            stats=payload,
        )

    try:
        req = PromoAplicarParticipacaoRequest(**(payload_req or {}))
        _promo_job_set(
            job_id,
            client_id=client_id,
            status="running",
            progress=1,
            message="Preparando entrada nas promocoes...",
            result=None,
            error="",
        )
        result = _aplicar_participacoes_promocoes_payload(req, client_id, progress_callback=_progress)
        _promo_job_set(
            job_id,
            client_id=client_id,
            status="completed",
            progress=100,
            message=(
                f"Entrada concluida: {int(result.get('total_sucesso') or 0)} sucesso(s), "
                f"{int(result.get('total_falha') or 0)} falha(s), "
                f"{int(result.get('total_ignorados') or 0)} ignorado(s)."
            ),
            result=result,
            error="",
        )
    except Exception as exc:
        detalhe = str(getattr(exc, "detail", None) or exc or "Erro ao entrar nas promocoes.")
        logger.exception("[PROMO APPLY] Falha no job %s", job_id)
        _promo_job_set(
            job_id,
            client_id=client_id,
            status="error",
            progress=100,
            message=detalhe,
            result=None,
            error=detalhe,
        )

PEER_EXPORTS = ['_promo_analise_background_worker', 'PROMO_AUTOMACAO_INTERVAL_UNITS', '_promo_automacao_path', '_promo_automacao_carregar', '_promo_automacao_salvar', '_promo_automacao_intervalo', '_promo_automacao_sanitizar', '_promo_automacao_public_payload', '_promo_start_api_worker_job', '_promo_consultar_worker_job', '_promo_automacao_linha_valor', '_promo_automacao_montar_participacoes', '_promo_automacao_refresh_job_state', '_promo_automacao_config_pronta', '_promo_automacao_processar_tenant', '_promo_automacao_tenants', '_promo_automacao_worker', '_promo_automacao_iniciar_background', 'iniciar_analise_promo_via_api', 'iniciar_analise_promo_via_api_com_arquivos', 'progresso_analise_promo_via_api_com_arquivos', 'cancelar_analise_promo_via_api_com_arquivos', 'promo_automacao_obter', 'promo_automacao_salvar', '_promo_aplicar_participacoes_job_worker']
__all__ = PEER_EXPORTS + ["configure_promocoes_api_jobs_runtime"]

configure_promocoes_api_jobs_runtime()
