"""Internal Codex Assistant component."""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Optional

from backend.services import codex_turn_context

from .catalog import _assistant_tool_access, _assistant_tool_allowed, _assistant_tool_meta
from .catalog_data import CODEX_DATA_TOOLS
from .evidence import (
    _assistant_agent_fallback_ids,
    _assistant_agent_int,
    _assistant_agent_result_package,
    denied_evidence,
    failed_evidence,
    mark_recent_cache,
)
from .normalization import _assistant_is_broad_open_questions_query, _assistant_is_generic_sales_api_query, _assistant_latest_ml_event_kind
from .references import _assistant_bool_arg, _assistant_calendar_date, _assistant_extract_sku_filter, _assistant_force_refresh_requested, _assistant_normalize_api_status, _assistant_normalize_identifier, _assistant_normalize_ml_item_id, _assistant_normalize_sku, _assistant_previous_period, _assistant_resolve_loja, _assistant_resolve_period, _assistant_wants_store_breakdown
from .registry import _assistant_execute_registry_tool
from .routing import _assistant_registry_plan, _assistant_source_routing_policy
from .runtime import _ASSISTANT_AUDITED_API_TOOLS, _assistant_api_error_code, _assistant_api_query_audit, _assistant_cache_get, _assistant_cache_safe_payload, _assistant_cache_set, _assistant_external_cache_key, _assistant_non_retryable_auth_failure, _assistant_now, _assistant_periodo_padrao, _assistant_previous_contains_tool, _assistant_retryable_api_error, _assistant_texto_norm
from .settings import API_QUERY_TIMEOUT_SECONDS, CODEX_AGENT_NORMAL_ROW_LIMIT, CODEX_AGENT_REPORT_ROW_LIMIT, CODEX_DATA_CONTEXT_CHAR_LIMIT, CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT, EXTERNAL_CACHE_SECONDS
from .source_labels import _assistant_human_source_label, _assistant_human_tool_label

def _assistant_execution_access_error(tool_id: str, permissions: Any) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    meta = _assistant_tool_meta(tool_id)
    if not tool_id or not any(str(tool.get("id") or "") == tool_id for tool in CODEX_DATA_TOOLS):
        reason = "Ferramenta inexistente no Codex Data Tools Registry."
        return meta, {"success": False, "tool_id": tool_id, "error": reason,
            "evidence": failed_evidence(reason), "generated_at": _assistant_now()}
    if meta.get("read_only") is not True:
        reason = "Ferramenta mutavel bloqueada. Acoes mutaveis exigem aprovacao explicita."
        return meta, {"success": False, "tool_id": tool_id, "error": reason,
            "evidence": denied_evidence("A politica read-only bloqueou a ferramenta mutavel."), "generated_at": _assistant_now()}
    access = _assistant_tool_access(tool_id, permissions)
    if not access.get("allowed"):
        return meta, {"success": False, "tool_id": tool_id, "module": meta.get("module") or "",
            "error": "Acesso negado: usuario sem permissao para consultar esta fonte de dados.",
            "error_code": "tool_permission_denied", "required_permissions": list(access.get("required_permissions") or []),
            "missing_permissions": list(access.get("missing_permissions") or []), "records": 0, "read_only": True,
            "evidence": denied_evidence("A permissao do usuario nao autoriza esta fonte."), "generated_at": _assistant_now()}
    return meta, None


