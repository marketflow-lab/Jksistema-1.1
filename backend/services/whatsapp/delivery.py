"""Extracted WhatsApp bridge component: delivery."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp.contracts import (
    _QuestionResearchPending,
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _post_typing_indicator(config: dict[str, Any], message_id: str) -> dict[str, Any]:
    response = _gateway_request(
        config,
        "POST",
        f"/bridge/messages/{message_id}/typing",
        payload={"machine_id": config.get("machine_id")},
        timeout=15,
    )
    try:
        try:
            payload = response.json()
        except Exception:
            payload = {"error": response.text[:500]}
        if response.status_code in {403, 404, 409, 429} and isinstance(payload, dict):
            return payload
        if response.status_code >= 400:
            raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
        return payload if isinstance(payload, dict) else {"success": False, "status": "invalid_gateway_json"}
    finally:
        response.close()

def _post_message_progress(
    config: dict[str, Any],
    message_id: str,
    *,
    task_id: str,
    sequence: int,
    stage: str,
    text: str,
    fingerprint: str,
) -> dict[str, Any]:
    return _gateway_json(
        config,
        "POST",
        f"/bridge/messages/{message_id}/progress",
        {
            "machine_id": config.get("machine_id"),
            "task_id": task_id,
            "sequence": int(sequence),
            "stage": str(stage or "processando")[:80],
            "text": str(text or "")[:1200],
            "fingerprint": str(fingerprint or "")[:160],
        },
        timeout=20,
    )

def _progress_stage(task: dict[str, Any]) -> tuple[str, str]:
    status = str(task.get("status") or "").strip().lower()
    reason = str(task.get("wait_reason") or "").strip().lower()
    agent_state = str(task.get("agent_state") or "").strip().lower()
    live = unicodedata.normalize("NFKD", str(task.get("live_status") or "")).encode("ascii", "ignore").decode("ascii").lower()
    if reason == "retry_backoff":
        return "retentativa", "O Codex ficou temporariamente indisponivel e a tarefa foi preservada para uma nova tentativa automatica."
    if status == "queued":
        return "fila", "A solicitação continua na fila da conversa para não misturar respostas."
    if reason == "restart_recovery":
        return "retomando", "Estou reconstruindo a consulta após a reinicialização, sem reaproveitar uma resposta incompleta."
    if reason == "api_query" or any(word in live for word in ("ferramenta", "api", "mercado livre", "bling", "consult")):
        return "consultando", "Ainda estou aguardando ou percorrendo os dados das APIs necessárias."
    if reason in {"data_validation", "data_analysis"} or any(word in live for word in ("validando", "fallback", "insuficiente", "raciocin")):
        return "validando", "Estou comparando os resultados porque ainda preciso confirmar a suficiência e a consistência dos dados."
    if "gerando" in live or agent_state in {"validando", "verificando"}:
        return "preparando_resposta", "As consultas terminaram e estou organizando a resposta final sem repetir ou completar dados por suposição."
    return "analisando", "A análise ainda está em andamento e o Codex não concluiu uma resposta segura."

def _progress_message(task: dict[str, Any], sequence: int, elapsed_seconds: int) -> tuple[str, str]:
    stage, explanation = _progress_stage(task)
    previous = [item for item in (task.get("progress_events") or []) if isinstance(item, dict)]
    previous_stage = str(previous[-1].get("stage") or "") if previous else ""
    prefix = "Avancei para a próxima etapa." if previous_stage and previous_stage != stage else "Ainda estou trabalhando nesta solicitação."
    queue_position = int(task.get("queue_position") or codex_console._codex_task_queue_position(task) or 0)
    queue_note = f" Posição atual na fila: {queue_position}." if stage == "fila" and queue_position else ""
    text = f"*Andamento ({elapsed_seconds}s):* {prefix} {explanation}{queue_note}"
    return stage, text[:1200]

def _record_progress_event(task_id: str, event: dict[str, Any]) -> None:
    task = codex_console._codex_load_task(task_id)
    if not isinstance(task, dict):
        return
    events = [item for item in (task.get("progress_events") or []) if isinstance(item, dict)]
    codex_console._codex_update_task(
        task_id,
        progress_events=(events + [event])[-120:],
        last_progress_at=str(event.get("created_at") or _now()),
    )

def _progress_pulse_worker(
    config: dict[str, Any],
    message_id: str,
    task_id: str,
    stop_event: threading.Event,
) -> None:
    interval = _normalize_progress_interval(config.get("progress_interval_seconds"))
    started_at = time.monotonic()
    try:
        while not stop_event.wait(interval) and not BRIDGE_STOP_EVENT.is_set():
            task = codex_console._codex_load_task(task_id)
            if not isinstance(task, dict):
                break
            status = str(task.get("status") or "")
            if status in {"completed", "partial", "failed", "canceled", "awaiting_approval", "awaiting_input"}:
                break
            sequence = len([item for item in (task.get("progress_events") or []) if isinstance(item, dict)]) + 1
            elapsed = max(interval, int(time.monotonic() - started_at))
            stage, progress_text = _progress_message(task, sequence, elapsed)
            fingerprint = hashlib.sha256(f"{message_id}|{task_id}|{sequence}".encode("utf-8")).hexdigest()
            event = {
                "sequence": sequence,
                "stage": stage,
                "text": progress_text,
                "fingerprint": fingerprint,
                "created_at": _now(),
                "delivery": "pending",
            }
            try:
                result = _post_message_progress(
                    config,
                    message_id,
                    task_id=task_id,
                    sequence=sequence,
                    stage=stage,
                    text=progress_text,
                    fingerprint=fingerprint,
                )
                event["delivery"] = str(result.get("status") or ("sent" if result.get("success") else "failed"))
                RUNTIME_STATE["progress_last_error"] = ""
                RUNTIME_STATE["progress_last_sent_at"] = _now()
            except Exception as exc:
                event["delivery"] = "failed"
                event["error"] = str(exc)[:500]
                RUNTIME_STATE["progress_last_error"] = str(exc)[:800]
            _record_progress_event(task_id, event)
    finally:
        with PROGRESS_PULSES_LOCK:
            if PROGRESS_PULSES.get(message_id) is stop_event:
                PROGRESS_PULSES.pop(message_id, None)

def _start_progress_pulse(config: dict[str, Any], message_id: str, task_id: str) -> bool:
    if (
        config.get("progress_messages_enabled") is False
        or config.get("progress_explain_wait") is False
        or not message_id
        or not task_id
        or not config.get("worker_url")
        or not config.get("bridge_token")
        or not config.get("machine_id")
        or _whatsapp_ai_settings(config).get("provider") != "codex"
    ):
        return False
    with PROGRESS_PULSES_LOCK:
        existing = PROGRESS_PULSES.get(message_id)
        if existing is not None and not existing.is_set():
            return False
        stop_event = threading.Event()
        PROGRESS_PULSES[message_id] = stop_event
    threading.Thread(
        target=_progress_pulse_worker,
        args=(dict(config), message_id, task_id, stop_event),
        name=f"jk-whatsapp-progress-{hashlib.sha256(message_id.encode('utf-8')).hexdigest()[:8]}",
        daemon=True,
    ).start()
    return True

def _stop_progress_pulse(message_id: str) -> None:
    with PROGRESS_PULSES_LOCK:
        stop_event = PROGRESS_PULSES.get(str(message_id or "").strip())
    if stop_event is not None:
        stop_event.set()

def _typing_pulse_worker(config: dict[str, Any], message_id: str, stop_event: threading.Event) -> None:
    terminal_statuses = {
        "not_active",
        "message_not_owned",
        "binding_machine_mismatch",
    }
    try:
        while not stop_event.is_set() and not BRIDGE_STOP_EVENT.is_set():
            try:
                result = _post_typing_indicator(config, message_id)
                status = str(result.get("status") or result.get("error") or "").strip().lower()
                if status in terminal_statuses:
                    break
                if status in {"sent", "too_soon", "daily_limit", "policy_recheck_required", "waiting_free_window"}:
                    RUNTIME_STATE["typing_last_error"] = ""
                    if status == "sent":
                        RUNTIME_STATE["typing_last_sent_at"] = _now()
            except Exception as exc:
                RUNTIME_STATE["typing_last_error"] = str(exc)[:800]
            if stop_event.wait(TYPING_REFRESH_SECONDS) or BRIDGE_STOP_EVENT.is_set():
                break
    finally:
        with TYPING_PULSES_LOCK:
            if TYPING_PULSES.get(message_id) is stop_event:
                TYPING_PULSES.pop(message_id, None)

def _start_typing_pulse(config: dict[str, Any], message_id: str) -> bool:
    message_key = str(message_id or "").strip()
    if not message_key or not config.get("worker_url") or not config.get("bridge_token") or not config.get("machine_id"):
        return False
    with TYPING_PULSES_LOCK:
        existing = TYPING_PULSES.get(message_key)
        if existing is not None and not existing.is_set():
            return False
        stop_event = threading.Event()
        TYPING_PULSES[message_key] = stop_event
    threading.Thread(
        target=_typing_pulse_worker,
        args=(dict(config), message_key, stop_event),
        name=f"jk-whatsapp-typing-{hashlib.sha256(message_key.encode('utf-8')).hexdigest()[:8]}",
        daemon=True,
    ).start()
    return True

def _stop_typing_pulse(message_id: str) -> None:
    with TYPING_PULSES_LOCK:
        stop_event = TYPING_PULSES.get(str(message_id or "").strip())
    if stop_event is not None:
        stop_event.set()

def _post_message_result(config: dict[str, Any], message_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _stop_progress_pulse(message_id)
    body = {"machine_id": config.get("machine_id"), **payload}
    result = _gateway_json(config, "POST", f"/bridge/messages/{message_id}/result", body, timeout=20)
    _stop_typing_pulse(message_id)
    _record_message_timing(message_id, sent_at=_now())
    return result

def _post_outbound_image(
    config: dict[str, Any],
    message_id: str,
    path: Path,
    mime_type: str,
    caption: str,
    filename: str,
    artifact_type: str = "product_photo",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not token or not machine_id:
        raise RuntimeError("worker_url_bridge_token_or_machine_missing")
    if not path.is_file():
        raise RuntimeError("outbound_image_missing")
    artifact_type = str(artifact_type or "").strip().lower()
    if artifact_type not in {"product_photo", "report_chart"}:
        raise RuntimeError("outbound_image_artifact_not_allowed")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    with path.open("rb") as source:
        response = requests.post(
            worker_url + f"/bridge/messages/{quote(str(message_id), safe='')}/image",
            headers=_gateway_headers(config),
            data={
                "machine_id": machine_id,
                "caption": str(caption or "")[:1024],
                "artifact_type": artifact_type,
                "sha256": digest.hexdigest(),
            },
            files={"file": (_safe_filename(filename, "black-john-image.jpg"), source, mime_type)},
            timeout=90,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value

def _post_outbound_document(
    config: dict[str, Any],
    message_id: str,
    path: Path,
    mime_type: str,
    caption: str,
    filename: str,
    artifact_type: str,
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not token or not machine_id:
        raise RuntimeError("worker_url_bridge_token_or_machine_missing")
    if not path.is_file() or path.stat().st_size > WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES:
        raise RuntimeError("outbound_document_missing_or_too_large")
    expected = {
        "report_pdf": "application/pdf",
        "report_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if expected.get(artifact_type) != str(mime_type or "").strip().lower():
        raise RuntimeError("outbound_document_artifact_or_mime_not_allowed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("rb") as source:
        response = requests.post(
            worker_url + f"/bridge/messages/{quote(str(message_id), safe='')}/document",
            headers=_gateway_headers(config),
            data={
                "machine_id": machine_id,
                "caption": str(caption or "")[:1024],
                "artifact_type": artifact_type,
                "sha256": digest,
            },
            files={"file": (_safe_filename(filename, path.name), source, mime_type)},
            timeout=120,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value

def _post_proactive_image(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    path: Path,
    caption: str,
    filename: str,
    event_type: str = "weekly_report",
    artifact_type: str = "report_chart",
    mime_type: str = "image/png",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    subject_id = str(subject_id or config.get("subject_id") or "").strip()
    fingerprint = str(fingerprint or "").strip()[:160]
    if not worker_url or not token or not machine_id or not subject_id:
        raise RuntimeError("worker_url_bridge_token_machine_or_subject_missing")
    if not re.fullmatch(r"[A-Za-z0-9:_-]{16,160}", fingerprint):
        raise RuntimeError("invalid_proactive_image_fingerprint")
    if not path.is_file():
        raise RuntimeError("outbound_image_missing")
    safe_event_type = str(event_type or "weekly_report").strip().lower()
    safe_artifact_type = str(artifact_type or "report_chart").strip().lower()
    safe_mime_type = str(mime_type or "image/png").split(";", 1)[0].strip().lower()
    if safe_event_type not in {"weekly_report", "monthly_report", "task_completed"}:
        raise RuntimeError("invalid_proactive_image_event_type")
    if safe_artifact_type not in {"report_chart", "product_photo"}:
        raise RuntimeError("invalid_proactive_image_artifact_type")
    if safe_artifact_type == "report_chart" and safe_mime_type != "image/png":
        raise RuntimeError("report_chart_png_required")
    if safe_artifact_type == "product_photo" and safe_mime_type not in {"image/jpeg", "image/png"}:
        raise RuntimeError("product_photo_mime_not_allowed")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(256 * 1024), b""):
            digest.update(chunk)
    with path.open("rb") as source:
        response = requests.post(
            worker_url + "/bridge/proactive/image",
            headers=_gateway_headers(config),
            data={
                "subject_id": subject_id,
                "machine_id": machine_id,
                "fingerprint": fingerprint,
                "event_type": safe_event_type,
                "artifact_type": safe_artifact_type,
                "caption": str(caption or "")[:1024],
                "sha256": digest.hexdigest(),
            },
            files={"file": (_safe_filename(filename, "black-jhon-image.jpg"), source, safe_mime_type)},
            timeout=90,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code == 409 and isinstance(value, dict):
        return {"success": False, **value}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    if not isinstance(value, dict):
        raise RuntimeError("gateway_invalid_json")
    return value

def _post_proactive_document(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    path: Path,
    caption: str,
    filename: str,
    artifact_type: str,
    mime_type: str,
    event_type: str = "weekly_report",
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    machine_id = str(config.get("machine_id") or "").strip()
    subject_id = str(subject_id or config.get("subject_id") or "").strip()
    if not worker_url or not config.get("bridge_token") or not machine_id or not subject_id:
        raise RuntimeError("worker_url_bridge_token_machine_or_subject_missing")
    if not path.is_file() or path.stat().st_size > WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES:
        raise RuntimeError("outbound_document_missing_or_too_large")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("rb") as source:
        response = requests.post(
            worker_url + "/bridge/proactive/document",
            headers=_gateway_headers(config),
            data={
                "subject_id": subject_id,
                "machine_id": machine_id,
                "fingerprint": str(fingerprint or "")[:160],
                "event_type": str(event_type or "weekly_report")[:80],
                "artifact_type": artifact_type,
                "caption": str(caption or "")[:1024],
                "sha256": digest,
            },
            files={"file": (_safe_filename(filename, path.name), source, mime_type)},
            timeout=120,
        )
    try:
        value = response.json()
    except Exception:
        value = {"message": response.text[:500]}
    if response.status_code == 409 and isinstance(value, dict):
        return {"success": False, **value}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {value}")
    return value if isinstance(value, dict) else {"success": False, "status": "invalid_gateway_json"}

def _post_proactive(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload or {})
    subject = str(body.pop("subject_id", "") or config.get("subject_id") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not subject:
        return {"success": False, "status": "no_paired_subject"}
    if not machine_id:
        return {"success": False, "status": "machine_id_missing"}
    body.pop("machine_id", None)
    request_body = {"subject_id": subject, "machine_id": machine_id, **body}
    result = _gateway_json(
        config,
        "POST",
        "/bridge/proactive",
        request_body,
        timeout=20,
    )
    if (
        str(body.get("event_type") or "") == "task_partial"
        and isinstance(result, dict)
        and str(result.get("status") or "") == "ignored_low_severity"
    ):
        compatibility_body = dict(request_body)
        compatibility_body["event_type"] = "task_failed"
        fallback = _gateway_json(
            config,
            "POST",
            "/bridge/proactive",
            compatibility_body,
            timeout=20,
        )
        if isinstance(fallback, dict):
            return {
                **fallback,
                "compatibility_fallback": True,
                "requested_event_type": "task_partial",
                "delivery_event_type": "task_failed",
            }
        return {"success": False, "status": "invalid_gateway_json"}
    return result

def _post_interactive_approval(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    token: str,
    body: str,
    state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    bridge_token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not bridge_token or not machine_id:
        return {"success": False, "status": "bridge_not_configured"}
    response = requests.post(
        worker_url + "/bridge/interactive",
        headers={**_gateway_headers(config), "content-type": "application/json"},
        json={
            "subject_id": str(subject_id or "").strip(),
            "machine_id": machine_id,
            "fingerprint": str(fingerprint or "")[:160],
            "event_type": "question_approval",
            "header": "Black Jhon - resposta sugerida",
            "body": str(body or "")[:1024],
            "footer": "Somente o numero vinculado pode decidir",
            "button_label": "Revisar resposta",
            "options": [
                {"id": f"ppv_approve:{token}", "title": "Aprovar e enviar", "description": "Enviar exatamente este rascunho"},
                {"id": f"ppv_correct:{token}", "title": "Corrigir", "description": "Orientar uma correcao no mesmo rascunho"},
                {"id": f"ppv_regenerate:{token}", "title": "Gerar outra resposta", "description": "Criar uma nova versao para revisar"},
                {"id": f"ppv_reject:{token}", "title": "Negar", "description": "Nao enviar e manter a pergunta pendente"},
            ],
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text[:500]}
    if response.status_code == 409 and isinstance(payload, dict):
        return {"success": False, "status": str(payload.get("status") or payload.get("error") or "blocked")}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
    if not isinstance(payload, dict):
        return {"success": False, "status": "invalid_gateway_json"}
    outbound_message_id = str(
        payload.get("meta_message_id")
        or payload.get("outbound_message_id")
        or payload.get("message_id")
        or ""
    ).strip()[:200]
    if outbound_message_id and isinstance(state, dict):
        normalized_token = str(token or "").strip().upper()
        tokens = state.get("question_approval_tokens") if isinstance(state.get("question_approval_tokens"), dict) else {}
        token_item = tokens.get(normalized_token)
        if isinstance(token_item, dict) and str(token_item.get("subject_id") or "") == str(subject_id or ""):
            token_item["outbound_message_id"] = outbound_message_id
        threads = state.get("question_active_threads") if isinstance(state.get("question_active_threads"), dict) else {}
        for thread in threads.values():
            if (
                isinstance(thread, dict)
                and str(thread.get("token") or "").strip().upper() == normalized_token
                and str(thread.get("subject_id") or "") == str(subject_id or "")
            ):
                thread["outbound_message_id"] = outbound_message_id
        payload["outbound_message_id"] = outbound_message_id
    return payload

def _post_interactive_store_selection(
    config: dict[str, Any],
    *,
    subject_id: str,
    fingerprint: str,
    options: list[dict[str, str]],
) -> dict[str, Any]:
    worker_url = _normalize_worker_url(config.get("worker_url"))
    bridge_token = str(config.get("bridge_token") or "").strip()
    machine_id = str(config.get("machine_id") or "").strip()
    if not worker_url or not bridge_token or not machine_id:
        return {"success": False, "status": "bridge_not_configured"}
    response = requests.post(
        worker_url + "/bridge/interactive",
        headers={**_gateway_headers(config), "content-type": "application/json"},
        json={
            "subject_id": str(subject_id or "").strip(),
            "machine_id": machine_id,
            "fingerprint": str(fingerprint or "")[:160],
            "event_type": "store_selection",
            "header": "Escolha a loja",
            "body": "Selecione uma loja ou consulte todas separadamente.",
            "footer": "Os totais de lojas diferentes nunca serao misturados.",
            "button_label": "Ver lojas",
            "options": list(options or [])[:10],
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text[:500]}
    if response.status_code == 409 and isinstance(payload, dict):
        return {"success": False, "status": str(payload.get("status") or payload.get("error") or "blocked")}
    if response.status_code >= 400:
        raise RuntimeError(f"gateway_http_{response.status_code}: {payload}")
    return payload if isinstance(payload, dict) else {"success": False, "status": "invalid_gateway_json"}


_COMPONENT_FUNCTIONS = frozenset((
    '_post_typing_indicator',
    '_post_message_progress',
    '_progress_stage',
    '_progress_message',
    '_record_progress_event',
    '_progress_pulse_worker',
    '_start_progress_pulse',
    '_stop_progress_pulse',
    '_typing_pulse_worker',
    '_start_typing_pulse',
    '_stop_typing_pulse',
    '_post_message_result',
    '_post_outbound_image',
    '_post_outbound_document',
    '_post_proactive_image',
    '_post_proactive_document',
    '_post_proactive',
    '_post_interactive_approval',
    '_post_interactive_store_selection'
))
_IMPLEMENTATIONS = {
    '_post_typing_indicator': _post_typing_indicator,
    '_post_message_progress': _post_message_progress,
    '_progress_stage': _progress_stage,
    '_progress_message': _progress_message,
    '_record_progress_event': _record_progress_event,
    '_progress_pulse_worker': _progress_pulse_worker,
    '_start_progress_pulse': _start_progress_pulse,
    '_stop_progress_pulse': _stop_progress_pulse,
    '_typing_pulse_worker': _typing_pulse_worker,
    '_start_typing_pulse': _start_typing_pulse,
    '_stop_typing_pulse': _stop_typing_pulse,
    '_post_message_result': _post_message_result,
    '_post_outbound_image': _post_outbound_image,
    '_post_outbound_document': _post_outbound_document,
    '_post_proactive_image': _post_proactive_image,
    '_post_proactive_document': _post_proactive_document,
    '_post_proactive': _post_proactive,
    '_post_interactive_approval': _post_interactive_approval,
    '_post_interactive_store_selection': _post_interactive_store_selection
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
