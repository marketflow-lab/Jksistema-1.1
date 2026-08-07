"""Evidence classification and stable tool-result contracts."""

from __future__ import annotations

import copy
import re
from typing import Any

from .catalog import _assistant_tool_allowed, _assistant_tool_meta
from .normalization import _assistant_is_generic_sales_api_query
from .runtime import _assistant_now, _assistant_texto_norm
from .settings import CODEX_AGENT_TEXT_VALUE_LIMIT, CODEX_AGENT_TOP_ROWS_LIMIT
from .source_labels import (
    _assistant_human_source_label,
    _assistant_human_source_list,
    _assistant_human_tool_label,
    _assistant_humanize_source_text,
)

EVIDENCE_SCHEMA = "jk.codex.evidence.v1"
CONCLUSIVE_EVIDENCE_STATUSES = frozenset({"complete", "confirmed_zero"})
_PRIMARY_API_TOOLS = frozenset({
    "bling_sales_orders", "bling_positive_stock_sku_count", "bling_stock_balances",
    "mercado_livre_resource_query", "mercado_livre_orders", "mercado_livre_returns",
    "mercado_livre_listing", "mercado_livre_visits", "mercado_livre_promotions",
    "mercado_livre_post_sale_detail", "mercado_livre_full_stock", "questions_post_sale_query",
})


def _assistant_agent_int(value: Any, default: int, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(minimum, min(parsed, maximum))


def _assistant_agent_compact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return str(value)[:160]
    if isinstance(value, str):
        return value.strip()[:CODEX_AGENT_TEXT_VALUE_LIMIT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_assistant_agent_compact(item, depth + 1) for item in value[:CODEX_AGENT_TOP_ROWS_LIMIT]]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key)[:80]
            if key_text in {"context_text", "tool_context", "tool_results_preview", "html", "raw", "payload"}:
                clean[key_text] = str(item or "")[:1200]
            else:
                clean[key_text] = _assistant_agent_compact(item, depth + 1)
        return clean
    return str(value)[:CODEX_AGENT_TEXT_VALUE_LIMIT]


def _summary(item: Any) -> dict[str, Any]:
    return item.get("summary") if isinstance(item, dict) and isinstance(item.get("summary"), dict) else {}


def _primary_items(tool_id: str, registry_results: Any) -> list[dict[str, Any]]:
    if not isinstance(registry_results, list):
        return []
    return [
        item for item in registry_results
        if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id
    ]


def _dlp_removed(item: dict[str, Any]) -> bool:
    summary = _summary(item)
    return any(
        int(container.get(key) or 0) > 0
        for container in (item, summary)
        for key in ("dlp_blocked_count", "removed_count", "sensitive_rows_removed")
    )


def _item_complete(item: dict[str, Any]) -> bool:
    summary = _summary(item)
    chart = summary.get("chart_data") if isinstance(summary.get("chart_data"), dict) else {}
    exact = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
    paging = summary.get("paging") if isinstance(summary.get("paging"), dict) else {}
    if (
        summary.get("error")
        or summary.get("partial_response") is True
        or summary.get("truncated") is True
        or paging.get("has_more") is True
        or _dlp_removed(item)
    ):
        return False
    return bool(
        summary.get("coverage_complete") is True
        or chart.get("coverage_complete") is True
        or exact.get("complete") is True
    )


def _error_state(warnings: Any, primary_items: list[dict[str, Any]]) -> tuple[str, bool, str]:
    values = [str(item or "") for item in warnings if str(item or "").strip()] if isinstance(warnings, list) else []
    for item in primary_items:
        summary = _summary(item)
        values.extend(str(summary.get(key) or "") for key in ("error", "status", "message", "detail"))
    text = _assistant_texto_norm(" ".join(values))
    if re.search(r"\b(token.*expir\w*|http 401|http 403|unauthorized|forbidden|autentic\w*|credencial\w*)\b", text):
        return "unavailable", False, "A autenticacao do provedor impediu a consulta."
    if re.search(
        r"\b(timeout|timed out|tempo limite|rate limited|rate_limited|http 5\d\d|"
        r"provider unavailable|provider_unavailable|api indisponivel|indisponivel)\b",
        text,
    ):
        return "unavailable", True, "O provedor ficou temporariamente indisponivel."
    if any(_summary(item).get("error") for item in primary_items):
        return "failed", False, "A fonte primaria retornou um erro interno ou um contrato invalido."
    return "", False, ""