def _assistant_execution_message_period(
    client_id: str, tool_id: str, args: dict[str, Any], screen_context: Any, materialized_context: bool,
) -> tuple[str, str, str, str, str, str, bool]:
    message = str(
        args.get("message")
        or args.get("mensagem")
        or args.get("query")
        or args.get("pergunta")
        or args.get("recurso")
        or tool_id
    ).strip()
    mode = str(args.get("mode") or args.get("modo") or "").strip().lower()
    if mode not in {"chat", "report", "daily", "proactive"}:
        wants_report = bool(re.search(r"\b(relatorio|analise completa|ultimos?\s+\d+\s+dias?)\b", _assistant_texto_norm(message)))
        mode = "report" if wants_report else "chat"

    data_inicio = _assistant_calendar_date(args.get("data_inicio") or args.get("inicio") or args.get("start_date"))
    data_fim = _assistant_calendar_date(args.get("data_fim") or args.get("fim") or args.get("end_date"))
    if not (data_inicio and data_fim):
        data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    latest_kind = "" if materialized_context else _assistant_latest_ml_event_kind(message)
    latest_ml_event = bool(
        tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
        and latest_kind in {
            "both",
            "sale" if tool_id == "mercado_livre_orders" else "return",
        }
        and not re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", str(message or ""))
    )
    if latest_ml_event:
        data_inicio, data_fim = _assistant_periodo_padrao(365)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    if tool_id == "period_comparison":
        explicit_a = (
            _assistant_calendar_date(args.get("data_inicio_a") or args.get("inicio_a") or args.get("start_date_a")),
            _assistant_calendar_date(args.get("data_fim_a") or args.get("fim_a") or args.get("end_date_a")),
        )
        explicit_b = (
            _assistant_calendar_date(args.get("data_inicio_b") or args.get("inicio_b") or args.get("start_date_b")),
            _assistant_calendar_date(args.get("data_fim_b") or args.get("fim_b") or args.get("end_date_b")),
        )
        if all((*explicit_a, *explicit_b)) and explicit_a[0] <= explicit_a[1] and explicit_b[0] <= explicit_b[1]:
            # O agente pode chamar A=periodo atual e B=periodo anterior. Para
            # que a variacao seja sempre intuitiva, a ferramenta recebe os
            # periodos em ordem cronologica: anterior primeiro, atual depois.
            if explicit_a[0] <= explicit_b[0]:
                (prev_inicio, prev_fim), (data_inicio, data_fim) = explicit_a, explicit_b
            else:
                (prev_inicio, prev_fim), (data_inicio, data_fim) = explicit_b, explicit_a

    return message, mode, data_inicio, data_fim, prev_inicio, prev_fim, latest_ml_event


def _assistant_execution_identifiers(
    client_id: str, tool_id: str, args: dict[str, Any], screen_context: Any, message: str,
    materialized_context: bool, latest_ml_event: bool, meta: dict[str, Any], query_deadline: Optional[float],
) -> tuple[str, str, str, str, str, str, int, bool, bool, bool, Optional[float]]:
    loja = str(args.get("loja") or args.get("conta") or args.get("store") or "").strip()
    if not loja and not materialized_context:
        loja = _assistant_resolve_loja(client_id, message, screen_context)
    sku = _assistant_normalize_sku(args.get("sku") or args.get("codigo") or args.get("seller_sku") or "")
    if not sku and not materialized_context:
        sku = _assistant_extract_sku_filter(message, screen_context)
    item_id = _assistant_normalize_ml_item_id(args.get("item_id") or args.get("mlb") or args.get("id_anuncio"))
    if not item_id and not materialized_context:
        item_match = re.search(r"\bMLB[\s_-]?\d{6,}\b", str(message or ""), flags=re.IGNORECASE)
        item_id = _assistant_normalize_ml_item_id(item_match.group(0) if item_match else "")
    if tool_id == "questions_post_sale_query" and _assistant_is_broad_open_questions_query(message):
        # A fila atual e uma consulta propria. SKU/MLB de um turno ou selecao
        # anterior nao podem restringir nem reabrir a pesquisa de produto.
        sku = ""
        item_id = ""
    id_pedido = _assistant_normalize_identifier(
        args.get("id_pedido") or args.get("pedido_id") or args.get("order_id") or args.get("id_order")
    )
    if not id_pedido and not materialized_context:
        pedido_match = re.search(
            r"\b(?:pedido|order)\s*(?:id|numero|n\.?|#)?\s*[:#-]?\s*(\d{3,})\b",
            str(message or ""),
            flags=re.IGNORECASE,
        )
        id_pedido = _assistant_normalize_identifier(pedido_match.group(1) if pedido_match else "")
    if not id_pedido and not materialized_context and tool_id == "mercado_livre_orders":
        standalone_ids = re.findall(r"\b\d{10,20}\b", str(message or ""))
        if (
            len(standalone_ids) == 1
            and re.search(r"\b(venda|pedido|order|pack|compra)\b", _assistant_texto_norm(message))
        ):
            id_pedido = _assistant_normalize_identifier(standalone_ids[0])
    pack_id = _assistant_normalize_identifier(args.get("pack_id") or args.get("pack") or "")
    if not pack_id and not materialized_context:
        pack_match = re.search(
            r"\bpack(?:\s*(?:id|numero|#))?\s*[:#-]?\s*(\d{5,30})\b",
            str(message or ""),
            flags=re.IGNORECASE,
        )
        pack_id = _assistant_normalize_identifier(pack_match.group(1) if pack_match else "")
    default_status = ""
    if tool_id == "mercado_livre_listing":
        default_status = "active"
    elif tool_id == "mercado_livre_orders":
        default_status = "paid,partially_refunded"
    status = _assistant_normalize_api_status(args.get("status") or args.get("situacao"), default_status)
    offset = _assistant_agent_int(args.get("offset"), 0, 0, 10000)
    incluir_detalhes = _assistant_bool_arg(
        args.get("incluir_detalhes", args.get("include_details", args.get("incluir_descricao"))),
        False,
    )
    routing_policy = _assistant_source_routing_policy(message)
    incluir_comercial = _assistant_bool_arg(
        args.get("incluir_comercial", args.get("include_commercial")),
        bool(routing_policy.get("include_commercial_detail")),
    )
    strict_latest_ml_event = bool(
        latest_ml_event and tool_id in {"mercado_livre_orders", "mercado_livre_returns"}
    )
    if strict_latest_ml_event:
        offset = 0
    force_refresh = bool(
        _assistant_force_refresh_requested(args, message) or strict_latest_ml_event
    )

    return loja, sku, item_id, id_pedido, pack_id, status, offset, incluir_detalhes, incluir_comercial, force_refresh, query_deadline


