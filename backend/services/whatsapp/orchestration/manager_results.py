"""Extracted WhatsApp bridge component: manager_results."""

from __future__ import annotations
import base64
import concurrent.futures
import hashlib
import heapq
import importlib.util
import itertools
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse
from zoneinfo import ZoneInfo
import requests
from fastapi import Header, HTTPException, Request
from backend.schemas import IAChatAttachment, IAChatRequest
from backend.services.whatsapp import formatting as whatsapp_formatting
from backend.services.whatsapp import gateway as whatsapp_gateway
from backend.services.whatsapp import intent as whatsapp_intent
from backend.services.whatsapp import media as whatsapp_media
from backend.services.whatsapp import marketplace_listing_delivery as whatsapp_marketplace_listing
from backend.services.whatsapp import message as whatsapp_message
from backend.services.whatsapp import report_scheduling as whatsapp_report_scheduling
from backend.services.whatsapp import retry_policy as whatsapp_retry_policy
from backend.services.whatsapp import settings as whatsapp_settings
from backend.services.whatsapp import tool_results as whatsapp_tool_results
from backend.services.whatsapp.contracts import (
    _QuestionResearchPending,
    WhatsappAdhocMessageRequest,
    WhatsappBindingRevokeRequest,
    WhatsappBridgeConfigRequest,
    WhatsappPairingCodeRequest,
    WhatsappPhoneRegistrationRequest,
    WhatsappPhoneSettingsRequest,
    WhatsappTemplatesRequest,
    WhatsappVoiceToggleRequest,
)
from backend.services import (
    admin_usuarios_common,
    codex_actions,
    codex_console,
    codex_whatsapp_agents,
    whatsapp_report_files,
    whatsapp_report_visuals,
    whatsapp_voice,
)
from backend.services.whatsapp_bridge_store import WhatsappBridgeStore

from backend.services.whatsapp.composition import (
    BridgeDependencies,
    bind_component_namespace,
    invoke_component,
)

WHATSAPP_MAX_OUTBOUND_IMAGES = whatsapp_media.WHATSAPP_MAX_OUTBOUND_IMAGES
WHATSAPP_PART_BODY_CHARS = whatsapp_formatting.WHATSAPP_PART_BODY_CHARS
WHATSAPP_MAX_PARTS = whatsapp_formatting.WHATSAPP_MAX_PARTS


def _format_stock_quantity(value: Any) -> str:
    return whatsapp_tool_results.format_stock_quantity(value)

def _positive_stock_sku_count_contract(result: Any) -> dict[str, Any]:
    return whatsapp_tool_results.positive_stock_sku_count_contract(result)

def _deterministic_positive_stock_sku_count_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    results = [
        item
        for item in list(evidence.get("tool_results") or [])
        if isinstance(item, dict) and str(item.get("tool_id") or "") == "bling_positive_stock_sku_count"
    ]
    if not results:
        return ""
    blocks: list[str] = []
    for result in results:
        details = _positive_stock_sku_count_contract(result)
        store = str(details.get("store") or result.get("manager_store") or "Loja consultada").strip()
        lines = [f"*{store}*"]
        if details.get("confirmed") is True:
            count = int(details.get("positive_sku_count") or 0)
            label = "SKU" if count == 1 else "SKUs"
            lines.append(f"*{count} {label}* {'esta' if count == 1 else 'estao'} com saldo positivo no estoque da loja.")
            if isinstance(details.get("store_available"), (int, float)) and not isinstance(details.get("store_available"), bool):
                quantity = _format_stock_quantity(details.get("store_available"))
                lines.append(f"Saldo total da loja fora do Full: *{quantity} unidade(s)*.")
            scanned = int(details.get("catalog_products_scanned") or 0)
            if scanned:
                lines.append(f"Catalogo verificado: {scanned} produto(s).")
            if details.get("full_excluded") is True:
                lines.append("Estoque Full/Fulfillment nao esta incluido nessa contagem.")
        else:
            lines.append("Nao consegui confirmar a contagem completa de SKUs com estoque nesta loja.")
            lines.append(f"Motivo: {str(details.get('reason') or 'a consulta retornou cobertura parcial')[:500]}.")
            scanned = int(details.get("catalog_products_scanned") or 0)
            returned = int(details.get("balances_returned") or 0)
            requested = int(details.get("balances_requested") or 0)
            if scanned:
                lines.append(f"Produtos verificados antes da interrupcao: {scanned}.")
            if requested:
                lines.append(f"Saldos retornados: {returned} de {requested} produto(s).")
            lines.append("Nenhum numero parcial foi apresentado como total exato.")
        lines.append("Fonte: API Bling (consulta somente leitura).")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)[:3500]

