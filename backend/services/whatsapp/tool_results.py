"""Tool-result contracts, compaction and evidence consolidation."""

from __future__ import annotations

import json
import re
from typing import Any

from backend.services.whatsapp import formatting, retry_policy


def stock_balance_contract(result: Any) -> dict[str, Any]:
    """Extract only a store-scoped, reliable Bling balance from a tool wrapper."""
    source = result if isinstance(result, dict) else {}
    stored = source.get("stock_balance")
    if isinstance(stored, dict):
        return dict(stored)

    candidates: list[dict[str, Any]] = []
    summary = source.get("summary")
    if isinstance(summary, list):
        candidates.extend(item for item in summary if isinstance(item, dict))
    elif isinstance(summary, dict) and str(source.get("tool_id") or "") == "bling_stock_balances":
        candidates.append(source)
    for row in list(source.get("top_rows") or []):
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                "tool_id": "bling_stock_balances",
                "loja": row.get("loja") or row.get("store"),
                "summary": {
                    "chart_data": {
                        "totals": {
                            "skus": 1,
                            "store_available": row.get("saldo_loja_total"),
                            "gross_returned": row.get("saldo_bruto_retornado"),
                        },
                        "ranking": [
                            {
                                "sku": row.get("sku"),
                                "title": row.get("produto") or row.get("title"),
                                "quantity": row.get("saldo_loja_total"),
                                "quantity_reliable": row.get("cobertura_depositos_completa") is not False,
                            }
                        ],
                        "coverage_complete": row.get("cobertura_depositos_completa") is not False,
                    },
                    "partial": row.get("cobertura_depositos_completa") is False,
                },
            }
        )

    direct = next(
        (item for item in candidates if str(item.get("tool_id") or "") == "bling_stock_balances"),
        {},
    )
    payload = direct.get("summary") if isinstance(direct.get("summary"), dict) else {}
    chart = payload.get("chart_data") if isinstance(payload.get("chart_data"), dict) else {}
    totals = chart.get("totals") if isinstance(chart.get("totals"), dict) else {}
    ranking = [item for item in list(chart.get("ranking") or []) if isinstance(item, dict)]
    first = ranking[0] if ranking else {}
    quantity = totals.get("store_available")
    numeric_quantity = isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
    quantity_reliable = not ranking or first.get("quantity_reliable") is True
    confirmed = bool(
        numeric_quantity
        and int(totals.get("skus") or 0) > 0
        and chart.get("coverage_complete") is True
        and payload.get("partial") is not True
        and quantity_reliable
    )
    stores = [str(item or "").strip() for item in list(chart.get("stores") or []) if str(item or "").strip()]
    validation = source.get("tool_validation") if isinstance(source.get("tool_validation"), dict) else {}
    warnings = [str(item or "").strip() for item in list(validation.get("warnings") or []) if str(item or "").strip()]
    warning_key = " ".join(formatting._whatsapp_text_key(item) for item in warnings)
    auth_failed = bool(
        re.search(r"\b(token.*expir\w*|http 401|nao autoriz\w*|autentic\w*|credencial\w*)\b", warning_key)
    )
    fallback_identified = any(
        str(item.get("tool_id") or "") in {"stock_data", "product_data"}
        and isinstance(item.get("summary"), dict)
        and item.get("summary", {}).get("found") is True
        for item in candidates
    )
    if confirmed:
        reason = "Saldo numerico por loja confirmado diretamente na Bling."
        error_class = ""
    elif auth_failed:
        reason = "A autenticacao da Bling desta loja expirou; o saldo nao foi confirmado."
        error_class = "authentication"
    else:
        reason = "A consulta nao retornou saldo numerico confiavel para esta loja."
        error_class = "insufficient_evidence"
    return {
        "confirmed": confirmed,
        "store": str(direct.get("loja") or direct.get("store") or (stores[0] if stores else "")).strip(),
        "sku": str(first.get("sku") or source.get("sku") or "").strip(),
        "title": str(first.get("title") or first.get("produto") or "").strip(),
        "store_available": float(quantity) if numeric_quantity else None,
        "scope": str(payload.get("stock_scope") or "bling_non_full_only"),
        "full_excluded": str(payload.get("full_provider") or "") == "mercado_livre_api_only",
        "fallback_identified": fallback_identified,
        "auth_failed": auth_failed,
        "reason": reason,
        "error_class": error_class,
        "retryable": False,
    }


