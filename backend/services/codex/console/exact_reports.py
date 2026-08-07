"""Codex console exact reports component."""

from __future__ import annotations


import copy
import base64
import importlib.util
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Optional

from fastapi import File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.services import (
    codex_actions,
    codex_agent_runtime,
    codex_ai_telemetry,
    codex_assistant_storage,
    codex_capabilities,
    codex_evaluations,
    codex_mcp_rollout,
    codex_model_router,
    codex_operational_memory,
    codex_turn_context,
)


CODEX_EXACT_ORDER_HISTORY_MARKER = "<!-- JK_EXACT_ORDER_FULL_HISTORY -->"


def _codex_exact_value(value: Any, fallback: str = "indisponível") -> str:
    if value is None:
        return fallback
    text = " ".join(str(value).split()).strip()
    return text if text else fallback


def _codex_exact_money(value: Any) -> str:
    return "indisponível" if value is None or value == "" else _codex_whatsapp_money(value)


def _codex_exact_message_lines(messages: Any, seen: set[tuple[str, str, str]]) -> list[str]:
    lines: list[str] = []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        text = str(message.get("text") or "").strip()
        attachments = [
            _codex_whatsapp_inline(item.get("name") if isinstance(item, dict) else item)
            for item in (message.get("attachments") or [])
        ]
        attachments = [item for item in attachments if item]
        key = (
            str(message.get("message_id") or message.get("date") or ""),
            str(message.get("role") or ""),
            text,
        )
        if key in seen:
            continue
        seen.add(key)
        label = _codex_exact_value(message.get("label"), "Participante")
        date_label = _codex_whatsapp_report_date(message.get("date")) or "data indisponível"
        if text:
            quoted = "\n> ".join(line.rstrip() for line in text.replace("\r", "").split("\n"))
            lines.append(f"- {date_label} — **{label}:**\n> {quoted}")
        else:
            lines.append(f"- {date_label} — **{label}:** mensagem sem texto disponível")
        if attachments:
            lines.append("  Anexos: " + ", ".join(attachments))
        moderation = _codex_whatsapp_inline(message.get("moderation_status"))
        if moderation and moderation.lower() not in {"available", "clean"}:
            lines.append(f"  Moderação: {moderation}")
    return lines


def _codex_exact_status_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "confirmed": "Confirmado",
        "payment_required": "Aguardando pagamento",
        "payment_in_process": "Pagamento em análise",
        "partially_paid": "Parcialmente pago",
        "paid": "Pago",
        "partially_refunded": "Parcialmente reembolsado",
        "pending_cancel": "Cancelamento pendente",
        "cancelled": "Cancelado",
        "invalid": "Inválido",
    }.get(key, _codex_exact_value(value))


def _codex_exact_shipment_label(shipment: dict[str, Any]) -> str:
    state = str(shipment.get("delivery_state") or "").strip().lower()
    return {
        "delivered": "Entregue",
        "in_transit": "Em trânsito",
        "preparing": "Em preparação",
        "not_delivered": "Não entregue",
        "cancelled": "Cancelado",
        "unavailable": "Indisponível",
    }.get(state, _codex_exact_value(shipment.get("delivery_state_label")))


def _codex_exact_logistics_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return {
        "fulfillment": "Mercado Full",
        "cross_docking": "Cross docking",
        "xd_drop_off": "Cross docking com postagem",
        "drop_off": "Postagem em agência",
        "self_service": "Mercado Envios Flex",
        "custom": "Logística própria",
    }.get(key, _codex_exact_value(value))


def _codex_exact_quantity(value: Any) -> str:
    if value is None or value == "":
        return "indisponível"
    return _codex_whatsapp_number(value)


