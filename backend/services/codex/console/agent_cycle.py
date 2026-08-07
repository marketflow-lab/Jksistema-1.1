"""Bounded orchestration steps for one Codex agent execution."""

from __future__ import annotations

import re
import time
from typing import Any, Optional


def _initialize(
    ctx: dict[str, Any],
    task: dict[str, Any],
    initial_prompt: str,
    report_mode: bool,
    native_mcp: bool,
) -> None:
    ctx["max_cycles"] = _codex_int_env(
        "JK_CODEX_AGENT_REPORT_MAX_CYCLES" if report_mode else "JK_CODEX_AGENT_MAX_CYCLES",
        CODEX_AGENT_REPORT_MAX_CYCLES if report_mode else CODEX_AGENT_MAX_CYCLES,
        1,
        20,
    )
    ctx["max_calls"] = _codex_int_env(
        "JK_CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE",
        CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE,
        1,
        10,
    )
    ctx.update(
        current_prompt=initial_prompt,
        previous_results=[],
        api_query_deadline=None,
        trace={
            "agent_steps": [],
            "tool_calls": [],
            "tool_results_summary": [],
            "sources": [],
            "warnings": [],
            "token_usage": {},
        },
        final_state={"items": [], "live_answer": "", "reasoning_summary": "", "live_plan": ""},
        final_response="",
        mcp_seen=set(),
        started_monotonic=time.monotonic(),
        deadline_seconds=_codex_agent_deadline_seconds(task, report_mode),
        native_mcp=native_mcp,
    )
    ctx["data_selection"] = _codex_agent_data_selection_from_task(task)
    ctx["selection_enforced"] = bool(ctx["data_selection"])
    ctx["planned_calls"] = _codex_agent_planned_calls(ctx["data_selection"])
    ctx["attempted_planned_indexes"] = set()
    ctx["planned_success_by_index"] = {}
    ctx["selected_tool_ids"] = set(
        _codex_agent_data_selection_tool_ids(ctx["data_selection"])
    )
    ctx["source_policy"] = (
        {} if ctx["selection_enforced"] else _codex_agent_source_policy_for_task(task)
    )


def _deadline_reached(ctx: dict[str, Any], *, during_tools: bool = False) -> bool:
    seconds = ctx["deadline_seconds"]
    if seconds is None or time.monotonic() - ctx["started_monotonic"] < seconds:
        return False
    ctx["trace"]["deadline_exceeded"] = True
    suffix = " durante as consultas" if during_tools else ""
    ctx["trace"]["warnings"].append(
        f"Limite seguro de {seconds} segundos atingido{suffix}."
    )
    if not during_tools:
        ctx["final_response"] = (
            "A consulta atingiu o limite seguro de tempo. Vou apresentar somente os "
            "dados que consegui confirmar ate aqui, sem completar informacoes por suposicao."
        )
    return True


