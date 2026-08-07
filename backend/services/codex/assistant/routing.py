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

from .catalog import _assistant_tool_meta
from .normalization import _assistant_is_broad_open_questions_query, _assistant_is_generic_sales_api_query, _assistant_latest_ml_event_kind, _assistant_message_has_product_ref, _assistant_questions_addressed_to_assistant
from .references import _assistant_extract_sku_filter, _assistant_normalize_ml_item_id, _assistant_normalize_sku, _assistant_previous_period, _assistant_resolve_loja, _assistant_resolve_period, _assistant_wants_store_breakdown
from .runtime import _assistant_context_page, _assistant_now, _assistant_periodo_padrao, _assistant_texto_norm
from .source_labels import _assistant_human_fallback_list, _assistant_human_source_label, _assistant_human_source_list, _assistant_human_tool_label
from .utils import _assistant_float, _assistant_result_count

def _assistant_source_policy_flags(message: Any) -> dict[str, bool]:
    text = _assistant_texto_norm(str(message or ""))
    stock = bool(re.search(r"\b(estoque|saldo|quantidade em estoque|disponivel em estoque)\b", text))
    flags = {
        "open_questions": _assistant_is_broad_open_questions_query(message),
        "stock": stock,
        "positive_count": whatsapp_intent.positive_stock_sku_count_requested(message),
        "full": bool(stock and re.search(r"\b(full|fulfillment|mercado envios)\b", text)),
        "sum": bool(stock and re.search(r"\b(somar|some|soma|somatorio|totalizar|total geral|loja\s*\+\s*full|loja e full)\b", text)),
        "combined": bool(stock and re.search(r"\b(loja\s*\+\s*full|loja e full|estoque total geral|saldo total geral)\b", text)),
        "listing": bool(
        re.search(r"\b(anuncio|anuncios|mercado livre|mercadolivre|mlb[\s_-]*\d+)\b", text)
        and re.search(
            r"\b(descricao|descricoes|detalhe|detalhes|atributo|atributos|ficha|conteudo|"
            r"link|links|url|foto|fotos|imagem|imagens|dados|informacao|informacoes|mlb)\b",
            text,
        )
        ),
        "commercial": bool(
        re.search(r"\b(anuncio|anuncios|mercado livre|mercadolivre|mlb[\s_-]*\d+)\b", text)
        and re.search(r"\b(taxa|taxas|tarifa|tarifas|comissao|comissoes|frete|custo de envio|preco liquido|valor liquido)\b", text)
        ),
        "visits": bool(
        re.search(r"\b(visita|visitas|visualizacao|visualizacoes|acesso|acessos)\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|anuncio)\b", text)
        ),
        "promotions": bool(
        re.search(r"\b(promocao|promocoes|campanha|campanhas|oferta|ofertas)\b", text)
        and re.search(r"\b(mercado livre|mercadolivre|conta|loja)\b", text)
        ),
        "post_sale": bool(
        re.search(r"\b(conversa|conversas|mensagem|mensagens|anexo|anexos|mediacao|pos venda|pos-venda)\b", text)
        and re.search(r"\b(pack|pedido|order)\b", text)
        ),
    }
    daily_sales_report = bool(
        re.search(r"\b(relatorio|resumo)\b", text)
        and re.search(r"\b(do dia|diario|diaria|de hoje|hoje)\b", text)
    )
    flags["sales"] = bool(
        daily_sales_report
        or re.search(r"\b(pedido|pedidos|venda|vendas|vendido|vendidos|faturamento|ranking|mais vendido)\b", text)
    )
    flags["returns"] = bool(re.search(r"\b(devolucao|devolucoes|devolvido|devolvidos|reembolso|reembolsos|estorno|estornos)\b", text))
    flags["latest"] = bool(re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text))
    if flags["post_sale"] or (flags["returns"] and whatsapp_intent.mercado_livre_return_reference_only(message)):
        flags["sales"] = False
    latest_kind = _assistant_latest_ml_event_kind(message)
    if latest_kind == "return":
        flags.update(sales=False, returns=True)
    elif latest_kind == "sale":
        flags.update(sales=True, returns=False)
    elif latest_kind == "both":
        flags.update(sales=True, returns=True)
    return flags


