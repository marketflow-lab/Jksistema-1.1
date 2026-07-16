"""Extracted WhatsApp bridge component: scheduler."""

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


def _whatsapp_week_key(now: Optional[datetime] = None) -> str:
    current = now or datetime.now()
    return whatsapp_report_scheduling.week_key(current)

def _whatsapp_month_key(now: Optional[datetime] = None) -> str:
    current = now or datetime.now()
    return whatsapp_report_scheduling.month_key(current)

def _send_weekly_visual(
    config: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    week_key: str,
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    outcome = whatsapp_report_visuals.generate_weekly_chart_artifacts(
        base_info_dir=_info_dir(),
        client_id=client_id,
        week_key=week_key,
        chart_data=chart_data,
    )
    artifacts = [item for item in (outcome.get("artifacts") or []) if isinstance(item, dict)]
    if not artifacts:
        return {"success": False, "status": outcome.get("status") or "generation_empty", "error": outcome.get("error")}
    artifact = artifacts[0]
    path = _whatsapp_report_chart_path(artifact, client_id)
    if not path:
        return {"success": False, "status": "invalid_artifact", "error": "report_chart_invalid_or_expired"}
    try:
        return _post_proactive_image(
            config,
            subject_id=subject_id,
            fingerprint=f"weekly-report:{client_id}:{week_key}",
            path=path,
            caption="",
            filename=path.name,
        )
    except Exception as exc:
        return {"success": False, "status": "send_failed", "error": str(exc)[:500]}
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

def _remember_pending_weekly_visual(
    state: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    week_key: str,
    chart_data: dict[str, Any],
    last_status: str,
) -> None:
    pending = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
    fingerprint = f"weekly-report:{client_id}:{week_key}"
    pending[fingerprint] = {
        "client_id": str(client_id or "default"),
        "subject_id": str(subject_id or ""),
        "week_key": str(week_key or ""),
        "chart_data": dict(chart_data or {}),
        "created_at": time.time(),
        "expires_at": time.time() + 8 * 24 * 60 * 60,
        "last_status": str(last_status or "waiting_free_window")[:80],
        "last_attempt_at": time.time(),
    }
    # A semana nova substitui especificacoes antigas do mesmo cliente.
    state["pending_weekly_visuals"] = {
        key: value
        for key, value in pending.items()
        if isinstance(value, dict)
        and (key == fingerprint or str(value.get("client_id") or "") != str(client_id or "default"))
    }

def _flush_pending_weekly_visuals(config: dict[str, Any], state: dict[str, Any]) -> None:
    pending = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
    if not pending:
        return
    now = time.time()
    changed = False
    for fingerprint, item in list(pending.items()):
        if not isinstance(item, dict) or float(item.get("expires_at") or 0) <= now:
            pending.pop(fingerprint, None)
            changed = True
            continue
        if now - float(item.get("last_attempt_at") or 0) < 60:
            continue
        result = _send_weekly_visual(
            config,
            client_id=str(item.get("client_id") or config.get("client_id") or "default"),
            subject_id=str(item.get("subject_id") or config.get("subject_id") or ""),
            week_key=str(item.get("week_key") or ""),
            chart_data=dict(item.get("chart_data") or {}),
        )
        changed = True
        if result.get("success") or result.get("duplicate"):
            pending.pop(fingerprint, None)
            state["last_weekly_visual_sent_at"] = _now()
            state["last_weekly_visual_status"] = str(result.get("status") or "sent")[:80]
        else:
            item["last_attempt_at"] = now
            item["last_status"] = str(result.get("status") or result.get("error") or "send_failed")[:80]
            pending[fingerprint] = item
    state["pending_weekly_visuals"] = pending
    if changed:
        _save_state(state)

def _whatsapp_compact_alert_detail(value: Any, limit: int = 520) -> str:
    return whatsapp_report_scheduling.compact_alert_detail(value, limit)

def _whatsapp_weekly_operational_parts(suggestions: list[dict[str, Any]], week_key: str) -> list[str]:
    return whatsapp_report_scheduling.weekly_operational_parts(suggestions, week_key)

def _forward_operational_alerts(config: dict[str, Any], week_key: str) -> None:
    try:
        from backend.services import codex_assistant

        client_id = str(config.get("client_id") or "default")
        with codex_assistant.ASSISTANT_LOCK:
            context = codex_assistant._assistant_collect_data(
                client_id,
                "resumo operacional semanal para WhatsApp",
                {},
                mode="report",
                force_refresh=True,
            )
            suggestions = codex_assistant._assistant_save_suggestions(client_id, context.get("suggestions") or [])
        important = [
            item
            for item in suggestions
            if isinstance(item, dict) and str(item.get("severity") or "").lower() in {"warning", "high", "critical"}
        ][:6]
        state = _load_state()
        if important:
            severity = "critical" if any(str(item.get("severity") or "").lower() == "critical" for item in important) else "high"
            parts = _whatsapp_weekly_operational_parts(important, week_key)
            text = parts[0]
            chart_data = whatsapp_report_visuals.build_weekly_chart_data(important, week_key)
            visual_result = _send_weekly_visual(
                config,
                client_id=client_id,
                subject_id=str(config.get("subject_id") or ""),
                week_key=week_key,
                chart_data=chart_data,
            )
            state["last_weekly_visual_status"] = str(
                visual_result.get("status") or visual_result.get("error") or "unknown"
            )[:80]
            if visual_result.get("success") or visual_result.get("duplicate"):
                state["last_weekly_visual_sent_at"] = _now()
                pending_visuals = state.get("pending_weekly_visuals") if isinstance(state.get("pending_weekly_visuals"), dict) else {}
                pending_visuals.pop(f"weekly-report:{client_id}:{week_key}", None)
                state["pending_weekly_visuals"] = pending_visuals
            else:
                _remember_pending_weekly_visual(
                    state,
                    client_id=client_id,
                    subject_id=str(config.get("subject_id") or ""),
                    week_key=week_key,
                    chart_data=chart_data,
                    last_status=str(visual_result.get("status") or visual_result.get("error") or "waiting_free_window"),
                )
            _post_proactive(
                config,
                {
                    "fingerprint": f"ops-week:{client_id}:{week_key}",
                    "event_type": "operational_alert",
                    "severity": severity,
                    "text": text,
                    "text_parts": parts,
                },
            )
        state["last_weekly_operational_key"] = week_key
        state["last_weekly_operational_at"] = _now()
        _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = str(exc)[:1000]

def _start_operational_alert_scan(config: dict[str, Any]) -> None:
    global ALERT_THREAD
    if isinstance(config.get("phone_notification_settings"), dict) and config.get("phone_notification_settings"):
        return
    state = _load_state()
    current = datetime.now()
    week_key = _whatsapp_week_key(current)
    if not str(state.get("last_weekly_operational_key") or ""):
        state["last_weekly_operational_key"] = week_key
        state["weekly_operational_initialized_at"] = _now()
        _save_state(state)
        return
    if str(state.get("last_weekly_operational_key") or "") == week_key:
        return
    if current.weekday() == 0 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR:
        return
    if ALERT_THREAD and ALERT_THREAD.is_alive():
        return
    ALERT_THREAD = threading.Thread(
        target=_forward_operational_alerts,
        args=(dict(config), week_key),
        name="jk-whatsapp-weekly-report",
        daemon=True,
    )
    ALERT_THREAD.start()

def _scheduled_report_period(kind: str, current: datetime) -> dict[str, str]:
    return whatsapp_report_scheduling.scheduled_report_period(kind, current)

def _scheduled_report_parts(
    client_id: str,
    kind: str,
    current: datetime,
) -> tuple[dict[str, str], list[str], dict[str, Any]]:
    from backend.services import codex_assistant

    period = _scheduled_report_period(kind, current)
    prompt = (
        f"Gerar {period['title'].lower()} de vendas e operações do período de "
        f"{period['start']} a {period['end']}, usando todas as lojas deste cliente e separando os dados por loja. "
        "Incluir resumo executivo, indicadores de vendas e pedidos, ranking de SKUs, devoluções, ticket médio, "
        "comparação entre lojas, alertas, próximos passos, fontes e avisos de dados incompletos."
    )
    with codex_assistant.ASSISTANT_LOCK:
        context = codex_assistant._assistant_collect_data(
            client_id,
            prompt,
            {
                "periodo": {"data_inicio": period["start"], "data_fim": period["end"]},
                "data_inicio": period["start"],
                "data_fim": period["end"],
            },
            mode="report",
            force_refresh=True,
        )
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    answer = codex_assistant._assistant_report_chat_text(period["title"], context, suggestions)
    # O formatador móvel recebe um único título controlado pelo agendamento;
    # elimina-se apenas o primeiro H1 produzido pelo relatório-base.
    answer = re.sub(r"^\s*#\s+[^\n]+\n*", "", str(answer or ""), count=1).strip()
    start_label = _whatsapp_report_date(period["start"])
    end_label = _whatsapp_report_date(period["end"])
    report_text = (
        f"# {period['title']}\n"
        f"Período: {start_label} a {end_label}\n"
        "Conta: todas as lojas vinculadas\n\n"
        f"{answer}"
    ).strip()
    parts = _whatsapp_response_parts(report_text, "📊 BLACK JHON — RELATÓRIO")
    fallback = (
        f"*📊 {period['title']}*\n"
        f"_Período: {start_label} a {end_label}_\n\n"
        "Não foram encontrados dados suficientes para gerar o relatório."
    )
    chart_data = whatsapp_report_visuals.build_scheduled_report_chart_data(context, period, kind)
    return period, parts or [fallback], chart_data

def _send_scheduled_report_visuals(
    config: dict[str, Any],
    *,
    client_id: str,
    subject_id: str,
    kind: str,
    period: dict[str, str],
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    outcome = whatsapp_report_visuals.generate_scheduled_report_chart_artifacts(
        base_info_dir=_info_dir(),
        client_id=client_id,
        period=period,
        kind=kind,
        chart_data=chart_data,
        max_images=2,
    )
    artifacts = [item for item in (outcome.get("artifacts") or []) if isinstance(item, dict)]
    if not artifacts:
        return {
            "success": False,
            "status": outcome.get("status") or "generation_empty",
            "error": outcome.get("error"),
            "results": [],
        }
    results: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts, 1):
        path = _whatsapp_report_chart_path(artifact, client_id)
        if not path:
            results.append({"success": False, "status": "invalid_artifact"})
            continue
        try:
            result = _post_proactive_image(
                config,
                subject_id=subject_id,
                fingerprint=f"scheduled-chart:{kind}:{subject_id}:{period['key']}:{index}",
                path=path,
                caption="",
                filename=path.name,
                event_type=period["event_type"],
            )
            results.append(dict(result or {}))
        except Exception as exc:
            results.append({"success": False, "status": "send_failed", "error": str(exc)[:500]})
    return {
        "success": any(item.get("success") is True for item in results),
        "status": "sent" if any(item.get("success") is True for item in results) else "send_failed",
        "results": results,
    }

def _scheduled_report_targets(config: dict[str, Any], worker: dict[str, Any], current: datetime) -> list[dict[str, str]]:
    state = _load_state()
    deliveries = state.get("scheduled_report_deliveries")
    deliveries = deliveries if isinstance(deliveries, dict) else {}
    machine_id = str(config.get("machine_id") or "")
    weekly_ready = not (current.weekday() == 0 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR)
    monthly_ready = not (current.day == 1 and current.hour < WHATSAPP_WEEKLY_REPORT_START_HOUR)
    targets: list[dict[str, str]] = []
    for binding in (worker.get("bindings") or []):
        if not isinstance(binding, dict) or str(binding.get("machine_id") or "") != machine_id:
            continue
        subject_id = str(binding.get("subject_id") or "").strip()
        client_id = str(binding.get("client_id") or "").strip()
        username = str(binding.get("username") or "").strip().lower()
        if not subject_id or not client_id or not username:
            continue
        settings = _phone_notification_settings(config, subject_id, client_id=client_id, username=username)
        delivered = deliveries.get(subject_id)
        delivered = delivered if isinstance(delivered, dict) else {}
        weekly_key = _whatsapp_week_key(current)
        monthly_key = _whatsapp_month_key(current)
        if settings["send_weekly_report"] and weekly_ready and str(delivered.get("weekly") or "") != weekly_key:
            targets.append({"subject_id": subject_id, "client_id": client_id, "username": username, "kind": "weekly", "key": weekly_key})
        if settings["send_monthly_report"] and monthly_ready and str(delivered.get("monthly") or "") != monthly_key:
            targets.append({"subject_id": subject_id, "client_id": client_id, "username": username, "kind": "monthly", "key": monthly_key})
    return targets

def _scheduled_report_result_accepted(result: Any) -> bool:
    return whatsapp_report_scheduling.scheduled_report_result_accepted(result)

def _forward_scheduled_reports(config: dict[str, Any], targets: list[dict[str, str]], current: datetime) -> None:
    try:
        rendered: dict[tuple[str, str], tuple[dict[str, str], list[str], dict[str, Any]]] = {}
        for target in targets:
            client_id = str(target.get("client_id") or "default")
            kind = str(target.get("kind") or "weekly")
            cache_key = (client_id, kind)
            if cache_key not in rendered:
                rendered[cache_key] = _scheduled_report_parts(client_id, kind, current)
            period, parts, chart_data = rendered[cache_key]
            subject_id = str(target.get("subject_id") or "")
            result = _post_proactive(
                config,
                {
                    "subject_id": subject_id,
                    "fingerprint": f"scheduled:{kind}:{subject_id}:{period['key']}",
                    "event_type": period["event_type"],
                    "severity": "high",
                    "text": parts[0],
                    "text_parts": parts,
                },
            )
            if not _scheduled_report_result_accepted(result):
                continue
            visual_result = _send_scheduled_report_visuals(
                config,
                client_id=client_id,
                subject_id=subject_id,
                kind=kind,
                period=period,
                chart_data=chart_data,
            )
            state = _load_state()
            deliveries = state.get("scheduled_report_deliveries")
            deliveries = deliveries if isinstance(deliveries, dict) else {}
            subject_deliveries = deliveries.get(subject_id)
            subject_deliveries = subject_deliveries if isinstance(subject_deliveries, dict) else {}
            subject_deliveries[kind] = period["key"]
            subject_deliveries[f"{kind}_sent_at"] = _now()
            subject_deliveries[f"{kind}_chart_status"] = str(visual_result.get("status") or "")[:80]
            subject_deliveries[f"{kind}_chart_count"] = sum(
                1 for item in (visual_result.get("results") or []) if isinstance(item, dict) and item.get("success") is True
            )
            deliveries[subject_id] = subject_deliveries
            state["scheduled_report_deliveries"] = deliveries
            _save_state(state)
    except Exception as exc:
        RUNTIME_STATE["last_error"] = f"scheduled_whatsapp_report: {str(exc)[:900]}"

def _start_phone_notification_report_scan(config: dict[str, Any]) -> None:
    global ALERT_THREAD
    if ALERT_THREAD and ALERT_THREAD.is_alive():
        return
    worker = _worker_health(config)
    current = datetime.now()
    targets = _scheduled_report_targets(config, worker, current)
    if not targets:
        return
    ALERT_THREAD = threading.Thread(
        target=_forward_scheduled_reports,
        args=(dict(config), targets, current),
        name="jk-whatsapp-scheduled-reports",
        daemon=True,
    )
    ALERT_THREAD.start()


_COMPONENT_FUNCTIONS = frozenset((
    '_whatsapp_week_key',
    '_whatsapp_month_key',
    '_send_weekly_visual',
    '_remember_pending_weekly_visual',
    '_flush_pending_weekly_visuals',
    '_whatsapp_compact_alert_detail',
    '_whatsapp_weekly_operational_parts',
    '_forward_operational_alerts',
    '_start_operational_alert_scan',
    '_scheduled_report_period',
    '_scheduled_report_parts',
    '_send_scheduled_report_visuals',
    '_scheduled_report_targets',
    '_scheduled_report_result_accepted',
    '_forward_scheduled_reports',
    '_start_phone_notification_report_scan'
))
_IMPLEMENTATIONS = {
    '_whatsapp_week_key': _whatsapp_week_key,
    '_whatsapp_month_key': _whatsapp_month_key,
    '_send_weekly_visual': _send_weekly_visual,
    '_remember_pending_weekly_visual': _remember_pending_weekly_visual,
    '_flush_pending_weekly_visuals': _flush_pending_weekly_visuals,
    '_whatsapp_compact_alert_detail': _whatsapp_compact_alert_detail,
    '_whatsapp_weekly_operational_parts': _whatsapp_weekly_operational_parts,
    '_forward_operational_alerts': _forward_operational_alerts,
    '_start_operational_alert_scan': _start_operational_alert_scan,
    '_scheduled_report_period': _scheduled_report_period,
    '_scheduled_report_parts': _scheduled_report_parts,
    '_send_scheduled_report_visuals': _send_scheduled_report_visuals,
    '_scheduled_report_targets': _scheduled_report_targets,
    '_scheduled_report_result_accepted': _scheduled_report_result_accepted,
    '_forward_scheduled_reports': _forward_scheduled_reports,
    '_start_phone_notification_report_scan': _start_phone_notification_report_scan
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