def _deterministic_stock_result_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    results = [
        item
        for item in list(evidence.get("tool_results") or [])
        if isinstance(item, dict)
        and str(item.get("tool_id") or "") in {"bling_stock_balances", "mercado_livre_listing", "stock_data"}
    ]
    if not results:
        return ""
    policy = (
        pending.get("manager_query_policy")
        if isinstance(pending.get("manager_query_policy"), dict)
        else pending.get("query_policy") if isinstance(pending.get("query_policy"), dict) else {}
    )
    requested_stores = [str(item or "").strip() for item in list(policy.get("stores") or []) if str(item or "").strip()]
    if not requested_stores and str(policy.get("store") or "").strip():
        requested_stores = [str(policy.get("store") or "").strip()]
    by_store: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for result in results:
        details = _stock_balance_contract(result) if str(result.get("tool_id") or "") == "bling_stock_balances" else {}
        store = str(result.get("manager_store") or details.get("store") or "Loja consultada").strip()
        key = _whatsapp_text_key(store)
        if key not in by_store:
            by_store[key] = (store, [])
        by_store[key][1].append(result)
    ordered: list[tuple[str, list[dict[str, Any]]]] = []
    used: set[str] = set()
    for store in requested_stores:
        key = _whatsapp_text_key(store)
        value = by_store.get(key)
        if value:
            ordered.append((store, value[1]))
            used.add(key)
    ordered.extend(value for key, value in by_store.items() if key not in used)

    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    sku = str(plan.get("sku") or _function_manager_extract_identifiers(pending.get("request_text"))[0] or "").strip()
    lines: list[str] = []
    confirmed_any = False
    partial_any = False
    for store, store_results in ordered:
        lines.append(f"*{store}*")
        bling_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "bling_stock_balances"),
            {},
        )
        ml_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "mercado_livre_listing"),
            {},
        )
        local_result = next(
            (item for item in store_results if str(item.get("tool_id") or "") == "stock_data"),
            {},
        )
        bling = _stock_balance_contract(bling_result)
        marketplace = _marketplace_listing_stock_contract(ml_result)
        local = _local_stock_contract(local_result)
        row_sku = str(bling.get("sku") or local.get("sku") or sku or "SKU consultado")
        if bling.get("confirmed") is True:
            confirmed_any = True
            quantity = _format_stock_quantity(bling.get("store_available"))
            lines.append(f"SKU {row_sku}: *{quantity} unidade(s)* no estoque da loja, confirmado diretamente na Bling.")
            if bling.get("full_excluded") is True:
                lines.append("Estoque Full/Fulfillment nao esta incluido nesse saldo.")
        elif marketplace.get("confirmed") is True:
            confirmed_any = True
            lines.append("A Bling nao confirmou o saldo; usei o Mercado Livre como segunda fonte.")
            for row in list(marketplace.get("rows") or [])[:4]:
                label = str(row.get("item_id") or row.get("sku") or row_sku or "anuncio")
                quantity = _format_stock_quantity(row.get("available_quantity"))
                lines.append(f"{label}: *{quantity} unidade(s) disponivel(is)* no anuncio.")
        elif local.get("confirmed") is True:
            partial_any = True
            quantity = _format_stock_quantity(local.get("store_available"))
            lines.append(
                f"Bling e Mercado Livre nao confirmaram o saldo desta loja. O JK Sistema registra *{quantity} "
                "unidade(s)* de saldo interno agregado para o SKU."
            )
            lines.append("Esse cadastro interno nao separa com seguranca o saldo por conta/loja; por isso a cobertura permanece parcial.")
        else:
            partial_any = True
            if bling.get("auth_failed") is True:
                lines.append(f"SKU {row_sku}: saldo nao confirmado. A conexao da Bling desta loja esta expirada e precisa ser refeita.")
            else:
                lines.append(f"SKU {row_sku}: saldo nao confirmado nas fontes disponiveis.")
            if bling.get("fallback_identified") is True:
                lines.append("O SKU foi localizado no cadastro interno, mas esse retorno nao comprova o saldo desta loja.")
        lines.append("")
    attempted_tools = {str(item.get("tool_id") or "") for item in results}
    attempted_sources = [
        label
        for tool_id, label in (
            ("bling_stock_balances", "Bling"),
            ("mercado_livre_listing", "Mercado Livre"),
            ("stock_data", "JK Sistema"),
        )
        if tool_id in attempted_tools
    ]
    if attempted_sources:
        lines.append("Fontes consultadas, em ordem de prioridade: " + "; ".join(attempted_sources) + ".")
    if partial_any:
        lines.append("Cobertura parcial: lojas sem saldo confiavel foram mantidas como nao confirmadas, nunca como estoque zero.")
    return "\n".join(lines).strip()[:3500]

