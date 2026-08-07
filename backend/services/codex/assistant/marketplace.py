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

from .margin_analysis import _assistant_collect_margin_rows
from .references import _assistant_calendar_date
from .runtime import _assistant_info_base, _assistant_now, _assistant_texto_norm
from .utils import _assistant_float

def _assistant_profile_requires_marketplace(profile: str, prompt: str = "") -> bool:
    if profile in {"weekly_sales_stock", "daily_exceptions"}:
        return True
    if profile != "custom":
        return False
    normalized = _assistant_texto_norm(prompt)
    return any(
        token in normalized
        for token in ("margem", "lucro", "financeir", "rentabil", "tarifa", "frete", "custo mercado livre")
    )


def _assistant_connected_ml_stores(client_id: str, selected_store: str = "") -> list[str]:
    from backend.services.marketplace_tools import runtime as marketplace_runtime

    stores: list[str] = []
    for row in marketplace_runtime.load_stores(client_id) or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("nome") or "").strip()
        integrations = row.get("integracoes") if isinstance(row.get("integracoes"), dict) else {}
        cfg = integrations.get("mercadolivre") if isinstance(integrations.get("mercadolivre"), dict) else {}
        if name and str(cfg.get("access_token") or "").strip():
            stores.append(name)
    selected = str(selected_store or "").strip()
    if selected and _assistant_texto_norm(selected) not in {
        _assistant_texto_norm("todas"),
        _assistant_texto_norm("todas as lojas"),
        _assistant_texto_norm("__todas"),
    }:
        exact = [name for name in stores if _assistant_texto_norm(name) == _assistant_texto_norm(selected)]
        return exact
    return list(dict.fromkeys(stores))


def _assistant_ml_tool_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    return result if isinstance(result, dict) else {}


def _assistant_marketplace_fetchers(period_start: str, period_end: str) -> dict[str, Any]:
    from .marketplace_fetchers import build_marketplace_fetchers

    return build_marketplace_fetchers(period_start, period_end)


