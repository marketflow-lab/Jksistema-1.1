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

from .analysis_stock import _assistant_collect_stockout_rows
from .margin_analysis import _assistant_collect_margin_rows, _assistant_specialist_sku_diagnostics
from .runtime import _assistant_now, _assistant_texto_norm, _assistant_today
from .source_labels import _assistant_human_source_label
from .utils import _assistant_find_result, _assistant_first_list, _assistant_float, _assistant_function_name, _assistant_money, _assistant_percent, _assistant_qty, _assistant_result_count, _assistant_sku_label

def _assistant_add_finding(
    sections: dict[str, list[dict[str, Any]]],
    section: str,
    title: str,
    evidence: str,
    recommendation: str,
    severity: str = "info",
    impact: str = "",
    source: str = "",
) -> None:
    sections.setdefault(section, []).append(
        {
            "section": section,
            "title": str(title or "")[:160],
            "severity": severity,
            "evidence": str(evidence or "")[:700],
            "impact": str(impact or "")[:500],
            "recommendation": str(recommendation or "")[:700],
            "source": source,
        }
    )

def _assistant_management_sales(results: list[dict[str, Any]], kpis: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]]) -> None:
    sales = _assistant_find_result(results, "get_sales_by_period")
    sales_qty = _assistant_find_result(results, "get_sales_quantity_by_period")
    avg_ticket = _assistant_find_result(results, "get_avg_ticket_by_period")
    comparison = _assistant_find_result(results, "get_period_comparison")
    anomalies = _assistant_find_result(results, "detect_sales_anomalies")
    return_rate = _assistant_find_result(results, "get_return_rate_by_period")
    if sales:
        kpis.extend(
            [
                {"label": "Faturamento", "value": _assistant_money(sales.get("valor_total")), "detail": "Periodo base de vendas", "severity": "info"},
                {"label": "Pedidos", "value": str(int(_assistant_float(sales.get("pedidos_total")))), "detail": "Pedidos validos", "severity": "info"},
                {"label": "Itens vendidos", "value": f"{_assistant_float(sales.get('quantidade_total')):.0f}", "detail": "Quantidade liquida vendida", "severity": "info"},
            ]
        )
        if _assistant_float(sales.get("valor_total")) <= 0:
            _assistant_add_finding(
                sections,
                "Vendas",
                "Sem faturamento no periodo consultado",
                "A consulta de vendas retornou faturamento zerado.",
                "Validar se o periodo, a loja e a sincronizacao de vendas estao corretos antes de tomar decisoes comerciais.",
                "warning",
                "Sem vendas confiaveis, qualquer leitura de estoque e margem pode ficar distorcida.",
                "get_sales_by_period",
            )
        top_skus = _assistant_first_list(sales, "top_skus")
        if top_skus:
            top = top_skus[0] if isinstance(top_skus[0], dict) else {}
            _assistant_add_finding(
                sections,
                "Vendas",
                "SKU lider deve orientar reposicao e campanha",
                f"Principal SKU vendido: {_assistant_sku_label(top)}. Quantidade: {top.get('quantidade') or top.get('qtd') or top.get('quantidade_total') or '-'}; valor: {_assistant_money(top.get('valor') or top.get('valor_total') or 0)}.",
                "Garantir estoque, revisar buy box/anuncio e usar esse SKU como referencia de margem, fotos, preco e frete para produtos similares.",
                "info",
                "SKU lider sem cobertura de estoque vira perda direta de receita; SKU lider com baixa margem exige ajuste de preco/custo.",
                "get_sales_by_period",
            )

    if sales_qty and not sales:
        kpis.append({"label": "Itens vendidos", "value": f"{_assistant_float(sales_qty.get('quantidade_total') or sales_qty.get('quantidade_vendida_total')):.0f}", "detail": "Quantidade vendida no periodo", "severity": "info"})

    if avg_ticket:
        ticket = _assistant_float(avg_ticket.get("ticket_medio"))
        kpis.append({"label": "Ticket medio", "value": _assistant_money(ticket), "detail": "Receita media por pedido", "severity": "info"})
        if ticket > 0 and ticket < 80:
            _assistant_add_finding(
                sections,
                "Vendas",
                "Ticket medio baixo",
                f"Ticket medio estimado em {_assistant_money(ticket)}.",
                "Avaliar kits, combos, frete progressivo e anuncios complementares para elevar valor por pedido sem depender apenas de volume.",
                "info",
                "Ticket baixo aumenta peso de frete, tarifa e operacao sobre a margem.",
                "get_avg_ticket_by_period",
            )

    comp = comparison.get("comparativo") if isinstance(comparison.get("comparativo"), dict) else {}
    if comp:
        delta_valor_pct = _assistant_float(comp.get("variacao_faturamento_percentual"))
        delta_qtd_pct = _assistant_float(comp.get("variacao_quantidade_percentual"))
        severity = "warning" if delta_valor_pct <= -10 else "ok" if delta_valor_pct >= 10 else "info"
        _assistant_add_finding(
            sections,
            "Vendas",
            "Comparativo de periodo",
            f"Faturamento variou {_assistant_percent(delta_valor_pct)} e quantidade variou {_assistant_percent(delta_qtd_pct)} contra o periodo anterior.",
            "Quando houver queda, separar efeito de preco, ruptura, pausa de anuncio e reducao de demanda. Quando houver alta, proteger estoque dos SKUs que puxaram o crescimento.",
            severity,
            "Quedas acima de 10% exigem acao comercial ou verificacao de sincronizacao/anuncios.",
            "get_period_comparison",
        )

    alertas = _assistant_first_list(anomalies, "alertas", "anomalias")
    if alertas:
        quedas = [item for item in alertas if isinstance(item, dict) and str(item.get("tipo") or "").lower() == "queda"]
        picos = [item for item in alertas if isinstance(item, dict) and str(item.get("tipo") or "").lower() == "pico"]
        _assistant_add_finding(
            sections,
            "Vendas",
            "Curva de vendas com pontos fora do padrao",
            f"Foram encontrados {len(alertas)} alerta(s): {len(quedas)} queda(s) e {len(picos)} pico(s).",
            "Investigar datas de queda para identificar anuncio pausado, ruptura, alteracao de preco, fim de campanha ou falha de sincronizacao. Replicar causas dos picos quando saudaveis.",
            "warning" if quedas else "info",
            "Anomalias podem esconder perda de venda ou dependencia excessiva de poucos dias/campanhas.",
            "detect_sales_anomalies",
        )


