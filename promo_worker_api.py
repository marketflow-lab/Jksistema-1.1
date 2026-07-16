from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import io
import json
import os
import time
import uuid
import threading

from backend_api import (
    analisar_promo_via_api_com_arquivos,
    analisar_promo_via_api_sem_arquivos,
    logger,
)
from backend.services.promocoes_common import PROMO_WORKER_PROTOCOL_VERSION


app = FastAPI(title="JK Sistema Promo Worker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
QUEUE_STALE_TIMEOUT_SEC = 45

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INFO_DIR_ENV = (os.getenv("JK_INFO_DIR") or "").strip()
INFO_DIR = _INFO_DIR_ENV if _INFO_DIR_ENV else os.path.join(BASE_DIR, "info")
if not os.path.isabs(INFO_DIR):
    INFO_DIR = os.path.join(BASE_DIR, INFO_DIR)
JOBS_DIR = os.path.join(INFO_DIR, "promo_worker_jobs")
os.makedirs(JOBS_DIR, exist_ok=True)
JOBS_LOCK = threading.Lock()
JOBS_CACHE = {}


class PromoJobCancelled(Exception):
    pass


def _job_path(job_id: str) -> str:
    return os.path.join(JOBS_DIR, f"{job_id}.json")


def _candidate_job_paths(job_id: str) -> list[str]:
    filename = f"{job_id}.json"
    info_dirs = [
        INFO_DIR,
        os.path.join(BASE_DIR, "info"),
        os.path.join(os.getcwd(), "info"),
    ]
    appdata = os.getenv("APPDATA") or ""
    if appdata:
        info_dirs.append(os.path.join(appdata, "JK Sistema Cliente", "local_app", "info"))

    paths = []
    seen = set()
    for info_dir in info_dirs:
        if not info_dir:
            continue
        path = os.path.abspath(os.path.join(info_dir, "promo_worker_jobs", filename))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _job_set(job_id: str, **kwargs):
    with JOBS_LOCK:
        job = dict(JOBS_CACHE.get(job_id) or {})
        job.update(kwargs)
        job["updated_at"] = time.time()
        JOBS_CACHE[job_id] = job
    with open(_job_path(job_id), "w", encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False)
    return job


def _job_push_log(job_id: str, message: str, progress: int | None = None, details: dict | None = None):
    atual = _job_get(job_id)
    logs = list(atual.get("logs") or [])
    entry = {
        "ts": time.time(),
        "message": str(message or "").strip() or "Processando...",
    }
    if progress is not None:
        try:
            entry["progress"] = int(progress)
        except Exception:
            pass
    if isinstance(details, dict) and details:
        entry["details"] = details
    logs.append(entry)
    logs = logs[-40:]

    payload = {"logs": logs, "message": entry["message"]}
    if progress is not None:
        payload["progress"] = max(0, min(99, int(progress)))
    _job_set(job_id, **payload)


def _job_get(job_id: str) -> dict:
    with JOBS_LOCK:
        job = dict(JOBS_CACHE.get(job_id) or {})
    if job:
        return job
    path = ""
    for candidate in _candidate_job_paths(job_id):
        if os.path.exists(candidate):
            path = candidate
            break
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        job = json.load(fh) or {}
    primary = _job_path(job_id)
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(primary)):
        try:
            os.makedirs(os.path.dirname(primary), exist_ok=True)
            with open(primary, "w", encoding="utf-8") as fh:
                json.dump(job, fh, ensure_ascii=False)
        except Exception:
            pass
    with JOBS_LOCK:
        JOBS_CACHE[job_id] = job
    return job


def _job_cancel_requested(job_id: str) -> bool:
    job = _job_get(job_id)
    return bool(job.get("cancel_requested")) or str(job.get("status") or "").lower() in {"canceled", "cancelled"}


def _raise_if_job_cancelled(job_id: str):
    if _job_cancel_requested(job_id):
        raise PromoJobCancelled("Verificacao cancelada pelo usuario.")