def _assistant_enrich_marketplace_ledger(
    client_id: str,
    advanced: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    from backend.services.codex_marketplace_margin import MarketplaceMarginLedger

    rows = [copy.deepcopy(item) for item in snapshot.get("ledger_rows") or [] if isinstance(item, dict)]
    if not rows:
        return snapshot
    cost_index: dict[tuple[str, str], dict[str, Any]] = {}
    for source_name in ("sales_rows", "inventory_rows"):
        for item in advanced.get(source_name) or []:
            if not isinstance(item, dict):
                continue
            key = (_assistant_texto_norm(item.get("store")), str(item.get("sku") or "").strip().upper())
            current = cost_index.setdefault(key, {})
            if current.get("unit_cost") is None and item.get("unit_cost") is not None:
                current["unit_cost"] = item.get("unit_cost")
            if current.get("tax_pct") is None and item.get("tax_pct") is not None:
                current["tax_pct"] = item.get("tax_pct")
    observed = snapshot.get("collected_at") or _assistant_now()
    observed_day_text = _assistant_calendar_date(str(observed)[:10])
    changed_by_store: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = (_assistant_texto_norm(row.get("store")), str(row.get("sku") or "").strip().upper())
        cost = cost_index.get(key) or {}
        if row.get("unit_cost") is None and cost.get("unit_cost") is not None:
            row["unit_cost"] = cost.get("unit_cost")
            row["cost_source"] = "cadastro_snapshot_reconciliacao"
            row["cost_observed_at"] = observed
        if row.get("tax_pct") is None and cost.get("tax_pct") is not None:
            row["tax_pct"] = cost.get("tax_pct")
            row["tax_source"] = "cadastro_snapshot_reconciliacao"
            row["tax_observed_at"] = observed
        order_day_text = _assistant_calendar_date(
            str(row.get("order_date") or row.get("date_created") or "")[:10]
        )
        near_sale_snapshot = False
        if order_day_text and observed_day_text:
            age_days = (date.fromisoformat(observed_day_text) - date.fromisoformat(order_day_text)).days
            near_sale_snapshot = 0 <= age_days <= 1
        if row.get("historical_cost_confirmed") is None:
            cost_scope = str(row.get("cost_scope") or row.get("cost_source") or "").strip().lower()
            row["historical_cost_confirmed"] = bool(
                near_sale_snapshot and row.get("unit_cost") is not None
            ) or cost_scope in {"order_historical", "ledger_historical"}
        if row.get("historical_tax_confirmed") is None:
            tax_scope = str(row.get("tax_scope") or row.get("tax_source") or "").strip().lower()
            row["historical_tax_confirmed"] = bool(
                near_sale_snapshot and row.get("tax_pct") is not None
            ) or tax_scope in {"order_historical", "ledger_historical"}
        both_historical_components_confirmed = (
            row.get("historical_cost_confirmed") is True
            and row.get("historical_tax_confirmed") is True
        )
        if not (both_historical_components_confirmed and row.get("historical_component_basis")):
            row["historical_component_basis"] = (
                "near_sale_reconciliation_snapshot"
                if near_sale_snapshot
                else "current_reconciliation_snapshot"
            )
        if row.get("pack_item_count") is None:
            row["pack_item_count"] = 2 if row.get("shipping_allocation_status") == "pack_multi_item_not_allocated" else 1
        required_components_known = all(
            row.get(field) is not None
            for field in ("unit_price", "unit_cost", "tax_pct", "shipping_seller_cost")
        )
        fee_known = row.get("sale_fee_amount") is not None or row.get("sale_fee_total") is not None
        row["reconciliation_state"] = "reconciled" if (
            required_components_known
            and fee_known
            and row.get("historical_cost_confirmed") is True
            and row.get("historical_tax_confirmed") is True
            and int(row.get("pack_item_count") or 1) <= 1
        ) else "partial"
        changed_by_store.setdefault(str(row.get("store") or ""), []).append(row)
    ledger = MarketplaceMarginLedger(_assistant_info_base(), client_id)
    for store_name, store_rows in changed_by_store.items():
        if store_name:
            ledger.upsert_orders(store_name, store_rows)
    scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
    snapshot["ledger_rows"] = ledger.list_rows(
        snapshot.get("stores") or [],
        scope.get("period_start"),
        scope.get("period_end"),
        None,
    )
    snapshot["order_rows"] = copy.deepcopy(snapshot["ledger_rows"])
    return snapshot


def _assistant_apply_advanced_profile(
    client_id: str,
    context: dict[str, Any],
    profile: str,
    *,
    store: str = "",
    import_list_id: str = "",
    force_refresh: bool = False,
    prompt: str = "",
) -> dict[str, Any]:
    from backend.services import codex_reports_advanced

    marketplace_required = _assistant_profile_requires_marketplace(profile, prompt)
    margin_rows = [] if marketplace_required else _assistant_collect_margin_rows(context, limit=500)
    advanced = codex_reports_advanced.build_profile_context(
        info_base=_assistant_info_base(),
        client_id=client_id,
        profile=profile,
        store=store,
        import_list_id=import_list_id,
        context=context,
        margin_rows=margin_rows,
    )
    if marketplace_required:
        stores = _assistant_connected_ml_stores(client_id, str(store or ""))
        scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
        relevant_skus = {
            str(item.get("sku") or "").strip().upper()
            for source_name in ("sales_rows", "inventory_rows")
            for item in (advanced.get(source_name) or [])
            if isinstance(item, dict) and str(item.get("sku") or "").strip()
        }
        if not stores:
            marketplace_snapshot = {
                "status": "unavailable",
                "collected_at": _assistant_now(),
                "stale": False,
                "stores": [],
                "listing_rows": [],
                "order_rows": [],
                "ledger_rows": [],
                "sources": [],
                "warnings": ["Nenhuma loja Mercado Livre conectada ficou disponivel para a coleta comercial."],
            }
        else:
            try:
                from backend.services.codex_marketplace_margin import collect_marketplace_commercial_snapshot

                fetchers = _assistant_marketplace_fetchers(
                    str(scope.get("period_start") or date.today().isoformat()),
                    str(scope.get("period_end") or date.today().isoformat()),
                )
                marketplace_snapshot = collect_marketplace_commercial_snapshot(
                    _assistant_info_base(),
                    client_id,
                    stores,
                    scope.get("period_start"),
                    scope.get("period_end"),
                    relevant_skus,
                    force_refresh=force_refresh,
                    listing_fetcher=fetchers["listing_fetcher"],
                    orders_fetcher=fetchers["orders_fetcher"],
                    shipping_fetcher=fetchers["shipping_fetcher"],
                    commercial_fetcher=fetchers["billing_fetcher"],
                    order_billing_fetcher=fetchers["order_billing_fetcher"],
                )
                marketplace_snapshot = _assistant_enrich_marketplace_ledger(client_id, advanced, marketplace_snapshot)
            except Exception as exc:
                marketplace_snapshot = {
                    "status": "unavailable",
                    "collected_at": _assistant_now(),
                    "stale": False,
                    "stores": stores,
                    "listing_rows": [],
                    "order_rows": [],
                    "ledger_rows": [],
                    "sources": [],
                    "warnings": [f"Coleta comercial Mercado Livre indisponivel: {type(exc).__name__}."],
                }
        advanced = codex_reports_advanced.apply_marketplace_commercial(advanced, marketplace_snapshot)
    context.update(advanced)
    scope = advanced.get("scope") if isinstance(advanced.get("scope"), dict) else {}
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    context["tool_plan"] = {
        **tool_plan,
        "data_inicio": scope.get("period_start") or tool_plan.get("data_inicio"),
        "data_fim": scope.get("period_end") or tool_plan.get("data_fim"),
        "comparison_start": scope.get("comparison_start"),
        "comparison_end": scope.get("comparison_end"),
        "loja": scope.get("selected_store") or "todas",
        "intent": profile,
    }
    quality_warnings = (advanced.get("data_quality") or {}).get("warnings") if isinstance(advanced.get("data_quality"), dict) else []
    if quality_warnings:
        context["warnings"] = list(dict.fromkeys(list(context.get("warnings") or []) + list(quality_warnings)))
    return context