def _assistant_management_returns_profit(results: list[dict[str, Any]], kpis: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]]) -> None:
    return_rate = _assistant_find_result(results, "get_return_rate_by_period")
    returns = _assistant_find_result(results, "get_returns_by_period")
    profit = _assistant_find_result(results, "get_profit_by_period")
    if return_rate:
        taxa_qtd = _assistant_float(return_rate.get("taxa_devolucao_quantidade_percentual"))
        taxa_valor = _assistant_float(return_rate.get("taxa_devolucao_valor_percentual"))
        severity = "critical" if taxa_qtd >= 10 or taxa_valor >= 10 else "warning" if taxa_qtd >= 5 or taxa_valor >= 5 else "info"
        kpis.append({"label": "Taxa devolucao", "value": _assistant_percent(max(taxa_qtd, taxa_valor)), "detail": "Maior taxa entre qtd/valor", "severity": severity})
        if taxa_qtd > 0 or taxa_valor > 0:
            _assistant_add_finding(
                sections,
                "Devolucoes",
                "Devolucoes impactam a rentabilidade",
                f"Taxa por quantidade: {_assistant_percent(taxa_qtd)}; taxa por valor: {_assistant_percent(taxa_valor)}.",
                "Separar top SKUs devolvidos, conferir compatibilidade, descricao, fotos, embalagem e recorrencia por loja/canal. Priorizar itens com devolucao alta e margem baixa.",
                severity,
                "Devolucao consome frete, reputacao, atendimento e capital de giro.",
                "get_return_rate_by_period",
            )

    if returns:
        top_returns = _assistant_first_list(returns, "top_skus")
        if top_returns:
            top = top_returns[0] if isinstance(top_returns[0], dict) else {}
            _assistant_add_finding(
                sections,
                "Devolucoes",
                "SKU com devolucao relevante",
                f"SKU em destaque nas devolucoes: {_assistant_sku_label(top)}. Valor devolvido: {_assistant_money(top.get('valor_devolvido') or top.get('valor') or 0)}.",
                "Revisar anuncio, aplicacao, dimensoes, perguntas frequentes e qualidade do envio antes de escalar campanha desse item.",
                "warning",
                "Um SKU com devolucao recorrente pode parecer vender bem, mas destruir margem no fechamento.",
                "get_returns_by_period",
            )

    if profit:
        margem_raw = profit.get("margem_percentual_estimada")
        lucro_raw = profit.get("lucro_estimado")
        margem = _assistant_float(margem_raw)
        lucro = _assistant_float(lucro_raw)
        skus_considerados = int(_assistant_float(profit.get("skus_considerados")))
        skus_com_custo = int(_assistant_float(profit.get("skus_com_custo")))
        cobertura = _assistant_float(profit.get("cobertura_faturamento_percentual"))
        if not cobertura:
            cobertura = (skus_com_custo / skus_considerados * 100.0) if skus_considerados else 0.0
        coverage_sufficient = bool(profit.get("coverage_sufficient")) if "coverage_sufficient" in profit else cobertura >= 95.0
        if coverage_sufficient and margem_raw is not None and lucro_raw is not None:
            severity = "critical" if margem < 5 else "warning" if margem < 15 else "ok"
            kpis.append({"label": "Lucro estimado", "value": _assistant_money(lucro), "detail": f"Margem {_assistant_percent(margem)} | cobertura {_assistant_percent(cobertura)}", "severity": severity})
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem estimada do periodo",
                f"Lucro estimado: {_assistant_money(lucro)}; margem: {_assistant_percent(margem)}; cobertura ponderada pelo faturamento: {_assistant_percent(cobertura)}.",
                "Priorizar revisao de preco/custo dos SKUs de maior faturamento e manter a cobertura acima de 95%.",
                severity,
                "A margem consolidada so e publicada quando a cobertura minima de dados e atendida.",
                "get_profit_by_period",
            )
        else:
            kpis.append({"label": "Margem consolidada", "value": "indisponivel", "detail": f"Cobertura {_assistant_percent(cobertura)}; minimo 95,0%", "severity": "warning"})
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem consolidada indisponivel",
                f"Cobertura ponderada pelo faturamento: {_assistant_percent(cobertura)}; minimo exigido: 95,0%.",
                "Completar custo, imposto, frete e tarifa dos SKUs de maior faturamento antes de usar lucro ou margem na decisao.",
                "warning",
                "Dados ausentes nao foram tratados como custo zero e nenhum lucro consolidado foi inventado.",
                "get_profit_by_period",
            )
        if skus_considerados and cobertura < 80:
            _assistant_add_finding(
                sections,
                "Margem",
                "Cadastro de custo incompleto",
                f"{skus_com_custo} de {skus_considerados} SKU(s) vendidos tinham custo cadastrado.",
                "Completar custo e imposto dos SKUs vendidos antes de decidir promocao, reposicao ou aumento de verba.",
                "warning",
                "Sem custo, o lucro estimado fica otimista ou impreciso.",
                "get_profit_by_period",
            )