def _assistant_execution_limit(tool_id: str, args: dict[str, Any], mode: str, strict_latest_ml_event: bool) -> int:
    if tool_id in {"program_functions_catalog", "capability_resolve"}:
        default_limit = 1000
        maximum_limit = 1000
    elif tool_id == "context_hub_search":
        request_surface = str(args.get("request_surface") or "").strip().casefold()
        default_limit = 8 if request_surface in {"whatsapp", "black_jhon_whatsapp"} else 12
        maximum_limit = default_limit
    elif tool_id == "mercado_livre_full_stock":
        default_limit = 10000
        maximum_limit = 20000
    elif tool_id == "mercado_livre_returns":
        default_limit = 1
        maximum_limit = 100
    elif tool_id in {"mercado_livre_listing", "mercado_livre_resource_query"}:
        default_limit = 20
        maximum_limit = 100
    elif tool_id == "mercado_livre_promotions":
        default_limit = 20
        maximum_limit = 50
    elif tool_id == "mercado_livre_post_sale_detail":
        default_limit = 50
        maximum_limit = 100
    elif tool_id == "mercado_livre_orders":
        default_limit = 20_000 if mode in {"report", "daily"} else 50
        maximum_limit = 20_000 if mode in {"report", "daily"} else 100
    else:
        default_limit = CODEX_AGENT_REPORT_ROW_LIMIT if mode in {"report", "daily"} else CODEX_AGENT_NORMAL_ROW_LIMIT
        maximum_limit = 500
    limit_safe = _assistant_agent_int(args.get("limite") or args.get("limit"), default_limit, 1, maximum_limit)
    if strict_latest_ml_event:
        limit_safe = 1

    return limit_safe


