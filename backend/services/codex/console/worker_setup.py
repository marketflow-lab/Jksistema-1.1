"""Preparation stages for the Codex Console worker."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from backend.services import codex_ai_telemetry, codex_mcp_rollout


def _start(ctx: dict[str, Any], task_id: str) -> bool:
    task = _codex_load_task(task_id)
    if not task:
        return False
    persisted_sandbox = str(task.get("sandbox") or "read_only")
    sandbox = "read_only"
    if persisted_sandbox != sandbox:
        _codex_update_task(
            task_id,
            sandbox=sandbox,
            approval_mode="read_only",
            access_mode="read_only",
            whatsapp_full_access=False,
        )
    tenant = str(task.get("client_id") or "").strip()
    if not tenant:
        raise RuntimeError("data_selection_tenant_required")
    acquired_full_lock = False
    if sandbox == "full_access":
        acquired_full_lock = CODEX_FULL_ACCESS_LOCK.acquire(blocking=False)
        if not acquired_full_lock:
            raise RuntimeError(
                "Ja existe uma tarefa Codex com acesso total em execucao."
            )
    _codex_update_task(task_id, status="running", started_at=_codex_now(), error="")
    _codex_transition_task_plan(
        task_id, "planejando", current_step="entender", step_status="completed"
    )
    _codex_transition_task_plan(
        task_id, "consultando", current_step="consultar", step_status="in_progress"
    )
    task = _codex_load_task(task_id) or task
    _codex_log(task, f"Iniciando Codex em {sandbox}.")
    _codex_apply_sdk_protocol_compat()
    ctx.update(
        task=task,
        tenant=tenant,
        sandbox=sandbox,
        acquired_full_lock=acquired_full_lock,
        thread_id="",
    )
    return True


def _configure_identity(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    sandbox = ctx["sandbox"]
    model = _codex_normalizar_model(task.get("model"))
    reasoning_effort = _codex_reasoning_effort_enum(task.get("reasoning_effort"))
    speed = _codex_normalizar_speed(task.get("speed"))
    service_tier = _codex_normalizar_service_tier(task.get("service_tier"), speed)
    approval_profile = _codex_normalizar_approval_profile(
        task.get("approval_mode"), sandbox
    )
    approval_mode = _codex_approval_mode_enum(approval_profile, sandbox)
    sandbox_enum = _codex_sandbox_enum(sandbox)
    permissions = task.get("permissions") if isinstance(task.get("permissions"), dict) else {}
    is_full_task = permissions.get("full") is True
    trusted_model_config = bool(
        str(task.get("origin") or "").strip().lower() == "whatsapp"
        and task.get("trusted_model_config") is True
    )
    prompt = str(task.get("prompt") or "").strip()
    session = {
        "client_id": ctx["tenant"],
        "username": str(task.get("created_by") or "user"),
    }
    conversation_id = _codex_conversation_id(
        str(task.get("conversation_id") or ""), str(task.get("task_id") or "")
    )
    cwd = _codex_readonly_cwd_for_session(session, conversation_id)
    conversation_state = _codex_conversation_state_for_task(task)
    thread_id = str(task.get("thread_id") or "").strip() if is_full_task else ""
    fingerprints_match = bool(
        is_full_task
        and str(task.get("thread_prompt_fingerprint") or "")
        and str(task.get("thread_prompt_fingerprint") or "")
        == str(conversation_state.get("thread_prompt_fingerprint") or "")
        and str(task.get("thread_schema_fingerprint") or "")
        == str(conversation_state.get("thread_schema_fingerprint") or "")
        and str(task.get("thread_scope_fingerprint") or "")
        == str(conversation_state.get("thread_scope_fingerprint") or "")
        and str(task.get("thread_conversation_key") or "")
        == str(conversation_state.get("thread_conversation_key") or "")
    )
    if not thread_id and fingerprints_match:
        thread_id = str(conversation_state.get("latest_thread_id") or "").strip()
    if thread_id != str(task.get("thread_id") or "").strip():
        _codex_update_task(task_id, thread_id=thread_id)
    ctx.update(
        model=model,
        reasoning_effort=reasoning_effort,
        speed=speed,
        service_tier=service_tier,
        approval_profile=approval_profile,
        approval_mode=approval_mode,
        sandbox_enum=sandbox_enum,
        is_full_task=is_full_task,
        trusted_model_config=trusted_model_config,
        external_safe_mode=bool(task.get("external_safe_mode")),
        whatsapp_full_access=bool(task.get("whatsapp_full_access")),
        read_only_channel_mode=True,
        prompt=prompt,
        cwd=cwd,
        conversation_state=conversation_state,
        thread_id=thread_id,
        goal=_codex_clean_text(task.get("goal"), 1200),
        paths=list(task.get("paths") or []),
    )


def _enforce_read_only(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    if not ctx["is_full_task"]:
        ctx["sandbox"] = "read_only"
        if not ctx["trusted_model_config"]:
            ctx["model"] = _codex_normalizar_model(None)
            ctx["reasoning_effort"] = _codex_reasoning_effort_enum(None)
            ctx["speed"] = _codex_normalizar_speed(None)
            ctx["service_tier"] = _codex_normalizar_service_tier(
                None, ctx["speed"]
            )
        ctx["approval_profile"] = "read_only"
        ctx["cwd"] = _codex_readonly_cwd_for_session(
            {
                "client_id": ctx["tenant"],
                "username": str(task.get("created_by") or "user"),
            },
            _codex_conversation_id(
                str(task.get("conversation_id") or ""),
                str(task.get("task_id") or ""),
            ),
        )
        ctx.update(thread_id="", goal="", paths=[])
    ctx["sandbox"] = "read_only"
    ctx["approval_profile"] = "read_only"
    ctx["approval_mode"] = _codex_approval_mode_enum(
        ctx["approval_profile"], ctx["sandbox"]
    )
    ctx["sandbox_enum"] = _codex_sandbox_enum(ctx["sandbox"])
    screen_context = (
        task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {}
    )
    scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
    if not scope or str(scope.get("sandbox") or "") != "read_only":
        scope = _codex_build_scope(
            prompt=ctx["prompt"],
            sandbox=ctx["sandbox"],
            paths=ctx["paths"],
            screen_context=screen_context,
            cwd=ctx["cwd"],
        )
        _codex_update_task(task_id, scope=scope)
    ctx.update(
        screen_context=screen_context,
        scope=scope,
        workspace_before=(
            _codex_workspace_snapshot(ctx["cwd"])
            if ctx["sandbox"] != "read_only"
            else {}
        ),
    )


def _select_data(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    agent_mode = _codex_agent_mode_enabled()
    trace_id = str(task.get("trace_id") or task_id)
    selection_started = time.perf_counter()
    telemetry: Optional[codex_ai_telemetry.CodexAITelemetry] = None
    try:
        telemetry = _codex_ai_telemetry_instance()
        telemetry.start_span(
            ctx["tenant"],
            trace_id=trace_id,
            span_id=f"{trace_id}:selection",
            stage="selection",
        )
    except Exception:
        telemetry = None
    conversation_context: dict[str, Any] = {}
    data_selection: dict[str, Any] = {}
    if agent_mode:
        conversation_context = _codex_prepare_conversation_context(task)
        data_selection = _codex_agent_materialize_task_data_selection(
            task,
            prompt=ctx["prompt"],
            screen_context=ctx["screen_context"],
            conversation_context=conversation_context,
            read_only_only=ctx["read_only_channel_mode"],
        )
        _codex_update_task(
            task_id,
            data_selection=data_selection,
            data_selection_trust_marker=_CODEX_AGENT_DATA_SELECTION_TRUST_MARKER,
            data_selection_tool_ids=_codex_agent_data_selection_tool_ids(data_selection),
            query_policy=(
                task.get("query_policy")
                if isinstance(task.get("query_policy"), dict)
                else {}
            ),
        )
    refreshed_task = _codex_load_task(task_id) or task
    native_mcp = _codex_native_mcp_enabled(refreshed_task)
    client_safe = _codex_safe_id(ctx["tenant"])
    rollout = codex_mcp_rollout.MCPRolloutPolicyStore(
        Path(_codex_base_info_dir()) / client_safe / "codex_ai" / "mcp_rollout.sqlite3"
    ).get()
    shadow_probe = _codex_mcp_shadow_prepare(
        refreshed_task, ctx["screen_context"], rollout
    )
    shadow_status = str(shadow_probe.get("status") or "")
    _codex_update_task(
        task_id,
        mcp_migration={
            "target": "jk_system_mcp",
            "rollout_mode": rollout.get("mode") or "off",
            "rollout_version": int(rollout.get("version") or 0),
            "native_enabled": bool(native_mcp),
            "native_active": False,
            "legacy_parser_fallback": True,
            "shadow_observed": False,
            "shadow_status": "prepared" if shadow_status == "prepared" else shadow_status,
            "external_call_executed": False if shadow_status else None,
            "disabled_reason": ""
            if native_mcp or shadow_status == "prepared"
            else "rollout_or_plan_not_eligible",
        },
    )
    ctx.update(
        agent_mode=agent_mode,
        trace_id=trace_id,
        selection_started=selection_started,
        telemetry=telemetry,
        conversation_context=conversation_context,
        data_selection=data_selection,
        native_mcp=native_mcp,
        shadow_probe=shadow_probe,
    )


def _finish_selection_span(ctx: dict[str, Any]) -> None:
    if ctx["telemetry"] is None:
        return
    ctx["telemetry"].finish_span(
        ctx["tenant"],
        trace_id=ctx["trace_id"],
        span_id=f"{ctx['trace_id']}:selection",
        stage="selection",
        status="completed",
        duration_ms=(time.perf_counter() - ctx["selection_started"]) * 1000,
    )


def _prepare_prompt(ctx: dict[str, Any], task_id: str) -> None:
    task = ctx["task"]
    app_data_context: dict[str, Any] = {}
    if ctx["agent_mode"]:
        _codex_update_live(task_id, agent_mode=True, live_status="interpretando pergunta")
        run_prompt = _codex_agent_initial_prompt(
            ctx["prompt"],
            ctx["screen_context"],
            ctx["conversation_context"],
            ctx["tenant"],
            task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
            ctx["sandbox"],
            ctx["model"],
            _codex_normalizar_reasoning_effort(task.get("reasoning_effort")),
            ctx["speed"],
            ctx["approval_profile"],
            ctx["read_only_channel_mode"],
            ctx["whatsapp_full_access"],
            ctx["native_mcp"],
            ctx["data_selection"],
        )
        stats = _codex_context_stats_from_prompt(
            ctx["prompt"],
            run_prompt,
            ctx["screen_context"],
            {"enabled": True, "tool_results_count": 0},
            ctx["conversation_context"],
        )
        _codex_update_live(
            task_id,
            context_stats=stats,
            conversation_summary={
                "conversation_id": ctx["conversation_context"].get("conversation_id") or "",
                "summary_chars": len(str(ctx["conversation_context"].get("summary") or "")),
                "recent_messages": len(ctx["conversation_context"].get("recent_messages") or []),
                "compacted": bool(ctx["conversation_context"].get("compacted")),
            },
            conversation_compaction=(
                ctx["conversation_context"].get("compaction")
                if isinstance(ctx["conversation_context"].get("compaction"), dict)
                else {}
            ),
            app_data_context={
                "enabled": True,
                "agent_mode": True,
                "tool_results_count": 0,
                "catalog_tools_count": len(
                    _codex_agent_tool_catalog(
                        task.get("permissions")
                        if isinstance(task.get("permissions"), dict)
                        else {},
                        read_only_only=ctx["read_only_channel_mode"],
                    )
                ),
                "error": "",
            },
            live_status="interpretando pergunta",
        )
        _codex_log(
            task,
            "Modo agente ativo: contexto bruto desativado; usando catalogo de ferramentas sob demanda.",
            "status",
        )
        if (
            ctx["conversation_context"].get("recent_messages")
            or ctx["conversation_context"].get("summary")
        ):
            _codex_log(task, "Historico da conversa anexado em formato compacto.", "status")
    else:
        _codex_update_live(
            task_id,
            agent_mode=False,
            live_status="Interpretando pedido e consultando dados internos.",
        )
        app_data_context = _codex_app_data_context(
            ctx["prompt"],
            ctx["screen_context"],
            str(task.get("client_id") or ""),
            task.get("permissions") if isinstance(task.get("permissions"), dict) else {},
        )
        run_prompt = _codex_prompt_com_contexto_tela(
            ctx["prompt"], ctx["screen_context"], app_data_context
        )
        stats = _codex_context_stats(
            ctx["prompt"], ctx["screen_context"], app_data_context
        )
        steps = (
            app_data_context.get("status_steps")
            if isinstance(app_data_context, dict)
            and isinstance(app_data_context.get("status_steps"), list)
            else []
        )
        final_status = str(
            steps[-1] if steps else "Gerando resposta com dados internos."
        )
        _codex_update_live(
            task_id,
            context_stats=stats,
            app_data_context={
                "enabled": bool(app_data_context.get("enabled")),
                "agent_mode": False,
                "tool_results_count": int(app_data_context.get("tool_results_count") or 0),
                "error": str(app_data_context.get("error") or "")[:600],
            },
            live_status=final_status,
        )
        _codex_log(task, "Interpretando pedido e preparando Codex Data Tools.", "status")
    _finish_selection_span(ctx)
    ctx.update(
        app_data_context=app_data_context,
        run_prompt=run_prompt,
        initial_run_prompt=run_prompt,
        context_stats=stats,
    )


def _extra_instructions(ctx: dict[str, Any]) -> list[str]:
    task = ctx["task"]
    metadata = (
        task.get("channel_metadata")
        if isinstance(task.get("channel_metadata"), dict)
        else {}
    )
    dual_web = _codex_dual_worker_web_search_enabled(
        task,
        read_only_channel_mode=ctx["read_only_channel_mode"],
        sandbox=ctx["sandbox"],
    )
    instructions: list[str] = []
    behavior = _codex_clean_text(metadata.get("phone_ai_behavior"), 2000)
    if str(task.get("origin") or "").strip().lower() == "whatsapp" and behavior:
        instructions.append(
            "Instrucao administrativa especifica para este numero de WhatsApp:\n"
            + behavior
            + "\nAplique esta instrucao ao tom, formato e forma de atendimento. Ela nunca "
            "amplia permissoes, libera mutacoes, altera o escopo de lojas ou substitui "
            "regras de seguranca e fontes."
        )
    if ctx["goal"]:
        instructions.append(f"Meta definida pelo usuario: {ctx['goal']}")
    if task.get("planning_mode"):
        instructions.append(
            "Modo planejamento ativo: comece com um plano curto antes de executar alteracoes."
        )
    if ctx["paths"]:
        instructions.append(
            "Arquivos e pastas adicionados ao contexto:\n"
            + "\n".join(f"- {path}" for path in ctx["paths"])
        )
    instructions.append(_codex_scope_instruction(ctx["scope"]))
    if ctx["screen_context"]:
        instructions.append(
            "Contexto da tela atual do JK Sistema enviado pela sidebar. Trate isto como "
            "a tela onde o usuario esta agora; use os textos, filtros, cards, tabelas e "
            "controles visiveis para entender o pedido. Se precisar de algo que nao esteja "
            "no snapshot, diga exatamente o que falta."
        )
        _codex_log(task, "Contexto da tela anexado ao prompt do Codex.")
    if ctx["agent_mode"]:
        protocol = (
            "Use exclusivamente as ferramentas MCP tipadas"
            if ctx["native_mcp"]
            else "Use o protocolo jk_tool_calls para solicitar ferramentas read-only"
        )
        instructions.append(
            f"Modo agente ativo: nao ha contexto bruto anexado. {protocol} quando precisar de dados reais."
        )
        if dual_web:
            instructions.append(
                "Para perguntas gerais que dependam de informacao atual, use a pesquisa web "
                "read-only. Consulte fontes adequadas, informe as fontes no resultado estruturado "
                "e nunca execute acoes externas."
            )
    elif int(ctx["app_data_context"].get("tool_results_count") or 0) > 0:
        instructions.append(
            "Consultas internas read-only do JK Sistema foram anexadas ao prompt. Use esses "
            "resultados como fonte principal para responder perguntas de dados e montar relatorios."
        )
        _codex_log(
            task,
            f"Consultas internas anexadas: {int(ctx['app_data_context'].get('tool_results_count') or 0)} resultado(s).",
        )
    elif ctx["app_data_context"].get("error"):
        _codex_log(
            task,
            "Falha ao preparar consultas internas: "
            + str(ctx["app_data_context"].get("error"))[:300],
            "warning",
        )
    ctx["dual_worker_web_search"] = dual_web
    return instructions


def _build_instructions(ctx: dict[str, Any]) -> None:
    task = ctx["task"]
    instructions = _extra_instructions(ctx)
    instructions.append(
        "Configuracao Codex: "
        f"modelo={ctx['model']}, "
        f"raciocinio={_codex_normalizar_reasoning_effort(task.get('reasoning_effort'))}, "
        f"velocidade={ctx['speed']}, aprovacao={ctx['approval_profile']}, "
        f"sandbox={ctx['sandbox']}."
    )
    if ctx["whatsapp_full_access"]:
        instructions.append(
            "No WhatsApp, fale de forma natural, cordial e descontraida, como um colega "
            "prestativo. Va direto ao ponto, varie a abertura, nao coloque titulo em respostas "
            "simples, nao repita o nome Black Jhon e nao assine ao final. Nao use emojis. "
            "Quando o usuario pedir relatorio pelo WhatsApp, use Markdown simples e estas secoes: "
            "# Relatorio, ## Dados principais, ## Mais vendidos (se houver), ## Analise e "
            "## Fontes e cobertura. Nao use tabelas. Limite o resumo a 8 indicadores e o "
            "ranking a 5 itens, salvo pedido expresso por mais. Separe cada produto do ranking "
            "em seu proprio bloco, com SKU, nome curto, quantidade e valor. Nas fontes, informe "
            "em linguagem simples o periodo, a conta ou loja, a quantidade de registros e "
            "qualquer lacuna. Se os dados forem insuficientes, declare a lacuna em vez de "
            "completar por suposicao."
        )
    else:
        instructions.append(
            "Quando o usuario pedir relatorio, gere um relatorio em Markdown com titulo, "
            "periodo/filtros usados, dados principais, analise e proximas acoes. Se os dados "
            "internos anexados forem insuficientes, declare a lacuna em vez de completar por suposicao."
        )
    if ctx["read_only_channel_mode"]:
        access = (
            "Esta tarefa veio do WhatsApp e foi vinculada a um usuario do JK Sistema. "
            "Durante a execucao, use apenas consultas read-only do catalogo. Nao crie "
            "propostas operacionais, nao execute acoes de negocio e nao tente aprovar a propria tarefa. "
        )
    elif ctx["whatsapp_full_access"]:
        access = (
            "Esta tarefa veio do WhatsApp de um usuario administrativo full. Consultas podem "
            "usar todo o catalogo permitido ao usuario. Se a tarefa for mutavel, ela ja foi "
            "confirmada por codigo unico no mesmo numero antes desta execucao. Execute somente "
            "o pedido confirmado, preserve a auditoria e nunca exponha credenciais ou segredos. "
        )
    elif ctx["is_full_task"]:
        access = (
            "Voce esta dentro do JK Sistema em modo interno administrativo e estritamente "
            "read-only. Nao use terminal, nao altere arquivos e execute mutacoes comerciais "
            "somente por propostas tipadas do backend. "
        )
    else:
        access = (
            "Voce atende um usuario autenticado sem acesso administrativo full. A tarefa e "
            "estritamente read-only e limitada aos modulos/ferramentas autorizados no catalogo "
            "deste turno. Nao leia, enumere ou descreva arquivos, fontes, modulos ou capacidades "
            "omitidos do catalogo. "
        )
    ctx["developer_instructions"] = (
        access
        + "Respeite o pedido do usuario, nao exponha segredos e explique limites de acesso com clareza.\n\n"
        + "\n".join(instructions)
    )
    fast_mode = ctx["speed"] == "fast"
    if ctx["dual_worker_web_search"]:
        ctx["config_overrides"] = _codex_web_readonly_config_overrides(
            fast_mode=fast_mode
        )
    elif ctx["read_only_channel_mode"] and ctx["sandbox"] == "read_only":
        ctx["config_overrides"] = _codex_external_readonly_config_overrides(
            fast_mode=fast_mode
        )
    else:
        ctx["config_overrides"] = _codex_nonfull_config_overrides(fast_mode=fast_mode)


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

__codex_exports__: list[str] = []