def _assistant_management_margin(context: dict[str, Any], kpis: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]]) -> None:
    margin_rows = _assistant_collect_margin_rows(context, limit=500)
    if margin_rows:
        complete_rows = [row for row in margin_rows if row.get("_margem_completa")]
        valor_completo = sum(_assistant_float(row.get("_valor_num")) for row in complete_rows)
        lucro_completo = sum(_assistant_float(row.get("_lucro_num")) for row in complete_rows)
        margem_real = (lucro_completo / valor_completo * 100.0) if valor_completo > 0 else 0.0
        missing_cost = [row for row in margin_rows if str(row.get("Status da margem") or "") == "custo ausente"]
        incomplete = [row for row in margin_rows if not row.get("_margem_completa")]
        severity = "critical" if margem_real < 5 and complete_rows else "warning" if margem_real < 15 and complete_rows else "ok"
        kpis.append(
            {
                "label": "Margem real por SKU",
                "value": _assistant_percent(margem_real) if complete_rows else "incompleta",
                "detail": f"{len(complete_rows)} de {len(margin_rows)} SKU(s) com custo/imposto/frete/tarifa suficientes",
                "severity": severity if complete_rows else "warning",
            }
        )
        if complete_rows:
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem calculada com custo do cadastro",
                f"Nos SKUs com dados completos, lucro estimado foi {_assistant_money(lucro_completo)} sobre {_assistant_money(valor_completo)}, margem {_assistant_percent(margem_real)}.",
                "Comparar os SKUs de maior faturamento com baixa margem, revisar preco, custo de compra, frete gratis e tarifa antes de escalar campanha.",
                severity,
                "Este calculo usa custo/imposto do cadastro e a mesma logica de margem do modulo Favoritos.",
                "product_costs_and_margin",
            )
        if missing_cost:
            exemplos = ", ".join(str(row.get("SKU") or "") for row in missing_cost[:8])
            _assistant_add_finding(
                sections,
                "Margem",
                "SKU vendido sem custo cadastrado",
                f"{len(missing_cost)} SKU(s) vendidos nao tinham custo localizado no cadastro. Exemplos: {exemplos}.",
                "Cadastrar custo por loja quando existir; se nao houver, preencher custo geral do produto para permitir margem real nos proximos relatorios.",
                "warning",
                "Sem custo cadastrado, o Black Jhon nao deve inventar lucro nem margem.",
                "product_costs_and_margin",
            )
        elif incomplete:
            exemplos = ", ".join(f"{row.get('SKU')} ({row.get('Status da margem')})" for row in incomplete[:6])
            _assistant_add_finding(
                sections,
                "Margem",
                "Margem incompleta por frete/tarifa/imposto",
                f"{len(incomplete)} SKU(s) ainda ficaram com margem incompleta. Exemplos: {exemplos}.",
                "Cruzar os anuncios Mercado Livre para preencher tarifa/frete, e revisar imposto no cadastro antes de decidir promocao.",
                "warning",
                "Margem incompleta deve orientar cadastro e integracao antes de decisao comercial.",
                "product_costs_and_margin",
            )