def _assistant_execution_plan(
    client_id: str, tool_id: str, args: dict[str, Any], screen_context: Any, materialized_context: bool,
    meta: dict[str, Any], query_deadline: Optional[float],
) -> tuple[dict[str, Any], str, str, bool, bool, Optional[float]]:
    message, mode, data_inicio, data_fim, prev_inicio, prev_fim, latest_ml_event = _assistant_execution_message_period(
        client_id, tool_id, args, screen_context, materialized_context,
    )
    if meta.get("external") is True and query_deadline is None:
        query_deadline = time.monotonic() + API_QUERY_TIMEOUT_SECONDS
    (loja, sku, item_id, id_pedido, pack_id, status, offset, incluir_detalhes,
     incluir_comercial, force_refresh, query_deadline) = _assistant_execution_identifiers(
        client_id, tool_id, args, screen_context, message, materialized_context,
        latest_ml_event, meta, query_deadline,
    )
    strict_latest_ml_event = bool(latest_ml_event and tool_id in {"mercado_livre_orders", "mercado_livre_returns"})
    api_sales_query = _assistant_is_generic_sales_api_query(message)
    limit_safe = _assistant_execution_limit(tool_id, args, mode, strict_latest_ml_event)
    plan = {
        "intent": tool_id, "mode": mode,
        "message": message,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
        "periodo_anterior": {"data_inicio": prev_inicio, "data_fim": prev_fim},
        "loja": loja,
        "sku": sku,
        "item_id": item_id,
        "mlb": item_id,
        "id_pedido": id_pedido,
        "pack_id": pack_id,
        "status": status,
        "offset": offset,
        "incluir_detalhes": incluir_detalhes,
        "incluir_comercial": incluir_comercial,
        "dias": _assistant_agent_int(args.get("dias") or args.get("days"), 30, 1, 150),
        "promotion_id": str(args.get("promotion_id") or args.get("promocao_id") or "").strip()[:120],
        "resource_id": str(args.get("resource_id") or args.get("recurso_id") or args.get("endpoint_id") or "").strip()[:160],
        "resource_params": (
            dict(args.get("params"))
            if isinstance(args.get("params"), dict)
            else dict(args.get("parametros"))
            if isinstance(args.get("parametros"), dict)
            else {}
        ),
        "incluir_contagens": _assistant_bool_arg(args.get("incluir_contagens", args.get("include_counts")), False),
        "force_refresh": force_refresh,
        "query_deadline": query_deadline,
        "api_sales_query": api_sales_query,
        "source_role": (
            "primary_api"
            if tool_id in {"bling_sales_orders", "bling_positive_stock_sku_count", "mercado_livre_orders", "mercado_livre_returns", "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions", "mercado_livre_post_sale_detail"}
            else "supporting_local_history"
            if api_sales_query and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}
            else "context"
        ),
        "aggregation_policy": (
            "single_store_scalar_no_sum"
            if tool_id == "bling_positive_stock_sku_count"
            else "separate_sources_no_sum"
            if api_sales_query
            else "standard"
        ),
        "limite": limit_safe,
        "max_paginas": _assistant_agent_int(
            args.get("max_paginas") or args.get("max_pages"),
            400 if mode in {"report", "daily"} else 2,
            1,
            400 if mode in {"report", "daily"} else 2,
        ),
        "module_filter": str(args.get("modulo") or args.get("module") or args.get("module_filter") or "").strip(),
        "category_filter": str(args.get("categoria") or args.get("category") or args.get("category_filter") or "").strip(),
        "capability_id": str(args.get("capability_id") or args.get("capacidade_id") or "").strip(),
        "source_id": str(args.get("source_id") or args.get("fonte") or "").strip(),
        "source_type": str(args.get("source_type") or args.get("tipo") or args.get("type") or "").strip(),
        "context_ids": [
            str(item).strip()
            for item in (
                args.get("ids")
                if isinstance(args.get("ids"), list)
                else args.get("entity_ids")
                if isinstance(args.get("entity_ids"), list)
                else []
            )[:50]
            if str(item or "").strip()
        ],
        "environment_filter": str(args.get("environment") or args.get("ambiente") or "").strip(),
        "context_surface": str(
            args.get("surface") or args.get("environment") or args.get("ambiente") or ""
        ).strip(),
        "document_types": [
            str(item).strip() for item in (
                args.get("document_types") if isinstance(args.get("document_types"), list) else []
            )[:10] if str(item).strip()
        ],
        "context_store_ref": str(args.get("store_ref") or "").strip(),
        "context_tags": [
            str(item).strip() for item in (args.get("tags") if isinstance(args.get("tags"), list) else [])[:12]
            if str(item).strip()
        ],
        "context_valid_at": str(args.get("valid_at") or "").strip(),
        "context_truth_class": str(args.get("truth_class") or "").strip(),
        "context_authority": str(args.get("authority") or "").strip(),
        "context_sensitivity": str(args.get("sensitivity") or "").strip(),
        "sql": str(args.get("sql") or "").strip(),
        "include_routes": bool(args.get("incluir_rotas", args.get("include_routes", True))),
        "include_services": bool(args.get("incluir_servicos", args.get("include_services", True))),
        "include_actions": bool(args.get("incluir_acoes", args.get("include_actions", True))),
        "include_pages": bool(args.get("incluir_telas", args.get("include_pages", True))),
        "action_id": str(args.get("action_id") or "").strip(),
        "action_params": args.get("params") if isinstance(args.get("params"), dict) else {},
        "history": args.get("history") if isinstance(args.get("history"), list) else [],
        "separar_por_loja": any(
            _assistant_bool_arg(args.get(key), False)
            for key in ("separar_por_loja", "todas_lojas", "all_stores")
        ) or _assistant_wants_store_breakdown(message, loja),
        "incluir_registros": bool(args.get("incluir_registros", True)),
        "request_surface": str(args.get("request_surface") or "").strip().casefold(),
        "selected_tools": [tool_id],
    }
    return plan, message, mode, strict_latest_ml_event, force_refresh, query_deadline


