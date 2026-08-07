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

from .analysis_management import _assistant_management_analysis, _assistant_sources, _assistant_suggestions_from_results
from .dispatch import _assistant_dedupe_results, _assistant_direct_tools, _assistant_execute_dispatcher, _assistant_prompt_queries
from .execution import _assistant_execute_registry, _assistant_registry_context_text
from .routing import _assistant_source_routing_policy
from .runtime import _assistant_cache_get, _assistant_cache_key, _assistant_cache_set, _assistant_now, _assistant_texto_norm
from .settings import CODEX_DATA_CONTEXT_CHAR_LIMIT, CODEX_DATA_PREVIEW_CHAR_LIMIT, EXTERNAL_CACHE_SECONDS
from .source_labels import _assistant_humanize_source_text

def _assistant_collect_data(
    client_id: str,
    message: str,
    screen_context: Any = None,
    mode: str = "chat",
    force_refresh: bool = False,
) -> dict[str, Any]:
    mode = str(mode or "chat").strip().lower() or "chat"
    source_policy = _assistant_source_routing_policy(message)
    if source_policy.get("force_refresh"):
        force_refresh = True
    strict_api_route = bool(
        set(source_policy.get("forbidden_tools") or []).intersection(
            {"sales_returns_query", "sales_ranking", "sales_summary", "returns_summary", "return_rate"}
        )
    )
    key = _assistant_cache_key(client_id, mode, message, screen_context)
    if not force_refresh:
        cached = _assistant_cache_get(client_id, key, EXTERNAL_CACHE_SECONDS)
        if cached:
            cached["cache_hit"] = True
            return cached

    warnings: list[str] = []
    all_results: list[dict[str, Any]] = []
    tool_context_parts: list[str] = []
    registry_results: list[dict[str, Any]] = []
    tool_plan: dict[str, Any] = {}
    registry_raw, registry_results, tool_plan, registry_warnings = _assistant_execute_registry(client_id, message, screen_context, mode)
    all_results.extend(registry_raw)
    warnings.extend(registry_warnings)
    registry_context = _assistant_registry_context_text(tool_plan, registry_results)
    if registry_context:
        tool_context_parts.append(registry_context)
    broad_chat = bool(re.search(r"\b(analise|analisar|relatorio|melhoria|melhorias|oportunidade|vendas|estoque|ruptura|devolucao|devolucoes)\b", _assistant_texto_norm(message)))
    if not strict_api_route and (mode in {"proactive", "daily", "report"} or broad_chat):
        all_results.extend(_assistant_direct_tools(client_id, mode if mode in {"proactive", "daily", "report"} else "report"))

    if not strict_api_route:
        for query in _assistant_prompt_queries(message, mode):
            results, context_text, query_warnings = _assistant_execute_dispatcher(client_id, query, screen_context)
            all_results.extend(results)
            if context_text:
                tool_context_parts.append(context_text)
            warnings.extend(query_warnings)

    all_results = _assistant_dedupe_results(all_results)
    sources = _assistant_sources(all_results)
    suggestions = _assistant_suggestions_from_results(all_results, mode)
    if not tool_context_parts and all_results:
        tool_context_parts.append(
            codex_turn_context.bounded_json(
                all_results[:12],
                max_bytes=CODEX_DATA_PREVIEW_CHAR_LIMIT,
                priority_paths=("field", "value", "source", "coverage", "status", "reference"),
            )
        )

    payload = {
        "success": True,
        "enabled": True,
        "client_id": str(client_id or "default"),
        "mode": mode,
        "generated_at": _assistant_now(),
        "tool_results_count": len(all_results),
        "tool_results": all_results[:40],
        "registry_results": registry_results[:80],
        "tool_plan": tool_plan,
        "status_steps": tool_plan.get("status_steps") or [],
        "tool_context": "\n\n".join(tool_context_parts)[:CODEX_DATA_CONTEXT_CHAR_LIMIT],
        "tool_results_preview": codex_turn_context.bounded_json(
            all_results[:20],
            max_bytes=CODEX_DATA_PREVIEW_CHAR_LIMIT,
            priority_paths=("field", "value", "source", "coverage", "status", "reference"),
        ),
        "sources": sources,
        "warnings": [_assistant_humanize_source_text(item)[:600] for item in warnings[:10]],
        "suggestions": suggestions,
        "cache_hit": False,
    }
    payload["management_analysis"] = _assistant_management_analysis(payload)
    _assistant_cache_set(client_id, key, payload)
    return payload


def collect_data(
    client_id: str,
    message: str,
    screen_context: Any = None,
    mode: str = "chat",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Collect read-only data for one assistant request."""

    return _assistant_collect_data(
        client_id,
        message,
        screen_context,
        mode=mode,
        force_refresh=force_refresh,
    )
