"""Common helpers, jobs and worker bridge for the Promocoes module."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests

from backend.services.runtime_bridge import bind_runtime_globals

logger = logging.getLogger("jk_sistema")
PASTA_INFO = os.path.join(os.getcwd(), "info")


def get_tenant_path(client_id: str):
    return os.path.join(PASTA_INFO, str(client_id or "default"))


PROMO_ANALISE_JOBS = {}
PROMO_ANALISE_JOBS_LOCK = threading.Lock()
PROMO_ANALISE_JOBS_DIR = os.path.join(PASTA_INFO, "promo_jobs")
PROMO_AUTOMACAO_LOCK = threading.Lock()
PROMO_AUTOMACAO_THREAD_STARTED = False
PROMO_AUTOMACAO_RUNNING: set[str] = set()


def configure_promocoes_common_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    global logger, PASTA_INFO, PROMO_ANALISE_JOBS_DIR, get_tenant_path
    if runtime is not None:
        if hasattr(runtime, "logger"):
            logger = getattr(runtime, "logger")
        if hasattr(runtime, "PASTA_INFO"):
            PASTA_INFO = getattr(runtime, "PASTA_INFO")
        if hasattr(runtime, "get_tenant_path"):
            get_tenant_path = getattr(runtime, "get_tenant_path")
    PROMO_ANALISE_JOBS_DIR = os.path.join(PASTA_INFO, "promo_jobs")
    os.makedirs(PROMO_ANALISE_JOBS_DIR, exist_ok=True)
    return runtime


configure_promocoes_common_runtime()

def _promo_txt_clean(valor: Any) -> str:
    s = str(valor if valor is not None else "").strip()
    return "" if s.lower() in {"nan", "none", "null", "nat", "<na>"} else s


def _promo_normalizar_mlb(valor: Any) -> str:
    txt = str(valor or "").strip().upper()
    if not txt:
        return ""
    m = re.search(r"MLB\s*([0-9]+)", txt)
    if m:
        return f"MLB{m.group(1)}"
    num_match = re.match(r"^\s*([0-9]+)(?:\.0+)?\s*$", txt)
    if num_match:
        dig = num_match.group(1)
        return f"MLB{dig}" if len(dig) >= 7 else ""
    sci_match = re.match(r"^\s*[0-9]+(?:\.[0-9]+)?[Ee][+-]?[0-9]+\s*$", txt)
    if sci_match:
        try:
            dig = str(int(float(txt)))
            return f"MLB{dig}" if len(dig) >= 7 else ""
        except Exception:
            return ""
    txt_num = re.sub(r"[^0-9]", "", txt)
    if txt_num and len(txt_num) >= 7:
        return f"MLB{txt_num}"
    return txt if txt.startswith("MLB") else ""


def _extrair_mapa_promocao2_arquivo(df: pd.DataFrame) -> tuple[dict[str, dict], list[str]]:
    if df is None or df.empty:
        return {}, []

    item_id_aliases = [
        "ITEM_ID", "MLB", "ID anuncio", "ID anuncio", "ID do anuncio", "ID do anuncio",
        "AnÃƒÂºncio", "Anuncio", "ID", "NÃƒÂºmero do anuncio", "Numero do anuncio",
        "CÃƒÂ³digo do anuncio", "Codigo do anuncio", "CÃƒÂ³digo anuncio", "Codigo anuncio",
    ]
    col_item = _pick_first_col(df, item_id_aliases)
    col_sku = _pick_first_col(df, ["SKU", "SELLER_SKU"])
    col_title = _pick_first_col(df, ["TITLE", "TÃƒÂTULO", "TITULO", "TÃ­tulo"])
    col_price = _pick_first_col(df, ["FINAL_PRICEFINAL_PRICE", "FINAL_PRICE", "LOYALTY_PRICE", "PREÃƒâ€¡O FINAL", "PRECO FINAL"])
    col_sale_fee = _pick_first_col(df, ["SALE_FEE", "SALE FEE", "DESCONTO ML"])
    col_disc = _pick_first_col(df, [
        "DISCOUNT_PERCENTAGE",
        "ML % CAMPANHA",
        "DESCONTO SUGERIDO",
        "DESCONTO OFERECIDO",
        "SUGGESTED_DISCOUNT_PERCENTAGE",
        "SUGGESTED DISCOUNT",
        "RECOMMENDED_DISCOUNT_PERCENTAGE",
    ])
    col_status = _pick_first_col(df, ["STATUS", "SITUAÃƒâ€¡ÃƒÆ’O", "SITUACAO"])

    if not col_item:
        return {}, []

    mapa = {}
    ordem = []
    for row in df.to_dict("records"):
        item_id = _promo_normalizar_mlb(row.get(col_item, ""))
        if not item_id:
            continue
        if item_id not in mapa:
            ordem.append(item_id)
        mapa[item_id] = {
            "MLB": item_id,
            "SKU": _promo_txt_clean(row.get(col_sku, "")) if col_sku else "",
            "TÃ­tulo": _promo_txt_clean(row.get(col_title, "")) if col_title else "",
            "PreÃ§o Final ML": _format_money_safe(row.get(col_price, "") if col_price else ""),
            "Desconto ML": _format_money_safe(row.get(col_sale_fee, "") if col_sale_fee else ""),
            "ML % Campanha": _format_pct_br(_ml_parse_percentual_promocao_texto(row.get(col_disc, "") if col_disc else "")),
            "Status Arquivo": _promo_txt_clean(row.get(col_status, "")) if col_status else "",
        }
    return mapa, ordem


def _promo_job_set(job_id: str, **kwargs):
    with PROMO_ANALISE_JOBS_LOCK:
        atual = dict(PROMO_ANALISE_JOBS.get(job_id) or {})
        atual.update(kwargs)
        atual["updated_at"] = time.time()
        PROMO_ANALISE_JOBS[job_id] = atual
        try:
            job_path = os.path.join(PROMO_ANALISE_JOBS_DIR, f"{job_id}.json")
            with open(job_path, "w", encoding="utf-8") as fh:
                json.dump(atual, fh, ensure_ascii=False)
        except Exception:
            logger.exception("[PROMO API BG] Falha ao persistir job %s", job_id)
        return atual


def _promo_job_get(job_id: str) -> dict:
    with PROMO_ANALISE_JOBS_LOCK:
        atual = dict(PROMO_ANALISE_JOBS.get(job_id) or {})
    if atual:
        return atual
    job_path = os.path.join(PROMO_ANALISE_JOBS_DIR, f"{job_id}.json")
    if not os.path.exists(job_path):
        return {}
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            atual = json.load(fh) or {}
        with PROMO_ANALISE_JOBS_LOCK:
            PROMO_ANALISE_JOBS[job_id] = atual
        return atual
    except Exception:
        logger.exception("[PROMO API BG] Falha ao ler job %s", job_id)
        return {}


PROMO_WORKER_URL = os.getenv("PROMO_WORKER_URL", "http://127.0.0.1:8011").rstrip("/")
PROMO_WORKER_LOCK = threading.Lock()
PROMO_WORKER_PROTOCOL_VERSION = 2


def _promo_worker_health_state() -> tuple[bool, bool, dict]:
    try:
        resp = requests.get(f"{PROMO_WORKER_URL}/health", timeout=1.5)
        if not resp.ok:
            return False, False, {}
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return True, False, {}
        try:
            protocol = int(payload.get("protocolVersion") or payload.get("protocol_version") or 0)
        except (TypeError, ValueError):
            protocol = 0
        expected_version = str(os.getenv("JK_APP_VERSION") or "").strip()
        worker_version = str(payload.get("appVersion") or payload.get("app_version") or "").strip()
        if expected_version.lower().startswith("v"):
            expected_version = expected_version[1:]
        if worker_version.lower().startswith("v"):
            worker_version = worker_version[1:]
        compatible = protocol == PROMO_WORKER_PROTOCOL_VERSION
        if expected_version:
            compatible = compatible and worker_version == expected_version
        return True, compatible, payload
    except Exception:
        return False, False, {}


def _promo_worker_healthcheck() -> bool:
    return _promo_worker_health_state()[1]


def _promo_worker_target() -> tuple[str, int]:
    parsed = urlparse(PROMO_WORKER_URL if "://" in PROMO_WORKER_URL else f"http://{PROMO_WORKER_URL}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8011
    return host, int(port)


def _promo_worker_app_dir() -> str:
    services_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.getcwd(),
        os.path.abspath(os.path.join(services_dir, "..", "..")),
        os.path.abspath(os.path.join(services_dir, "..")),
        services_dir,
    ]
    seen = set()
    for path in candidates:
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.exists(os.path.join(path, "promo_worker_api.py")):
            return path
    return os.path.abspath(os.path.join(services_dir, "..", ".."))


def _promo_worker_log_handles(app_dir: str):
    logs_dir = os.path.join(app_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    stdout_path = os.path.join(logs_dir, "promo_worker_stdout.log")
    stderr_path = os.path.join(logs_dir, "promo_worker_stderr.log")
    return open(stdout_path, "ab"), open(stderr_path, "ab")


def _stop_stale_promo_worker(host: str, port: int) -> bool:
    if str(host or "").strip().lower() not in {"127.0.0.1", "localhost", "::1"}:
        logger.error("[PROMO WORKER] Worker incompativel em host remoto; reinicio automatico ignorado: %s", host)
        return False
    if os.name != "nt":
        logger.error("[PROMO WORKER] Reinicio automatico do worker antigo indisponivel neste sistema operacional.")
        return False
    script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"$pids = Get-NetTCPConnection -LocalPort {int(port)} -State Listen | "
        "Select-Object -ExpandProperty OwningProcess -Unique; "
        "foreach ($pidValue in $pids) { if ($pidValue) { Stop-Process -Id $pidValue -Force } }"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
        if completed.returncode != 0:
            return False
        for _ in range(20):
            available, _, _ = _promo_worker_health_state()
            if not available:
                return True
            time.sleep(0.25)
    except Exception:
        logger.exception("[PROMO WORKER] Falha ao encerrar worker incompativel")
    return False


def _ensure_promo_worker_running() -> bool:
    if _promo_worker_healthcheck():
        return True
    with PROMO_WORKER_LOCK:
        if _promo_worker_healthcheck():
            return True
        app_dir = _promo_worker_app_dir()
        host, port = _promo_worker_target()
        worker_available, worker_compatible, worker_health = _promo_worker_health_state()
        if worker_available and not worker_compatible:
            logger.warning(
                "[PROMO WORKER] Worker incompativel detectado; reiniciando. esperado=%s protocolo=%s health=%s",
                str(os.getenv("JK_APP_VERSION") or ""),
                PROMO_WORKER_PROTOCOL_VERSION,
                worker_health,
            )
            if not _stop_stale_promo_worker(host, port):
                return False
        stdout_fh = None
        stderr_fh = None
        try:
            stdout_fh, stderr_fh = _promo_worker_log_handles(app_dir)
            env = os.environ.copy()
            env["JK_INFO_DIR"] = os.path.abspath(PASTA_INFO)
            env["PROMO_WORKER_URL"] = PROMO_WORKER_URL
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "promo_worker_api:app",
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
                cwd=app_dir,
                env=env,
                stdout=stdout_fh,
                stderr=stderr_fh,
            )
        except Exception:
            logger.exception("[PROMO WORKER] Falha ao iniciar worker dedicado")
            return False
        finally:
            for fh in (stdout_fh, stderr_fh):
                try:
                    if fh:
                        fh.close()
                except Exception:
                    pass

    for _ in range(30):
        time.sleep(0.5)
        if _promo_worker_healthcheck():
            return True
    return False


def _safe_json_response(resp: requests.Response) -> dict:
    try:
        if not resp.content:
            return {}
        return resp.json()
    except Exception:
        texto = ""
        try:
            texto = (resp.text or "").strip()
        except Exception:
            texto = ""
        return {"detail": texto[:500] if texto else ""}

def _promo_original_uploads_dir(client_id: str, job_id: str) -> str:
    job_limpo = re.sub(r"[^A-Za-z0-9_-]+", "", str(job_id or "").strip())
    if not job_limpo:
        job_limpo = "sem_job"
    return os.path.join(get_tenant_path(client_id), "tmp", "promo", "uploads", job_limpo)

def _salvar_arquivos_originais_promo_job(client_id: str, job_id: str, files_payload: list) -> None:
    if not job_id or not files_payload:
        return
    pasta = _promo_original_uploads_dir(client_id, job_id)
    os.makedirs(pasta, exist_ok=True)
    for item in files_payload:
        try:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            _campo, file_tuple = item[0], item[1]
            if not isinstance(file_tuple, tuple) or len(file_tuple) < 2:
                continue
            filename = os.path.basename(str(file_tuple[0] or "").strip())
            content = file_tuple[1]
            if not filename or content is None:
                continue
            with open(os.path.join(pasta, filename), "wb") as fh:
                fh.write(content)
        except Exception:
            logger.exception("[PROMO EXPORT] Falha ao salvar arquivo original do job %s", job_id)

def _ler_arquivo_original_promo_job(client_id: str, job_id: str, arquivo_nome: str) -> tuple[bytes, str] | tuple[None, None]:
    if not job_id or not arquivo_nome:
        return None, None
    pasta = _promo_original_uploads_dir(client_id, job_id)
    if not os.path.isdir(pasta):
        return None, None
    alvo = os.path.basename(str(arquivo_nome or "").strip()).lower()
    if not alvo:
        return None, None
    for filename in os.listdir(pasta):
        if filename.lower() != alvo:
            continue
        caminho = os.path.join(pasta, filename)
        if not os.path.isfile(caminho):
            continue
        with open(caminho, "rb") as fh:
            return fh.read(), filename
    return None, None


__all__ = ('PASTA_INFO', 'get_tenant_path', '_promo_txt_clean', '_promo_normalizar_mlb', '_extrair_mapa_promocao2_arquivo', '_promo_job_set', '_promo_job_get', 'PROMO_WORKER_URL', 'PROMO_WORKER_LOCK', 'PROMO_WORKER_PROTOCOL_VERSION', '_promo_worker_health_state', '_promo_worker_healthcheck', '_ensure_promo_worker_running', '_safe_json_response', '_promo_original_uploads_dir', '_salvar_arquivos_originais_promo_job', '_ler_arquivo_original_promo_job', 'PROMO_ANALISE_JOBS', 'PROMO_ANALISE_JOBS_LOCK', 'PROMO_ANALISE_JOBS_DIR', 'PROMO_AUTOMACAO_LOCK', 'PROMO_AUTOMACAO_THREAD_STARTED', 'PROMO_AUTOMACAO_RUNNING', 'configure_promocoes_common_runtime')
