"""Extracted WhatsApp bridge component: function_manager."""

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


def _function_manager_catalog(permissions: Any) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    catalog: list[dict[str, Any]] = []
    for item in codex_assistant._assistant_tools_public(permissions):
        if not isinstance(item, dict) or item.get("read_only") is not True:
            continue
        tool_id = str(item.get("id") or "").strip()
        if not tool_id:
            continue
        catalog.append(
            {
                "id": tool_id,
                "description": str(item.get("description") or "")[:500],
                "external": item.get("external") is True,
                "output_fields": [str(value or "")[:100] for value in list(item.get("output_fields") or [])[:30]],
                "input_schema": item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {},
            }
        )
    return catalog[:100]

def _function_manager_extract_identifiers(value: Any) -> tuple[str, str]:
    text = str(value or "")
    sku_match = re.search(r"\bsku\s*[:#-]?\s*([a-z0-9][a-z0-9._/-]{0,99})\b", text, re.IGNORECASE)
    item_match = re.search(r"\b(MLB\d{6,})\b", text, re.IGNORECASE)
    return (
        str(sku_match.group(1) if sku_match else "").strip(),
        str(item_match.group(1) if item_match else "").upper(),
    )

def _function_manager_enforce_plan(
    plan: dict[str, Any],
    *,
    request_text: str,
    query_policy: dict[str, Any],
    catalog: list[dict[str, Any]],
    max_calls: int,
) -> dict[str, Any]:
    result = dict(plan or {})
    text = _whatsapp_text_key(request_text)
    context_request = str(query_policy.get("context_request") or request_text or "").strip()
    allowed = {str(item.get("id") or "") for item in catalog if isinstance(item, dict)}
    source_policy = query_policy.get("source_policy") if isinstance(query_policy.get("source_policy"), dict) else {}
    forbidden = {str(item or "") for item in list(source_policy.get("forbidden_tools") or []) if str(item or "")}
    explicit_sales = bool(re.search(r"\b(venda|vendas|vendido|vendidos|pedido|pedidos|faturamento|ranking)\b", text))
    explicit_returns = bool(re.search(r"\b(devolucao|devolucoes|reembolso|reembolsos|estorno|estornos)\b", text))
    explicit_stock = bool(
        re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque|full|fulfillment)\b", text)
        or "estoque" in list(query_policy.get("domains") or [])
        or "mercado_full" in list(query_policy.get("domains") or [])
    )
    explicit_image = bool(re.search(r"\b(foto|fotos|imagem|imagens)\b", text))
    mentions_ml = bool(re.search(r"\b(mercado livre|mercadolivre|mlb\d+)\b", text))
    product_query = bool(re.search(r"\b(sku\s*[a-z0-9._/-]+|produto|anuncio|informacao|informacoes|detalhe|detalhes)\b", text))
    technical_web = bool(re.search(r"\b(serve|funciona|encaixa|compativel|compatibilidade|aplica|aplicacao|manual|oem|fabricante)\b", text))
    sku, item_id = _function_manager_extract_identifiers(context_request)
    result["sku"] = str(result.get("sku") or query_policy.get("sku") or sku)[:100]
    result["item_id"] = str(result.get("item_id") or query_policy.get("item_id") or item_id)[:60]
    exact_store = str(query_policy.get("store") or "").strip()
    if exact_store:
        result["store"] = exact_store
        result["store_mode"] = "single"
    elif str(query_policy.get("store_mode") or "") == "all":
        result["store"] = ""
        result["store_mode"] = "all"

    heavy_sales_tools = {
        "mercado_livre_orders", "bling_sales_orders", "sales_returns_query", "sales_ranking", "sales_summary",
        "sales_timeseries", "avg_ticket", "period_comparison", "sales_anomalies",
    }
    return_tools = {"mercado_livre_returns", "returns_summary", "return_rate"}
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_call(tool_id: str, arguments: Optional[dict[str, Any]] = None, required: bool = True, reason: str = "") -> None:
        if tool_id not in allowed or tool_id in forbidden:
            return
        if tool_id in heavy_sales_tools and not explicit_sales:
            return
        if tool_id in return_tools and not explicit_returns:
            return
        if tool_id in {"bling_stock_balances", "mercado_livre_full_stock", "stock_data"} and not explicit_stock:
            return
        args = dict(arguments or {})
        if exact_store:
            args["loja"] = exact_store
        if result.get("sku"):
            args.setdefault("sku", result["sku"])
        if result.get("item_id"):
            args.setdefault("item_id", result["item_id"])
        args.setdefault("message", context_request[:4000])
        signature = json.dumps({"tool_id": tool_id, "arguments": args}, ensure_ascii=False, sort_keys=True, default=str)
        if signature in seen or len(calls) >= max(1, min(6, int(max_calls or 6))):
            return
        seen.add(signature)
        calls.append({"tool_id": tool_id, "arguments": args, "required": bool(required), "reason": str(reason or "")[:500]})

    for item in list(result.get("tool_calls") or []):
        if isinstance(item, dict):
            add_call(
                str(item.get("tool_id") or ""),
                item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                item.get("required") is not False,
                str(item.get("reason") or ""),
            )

    for required_tool in list(source_policy.get("required_tools") or []):
        add_call(
            str(required_tool or ""),
            {"force_refresh": source_policy.get("force_refresh") is not False},
            True,
            "fonte determinada pela politica de roteamento",
        )

    # Guardas deterministicas impedem que uma expansao textual do Luna converta
    # uma consulta de cadastro em varredura completa de pedidos.
    if product_query and not explicit_sales and not explicit_returns and not explicit_stock:
        add_call(
            "mercado_livre_listing",
            {"incluir_detalhes": True, "force_refresh": True},
            mentions_ml,
            "dados do anuncio por SKU",
        )
    if product_query and not explicit_sales and not explicit_returns and not explicit_stock:
        add_call("product_data", {}, False, "cadastro do produto")
        add_call("product_image", {}, False, "imagem cadastrada do produto")
    if explicit_image:
        add_call("product_image", {}, True, "imagem solicitada")
    if explicit_stock:
        if re.search(r"\b(full|fulfillment)\b", text):
            add_call("mercado_livre_full_stock", {"force_refresh": True}, True, "estoque Full solicitado")
        else:
            add_call("bling_stock_balances", {"force_refresh": True}, False, "prioridade 1: saldo atual na Bling")
            add_call("mercado_livre_listing", {"force_refresh": True}, False, "prioridade 2: saldo dos anuncios no Mercado Livre")
            add_call("stock_data", {"force_refresh": True}, False, "prioridade 3: saldo interno do JK Sistema")
            stock_order = {"bling_stock_balances": 1, "mercado_livre_listing": 2, "stock_data": 3}
            stock_calls = [item for item in calls if str(item.get("tool_id") or "") in stock_order]
            other_calls = [item for item in calls if str(item.get("tool_id") or "") not in stock_order]
            for item in stock_calls:
                item["required"] = False
                item["stock_priority"] = stock_order[str(item.get("tool_id") or "")]
            calls = sorted(stock_calls, key=lambda item: int(item.get("stock_priority") or 99)) + other_calls
    if explicit_sales:
        add_call("mercado_livre_orders" if mentions_ml else "sales_ranking", {"force_refresh": True}, True, "vendas solicitadas")
    if explicit_returns:
        add_call("mercado_livre_returns" if mentions_ml else "returns_summary", {"force_refresh": True}, True, "devolucoes solicitadas")

    result["tool_calls"] = calls
    # Consultas internas simples sao respondidas pelo Luna Conversa com o
    # pacote validado, sem pagar uma segunda rodada Sol. Se nenhuma funcao
    # interna resolver a solicitacao, a decisao de pesquisa externa do
    # gerenciador e preservada. Compatibilidade sempre exige analise tecnica.
    result["requires_web"] = bool(technical_web or (result.get("requires_web") and not calls))
    result["requires_sol"] = bool(result["requires_web"] or (result.get("requires_sol") and not calls))
    result["manager_guard"] = {
        "explicit_sales": explicit_sales,
        "explicit_returns": explicit_returns,
        "explicit_stock": explicit_stock,
        "listing_first": product_query and not explicit_sales and not explicit_returns and not explicit_stock,
    }
    return result

