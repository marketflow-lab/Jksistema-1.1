"""Runtime configuration, paths, cache and audit helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException, Request

from backend.services import codex_assistant_storage
from backend.services.runtime_bridge import current_backend_runtime

from .settings import CODEX_DATA_TOOLS_VERSION, EXTERNAL_CACHE_SECONDS

ASSISTANT_LOCK = threading.RLock()
API_QUERY_AUDIT_LOCK = threading.RLock()

_RUNTIME_MODULE: Any = None
_RUNTIME_BASE_DIR = ""
_RUNTIME_INFO_BASE = ""

def configure_codex_assistant_runtime(runtime_module=None):
    """Bind only paths and adapters explicitly used by this package."""

    global _RUNTIME_MODULE, _RUNTIME_BASE_DIR, _RUNTIME_INFO_BASE
    runtime = runtime_module or current_backend_runtime()
    if runtime is not None:
        _RUNTIME_MODULE = runtime
        _RUNTIME_BASE_DIR = str(getattr(runtime, "BASE_DIR", "") or "").strip()
        _RUNTIME_INFO_BASE = str(getattr(runtime, "PASTA_INFO", "") or "").strip()
    try:
        from backend.services import codex_readonly_sources

        codex_readonly_sources.configure_codex_readonly_sources_runtime(runtime)
    except Exception:
        pass
    try:
        from backend.services import codex_operational_memory

        codex_operational_memory.configure_codex_operational_memory_runtime(runtime)
    except Exception:
        pass
    return runtime


def _assistant_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _assistant_today() -> str:
    return date.today().isoformat()


def _assistant_base_dir() -> str:
    base = str(_RUNTIME_BASE_DIR or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _assistant_info_base() -> str:
    base_info = str(_RUNTIME_INFO_BASE or os.path.join(_assistant_base_dir(), "info")).strip()
    if not os.path.isabs(base_info):
        base_info = os.path.join(_assistant_base_dir(), base_info)
    os.makedirs(base_info, exist_ok=True)
    return base_info


def info_base() -> str:
    """Return the configured tenant information root."""

    return _assistant_info_base()


def _assistant_safe_id(value: str, fallback: str = "default") -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe or fallback


def _assistant_client_dir(client_id: str) -> str:
    path = os.path.join(_assistant_info_base(), _assistant_safe_id(client_id), "codex_assistant")
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, "reports"), exist_ok=True)
    os.makedirs(os.path.join(path, "cache"), exist_ok=True)
    return path


def _assistant_path(client_id: str, name: str) -> str:
    return os.path.join(_assistant_client_dir(client_id), name)


def _assistant_read_json(path: str, fallback: Any) -> Any:
    try:
        if not os.path.exists(path):
            return fallback
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data
    except Exception:
        return fallback


def _assistant_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(temp, path)


def _assistant_require_full_admin(request: Request, authorization: Optional[str]) -> dict[str, Any]:
    from backend.services.codex.console import security as console_security

    return console_security.require_full_admin(request, authorization)


def _assistant_cache_key(client_id: str, mode: str, message: str, screen_context: Any) -> str:
    contexto = {}
    if isinstance(screen_context, dict):
        contexto = {
            "page": screen_context.get("modulo_atual") or screen_context.get("page") or screen_context.get("pathname") or "",
            "periodo": screen_context.get("periodo") or "",
            "loja": screen_context.get("loja") or "",
            "data_inicio": screen_context.get("data_inicio") or "",
            "data_fim": screen_context.get("data_fim") or "",
        }
    raw = json.dumps(
        {"client_id": client_id, "mode": mode, "message": message, "context": contexto, "tools_version": CODEX_DATA_TOOLS_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _assistant_cache_get(client_id: str, key: str, ttl_seconds: int) -> Optional[dict[str, Any]]:
    try:
        return codex_assistant_storage.codex_assistant_cache_get(
            _assistant_info_base(),
            client_id,
            key,
            ttl_seconds,
        )
    except Exception:
        pass
    path = os.path.join(_assistant_client_dir(client_id), "cache", f"{key}.json")
    data = _assistant_read_json(path, None)
    if not isinstance(data, dict):
        return None
    created = float(data.get("_created_ts") or 0)
    if created <= 0 or time.time() - created > ttl_seconds:
        return None
    payload = data.get("payload")
    return payload if isinstance(payload, dict) else None


def _assistant_cache_set(client_id: str, key: str, payload: dict[str, Any]) -> None:
    codex_assistant_storage.codex_assistant_cache_set(
        _assistant_info_base(),
        client_id,
        key,
        payload,
        EXTERNAL_CACHE_SECONDS,
    )


_ASSISTANT_CACHE_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:access_?token|refresh_?token|token|secret|client_?secret|authorization|cookie|api_?key|password|senha)(?:$|_)",
    flags=re.IGNORECASE,
)


def _assistant_cache_safe_payload(value: Any, depth: int = 0) -> Any:
    """Copy a cache payload while dropping credential-shaped fields."""

    if depth > 10:
        return None
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "")[:120]
            if _ASSISTANT_CACHE_SECRET_KEY_RE.search(key_text):
                continue
            clean[key_text] = _assistant_cache_safe_payload(item, depth + 1)
        return clean
    if isinstance(value, list):
        return [_assistant_cache_safe_payload(item, depth + 1) for item in value[:1000]]
    if isinstance(value, tuple):
        return [_assistant_cache_safe_payload(item, depth + 1) for item in value[:1000]]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)[:2000]


def _assistant_external_cache_key(client_id: str, tool_id: str, plan: dict[str, Any]) -> str:
    safe_contract = {
        "client_id": str(client_id or "default"),
        "tool_id": str(tool_id or ""),
        "message": str(plan.get("message") or "")[:4000],
        "loja": str(plan.get("loja") or "")[:200],
        "data_inicio": str(plan.get("data_inicio") or ""),
        "data_fim": str(plan.get("data_fim") or ""),
        "status": str(plan.get("status") or "")[:120],
        "sku": str(plan.get("sku") or "")[:80],
        "item_id": str(plan.get("item_id") or "")[:40],
        "id_pedido": str(plan.get("id_pedido") or "")[:60],
        "pack_id": str(plan.get("pack_id") or "")[:60],
        "offset": int(plan.get("offset") or 0),
        "limite": int(plan.get("limite") or 0),
        "mode": str(plan.get("mode") or "")[:20],
        "max_paginas": int(plan.get("max_paginas") or 0),
        "incluir_detalhes": bool(plan.get("incluir_detalhes")),
        "incluir_comercial": bool(plan.get("incluir_comercial")),
        "dias": int(plan.get("dias") or 0),
        "promotion_id": str(plan.get("promotion_id") or "")[:120],
        "incluir_contagens": bool(plan.get("incluir_contagens")),
        "tools_version": CODEX_DATA_TOOLS_VERSION,
    }
    raw = json.dumps(safe_contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "external_tool_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


_ASSISTANT_AUDITED_API_TOOLS = {
    "bling_sales_orders",
    "bling_positive_stock_sku_count",
    "bling_stock_balances",
    "mercado_livre_orders",
    "mercado_livre_returns",
    "mercado_livre_listing",
    "mercado_livre_visits",
    "mercado_livre_promotions",
    "mercado_livre_post_sale_detail",
    "mercado_livre_full_stock",
    "questions_post_sale_query",
}


def _assistant_api_query_audit(
    client_id: str,
    tool_id: str,
    plan: dict[str, Any],
    result: dict[str, Any],
    *,
    audit_user: str = "",
    cache_hit: bool = False,
) -> None:
    """Append a credential-free audit event for commercial API reads."""

    if str(tool_id or "") not in _ASSISTANT_AUDITED_API_TOOLS:
        return
    provider = "bling" if str(tool_id).startswith("bling_") else "mercado_livre"
    summaries = result.get("summary") if isinstance(result.get("summary"), list) else []
    provider_status = "ok"
    fallback_used = False
    for item in summaries:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_role") or "") == "supporting_local_history":
            fallback_used = True
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        error = str(summary.get("error") or "").strip()
        status = str(summary.get("status") or "").strip()
        if error:
            provider_status = error[:80]
        elif status and status not in {"ok", "success"}:
            provider_status = status[:80]
        if summary.get("fallback_used") is True:
            fallback_used = True
    if any("fallback" in str(item or "").lower() for item in (result.get("warnings") or [])):
        fallback_used = True
    event = {
        "timestamp": _assistant_now(),
        "user": str(audit_user or "system")[:120],
        "tenant": str(client_id or "default")[:120],
        "store": str(plan.get("loja") or "")[:160],
        "provider": provider,
        "tool": str(tool_id or "")[:80],
        "filters": {
            "data_inicio": str(plan.get("data_inicio") or "")[:20],
            "data_fim": str(plan.get("data_fim") or "")[:20],
            "status": str(plan.get("status") or "")[:120],
            "sku": str(plan.get("sku") or "")[:80],
            "item_id": str(plan.get("item_id") or "")[:40],
            "id_pedido": str(plan.get("id_pedido") or "")[:60],
            "offset": int(plan.get("offset") or 0),
            "limit": int(plan.get("limite") or 0),
        },
        "method": "GET",
        "status": provider_status,
        "records": max(0, int(result.get("records") or 0)),
        "cache_hit": bool(cache_hit),
        "fallback_used": bool(fallback_used),
        "read_only": True,
    }
    exact = result.get("exact_metadata") if isinstance(result.get("exact_metadata"), dict) else {}
    if exact.get("exact_lookup") is True:
        event["exact_lookup"] = {
            "requested_id": str(exact.get("requested_id") or "")[:60],
            "identifier_type": str(exact.get("identifier_type") or "")[:20],
            "matched_stores": [str(item)[:160] for item in (exact.get("matched_stores") or [])[:20]],
            "resolved_order_ids": [str(item)[:60] for item in (exact.get("resolved_order_ids") or [])[:100]],
            "resources": [str(item)[:160] for item in (exact.get("resources") or [])[:30]],
            "stores_checked": [
                {
                    "store": str(item.get("store") or "")[:160],
                    "order_http": item.get("order_http"),
                    "pack_http": item.get("pack_http"),
                    "result": str(item.get("result") or "")[:80],
                }
                for item in (exact.get("searched_stores") or [])[:20]
                if isinstance(item, dict)
            ],
            "partial_response": bool(exact.get("partial_response")),
        }
    for item in summaries:
        if not isinstance(item, dict) or str(item.get("tool_id") or "") != str(tool_id or ""):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        target = summary.get("target") if isinstance(summary.get("target"), dict) else {}
        coverage = summary.get("exact_coverage") if isinstance(summary.get("exact_coverage"), dict) else {}
        match = summary.get("match") if isinstance(summary.get("match"), dict) else {}
        if not target or not coverage:
            continue
        event["exact_target"] = {
            "sku": str(target.get("sku") or "")[:80],
            "item_ids": [str(value)[:40] for value in (target.get("item_ids") or [])[:100]],
            "matched": bool(match.get("exact")),
            "matched_by": str(match.get("matched_by") or "")[:60],
            "coverage_complete": bool(coverage.get("complete")),
            "stop_reason": str(coverage.get("stop_reason") or "")[:80],
            "pages_fetched": int(coverage.get("pages_fetched") or 0),
            "claims_scanned": int(coverage.get("claims_scanned") or 0),
            "orders_inspected": int(coverage.get("orders_inspected") or 0),
            "error": str(summary.get("error") or "")[:80],
        }
        break
    try:
        path = _assistant_path(client_id, "api_query_audit.jsonl")
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        with API_QUERY_AUDIT_LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        logging.getLogger("codex_assistant.api_audit").exception("Falha ao registrar auditoria read-only")


def _assistant_api_error_code(result: Any) -> str:
    if not isinstance(result, dict):
        return "provider_unavailable"
    for item in result.get("summary") if isinstance(result.get("summary"), list) else []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        code = str(summary.get("error") or "").strip().lower()
        if code:
            return code
    return str(result.get("error_code") or result.get("error") or "").strip().lower()


def _assistant_retryable_api_error(result: Any) -> bool:
    code = _assistant_api_error_code(result)
    return bool(
        code in {"rate_limited", "timeout", "provider_unavailable"}
        or re.fullmatch(r"http_5\d\d", code or "")
    )


def _assistant_non_retryable_auth_failure(warnings: Any, registry_results: Any) -> bool:
    values = [str(item or "") for item in warnings if str(item or "").strip()] if isinstance(warnings, list) else []
    for item in registry_results if isinstance(registry_results, list) else []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        values.extend(str(summary.get(key) or "") for key in ("error", "message", "detail"))
    text = _assistant_texto_norm(" ".join(values))
    return bool(
        re.search(
            r"\b(token.*expir\w*|http 401|http 403|unauthorized|forbidden|nao autoriz\w*|sem permiss\w*|autentic\w*|credencial\w*)\b",
            text,
        )
    )


def _assistant_previous_contains_tool(previous_results: Any, tool_id: str) -> bool:
    for result in previous_results if isinstance(previous_results, list) else []:
        if not isinstance(result, dict):
            continue
        if str(result.get("tool_id") or "") == tool_id:
            return True
        for item in result.get("summary") if isinstance(result.get("summary"), list) else []:
            if isinstance(item, dict) and str(item.get("tool_id") or "") == tool_id:
                return True
    return False


def _assistant_context_page(screen_context: Any) -> str:
    if not isinstance(screen_context, dict):
        return "codex"
    page = (
        str(screen_context.get("modulo_atual") or "").strip()
        or str(screen_context.get("page") or "").strip()
        or str(screen_context.get("pathname") or "").strip().strip("/").split("/", 1)[0]
        or str(screen_context.get("title") or "").strip()
    )
    return page or "codex"


def _assistant_texto_norm(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _assistant_slug(value: str) -> str:
    text = _assistant_texto_norm(value)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _assistant_periodo_padrao(days: int = 30) -> tuple[str, str]:
    fim = date.today()
    inicio = fim - timedelta(days=max(1, int(days or 30)) - 1)
    return inicio.isoformat(), fim.isoformat()


def _assistant_call_ia_tool(func_name: str, *args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    try:
        from backend.services import ia as ia_service
        try:
            from backend.services import ia_tools_produtos, ia_tools_vendas

            fallback_logger = logging.getLogger("codex_assistant.ia_tools")
            for module in (ia_tools_produtos, ia_tools_vendas):
                if getattr(module, "logger", None) is None:
                    setattr(module, "logger", fallback_logger)
        except Exception:
            pass

        from backend.services.marketplace_tools.registry import (
            TOOL_EXECUTORS as MARKETPLACE_TOOL_EXECUTORS,
            resolve_executor as resolve_marketplace_executor,
        )
        from backend.services.sales_tools.registry import (
            TOOL_EXECUTORS as SALES_TOOL_EXECUTORS,
            resolve_executor as resolve_sales_executor,
        )

        if func_name in MARKETPLACE_TOOL_EXECUTORS:
            func = resolve_marketplace_executor(func_name)
        elif func_name in SALES_TOOL_EXECUTORS:
            func = resolve_sales_executor(func_name)
        else:
            func = getattr(ia_service, func_name, None)
        if not callable(func):
            return None
        result = func(*args, **kwargs)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        return {
            "function": func_name,
            "arguments": {},
            "result": {"error": str(exc)[:300], "read_only": True},
        }


def _assistant_tool_call_signature_error(raw: Any) -> bool:
    result = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else {}
    error = _assistant_texto_norm(str(result.get("error") or ""))
    return bool(
        "unexpected keyword" in error
        or "argumento de palavra-chave inesperado" in error
        or "positional argument" in error
        or "argumentos posicionais" in error
    )