def _assistant_execution_cache(
    client_id: str, tool_id: str, plan: dict[str, Any], meta: dict[str, Any], force_refresh: bool,
    audit_user: str,
) -> tuple[str, int, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    cache_key = ""
    cache_ttl = 0
    recent: Optional[dict[str, Any]] = None
    if meta.get("external") is True and meta.get("sensitive") is not True:
        cache_ttl = max(1, min(int(meta.get("cache_ttl_seconds") or EXTERNAL_CACHE_SECONDS), EXTERNAL_CACHE_SECONDS))
        cache_key = _assistant_external_cache_key(client_id, tool_id, plan)
        cached = _assistant_cache_get(client_id, cache_key, cache_ttl)
        if isinstance(cached, dict) and str(cached.get("tool_id") or "") == tool_id:
            recent = copy.deepcopy(cached)
            if not force_refresh:
                result = copy.deepcopy(cached)
                result.update(cache_hit=True, cache_ttl_seconds=cache_ttl)
                result["evidence"] = mark_recent_cache(result.get("evidence"))
                _assistant_api_query_audit(client_id, tool_id, plan, result, audit_user=audit_user, cache_hit=True)
                return cache_key, cache_ttl, recent, result
    return cache_key, cache_ttl, recent, None


def _assistant_execute_primary(
    client_id: str, tool_id: str, message: str, screen_context: Any, plan: dict[str, Any],
    previous_results: Optional[list[dict[str, Any]]], permissions: Any,
) -> dict[str, Any]:
    registry_seed = list(previous_results or []) if isinstance(previous_results, list) else []
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed_tool_ids: set[str] = set()

    prereq_ids: list[str] = []
    if tool_id in {"bling_fiscal_product", "bling_lots", "bling_lot_movements"}:
        prereq_ids = ["product_data", "product_registry", "bling_product"]

    for prereq_id in prereq_ids:
        if not _assistant_tool_allowed(prereq_id, permissions):
            warnings.append(f"Pre-requisito omitido por permissao insuficiente: {prereq_id}.")
            continue
        executed_tool_ids.add(prereq_id)
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            prereq_id,
            message,
            screen_context,
            {**plan, "selected_tools": [prereq_id]},
            registry_seed + registry_results,
        )
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)
    executed_tool_ids.add(tool_id)
    raw, registry, local_warnings = _assistant_execute_registry_tool(
        client_id,
        tool_id,
        message,
        screen_context,
        plan,
        registry_seed + registry_results,
    )
    raw_results.extend(raw)
    registry_results.extend(registry)
    warnings.extend(local_warnings)

    return {"registry_seed": registry_seed, "raw_results": raw_results,
        "registry_results": registry_results, "warnings": warnings, "executed_tool_ids": executed_tool_ids}


def _assistant_primary_evidence_state(
    tool_id: str, registry_results: list[dict[str, Any]], strict_latest_ml_event: bool,
) -> tuple[bool, bool]:
    if tool_id == "operational_memory_query":
        meaningful_results = [item for item in registry_results if isinstance(item, dict)]
    else:
        meaningful_results = [
            item for item in registry_results
            if isinstance(item, dict)
            and str(item.get("tool_id") or "") not in {"capability_resolve", "program_action_match", "program_functions_catalog", "operational_memory_query"}
        ]
    has_records = any(int(item.get("records") or 0) > 0 for item in meaningful_results or registry_results)
    primary_exact_conclusive = False
    if strict_latest_ml_event:
        for item in meaningful_results or registry_results:
            if not isinstance(item, dict) or str(item.get("tool_id") or "") != tool_id:
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            coverage = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
            match = summary.get("match") if isinstance(summary.get("match"), dict) else {}
            paging = summary.get("paging") if isinstance(summary.get("paging"), dict) else {}
            latest_api_conclusive = bool(
                not summary.get("error")
                and summary.get("partial_response") is not True
                and int(paging.get("offset") or 0) == 0
                and (int(item.get("records") or 0) > 0 or paging.get("has_more") is False)
            )
            if (
                latest_api_conclusive
                or (
                    coverage.get("complete") is True
                    and not summary.get("error")
                    and (match.get("exact") is True or int(item.get("records") or 0) == 0)
                )
            ):
                primary_exact_conclusive = True
                break
    needs_supporting_history = (
        not primary_exact_conclusive
        if strict_latest_ml_event
        else not has_records
    )

    return has_records, needs_supporting_history


