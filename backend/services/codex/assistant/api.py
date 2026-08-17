"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.services import codex_assistant_storage, codex_turn_context
from backend.services.favoritos_margem import margem_calcular_anuncio, margem_formatar_moeda, margem_parse_float
from backend.services.whatsapp import intent as whatsapp_intent

from .catalog import _assistant_tool_meta, _assistant_tools_public
from .collection import _assistant_collect_data
from .contracts import CODEX_ASSISTANT_EVALUATION_CASES, CodexAssistantActionQueueRequest, CodexAssistantActionQueueUpdateRequest, CodexAssistantChatRequest, CodexAssistantEvaluationRequest, CodexAssistantFinancialAdjustmentRequest, CodexAssistantReportRequest, CodexAssistantReportSettingsRequest, CodexAssistantRunRequest, CodexOperationalMemoryRequest
from .marketplace import _assistant_apply_advanced_profile
from .reports_artifacts import _assistant_compact_chat_text_response, _assistant_create_report, _assistant_ensure_report_chat_text
from .reports_html import _assistant_report_dir
from .routing import _assistant_registry_plan
from .runtime import ASSISTANT_LOCK, configure_codex_assistant_runtime, _assistant_info_base, _assistant_now, _assistant_path, _assistant_read_json, _assistant_require_full_admin, _assistant_safe_id, _assistant_write_json
from .settings import CODEX_DATA_TOOLS_VERSION, REPORT_FORMATS