def _codex_append_whatsapp_order_summary(
    lines: list[str],
    order: dict[str, Any],
    requested_id: str,
    index: int,
    multiple_orders: bool,
) -> None:
    order_id = _codex_exact_value(order.get("order_id"))
    pack_id = _codex_exact_value(order.get("pack_id"), "")
    if multiple_orders:
        lines.extend(["", f"## Pedido {index} — {order_id}"])
    elif order_id != requested_id:
        lines.append(f"Pedido: **{order_id}**")

    sale_date = _codex_whatsapp_report_date(order.get("date_created"))
    closed_date = _codex_whatsapp_report_date(order.get("date_closed"))
    lines.extend([
        f"**Status:** {_codex_exact_status_label(order.get('status'))}",
        f"**Data da venda:** {_codex_exact_value(sale_date)}",
    ])
    if closed_date and closed_date != sale_date:
        lines.append(f"**Fechamento:** {closed_date}")
    if pack_id and pack_id not in {order_id, requested_id}:
        lines.append(f"**Pack:** {pack_id}")

    buyer = _codex_exact_value(order.get("buyer_name"), "")
    nickname = _codex_exact_value(order.get("buyer_nickname"), "")
    if buyer or nickname:
        buyer_label = buyer or nickname
        if buyer and nickname and buyer.lower() != nickname.lower():
            buyer_label = f"{buyer} • usuário ML {nickname}"
        lines.append(f"**Comprador:** {buyer_label}")

    lines.extend(["", "## Produto"])
    items = [item for item in (order.get("items") or []) if isinstance(item, dict)]
    if not items:
        lines.append("Produto indisponível na resposta da API.")
    for item in items:
        quantity = _codex_exact_quantity(item.get("quantity"))
        sku = _codex_exact_value(item.get("sku"))
        title = _codex_exact_value(item.get("title"), "Produto sem título")
        lines.append(f"• **{quantity}x • SKU {sku}** — {title}")
        price_line = f"  {_codex_exact_money(item.get('unit_price'))} por unidade"
        try:
            show_subtotal = float(item.get("quantity") or 0) > 1
        except (TypeError, ValueError):
            show_subtotal = False
        if show_subtotal:
            price_line += f" • subtotal {_codex_exact_money(item.get('gross_amount'))}"
        lines.append(price_line)

    lines.extend([
        "",
        "## Valores",
        f"• **Venda:** {_codex_exact_money(order.get('gross_amount'))}",
        f"• **Pago:** {_codex_exact_money(order.get('paid_amount'))}",
        f"• **Reembolsado:** {_codex_exact_money(order.get('refund_amount'))}",
        f"• **Líquido:** {_codex_exact_money(order.get('net_amount'))}",
    ])


def _codex_append_whatsapp_order_delivery(
    lines: list[str],
    order: dict[str, Any],
    seen_post_sale: set[tuple[str, str, str]],
) -> None:
    shipment = order.get("shipment") if isinstance(order.get("shipment"), dict) else {}
    fulfillment = order.get("fulfillment") if isinstance(order.get("fulfillment"), dict) else {}
    is_full = fulfillment.get("is_full")
    full_label = "Sim" if is_full is True else ("Não" if is_full is False else "Indisponível")
    logistic_type = fulfillment.get("logistic_type") or shipment.get("logistic_type")
    delivery_date = shipment.get("date_delivered") or shipment.get("estimated_delivery")
    lines.extend([
        "",
        "## Envio",
        f"• **Situação:** {_codex_exact_shipment_label(shipment)}",
        f"• **Logística:** {_codex_exact_logistics_label(logistic_type)}",
        f"• **Full:** {full_label}",
    ])
    if shipment.get("shipment_id"):
        lines.append(f"• **Código do envio:** {_codex_exact_value(shipment.get('shipment_id'))}")
    if delivery_date:
        lines.append(f"• **Entrega/previsão:** {_codex_whatsapp_report_date(delivery_date)}")

    claims = [claim for claim in (order.get("claims") or []) if isinstance(claim, dict)]
    claims_status = order.get("claims_status") if isinstance(order.get("claims_status"), dict) else {}
    return_status = order.get("return_status") if isinstance(order.get("return_status"), dict) else {}
    conversations = order.get("conversations") if isinstance(order.get("conversations"), dict) else {}
    post_sale = conversations.get("post_sale") if isinstance(conversations.get("post_sale"), dict) else {}
    total_messages = conversations.get("total_messages")
    claims_label = (
        "nenhuma"
        if claims_status.get("available") is True and not claims
        else str(len(claims))
        if claims
        else "indisponível"
    )
    return_label = _codex_exact_value(return_status.get("label"))
    if total_messages is None:
        messages_label = "indisponível"
    elif int(total_messages or 0) > 0:
        messages_label = str(int(total_messages or 0))
    elif conversations.get("complete") is True and post_sale.get("available") is True:
        messages_label = "nenhuma"
    else:
        messages_label = "indisponível"
    lines.extend([
        "",
        "## Pós-venda",
        f"• **Reclamações:** {claims_label}",
        f"• **Devolução:** {return_label}",
        f"• **Mensagens:** {messages_label}",
    ])

    for claim in claims:
        detail = claim.get("detail") if isinstance(claim.get("detail"), dict) else {}
        lines.append(
            f"• **Reclamação {_codex_exact_value(claim.get('claim_id'))}:** "
            f"{_codex_exact_value(detail.get('title') or claim.get('reason_id'))} — "
            f"{_codex_exact_value(claim.get('status'))}"
        )

    post_lines = _codex_exact_message_lines(post_sale.get("messages"), seen_post_sale)
    if post_lines:
        lines.extend(["", "## Histórico pós-venda", *post_lines])
    for claim in claims:
        conversation = claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}
        claim_lines = _codex_exact_message_lines(conversation.get("messages"), set())
        if claim_lines:
            lines.extend([
                "",
                f"## Histórico da reclamação {_codex_exact_value(claim.get('claim_id'))}",
                *claim_lines,
            ])