def _assistant_management_stock(context: dict[str, Any], kpis: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]]) -> None:
    results = context.get("tool_results") if isinstance(context.get("tool_results"), list) else []
    stale = _assistant_find_result(results, "get_days_without_sale_top")
    stale_candidates = [item.get("result") for item in results if isinstance(item, dict)
                        and _assistant_function_name(item) == "get_days_without_sale_top"
                        and isinstance(item.get("result"), dict)]
    if stale_candidates:
        stale = max(stale_candidates, key=lambda value: int(
            ((value.get("resumo_estoque_parado") or {}).get("total_skus")
             if isinstance(value.get("resumo_estoque_parado"), dict) else 0)
            or len(_assistant_first_list(value, "itens"))))
    stockout_report_rows = _assistant_collect_stockout_rows({"tool_results": results}, only_risky=True, limit=50)
    if stockout_report_rows:
        criticos = stockout_report_rows
        if criticos:
            first = criticos[0]
            top_labels = "; ".join(
                f"{item.get('sku')} - {item.get('produto') or ''} (saldo loja {item.get('saldo_considerado')}, media {item.get('media_dia')}/dia, ruptura {item.get('ruptura_prevista')})"
                for item in criticos[:8]
                if isinstance(item, dict)
            )
            kpis.append({"label": "Ruptura alta", "value": str(len(criticos)), "detail": "SKU(s) com risco alto/critico", "severity": "critical"})
            _assistant_add_finding(
                sections,
                "Estoque",
                "Risco de ruptura em SKUs com venda",
                f"{len(criticos)} SKU(s) com risco alto/critico considerando apenas saldo de loja. Principais: {top_labels or first.get('sku') or '-'}.",
                "Gerar lista de reposicao para estoque de loja, conferir compras em aberto e evitar escalar campanha antes de recompor saldo local.",
                "critical",
                "Ruptura interrompe ranking, perde venda e pode elevar custo de recuperacao do anuncio.",
                "get_stockout_forecast",
            )

    stale_items = _assistant_first_list(stale, "itens")
    if stale_items:
        parados = [
            item for item in stale_items
            if isinstance(item, dict) and _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) > 0
        ]
        if parados:
            alert = _assistant_stale_stock_alert_payload(stale, parados)
            evidence = alert["detail"].replace("**", "").replace("`", "")
            kpis.append({"label": "Estoque parado", "value": str(len(parados)), "detail": "SKU(s) com saldo sem venda recente", "severity": "warning"})
            _assistant_add_finding(
                sections,
                "Estoque",
                "Capital parado em produtos sem giro",
                evidence,
                alert["recommendation"],
                "warning",
                "Estoque parado consome capital e espaco, e mascara falta de verba para produtos de maior giro.",
                "get_days_without_sale_top",
            )