def normalize_tool_result_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {"success": False, "error": "resultado_invalido"}
    validation = source.get("tool_validation") if isinstance(source.get("tool_validation"), dict) else {}
    error = str(source.get("error") or source.get("failure") or source.get("empty_reason") or "").strip()
    error_class, retryable = retry_policy.retry_classification(error) if error else ("", False)
    data = source.get("data")
    if data is None:
        for key in ("result", "rows", "all_rows", "summary"):
            if source.get(key) not in (None, ""):
                data = source.get(key)
                break
    record_count = source.get("records")
    if not isinstance(record_count, (int, float)):
        if isinstance(data, list):
            record_count = len(data)
        elif isinstance(data, dict):
            rows = data.get("rows") or data.get("records") or data.get("items")
            record_count = len(rows) if isinstance(rows, list) else (1 if data else 0)
        else:
            record_count = 0
    explicit_sufficient = validation.get("dados_suficientes")
    if explicit_sufficient is None:
        explicit_sufficient = source.get("dados_suficientes")
    dados_suficientes = bool(
        explicit_sufficient is True
        or (
            explicit_sufficient is None
            and source.get("success") is True
            and int(record_count or 0) > 0
            and not error
        )
    )
    paging = source.get("paging") if isinstance(source.get("paging"), dict) else {}
    coverage_complete = bool(
        dados_suficientes
        and source.get("coverage_complete") is not False
        and source.get("partial_response") is not True
        and source.get("truncated") is not True
        and paging.get("has_more") is not True
    )
    raw_sources = source.get("sources") if isinstance(source.get("sources"), list) else []
    raw_human_sources = source.get("sources_human") if isinstance(source.get("sources_human"), list) else []
    sources: list[str] = []
    for value in [source.get("source_label"), source.get("source"), *raw_sources, *raw_human_sources]:
        text = str(value or "").strip()
        if text and text not in sources:
            sources.append(text[:500])
    return {
        "success": bool(source.get("success") is True and not error),
        "data": data,
        "sources": sources,
        "dados_suficientes": dados_suficientes,
        "coverage_complete": coverage_complete,
        "error_class": error_class or ("insufficient_evidence" if not dados_suficientes else ""),
        "retryable": bool(retryable),
        "records": int(record_count or 0),
    }


def function_manager_compact_result(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {"success": False, "error": "resultado_invalido"}
    compact: dict[str, Any] = {}
    for key in (
        "tool_id",
        "tool_label",
        "module",
        "success",
        "records",
        "source",
        "source_label",
        "sources",
        "sources_human",
        "warnings",
        "empty_reason",
        "tool_validation",
        "confidence",
        "paging",
        "generated_at",
        "error",
        "failure",
        "message",
        "summary",
        "data",
        "rows",
        "result",
        "top_rows",
    ):
        if key not in source:
            continue
        value = source.get(key)
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) > 12000:
            if isinstance(value, list):
                value = value[:20]
            elif isinstance(value, dict):
                value = {str(k): v for k, v in list(value.items())[:30]}
            serialized = json.dumps(value, ensure_ascii=False, default=str)[:12000]
            try:
                value = json.loads(serialized)
            except Exception:
                value = serialized
        compact[key] = value
    compact.update(normalize_tool_result_contract(source))
    if str(source.get("tool_id") or "") == "bling_stock_balances":
        stock_balance = stock_balance_contract(source)
        compact["stock_balance"] = stock_balance
        compact["dados_suficientes"] = stock_balance.get("confirmed") is True
        compact["coverage_complete"] = stock_balance.get("confirmed") is True
        compact["error_class"] = str(stock_balance.get("error_class") or "")
        compact["retryable"] = stock_balance.get("retryable") is True
    return compact