def _assistant_add_unique(target: list[str], *items: str) -> None:
    for item in items:
        if item and item not in target:
            target.append(item)


def _assistant_source_policy_targets(flags: dict[str, bool]) -> tuple[list[str], list[str], list[str], list[str]]:
    required_tools: list[str] = []
    forbidden_tools: list[str] = []
    providers: list[str] = []
    intents: list[str] = []
    add = _assistant_add_unique
    if flags["open_questions"]:
        add(required_tools, "questions_post_sale_query")
        add(providers, "mercado_livre")
        intents.append("open_questions_queue")
    elif flags["positive_count"]:
        add(required_tools, "bling_positive_stock_sku_count")
        add(
            forbidden_tools,
            "bling_stock_balances",
            "bling_deposits",
            "mercado_livre_listing",
            "stock_data",
            "stockout_forecast",
            "stale_stock",
            "product_data",
            "product_registry",
        )
        add(providers, "bling")
        intents.append("positive_stock_sku_count")
    elif flags["combined"]:
        add(required_tools, "bling_stock_balances", "mercado_livre_full_stock")
        add(forbidden_tools, "bling_deposits")
        add(providers, "bling", "mercado_livre")
        intents.append("combined_store_and_full_stock")
    elif flags["full"]:
        add(required_tools, "mercado_livre_full_stock")
        add(forbidden_tools, "bling_stock_balances", "bling_deposits", "stock_data")
        add(providers, "mercado_livre")
        intents.append("full_stock")
    elif flags["stock"]:
        add(required_tools, "bling_stock_balances")
        add(forbidden_tools, "bling_deposits")
        add(providers, "bling")
        intents.append("current_store_stock")
    if flags["listing"] or flags["commercial"]:
        add(required_tools, "mercado_livre_listing")
        add(providers, "mercado_livre")
        intents.append("listing_commercial" if flags["commercial"] else "listing_description")
    if flags["visits"]:
        add(required_tools, "mercado_livre_visits")
        add(providers, "mercado_livre")
        intents.append("listing_visits")
    if flags["promotions"]:
        add(required_tools, "mercado_livre_promotions")
        add(providers, "mercado_livre")
        intents.append("promotions")
    if flags["post_sale"]:
        add(required_tools, "mercado_livre_post_sale_detail")
        add(providers, "mercado_livre")
        intents.append("post_sale_detail")
    if flags["sales"]:
        add(required_tools, "mercado_livre_orders")
        add(providers, "mercado_livre")
        intents.append("orders_and_sales")
    if flags["returns"]:
        add(required_tools, "mercado_livre_returns")
        add(providers, "mercado_livre")
        intents.append("returns")
    if flags["latest"] and (flags["sales"] or flags["returns"]):
        add(
            forbidden_tools,
            "sales_returns_query",
            "sales_ranking",
            "sales_summary",
            "returns_summary",
            "return_rate",
        )
    return required_tools, forbidden_tools, providers, intents


def _assistant_source_routing_policy(message: Any) -> dict[str, Any]:
    """Contrato de origem para consultas operacionais do Black Jhon."""
    flags = _assistant_source_policy_flags(message)
    required_tools, forbidden_tools, providers, intents = _assistant_source_policy_targets(flags)
    if not required_tools:
        return {}
    return {
        "version": "20260722-whatsapp-source-routing-v7-open-questions-scope",
        "intent": "+".join(intents),
        "required_tools": required_tools,
        "forbidden_tools": forbidden_tools,
        "preferred_providers": providers,
        "force_refresh": any(flags[key] for key in ("open_questions", "stock", "listing", "commercial", "visits", "promotions", "post_sale", "sales", "returns")),
        "include_listing_details": flags["listing"],
        "include_commercial_detail": flags["commercial"],
        "sum_requested": flags["sum"],
        "full_exclusive": bool(flags["full"] and not flags["combined"]),
        "full_stock_provider": "mercado_livre_api_only",
        "bling_stock_scope": "exclude_full",
        "positive_stock_sku_count_requested": flags["positive_count"],
        "aggregation_policy": (
            "single_store_scalar_no_sum"
            if flags["positive_count"]
            else "sum_bling_store_plus_mercado_livre_full"
            if flags["combined"]
            else "sum_mercado_livre_full_only"
            if flags["full"] and flags["sum"]
            else "separate_sources_no_sum"
        ),
    }