def _assistant_management_operations(results: list[dict[str, Any]], warnings: list[str], sections: dict[str, list[dict[str, Any]]]) -> None:
    integrations = _assistant_find_result(results, "get_integrations_status")
    ml_listing = _assistant_find_result(results, "get_mercado_livre_listing")
    lojas = _assistant_first_list(integrations, "lojas")
    if lojas:
        desconectadas = [
            loja for loja in lojas
            if isinstance(loja, dict) and (not loja.get("mercado_livre_conectado") or not loja.get("bling_conectado"))
        ]
        if desconectadas:
            nomes = ", ".join(str(item.get("loja") or "-") for item in desconectadas[:6])
            _assistant_add_finding(
                sections,
                "Integracoes",
                "Integracoes desconectadas ou incompletas",
                f"{len(desconectadas)} loja(s) com Mercado Livre ou Bling desconectado/incompleto: {nomes}.",
                "Regularizar token antes de confiar em estoque, pedidos, anuncios e relatorios externos dessa loja.",
                "warning",
                "Token vencido gera leitura parcial e pode esconder ruptura, pedido ou anuncio problematico.",
                "get_integrations_status",
            )

    matches = _assistant_first_list(ml_listing, "matches")
    if matches:
        ruins = [
            item for item in matches
            if isinstance(item, dict)
            and (
                str(item.get("status") or "").lower() != "active"
                or _assistant_float(item.get("available_quantity")) <= 0
                or (_assistant_float(item.get("health"), 1.0) > 0 and _assistant_float(item.get("health"), 1.0) < 0.7)
            )
        ]
        if ruins:
            first = ruins[0]
            _assistant_add_finding(
                sections,
                "Anuncios",
                "Anuncios do Mercado Livre precisam de revisao",
                f"{len(ruins)} anuncio(s) com status, estoque ou saude abaixo do ideal. Primeiro: {_assistant_sku_label(first)}; status {first.get('status')}; saude {first.get('health')}.",
                "Revisar saude do anuncio, estoque disponivel, preco, fotos, catalogo e tipo de anuncio antes de investir trafego.",
                "warning",
                "Anuncio com baixa saude ou sem estoque reduz conversao e pode bloquear escala de vendas.",
                "get_mercado_livre_listing",
            )

    if warnings:
        _assistant_add_finding(
            sections,
            "Qualidade dos dados",
            "Avisos durante a coleta",
            "; ".join(str(item) for item in warnings[:4]),
            "Resolver avisos de coleta para que o diagnostico tenha cobertura completa.",
            "warning",
            "Dados incompletos podem mudar as prioridades do relatorio.",
            "warnings",
        )


def _assistant_management_diagnostics(context: dict[str, Any], kpis: list[dict[str, Any]], sections: dict[str, list[dict[str, Any]]]) -> None:
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=120)
    if diagnostic_rows:
        immediate = [row for row in diagnostic_rows if str(row.get("Prioridade") or "").startswith("1")]
        high = [row for row in diagnostic_rows if str(row.get("Prioridade") or "").startswith("2")]
        cost_missing = [row for row in diagnostic_rows if str(row.get("Custo ausente") or "").lower() == "sim"]
        kpis.append(
            {
                "label": "Diagnostico por SKU",
                "value": str(len(diagnostic_rows)),
                "detail": f"{len(immediate)} imediatos, {len(high)} altos, {len(cost_missing)} sem custo",
                "severity": "critical" if immediate else ("warning" if high or cost_missing else "info"),
            }
        )
        top_diag = diagnostic_rows[0]
        _assistant_add_finding(
            sections,
            "Diagnostico por SKU",
            "Fila especialista de acao por SKU",
            (
                f"Primeira prioridade: SKU {top_diag.get('SKU')} - {top_diag.get('Produto') or '-'}; "
                f"risco {top_diag.get('Risco')}; impacto {top_diag.get('Impacto financeiro')}."
            ),
            str(top_diag.get("Acao recomendada") or "Executar a fila de acoes priorizadas por SKU."),
            "critical" if immediate else ("warning" if high or cost_missing else "info"),
            "O diagnostico cruza vendas, margem/custo, ruptura de loja e estoque parado para evitar uma decisao baseada em apenas um indicador.",
            "specialist_sku_diagnostics",
        )