def _assistant_add_supporting_history(
    state: dict[str, Any], client_id: str, tool_id: str, message: str, screen_context: Any,
    plan: dict[str, Any], permissions: Any, previous_results: Optional[list[dict[str, Any]]],
    materialized_context: bool, strict_latest_ml_event: bool, needs_supporting_history: bool,
) -> None:
    registry_seed = state["registry_seed"]
    raw_results, registry_results, warnings = state["raw_results"], state["registry_results"], state["warnings"]
    if (
        needs_supporting_history
        and not materialized_context
        and not strict_latest_ml_event
        and tool_id in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_returns"}
        and (tool_id != "mercado_livre_returns" or strict_latest_ml_event)
        and not (tool_id == "mercado_livre_orders" and bool(plan.get("id_pedido")))
        and _assistant_tool_allowed("sales_returns_query", permissions)
        and not _assistant_previous_contains_tool(previous_results, "sales_returns_query")
    ):
        warnings.append(
            "A API primaria nao confirmou o registro exato; o historico local foi consultado apenas como apoio separado, sem substituir a confirmacao do Mercado Livre."
        )
        support_plan = {
            **plan,
            "selected_tools": ["sales_returns_query"],
            "source_role": "supporting_local_history",
            "source_roles": {"sales_returns_query": "supporting_local_history"},
            "aggregation_policy": "separate_sources_no_sum",
        }
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            "sales_returns_query",
            message,
            screen_context,
            support_plan,
            registry_seed + registry_results,
        )
        for support_item in registry:
            if isinstance(support_item, dict):
                support_item["next_fallbacks"] = []
                support_item["next_fallbacks_human"] = []
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)

def _assistant_add_execution_fallbacks(
    state: dict[str, Any], client_id: str, tool_id: str, message: str, screen_context: Any,
    plan: dict[str, Any], meta: dict[str, Any], mode: str, permissions: Any,
    materialized_context: bool, has_records: bool,
) -> None:
    registry_seed = state["registry_seed"]
    raw_results, registry_results, warnings = state["raw_results"], state["registry_results"], state["warnings"]
    executed_tool_ids = state["executed_tool_ids"]
    previous_results = registry_seed
    if (
        not materialized_context
        and not has_records
        and tool_id != "operational_memory_query"
        and meta.get("zero_is_authoritative") is not True
    ):
        auth_failure = _assistant_non_retryable_auth_failure(warnings, registry_results)
        fallback_limit = 5 if mode in {"report", "daily"} else 3
        fallback_count = 0
        for fallback_id in _assistant_agent_fallback_ids(tool_id, message):
            if fallback_count >= fallback_limit:
                break
            if fallback_id in executed_tool_ids:
                continue
            fallback_meta = _assistant_tool_meta(fallback_id)
            if (
                not fallback_meta.get("id")
                or fallback_meta.get("read_only") is not True
                or not _assistant_tool_allowed(fallback_id, permissions)
            ):
                continue
            if auth_failure and fallback_meta.get("external") is True:
                continue
            executed_tool_ids.add(fallback_id)
            fallback_count += 1
            warnings.append(f"Fallback automatico apos dados insuficientes: {fallback_id}.")
            raw, registry, local_warnings = _assistant_execute_registry_tool(
                client_id,
                fallback_id,
                message,
                screen_context,
                {**plan, "selected_tools": [fallback_id]},
                registry_seed + registry_results,
            )
            raw_results.extend(raw)
            registry_results.extend(registry)
            warnings.extend(local_warnings)
            if any(int(item.get("records") or 0) > 0 for item in registry):
                break

def _assistant_finalize_tool_execution(
    state: dict[str, Any], client_id: str, tool_id: str, args: dict[str, Any], plan: dict[str, Any],
    permissions: Any, recent_cached: Optional[dict[str, Any]], strict_latest_ml_event: bool,
    external_cache_key: str, external_cache_ttl: int, audit_user: str,
) -> dict[str, Any]:
    raw_results, registry_results, warnings = state["raw_results"], state["registry_results"], state["warnings"]
    result_package = _assistant_agent_result_package(
        client_id,
        tool_id,
        args,
        raw_results,
        registry_results,
        warnings,
        permissions=permissions,
    )
    result_package["cache_hit"] = False
    strict_live_sale_lookup = bool(
        tool_id == "mercado_livre_orders"
        and (strict_latest_ml_event or bool(plan.get("id_pedido")))
    )
    if recent_cached is not None and not strict_live_sale_lookup and _assistant_retryable_api_error(result_package):
        cached_result = copy.deepcopy(recent_cached)
        cached_result["cache_hit"] = True
        cached_result["cache_fallback"] = True
        cached_result["cache_ttl_seconds"] = external_cache_ttl
        cached_result["evidence"] = mark_recent_cache(cached_result.get("evidence"))
        cached_warnings = list(cached_result.get("warnings") or [])
        cached_warnings.append(
            "A atualizacao da API falhou temporariamente; usei o cache recente de ate 2 minutos e mantive a fonte identificada."
        )
        cached_result["warnings"] = cached_warnings[:20]
        _assistant_api_query_audit(
            client_id,
            tool_id,
            plan,
            cached_result,
            audit_user=audit_user,
            cache_hit=True,
        )
        return cached_result
    _assistant_api_query_audit(
        client_id,
        tool_id,
        plan,
        result_package,
        audit_user=audit_user,
        cache_hit=False,
    )
    if external_cache_key and external_cache_ttl and not _assistant_api_error_code(result_package):
        result_package["cache_ttl_seconds"] = external_cache_ttl
        safe_payload = _assistant_cache_safe_payload(result_package)
        if isinstance(safe_payload, dict):
            try:
                _assistant_cache_set(client_id, external_cache_key, safe_payload)
            except Exception:
                # A consulta continua valida se o cache local estiver
                # temporariamente indisponivel.
                pass
    return result_package