def _assistant_add_tool_ids(selected: list[str], *tool_ids: str) -> None:
    for tool_id in tool_ids:
        if tool_id not in selected:
            selected.append(tool_id)


def _assistant_finalize_selected_tools(selected: list[str], source_policy: dict[str, Any]) -> list[str]:
    forbidden = {str(item or "") for item in (source_policy.get("forbidden_tools") or [])}
    selected = [tool for tool in selected if tool not in forbidden]
    required = [str(item or "") for item in (source_policy.get("required_tools") or []) if str(item or "")]
    if required:
        selected = required + [tool for tool in selected if tool not in required]
    return selected[:28]


def _assistant_add_product_operational_tools(
    selected: list[str], text: str, page: str, *, wants_product: bool,
    wants_margin: bool, wants_operational: bool, wants_fiscal: bool,
    questions_addressed_to_assistant: bool,
) -> None:
    add = lambda *items: _assistant_add_tool_ids(selected, *items)
    if wants_product:
        add("product_data", "product_registry", "stock_data", "local_csv_query", "local_database_query")
        if wants_margin:
            add("product_margin", "product_costs_and_margin")
        if "imagem" in text or "foto" in text:
            add("product_image")
    if wants_operational:
        if not questions_addressed_to_assistant and re.search(r"\b(pergunta|perguntas|pos venda|pos-venda)\b", text + " " + page):
            add("questions_post_sale_query", "mercado_livre_readonly")
        if wants_fiscal:
            add("fiscal_local_query")
        add("operational_dispatcher")