def _assistant_management_analysis(context: dict[str, Any]) -> dict[str, Any]:
    results = context.get("tool_results") if isinstance(context.get("tool_results"), list) else []
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    generated_at = str(context.get("generated_at") or _assistant_now())
    kpis: list[dict[str, Any]] = []
    sections: dict[str, list[dict[str, Any]]] = {}
    _assistant_management_sales(results, kpis, sections)
    _assistant_management_returns_profit(results, kpis, sections)
    _assistant_management_margin(context, kpis, sections)
    _assistant_management_stock(context, kpis, sections)
    _assistant_management_operations(results, warnings, sections)
    _assistant_management_diagnostics(context, kpis, sections)
    ordered_sections = [
        {"title": title, "findings": findings}
        for title, findings in sections.items()
        if findings
    ]
    severity_weight = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    for section in ordered_sections:
        section["findings"] = sorted(section["findings"], key=lambda item: severity_weight.get(str(item.get("severity")), 2))

    all_findings = [finding for section in ordered_sections for finding in section.get("findings", [])]
    priority_actions = []
    for finding in sorted(all_findings, key=lambda item: severity_weight.get(str(item.get("severity")), 2)):
        rec = str(finding.get("recommendation") or "").strip()
        if rec and rec not in priority_actions:
            priority_actions.append(rec)
    executive = []
    critical_count = sum(1 for item in all_findings if item.get("severity") == "critical")
    warning_count = sum(1 for item in all_findings if item.get("severity") == "warning")
    if all_findings:
        executive.append(f"Foram encontrados {len(all_findings)} ponto(s) de gestao: {critical_count} critico(s), {warning_count} de atencao.")
    else:
        executive.append("Nenhum ponto critico foi detectado com os dados disponiveis, mas as fontes devem ser revisadas para confirmar cobertura.")
    executive.append(f"Consulta gerada em {generated_at}; fontes consultadas: {len(sources)}.")

    return {
        "generated_at": generated_at,
        "executive_summary": executive,
        "kpis": kpis[:12],
        "sections": ordered_sections,
        "priority_actions": priority_actions[:10],
        "data_quality": {
            "sources_count": len(sources),
            "tool_results_count": int(context.get("tool_results_count") or len(results)),
            "warnings": warnings[:10],
        },
    }