def _stream_cycle(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    thread: Any,
    run_kwargs: dict[str, Any],
) -> Optional[str]:
    cycle = ctx["cycle"]
    status = (
        "interpretando pergunta"
        if cycle == 1
        else f"analisando resultados do ciclo {cycle - 1}"
    )
    ctx["trace"]["agent_steps"].append(
        {"cycle": cycle, "status": status, "at": _codex_now()}
    )
    _codex_agent_update_trace(task_id, ctx["trace"], live_status=status, live_answer="")
    turn = thread.turn(ctx["current_prompt"], **run_kwargs)
    state: dict[str, Any] = {
        "items": [],
        "live_answer": "",
        "reasoning_summary": "",
        "live_plan": "",
    }
    _codex_update_live(
        task_id, live_status=status, wait_reason="codex_turn", live_answer=""
    )
    _codex_register_active_turn(task_id, turn)
    try:
        for event in turn.stream():
            _codex_process_stream_event(task_id, task, event, state)
    finally:
        _codex_unregister_active_turn(task_id, turn)
    completed_turn = state.get("completed_turn")
    if completed_turn is not None:
        status_value = str(
            getattr(
                getattr(completed_turn, "status", None),
                "value",
                getattr(completed_turn, "status", ""),
            )
            or ""
        )
        if status_value == "failed":
            error = getattr(completed_turn, "error", None)
            raise RuntimeError(
                str(getattr(error, "message", "") or "turn failed with status failed")
            )
    response = _codex_final_response_from_items(
        list(state.get("items") or []), fallback=str(state.get("live_answer") or "")
    )
    if isinstance(state.get("token_usage"), dict):
        ctx["trace"]["token_usage"] = state.get("token_usage") or {}
    ctx.update(state=state, response=response)
    if not ctx["native_mcp"]:
        return None
    legacy_calls, legacy_parse_error = _codex_agent_extract_tool_calls(response)
    if legacy_parse_error or legacy_calls:
        ctx["trace"]["warnings"].append("mcp_legacy_protocol_mix_blocked")
        ctx["final_response"] = (
            "A consulta MCP nao terminou em um protocolo valido. Nenhuma chamada pelo "
            "parser legado foi executada; tente novamente para reiniciar a consulta de forma segura."
        )
    else:
        ctx["final_response"] = response
    ctx["final_state"] = state
    return "break"


def _collect_mcp_results(ctx: dict[str, Any], task_id: str) -> None:
    results = (
        []
        if ctx["selection_enforced"]
        else _codex_native_mcp_read_results(task_id, ctx["mcp_seen"])
    )
    ctx["mcp_cycle_results"] = results
    for result in results:
        tool_id = str(result.get("tool_id") or "")
        raw_sources = list(result.get("sources_raw") or [])[:8]
        human_sources = list(
            result.get("sources_human") or result.get("sources") or []
        )[:8]
        source_label = str(
            result.get("source_label")
            or (human_sources[:1] or raw_sources[:1] or [""])[0]
            or ""
        )
        evidence = (
            result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        )
        next_sources = [
            item
            for item in list(evidence.get("next_sources") or [])[:8]
            if isinstance(item, dict)
        ]
        summary = {
            "cycle": ctx["cycle"],
            "tool_id": tool_id,
            "tool_label": result.get("tool_label") or _codex_agent_tool_status(tool_id),
            "module": result.get("module") or "",
            "records": int(result.get("records") or 0),
            "success": bool(result.get("success")),
            "sources": human_sources or raw_sources,
            "sources_raw": raw_sources,
            "sources_human": human_sources,
            "source": source_label,
            "source_label": source_label,
            "warnings": list(result.get("warnings") or [])[:6],
            "empty_reason": str(result.get("empty_reason") or result.get("error") or "")[:900],
            "evidence": evidence,
            "next_fallbacks": [
                str(item.get("label") or item.get("tool_id") or "")
                for item in next_sources
            ],
            "generated_at": result.get("generated_at") or _codex_now(),
        }
        ctx["trace"]["tool_calls"].append(
            {
                "cycle": ctx["cycle"],
                "tool_id": tool_id,
                "protocol": "mcp",
                "at": _codex_now(),
            }
        )
        ctx["trace"]["tool_results_summary"].append(summary)
        ctx["trace"]["agent_steps"].append(
            {
                "cycle": ctx["cycle"],
                "status": f"consulta MCP concluida: {_codex_agent_tool_status(tool_id)}",
                "tool_id": tool_id,
                "at": _codex_now(),
            }
        )
        _codex_agent_unique_extend(ctx["trace"]["sources"], human_sources or raw_sources)
        _codex_agent_unique_extend(ctx["trace"]["warnings"], result.get("warnings") or [])
        ctx["previous_results"].append(result)
    if results:
        _codex_agent_update_trace(
            task_id,
            ctx["trace"],
            live_status="validando resultados das consultas",
            wait_reason="data_validation",
        )