def marketplace_listing_stock_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {}
    rows = [item for item in list(source.get("top_rows") or []) if isinstance(item, dict)]
    confirmed_rows: list[dict[str, Any]] = []
    for row in rows:
        quantity = row.get("available_quantity")
        if quantity is None:
            quantity = row.get("variation_available_quantity")
        if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
            continue
        confirmed_rows.append(
            {
                "item_id": str(row.get("id") or row.get("item_id") or "").strip(),
                "sku": str(row.get("seller_sku") or row.get("sku") or "").strip(),
                "title": str(row.get("title") or row.get("titulo") or "").strip(),
                "available_quantity": float(quantity),
            }
        )
    confirmed = bool(
        source.get("dados_suficientes") is True
        and source.get("coverage_complete") is True
        and confirmed_rows
    )
    return {
        "confirmed": confirmed,
        "rows": confirmed_rows[:10],
        "reason": (
            "Saldo por anuncio confirmado na API do Mercado Livre."
            if confirmed
            else "O Mercado Livre nao retornou saldo numerico de anuncio com cobertura suficiente."
        ),
    }


def local_stock_contract(result: Any) -> dict[str, Any]:
    source = result if isinstance(result, dict) else {}
    stored = source.get("local_stock")
    if isinstance(stored, dict):
        return dict(stored)
    payload: dict[str, Any] = {}
    for item in list(source.get("summary") or []):
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != "stock_data":
            continue
        candidate = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        if candidate:
            payload = candidate
            break
    has_numeric_contract = bool(
        payload.get("found") is True
        and str(payload.get("sku") or "").strip()
        and all(
            isinstance(payload.get(key), (int, float)) and not isinstance(payload.get(key), bool)
            for key in ("saldo_loja_total", "saldo_full_total", "saldo_total")
        )
    )
    confirmed = bool(source.get("dados_suficientes") is True and has_numeric_contract)
    return {
        "confirmed": confirmed,
        "sku": str(payload.get("sku") or "").strip(),
        "title": str(payload.get("nome") or "").strip(),
        "store_available": payload.get("saldo_loja_total") if has_numeric_contract else None,
        "full_available": payload.get("saldo_full_total") if has_numeric_contract else None,
        "total_available": payload.get("saldo_total") if has_numeric_contract else None,
        "reason": (
            "Saldo agregado confirmado no cadastro interno do JK Sistema."
            if confirmed
            else "O JK Sistema nao retornou um contrato numerico de estoque para o SKU."
        ),
    }


def stock_tool_result_confirmed(result: dict[str, Any]) -> bool:
    tool_id = str(result.get("tool_id") or "")
    if tool_id == "bling_stock_balances":
        return stock_balance_contract(result).get("confirmed") is True
    if tool_id == "mercado_livre_listing":
        return marketplace_listing_stock_contract(result).get("confirmed") is True
    if tool_id == "stock_data":
        return local_stock_contract(result).get("confirmed") is True
    return False