def _assistant_sources(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    for item in results:
        result = item.get("result") if isinstance(item, dict) else {}
        if not isinstance(result, dict):
            result = {}
        name = _assistant_function_name(item if isinstance(item, dict) else {})
        external = any(token in name for token in ("mercado_livre", "bling", "integrations"))
        label = _assistant_human_source_label(name, name)
        sources.append(
            {
                "function": name,
                "function_label": label,
                "source_label": label,
                "arguments": item.get("arguments") or {},
                "records": _assistant_result_count(result),
                "external": external,
                "read_only": True,
            }
        )
    return sources

def _assistant_stale_stock_alert_payload(result: dict[str, Any], parados: list[dict[str, Any]]) -> dict[str, str]:
    def dias_item(item: dict[str, Any]) -> Optional[int]:
        value = item.get("dias_sem_vender")
        if value is None or str(value).strip() == "":
            return None
        return max(0, int(_assistant_float(value)))

    def nunca_vendeu(item: dict[str, Any]) -> bool:
        return _assistant_texto_norm(item.get("status")) == "nunca_vendeu" or (
            dias_item(item) is None and not str(item.get("ultima_venda") or "").strip()
        )

    ordered = sorted(
        (item for item in parados if isinstance(item, dict)),
        key=lambda item: (
            0 if nunca_vendeu(item) else 1,
            -(dias_item(item) or 0),
            -_assistant_float(item.get("saldo_loja", item.get("saldo_total"))),
            str(item.get("sku") or ""),
        ),
    )
    summary = result.get("resumo_estoque_parado") if isinstance(result.get("resumo_estoque_parado"), dict) else {}
    total_skus = int(summary.get("total_skus") or len(ordered))
    total_units = _assistant_float(
        summary.get("total_unidades_loja"),
        sum(_assistant_float(item.get("saldo_loja", item.get("saldo_total"))) for item in ordered),
    )
    never_count = int(summary.get("nunca_venderam") or sum(1 for item in ordered if nunca_vendeu(item)))
    over_180 = int(summary.get("dias_180_mais") or sum(1 for item in ordered if not nunca_vendeu(item) and (dias_item(item) or 0) >= 180))
    between_90_179 = int(summary.get("dias_90_179") or sum(1 for item in ordered if 90 <= (dias_item(item) or -1) < 180))
    between_30_89 = int(summary.get("dias_30_89") or sum(1 for item in ordered if 30 <= (dias_item(item) or -1) < 90))
    unknown_dates = sum(1 for item in ordered if dias_item(item) is None and not nunca_vendeu(item))
    known_cost = [
        item for item in ordered
        if item.get("custo_cadastrado") is True or (
            item.get("custo_cadastrado") is None and item.get("custo_unitario") not in (None, "")
        )
    ]
    known_cost_count = int(summary.get("custos_cobertos") or len(known_cost))
    capital_known = _assistant_float(
        summary.get("capital_custo_conhecido"),
        sum(
            _assistant_float(
                item.get("valor_custo_estoque_loja")
                if item.get("valor_custo_estoque_loja") is not None
                else _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) * _assistant_float(item.get("custo_unitario"))
            )
            for item in known_cost
        ),
    )

    preview_lines: list[str] = []
    for index, item in enumerate(ordered[:4], 1):
        sku = str(item.get("sku") or "-").strip() or "-"
        product = re.sub(r"\s+", " ", str(item.get("produto") or "Produto sem nome").strip())
        if len(product) > 62:
            product = product[:61].rstrip() + "…"
        saldo = _assistant_qty(item.get("saldo_loja", item.get("saldo_total")))
        days = dias_item(item)
        age = "nunca vendeu" if nunca_vendeu(item) else (f"{days} dias sem venda" if days is not None else "ultima venda sem data")
        last_sale = str(item.get("ultima_venda") or "").strip()
        last_text = f" • ultima: {last_sale}" if last_sale else ""
        preview_lines.append(f"{index}. `{sku}` — {product}\n   Saldo: {saldo} un. • {age}{last_text}")

    age_parts = [
        f"{never_count} nunca venderam",
        f"{over_180} com 180+ dias",
        f"{between_90_179} com 90–179 dias",
        f"{between_30_89} com 30–89 dias",
    ]
    if unknown_dates:
        age_parts.append(f"{unknown_dates} sem data confiavel")
    if known_cost_count:
        missing_cost = max(0, total_skus - known_cost_count)
        capital_text = (
            f"**Capital estimado pelo custo cadastrado:** {_assistant_money(capital_known)} em {known_cost_count}/{total_skus} SKU(s)"
            + (f"; {missing_cost} sem custo." if missing_cost else ".")
        )
    else:
        capital_text = f"**Capital:** indisponivel — os {total_skus} SKU(s) estao sem custo utilizavel."

    scope = str(result.get("loja") or "todas as lojas").strip() or "todas as lojas"
    reference_date = str(result.get("data_referencia") or _assistant_today()).strip()
    detail = "\n\n".join(
        [
            f"**Resumo:** {total_skus} SKU(s) e {_assistant_qty(total_units)} unidade(s) paradas em estoque de loja.",
            "**Idade do estoque:** " + " • ".join(age_parts) + ".",
            capital_text,
            "**Mais urgentes:**\n" + "\n".join(preview_lines),
            f"**Base:** {scope} • posicao {reference_date}. No consolidado, o custo usa a media dos cadastros por loja; saldo Full e apenas contexto.",
        ]
    )
    urgent_count = never_count + over_180
    recommendation = (
        f"Priorizar os {urgent_count or total_skus} SKU(s) sem historico ou com 180+ dias: bloquear recompra, validar cadastro/anuncio, "
        "revisar preco e oferta e definir kit, transferencia ou liquidacao com prazo e responsavel."
    )
    report_prompt = (
        "Gere um relatorio detalhado e focado somente no alerta de estoque parado com saldo de loja. "
        "Reconsulte o historico de vendas, o estoque e o cadastro para listar todos os SKUs envolvidos, sem limitar aos primeiros. "
        "Comece por um resumo com total de SKUs, total de unidades, faixas de 30–89, 90–179 e 180+ dias e itens que nunca venderam. "
        "Para cada SKU, informe produto, saldo de loja, saldo Full apenas como contexto, ultima venda, dias parado, custo cadastrado, "
        "capital estimado quando houver custo, prioridade, motivo e acao recomendada. Nao trate custo ausente como zero. "
        "Finalize com uma fila de acao ordenada, prazo sugerido, responsavel recomendado e avisos de dados incompletos."
    )
    return {"detail": detail, "recommendation": recommendation, "report_prompt": report_prompt}

def _assistant_best_stale_suggestion(results: list[dict[str, Any]]) -> Optional[dict[str, str]]:
    best_result: dict[str, Any] = {}
    best_items: list[dict[str, Any]] = []
    best_score = -1
    for candidate in results:
        if not isinstance(candidate, dict) or _assistant_function_name(candidate) != "get_days_without_sale_top":
            continue
        result = candidate.get("result") if isinstance(candidate.get("result"), dict) else {}
        items = result.get("itens") if isinstance(result.get("itens"), list) else []
        stale = [item for item in items if isinstance(item, dict)
                 and _assistant_float(item.get("saldo_loja", item.get("saldo_total"))) > 0
                 and (item.get("dias_sem_vender") is None or _assistant_float(item.get("dias_sem_vender")) >= 30)]
        summary = result.get("resumo_estoque_parado") if isinstance(result.get("resumo_estoque_parado"), dict) else {}
        score = int(summary.get("total_skus") or len(stale))
        if stale and score > best_score:
            best_result, best_items, best_score = result, stale, score
    return _assistant_stale_stock_alert_payload(best_result, best_items) if best_items else None