def codex_assistant_execute_tool_call(
    client_id: str, tool_id: str, args: Optional[dict[str, Any]] = None, screen_context: Any = None,
    previous_results: Optional[list[dict[str, Any]]] = None, permissions: Any = None,
    audit_user: str = "", query_deadline: Optional[float] = None, materialized_context: bool = False,
) -> dict[str, Any]:
    """Execute one registered read-only data tool for the Codex agent loop."""
    tool_id = str(tool_id or "").strip()
    args = dict(args or {}) if isinstance(args, dict) else {}
    meta, error = _assistant_execution_access_error(tool_id, permissions)
    if error:
        return error
    plan, message, mode, strict_latest, force_refresh, query_deadline = _assistant_execution_plan(
        client_id, tool_id, args, screen_context, materialized_context, meta, query_deadline,
    )
    cache_key, cache_ttl, recent, cached = _assistant_execution_cache(
        client_id, tool_id, plan, meta, force_refresh, audit_user,
    )
    if cached is not None:
        return cached
    state = _assistant_execute_primary(client_id, tool_id, message, screen_context, plan, previous_results, permissions)
    has_records, needs_support = _assistant_primary_evidence_state(tool_id, state["registry_results"], strict_latest)
    _assistant_add_supporting_history(
        state, client_id, tool_id, message, screen_context, plan, permissions, previous_results,
        materialized_context, strict_latest, needs_support,
    )
    _assistant_add_execution_fallbacks(
        state, client_id, tool_id, message, screen_context, plan, meta, mode, permissions,
        materialized_context, has_records,
    )
    return _assistant_finalize_tool_execution(
        state, client_id, tool_id, args, plan, permissions, recent, strict_latest,
        cache_key, cache_ttl, audit_user,
    )


execute_tool_call = codex_assistant_execute_tool_call