def _raise_reasoning(ctx: dict[str, Any], task_id: str, task: dict[str, Any], run_kwargs: dict[str, Any]) -> None:
    levels = ("low", "medium", "high", "xhigh")
    current = str(
        (_codex_load_task(task_id) or task).get("reasoning_level")
        or task.get("reasoning_effort")
        or "medium"
    )
    maximum = str(task.get("reasoning_max") or "xhigh")
    try:
        next_level = levels[min(levels.index(current) + 1, levels.index(maximum))]
    except ValueError:
        next_level = "high"
    run_kwargs["effort"] = _codex_reasoning_effort_enum(next_level)
    _codex_update_task(task_id, reasoning_level=next_level)


def _parse_response(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    run_kwargs: dict[str, Any],
) -> Optional[str]:
    calls, parse_error = _codex_agent_extract_tool_calls(ctx["response"])
    if parse_error:
        ctx["trace"]["warnings"].append(parse_error)
        ctx["current_prompt"] = (
            "O bloco jk_tool_calls anterior estava invalido. Reenvie somente JSON valido "
            "dentro de <jk_tool_calls>...</jk_tool_calls>, ou responda ao usuario se nao "
            f"precisar de ferramenta.\nErro: {parse_error}"
        )
        _codex_agent_update_trace(
            task_id, ctx["trace"], live_status="corrigindo chamada de ferramenta"
        )
        ctx["final_state"] = ctx["state"]
        return "continue"
    calls = _codex_agent_order_calls_by_source_policy(calls, ctx["source_policy"])
    ctx["calls"] = calls
    if calls:
        return None
    attempted = {
        str(item.get("tool_id") or "")
        for item in ctx["previous_results"]
        if isinstance(item, dict)
    }
    pending: list[str] = []
    for result in ctx["mcp_cycle_results"]:
        evidence = (
            result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        )
        if str(evidence.get("status") or "") in {"complete", "confirmed_zero"}:
            continue
        for item in evidence.get("next_sources") or []:
            tool_id = str(item.get("tool_id") or "").strip() if isinstance(item, dict) else ""
            if (
                tool_id
                and (not ctx["selection_enforced"] or tool_id in ctx["selected_tool_ids"])
                and tool_id not in attempted
                and tool_id not in pending
            ):
                pending.append(tool_id)
    if pending and ctx["cycle"] < ctx["max_cycles"]:
        if str(task.get("reasoning_policy") or "") == "adaptive":
            _raise_reasoning(ctx, task_id, task, run_kwargs)
        ctx["current_prompt"] = (
            "Os dados ainda nao sao suficientes. Continue pelas proximas fontes autorizadas, "
            "sem repetir chamadas: "
            + ", ".join(pending[:8])
            + ". Se nenhuma estiver disponivel, responda apenas com o que foi confirmado e declare a lacuna."
        )
        ctx["final_state"] = ctx["state"]
        return "continue"
    ctx["final_response"] = (
        _codex_exact_ml_order_response(task, ctx["previous_results"], ctx["response"])
        or _codex_whatsapp_complete_ml_report(task, ctx["previous_results"])
        or _codex_whatsapp_bling_stock_response(task, ctx["previous_results"])
        or ctx["response"]
    )
    ctx["final_state"] = ctx["state"]
    return "break"


def _prepare_tool_cycle(ctx: dict[str, Any], task: dict[str, Any]) -> None:
    ctx["cycle_results"] = []
    read_only = bool(
        task.get("external_safe_mode") or _codex_task_whatsapp_query_only(task)
    )
    allowed = (
        {
            str(item.get("id") or "")
            for item in _codex_agent_tool_catalog(
                task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
                read_only_only=True,
            )
        }
        if read_only
        else set()
    )
    if ctx["selection_enforced"]:
        allowed &= ctx["selected_tool_ids"]
    ctx.update(read_only_channel_mode=read_only, external_allowed_tools=allowed)