def _codex_append_desktop_order(
    lines: list[str],
    order: dict[str, Any],
    index: int,
    matched_stores: list[str],
    seen_post_sale: set[tuple[str, str, str]],
) -> None:
    order_id = _codex_exact_value(order.get("order_id"))
    pack_id = _codex_exact_value(order.get("pack_id"))
    store = _codex_exact_value(order.get("store"), matched_stores[0] if matched_stores else "indisponível")
    lines.extend([
        "",
        f"## Pedido {index} — order {order_id}",
        f"- **Loja:** {store}",
        f"- **Pack:** {pack_id}",
        f"- **Status do pedido:** {_codex_exact_value(order.get('status'))}",
        f"- **Data da venda:** {_codex_exact_value(_codex_whatsapp_report_date(order.get('date_created')))}",
        f"- **Data de fechamento:** {_codex_exact_value(_codex_whatsapp_report_date(order.get('date_closed')))}",
    ])
    buyer = _codex_exact_value(order.get("buyer_name"), "")
    nickname = _codex_exact_value(order.get("buyer_nickname"), "")
    buyer_label = buyer or nickname or "indisponível"
    if buyer and nickname and nickname.lower() != buyer.lower():
        buyer_label = f"{buyer} ({nickname})"
    lines.append(f"- **Comprador:** {buyer_label}")
    lines.extend([
        f"- **Total da venda:** {_codex_exact_money(order.get('gross_amount'))}",
        f"- **Valor pago:** {_codex_exact_money(order.get('paid_amount'))}",
        f"- **Valor reembolsado:** {_codex_exact_money(order.get('refund_amount'))}",
        f"- **Valor líquido:** {_codex_exact_money(order.get('net_amount'))}",
        "",
        "### Produtos",
    ])
    items = [item for item in (order.get("items") or []) if isinstance(item, dict)]
    if not items:
        lines.append("- Produtos indisponíveis na resposta da API.")
    for item in items:
        variation = ", ".join(
            f"{_codex_exact_value(attribute.get('name'))}: {_codex_exact_value(attribute.get('value'))}"
            for attribute in (item.get("variation_attributes") or [])
            if isinstance(attribute, dict)
        )
        lines.extend([
            f"- **{_codex_exact_value(item.get('title'), 'Produto sem título')}**",
            f"  SKU: {_codex_exact_value(item.get('sku'))} | Item: {_codex_exact_value(item.get('item_id'))}",
            f"  Quantidade: {_codex_exact_value(item.get('quantity'))} | Preço unitário: {_codex_exact_money(item.get('unit_price'))}",
        ])
        if variation:
            lines.append(f"  Variação: {variation}")

    shipment = order.get("shipment") if isinstance(order.get("shipment"), dict) else {}
    fulfillment = order.get("fulfillment") if isinstance(order.get("fulfillment"), dict) else {}
    is_full = fulfillment.get("is_full")
    full_label = "Sim" if is_full is True else ("Não" if is_full is False else "Indisponível")
    delivery_date = shipment.get("date_delivered") or shipment.get("estimated_delivery")
    lines.extend([
        "",
        "### Envio",
        f"- **Atendido pelo Full:** {full_label}",
        f"- **Situação:** {_codex_exact_value(shipment.get('delivery_state_label'))}",
        f"- **Status/substatus:** {_codex_exact_value(shipment.get('status'))} / {_codex_exact_value(shipment.get('substatus'))}",
        f"- **Entrega ou previsão:** {_codex_exact_value(_codex_whatsapp_report_date(delivery_date))}",
    ])

    return_status = order.get("return_status") if isinstance(order.get("return_status"), dict) else {}
    claims = [claim for claim in (order.get("claims") or []) if isinstance(claim, dict)]
    lines.extend([
        "",
        "### Reclamações e devolução",
        f"- **Devolução:** {_codex_exact_value(return_status.get('label'))}",
        f"- **Reclamações encontradas:** {len(claims)}",
    ])
    for claim in claims:
        detail = claim.get("detail") if isinstance(claim.get("detail"), dict) else {}
        lines.extend([
            f"- **Reclamação { _codex_exact_value(claim.get('claim_id')) }:** {_codex_exact_value(claim.get('status'))}",
            f"  Motivo: {_codex_exact_value(detail.get('title') or claim.get('reason_id'))}",
            f"  Situação: {_codex_exact_value(claim.get('stage'))} | Atualização: {_codex_exact_value(_codex_whatsapp_report_date(claim.get('last_updated')))}",
        ])
        if detail.get("description") or detail.get("problem"):
            lines.append("  Detalhe: " + _codex_exact_value(detail.get("description") or detail.get("problem")))

    conversations = order.get("conversations") if isinstance(order.get("conversations"), dict) else {}
    post_sale = conversations.get("post_sale") if isinstance(conversations.get("post_sale"), dict) else {}
    post_lines = _codex_exact_message_lines(post_sale.get("messages"), seen_post_sale)
    lines.extend(["", "### Histórico pós-venda do pack"])
    if post_lines:
        lines.extend(post_lines)
    elif post_sale.get("available") is True:
        lines.append("Nenhuma mensagem pós-venda foi retornada pela API.")
    else:
        lines.append("Histórico pós-venda indisponível na API.")

    for claim in claims:
        claim_id = _codex_exact_value(claim.get("claim_id"))
        conversation = claim.get("conversation") if isinstance(claim.get("conversation"), dict) else {}
        claim_lines = _codex_exact_message_lines(conversation.get("messages"), set())
        lines.extend(["", f"### Histórico da reclamação {claim_id}"])
        if claim_lines:
            lines.extend(claim_lines)
        elif conversation.get("available") is True:
            lines.append("Nenhuma mensagem da reclamação foi retornada pela API.")
        else:
            lines.append("Histórico da reclamação indisponível na API.")