def _assistant_select_tool_ids(message: str, mode: str, screen_context: Any) -> list[str]:
    text = _assistant_texto_norm(message)
    page = _assistant_texto_norm(_assistant_context_page(screen_context))
    selected: list[str] = []
    source_policy = _assistant_source_routing_policy(message)
    questions_addressed_to_assistant = _assistant_questions_addressed_to_assistant(message)
    if source_policy.get("intent") == "open_questions_queue":
        return ["questions_post_sale_query"]
    if source_policy.get("positive_stock_sku_count_requested") is True:
        return ["bling_positive_stock_sku_count"]

    add = lambda *tool_ids: _assistant_add_tool_ids(selected, *tool_ids)

    add(*list(source_policy.get("required_tools") or []))

    wants_report = mode in {"daily", "report"} or bool(re.search(r"\b(relatorio|analise|analisar|diagnostico|oportunidade|melhoria|melhorias)\b", text))
    wants_sales = wants_report or bool(re.search(r"\b(venda|vendas|vendido|vendidos|faturamento|pedido|pedidos|ranking|rank|top|mais vendido|mais vendeu)\b", text))
    wants_ranking = bool(re.search(r"\b(ranking|rank|top|mais vendido|mais vendidos|mais vendeu|lider|campeao)\b", text))
    wants_stock = wants_report or bool(re.search(r"\b(estoque|ruptura|saldo|parado|sem vender|giro|reposicao)\b", text))
    wants_returns = wants_report or bool(re.search(r"\b(devolucao|devolucoes|devolvido|devolvidos|return)\b", text))
    wants_margin = wants_report or bool(re.search(r"\b(margem|lucro|rentabilidade|custo|imposto|preco)\b", text))
    wants_api = bool(re.search(r"\b(api|apis|via api|pelas apis|pela api)\b", text))
    wants_external = wants_api or bool(re.search(r"\b(mercado livre|mercadolivre|bling|anuncio|anuncios|integracao|integracoes|token|conta|loja)\b", text))
    wants_product = _assistant_message_has_product_ref(message) or bool(re.search(r"\b(produto|cadastro|imagem|foto|ncm|cest)\b", text))
    wants_operational = bool(re.search(r"\b(full|favoritos|pergunta|perguntas|pos venda|pos-venda|fiscal|simulador|importacao|importacoes|promocao)\b", text + " " + page))
    wants_program_catalog = bool(re.search(r"\b(funcoes|funcionalidades|o que o programa faz|o que voce consegue|modulos|rotas|telas|servicos|catalogo|inventario|saber fazer tudo|fazer tudo)\b", text))
    wants_action_match = bool(re.search(r"\b(faca|faz|execute|executar|acionar|rodar|sincronizar|sicronizar|atualizar|salvar|gerar|aprovar|enviar|cancelar|remover|limpar)\b", text))
    wants_sources = bool(re.search(r"\b(fonte|fontes|banco|sqlite|db|csv|cache|json|arquivo local|dados locais|qualquer fonte|logs?)\b", text))
    wants_sync_logs = bool(re.search(r"\b(log|logs|erro|falha|status|estado|nao aparecem|nao apareceu|sumiu|sincronizacao|sincronizar|sync|shared sync|shared-sync)\b", text))
    wants_memory = wants_report or wants_action_match or bool(
        re.search(r"\b(lembra|memoria|preferencia|preferencias|regra|regras|decisao|decisoes|relatorio anterior|relatorios anteriores|custo pendente|custos pendentes|alerta ignorado|alertas ignorados|sku frequente|loja usada)\b", text)
    )
    mentions_bling = "bling" in text
    mentions_ml = bool(re.search(r"\b(mercado livre|mercadolivre|mlb[\s_-]*\d+|anuncio|anuncios)\b", text))
    wants_generic_sales_apis = _assistant_is_generic_sales_api_query(message)
    wants_fiscal = bool(re.search(r"\b(fiscal|nota|notas|nf|nfe|nf-e|nfce|nfse|ncm|cest|tributacao|natureza|naturezas|cfop)\b", text))
    wants_finance = bool(re.search(r"\b(financeiro|contas? a receber|contas? a pagar|receber|pagar|caixa|banco|boleto|boletos)\b", text))
    wants_lots = bool(re.search(r"\b(lote|lotes|validade|lancamento|lancamentos|entrada|saida)\b", text))
    wants_deposits = bool(re.search(r"\b(deposito|depositos|full|fulfillment)\b", text))
    wants_broad_bling = (
        mentions_bling
        and bool(re.search(r"\b(qualquer|tudo|todas|todos|geral|dados|informacao|informacoes|relatorio|analise)\b", text))
        and not any((wants_fiscal, wants_stock, wants_sales, wants_finance, wants_lots, wants_product))
    )

    if wants_memory:
        add("operational_memory_query")
    if wants_program_catalog or wants_action_match or wants_operational or wants_report:
        add("capability_resolve")
    if wants_sources:
        add("source_discovery")
    if wants_sync_logs:
        add("sync_logs_query")
    if wants_program_catalog:
        add("program_functions_catalog")
    if wants_action_match:
        add("capability_resolve", "program_action_match")
    if wants_sales or wants_returns or wants_ranking:
        add("sales_returns_query")
    if wants_sales or wants_ranking:
        add("sales_ranking", "sales_summary")
    if wants_report:
        add("avg_ticket", "period_comparison", "sales_timeseries", "sales_anomalies")
    if wants_returns:
        add("returns_summary", "return_rate")
    if wants_stock:
        add("stockout_forecast", "stale_stock")
    if wants_margin:
        add("profit_summary", "product_costs_and_margin")
    if wants_report:
        add("integrations_status")
    if wants_external:
        add("integrations_status")
        if wants_generic_sales_apis:
            add("bling_sales_orders", "mercado_livre_orders")
        if mentions_ml:
            add("mercado_livre_readonly", "mercado_livre_listing")
            if wants_sales:
                add("mercado_livre_orders")
        if mentions_bling:
            add("bling_status")
            if wants_product:
                add("bling_products")
            if wants_stock or wants_deposits:
                add("bling_stock_balances", "bling_deposits")
            if wants_sales or wants_ranking:
                add("bling_sales_orders")
                if re.search(r"\b(detalhe|detalhar|itens do pedido|pedido\s+\d+)\b", text):
                    add("bling_sales_order_detail")
            if wants_fiscal:
                if wants_product or "ncm" in text or "cest" in text:
                    add("bling_fiscal_product")
                add("bling_fiscal_nfe", "bling_operation_natures")
                if re.search(r"\b(detalhe|detalhar|chave|44 digitos|44 digitos|nota\s+\d+|nfe\s+\d+)\b", text):
                    add("bling_fiscal_nfe_detail")
            if wants_lots:
                add("bling_lots", "bling_lot_movements")
            if wants_finance:
                add("bling_finance_summary")
            if wants_broad_bling:
                add("bling_deposits", "bling_resource_query")
    _assistant_add_product_operational_tools(
        selected, text, page, wants_product=wants_product, wants_margin=wants_margin,
        wants_operational=wants_operational, wants_fiscal=wants_fiscal,
        questions_addressed_to_assistant=questions_addressed_to_assistant,
    )
    if wants_fiscal:
        add("fiscal_local_query")
    if wants_sources and not any(tool in selected for tool in ("local_database_query", "local_csv_query", "local_cache_query")):
        add("local_database_query", "local_csv_query", "local_cache_query")
    if wants_generic_sales_apis:
        primary = [tool for tool in ("bling_sales_orders", "mercado_livre_orders") if tool in selected]
        supporting = [tool for tool in ("sales_returns_query", "sales_ranking", "sales_summary") if tool in selected]
        selected = primary + supporting + [tool for tool in selected if tool not in primary and tool not in supporting]
    if not selected:
        add("operational_dispatcher", "integrations_status")
    return _assistant_finalize_selected_tools(selected, source_policy)