def function_manager_evidence(plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    facts: list[str] = []
    sources: list[str] = []
    validations: list[dict[str, Any]] = []
    failures: list[str] = []
    required_ok = True
    sufficient_count = 0
    for result in results:
        validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
        sufficient = (
            result.get("dados_suficientes") is True
            if "dados_suficientes" in result
            else validation.get("dados_suficientes") is True
        )
        if sufficient:
            sufficient_count += 1
        if result.get("manager_required") is True and not sufficient:
            required_ok = False
        stock_balance = result.get("stock_balance") if isinstance(result.get("stock_balance"), dict) else {}
        validations.append(
            {
                "tool_id": str(result.get("tool_id") or ""),
                "required": result.get("manager_required") is True,
                "dados_suficientes": sufficient,
                "coverage_complete": result.get("coverage_complete") is True,
                "error_class": str(result.get("error_class") or "")[:100],
                "retryable": result.get("retryable") is True,
                "store": str(result.get("manager_store") or "")[:160],
                "motivo": str(
                    stock_balance.get("reason")
                    or validation.get("motivo")
                    or result.get("empty_reason")
                    or result.get("error")
                    or ""
                )[:1000],
            }
        )
        source_values = [
            result.get("source_label"),
            result.get("source"),
            str(result.get("tool_id") or "") if sufficient else "",
            *(result.get("sources_human") or [] if isinstance(result.get("sources_human"), list) else []),
        ]
        for value in source_values:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text[:1000])
        if result.get("error") or result.get("success") is False:
            failures.append(str(result.get("error") or result.get("empty_reason") or "falha_na_consulta")[:1000])
        payload = {
            key: result.get(key)
            for key in ("tool_id", "records", "summary", "data", "rows", "result")
            if result.get(key) not in (None, "", [], {})
        }
        if payload:
            facts.append(json.dumps(payload, ensure_ascii=False, default=str)[:6000])
    has_required = any(item.get("required") is True for item in validations)
    sufficient = bool(results and required_ok and (has_required or sufficient_count > 0))
    return {
        "status": "completed" if sufficient else "partial",
        "summary": (
            "Dados internos coletados pelo Luna Gerenciador."
            if results
            else "Nenhuma funcao interna aplicavel retornou dados."
        ),
        "verified_facts": facts[:30],
        "sources": sources[:30],
        "confidence": "high" if sufficient else ("medium" if sufficient_count else "low"),
        "evidence_sufficient": sufficient,
        "answerable": sufficient_count > 0,
        "coverage_complete": sufficient,
        "missing": [
            item["motivo"]
            for item in validations
            if item.get("required") and not item.get("dados_suficientes") and item.get("motivo")
        ][:20],
        "questions": [],
        "data_requests": [],
        "validations": validations,
        "failures": failures[:20],
        "tool_results": results,
        "plan": plan,
    }


def function_manager_merge_evidence(previous: Any, current: dict[str, Any]) -> dict[str, Any]:
    old = previous if isinstance(previous, dict) else {}
    merged = dict(current or {})
    for target, limit, item_limit in (
        ("verified_facts", 30, 6000),
        ("sources", 30, 1000),
        ("missing", 20, 1000),
        ("failures", 20, 1000),
    ):
        merged[target] = retry_policy.append_unique(
            list(old.get(target) or []),
            merged.get(target),
            limit=limit,
            item_limit=item_limit,
        )
    old_validations = [item for item in list(old.get("validations") or []) if isinstance(item, dict)]
    new_validations = [item for item in list(merged.get("validations") or []) if isinstance(item, dict)]
    by_signature: dict[str, dict[str, Any]] = {}
    for item in [*old_validations, *new_validations]:
        signature = json.dumps(
            {"tool_id": item.get("tool_id"), "required": item.get("required")},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        prior = by_signature.get(signature)
        if prior and prior.get("dados_suficientes") is True and item.get("dados_suficientes") is not True:
            continue
        by_signature[signature] = dict(item)
    merged["validations"] = list(by_signature.values())[:30]
    if old.get("evidence_sufficient") is True:
        merged["evidence_sufficient"] = True
        merged["coverage_complete"] = bool(old.get("coverage_complete") or merged.get("coverage_complete"))
        merged["status"] = "completed"
        if str(merged.get("confidence") or "") not in {"high", "medium"}:
            merged["confidence"] = str(old.get("confidence") or "high")
    merged["tool_results"] = [
        *[item for item in list(old.get("tool_results") or []) if isinstance(item, dict)],
        *[item for item in list(current.get("tool_results") or []) if isinstance(item, dict)],
    ][-30:]
    return merged


def format_stock_quantity(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "indisponivel"
    if number.is_integer():
        return str(int(number))
    return (f"{number:.3f}").rstrip("0").rstrip(".").replace(".", ",")