def _codex_exact_ml_order_whatsapp_response(
    metadata: dict[str, Any],
    orders: list[dict[str, Any]],
) -> str:
    """Ficha móvel única; detalhes técnicos repetidos ficam fora do WhatsApp."""
    requested_id = _codex_exact_value(metadata.get("requested_id"), "não informado")
    stores = [str(item).strip() for item in (metadata.get("matched_stores") or []) if str(item).strip()]
    store_label = ", ".join(stores) or "loja indisponível"
    lines = [
        CODEX_EXACT_ORDER_HISTORY_MARKER,
        f"# Venda {requested_id} — {store_label}",
    ]
    multiple_orders = len(orders) > 1
    seen_post_sale: set[tuple[str, str, str]] = set()

    for index, order in enumerate(orders, start=1):
        _codex_append_whatsapp_order_summary(lines, order, requested_id, index, multiple_orders)
        _codex_append_whatsapp_order_delivery(lines, order, seen_post_sale)

    partial = metadata.get("partial_response") is True or metadata.get("coverage_complete") is False
    lines.extend([
        "",
        (
            "⚠️ **Cobertura parcial:** alguma seção não foi disponibilizada pela API."
            if partial
            else "_Fonte: API do Mercado Livre • consulta atual • cobertura completa._"
        ),
    ])
    return "\n".join(lines).strip()