def _assistant_registry_plan(client_id: str, message: str, screen_context: Any, mode: str) -> dict[str, Any]:
    data_inicio, data_fim = _assistant_resolve_period(client_id, message, screen_context)
    text_norm = _assistant_texto_norm(message)
    effective_mode = str(mode or "chat").strip().lower() or "chat"
    if effective_mode == "chat" and re.search(r"\b(relatorio|relatorios)\b", text_norm):
        effective_mode = "report"
    if (
        re.search(r"\b(ultima|ultimo|mais recente|ultima ocorrencia|ultimo registro)\b", text_norm)
        and re.search(r"\b(venda|pedido|devolucao|devolvido|reembolso|estorno)\b", text_norm)
        and not re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b", str(message or ""))
    ):
        data_inicio, data_fim = _assistant_periodo_padrao(365)
    prev_inicio, prev_fim = _assistant_previous_period(data_inicio, data_fim)
    loja = _assistant_resolve_loja(client_id, message, screen_context)
    broad_open_questions = _assistant_is_broad_open_questions_query(message)
    sku = "" if broad_open_questions else _assistant_extract_sku_filter(message, screen_context)
    separar_por_loja = _assistant_wants_store_breakdown(message, loja)
    selected = _assistant_select_tool_ids(message, effective_mode, screen_context)
    source_policy = _assistant_source_routing_policy(message)
    api_sales_query = _assistant_is_generic_sales_api_query(message)
    steps = ["interpretando pedido"]
    for tool_id in selected:
        status = str(_assistant_tool_meta(tool_id).get("status") or "").strip()
        if status and status not in steps:
            steps.append(status)
    return {
        "client_id": str(client_id or "default"),
        "intent": "sales_ranking" if "sales_ranking" in selected else ("report" if effective_mode in {"daily", "report"} else "data_query"),
        "mode": effective_mode,
        "message": str(message or ""),
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "periodo": {"data_inicio": data_inicio, "data_fim": data_fim},
        "periodo_anterior": {"data_inicio": prev_inicio, "data_fim": prev_fim},
        "loja": loja,
        "sku": sku,
        "separar_por_loja": separar_por_loja,
        "incluir_registros": True,
        "selected_tools": selected,
        "source_policy": source_policy,
        "force_refresh": bool(source_policy.get("force_refresh")),
        "api_sales_query": api_sales_query,
        "source_roles": {
            tool_id: (
                "primary_api"
                if tool_id in {"bling_sales_orders", "mercado_livre_orders", "mercado_livre_returns"}
                else "supporting_local_history"
                if api_sales_query and tool_id in {"sales_returns_query", "sales_ranking", "sales_summary"}
                else "context"
            )
            for tool_id in selected
        },
        "aggregation_policy": "separate_sources_no_sum" if api_sales_query else "standard",
        "status_steps": steps,
    }