def _run_job(
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
    heartbeat_stop = threading.Event()
    heartbeat_state = {
        "progress": 15,
        "message": "Processando análise de promoções...",
    }

    def _heartbeat_loop():
        # Mantém feedback periódico para a UI durante etapas longas.
        while not heartbeat_stop.wait(15):
            if _job_cancel_requested(job_id):
                continue
            _job_push_log(
                job_id,
                f"Em processamento... {heartbeat_state.get('message') or 'Aguarde...'}",
                progress=int(heartbeat_state.get("progress") or 15),
            )

    try:
        _raise_if_job_cancelled(job_id)
        _job_set(job_id, status="running", progress=15, message="Processando análise de promoções...", result=None, error=None)
        _job_push_log(job_id, "Worker iniciou o processamento.", progress=15)
        heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name=f"promo-heartbeat-{job_id[:8]}")
        heartbeat_thread.start()

        def _on_progress(payload: dict):
            _raise_if_job_cancelled(job_id)
            if not isinstance(payload, dict):
                return
            message = payload.get("message") or "Processando análise de promoções..."
            progress = payload.get("progress")
            details = payload.get("details") if isinstance(payload.get("details"), dict) else None
            try:
                heartbeat_state["message"] = str(message)
                heartbeat_state["progress"] = max(1, min(99, int(progress))) if progress is not None else heartbeat_state.get("progress", 15)
            except Exception:
                pass
            _job_push_log(job_id, str(message), progress=progress, details=details)

        if files_payload:
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
                    progress_hook=_on_progress,
                )
            )
        else:
            resultado = asyncio.run(
                analisar_promo_via_api_sem_arquivos(
                    loja=loja,
                    promocao_a_id=promocao_a_id,
                    promocao_a_type=promocao_a_type,
                    margem_minima=margem_minima,
                    margem_tolerancia=margem_tolerancia,
                    promocoes_b_meta=promocoes_b_meta,
                    client_id=client_id,
                    progress_hook=_on_progress,
                )
            )
        _raise_if_job_cancelled(job_id)
        _job_push_log(job_id, "Análise concluída com sucesso.", progress=100)
        _job_set(job_id, status="completed", progress=100, message="Análise concluída.", result=resultado, error=None)
    except PromoJobCancelled as e:
        _job_push_log(job_id, str(e), progress=100)
        _job_set(
            job_id,
            status="canceled",
            progress=100,
            message=str(e),
            error="cancelado_pelo_usuario",
            result=None,
            cancel_requested=True,
        )
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha no job %s", job_id)
        _job_push_log(job_id, f"Erro no processamento: {e}", progress=100)
        _job_set(job_id, status="error", progress=100, message="Falha ao processar análise.", error=str(e), result=None)
    finally:
        heartbeat_stop.set()


@app.get("/health")
async def health():
    app_version = str(os.getenv("JK_APP_VERSION") or "").strip()
    if app_version.lower().startswith("v"):
        app_version = app_version[1:]
    return {
        "ok": True,
        "appVersion": app_version,
        "protocolVersion": PROMO_WORKER_PROTOCOL_VERSION,
        "pid": os.getpid(),
    }