def _authorize_call(ctx: dict[str, Any], task: dict[str, Any]) -> None:
    call = ctx["call"]
    tool_id = str(call.get("tool_id") or "").strip()
    args = dict(call.get("args") or {}) if isinstance(call.get("args"), dict) else {}
    planned_call = None
    selection_error_code = ""
    selection_error = ""
    if ctx["selection_enforced"]:
        planned_call, selection_error_code, selection_error = (
            _codex_agent_authorize_planned_call(
                tool_id,
                args,
                ctx["planned_calls"],
                ctx["attempted_planned_indexes"],
                ctx["planned_success_by_index"],
            )
        )
        if planned_call is not None:
            args = dict(planned_call.get("arguments") or {})
        complete_report = False
    else:
        if tool_id in set(ctx["source_policy"].get("required_tools") or []):
            args["force_refresh"] = bool(ctx["source_policy"].get("force_refresh", True))
            if (
                tool_id == "mercado_livre_listing"
                and ctx["source_policy"].get("include_listing_details") is True
            ):
                args["incluir_detalhes"] = True
        args, complete_report = _codex_whatsapp_prepare_agent_tool_call(
            task,
            tool_id,
            args,
            ctx["previous_results"] + ctx["cycle_results"],
        )
    ctx.update(
        tool_id=tool_id,
        args=args,
        planned_call=planned_call,
        selection_error_code=selection_error_code,
        selection_error=selection_error,
        call_complete_ml_report=complete_report,
    )


def _execute_call(
    ctx: dict[str, Any],
    task: dict[str, Any],
    screen_context: Any,
) -> None:
    from backend.services.codex.assistant import execution as assistant_execution

    tool_id = ctx["tool_id"]
    if tool_id in {
        "bling_sales_orders",
        "mercado_livre_orders",
        "mercado_livre_returns",
        "mercado_livre_listing",
    }:
        if ctx["call_complete_ml_report"] and tool_id == "mercado_livre_orders":
            ctx["api_query_deadline"] = time.monotonic() + 300
        elif ctx["api_query_deadline"] is None:
            request_text = _codex_texto_sem_acentos(
                _codex_whatsapp_user_request_text(task)
            )
            latest_direct = bool(
                tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
                and re.search(
                    r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b",
                    request_text,
                )
            )
            ctx["api_query_deadline"] = time.monotonic() + (25 if latest_direct else 60)
    planned = ctx["planned_call"]
    if ctx["selection_enforced"] and planned is None:
        result = {
            "success": False,
            "tool_id": tool_id,
            "error": ctx["selection_error"]
            or "Chamada fora do plano de dados validado para este turno.",
            "error_code": ctx["selection_error_code"] or "data_selection_tool_blocked",
            "records": 0,
            "warnings": [
                ctx["selection_error"] or "A chamada nao foi autorizada pelo backend."
            ],
            "generated_at": _codex_now(),
        }
    elif ctx["read_only_channel_mode"] and tool_id not in ctx["external_allowed_tools"]:
        result = {
            "success": False,
            "tool_id": tool_id,
            "error": "Ferramenta bloqueada para tarefa originada fora do aplicativo.",
            "records": 0,
            "warnings": ["Somente consultas read-only sao permitidas no canal WhatsApp."],
            "generated_at": _codex_now(),
        }
    else:
        policy_error = _codex_agent_source_policy_error(
            tool_id, ctx["source_policy"], ctx["previous_results"] + ctx["cycle_results"]
        )
        if policy_error:
            result = {
                "success": False,
                "tool_id": tool_id,
                "error": policy_error,
                "records": 0,
                "warnings": [policy_error],
                "generated_at": _codex_now(),
            }
        else:
            planned_index = int(planned.get("index") or 0) if planned is not None else None
            if planned_index is not None:
                ctx["attempted_planned_indexes"].add(planned_index)
            result = assistant_execution.execute_tool_call(
                client_id=str(task.get("client_id") or ""),
                tool_id=tool_id,
                args=ctx["args"],
                screen_context={
                    key: screen_context.get(key)
                    for key in ("title", "pathname", "modulo_atual")
                    if isinstance(screen_context, dict)
                    and screen_context.get(key) not in (None, "", [], {})
                },
                previous_results=ctx["previous_results"] + ctx["cycle_results"],
                permissions=task.get("permissions")
                if isinstance(task.get("permissions"), dict)
                else {},
                audit_user=str(task.get("username") or task.get("created_by") or ""),
                query_deadline=ctx["api_query_deadline"],
            )
    ctx["result"] = result