def _assistant_suggestions_from_results(results: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    today = _assistant_today()

    def add(
        kind: str,
        title: str,
        detail: str,
        severity: str = "info",
        source: str = "",
        recommendation: str = "",
        report_prompt: str = "",
    ) -> None:
        raw = f"{today}|{kind}|{title}|{detail}|{source}"
        sid = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        source_label = _assistant_human_source_label(source, source)
        suggestion = {
            "id": sid,
            "kind": kind,
            "title": title[:120],
            "detail": detail[:1400],
            "severity": severity,
            "source": source_label,
            "source_raw": source,
            "recommendation": recommendation[:700],
            "created_at": _assistant_now(),
        }
        if report_prompt:
            suggestion["report_prompt"] = report_prompt[:1800]
        suggestions.append(suggestion)

    alert = _assistant_best_stale_suggestion(results)
    if alert:
        add(
            "stale_stock",
            "Estoque parado com saldo",
            alert["detail"],
            "warning",
            "get_days_without_sale_top",
            alert["recommendation"],
            alert["report_prompt"],
        )

    for item in results:
        name = _assistant_function_name(item if isinstance(item, dict) else {})
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if name == "get_integrations_status":
            lojas = result.get("lojas") if isinstance(result.get("lojas"), list) else []
            desconectadas = [
                loja
                for loja in lojas
                if not loja.get("mercado_livre_conectado") or not loja.get("bling_conectado")
            ]
            if desconectadas:
                add(
                    "integrations",
                    "Integracoes precisam de atencao",
                    f"{len(desconectadas)} loja(s) tem Mercado Livre ou Bling desconectado/incompleto.",
                    "warning",
                    name,
                )
        elif name == "get_stockout_forecast":
            criticos = _assistant_collect_stockout_rows({"tool_results": [item]}, only_risky=True, limit=50)
            if criticos:
                primeiro = criticos[0]
                top_skus = ", ".join(str(it.get("sku") or "-") for it in criticos[:8])
                add(
                    "stockout",
                    "Risco de ruptura detectado",
                    (
                        f"{len(criticos)} SKU(s) com risco alto/critico considerando apenas saldo de loja. "
                        f"SKUs principais: {top_skus}. Primeiro: {primeiro.get('sku') or '-'} - {primeiro.get('produto') or ''}; "
                        f"saldo loja {primeiro.get('saldo_considerado')}; media/dia {primeiro.get('media_dia')}; "
                        f"ruptura prevista {primeiro.get('ruptura_prevista')}."
                    ),
                    "warning",
                    name,
                    "Gerar relatorio completo no chat com todos os SKUs, motivo por SKU e acao de reposicao de estoque de loja.",
                )
        elif name == "get_days_without_sale_top":
            continue
        elif name == "detect_sales_anomalies":
            anomalias = []
            if isinstance(result.get("anomalias"), list):
                anomalias = result.get("anomalias") or []
            elif isinstance(result.get("alertas"), list):
                anomalias = result.get("alertas") or []
            if anomalias:
                add(
                    "sales_anomaly",
                    "Anomalia de vendas no periodo",
                    f"{len(anomalias)} ponto(s) fora do padrao foram encontrados na curva de vendas.",
                    "warning",
                    name,
                )
        elif name in {"get_returns_by_period", "sales_returns_query"}:
            itens = result.get("items") or result.get("itens") or []
            if name == "sales_returns_query":
                itens = result.get("devolucoes") or result.get("por_sku") or []
            if isinstance(itens, list) and itens:
                add(
                    "returns",
                    "Devolucoes merecem revisao",
                    f"Ha {len(itens)} SKU(s)/registro(s) relevantes de devolucao no periodo consultado.",
                    "info",
                    name,
                )

    if not suggestions and mode == "proactive":
        add("ok", "Nenhum alerta critico agora", "As leituras leves nao encontraram risco operacional relevante neste ciclo.", "ok", "proactive")
    return suggestions[:8]
