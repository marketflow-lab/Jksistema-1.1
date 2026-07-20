"""Extracted WhatsApp bridge component: function_manager."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import re
import time
from typing import Any, Optional
import requests
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp import context_hub_telemetry as whatsapp_context_hub_telemetry
from backend.services.whatsapp import data_selection_enforcement as whatsapp_data_selection_enforcement
from backend.services.whatsapp.orchestration import retry_coordinator as whatsapp_retry_coordinator
from backend.services import codex_whatsapp_agents
from backend.services.whatsapp.composition import BridgeDependencies, bind_component_namespace, invoke_component
WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS
def _whatsapp_dual_agent_settings(config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Direct-import fallback; bridge composition may replace this binding."""

    return whatsapp_settings.dual_agent_settings(dict(config or {}))
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
    item_match = re.search(r"\bMLB[\s_-]*(\d{6,})\b", text, re.IGNORECASE)
    return (
        str(sku_match.group(1) if sku_match else "").strip(),
        (f"MLB{item_match.group(1)}" if item_match else "").upper(),
    )
_function_manager_enforce_plan = whatsapp_data_selection_enforcement.enforce_plan

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
def _function_manager_result_sufficient(value: dict[str, Any]) -> bool:
    validation = value.get("tool_validation") if isinstance(value.get("tool_validation"), dict) else {}
    return value.get("dados_suficientes") is True or validation.get("dados_suficientes") is True