def _assistant_save_suggestions(client_id: str, suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = _assistant_path(client_id, "suggestions.json")
    existing = _assistant_read_json(path, [])
    if not isinstance(existing, list):
        existing = []
    by_id = {str(item.get("id") or ""): item for item in existing if isinstance(item, dict)}
    for item in suggestions:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        by_id[str(item.get("id"))] = item
    merged = sorted(by_id.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)[:80]
    _assistant_write_json(path, merged)
    return merged


def save_suggestions(client_id: str, suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist assistant suggestions using the service-owned repository."""

    return _assistant_save_suggestions(client_id, suggestions)


def _assistant_scheduler_state(client_id: str) -> dict[str, Any]:
    try:
        data = codex_assistant_storage.codex_assistant_scheduler_get(_assistant_info_base(), client_id)
    except Exception:
        data = _assistant_read_json(_assistant_path(client_id, "scheduler_state.json"), {})
    return data if isinstance(data, dict) else {}


def _assistant_save_scheduler_state(client_id: str, state: dict[str, Any]) -> None:
    codex_assistant_storage.codex_assistant_scheduler_save(_assistant_info_base(), client_id, state)


def codex_assistant_chat(
    payload: CodexAssistantChatRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    message = str(payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Informe uma mensagem.")
    client_id = str(sessao.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=401, detail="Sessao sem tenant autenticado.")

    # Compatibilidade de rota: o antigo assistente deterministico nao escolhe
    # mais fontes. Sistema e WhatsApp compartilham o mesmo seletor Codex e os
    # mesmos guards de tenant, loja, permissao e read-only do /api/ia/chat.
    from backend.schemas import IAChatRequest
    from backend.services import ia_endpoints

    delegated = ia_endpoints.ia_chat(
        IAChatRequest(
            message=message,
            page="codex_assistant",
            context={
                "selection": (
                    payload.screen_context.get("selection")
                    if isinstance(payload.screen_context, dict)
                    and isinstance(payload.screen_context.get("selection"), dict)
                    else {}
                )
            },
        ),
        request,
        client_id=client_id,
    )
    resposta = str(delegated.get("resposta") or "")
    tool_results = [
        item for item in list(delegated.get("tool_results") or []) if isinstance(item, dict)
    ]
    return {
        "success": delegated.get("success") is True,
        "resposta": resposta,
        "answer": resposta,
        "model": delegated.get("model") or "",
        "context": {
            "sources": list(dict.fromkeys(
                str(source or "")[:300]
                for item in tool_results
                for source in list(item.get("sources_human") or item.get("sources") or [])[:8]
                if str(source or "").strip()
            ))[:20],
            "warnings": list(dict.fromkeys(
                str(warning or "")[:300]
                for item in tool_results
                for warning in list(item.get("warnings") or [])[:8]
                if str(warning or "").strip()
            ))[:20],
            "suggestions": [],
            "management_analysis": {},
            "registry_results": [],
            "tool_plan": {"managed_by": "CodexDataSelectionAgent"},
            "status_steps": [],
            "tool_results_count": len(tool_results),
            "generated_at": _assistant_now(),
            "cache_hit": False,
        },
    }


def codex_assistant_suggestions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    state = _assistant_scheduler_state(client_id)
    return {
        "success": True,
        "status": "disabled",
        "due": False,
        "suggestions": [],
        "scheduler": state,
        "message": "Relatorios automaticos do Black Jhon estao desativados.",
    }


def codex_assistant_tools(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    tools = _assistant_tools_public(sessao.get("permissions") or {})
    modules = sorted({str(tool.get("module") or "") for tool in tools if tool.get("module")})
    return {
        "success": True,
        "tools": tools,
        "modules": modules,
        "total": len(tools),
        "version": CODEX_DATA_TOOLS_VERSION,
        "read_only": True,
        "mutating_actions_require_approval": True,
        "generated_at": _assistant_now(),
    }


def _assistant_evaluation_run(client_id: str, screen_context: Any = None) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    total = len(CODEX_ASSISTANT_EVALUATION_CASES)
    passed = 0
    for case in CODEX_ASSISTANT_EVALUATION_CASES:
        question = str(case.get("question") or "").strip()
        mode = str(case.get("mode") or "chat").strip() or "chat"
        plan = _assistant_registry_plan(client_id, question, screen_context or {}, mode)
        planned_tools = [str(item or "").strip() for item in plan.get("selected_tools") or [] if str(item or "").strip()]
        expected = [str(item or "").strip() for item in case.get("expected_tools") or [] if str(item or "").strip()]
        compatible = [str(item or "").strip() for item in case.get("compatible_tools") or [] if str(item or "").strip()]
        matched_expected = [tool for tool in expected if tool in planned_tools]
        matched_compatible = [tool for tool in compatible if tool in planned_tools]
        missing = [tool for tool in expected if tool not in planned_tools]
        mutating = [
            tool
            for tool in planned_tools
            if _assistant_tool_meta(tool).get("read_only") is False
        ]
        ok = bool(matched_expected) and not mutating
        if ok:
            passed += 1
        confidence = "alta" if ok and matched_compatible else ("media" if ok else "baixa")
        cases.append(
            {
                "id": case.get("id"),
                "question": question,
                "mode": mode,
                "expected_tools": expected,
                "compatible_tools": compatible,
                "planned_tools": planned_tools,
                "matched_expected": matched_expected,
                "matched_compatible": matched_compatible,
                "missing_tools": missing,
                "mutating_tools": mutating,
                "passed": ok,
                "confidence": confidence,
                "status_steps": plan.get("status_steps") or [],
                "notes": (
                    "Plano chamou ferramenta esperada e permaneceu read-only."
                    if ok
                    else "Plano nao chamou a ferramenta esperada; revisar selecao de intencao/capacidade."
                ),
            }
        )
    score = round((passed / total) * 100.0, 1) if total else 0.0
    return {
        "success": True,
        "client_id": client_id,
        "generated_at": _assistant_now(),
        "read_only": True,
        "tools_version": CODEX_DATA_TOOLS_VERSION,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "score_percent": score,
        "cases": cases,
    }


def codex_assistant_evaluation_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    path = _assistant_path(client_id, "evaluation_last.json")
    data = _assistant_read_json(path, {})
    if isinstance(data, dict) and data.get("cases"):
        return data
    result = _assistant_evaluation_run(client_id, {})
    _assistant_write_json(path, result)
    return result


def codex_assistant_evaluation_run(
    payload: CodexAssistantEvaluationRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    result = _assistant_evaluation_run(client_id, payload.screen_context or {})
    _assistant_write_json(_assistant_path(client_id, "evaluation_last.json"), result)
    return result


def codex_assistant_memory_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    category: str = "",
    q: str = "",
    limit: int = 100,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    client_id = str(sessao.get("client_id") or "default")
    if str(q or "").strip():
        return codex_operational_memory.query_memory(
            client_id=client_id,
            message=str(q or ""),
            category=str(category or ""),
            limit=limit,
        )
    return codex_operational_memory.list_memory(
        client_id=client_id,
        category=str(category or ""),
        limit=limit,
    )


def codex_assistant_memory_post(
    payload: CodexOperationalMemoryRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    return codex_operational_memory.add_memory(
        client_id=str(sessao.get("client_id") or "default"),
        category=str(payload.category or "decisoes"),
        content=str(payload.content or ""),
        source=str(payload.source or "manual"),
        metadata=payload.metadata if isinstance(payload.metadata, dict) else {},
        importance=payload.importance,
        entry_id=str(payload.entry_id or ""),
    )


def codex_assistant_memory_delete(
    entry_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_operational_memory

    return codex_operational_memory.delete_memory(
        client_id=str(sessao.get("client_id") or "default"),
        entry_id=str(entry_id or ""),
    )


def codex_assistant_data_sources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    q: str = "",
    module: str = "",
    type: str = "",
    limit: int = 300,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_readonly_sources

    return codex_readonly_sources.discover_data_sources(
        client_id=str(sessao.get("client_id") or "default"),
        query=str(q or ""),
        module=str(module or ""),
        source_type=str(type or ""),
        limit=limit,
    )


def codex_assistant_bling_resources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _assistant_require_full_admin(request, authorization)
    from backend.services import codex_bling_tools

    payload = codex_bling_tools.bling_resources_public()
    payload["mutating_actions_require_approval"] = True
    return payload


def codex_assistant_mercado_livre_resources(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _assistant_require_full_admin(request, authorization)
    from backend.services.mercado_livre_query_catalog import mercado_livre_query_catalog_public

    payload = mercado_livre_query_catalog_public()
    payload["execution_requires_exact_store"] = True
    payload["mutating_routes_blocked"] = True
    return payload


def codex_assistant_proactive_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        response = {
            "success": True,
            "status": "disabled",
            "due": False,
            "suggestions": [],
            "scheduler": state,
            "message": "Alertas e relatorios proativos do Black Jhon estao desativados.",
        }
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def _assistant_daily_due(state: dict[str, Any], force: bool = False) -> bool:
    del state, force
    return False


def _assistant_weekly_due(state: dict[str, Any], force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now()
    if now.weekday() == 0 and now.hour < 8:
        return False
    week_key = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    return str(state.get("last_weekly_key") or "") != week_key



def codex_assistant_daily_analysis_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        response = {
            "success": True,
            "status": "disabled",
            "due": False,
            "suggestions": [],
            "report": None,
            "scheduler": state,
            "message": "Relatorios automaticos do Black Jhon estao desativados.",
        }
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def codex_assistant_weekly_analysis_run(
    payload: CodexAssistantRunRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    with ASSISTANT_LOCK:
        state = _assistant_scheduler_state(client_id)
        response = {
            "success": True,
            "status": "disabled",
            "due": False,
            "suggestions": [],
            "report": None,
            "scheduler": state,
            "message": "Relatorios automaticos do Black Jhon estao desativados.",
        }
        return _assistant_compact_chat_text_response(response) if payload.compact else response


def codex_assistant_report_create(
    payload: CodexAssistantReportRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    prompt = str(payload.prompt or "").strip() or "relatorio operacional solicitado ao Codex"
    profile = str(payload.profile or "").strip()
    if profile and profile not in {"daily_exceptions", "weekly_sales_stock", "import_order", "custom"}:
        raise HTTPException(status_code=400, detail="Perfil de relatorio invalido.")
    if profile == "import_order" and not str(payload.import_list_id or "").strip():
        raise HTTPException(status_code=400, detail="Informe import_list_id para o relatorio de importacao.")
    context = _assistant_collect_data(
        client_id,
        prompt,
        payload.screen_context,
        mode="report",
        force_refresh=bool(payload.force_refresh),
    )
    if profile:
        context = _assistant_apply_advanced_profile(
            client_id,
            context,
            profile,
            store=str(payload.store or ""),
            import_list_id=str(payload.import_list_id or ""),
            force_refresh=bool(payload.force_refresh),
            prompt=prompt,
        )
    title = {
        "daily_exceptions": "Black Jhon - Diario executivo de vendas e estoque",
        "weekly_sales_stock": "Black Jhon - Semanal completo de vendas e estoque",
        "import_order": "Black Jhon - Analise de importacao por pedido",
        "custom": "Relatorio Black Jhon",
    }.get(profile, "Relatorio Codex Assistente")
    report = _assistant_create_report(client_id, title, context, prompt)
    thread_id = str(payload.thread_id or "").strip()
    conversation_id = str(payload.conversation_id or "").strip()
    if thread_id or conversation_id:
        report["thread_id"] = thread_id
        report["conversation_id"] = conversation_id
        report["screen_context"] = payload.screen_context if isinstance(payload.screen_context, dict) else {}
        try:
            saved_report = codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, report)
            if isinstance(saved_report, dict) and saved_report:
                report = saved_report
        except Exception as exc:
            report.setdefault("warnings", []).append(f"Falha ao atualizar metadata do relatorio: {exc}")
    try:
        from backend.services.codex.console import tasks as console_tasks

        history_task = console_tasks.register_report_history(
            client_id=client_id,
            username=str(sessao.get("username") or ""),
            prompt=prompt,
            report=report,
            thread_id=thread_id,
            conversation_id=conversation_id,
            screen_context=payload.screen_context if isinstance(payload.screen_context, dict) else {},
        )
        if history_task:
            report["history_task"] = history_task
    except Exception as exc:
        report.setdefault("warnings", []).append(f"Falha ao persistir relatorio no historico Codex: {exc}")
    return {"success": True, "report": report}


def codex_assistant_report_get(
    report_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    try:
        metadata = codex_assistant_storage.codex_assistant_report_get(_assistant_info_base(), client_id, report_id)
    except Exception:
        metadata = _assistant_read_json(os.path.join(_assistant_report_dir(client_id, report_id), "metadata.json"), None)
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=404, detail="Relatorio Codex nao encontrado.")
    metadata = _assistant_ensure_report_chat_text(metadata)
    try:
        codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, metadata)
    except Exception:
        pass
    return {"success": True, "report": metadata}


def codex_assistant_report_settings_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    settings = codex_reports_advanced.report_settings_get(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
    )
    return {"success": True, "settings": settings}


def codex_assistant_report_settings_put(
    payload: CodexAssistantReportSettingsRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    settings = codex_reports_advanced.report_settings_save(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        payload.settings,
        str(sessao.get("username") or ""),
    )
    return {"success": True, "settings": settings}


def codex_assistant_financial_adjustments_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    kind: str = "advertising",
    store: str = "",
    period_start: str = "",
    period_end: str = "",
    limit: int = 500,
):
    sessao = _assistant_require_full_admin(request, authorization)
    items = codex_assistant_storage.codex_assistant_financial_adjustments_list(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        kind=kind,
        store=store,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
    )
    return {"success": True, "adjustments": items}


def codex_assistant_financial_adjustments_post(
    payload: CodexAssistantFinancialAdjustmentRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    if payload.amount < 0:
        raise HTTPException(status_code=400, detail="O valor do ajuste nao pode ser negativo.")
    try:
        start = date.fromisoformat(str(payload.period_start)[:10])
        end = date.fromisoformat(str(payload.period_end)[:10])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Periodo do ajuste invalido.") from exc
    if start > end:
        raise HTTPException(status_code=400, detail="O inicio do ajuste deve ser anterior ao fim.")
    item = codex_assistant_storage.codex_assistant_financial_adjustment_save(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
        created_by=str(sessao.get("username") or ""),
    )
    return {"success": True, "adjustment": item}


def codex_assistant_financial_adjustments_delete(
    adjustment_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    removed = codex_assistant_storage.codex_assistant_financial_adjustment_delete(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        adjustment_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Ajuste financeiro nao encontrado.")
    return {"success": True, "deleted": True}


def codex_assistant_action_queue_get(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    status: str = "",
    action_type: str = "",
    owner_username: str = "",
    limit: int = 500,
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    actions = codex_reports_advanced.queue_actions_list(
        _assistant_info_base(),
        str(sessao.get("client_id") or "default"),
        status=status,
        action_type=action_type,
        owner_username=owner_username,
        limit=limit,
    )
    return {"success": True, "actions": actions}


def codex_assistant_action_queue_post(
    payload: CodexAssistantActionQueueRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    item = codex_reports_advanced.create_queue_action(
        info_base=_assistant_info_base(),
        client_id=str(sessao.get("client_id") or "default"),
        username=str(sessao.get("username") or ""),
        payload=payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
    )
    return {"success": True, "action": item}


def codex_assistant_action_queue_patch(
    action_id: str,
    payload: CodexAssistantActionQueueUpdateRequest,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    from backend.services import codex_reports_advanced

    raw_updates = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else payload.dict(exclude_none=True)
    try:
        item = codex_reports_advanced.update_queue_action(
            info_base=_assistant_info_base(),
            client_id=str(sessao.get("client_id") or "default"),
            action_id=action_id,
            username=str(sessao.get("username") or ""),
            updates=raw_updates,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Acao interna nao encontrada.") from exc
    return {"success": True, "action": item}


def codex_assistant_report_download(
    report_id: str,
    request: Request,
    format: str = "html",
    inline: bool = False,
    authorization: Optional[str] = Header(default=None),
):
    sessao = _assistant_require_full_admin(request, authorization)
    client_id = str(sessao.get("client_id") or "default")
    fmt = str(format or "html").strip().lower()
    if fmt not in REPORT_FORMATS:
        raise HTTPException(status_code=400, detail="Formato de relatorio invalido.")
    report_dir = _assistant_report_dir(client_id, report_id)
    path = os.path.join(report_dir, f"report.{fmt}")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Arquivo do relatorio nao encontrado.")
    media_types = {
        "html": "text/html; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }
    filename = f"codex_relatorio_{_assistant_safe_id(report_id)}.{fmt}"
    return FileResponse(
        path,
        media_type=media_types[fmt],
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
    )
configure_codex_assistant_runtime()