def _assistant_tool_rows(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in (
        "rows",
        "results",
        "campaigns",
        "by_sku",
        "por_sku",
        "vendas",
        "devolucoes",
        "por_loja",
        "top_skus",
        "items",
        "itens",
        "matches",
        "lojas",
        "produtos",
        "produtos_fiscais",
        "saldos",
        "depositos",
        "pedidos",
        "orders",
        "notas",
        "naturezas",
        "lotes",
        "lancamentos",
        "financeiro",
        "resources",
        "sources",
        "capabilities",
        "candidates",
        "routes",
        "services",
        "actions",
        "pages",
        "modules",
        "functions",
        "missing_params",
        "alertas",
        "anomalias",
        "pontos",
    ):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def _assistant_tool_empty_reason(tool_id: str, records: int, result: Any, plan: dict[str, Any]) -> str:
    if records > 0:
        return ""
    if isinstance(result, dict) and result.get("error"):
        return str(result.get("error") or "")[:300]
    if isinstance(result, dict) and result.get("live_query"):
        if result.get("coverage_complete"):
            return ""
        failed = str(result.get("lojas_incompletas_text") or "").strip()
        errors = result.get("errors_by_store") if isinstance(result.get("errors_by_store"), dict) else {}
        affected = ", ".join([name for name in list(errors) + failed.split(", ") if str(name or "").strip()])
        return (
            f"Nao foi possivel confirmar toda a fila atual{f' de {affected}' if affected else ''}."
        )[:300]
    if tool_id == "sales_ranking":
        return (
            "Nenhuma venda por SKU foi encontrada para "
            f"{plan.get('loja') or 'todas as lojas'} entre {plan.get('data_inicio')} e {plan.get('data_fim')}."
        )
    if tool_id == "mercado_livre_listing":
        if (
            isinstance(result, dict)
            and result.get("coverage_complete") is True
            and result.get("partial_response") is not True
            and not result.get("error")
        ):
            return ""
        return "Nenhum anuncio Mercado Livre foi retornado para a consulta read-only."
    if tool_id == "mercado_livre_orders":
        return "A API do Mercado Livre retornou zero pedidos para os filtros informados."
    if tool_id == "mercado_livre_returns":
        return "A API do Mercado Livre retornou zero devolucoes para o periodo informado."
    if tool_id == "mercado_livre_full_stock":
        return "A API de inventario Full do Mercado Livre nao retornou saldo para os filtros informados."
    if tool_id == "bling_product":
        return "Nenhum produto Bling foi encontrado ou nao havia SKU/produto suficiente para consultar."
    if str(tool_id or "").startswith("bling_"):
        return "Consulta Bling read-only nao retornou registros para os filtros informados."
    if tool_id == "program_action_match":
        return "Nenhuma acao operacional aprovada foi reconhecida para este pedido."
    if tool_id == "program_functions_catalog":
        return "Catalogo do programa nao retornou funcoes para o filtro informado."
    if tool_id == "context_hub_search":
        return "A geracao ativa do Context Hub nao retornou resultados para a consulta."
    return "Consulta read-only nao retornou registros."


def _assistant_dlp_safe_rows(rows: Any) -> tuple[list[Any], int]:
    """Drop complete evidence rows whose snippets trip the Context Hub DLP."""

    from backend.modules.context_hub import dlp as context_hub_dlp

    def snippets(value: Any) -> list[str]:
        if isinstance(value, dict):
            found = [str(value.get("snippet") or "")] if "snippet" in value else []
            return found + [item for child in value.values() for item in snippets(child)]
        if isinstance(value, list):
            return [item for child in value for item in snippets(child)]
        return []

    safe: list[Any] = []
    blocked = 0
    for row in rows if isinstance(rows, list) else []:
        if any(context_hub_dlp.scan_dlp(value, source_ref="whatsapp_tool_snippet") for value in snippets(row) if value):
            blocked += 1
        else:
            safe.append(row)
    return safe, blocked


def _assistant_context_hub_public_rows(rows: list[Any]) -> list[dict[str, Any]]:
    allowed_keys = {
        "doc_id", "chunk_id", "snippet", "score", "truth_class", "source_version",
        "source_hash", "content_hash", "generation_id", "version", "hash", "generation",
        "type", "module", "surface", "selection_strategy", "selection_reason",
    }
    return [
        {str(key): value for key, value in row.items() if str(key) in allowed_keys}
        | ({"reference": str(row.get("doc_id") or "")[:240]} if row.get("doc_id") else {})
        for row in rows if isinstance(row, dict)
    ]


def _assistant_standard_record_count(tool_id: str, result: dict[str, Any], rows: list[Any], single_sale: bool) -> int:
    records = len(rows) if single_sale else _assistant_result_count(result)
    if rows and records == 0:
        records = len(rows)
    scalar_keys = (
        "quantidade_total", "quantidade_vendida_total", "quantidade_devolvida_total",
        "valor_total", "valor_vendido_total", "valor_devolvido_total", "pedidos_total",
        "lucro_estimado", "ticket_medio", "taxa_devolucao_quantidade_percentual",
        "taxa_devolucao_valor_percentual",
    )
    if records == 0 and any(result.get(key) not in (None, "", 0, 0.0) for key in scalar_keys):
        records = 1
    confirmed_stock = (
        tool_id == "stock_data" and result.get("found") is True
        and str(result.get("sku") or "").strip()
        and all(key in result for key in ("saldo_loja_total", "saldo_full_total", "saldo_total"))
    )
    return 1 if records == 0 and confirmed_stock else records


def _assistant_safe_result_payload(tool_id: str, raw: dict[str, Any], result: dict[str, Any], dlp_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    if tool_id == "context_hub_search":
        summary = {
            key: result.get(key)
            for key in ("success", "count", "generation_id", "generation", "source_version")
            if result.get(key) not in (None, "")
        }
        arguments: dict[str, Any] = {}
    else:
        summary = {key: value for key, value in result.items() if not isinstance(value, list)}
        arguments = raw.get("arguments") or {}
    if dlp_count:
        summary["dlp_blocked_count"] = dlp_count
    return summary, arguments


def _assistant_exact_order_metadata(tool_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if tool_id != "mercado_livre_orders" or result.get("exact_lookup") is not True:
        return {}
    return {
        "exact_lookup": True,
        "requested_id": str(result.get("requested_id") or ""),
        "identifier_type": str(result.get("identifier_type") or ""),
        "matched_stores": copy.deepcopy(result.get("matched_stores") or []),
        "resolved_order_ids": copy.deepcopy(result.get("resolved_order_ids") or []),
        "searched_stores": copy.deepcopy(result.get("searched_stores") or []),
        "resources": [
            str(item.get("resource") or "") for item in (result.get("sources") or [])
            if isinstance(item, dict) and str(item.get("resource") or "").strip()
        ],
        "found": bool(result.get("found")),
        "error": str(result.get("error") or ""),
        "message": str(result.get("message") or ""),
        "partial_response": bool(result.get("partial_response")),
        "coverage_complete": bool(result.get("coverage_complete")),
    }


def _assistant_standard_result(tool_id: str, raw: Optional[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any]:
    meta = _assistant_tool_meta(tool_id)
    raw = raw if isinstance(raw, dict) else {}
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    rows = _assistant_tool_rows(result)
    rows, dlp_blocked_count = _assistant_dlp_safe_rows(rows)
    if tool_id == "context_hub_search":
        rows = _assistant_context_hub_public_rows(rows)
    single_sale_rows = bool(
        tool_id == "mercado_livre_orders"
        and isinstance(result.get("orders"), list)
        and (
            _assistant_latest_ml_event_kind(plan.get("message")) in {"sale", "both"}
            or result.get("exact_lookup") is True
        )
    )
    if (
        tool_id == "mercado_livre_orders"
        and isinstance(result.get("orders"), list)
        and (
            single_sale_rows
            or result.get("exact_lookup") is True
            or (
                isinstance(result.get("target"), dict)
                and isinstance(result.get("exact_coverage"), dict)
            )
        )
    ):
        rows = result.get("orders") or []
    records = len(rows) if dlp_blocked_count else _assistant_standard_record_count(tool_id, result, rows, single_sale_rows)
    source_raw = raw.get("function") or meta.get("executor") or tool_id
    tool_label = _assistant_human_tool_label(tool_id)
    source_label = _assistant_human_source_label(source_raw, tool_id)
    safe_summary, safe_arguments = _assistant_safe_result_payload(tool_id, raw, result, dlp_blocked_count)
    exact_metadata = _assistant_exact_order_metadata(tool_id, result)
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "module": meta.get("module"),
        "description": meta.get("description"),
        "executor": meta.get("executor"),
        "external": bool(meta.get("external")),
        "read_only": meta.get("read_only") is True,
        "records": records,
        "rows": rows if tool_id == "sales_returns_query" and isinstance(rows, list) else (rows[:500] if isinstance(rows, list) else []),
        "summary": safe_summary,
        "exact_metadata": exact_metadata,
        "source": source_raw,
        "source_label": source_label,
        "sources_human": _assistant_human_source_list([source_raw], tool_id),
        "source_role": str((plan.get("source_roles") or {}).get(tool_id) or plan.get("source_role") or "context"),
        "aggregation_policy": str(plan.get("aggregation_policy") or "standard"),
        "arguments": safe_arguments,
        "periodo": plan.get("periodo") or {},
        "loja": plan.get("loja") or "",
        "empty_reason": _assistant_tool_empty_reason(tool_id, records, result, plan),
        "next_fallbacks": meta.get("fallbacks") or [],
        "next_fallbacks_human": _assistant_human_fallback_list(meta.get("fallbacks") or []),
        "generated_at": _assistant_now(),
    }


def _assistant_mercado_livre_full_stock_raw(client_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    from backend.services import full_mercadolivre

    loja = str(plan.get("loja") or "").strip()
    sku = _assistant_normalize_sku(plan.get("sku") or "")
    item_id = _assistant_normalize_ml_item_id(plan.get("item_id") or plan.get("mlb") or "")
    try:
        limite = max(100, min(int(plan.get("limite") or plan.get("limit") or 10000), 20000))
    except Exception:
        limite = 10000
    arguments = {
        "loja": loja,
        "sku": sku,
        "item_id": item_id,
        "limit": limite,
        "force_refresh": bool(plan.get("force_refresh", True)),
    }
    payload = full_mercadolivre.listar_anuncios_full_mercadolivre_payload(
        client_id,
        loja,
        limite,
        force_refresh=bool(plan.get("force_refresh", True)),
    )
    rows = [item for item in (payload.get("results") or []) if isinstance(item, dict)]
    if item_id:
        rows = [item for item in rows if _assistant_normalize_ml_item_id(item.get("id")) == item_id]
    if sku:
        sku_key = _assistant_normalize_sku(sku)

        def sku_matches(item: dict[str, Any]) -> bool:
            candidates = [item.get("sku"), item.get("sku_display")]
            for variation in item.get("variations") if isinstance(item.get("variations"), list) else []:
                if isinstance(variation, dict):
                    candidates.extend([variation.get("sku"), variation.get("seller_sku")])
            return any(sku_key == _assistant_normalize_sku(value) for value in candidates if str(value or "").strip())

        rows = [item for item in rows if sku_matches(item)]
    warnings = []
    for item in rows:
        if str(item.get("stock_source") or "") != "fulfillment_stock":
            warnings.append(
                f"{item.get('id') or item.get('sku') or 'item'}: o inventario Full nao confirmou saldo atual."
            )
    available = sum(_assistant_float(item.get("full_available_quantity")) for item in rows)
    unavailable = sum(_assistant_float(item.get("full_not_available_quantity")) for item in rows)
    total = sum(_assistant_float(item.get("full_total_quantity")) for item in rows)
    return {
        "function": "get_mercado_livre_full_stock",
        "arguments": arguments,
        "result": {
            "found": bool(rows),
            "store": loja,
            "matches": rows,
            "records": len(rows),
            "full_available_quantity_total": available,
            "full_not_available_quantity_total": unavailable,
            "full_total_quantity": total,
            "stock_scope": "mercado_livre_fulfillment_only",
            "api_consulted": True,
            "sources": [{
                "provider": "mercado_livre",
                "resource": "inventories/{inventory_id}/stock/fulfillment",
                "method": "GET",
                "store": loja,
            }],
            "warnings": warnings,
            "read_only": True,
        },
    }


def source_policy(message: Any) -> dict[str, Any]:
    """Resolve the source policy for a user request."""

    return _assistant_source_routing_policy(message)