@app.post("/api/promo/jobs/start")
async def start_job(
    loja: str = Form(...),
    promocao_a_id: str = Form(...),
    promocao_a_type: str = Form(""),
    margem_minima: float = Form(15.0),
    margem_tolerancia: float = Form(0.0),
    promocoes_b_meta: str = Form(...),
    client_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    if not files:
        raise HTTPException(status_code=400, detail="Envie os arquivos das Promoções 2.")

    files_payload = []
    for upload in files:
        filename = str(getattr(upload, "filename", "") or "").strip()
        if not filename:
            continue
        content = await upload.read()
        files_payload.append({"filename": filename, "content": content})

    if not files_payload:
        raise HTTPException(status_code=400, detail="Nenhum arquivo válido foi enviado.")

    job_id = uuid.uuid4().hex
    _job_set(
        job_id,
        client_id=client_id,
        loja=loja,
        status="queued",
        progress=5,
        message="Job criado no worker dedicado.",
        created_at=time.time(),
        cancel_requested=False,
        logs=[{"ts": time.time(), "message": "Job criado no worker dedicado.", "progress": 5}],
        result=None,
        error=None,
    )

    try:
        t = threading.Thread(
            target=_run_job,
            kwargs={
                "job_id": job_id,
                "loja": loja,
                "promocao_a_id": promocao_a_id,
                "promocao_a_type": promocao_a_type,
                "margem_minima": margem_minima,
                "margem_tolerancia": max(0.0, min(100.0, float(margem_tolerancia or 0.0))),
                "promocoes_b_meta": promocoes_b_meta,
                "files_payload": files_payload,
                "client_id": client_id,
            },
            daemon=True,
            name=f"promo-job-{job_id[:8]}",
        )
        t.start()
        _job_set(job_id, worker_thread=t.name)
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha ao iniciar thread do job %s", job_id)
        _job_push_log(job_id, f"Erro ao iniciar processamento: {e}", progress=100)
        _job_set(
            job_id,
            status="error",
            progress=100,
            message="Falha ao iniciar processamento em segundo plano.",
            error=str(e),
            result=None,
        )
        raise HTTPException(status_code=500, detail="Não foi possível iniciar o processamento do job.")

    return {
        "success": True,
        "job_id": job_id,
        "status": "queued",
        "message": "Análise iniciada no backend dedicado de promoções.",
    }


@app.post("/api/promo/jobs/start-api")
async def start_api_job(
    loja: str = Form(...),
    promocao_a_id: str = Form(...),
    promocao_a_type: str = Form(""),
    margem_minima: float = Form(15.0),
    margem_tolerancia: float = Form(0.0),
    promocoes_b_meta: str = Form(...),
    client_id: str = Form(...),
):
    job_id = uuid.uuid4().hex
    _job_set(
        job_id,
        client_id=client_id,
        loja=loja,
        status="queued",
        progress=5,
        message="Job API criado no worker dedicado.",
        created_at=time.time(),
        cancel_requested=False,
        logs=[{"ts": time.time(), "message": "Job API criado no worker dedicado.", "progress": 5}],
        result=None,
        error=None,
    )

    try:
        t = threading.Thread(
            target=_run_job,
            kwargs={
                "job_id": job_id,
                "loja": loja,
                "promocao_a_id": promocao_a_id,
                "promocao_a_type": promocao_a_type,
                "margem_minima": margem_minima,
                "margem_tolerancia": max(0.0, min(100.0, float(margem_tolerancia or 0.0))),
                "promocoes_b_meta": promocoes_b_meta,
                "files_payload": [],
                "client_id": client_id,
            },
            daemon=True,
            name=f"promo-api-job-{job_id[:8]}",
        )
        t.start()
        _job_set(job_id, worker_thread=t.name)
    except Exception as e:
        logger.exception("[PROMO WORKER] Falha ao iniciar thread do job API %s", job_id)
        _job_push_log(job_id, f"Erro ao iniciar processamento: {e}", progress=100)
        _job_set(
            job_id,
            status="error",
            progress=100,
            message="Falha ao iniciar processamento em segundo plano.",
            error=str(e),
            result=None,
        )
        raise HTTPException(status_code=500, detail="Nao foi possivel iniciar o processamento do job API.")

    return {
        "success": True,
        "job_id": job_id,
        "status": "queued",
        "message": "Analise via API iniciada no backend dedicado de promocoes.",
    }


@app.get("/api/promo/jobs/{job_id}")
async def get_job(job_id: str, client_id: str):
    job = _job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if str(job.get("client_id") or "") != str(client_id or ""):
        raise HTTPException(status_code=403, detail="Job não pertence ao cliente informado.")

    status = str(job.get("status") or "queued").lower()
    if status == "queued":
        created_at = float(job.get("created_at") or 0)
        if created_at and (time.time() - created_at) > QUEUE_STALE_TIMEOUT_SEC:
            msg = "Job ficou em fila por tempo acima do esperado e foi cancelado automaticamente."
            _job_push_log(job_id, msg, progress=100)
            job = _job_set(
                job_id,
                status="error",
                progress=100,
                message=msg,
                error="timeout_em_fila",
                result=None,
            )

    return {
        "success": True,
        "job_id": job_id,
        "status": job.get("status") or "queued",
        "progress": job.get("progress", 0),
        "message": job.get("message") or "",
        "logs": job.get("logs") or [],
        "updated_at": job.get("updated_at") or 0,
        "error": job.get("error"),
        "result": job.get("result") if job.get("status") == "completed" else None,
    }


@app.post("/api/promo/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, client_id: str):
    job = _job_get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nao encontrado.")
    if str(job.get("client_id") or "") != str(client_id or ""):
        raise HTTPException(status_code=403, detail="Job nao pertence ao cliente informado.")

    status = str(job.get("status") or "queued").lower()
    if status in {"completed", "error", "canceled", "cancelled"}:
        return {
            "success": True,
            "job_id": job_id,
            "status": job.get("status") or status,
            "message": job.get("message") or "Job ja finalizado.",
        }

    msg = "Verificacao cancelada pelo usuario."
    _job_push_log(job_id, msg, progress=100)
    job = _job_set(
        job_id,
        status="canceled",
        progress=100,
        message=msg,
        error="cancelado_pelo_usuario",
        result=None,
        cancel_requested=True,
    )
    return {
        "success": True,
        "job_id": job_id,
        "status": job.get("status") or "canceled",
        "message": job.get("message") or msg,
    }