def _assistant_execute_registry(
    client_id: str,
    message: str,
    screen_context: Any,
    mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    plan = _assistant_registry_plan(client_id, message, screen_context, mode)
    if any(str(item or "") in _ASSISTANT_AUDITED_API_TOOLS for item in (plan.get("selected_tools") or [])):
        plan["query_deadline"] = time.monotonic() + API_QUERY_TIMEOUT_SECONDS
    raw_results: list[dict[str, Any]] = []
    registry_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    executed: set[str] = set()

    def run(tool_id: str) -> None:
        if tool_id in executed:
            return
        executed.add(tool_id)
        raw, registry, local_warnings = _assistant_execute_registry_tool(
            client_id,
            tool_id,
            message,
            screen_context,
            plan,
            registry_results,
        )
        raw_results.extend(raw)
        registry_results.extend(registry)
        warnings.extend(local_warnings)
        if tool_id in _ASSISTANT_AUDITED_API_TOOLS:
            audited_item = next(
                (item for item in reversed(registry) if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id),
                {},
            )
            audit_summary = audited_item.get("summary") if isinstance(audited_item.get("summary"), dict) else {}
            selection = screen_context.get("selection") if isinstance(screen_context, dict) and isinstance(screen_context.get("selection"), dict) else {}
            _assistant_api_query_audit(
                client_id,
                tool_id,
                plan,
                {
                    "records": int(audited_item.get("records") or 0),
                    "warnings": list(local_warnings or []),
                    "summary": [{"tool_id": tool_id, "summary": audit_summary}],
                },
                audit_user=str(selection.get("username") or selection.get("user") or "system"),
                cache_hit=False,
            )

    for tool_id in plan.get("selected_tools") or []:
        run(str(tool_id))

    empty_sales = any(item.get("tool_id") == "sales_ranking" and int(item.get("records") or 0) == 0 for item in registry_results)
    if empty_sales:
        fallback_ids = ["integrations_status", "mercado_livre_listing"]
        if "bling" in _assistant_texto_norm(message):
            fallback_ids.insert(1, "bling_sales_orders")
        for fallback_id in fallback_ids:
            run(fallback_id)
        warnings.append(
            "Ranking de vendas local retornou vazio; foram executadas consultas read-only de diagnostico/fallback. "
            "Sincronizar pedidos ou atualizar bancos continua exigindo aprovacao."
        )
    source_tool_ids = {
        "source_discovery",
        "local_database_query",
        "local_csv_query",
        "local_cache_query",
        "sync_logs_query",
        "mercado_livre_readonly",
        "questions_post_sale_query",
        "fiscal_local_query",
        "operational_memory_query",
    }
    ignored_empty = {"capability_resolve", "program_action_match", "program_functions_catalog", "integrations_status", "operational_memory_query"}
    any_empty_specialized = any(
        item.get("tool_id") not in source_tool_ids
        and item.get("tool_id") not in ignored_empty
        and _assistant_tool_meta(str(item.get("tool_id") or "")).get("zero_is_authoritative") is not True
        and int(item.get("records") or 0) == 0
        for item in registry_results
    )
    if any_empty_specialized:
        text_norm = _assistant_texto_norm(message)
        fallback_ids = ["source_discovery"]
        if re.search(r"\b(log|erro|falha|sync|sincronizacao|nao aparecem|nao apareceu)\b", text_norm):
            fallback_ids.append("sync_logs_query")
        if re.search(r"\b(pergunta|perguntas|pos venda|pos-venda|mercado livre|mercadolivre|mlb\d+|anuncio)\b", text_norm):
            fallback_ids.extend(["questions_post_sale_query", "mercado_livre_readonly"])
        if re.search(r"\b(fiscal|ncm|cest|nota|nfe|imposto|tributacao)\b", text_norm):
            fallback_ids.append("fiscal_local_query")
        fallback_ids.extend(["local_database_query", "local_csv_query", "local_cache_query"])
        for fallback_id in list(dict.fromkeys(fallback_ids)):
            run(fallback_id)
        warnings.append(
            "Uma ou mais consultas especializadas retornaram vazio; executei fallbacks read-only em fontes locais "
            "e logs antes de finalizar a resposta."
        )
    plan["executed_tools"] = list(executed)
    plan["status_steps"] = list(dict.fromkeys((plan.get("status_steps") or []) + ["gerando resposta"]))
    return raw_results, registry_results, plan, warnings


def _assistant_registry_context_text(plan: dict[str, Any], registry_results: list[dict[str, Any]]) -> str:
    if not registry_results:
        return ""
    lines = [
        "Codex Data Tools Registry executado em modo read-only:",
        f"Intencao: {plan.get('intent') or '-'}",
        f"Periodo: {plan.get('data_inicio') or '-'} a {plan.get('data_fim') or '-'}",
        f"Loja/conta: {plan.get('loja') or 'todas'}",
    ]
    for item in registry_results[:30]:
        tool_label = item.get("tool_label") or _assistant_human_tool_label(item.get("tool_id"))
        source_label = item.get("source_label") or _assistant_human_source_label(item.get("source") or "", item.get("tool_id"))
        lines.append(
            f"- {tool_label} [{item.get('module')}]: {int(item.get('records') or 0)} registro(s); "
            f"fonte {source_label or '-'}; externo {'sim' if item.get('external') else 'nao'}."
        )
        if item.get("empty_reason"):
            lines.append(f"  vazio: {item.get('empty_reason')}")
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        if item.get("tool_id") == "sales_returns_query" and summary.get("context_text"):
            lines.append("  dados detalhados:")
            lines.append(str(summary.get("context_text") or "")[:CODEX_SALES_RETURNS_CONTEXT_CHAR_LIMIT])
            continue
        rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        if rows:
            lines.append("  amostra:")
            for row in rows[:8]:
                lines.append(
                    "  - "
                    + codex_turn_context.bounded_json(
                        row,
                        max_bytes=900,
                        priority_paths=("field", "value", "source", "coverage", "status", "reference"),
                    )
                )
        if summary:
            lines.append(
                "  resumo: "
                + codex_turn_context.bounded_json(
                    summary,
                    max_bytes=1200,
                    priority_paths=("field", "value", "source", "coverage", "status", "reference"),
                )
            )
    return "\n".join(lines)[:CODEX_DATA_CONTEXT_CHAR_LIMIT]