def _function_manager_execute_tools(
    pending: dict[str, Any],
    plan: dict[str, Any],
    query_policy: dict[str, Any],
    config: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    from backend.services import codex_assistant

    calls = [item for item in list(plan.get("tool_calls") or []) if isinstance(item, dict)][:6]
    stores = [str(item or "").strip() for item in list(query_policy.get("stores") or []) if str(item or "").strip()]
    if not stores and str(query_policy.get("store") or "").strip():
        stores = [str(query_policy.get("store") or "").strip()]
    stores = stores or [""]
    if not calls:
        return []

    def execute(
        entry: tuple[int, dict[str, Any], int, str, list[dict[str, Any]]],
    ) -> tuple[int, dict[str, Any]]:
        index, call, store_index, store, dependency_results = entry
        args = dict(call.get("arguments") or {}) if isinstance(call.get("arguments"), dict) else {}
        for untrusted_tenant_key in (
            "authorization", "permissions", "client_id", "tenant_id", "tenant", "cliente_id",
            "access_token", "refresh_token", "token", "api_key",
        ):
            args.pop(untrusted_tenant_key, None)
        if store:
            args["loja"] = store
        args.setdefault("mode", "chat")
        args.setdefault("limite", 20)
        args.setdefault("force_refresh", True)
        if str(call.get("tool_id") or "") == "context_hub_search": args["request_surface"] = "black_jhon_whatsapp"
        try:
            is_hub = str(call.get("tool_id") or "") == "context_hub_search"
            bound_session = whatsapp_retry_coordinator._reload_active_bound_session(config or {}, pending) if is_hub else pending
            bound_client_id = str(bound_session.get("client_id") or "").strip()
            if not bound_client_id:
                raise RuntimeError("data_selection_tenant_required")
            permissions = bound_session.get("permissions") if is_hub else pending.get("session_permissions")
            raw = codex_assistant.codex_assistant_execute_tool_call(
                client_id=bound_client_id,
                tool_id=str(call.get("tool_id") or ""),
                args=args,
                screen_context=pending.get("screen_context") if isinstance(pending.get("screen_context"), dict) else {},
                previous_results=dependency_results,
                permissions=permissions if isinstance(permissions, dict) else {},
                audit_user=str(pending.get("username") or "whatsapp"),
                query_deadline=time.monotonic() + 60,
            )
            value = _function_manager_compact_result(raw)
        except Exception as exc:
            error_class, retryable = whatsapp_retry_policy.retry_classification(exc)
            error_code = f"tool_{error_class}"
            value = {
                "tool_id": str(call.get("tool_id") or ""),
                "success": False,
                "error": error_code,
                "error_class": error_class,
                "retryable": retryable,
                "tool_validation": {"dados_suficientes": False, "motivo": error_code},
            }
        value.setdefault("tool_id", str(call.get("tool_id") or ""))
        value["manager_call_index"] = index
        value["manager_required"] = call.get("required") is not False
        value["manager_store"] = store
        return store_index, value

    started = time.monotonic()
    output: list[dict[str, Any]] = []
    by_call_store: dict[tuple[int, str], dict[str, Any]] = {}

    for call_index, call in enumerate(calls):
        dependencies = [
            item for item in list(call.get("depends_on") or [])[:6]
            if isinstance(item, int) and 0 <= item < call_index
        ]
        call_results: list[tuple[int, dict[str, Any]]] = []
        executable: list[tuple[int, dict[str, Any], int, str, list[dict[str, Any]]]] = []
        for store_index, store in enumerate(stores):
            dependency_results = [
                by_call_store[(dependency, store)]
                for dependency in dependencies
                if (dependency, store) in by_call_store
            ]
            blocked = any(
                calls[dependency].get("required") is not False
                and (
                    (dependency, store) not in by_call_store
                    or not _function_manager_result_sufficient(by_call_store[(dependency, store)])
                )
                for dependency in dependencies
            )
            if blocked:
                call_results.append((store_index, {
                    "tool_id": str(call.get("tool_id") or ""),
                    "success": False,
                    "error": "dependency_failed",
                    "error_class": "dependency",
                    "retryable": False,
                    "tool_validation": {"dados_suficientes": False, "motivo": "dependency_failed"},
                    "manager_call_index": call_index,
                    "manager_required": call.get("required") is not False,
                    "manager_store": store,
                }))
                continue
            executable.append((call_index, call, store_index, store, dependency_results))
        if executable:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(6, len(executable)),
                thread_name_prefix="jk-wa-manager-call",
            ) as executor:
                futures = [executor.submit(execute, item) for item in executable]
                for future in concurrent.futures.as_completed(futures):
                    call_results.append(future.result())
        call_results.sort(key=lambda item: item[0])
        for _store_index, value in call_results:
            by_call_store[(call_index, str(value.get("manager_store") or ""))] = value
            output.append(value)
    _record_latency("manager_tools_duration", time.monotonic() - started)
    return output
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
    plan = (
        pending.get("data_selection_plan")
        if isinstance(pending.get("data_selection_plan"), dict)
        else pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    )
    evidence = pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else {}
    context_hub_diagnostic = whatsapp_context_hub_telemetry.context_hub_diagnostic_summary(pending, plan, evidence)
    raw_reason = str(reason or "").strip()
    safe_reason = raw_reason[:120] if re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", raw_reason) else "redacted"
    entry = {
        "client_id": whatsapp_context_hub_telemetry.safe_internal_client_id(pending.get("client_id")),
        "job_id": str(pending.get("job_group_id") or "")[:100],
        "agent_role": "data_selection",
        "status": str(status or "")[:80],
        "reason": safe_reason,
        "effective_model": str(pending.get("data_selection_effective_model") or pending.get("manager_effective_model") or "")[:100],
        "reasoning_effort": str(pending.get("data_selection_reasoning_effort") or pending.get("manager_reasoning_effort") or "")[:20],
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
    if context_hub_diagnostic:
        entry["context_hub"] = context_hub_diagnostic
    with DUAL_AGENT_STATE_LOCK, BRIDGE_STATE_LOCK:
        history = [item for item in list(state.get("data_selection_diagnostics") or []) if isinstance(item, dict)]
        history.append(entry)
        state["data_selection_diagnostics"] = history[-100:]
        _save_state(state)
def _function_manager_retry(pending: dict[str, Any], reason: str) -> None:
    count = max(0, int(pending.get("manager_retry_count") or 0)) + 1
    error_class, retryable = _dual_retry_classification(reason)
    if not retryable or count > WHATSAPP_MAX_RETRY_ATTEMPTS:
        pending.update(
            {
                "manager_state": "partial",
                "data_selection_state": "partial",
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
            "data_selection_state": "waiting_retry",
            "job_state": "waiting_retry",
            "manager_retry_count": count,
            "manager_retry_reason": str(reason or "resultado_interno_incompleto")[:1000],
            "manager_next_retry_at_epoch": time.time() + delay,
            "next_retry_at_epoch": time.time() + delay,
            "retry_policy": "bounded",
        }
    )

def _function_manager_pending_snapshot(state: dict[str, Any], message_id: str) -> dict[str, Any]:
    with BRIDGE_STATE_LOCK:
        current = (state.get("pending_messages") or {}).get(message_id) if isinstance(state.get("pending_messages"), dict) else None
    return dict(current) if isinstance(current, dict) else {}

def _function_manager_revision_matches(state: dict[str, Any], message_id: str, revision: int) -> bool:
    latest = _function_manager_pending_snapshot(state, message_id)
    return bool(latest and int(latest.get("manager_revision") or 0) == revision)
def _data_selection_gap_key(pending: dict[str, Any]) -> str:
    requests = list(pending.get("manager_data_requests") or [])[:12]
    if not requests:
        return "initial"
    encoded = json.dumps(requests, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
def _data_selection_error_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    match = re.search(r"\b(data_selection_[a-z0-9_.:-]{1,100})\b", text)
    if match:
        return match.group(1)
    return "data_selection_internal_error"
def _data_selection_register_plan_attempt(pending: dict[str, Any]) -> None:
    """Allow one replan per data gap; tool retries reuse the stored plan."""

    gap_key = _data_selection_gap_key(pending)
    reusable_plan = bool(
        isinstance(pending.get("data_selection_raw_plan"), dict)
        and pending.get("data_selection_raw_plan")
        and str(pending.get("data_selection_gap_key") or "") == gap_key
    )
    if reusable_plan:
        return
    attempt_counts = {
        str(key): max(0, int(value or 0))
        for key, value in dict(pending.get("data_selection_attempts_by_gap") or {}).items()
        if re.fullmatch(r"(?:initial|[a-f0-9]{64})", str(key or ""))
    }
    next_attempt = attempt_counts.get(gap_key, 0) + 1
    if next_attempt > 2:
        raise RuntimeError("data_selection_replan_limit")
    attempt_counts[gap_key] = next_attempt
    pending["data_selection_attempts_by_gap"] = dict(list(attempt_counts.items())[-12:])

def _function_manager_build_plan(
    config: dict[str, Any],
    pending: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], int]:
    settings = _whatsapp_dual_agent_settings(config)
    queued_policy = (
        pending.get("manager_query_policy")
        if isinstance(pending.get("manager_query_policy"), dict)
        else pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {}
    )
    manager_policy = {
        "read_only": True,
        "deny_approval": True,
        "authorized_stores": [
            str(item or "").strip()
            for item in list(queued_policy.get("authorized_stores") or [])
            if str(item or "").strip()
        ],
        "context_hub_enabled": queued_policy.get("context_hub_enabled") is not False,
    }
    client_id = str(pending.get("client_id") or "").strip()
    if not client_id:
        raise RuntimeError("data_selection_tenant_required")
    permissions = dict(pending.get("session_permissions") or {})
    if manager_policy.get("context_hub_enabled") is not False:
        # Ephemeral planning permission only.  Execution reloads the active
        # bound session and applies the tenant Context Hub flag again.
        permissions["context_hub_read_full"] = True
    catalog = _function_manager_catalog(permissions)
    authorized_stores = [
        str(item or "").strip()
        for item in list(manager_policy.get("authorized_stores") or manager_policy.get("stores") or [])
        if str(item or "").strip()
    ]
    if not authorized_stores and str(manager_policy.get("store") or "").strip():
        authorized_stores = [str(manager_policy.get("store") or "").strip()]
    conversation_anchors = (
        dict(pending.get("conversation_anchors") or {})
        if isinstance(pending.get("conversation_anchors"), dict)
        else {}
    )
    planning_started = time.monotonic()
    gap_key = _data_selection_gap_key(pending)
    stored_raw_plan = (
        pending.get("data_selection_raw_plan")
        if isinstance(pending.get("data_selection_raw_plan"), dict)
        and str(pending.get("data_selection_gap_key") or "") == gap_key
        else {}
    )
    if stored_raw_plan:
        raw_plan = dict(stored_raw_plan)
    else:
        raw_plan = codex_whatsapp_agents.DATA_SELECTION_RUNTIME.plan(
            request_text=str(pending.get("request_text") or ""),
            job_prompt=str(pending.get("job_prompt") or pending.get("request_text") or ""),
            surface="whatsapp",
            allowed_tools=catalog,
            authorized_stores=authorized_stores,
            conversation_anchors=conversation_anchors,
            previous_evidence=pending.get("manager_evidence") if isinstance(pending.get("manager_evidence"), dict) else None,
            data_gap={
                "requests": list(pending.get("manager_data_requests") or [])[:12],
            } if isinstance(pending.get("manager_data_requests"), list) else None,
            model="gpt-5.6-luna",
            reasoning_effort="low",
            max_calls=settings["max_subtasks_per_job"],
        )
    if not isinstance(raw_plan, dict) or not str(raw_plan.get("action") or "").strip():
        raise RuntimeError("data_selection_invalid_plan")
    duration_ms = int(round((time.monotonic() - planning_started) * 1000))
    selection_context = "\n".join(
        value
        for value in (
            str(pending.get("request_text") or "").strip(),
            str(pending.get("job_prompt") or "").strip(),
        )
        if value
    )[:8000]
    plan = _function_manager_enforce_plan(
        raw_plan,
        request_text=selection_context,
        query_policy=manager_policy,
        catalog=catalog,
        max_calls=settings["max_subtasks_per_job"],
    )
    execution_policy = dict(manager_policy)
    if str(plan.get("store_mode") or "") == "single" and str(plan.get("store") or "").strip():
        execution_policy.update({"store_mode": "single", "store": str(plan["store"]).strip(), "stores": []})
    elif str(plan.get("store_mode") or "") == "all":
        scoped_stores = [
            str(item or "").strip()
            for item in list(manager_policy.get("authorized_stores") or [])
            if str(item or "").strip()
        ]
        if not scoped_stores:
            raise RuntimeError("data_selection_store_scope_unavailable")
        execution_policy.update({
            "store_mode": "all",
            "store": "",
            "stores": scoped_stores,
        })
    return plan, raw_plan, execution_policy, catalog, duration_ms

def _function_manager_apply_plan(
    pending: dict[str, Any],
    plan: dict[str, Any],
    raw_plan: dict[str, Any],
    catalog: list[dict[str, Any]],
    duration_ms: int,
) -> None:
    pending.update(
        {
            "manager_tool_catalog": catalog,
            "data_selection_thread_id": str(raw_plan.get("thread_id") or "")[:200],
            "function_manager_thread_id": str(raw_plan.get("thread_id") or "")[:200],
            "data_selection_effective_model": str(raw_plan.get("effective_model") or raw_plan.get("model") or "")[:100],
            "manager_effective_model": str(raw_plan.get("effective_model") or raw_plan.get("model") or "")[:100],
            "data_selection_reasoning_effort": str(raw_plan.get("reasoning_effort") or "")[:20],
            "manager_reasoning_effort": str(raw_plan.get("reasoning_effort") or "")[:20],
            "manager_speed": str(raw_plan.get("speed") or "")[:20],
            "manager_service_tier": str(raw_plan.get("service_tier") or "")[:40],
            "data_selection_plan": plan,
            "data_selection_raw_plan": raw_plan,
            "data_selection_gap_key": _data_selection_gap_key(pending),
            "data_selection_state": "planned",
            "manager_plan": plan,
            "manager_planned_at": _now(),
            "manager_planning_duration_ms": duration_ms,
        }
    )

def _function_manager_request_missing_input(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    plan: dict[str, Any],
    started: float,
) -> bool:
    missing = list(plan.get("missing_user_fields") or [])
    if not missing:
        return False
    evidence = {
        "status": "blocked",
        "summary": "Faltam dados do usuario para executar as consultas internas.",
        "verified_facts": [],
        "sources": [],
        "confidence": "low",
        "evidence_sufficient": False,
        "coverage_complete": False,
        "missing": missing,
        "questions": missing[:2],
        "data_requests": [],
    }
    pending.update({
        "manager_evidence": evidence,
        "manager_state": "awaiting_input",
        "data_selection_state": "awaiting_input",
        "job_state": "awaiting_input",
    })
    pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
    _save_pending(state, message_id, pending)
    _record_function_manager_diagnostic(state, pending, status="awaiting_input", reason="missing_user_fields")
    try:
        query_policy = pending.get("manager_query_policy") if isinstance(pending.get("manager_query_policy"), dict) else {}
        decision = _run_conversation_agent(
            config,
            state,
            str(pending.get("conversation_id") or ""),
            event_type="worker_result",
            user_message=str(pending.get("request_text") or ""),
            active_job={
                "job_id": str(pending.get("job_group_id") or message_id),
                "job_title": str(pending.get("job_title") or ""),
                "status": "blocked",
            },
            worker_result=evidence,
            ai_behavior=str(pending.get("phone_ai_behavior") or ""),
            authorized_stores=[
                str(item or "").strip()
                for item in list(query_policy.get("authorized_stores") or [])
                if str(item or "").strip()
            ],
            client_id=str(pending.get("client_id") or ""),
        )
        prompt = str(decision.get("reply_text") or "").strip()
    except Exception:
        prompt = ""
    if not prompt:
        prompt = "Nao consegui confirmar os dados necessarios. Pode reformular seu pedido em uma frase?"
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
    pending["awaiting_notified"] = str(delivery.get("status") or "") in {"sent", "queued", "duplicate", "waiting_free_window"}
    pending["delivery_state"] = f"awaiting_input_{str(delivery.get('status') or 'failed')}"
    _save_pending(state, message_id, pending)
    return True

def _function_manager_run_tools(
    config: dict[str, Any],
    pending: dict[str, Any],
    plan: dict[str, Any],
    manager_policy: dict[str, Any],
    started: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tools_started = time.monotonic()
    results = _function_manager_execute_tools(pending, plan, manager_policy, config)
    pending["manager_tools_duration_ms"] = int(round((time.monotonic() - tools_started) * 1000))
    evidence = _function_manager_merge_evidence(pending.get("manager_evidence"), _function_manager_evidence(plan, results))
    codex_whatsapp_agents.DATA_SELECTION_RUNTIME.record_evidence_size(
        evidence,
        report=any(
            "report" in str(item or "").casefold() or "relatorio" in str(item or "").casefold()
            for item in [*list(plan.get("intents") or []), *list(plan.get("requested_fields") or [])]
        ),
    )
    pending.update(
        {
            "manager_evidence": evidence,
            "manager_state": "completed" if evidence.get("evidence_sufficient") else "partial",
            "data_selection_state": "completed" if evidence.get("evidence_sufficient") else "partial",
            "manager_completed_at": _now(),
            "verified_facts": list(evidence.get("verified_facts") or []),
            "verified_sources": list(evidence.get("sources") or []),
            "manager_total_duration_ms": int(round((time.monotonic() - started) * 1000)),
        }
    )
    return results, evidence
def _function_manager_handoff_mutation_candidate(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    plan: dict[str, Any],
) -> bool:
    """Hand a classified mutation to the existing approval workflow.

    The data-selection agent only identifies the candidate.  It never receives
    write tools and never executes or approves the action.  Full-access users
    are handed to the canonical Codex proposal flow; read-only users remain in
    the fail-closed explanatory path below.
    """

    permissions = dict(pending.get("session_permissions") or {})
    mobile_full_access = bool(pending.get("session_is_full") and permissions.get("full") is True)
    if not mobile_full_access:
        return False
    client_id = str(pending.get("client_id") or "").strip()
    username = str(pending.get("username") or "").strip().lower()
    if not client_id or not username:
        raise RuntimeError("data_selection_mutation_identity_required")
    request_text = str(pending.get("request_text") or "").strip()
    if not request_text:
        raise RuntimeError("data_selection_mutation_request_required")
    session = {
        "username": username,
        "client_id": client_id,
        "permissions": permissions,
        "is_full": True,
    }
    result = _create_selected_ai_task(
        config,
        prompt=request_text,
        session=session,
        conversation_id=str(pending.get("conversation_id") or ""),
        paths=[],
        screen_context=(
            dict(pending.get("screen_context") or {})
            if isinstance(pending.get("screen_context"), dict)
            else {}
        ),
        safe_read_only=False,
        mobile_full_access=True,
        channel_metadata={
            "message_id": message_id,
            "subject_id": str(pending.get("subject_id") or ""),
            "wa_id": str(pending.get("wa_id") or ""),
            "message_type": "text",
            "request_text": request_text,
            "query_policy": {},
            "mobile_full_access": True,
            "data_selection_action": "mutation_candidate",
            "data_selection_schema_version": str(plan.get("schema_version") or "1.0")[:20],
        },
    )
    task = result.get("task") if isinstance(result, dict) and isinstance(result.get("task"), dict) else {}
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError("data_selection_mutation_proposal_failed")
    proposal = task.get("proposal") if isinstance(task.get("proposal"), dict) else {}
    action_pending = {
        "task_id": task_id,
        "kind": "task",
        "conversation_id": str(pending.get("conversation_id") or ""),
        "subject_id": str(pending.get("subject_id") or ""),
        "username": username,
        "client_id": client_id,
        "request_text": request_text,
        "mobile_full_access": True,
        "query_policy": {},
        "general_answer": False,
        "created_at": _now(),
        "awaiting_notified": False,
        "trusted_bound_number": True,
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "proposal_version": int(proposal.get("version") or 1),
        "proposal_hash": str(proposal.get("proposal_hash") or ""),
        "action_summary": str(proposal.get("summary") or proposal.get("title") or ""),
        "risk": str(proposal.get("risk") or ""),
        "wa_id": str(pending.get("wa_id") or ""),
        "selection_action": "mutation_candidate",
        "approval_required": True,
    }
    _save_pending(state, message_id, action_pending)
    _record_function_manager_diagnostic(
        state, pending, status="mutation_handed_to_approval", reason="mutation_candidate",
    )
    _complete_pending(config, state, message_id, action_pending)
    return True
def _function_manager_finish_job(
    config: dict[str, Any],
    state: dict[str, Any],
    message_id: str,
    pending: dict[str, Any],
    plan: dict[str, Any],
    results: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> None:
    manager_guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    selection_action = str(manager_guard.get("data_selection_action") or "")
    if selection_action == "mutation_candidate" and not results:
        if _function_manager_handoff_mutation_candidate(config, state, message_id, pending, plan):
            return
    if selection_action in {"answer_without_data", "mutation_candidate"} and not results:
        direct_evidence = {
            "status": "completed",
            "summary": (
                "Nenhuma consulta de dados e necessaria para responder a mensagem."
                if selection_action == "answer_without_data"
                else "Pedido de alteracao identificado; nenhuma acao foi executada pelo WhatsApp."
            ),
            "verified_facts": [],
            "sources": [],
            "confidence": "high",
            "evidence_sufficient": True,
            "coverage_complete": True,
            "missing": [],
            "questions": [],
            "data_requests": [],
            "validations": [],
            "failures": [],
            "tool_results": [],
            "plan": plan,
        }
        compact_direct_evidence = whatsapp_tool_results.compact_evidence(
            direct_evidence, report=False,
        )
        direct_evidence = (
            compact_direct_evidence
            if isinstance(compact_direct_evidence, dict)
            else {"status": "completed", "summary": "Nenhuma coleta automatica foi executada."}
        )
        pending.update({
            "manager_evidence": direct_evidence,
            "manager_state": "completed",
            "data_selection_state": "completed",
        })
        _save_pending(state, message_id, pending)
        _record_function_manager_diagnostic(
            state, pending, status="completed_without_collection", reason=selection_action,
        )
        _function_manager_deliver_direct(config, state, message_id, pending)
        return
    required_retryable = any(
        isinstance(item, dict)
        and item.get("required") is True
        and item.get("dados_suficientes") is not True
        and item.get("retryable") is True
        for item in list(evidence.get("validations") or [])
    )
    terminal_non_retryable_evidence = bool(
        results and not required_retryable and any(
            isinstance(item, dict) and item.get("dados_suficientes") is True
            for item in list(evidence.get("validations") or [])
        )
    )
    if (evidence.get("evidence_sufficient") is True or terminal_non_retryable_evidence) and not plan.get("requires_sol") and not plan.get("requires_web"):
        _record_function_manager_diagnostic(
            state,
            pending,
            status="completed_without_sol" if evidence.get("evidence_sufficient") is True else "partial_without_sol",
            reason="internal_evidence_sufficient" if evidence.get("evidence_sufficient") is True else "non_retryable_partial_evidence",
        )
        _function_manager_deliver_direct(config, state, message_id, pending)
        return
    if plan.get("requires_sol") or plan.get("requires_web"):
        _record_function_manager_diagnostic(state, pending, status="handed_to_sol", reason="technical_or_external_analysis_required")
        if _function_manager_start_sol(config, state, message_id, pending):
            return
        _function_manager_retry(pending, "sol_creation_failed")
        _save_pending(state, message_id, pending)
        return
    retry_reason = "data_selection_tool_timeout" if required_retryable else "evidencia_interna_insuficiente"
    _function_manager_retry(pending, retry_reason)
    _record_function_manager_diagnostic(state, pending, status="waiting_retry", reason=retry_reason)
    _save_pending(state, message_id, pending)

def _function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> None:
    started = time.monotonic()
    try:
        pending = _function_manager_pending_snapshot(state, message_id)
        if not pending or str(pending.get("kind") or "") != "dual_function_manager":
            return
        run_revision = max(0, int(pending.get("manager_revision") or 0))
        _data_selection_register_plan_attempt(pending)
        pending.update({
            "manager_state": "running",
            "data_selection_state": "running",
            "job_state": "manager_running",
            "manager_started_at": _now(),
        })
        _save_pending(state, message_id, pending)
        plan, raw_plan, manager_policy, catalog, planning_duration_ms = _function_manager_build_plan(config, pending)
        if not _function_manager_revision_matches(state, message_id, run_revision):
            return
        _function_manager_apply_plan(pending, plan, raw_plan, catalog, planning_duration_ms)
        _save_pending(state, message_id, pending)
        if _function_manager_request_missing_input(config, state, message_id, pending, plan, started):
            return
        results, evidence = _function_manager_run_tools(config, pending, plan, manager_policy, started)
        if not _function_manager_revision_matches(state, message_id, run_revision):
            return
        _save_pending(state, message_id, pending)
        current = _function_manager_pending_snapshot(state, message_id)
        if not current or current.get("cancel_requested") is True:
            return
        _function_manager_finish_job(config, state, message_id, pending, plan, results, evidence)
    except Exception as exc:
        pending = _function_manager_pending_snapshot(state, message_id)
        if pending:
            error_code = _data_selection_error_code(exc)
            pending.update({
                "manager_state": "partial",
                "data_selection_state": "failed",
                "job_state": "partial",
                "manager_next_retry_at_epoch": 0,
                "next_retry_at_epoch": 0,
                "retry_policy": "selector_fail_closed",
                "terminal_reason": error_code,
                "terminal_error_class": "data_selection",
                "manager_last_error": error_code,
            })
            pending["manager_total_duration_ms"] = int(round((time.monotonic() - started) * 1000))
            _record_function_manager_diagnostic(state, pending, status="failed_closed", reason=error_code)
            _save_pending(state, message_id, pending)
            delivery = _post_proactive(
                config,
                {
                    "subject_id": str(pending.get("subject_id") or ""),
                    "fingerprint": f"manager:{pending.get('job_group_id') or message_id}:selector-unavailable",
                    "event_type": "task_partial",
                    "severity": "warning",
                    "text": (
                        "O seletor seguro de dados esta indisponivel agora. "
                        "Nenhuma fonte foi consultada; tente novamente em instantes."
                    ),
                },
            )
            pending["delivery_state"] = f"selector_unavailable_{str(delivery.get('status') or 'failed')}"
            _save_pending(state, message_id, pending)
        RUNTIME_STATE["dual_agent_last_error"] = _data_selection_error_code(exc)
    finally:
        _record_latency("manager_duration", time.monotonic() - started)

def _function_manager_future_done(message_id: str, future: concurrent.futures.Future[Any]) -> None:
    with FUNCTION_MANAGER_LOCK:
        if FUNCTION_MANAGER_FUTURES.get(message_id) is future:
            FUNCTION_MANAGER_FUTURES.pop(message_id, None)

def _submit_function_manager_job(config: dict[str, Any], state: dict[str, Any], message_id: str) -> bool:
    settings = _whatsapp_dual_agent_settings(config)
    _configure_function_manager_executor(settings["data_selection_worker_count"])
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