def _deterministic_marketplace_sale_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    lookup_mode = str(guard.get("sales_lookup_mode") or "")
    if lookup_mode not in {"latest", "exact"}:
        return ""
    results = [
        item for item in list(evidence.get("tool_results") or [])
        if isinstance(item, dict) and str(item.get("tool_id") or "") == "mercado_livre_orders"
    ]
    blocks: list[str] = []
    for result in results:
        candidates = result.get("data") if isinstance(result.get("data"), list) else result.get("top_rows")
        orders = [item for item in candidates or [] if isinstance(item, dict) and str(item.get("order_id") or "")]
        if not orders:
            continue
        order = orders[0]
        store = str(order.get("store") or result.get("manager_store") or "Loja consultada").strip()
        raw_date = str(order.get("date_created") or order.get("date_closed") or "").strip()
        date_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", raw_date)
        sale_date = (
            f"{date_match.group(3)}/{date_match.group(2)}/{date_match.group(1)}"
            + (f" às {date_match.group(4)}:{date_match.group(5)}" if date_match.group(4) else "")
            if date_match else raw_date or "não informada"
        )
        lines = [
            f"*{store}*",
            "Última venda confirmada na API do Mercado Livre" if lookup_mode == "latest" else "Venda confirmada na API do Mercado Livre",
            f"Pedido: {str(order.get('order_id') or '').strip()}",
            f"Data: {sale_date}",
            f"Situação: {str(order.get('status') or 'não informada').strip()}",
            f"Valor pago: {whatsapp_formatting._whatsapp_money(order.get('paid_amount') or order.get('gross_amount'))}",
        ]
        for item in [value for value in order.get("items") or [] if isinstance(value, dict)][:8]:
            sku = str(item.get("sku") or item.get("item_id") or "sem SKU").strip()
            title = str(item.get("title") or "Produto sem título").strip()[:120]
            quantity = whatsapp_formatting._whatsapp_number(item.get("quantity") or 0)
            lines.append(f"- {sku}: {title} — {quantity} un.")
        buyer = str(order.get("buyer_name") or "").strip()
        city = str(order.get("buyer_city") or "").strip()
        if buyer or city:
            lines.append("Comprador: " + " — ".join(value for value in (buyer, city) if value))
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return ("\n\n".join(blocks) + "\n\nFonte: API oficial do Mercado Livre (orders).")[:3500]