def _summarize_result(ctx: dict[str, Any]) -> dict[str, Any]:
    result = ctx["result"]
    raw_sources = list(result.get("sources_raw") or [])[:8]
    human_sources = list(result.get("sources_human") or result.get("sources") or [])[:8]
    source_label = str(
        result.get("source_label")
        or (human_sources[:1] or raw_sources[:1] or [""])[0]
        or ""
    )
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    next_entries = [
        item
        for item in list(evidence.get("next_sources") or [])[:8]
        if isinstance(item, dict)
    ]
    next_raw = [str(item.get("tool_id") or "") for item in next_entries]
    next_human = [
        str(item.get("label") or item.get("tool_id") or "") for item in next_entries
    ]
    paging: dict[str, Any] = {}
    for item in result.get("summary") if isinstance(result.get("summary"), list) else []:
        if not isinstance(item, dict):
            continue
        payload = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        current = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        if current:
            paging = dict(current)
            break
    return {
        "cycle": ctx["cycle"],
        "tool_id": result.get("tool_id") or ctx["tool_id"],
        "tool_label": result.get("tool_label") or ctx["label"],
        "module": result.get("module") or "",
        "records": int(result.get("records") or 0),
        "success": bool(result.get("success")),
        "sources": human_sources or raw_sources,
        "sources_raw": raw_sources,
        "sources_human": human_sources,
        "source": source_label,
        "source_label": source_label,
        "warnings": list(result.get("warnings") or [])[:6],
        "empty_reason": str(result.get("empty_reason") or result.get("error") or "")[:900],
        "evidence": evidence,
        "next_fallbacks": next_human or next_raw,
        "next_fallbacks_raw": next_raw,
        "next_fallbacks_human": next_human,
        "failures": [
            item
            for item in [str(result.get("empty_reason") or result.get("error") or "")[:600]]
            + [str(value or "")[:300] for value in list(result.get("warnings") or [])[:4]]
            if str(item or "").strip()
        ],
        "paging": paging,
        "generated_at": result.get("generated_at") or _codex_now(),
    }


