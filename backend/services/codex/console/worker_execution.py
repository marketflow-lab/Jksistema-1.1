"""Worker lifecycle for Codex Console tasks."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from backend.services import codex_ai_telemetry, codex_mcp_rollout, codex_operational_memory
from .worker_setup import (
    _build_instructions,
    _configure_identity,
    _enforce_read_only,
    _extra_instructions,
    _prepare_prompt,
    _select_data,
    _start,
)


def _open_thread(ctx: dict[str, Any], task_id: str, codex: Any) -> Any:
    task = ctx["task"]
    kwargs = {
        "cwd": ctx["cwd"],
        "model": ctx["model"],
        "sandbox": ctx["sandbox_enum"],
        "approval_mode": ctx["approval_mode"],
        "developer_instructions": ctx["developer_instructions"],
    }
    if ctx["native_mcp"]:
        try:
            kwargs["config"] = _codex_native_mcp_thread_config(
                _codex_load_task(task_id) or task, ctx["screen_context"]
            )
            _codex_update_task(
                task_id,
                tool_protocol="mcp_v2",
                mcp_migration={
                    "target": "jk_system_mcp",
                    "native_enabled": True,
                    "native_active": True,
                    "legacy_parser_fallback": False,
                    "fallback_boundary": "before_first_external_call_only",
                },
            )
        except Exception as exc:
            ctx["native_mcp"] = False
            _codex_log(
                task,
                f"Plano MCP indisponivel antes da primeira chamada: {exc}",
                "warning",
            )
            _codex_update_task(
                task_id,
                tool_protocol="typed_catalog_text_v1",
                mcp_migration={
                    "target": "jk_system_mcp",
                    "native_enabled": True,
                    "native_active": False,
                    "fallback_used": True,
                    "fallback_boundary": "before_first_external_call",
                    "legacy_parser_fallback": True,
                },
            )
    kwargs.pop("sandbox", None)
    if ctx["service_tier"]:
        kwargs["service_tier"] = ctx["service_tier"]
    try:
        if ctx["thread_id"]:
            try:
                resume = {key: value for key, value in kwargs.items() if key != "developer_instructions"}
                thread = codex.thread_resume(ctx["thread_id"], **resume)
                if ctx["agent_mode"]:
                    ctx["run_prompt"] = _codex_agent_delta_prompt(
                        ctx["prompt"],
                        ctx["screen_context"],
                        task.get("permissions")
                        if isinstance(task.get("permissions"), dict)
                        else {},
                        read_only_only=ctx["read_only_channel_mode"],
                        native_mcp=ctx["native_mcp"],
                        server_data_selection=ctx["data_selection"],
                    )
                    _codex_update_task(
                        task_id,
                        thread_reused=True,
                        context_stats=_codex_context_stats_from_prompt(
                            ctx["prompt"],
                            ctx["run_prompt"],
                            ctx["screen_context"],
                            {"enabled": True, "tool_results_count": 0},
                            {},
                        ),
                    )
                else:
                    ctx["run_prompt"] = (
                        "Turno incremental da thread ja inicializada. Preserve as instrucoes anteriores.\n\n"
                        + _codex_agent_json(
                            {
                                "question": ctx["prompt"],
                                "screen": _codex_agent_screen_summary(ctx["screen_context"]),
                                "app_data_context": ctx["app_data_context"],
                            },
                            CODEX_AGENT_TOOL_RESULT_LIMIT,
                        )
                    )
                return thread
            except Exception as exc:
                _codex_log(
                    task,
                    f"Thread tecnica anterior indisponivel; contexto logico preservado: {exc}",
                    "warning",
                )
                _codex_save_conversation_state(task, latest_thread_id="")
                _codex_update_task(task_id, thread_id="")
                ctx.update(thread_id="", run_prompt=ctx["initial_run_prompt"])
        return codex.thread_start(**kwargs)
    except Exception as exc:
        if not ctx["native_mcp"]:
            raise
        _codex_log(
            task,
            f"MCP local indisponivel; ativando parser tipado de rollback: {exc}",
            "warning",
        )
        kwargs.pop("config", None)
        ctx.update(native_mcp=False, run_prompt=ctx["initial_run_prompt"])
        _codex_update_task(
            task_id,
            tool_protocol="typed_catalog_text_v1",
            mcp_migration={
                "target": "jk_system_mcp",
                "native_enabled": True,
                "native_active": False,
                "fallback_used": True,
                "fallback_error_code": "MCP_RUNTIME_UNAVAILABLE",
                "legacy_parser_fallback": True,
            },
        )
        return codex.thread_start(**kwargs)


def _run_provider(ctx: dict[str, Any], task_id: str) -> None:
    from openai_codex import Codex, CodexConfig
    from openai_codex.generated.v2_all import ReasoningSummary

    if str((_codex_load_task(task_id) or {}).get("status") or "") == "cancel_requested":
        _codex_update_task(
            task_id,
            status="canceled",
            completed_at=_codex_now(),
            live_status="Tarefa cancelada.",
            wait_reason="",
            active_turn_id="",
            can_steer=False,
        )
        ctx["cancelled"] = True
        return
    provider_started = time.perf_counter()
    if ctx["telemetry"] is not None:
        ctx["telemetry"].start_span(
            ctx["tenant"],
            trace_id=ctx["trace_id"],
            span_id=f"{ctx['trace_id']}:provider",
            stage="provider",
        )
    runtime_bin = _codex_runtime_require_ready()
    config = CodexConfig(
        codex_bin=runtime_bin,
        env=_codex_sdk_env(),
        cwd=ctx["cwd"],
        config_overrides=ctx["config_overrides"],
    )
    with Codex(config) as codex:
        thread = _open_thread(ctx, task_id, codex)
        run_kwargs = {
            "cwd": ctx["cwd"],
            "sandbox": ctx["sandbox_enum"],
            "model": ctx["model"],
            "approval_mode": ctx["approval_mode"],
            "effort": ctx["reasoning_effort"],
            "summary": ReasoningSummary.model_validate("auto"),
        }
        run_kwargs.pop("sandbox", None)
        if ctx["service_tier"]:
            run_kwargs["service_tier"] = ctx["service_tier"]
        if ctx["agent_mode"]:
            final_response, state, agent_trace = _codex_agent_run_loop(
                task_id,
                ctx["task"],
                thread,
                run_kwargs,
                ctx["run_prompt"],
                ctx["screen_context"],
                _codex_agent_is_report_request(ctx["prompt"]),
                native_mcp=ctx["native_mcp"],
            )
        else:
            turn = thread.turn(ctx["run_prompt"], **run_kwargs)
            state = {
                "items": [],
                "live_answer": "",
                "reasoning_summary": "",
                "live_plan": "",
            }
            agent_trace = {}
            _codex_update_live(
                task_id,
                live_status="Codex iniciou o processamento.",
                wait_reason="codex_turn",
            )
            _codex_register_active_turn(task_id, turn)
            try:
                for event in turn.stream():
                    _codex_process_stream_event(task_id, ctx["task"], event, state)
            finally:
                _codex_unregister_active_turn(task_id, turn)
            final_response = ""
    ctx.update(
        thread=thread,
        state=state,
        agent_trace=agent_trace,
        final_response=final_response,
        provider_started=provider_started,
    )


def _complete_provider(ctx: dict[str, Any], task_id: str) -> None:
    shadow = _codex_mcp_shadow_finish(ctx["shadow_probe"], ctx["agent_trace"])
    if shadow:
        migration = (_codex_load_task(task_id) or {}).get("mcp_migration")
        _codex_update_task(
            task_id,
            mcp_migration={
                **(migration if isinstance(migration, dict) else {}),
                **shadow,
            },
        )
    if ctx["telemetry"] is not None:
        ctx["telemetry"].finish_span(
            ctx["tenant"],
            trace_id=ctx["trace_id"],
            span_id=f"{ctx['trace_id']}:provider",
            stage="provider",
            status="completed",
            duration_ms=(time.perf_counter() - ctx["provider_started"]) * 1000,
        )
    state = ctx["state"]
    if not ctx["agent_mode"]:
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
                    str(
                        getattr(error, "message", "")
                        or "turn failed with status failed"
                    )
                )
        ctx["final_response"] = _codex_final_response_from_items(
            list(state.get("items") or []),
            fallback=str(state.get("live_answer") or ""),
        )
    result_thread_id = str(
        getattr(ctx["thread"], "id", "") or ctx["thread_id"] or ""
    ).strip()
    if not result_thread_id:
        try:
            read = ctx["thread"].read()
            result_thread_id = str(
                getattr(read, "id", "") or getattr(read, "thread_id", "") or ""
            ).strip()
        except Exception:
            result_thread_id = ctx["thread_id"]
    ctx["result_thread_id"] = result_thread_id


def _validate_scope(ctx: dict[str, Any], task_id: str) -> bool:
    task = ctx["task"]
    current = _codex_load_task(task_id) or {}
    if str(current.get("status") or "") == "cancel_requested":
        _codex_update_task(
            task_id,
            status="canceled",
            completed_at=_codex_now(),
            live_status="Tarefa cancelada.",
            wait_reason="",
            active_turn_id="",
            can_steer=False,
            error=str(current.get("error") or "")[:1000],
        )
        return False
    scope_changed: list[str] = []
    violations: list[str] = []
    if ctx["sandbox"] != "read_only" and ctx["workspace_before"]:
        after = _codex_workspace_snapshot(ctx["cwd"])
        scope_changed = _codex_workspace_changed_files(ctx["workspace_before"], after)
        violations = _codex_scope_violations(ctx["scope"], scope_changed)
        if violations:
            preview = "\n".join(f"- {item}" for item in violations[:30])
            message = (
                "Bloqueio de escopo: a tarefa alterou arquivo(s) fora do modulo permitido.\n"
                + preview
                + ("\n- ..." if len(violations) > 30 else "")
                + "\n\nNenhuma nova tarefa deve ser aprovada com esse resultado. Revise as "
                "alteracoes fora do escopo ou rode novamente ampliando explicitamente o escopo."
            )
            _codex_log(task, message, "scope")
            _codex_update_task(
                task_id,
                status="failed",
                completed_at=_codex_now(),
                final_response=message,
                thread_id=ctx["result_thread_id"],
                conversation_id=_codex_task_conversation_id(task),
                live_status="Codex bloqueado por escopo.",
                error=message,
                scope=ctx["scope"],
                scope_changed_files=scope_changed,
                scope_violations=violations,
                warnings=(
                    list(task.get("warnings") or [])
                    + ["Alteracoes fora do escopo detectadas."]
                )[-80:],
            )
            _codex_transition_task_plan(
                task_id,
                "falhou",
                current_step="verificar",
                step_status="failed",
                verification={
                    "status": "failed",
                    "confirmed": False,
                    "reason": "scope_violation",
                },
            )
            return False
    ctx.update(scope_changed_files=scope_changed, scope_violations=violations)
    return True


def _verification(ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    verification = {
        "status": "confirmed",
        "confirmed": True,
        "method": "evidence_envelope" if ctx["agent_mode"] else "codex_result",
        "verified_at": _codex_now(),
    }
    trace = ctx["agent_trace"]
    if isinstance(trace, dict) and trace.get("tool_results_summary"):
        last = list(trace.get("tool_results_summary") or [])[-1]
        evidence = (
            last.get("evidence")
            if isinstance(last, dict) and isinstance(last.get("evidence"), dict)
            else {}
        )
        if str(evidence.get("status") or "") not in {"complete", "confirmed_zero"}:
            verification.update(
                {
                    "status": "partial",
                    "confirmed": False,
                    "reason": str(
                        evidence.get("reason") or "insufficient_evidence"
                    )[:1000],
                }
            )
    terminal = (
        "partial"
        if isinstance(trace, dict) and trace.get("deadline_exceeded")
        else "completed"
    )
    if terminal == "partial":
        verification.update(
            {"status": "partial", "confirmed": False, "reason": "deadline_exceeded"}
        )
    return terminal, verification


def _persist_result(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    _codex_transition_task_plan(
        task_id, "validando", current_step="consultar", step_status="completed"
    )
    _codex_transition_task_plan(
        task_id, "validando", current_step="validar", step_status="in_progress"
    )
    terminal, verification = _verification(ctx)
    final_response = ctx["final_response"]
    trace = ctx["agent_trace"]
    state = ctx["state"]
    _codex_update_task(
        task_id,
        status=terminal,
        completed_at=_codex_now(),
        final_response=final_response or "Codex concluiu sem resposta final.",
        thread_id=ctx["result_thread_id"],
        conversation_id=_codex_task_conversation_id(task),
        live_status="Codex concluiu.",
        wait_reason="",
        active_turn_id="",
        can_steer=False,
        live_answer=str(state.get("live_answer") or "")[-8000:],
        reasoning_summary=str(state.get("reasoning_summary") or "")[-8000:],
        live_plan=str(state.get("live_plan") or "")[-6000:],
        token_usage=state.get("token_usage") if isinstance(state.get("token_usage"), dict) else {},
        agent_mode=ctx["agent_mode"],
        agent_steps=list(trace.get("agent_steps") or []) if isinstance(trace, dict) else [],
        tool_calls=list(trace.get("tool_calls") or []) if isinstance(trace, dict) else [],
        tool_results_summary=list(trace.get("tool_results_summary") or []) if isinstance(trace, dict) else [],
        sources=list(trace.get("sources") or []) if isinstance(trace, dict) else [],
        warnings=list(trace.get("warnings") or []) if isinstance(trace, dict) else [],
        scope=ctx["scope"],
        scope_changed_files=ctx["scope_changed_files"],
        scope_violations=ctx["scope_violations"],
        verification=verification,
        error="",
    )
    _codex_transition_task_plan(
        task_id,
        "concluido" if verification.get("confirmed") else "parcial",
        current_step="responder",
        step_status="completed",
        verification=verification,
    )
    ctx.update(final_response=final_response, verification=verification)


def _remember(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    if ctx["result_thread_id"] and ctx["is_full_task"]:
        _codex_save_conversation_state(
            task,
            latest_thread_id=ctx["result_thread_id"],
            thread_prompt_fingerprint=str(task.get("thread_prompt_fingerprint") or ""),
            thread_schema_fingerprint=str(task.get("thread_schema_fingerprint") or ""),
            thread_scope_fingerprint=str(task.get("thread_scope_fingerprint") or ""),
            thread_conversation_key=str(task.get("thread_conversation_key") or ""),
            thread_restart_reason="",
        )
    permissions = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
    if permissions.get("full") is True:
        try:
            result = codex_operational_memory.remember_from_interaction(
                client_id=ctx["tenant"],
                prompt=str(ctx["prompt"] or ""),
                final_answer=ctx["final_response"] or "",
                trace=ctx["agent_trace"] if isinstance(ctx["agent_trace"], dict) else {},
                task={**task, "task_id": task_id},
            )
            if result.get("added_count"):
                _codex_update_task(task_id, operational_memory=result)
        except Exception as exc:
            _codex_update_task(
                task_id,
                warnings=(
                    list(task.get("warnings") or [])
                    + [f"Falha ao atualizar memoria operacional: {exc}"]
                )[-80:],
            )
    if ctx["agent_mode"]:
        memory = _codex_update_conversation_memory(task_id)
        if memory:
            _codex_update_task(
                task_id,
                conversation_summary={
                    "conversation_id": memory.get("conversation_id")
                    or _codex_task_conversation_id(task),
                    "summary_chars": len(str(memory.get("summary") or "")),
                    "recent_messages": len(memory.get("recent_messages") or []),
                    "compacted": bool(memory.get("summary")),
                },
                conversation_compaction={
                    "compacted_until": memory.get("compacted_until") or "",
                    "summary_updated_at": memory.get("summary_updated_at") or "",
                    "estimated_tokens_before": memory.get("estimated_tokens_before") or 0,
                    "estimated_tokens_after": memory.get("estimated_tokens_after") or 0,
                },
            )


def _handle_error(ctx: dict[str, Any], task_id: str, exc: Exception) -> None:
    task = ctx.get("task") or {}
    current = _codex_load_task(task_id) or {}
    if str(current.get("status") or "") == "cancel_requested":
        _codex_update_task(
            task_id,
            status="canceled",
            completed_at=_codex_now(),
            live_status="Tarefa cancelada.",
            wait_reason="",
            active_turn_id="",
            can_steer=False,
            error=str(current.get("error") or "")[:1000],
        )
        return
    if (
        ctx.get("thread_id")
        and not bool(task.get("thread_resume_retried"))
        and _codex_thread_resume_failure(exc)
    ):
        _codex_save_conversation_state(task, latest_thread_id="")
        _codex_update_task(
            task_id,
            status="queued",
            started_at="",
            completed_at="",
            thread_id="",
            thread_reused=False,
            thread_reset_reason="resume_failed",
            thread_resume_retried=True,
            live_status="Renovando a thread tecnica sem perder o contexto.",
            error="",
        )
        return
    retry_count = int(task.get("runtime_retry_count") or 0)
    if (
        str(task.get("origin") or "") == "whatsapp"
        and not bool(task.get("mutable_intent"))
        and retry_count < 3
        and _codex_transient_runtime_failure(exc)
    ):
        delay = (2, 5, 10)[retry_count]
        _codex_update_task(
            task_id,
            status="queued",
            started_at="",
            completed_at="",
            runtime_retry_count=retry_count + 1,
            runtime_retry_after_seconds=delay,
            live_status=(
                f"Codex indisponivel; nova tentativa {retry_count + 1}/3 "
                f"em {delay} segundos."
            ),
            wait_reason="retry_backoff",
            error="",
        )
        time.sleep(delay)
        return
    _codex_update_task(
        task_id,
        status="failed",
        completed_at=_codex_now(),
        live_status="Codex falhou.",
        error=str(exc),
    )
    _codex_transition_task_plan(
        task_id,
        "falhou",
        current_step=str(task.get("current_step") or "consultar"),
        step_status="failed",
        verification={
            "status": "failed",
            "confirmed": False,
            "error": str(exc)[:1000],
        },
    )


def _codex_run_worker(task_id: str) -> None:
    ctx: dict[str, Any] = {"acquired_full_lock": False, "thread_id": ""}
    try:
        if not _start(ctx, task_id):
            return
        _configure_identity(ctx, task_id)
        _enforce_read_only(ctx, task_id)
        _select_data(ctx, task_id)
        _prepare_prompt(ctx, task_id)
        _build_instructions(ctx)
        _run_provider(ctx, task_id)
        if ctx.get("cancelled"):
            return
        _complete_provider(ctx, task_id)
        if not _validate_scope(ctx, task_id):
            return
        _persist_result(ctx, task_id)
        _remember(ctx, task_id)
    except Exception as exc:
        _handle_error(ctx, task_id, exc)
    finally:
        _codex_native_mcp_cleanup(task_id)
        if ctx.get("acquired_full_lock"):
            CODEX_FULL_ACCESS_LOCK.release()


__codex_dependencies__ = [
    "CODEX_AGENT_TOOL_RESULT_LIMIT",
    "CODEX_FULL_ACCESS_LOCK",
    "_CODEX_AGENT_DATA_SELECTION_TRUST_MARKER",
    "_codex_agent_data_selection_tool_ids",
    "_codex_agent_delta_prompt",
    "_codex_agent_initial_prompt",
    "_codex_agent_is_report_request",
    "_codex_agent_json",
    "_codex_agent_materialize_task_data_selection",
    "_codex_agent_mode_enabled",
    "_codex_agent_run_loop",
    "_codex_agent_screen_summary",
    "_codex_agent_tool_catalog",
    "_codex_ai_telemetry_instance",
    "_codex_app_data_context",
    "_codex_apply_sdk_protocol_compat",
    "_codex_approval_mode_enum",
    "_codex_base_info_dir",
    "_codex_build_scope",
    "_codex_clean_text",
    "_codex_context_stats",
    "_codex_context_stats_from_prompt",
    "_codex_conversation_id",
    "_codex_conversation_state_for_task",
    "_codex_dual_worker_web_search_enabled",
    "_codex_external_readonly_config_overrides",
    "_codex_final_response_from_items",
    "_codex_load_task",
    "_codex_log",
    "_codex_mcp_shadow_finish",
    "_codex_mcp_shadow_prepare",
    "_codex_native_mcp_cleanup",
    "_codex_native_mcp_enabled",
    "_codex_native_mcp_thread_config",
    "_codex_nonfull_config_overrides",
    "_codex_normalizar_approval_profile",
    "_codex_normalizar_model",
    "_codex_normalizar_reasoning_effort",
    "_codex_normalizar_service_tier",
    "_codex_normalizar_speed",
    "_codex_now",
    "_codex_prepare_conversation_context",
    "_codex_process_stream_event",
    "_codex_prompt_com_contexto_tela",
    "_codex_readonly_cwd_for_session",
    "_codex_reasoning_effort_enum",
    "_codex_register_active_turn",
    "_codex_runtime_require_ready",
    "_codex_safe_id",
    "_codex_sandbox_enum",
    "_codex_save_conversation_state",
    "_codex_scope_instruction",
    "_codex_scope_violations",
    "_codex_sdk_env",
    "_codex_task_conversation_id",
    "_codex_task_whatsapp_query_only",
    "_codex_thread_resume_failure",
    "_codex_transition_task_plan",
    "_codex_transient_runtime_failure",
    "_codex_unregister_active_turn",
    "_codex_update_conversation_memory",
    "_codex_update_live",
    "_codex_update_task",
    "_codex_web_readonly_config_overrides",
    "_codex_workspace_changed_files",
    "_codex_workspace_snapshot",
]

__codex_exports__ = ["_codex_run_worker"]