def _special_contract(tool_id: str, primary_items: list[dict[str, Any]]) -> tuple[bool, str]:
    if tool_id == "stock_data":
        confirmed = any(
            summary.get("found") is True
            and str(summary.get("sku") or "").strip()
            and all(
                isinstance(summary.get(key), (int, float)) and not isinstance(summary.get(key), bool)
                for key in ("saldo_loja_total", "saldo_full_total", "saldo_total")
            )
            for summary in (_summary(item) for item in primary_items)
        )
        return confirmed, "numeric_stock_balance"
    if tool_id == "bling_positive_stock_sku_count":
        confirmed = any(
            isinstance(row, dict)
            and isinstance(row.get("positive_sku_count"), (int, float))
            and not isinstance(row.get("positive_sku_count"), bool)
            and row.get("coverage_complete") is True
            for item in primary_items
            for row in (item.get("rows") if isinstance(item.get("rows"), list) else [])
        )
        return confirmed, "complete_positive_sku_count"
    return False, ""


def _exact_contract(tool_id: str, primary_items: list[dict[str, Any]]) -> tuple[bool, bool]:
    if tool_id not in {"mercado_livre_orders", "mercado_livre_returns"}:
        return False, False
    candidates = [
        item for item in primary_items
        if isinstance(_summary(item).get("target"), dict)
        and isinstance(_summary(item).get("exact_coverage"), dict)
    ]
    if not candidates:
        return False, False
    complete = all(_item_complete(item) for item in candidates)
    matched = any((_summary(item).get("match") or {}).get("exact") is True for item in candidates)
    zero = sum(max(0, int(item.get("records") or 0)) for item in candidates) == 0
    return True, bool(complete and (matched or zero))