def _record_result(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    result = ctx["result"]
    ctx["trace"]["tool_results_summary"].append(summary)
    _codex_capture_whatsapp_listing_bundle(task_id, task, result)
    _codex_agent_unique_extend(ctx["trace"]["sources"], summary["sources"])
    _codex_agent_unique_extend(ctx["trace"]["warnings"], result.get("warnings") or [])
    if result.get("empty_reason"):
        _codex_agent_unique_extend(
            ctx["trace"]["warnings"], [str(result.get("empty_reason"))]
        )
    ctx["cycle_results"].append(result)
    ctx["previous_results"].append(result)
    evidence = summary.get("evidence") if isinstance(summary.get("evidence"), dict) else {}
    if not evidence:
        _codex_agent_update_trace(
            task_id, ctx["trace"], live_status=f"{ctx['label']} concluido"
        )
        return
    confidence = str(evidence.get("confidence") or "").strip()
    evidence_status = str(evidence.get("status") or "")
    records = int(summary.get("records") or 0)
    status = (
        f"validando evidencia: {evidence_status or 'sem classificacao'} | "
        f"{records} registros | confianca {confidence or '-'}"
    )
    ctx["trace"]["agent_steps"].append(
        {
            "cycle": ctx["cycle"],
            "status": status,
            "tool_id": ctx["tool_id"],
            "next_fallbacks": summary["next_fallbacks_human"],
            "at": _codex_now(),
        }
    )
    _codex_agent_update_trace(task_id, ctx["trace"], live_status=status)


def _run_tools(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    screen_context: Any,
) -> None:
    for call in ctx["calls"][: ctx["max_calls"]]:
        if _deadline_reached(ctx, during_tools=True):
            break
        ctx["call"] = call
        _authorize_call(ctx, task)
        ctx["label"] = _codex_agent_tool_status(ctx["tool_id"])
        planned = ctx["planned_call"]
        ctx["trace"]["tool_calls"].append(
            {
                "cycle": ctx["cycle"],
                "tool_id": ctx["tool_id"],
                "args": ctx["args"],
                "planned_index": int(planned.get("index") or 0)
                if planned is not None
                else None,
                "reason": call.get("reason") or "",
                "at": _codex_now(),
            }
        )
        ctx["trace"]["agent_steps"].append(
            {
                "cycle": ctx["cycle"],
                "status": f"solicitando ferramenta: {ctx['label']}",
                "tool_id": ctx["tool_id"],
                "at": _codex_now(),
            }
        )
        _codex_agent_update_trace(
            task_id,
            ctx["trace"],
            live_status=f"solicitando ferramenta: {ctx['label']}",
            live_answer="",
            wait_reason="api_query",
        )
        try:
            _execute_call(ctx, task, screen_context)
        except Exception as exc:
            ctx["result"] = {
                "success": False,
                "tool_id": ctx["tool_id"],
                "error": str(exc)[:600],
                "records": 0,
                "warnings": [str(exc)[:600]],
                "generated_at": _codex_now(),
            }
        if planned is not None:
            planned_index = int(planned.get("index") or 0)
            if planned_index in ctx["attempted_planned_indexes"]:
                ctx["planned_success_by_index"][planned_index] = (
                    ctx["result"].get("success") is True
                )
        _record_result(ctx, task_id, task, _summarize_result(ctx))


def _finish_cycle(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    run_kwargs: dict[str, Any],
) -> Optional[str]:
    if ctx["trace"].get("deadline_exceeded"):
        confirmed = [
            item
            for item in ctx["trace"].get("tool_results_summary") or []
            if item.get("success")
        ]
        ctx["final_response"] = (
            "A consulta atingiu o limite seguro de tempo. Foram preservados "
            f"{len(confirmed)} resultado(s) confirmado(s), mas ainda faltam dados "
            "para uma conclusao completa."
        )
        ctx["final_state"] = ctx["state"]
        return "break"
    if str(task.get("reasoning_policy") or "") == "adaptive":
        needs_more = any(
            isinstance(item.get("evidence"), dict)
            and str(item.get("evidence", {}).get("status") or "")
            not in {"complete", "confirmed_zero"}
            for item in ctx["trace"].get("tool_results_summary") or []
            if isinstance(item, dict)
        )
        if needs_more:
            levels = ("low", "medium", "high", "xhigh")
            current = str(
                (_codex_load_task(task_id) or task).get("reasoning_level")
                or task.get("reasoning_effort")
                or "medium"
            )
            maximum = str(task.get("reasoning_max") or "xhigh")
            try:
                next_index = min(levels.index(current) + 1, levels.index(maximum))
            except ValueError:
                next_index = levels.index("high")
            next_level = levels[next_index]
            if next_level != current:
                run_kwargs["effort"] = _codex_reasoning_effort_enum(next_level)
                _codex_update_task(
                    task_id,
                    reasoning_level=next_level,
                    live_status=(
                        "Aprofundando a analise para validar dados ainda insuficientes "
                        f"({next_level})."
                    ),
                    wait_reason="data_validation",
                )
    ctx["current_prompt"] = _codex_agent_results_prompt(
        ctx["cycle"], ctx["cycle_results"]
    )
    ctx["final_state"] = ctx["state"]
    ctx["trace"]["agent_steps"].append(
        {"cycle": ctx["cycle"], "status": "gerando proximo passo", "at": _codex_now()}
    )
    _codex_agent_update_trace(
        task_id, ctx["trace"], live_status="gerando proximo passo", live_answer=""
    )
    return None


def _finalize(
    ctx: dict[str, Any],
    task_id: str,
    task: dict[str, Any],
    report_mode: bool,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if not ctx["final_response"]:
        ctx["final_response"] = "Nao consegui gerar uma resposta final nesta execucao do agente."
    final_usage = ctx["final_state"].get("token_usage")
    ctx["trace"]["token_usage"] = (
        final_usage
        if isinstance(final_usage, dict)
        else ctx["trace"].get("token_usage") or {}
    )
    _codex_generate_whatsapp_chart_artifacts(task_id, task, ctx["previous_results"])
    try:
        from backend.services.codex_data_selection_agent import DATA_SELECTION_RUNTIME

        DATA_SELECTION_RUNTIME.record_evidence_size(
            ctx["previous_results"], report=report_mode
        )
    except Exception:
        pass
    return ctx["final_response"], ctx["final_state"], ctx["trace"]


def _codex_agent_run_loop(
    task_id: str,
    task: dict[str, Any],
    thread: Any,
    run_kwargs: dict[str, Any],
    initial_prompt: str,
    screen_context: Any,
    report_mode: bool,
    *,
    native_mcp: bool = False,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    ctx: dict[str, Any] = {}
    _initialize(ctx, task, initial_prompt, report_mode, native_mcp)
    for cycle in range(1, ctx["max_cycles"] + 1):
        ctx["cycle"] = cycle
        if _deadline_reached(ctx):
            break
        if _stream_cycle(ctx, task_id, task, thread, run_kwargs) == "break":
            break
        _collect_mcp_results(ctx, task_id)
        action = _parse_response(ctx, task_id, task, run_kwargs)
        if action == "continue":
            continue
        if action == "break":
            break
        if cycle >= ctx["max_cycles"]:
            ctx["final_response"] = (
                _codex_exact_ml_order_response(task, ctx["previous_results"])
                or "Nao consegui concluir a resposta dentro do limite de ciclos do agente. "
                "Ferramentas solicitadas: "
                + ", ".join(str(call.get("tool_id") or "") for call in ctx["calls"])
            )
            ctx["final_state"] = ctx["state"]
            ctx["trace"]["warnings"].append(
                "Limite de ciclos do agente atingido antes da resposta final."
            )
            break
        _prepare_tool_cycle(ctx, task)
        _run_tools(ctx, task_id, task, screen_context)
        if _finish_cycle(ctx, task_id, task, run_kwargs) == "break":
            break
    return _finalize(ctx, task_id, task, report_mode)


__codex_dependencies__ = [
    "CODEX_AGENT_MAX_CYCLES",
    "CODEX_AGENT_MAX_TOOL_CALLS_PER_CYCLE",
    "CODEX_AGENT_REPORT_MAX_CYCLES",
    "_codex_agent_authorize_planned_call",
    "_codex_agent_data_selection_from_task",
    "_codex_agent_data_selection_tool_ids",
    "_codex_agent_deadline_seconds",
    "_codex_agent_extract_tool_calls",
    "_codex_agent_order_calls_by_source_policy",
    "_codex_agent_planned_calls",
    "_codex_agent_results_prompt",
    "_codex_agent_source_policy_error",
    "_codex_agent_source_policy_for_task",
    "_codex_agent_tool_catalog",
    "_codex_agent_tool_status",
    "_codex_agent_unique_extend",
    "_codex_agent_update_trace",
    "_codex_capture_whatsapp_listing_bundle",
    "_codex_exact_ml_order_response",
    "_codex_final_response_from_items",
    "_codex_generate_whatsapp_chart_artifacts",
    "_codex_int_env",
    "_codex_load_task",
    "_codex_native_mcp_read_results",
    "_codex_now",
    "_codex_process_stream_event",
    "_codex_reasoning_effort_enum",
    "_codex_register_active_turn",
    "_codex_task_whatsapp_query_only",
    "_codex_texto_sem_acentos",
    "_codex_unregister_active_turn",
    "_codex_update_live",
    "_codex_update_task",
    "_codex_whatsapp_bling_stock_response",
    "_codex_whatsapp_complete_ml_report",
    "_codex_whatsapp_prepare_agent_tool_call",
    "_codex_whatsapp_user_request_text",
]

__codex_exports__ = ["_codex_agent_run_loop"]