def _deterministic_tool_result_text(evidence: dict[str, Any], pending: dict[str, Any]) -> str:
    plan = pending.get("manager_plan") if isinstance(pending.get("manager_plan"), dict) else {}
    guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    if guard.get("positive_stock_sku_count") is True:
        count_text = _deterministic_positive_stock_sku_count_text(evidence, pending)
        if count_text:
            return count_text
    if guard.get("explicit_stock") is True:
        stock_text = _deterministic_stock_result_text(evidence, pending)
        if stock_text:
            return stock_text
    sale_text = _deterministic_marketplace_sale_text(evidence, pending)
    if sale_text:
        return sale_text
    results = [item for item in list(evidence.get("tool_results") or []) if isinstance(item, dict)]
    if guard.get("listing_first") is True and whatsapp_marketplace_listing.delivery_requested(pending.get("request_text")):
        listing_text = whatsapp_marketplace_listing.format_listing_bundle(
            whatsapp_marketplace_listing.build_listing_bundle(results),
            pending.get("request_text") or "",
        )
        if listing_text:
            return listing_text
    by_store: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_store.setdefault(str(result.get("manager_store") or "").strip(), []).append(result)
    lines: list[str] = []
    for store, items in by_store.items():
        if len(by_store) > 1 or store:
            lines.append(f"*{store or 'Escopo consolidado'}*")
        for result in items:
            label = str(result.get("tool_label") or result.get("tool_id") or "Fonte consultada").replace("_", " ").strip()
            summary = result.get("summary")
            message = re.sub(r"\s+", " ", str(result.get("message") or "")).strip()
            if result.get("dados_suficientes") is not True:
                validation = result.get("tool_validation") if isinstance(result.get("tool_validation"), dict) else {}
                reason = str(result.get("empty_reason") or result.get("error") or validation.get("motivo") or "dados insuficientes")
                lines.append(f"{label}: dados insuficientes para confirmar a resposta.")
                lines.append(f"Limitacao: {reason[:500]}.")
                continue
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip()[:1600])
            elif message:
                lines.append(message[:1600])
            else:
                data = result.get("data")
                if isinstance(data, dict):
                    scalar = [
                        f"{str(key).replace('_', ' ')}: {value}"
                        for key, value in data.items()
                        if isinstance(value, (str, int, float, bool)) and str(value).strip()
                    ][:10]
                    lines.append((f"{label}: " + "; ".join(scalar))[:1800] if scalar else f"{label}: dados estruturados confirmados.")
                elif isinstance(data, list):
                    count = int(result.get("records") or len(data))
                    lines.append(f"{label}: {count} registro(s) confirmado(s).")
                else:
                    lines.append(f"{label}: consulta concluida.")
            if result.get("coverage_complete") is not True:
                reason = str(result.get("empty_reason") or result.get("error") or "cobertura parcial")
                lines.append(f"Limitacao: {reason[:500]}.")
        if store:
            lines.append("")
    sources = [str(item or "").strip() for item in list(evidence.get("sources") or []) if str(item or "").strip()]
    if sources:
        lines.append("Fontes: " + "; ".join(sources[:8]) + ".")
    if not lines:
        return _worker_result_fallback_text(evidence, pending)
    return "\n".join(lines).strip()[:3500]

def _whatsapp_report_metadata_text(request_text: Any, query_policy: Any, tool_results: Any) -> str:
    if not whatsapp_report_files.report_requested(request_text):
        return ""
    dataset = whatsapp_report_files.build_report_dataset(query_policy, tool_results)
    limitations = "; ".join(dataset.get("limitations") or []) or "nenhuma informada"
    return (
        f"Periodo: {dataset.get('period_start')} a {dataset.get('period_end')}\n"
        f"Lojas: {', '.join(dataset.get('stores') or []) or 'nao informadas'}\n"
        f"Fontes: {'; '.join(dataset.get('sources') or []) or 'nao informadas'}\n"
        f"Registros: {int(dataset.get('record_count') or 0)}\n"
        f"Cobertura: {'completa' if dataset.get('coverage_complete') else 'incompleta'}\n"
        f"Limitacoes: {limitations}"
    )[:1800]


_COMPONENT_FUNCTIONS = frozenset((
    '_format_stock_quantity',
    '_positive_stock_sku_count_contract',
    '_deterministic_positive_stock_sku_count_text',
    '_deterministic_stock_result_text',
    '_deterministic_tool_result_text',
    '_whatsapp_report_metadata_text'
))
_IMPLEMENTATIONS = {
    '_format_stock_quantity': _format_stock_quantity,
    '_positive_stock_sku_count_contract': _positive_stock_sku_count_contract,
    '_deterministic_positive_stock_sku_count_text': _deterministic_positive_stock_sku_count_text,
    '_deterministic_stock_result_text': _deterministic_stock_result_text,
    '_deterministic_tool_result_text': _deterministic_tool_result_text,
    '_whatsapp_report_metadata_text': _whatsapp_report_metadata_text
}


def bind_bridge_dependencies(dependencies: BridgeDependencies) -> None:
    bind_component_namespace(globals(), _IMPLEMENTATIONS, dependencies)


def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return invoke_component(_IMPLEMENTATIONS, name, args, kwargs)


__all__ = ["bind_bridge_dependencies", "invoke"]