def _evidence_sources(
    tool_id: str,
    sources: list[str],
    registry_results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in registry_results:
        source = str(item.get("source") or "").strip() if isinstance(item, dict) else ""
        if not source or any(entry["id"] == source for entry in result):
            continue
        result.append({
            "id": source,
            "label": str(item.get("source_label") or _assistant_human_source_label(source, item.get("tool_id"))),
            "role": str(item.get("source_role") or "context"),
        })
    for source in sources:
        if source and not any(entry["id"] == source for entry in result):
            result.append({"id": source, "label": _assistant_human_source_label(source, tool_id), "role": "context"})
    return result[:20]


def _missing_fields(
    status: str,
    primary_records: int,
    sources: list[str],
    special_missing: str,
    special_complete: bool,
    live_incomplete: bool,
    exact_incomplete: bool,
    empty_reasons: list[str],
) -> list[str]:
    if status in CONCLUSIVE_EVIDENCE_STATUSES:
        return []
    missing: list[str] = []
    if primary_records <= 0:
        missing.append("records")
    if not sources:
        missing.append("sources")
    if special_missing and not special_complete:
        missing.append(special_missing)
    if live_incomplete:
        missing.append("store_coverage")
    if exact_incomplete:
        missing.append("conclusive_coverage")
    if empty_reasons or not missing:
        missing.append("decisive_evidence")
    return list(dict.fromkeys(missing))[:10]


def classify_evidence(
    *,
    tool_id: str,
    records: int,
    sources: list[str],
    warnings: list[str],
    empty_reasons: list[str],
    next_fallbacks: list[str],
    registry_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify what may be claimed independently from execution success."""

    meta = _assistant_tool_meta(tool_id)
    primary = _primary_items(tool_id, registry_results)
    primary_records = sum(max(0, int(item.get("records") or 0)) for item in primary)
    complete = bool(primary and all(_item_complete(item) for item in primary))
    error_status, retryable, error_reason = _error_state(warnings, primary)
    special_complete, special_missing = _special_contract(tool_id, primary)
    exact_query, exact_complete = _exact_contract(tool_id, primary)
    live_items = [item for item in primary if _summary(item).get("live_query")]
    if live_items:
        complete = all(_item_complete(item) for item in live_items)
    if special_missing:
        complete = special_complete
    if exact_query:
        complete = exact_complete

    if error_status and primary_records > 0:
        status = "partial"
        error_reason = ""
    else:
        status = error_status
    if not status and any(_dlp_removed(item) for item in primary):
        status = "partial"
    elif not status and complete and primary_records == 0 and meta.get("zero_is_authoritative") is True:
        status = "confirmed_zero"
    elif not status and complete and (primary_records > 0 or special_complete):
        status = "complete"
    elif not status and primary_records > 0:
        status = "partial"
    elif not status:
        status = "insufficient"

    reason = error_reason or {
        "complete": "A fonte primaria retornou registros com cobertura completa.",
        "confirmed_zero": "A fonte primaria autorizou zero registros com cobertura completa.",
        "partial": "Ha fatos observados, mas a cobertura nao permite uma conclusao geral.",
        "insufficient": "A execucao terminou sem evidencia decisiva.",
    }.get(status, "A evidencia nao pode ser classificada.")
    attempted = list(dict.fromkeys(
        str(item.get("tool_id") or "").strip()
        for item in registry_results
        if isinstance(item, dict)
        and str(item.get("tool_id") or "").strip()
        and str(item.get("tool_id") or "") != tool_id
    ))
    next_ids = next_fallbacks if status not in CONCLUSIVE_EVIDENCE_STATUSES else []
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": status,
        "claim_scope": "full" if status in CONCLUSIVE_EVIDENCE_STATUSES else "observed_only" if status == "partial" else "none",
        "coverage_complete": status in CONCLUSIVE_EVIDENCE_STATUSES,
        "confidence": "high" if status in CONCLUSIVE_EVIDENCE_STATUSES else "medium" if status == "partial" else "low",
        "freshness": "live" if meta.get("external") is True else "local_snapshot",
        "retryable": retryable,
        "reason": reason,
        "missing_fields": _missing_fields(
            status, primary_records, sources, special_missing, special_complete,
            bool(live_items and not complete), bool(exact_query and not exact_complete), empty_reasons,
        ),
        "sources": _evidence_sources(tool_id, sources, registry_results),
        "attempted_fallbacks": [
            {"tool_id": item, "label": _assistant_human_tool_label(item)} for item in attempted[:20]
        ],
        "next_sources": [
            {"tool_id": item, "label": _assistant_human_tool_label(item)} for item in next_ids[:10]
        ],
    }


def unavailable_evidence(reason: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "unavailable",
        "claim_scope": "none",
        "coverage_complete": False,
        "confidence": "low",
        "freshness": "live",
        "retryable": bool(retryable),
        "reason": str(reason or "A fonte solicitada esta indisponivel."),
        "missing_fields": ["decisive_evidence"],
        "sources": [],
        "attempted_fallbacks": [],
        "next_sources": [],
    }


def denied_evidence(reason: str) -> dict[str, Any]:
    result = unavailable_evidence(reason)
    result["status"] = "denied"
    result["freshness"] = "local_snapshot"
    return result


def failed_evidence(reason: str) -> dict[str, Any]:
    result = unavailable_evidence(reason)
    result["status"] = "failed"
    result["freshness"] = "local_snapshot"
    return result


def mark_recent_cache(evidence: Any) -> dict[str, Any]:
    result = copy.deepcopy(evidence) if isinstance(evidence, dict) else unavailable_evidence("Cache sem envelope valido.")
    result["schema"] = EVIDENCE_SCHEMA
    result["freshness"] = "recent_cache"
    result["coverage_complete"] = False
    result["claim_scope"] = "observed_only" if result.get("status") in {"complete", "confirmed_zero", "partial"} else "none"
    if result.get("claim_scope") == "observed_only":
        result["status"] = "partial"
        result["confidence"] = "medium"
    result["reason"] = "Cache recente usado apos falha da fonte; nao representa o estado atual."
    return result


def mark_dlp_partial(evidence: Any, removed_count: int) -> dict[str, Any]:
    result = copy.deepcopy(evidence) if isinstance(evidence, dict) else failed_evidence("Envelope de evidencia ausente.")
    if int(removed_count or 0) <= 0:
        return result
    result.update({
        "schema": EVIDENCE_SCHEMA,
        "status": "partial",
        "claim_scope": "observed_only",
        "coverage_complete": False,
        "confidence": "medium",
        "reason": "Uma ou mais linhas foram removidas pela politica DLP; a cobertura publica e parcial.",
    })
    missing = [str(item or "") for item in list(result.get("missing_fields") or []) if str(item or "")]
    result["missing_fields"] = list(dict.fromkeys([*missing, "dlp_filtered_rows"]))[:10]
    return result


def _assistant_agent_fallback_ids(tool_id: str, message: str) -> list[str]:
    text = _assistant_texto_norm(message)
    meta = _assistant_tool_meta(tool_id)
    fallbacks: list[str] = []

    def add(*items: str) -> None:
        for item in items:
            clean = str(item or "").strip()
            if clean and clean not in fallbacks:
                fallbacks.append(clean)

    add(*[str(item or "") for item in (meta.get("fallbacks") or [])])
    if re.search(r"\b(venda|vendas|pedido|pedidos|ranking|mais vendido|top)\b", text):
        add("sales_returns_query", "local_database_query", "local_csv_query", "sync_logs_query", "bling_sales_orders", "mercado_livre_readonly")
    if re.search(r"\b(estoque|saldo|ruptura|reposicao|sku)\b", text):
        add("stock_data", "product_data", "local_csv_query", "local_cache_query", "bling_products", "bling_stock_balances", "mercado_livre_listing")
    if re.search(r"\b(pergunta|perguntas|pos venda|pos-venda|mercado livre|mercadolivre|mlb\d+|anuncio)\b", text):
        add("questions_post_sale_query", "mercado_livre_readonly", "local_cache_query", "product_data")
    if re.search(r"\b(fiscal|ncm|cest|nota|nfe|imposto|tributacao)\b", text):
        add("fiscal_local_query", "product_registry", "local_csv_query", "bling_fiscal_product", "bling_fiscal_nfe")
    add("source_discovery")
    return fallbacks[:12]


def _result_details(
    tool_id: str,
    raw_results: list[dict[str, Any]],
    registry_results: list[dict[str, Any]],
    permissions: Any,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "rows": [], "primary_rows": [], "records": 0, "summaries": [], "sources": [],
        "source_labels": [], "empty_reasons": [], "next_fallbacks": [],
    }
    for item in registry_results:
        if not isinstance(item, dict):
            continue
        item_rows = item.get("rows") if isinstance(item.get("rows"), list) else []
        details["records"] += int(item.get("records") or 0)
        details["rows"].extend(item_rows)
        if str(item.get("tool_id") or "") == tool_id:
            details["primary_rows"].extend(item_rows)
        details["summaries"].append(_result_summary(item))
        source = str(item.get("source") or "").strip()
        if source and source not in details["sources"]:
            details["sources"].append(source)
        for label in _assistant_human_source_list(item.get("sources_human") or [source], item.get("tool_id")):
            if label and label not in details["source_labels"]:
                details["source_labels"].append(label)
        empty_reason = str(item.get("empty_reason") or "").strip()
        if empty_reason and empty_reason not in details["empty_reasons"]:
            details["empty_reasons"].append(empty_reason)
        for fallback in item.get("next_fallbacks") or []:
            fallback_id = str(fallback or "").strip()
            if fallback_id and fallback_id not in details["next_fallbacks"] and _assistant_tool_allowed(fallback_id, permissions):
                details["next_fallbacks"].append(fallback_id)
    if not details["sources"]:
        details["sources"] = list(dict.fromkeys(
            str(raw.get("function") or "").strip() for raw in raw_results
            if isinstance(raw, dict) and str(raw.get("function") or "").strip()
        ))
    if not details["source_labels"]:
        details["source_labels"] = _assistant_human_source_list(details["sources"], tool_id)
    return details


def _result_summary(item: dict[str, Any]) -> dict[str, Any]:
    source = str(item.get("source") or "")
    return {
        "tool_id": item.get("tool_id"),
        "tool_label": item.get("tool_label") or _assistant_human_tool_label(item.get("tool_id")),
        "module": item.get("module"),
        "records": int(item.get("records") or 0),
        "source": source,
        "source_label": item.get("source_label") or _assistant_human_source_label(source, item.get("tool_id")),
        "source_role": item.get("source_role") or "context",
        "aggregation_policy": item.get("aggregation_policy") or "standard",
        "periodo": item.get("periodo") or {},
        "loja": item.get("loja") or "",
        "summary": _assistant_agent_compact(_summary(item)),
    }


def _exact_metadata(tool_id: str, registry_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in registry_results:
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != tool_id:
            continue
        candidate = item.get("exact_metadata")
        if isinstance(candidate, dict) and candidate.get("exact_lookup") is True:
            return copy.deepcopy(candidate)
    return {}


def _chart_data(tool_id: str, raw_results: list[dict[str, Any]]) -> dict[str, Any]:
    aliases = {
        "get_mercado_livre_orders": "mercado_livre_orders",
        "get_mercado_livre_listing": "mercado_livre_listing",
        "get_mercado_livre_visits": "mercado_livre_visits",
        "get_mercado_livre_promotions": "mercado_livre_promotions",
        "codex_readonly_sources.mercado_livre_post_sale_detail": "mercado_livre_post_sale_detail",
        "get_days_without_sale_top": "stale_stock",
        "get_stockout_forecast": "stockout_forecast",
        "get_sales_by_period": "sales_ranking",
        "get_period_comparison": "period_comparison",
        "get_sales_timeseries": "sales_timeseries",
    }
    matching = [
        raw for raw in raw_results if isinstance(raw, dict)
        and aliases.get(str(raw.get("function") or "").strip(), str(raw.get("function") or "").strip()) == tool_id
    ]
    if not matching and len(raw_results) == 1:
        matching = list(raw_results)
    for raw in matching:
        payload = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        candidate = payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
        if candidate:
            return copy.deepcopy(candidate)
    return {}


def _source_contract(tool_id: str, args: dict[str, Any]) -> tuple[str, str]:
    generic_sales = _assistant_is_generic_sales_api_query(str(args.get("message") or args.get("mensagem") or ""))
    if tool_id in _PRIMARY_API_TOOLS:
        return "primary_api", "separate_sources_no_sum"
    if generic_sales and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}:
        return "supporting_local_history", "separate_sources_no_sum"
    return "context", "separate_sources_no_sum" if generic_sales else "standard"


def _assistant_agent_result_package(
    client_id: str,
    tool_id: str,
    args: dict[str, Any],
    raw_results: list[dict[str, Any]],
    registry_results: list[dict[str, Any]],
    warnings: list[str],
    permissions: Any = None,
) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    details = _result_details(tool_id, raw_results, registry_results, permissions)
    evidence = classify_evidence(
        tool_id=tool_id,
        records=details["records"],
        sources=details["sources"],
        warnings=warnings,
        empty_reasons=details["empty_reasons"],
        next_fallbacks=details["next_fallbacks"],
        registry_results=registry_results,
    )
    source_role, aggregation_policy = _source_contract(tool_id, args)
    primary_rows = details["primary_rows"]
    all_rows = (
        copy.deepcopy(primary_rows[:20000]) if tool_id == "mercado_livre_orders"
        else copy.deepcopy(primary_rows[:100]) if tool_id == "mercado_livre_listing"
        else []
    )
    return {
        "success": True,
        "client_id": str(client_id or ""),
        "tool_id": tool_id,
        "tool_label": _assistant_human_tool_label(tool_id),
        "module": meta.get("module") or "",
        "description": meta.get("description") or "",
        "external": bool(meta.get("external")),
        "read_only": meta.get("read_only") is True,
        "args": {} if tool_id == "context_hub_search" or meta.get("sensitive") is True else _assistant_agent_compact(args),
        "records": details["records"],
        "top_rows": _assistant_agent_compact(details["rows"][:CODEX_AGENT_TOP_ROWS_LIMIT]),
        "all_rows": all_rows,
        "chart_data": _chart_data(tool_id, raw_results),
        "exact_metadata": _exact_metadata(tool_id, registry_results),
        "summary": details["summaries"][:12],
        "sources": details["source_labels"][:20],
        "sources_raw": details["sources"][:20],
        "sources_human": details["source_labels"][:20],
        "source_label": (details["source_labels"][:1] or [_assistant_human_tool_label(tool_id)])[0],
        "source_role": source_role,
        "aggregation_policy": aggregation_policy,
        "warnings": [_assistant_humanize_source_text(str(item or "")[:600]) for item in warnings[:20]],
        "empty_reason": "; ".join(details["empty_reasons"])[:900],
        "evidence": evidence,
        "generated_at": _assistant_now(),
    }