def _codex_exact_ml_order_response(
    task: dict[str, Any],
    results: list[dict[str, Any]],
    ai_summary: str = "",
) -> str:
    """Anexa fatos e falas diretamente do resultado, sem reescrita pelo modelo."""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "mercado_livre_orders"
            and isinstance(item.get("exact_metadata"), dict)
            and item.get("exact_metadata", {}).get("exact_lookup") is True
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    metadata = result.get("exact_metadata") or {}
    requested_id = _codex_exact_value(metadata.get("requested_id"), "não informado")
    if not metadata.get("found"):
        searched = [
            _codex_exact_value(item.get("store"))
            for item in (metadata.get("searched_stores") or [])
            if isinstance(item, dict) and item.get("store")
        ]
        lines = [
            CODEX_EXACT_ORDER_HISTORY_MARKER,
            f"Não localizei o número **{requested_id}** como order nem como pack nas contas permitidas.",
        ]
        if searched:
            lines.append("Lojas consultadas diretamente na API do Mercado Livre: " + ", ".join(dict.fromkeys(searched)) + ".")
        message = _codex_whatsapp_inline(metadata.get("message"))
        if message:
            lines.append(message)
        lines.append("O histórico local não foi usado para substituir essa consulta exata.")
        return "\n\n".join(lines).strip()

    orders = [row for row in (result.get("all_rows") or []) if isinstance(row, dict)]
    if str(task.get("origin") or "").strip().lower() == "whatsapp":
        return _codex_exact_ml_order_whatsapp_response(metadata, orders)
    identifier_type = "pack" if metadata.get("identifier_type") == "pack" else "order"
    matched_stores = [str(item) for item in (metadata.get("matched_stores") or []) if str(item).strip()]
    lines = [
        CODEX_EXACT_ORDER_HISTORY_MARKER,
        f"# Venda específica {requested_id}",
        f"Identificador reconhecido: **{identifier_type}**",
    ]
    if matched_stores:
        lines.append("Loja: **" + ", ".join(matched_stores) + "**")
    summary_text = str(ai_summary or "").strip().replace(CODEX_EXACT_ORDER_HISTORY_MARKER, "")
    if summary_text:
        lines.extend(["", "## Resumo da IA", summary_text])

    seen_post_sale: set[tuple[str, str, str]] = set()
    for index, order in enumerate(orders, start=1):
        _codex_append_desktop_order(lines, order, index, matched_stores, seen_post_sale)

    partial = metadata.get("partial_response") is True or metadata.get("coverage_complete") is False
    lines.extend([
        "",
        "## Fontes e cobertura",
        "Consulta read-only feita diretamente nos recursos de orders/packs, envio, reclamações, devoluções e mensagens pós-venda do Mercado Livre.",
        "As mensagens foram preservadas em ordem cronológica; a consulta usou mark_as_read=false e não enviou respostas.",
        (
            "Cobertura parcial: uma ou mais seções ficaram indisponíveis; valores ausentes foram mantidos como indisponíveis."
            if partial
            else "Cobertura completa para todas as seções disponibilizadas pela API nesta consulta."
        ),
    ])
    return "\n".join(lines).strip()