def _stock_balance_contract(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.stock_balance_contract(result)

def _normalize_tool_result_contract(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.normalize_tool_result_contract(result)

def _function_manager_compact_result(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.function_manager_compact_result(result)

def _marketplace_listing_stock_contract(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.marketplace_listing_stock_contract(result)

def _local_stock_contract(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.local_stock_contract(result)

def _stock_tool_result_confirmed(result: dict[str, Any]) -> bool:
    return whatsapp_tool_results.stock_tool_result_confirmed(result)

def _function_manager_execute_tools(
    pending: dict[str, Any],
    plan: dict[str, Any],
    query_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)][:6]
    stores = [str(item or "").strip() for item in list(query_policy.get("stores") or []) if str(item or "").strip()]
    if not stores and str(query_policy.get("store") or "").strip():
        stores = [str(query_policy.get("store") or "").strip()]
    stores = stores or [""]
    expanded: list[tuple[int, dict[str, Any], str]] = []
    for index, call in enumerate(calls):
        for store in stores:
            expanded.append((index, call, store))
    if not expanded:
        return []

    def execute(entry: tuple[int, dict[str, Any], str]) -> tuple[int, dict[str, Any]]:
        index, call, store = entry
        args = dict(call.get("arguments") or {}) if isinstance(call.get("arguments"), dict) else {}
        if store:
            args["loja"] = store
        args.setdefault("mode", "chat")
        args.setdefault("limite", 20)
        args.setdefault("force_refresh", True)
        try:
            raw = codex_assistant.codex_assistant_execute_tool_call(
                client_id=str(pending.get("client_id") or "default"),
                tool_id=str(call.get("tool_id") or ""),
                args=args,
                screen_context=pending.get("screen_context") if isinstance(pending.get("screen_context"), dict) else {},
                previous_results=[],
                permissions=pending.get("session_permissions") if isinstance(pending.get("session_permissions"), dict) else {},
                audit_user=str(pending.get("username") or "whatsapp"),
                query_deadline=time.monotonic() + 60,
            )
            value = _function_manager_compact_result(raw)
        except Exception as exc:
            value = {
                "tool_id": str(call.get("tool_id") or ""),
                "success": False,
                "error": str(exc)[:1000],
                "tool_validation": {"dados_suficientes": False, "motivo": str(exc)[:1000]},
            }
        value.setdefault("tool_id", str(call.get("tool_id") or ""))
        value["manager_call_index"] = index
        value["manager_required"] = call.get("required") is not False
        value["manager_store"] = store
        return index, value

    started = time.monotonic()
    manager_guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    staged_stock = bool(
        manager_guard.get("explicit_stock") is True
        and not any(str(item.get("tool_id") or "") == "mercado_livre_full_stock" for item in calls)
    )
    if staged_stock:
        priority = {"bling_stock_balances": 1, "mercado_livre_listing": 2, "stock_data": 3}
        stock_calls = sorted(
            [item for item in calls if str(item.get("tool_id") or "") in priority],
            key=lambda item: priority[str(item.get("tool_id") or "")],
        )

        def execute_store(store_entry: tuple[int, str]) -> tuple[int, list[dict[str, Any]]]:
            store_index, store = store_entry
            store_results: list[dict[str, Any]] = []
            for call in stock_calls:
                _index, value = execute((priority[str(call.get("tool_id") or "")] - 1, call, store))
                value["manager_required"] = False
                store_results.append(value)
                confirmed = _stock_tool_result_confirmed(value)
                if str(value.get("tool_id") or "") == "stock_data" and confirmed:
                    value["local_stock"] = _local_stock_contract(value)
                    if store:
                        # The current local cadastro is an aggregate and is not
                        # evidence of a balance for a named commercial account.
                        value["dados_suficientes"] = False
                        value["coverage_complete"] = False
                        value["error_class"] = "scope_incomplete"
                        value["stock_supporting_only"] = True
                if confirmed:
                    break
            if store_results:
                # One conclusive source is required for every requested store.
                # Earlier failed priorities remain diagnostic, not blockers.
                store_results[-1]["manager_required"] = True
            return store_index, store_results

        grouped: list[tuple[int, list[dict[str, Any]]]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(6, len(stores)),
            thread_name_prefix="jk-wa-stock-priority",
        ) as executor:
            futures = [executor.submit(execute_store, item) for item in enumerate(stores)]
            for future in concurrent.futures.as_completed(futures):
                grouped.append(future.result())
        grouped.sort(key=lambda item: item[0])
        _record_latency("manager_tools_duration", time.monotonic() - started)
        return [result for _store_index, store_results in grouped for result in store_results]

    output: list[tuple[int, dict[str, Any]]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(expanded)), thread_name_prefix="jk-wa-manager-tools") as executor:
        futures = [executor.submit(execute, item) for item in expanded]
        for future in concurrent.futures.as_completed(futures):
            output.append(future.result())
    _record_latency("manager_tools_duration", time.monotonic() - started)
    output.sort(key=lambda item: item[0])
    return [item[1] for item in output]

def _function_manager_evidence(plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    return whatsapp_tool_results.function_manager_evidence(plan, results)

def _function_manager_merge_evidence(previous: Any, current: dict[str, Any]) -> dict[str, Any]:
    return whatsapp_tool_results.function_manager_merge_evidence(previous, current)

def _configure_function_manager_executor(worker_count: int) -> None:
    global FUNCTION_MANAGER_EXECUTOR, FUNCTION_MANAGER_EXECUTOR_WORKERS
    workers = max(1, min(8, int(worker_count or 4)))
    with FUNCTION_MANAGER_LOCK:
        if FUNCTION_MANAGER_EXECUTOR is not None and FUNCTION_MANAGER_EXECUTOR_WORKERS == workers:
            return
        previous = FUNCTION_MANAGER_EXECUTOR
        FUNCTION_MANAGER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="jk-whatsapp-function-manager",
        )
        FUNCTION_MANAGER_EXECUTOR_WORKERS = workers
    if previous is not None:
        previous.shutdown(wait=False, cancel_futures=False)

def _record_function_manager_diagnostic(
    state: dict[str, Any],
    pending: dict[str, Any],
    *,
    status: str,
    reason: str,
) -> None:
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    evidence = pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {}
    entry = {
        "job_id": str(pending.get("job_group_id") or "")[:100],
        "agent_role": "function_manager",
        "status": str(status or "")[:80],
        "reason": str(reason or plan.get("reason") or "")[:1000],
        "effective_model": str(pending.get("manager_effective_model") or "")[:100],
        "reasoning_effort": str(pending.get("manager_reasoning_effort") or "")[:20],
        "speed": str(pending.get("manager_speed") or "")[:20],
        "service_tier": str(pending.get("manager_service_tier") or "")[:40],
        "planning_duration_ms": int(pending.get("manager_planning_duration_ms") or 0),
        "tools_duration_ms": int(pending.get("manager_tools_duration_ms") or 0),
        "total_duration_ms": int(pending.get("manager_total_duration_ms") or 0),
        "tool_ids": [
            str(item.get("tool_id") or "")[:100]
            for item in list(plan.get("tool_calls") or [])[:6]
            if isinstance(item, dict) and str(item.get("tool_id") or "")
        ],
        "validations": [
            {
                "tool_id": str(item.get("tool_id") or "")[:100],
                "required": item.get("required") is True,
                "dados_suficientes": item.get("dados_suficientes") is True,
            }
            for item in list(evidence.get("validations") or [])[:12]
            if isinstance(item, dict)
        ],
        "requires_sol": plan.get("requires_sol") is True,
        "requires_web": plan.get("requires_web") is True,
        "retry_count": int(pending.get("manager_retry_count") or 0),
        "recorded_at": _now(),
    }
    with DUAL_AGENT_STATE_LOCK, BRIDGE_STATE_LOCK:
        history = [item for item in list(state.get("function_manager_diagnostics") or []) if isinstance(item, dict)]
        history.append(entry)
        state["function_manager_diagnostics"] = history[-100:]
        _save_state(state)

def _function_manager_retry(pending: dict[str, Any], reason: str) -> None:
    count = max(0, int(pending.get("manager_retry_count") or 0)) + 1
    error_class, retryable = _dual_retry_classification(reason)
    if not retryable or count > WHATSAPP_MAX_RETRY_ATTEMPTS:
        pending.update(
            {
                "manager_state": "partial",
                "job_state": "partial",
                "manager_retry_count": count,
                "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
                "manager_next_retry_at_epoch": 0,
                "next_retry_at_epoch": 0,
                "retry_policy": "bounded",
                "terminal_reason": str(reason or "limite_de_tentativas_atingido")[:1000],
                "terminal_error_class": error_class,
            }
        )
        return
    delay = _dual_retry_delay_seconds(count, reason, str(pending.get("job_group_id") or "manager"))
    pending.update(
        {
            "manager_state": "waiting_retry",
            "job_state": "waiting_retry",
            "manager_retry_count": count,
            "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
            "manager_next_retry_at_epoch": time.time() + delay,
            "next_retry_at_epoch": time.time() + delay,
            "retry_policy": "bounded",
        }
    )

def _function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> None:
    started = time.monotonic()
    try:
        with BRIDGE_STATE_LOCK:
            current = (state.get("pending_messages") or {}).get(message_id) if isinstance(state.get("pending_messages"), dict) else None
            pending = dict(current) if isinstance(current, dict) else {}
        if not pending or str(pending.get("kind") or "") != "dual_function_manager":
            return
        run_revision = max(0, int(pending.get("manager_revision") or 0))
        pending.update({"manager_state": "running", "job_state": "manager_running", "manager_started_at": _now()})
        _save_pending(state, message_id, pending)
        settings = _whatsapp_dual_agent_settings(config)
        catalog = _function_manager_catalog(pending.get("session_permissions"))
        pending["manager_tool_catalog"] = catalog
        manager_policy = (
            pending.get("manager_query_policy")
            if isinstance(pending.get("manager_query_policy"), dict)
            else pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {}
        )
        planning_started = time.monotonic()
        deterministic_plan = pending.get("deterministic_plan") if isinstance(pending.get("deterministic_plan"), dict) else {}
        if deterministic_plan:
            raw_plan = dict(deterministic_plan)
            raw_plan.update(
                {
                    "thread_id": "",
                    "effective_model": "deterministic-router",
                    "reasoning_effort": "none",
                    "speed": "direct",
                    "service_tier": "local",
                }
            )
        else:
            raw_plan = codex_whatsapp_agents.FUNCTION_MANAGER_RUNTIME.run_manager(
                thread_id=str(pending.get("function_manager_thread_id") or ""),
                model=settings["conversation_agent_model"],
                reasoning_effort=settings["conversation_agent_reasoning"],
                speed=settings["conversation_agent_speed"],
                service_tier=settings["conversation_agent_service_tier"],
                request_text=str(pending.get("request_text") or ""),
                job_prompt=str(pending.get("job_prompt") or pending.get("request_text") or ""),
                query_policy=manager_policy,
                tool_catalog=catalog,
                previous_evidence=pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {},
                data_requests=pending.get("manager_data_requests") if isinstance(pending.get("manager_data_requests"), list) else [],
                max_calls=settings["max_subtasks_per_job"],
            )
        planning_duration_ms = int(round((time.monotonic() - planning_started) * 1000))
        plan = _function_manager_enforce_plan(
            raw_plan,
            request_text=str(pending.get("request_text") or ""),
            query_policy=manager_policy,
            catalog=catalog,
            max_calls=settings["max_subtasks_per_job"],
        )
        with BRIDGE_STATE_LOCK:
            latest = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(latest, dict) or int(latest.get("manager_revision") or 0) != run_revision:
            return
        pending.update(
            {
                "function_manager_thread_id": str(raw_plan.get("thread_id") or "")[:200],
                "manager_effective_model": str(raw_plan.get("effective_model") or "")[:100],
                "manager_reasoning_effort": str(raw_plan.get("reasoning_effort") or "")[:20],
                "manager_speed": str(raw_plan.get("speed") or "")[:20],
                "manager_service_tier": str(raw_plan.get("service_tier") or "")[:40],
                "manager_plan": plan,
                "manager_planned_at": _now(),
                "manager_planning_duration_ms": planning_duration_ms,
            }
        )
        if list(plan.get("missing_user_fields") or []):
            evidence = {
                "status": "blocked",
                "summary": "Faltam dados do usuario para executar as consultas internas.",
                "verified_facts": [],
                "sources": [],
                "confidence": "low",
                "evidence_sufficient": False,
                "coverage_complete": False,
                "missing": list(plan.get("missing_user_fields") or []),
                "questions": list(plan.get("missing_user_fields") or [])[:2],
                "data_requests": [],
            }
            pending.update({"manager_evidence": evidence, "manager_state": "awaiting_input", "job_state": "awaiting_input"})
            pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
            _save_pending(state, message_id, pending)
            _record_function_manager_diagnostic(
                state,
                pending,
                status="awaiting_input",
                reason="missing_user_fields",
            )
            questions = [str(item or "").strip() for item in list(evidence.get("questions") or []) if str(item or "").strip()]
            prompt = "Preciso desta informação para continuar: " + (questions[0] if questions else "informe os dados que faltam no pedido.")
            delivery = _post_proactive(
                config,
                {
                    "subject_id": str(pending.get("subject_id") or ""),
                    "fingerprint": f"manager:{pending.get('job_group_id')}:input",
                    "event_type": "task_partial",
                    "severity": "info",
                    "text": prompt[:3500],
                },
            )
            pending["awaiting_notified"] = str(delivery.get("status") or "") in {
                "sent",
                "queued",
                "duplicate",
                "waiting_free_window",
            }
            pending["delivery_state"] = f"awaiting_input_{str(delivery.get('status') or 'failed')}"
            _save_pending(state, message_id, pending)
            return
        tools_started = time.monotonic()
        results = _function_manager_execute_tools(pending, plan, manager_policy)
        pending["manager_tools_duration_ms"] = int(round((time.monotonic() - tools_started) * 1000))
        evidence = _function_manager_merge_evidence(
            pending.get("manager_evidence"),
            _function_manager_evidence(plan, results),
        )
        with BRIDGE_STATE_LOCK:
            latest = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(latest, dict) or int(latest.get("manager_revision") or 0) != run_revision:
            return
        pending.update(
            {
                "manager_evidence": evidence,
                "manager_state": "completed" if evidence.get("evidence_sufficient") else "partial",
                "manager_completed_at": _now(),
                "verified_facts": list(evidence.get("verified_facts") or []),
                "verified_sources": list(evidence.get("sources") or []),
                "manager_total_duration_ms": int(round((time.monotonic() - started) * 1000)),
            }
        )
        _save_pending(state, message_id, pending)
        with BRIDGE_STATE_LOCK:
            still_pending = (
                (state.get("pending_messages") or {}).get(message_id)
                if isinstance(state.get("pending_messages"), dict)
                else None
            )
        if not isinstance(still_pending, dict) or still_pending.get("cancel_requested") is True:
            return
        required_retryable = any(
            isinstance(item, dict)
            and item.get("required") is True
            and item.get("dados_suficientes") is not True
            and item.get("retryable") is True
            for item in list(evidence.get("validations") or [])
        )
        deterministic_terminal = bool(
            pending.get("deterministic_plan")
            and results
            and not required_retryable
            and not plan.get("requires_sol")
            and not plan.get("requires_web")
        )
        if (
            evidence.get("evidence_sufficient") is True
            or deterministic_terminal
        ) and not plan.get("requires_sol") and not plan.get("requires_web"):
            _record_function_manager_diagnostic(
                state,
                pending,
                status="completed_without_sol" if evidence.get("evidence_sufficient") is True else "partial_without_sol",
                reason="internal_evidence_sufficient" if evidence.get("evidence_sufficient") is True else "non_retryable_partial_evidence",
            )
            _function_manager_deliver_direct(config, state, message_id, pending)
            return
        if plan.get("requires_sol") or plan.get("requires_web"):
            _record_function_manager_diagnostic(
                state,
                pending,
                status="handed_to_sol",
                reason="technical_or_external_analysis_required",
            )
            if _function_manager_start_sol(config, state, message_id, pending):
                return
            _function_manager_retry(pending, "sol_creation_failed")
            _save_pending(state, message_id, pending)
            return
        _function_manager_retry(pending, "evidencia_interna_insuficiente")
        _record_function_manager_diagnostic(
            state,
            pending,
            status="waiting_retry",
            reason="evidencia_interna_insuficiente",
        )
        _save_pending(state, message_id, pending)
    except Exception as exc:
        with BRIDGE_STATE_LOCK:
            current = (state.get("pending_messages") or {}).get(message_id) if isinstance(state.get("pending_messages"), dict) else None
            pending = dict(current) if isinstance(current, dict) else {}
        if pending:
            _function_manager_retry(pending, str(exc)[:1000])
            pending["manager_last_error"] = str(exc)[:1000]
            pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
            _record_function_manager_diagnostic(
                state,
                pending,
                status="waiting_retry",
                reason=str(exc)[:1000],
            )
            _save_pending(state, message_id, pending)
        RUNTIME_STATE["dual_agent_last_error"] = str(exc)[:1000]
    finally:
        _record_latency("manager_duration", time.monotonic() - started)

def _function_manager_future_done(message_id: str, future: concurrent.futures.Future[Any]) -> None:
    with FUNCTION_MANAGER_LOCK:
        if FUNCTION_MANAGER_FUTURES.get(message_id) is future:
            FUNCTION_MANAGER_FUTURES.pop(message_id, None)

def _submit_function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> bool:
    settings = _whatsapp_dual_agent_settings(config)
    _configure_function_manager_executor(settings["function_manager_worker_count"])
    with FUNCTION_MANAGER_LOCK:
        current = FUNCTION_MANAGER_FUTURES.get(message_id)
        if current is not None and not current.done():
            return False
        assert FUNCTION_MANAGER_EXECUTOR is not None
        future = FUNCTION_MANAGER_EXECUTOR.submit(_function_manager_job, dict(config), state, message_id)
        FUNCTION_MANAGER_FUTURES[message_id] = future
        future.add_done_callback(lambda completed, mid=message_id: _function_manager_future_done(mid, completed))
    return True


_COMPONENT_FUNCTIONS = frozenset((
    '_function_manager_catalog',
    '_function_manager_extract_identifiers',
    '_function_manager_enforce_plan',
    '_stock_balance_contract',
    '_normalize_tool_result_contract',
    '_function_manager_compact_result',
    '_marketplace_listing_stock_contract',
    '_local_stock_contract',
    '_stock_tool_result_confirmed',
    '_function_manager_execute_tools',
    '_function_manager_evidence',
    '_function_manager_merge_evidence',
    '_configure_function_manager_executor',
    '_record_function_manager_diagnostic',
    '_function_manager_retry',
    '_function_manager_job',
    '_function_manager_future_done',
    '_submit_function_manager_job'
))
_IMPLEMENTATIONS = {
    '_function_manager_catalog': _function_manager_catalog,
    '_function_manager_extract_identifiers': _function_manager_extract_identifiers,
    '_function_manager_enforce_plan': _function_manager_enforce_plan,
    '_stock_balance_contract': _stock_balance_contract,
    '_normalize_tool_result_contract': _normalize_tool_result_contract,
    '_function_manager_compact_result': _function_manager_compact_result,
    '_marketplace_listing_stock_contract': _marketplace_listing_stock_contract,
    '_local_stock_contract': _local_stock_contract,
    '_stock_tool_result_confirmed': _stock_tool_result_confirmed,
    '_function_manager_execute_tools': _function_manager_execute_tools,
    '_function_manager_evidence': _function_manager_evidence,
    '_function_manager_merge_evidence': _function_manager_merge_evidence,
    '_configure_function_manager_executor': _configure_function_manager_executor,
    '_record_function_manager_diagnostic': _record_function_manager_diagnostic,
    '_function_manager_retry': _function_manager_retry,
    '_function_manager_job': _function_manager_job,
    '_function_manager_future_done': _function_manager_future_done,
    '_submit_function_manager_job': _submit_function_manager_job
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