def _codex_whatsapp_complete_ml_report(task: dict[str, Any], results: list[dict[str, Any]]) -> str:
    """Gera o relatorio completo sem permitir que o modelo resuma o agregado por SKU."""
    if str(task.get("origin") or "") != "whatsapp":
        return ""
    query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
    if str(query_policy.get("store_mode") or "single") == "all":
        return ""
    if not _codex_whatsapp_ml_report_requested(task, results=results):
        return ""
    result = next(
        (
            item for item in results
            if isinstance(item, dict)
            and item.get("success") is True
            and str(item.get("tool_id") or "") == "mercado_livre_orders"
        ),
        None,
    )
    if not isinstance(result, dict):
        return ""
    summaries = [item for item in (result.get("summary") or []) if isinstance(item, dict)]
    primary = next(
        (item for item in summaries if str(item.get("tool_id") or "") == "mercado_livre_orders"),
        summaries[0] if summaries else {},
    )
    data = primary.get("summary") if isinstance(primary.get("summary"), dict) else {}
    if data.get("api_consulted") is False or data.get("error"):
        return ""
    rows = [row for row in (result.get("all_rows") or result.get("top_rows") or []) if isinstance(row, dict)]
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    paging = data.get("paging") if isinstance(data.get("paging"), dict) else {}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    requested = period.get("requested") if isinstance(period.get("requested"), dict) else {}
    fallback_period = primary.get("periodo") if isinstance(primary.get("periodo"), dict) else {}
    start = _codex_whatsapp_report_date(requested.get("from") or fallback_period.get("data_inicio"))
    end = _codex_whatsapp_report_date(requested.get("to") or fallback_period.get("data_fim"))
    period_label = start if start and start == end else f"{start} a {end}".strip(" a")
    store = str(query_policy.get("store") or primary.get("loja") or data.get("store") or "").strip()

    lines = [f"# Relatório de vendas — {store or 'Mercado Livre'}"]
    if period_label:
        lines.append(f"Período: {period_label}")
    if store:
        lines.append(f"Loja: {store}")
    lines.extend([
        "", "## Dados principais",
        f"- **Pedidos pagos:** {_codex_whatsapp_number(totals.get('orders'))}",
        f"- **Itens vendidos:** {_codex_whatsapp_number(totals.get('items_quantity'))}",
        f"- **Valor bruto:** {_codex_whatsapp_money(totals.get('gross_amount'))}",
        f"- **Valor pago:** {_codex_whatsapp_money(totals.get('paid_amount'))}",
    ])
    if totals.get("refund_amount") is None:
        lines.extend(["- **Estornos:** indisponível", "- **Valor líquido:** indisponível"])
    else:
        lines.extend([
            f"- **Estornos:** {_codex_whatsapp_money(totals.get('refund_amount'))}",
            f"- **Valor líquido:** {_codex_whatsapp_money(totals.get('net_amount'))}",
        ])
    lines.extend(["", "## SKUs vendidos"])
    if not rows:
        lines.append("Nenhum SKU vendido no período consultado.")
    for row in rows:
        sku = _codex_whatsapp_inline(
            row.get("sku") or row.get("seller_sku") or row.get("item_id") or row.get("mlb") or row.get("id"),
            "não informado",
        )
        title = _codex_whatsapp_inline(row.get("title") or row.get("produto") or row.get("nome"), "Produto sem título")
        try:
            quantity = float(row.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0.0
        try:
            gross = float(row.get("gross_amount") or 0)
        except (TypeError, ValueError):
            gross = 0.0
        unit = gross / quantity if quantity > 0 else 0.0
        lines.extend([
            f"- **SKU:** {sku}",
            f"  **Produto:** {title}",
            f"  **Qtd.:** {_codex_whatsapp_number(quantity)}",
            f"  **Valor unitário médio:** {_codex_whatsapp_money(unit)}",
            f"  **Total vendido:** {_codex_whatsapp_money(gross)}",
        ])

    has_more = bool(paging.get("has_more") or data.get("truncated") or data.get("coverage_complete") is False)
    pages = int(paging.get("pages_fetched") or 0)
    scanned = int(paging.get("scanned") or paging.get("returned") or totals.get("orders") or 0)
    considered = int(totals.get("orders") or paging.get("returned") or 0)
    lines.extend([
        "", "## Fontes e cobertura",
        (
            f"Consulta direta à API do Mercado Livre da loja {store or 'selecionada'}, "
            f"no período {period_label or 'informado'}, com {pages} página(s), "
            f"{scanned} pedido(s) verificado(s), {considered} pedido(s) considerado(s) "
            f"e {len(rows)} SKU(s) consolidado(s)."
        ),
        (
            "Cobertura completa: todas as páginas disponíveis para o período foram consultadas."
            if not has_more
            else "Cobertura incompleta: a API ainda indicou páginas pendentes; nenhum valor ausente foi estimado."
        ),
    ])
    return "\n".join(lines).strip()


def _codex_generate_whatsapp_chart_artifacts(
    task_id: str,
    task: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create requested private report artifacts while tool results still exist."""

    if str(task.get("origin") or "").strip().lower() != "whatsapp":
        return {"expected": False, "status": "not_whatsapp", "artifacts": []}
    try:
        from backend.services import whatsapp_report_files, whatsapp_report_visuals

        chart_query_policy = task.get("query_policy") if isinstance(task.get("query_policy"), dict) else {}
        if not chart_query_policy:
            channel_metadata = task.get("channel_metadata") if isinstance(task.get("channel_metadata"), dict) else {}
            chart_query_policy = (
                channel_metadata.get("query_policy")
                if isinstance(channel_metadata.get("query_policy"), dict)
                else {}
            )
        if not chart_query_policy:
            screen_context = task.get("screen_context") if isinstance(task.get("screen_context"), dict) else {}
            selection = screen_context.get("selection") if isinstance(screen_context.get("selection"), dict) else {}
            chart_query_policy = (
                selection.get("query_policy")
                if isinstance(selection.get("query_policy"), dict)
                else {}
            )

        prompt = _codex_whatsapp_user_request_text(task) or task.get("prompt") or ""
        requested_formats = whatsapp_report_files.requested_report_formats(prompt)
        chart_outcome = (
            whatsapp_report_visuals.generate_task_chart_artifacts(
                base_info_dir=_codex_base_info_dir(),
                client_id=task.get("client_id") or "default",
                task_id=task_id,
                prompt=prompt,
                tool_results=results,
                query_policy=chart_query_policy,
                max_images=2,
            )
            if "png" in requested_formats
            else {"expected": False, "status": "not_requested", "artifacts": []}
        )
        document_outcome = whatsapp_report_files.generate_report_documents(
            base_info_dir=_codex_base_info_dir(),
            client_id=task.get("client_id") or "default",
            task_id=task_id,
            prompt=prompt,
            tool_results=results,
            query_policy=chart_query_policy,
            formats=requested_formats,
        )
        artifacts = [
            *list(chart_outcome.get("artifacts") or []),
            *list(document_outcome.get("artifacts") or []),
        ][:4]
        expected = bool(requested_formats & {"png", "pdf", "xlsx"})
        errors = [
            str(chart_outcome.get("error") or ""),
            *[str(item or "") for item in list(document_outcome.get("errors") or [])],
        ]
        expected = bool(requested_formats & {"png", "pdf", "xlsx"})
        outcome = {
            "expected": expected,
            "status": (
                "not_requested"
                if not expected
                else "completed"
                if len(artifacts) >= len(requested_formats & {"png", "pdf", "xlsx"})
                else "partial"
                if artifacts
                else "generation_failed"
            ),
            "artifacts": artifacts,
            "chart_expected": "png" in requested_formats,
            "error": "; ".join(item for item in errors if item)[:500],
        }
    except Exception as exc:
        outcome = {
            "expected": False,
            "status": "generation_failed",
            "artifacts": [],
            "error": str(exc)[:500],
        }
    if outcome.get("expected"):
        _codex_update_task(
            task_id,
            whatsapp_artifacts=list(outcome.get("artifacts") or [])[:4],
            whatsapp_chart_expected=bool(outcome.get("chart_expected")),
            whatsapp_chart_status=str(outcome.get("status") or "")[:80],
            whatsapp_chart_error=str(outcome.get("error") or "")[:500],
        )
    return outcome
__codex_dependencies__ = ['_codex_base_info_dir', '_codex_update_task', '_codex_whatsapp_inline', '_codex_whatsapp_ml_report_requested', '_codex_whatsapp_money', '_codex_whatsapp_number', '_codex_whatsapp_report_date', '_codex_whatsapp_user_request_text']

__codex_exports__ = ['_codex_exact_value', '_codex_exact_money', '_codex_exact_message_lines', '_codex_exact_status_label', '_codex_exact_shipment_label', '_codex_exact_logistics_label', '_codex_exact_quantity', '_codex_exact_ml_order_whatsapp_response', '_codex_exact_ml_order_response', '_codex_whatsapp_complete_ml_report', '_codex_generate_whatsapp_chart_artifacts']
