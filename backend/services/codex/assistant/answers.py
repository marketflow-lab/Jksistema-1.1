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

from .analysis_management import _assistant_management_analysis
from .analysis_stock import _assistant_collect_sales_rank_rows, _assistant_collect_stale_stock_rows, _assistant_collect_stockout_rows
from .collection import _assistant_collect_data
from .margin_analysis import _assistant_collect_margin_rows, _assistant_specialist_sku_diagnostics
from .runtime import _assistant_now
from .source_labels import _assistant_human_fallback_list, _assistant_human_source_label, _assistant_source_display
from .utils import _assistant_money, _assistant_percent

def codex_assistant_collect_context(
    prompt: str,
    screen_context: Any,
    client_id: str,
    mode: str = "chat",
    force_refresh: bool = False,
) -> dict[str, Any]:
    return _assistant_collect_data(client_id, prompt, screen_context, mode=mode, force_refresh=force_refresh)


def _assistant_answer_registry_lines(registry_results: list[dict[str, Any]], lines: list[str]) -> None:
    sales_rank = next((item for item in registry_results if item.get("tool_id") == "sales_ranking"), None)
    if sales_rank:
        rows = sales_rank.get("rows") if isinstance(sales_rank.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Ranking de SKUs mais vendidos:")
            for idx, row in enumerate(rows[:15], 1):
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or row.get("SKU") or "-"
                nome = row.get("nome") or row.get("produto") or row.get("title") or ""
                qtd = row.get("qtd") or row.get("quantidade") or row.get("quantidade_total") or 0
                valor = row.get("valor") or row.get("valor_total") or 0
                lines.append(f"{idx}. SKU {sku} | {nome} | qtd {qtd} | valor {_assistant_money(valor)}")
        elif sales_rank.get("empty_reason"):
            lines.append("")
            lines.append("Diagnostico da consulta de ranking:")
            lines.append(f"- {sales_rank.get('empty_reason')}")
            fallbacks = sales_rank.get("next_fallbacks") if isinstance(sales_rank.get("next_fallbacks"), list) else []
            if fallbacks:
                fallback_labels = _assistant_human_fallback_list(fallbacks)
                lines.append(f"- Proximas consultas alternativas: {', '.join(fallback_labels or [str(item) for item in fallbacks])}.")
    bling_sales = next((item for item in registry_results if item.get("tool_id") == "bling_sales_orders"), None)
    if bling_sales:
        rows = bling_sales.get("rows") if isinstance(bling_sales.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Dados de vendas consultados na Bling:")
            for idx, row in enumerate(rows[:12], 1):
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or row.get("SKU") or row.get("id") or "-"
                nome = row.get("produto") or row.get("nome") or row.get("numero") or ""
                qtd = row.get("quantidade_vendida") or row.get("quantidade") or "-"
                valor = row.get("valor_total") or row.get("valor") or row.get("valor_total") or 0
                lines.append(f"{idx}. {sku} | {nome} | qtd {qtd} | valor {_assistant_money(valor)}")
    bling_stock = next((item for item in registry_results if item.get("tool_id") == "bling_stock_balances"), None)
    if bling_stock:
        rows = bling_stock.get("rows") if isinstance(bling_stock.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Saldo Bling:")
            for row in rows[:10]:
                if not isinstance(row, dict):
                    continue
                sku = row.get("sku") or "-"
                nome = row.get("produto") or ""
                saldo = row.get("saldo_total")
                lines.append(f"- SKU {sku} | {nome} | saldo total {saldo}")
    bling_fiscal = next((item for item in registry_results if item.get("tool_id") in {"bling_fiscal_product", "bling_fiscal_nfe"}), None)
    if bling_fiscal:
        rows = bling_fiscal.get("rows") if isinstance(bling_fiscal.get("rows"), list) else []
        if rows:
            lines.append("")
            lines.append("Fiscal Bling:")
            for row in rows[:8]:
                if not isinstance(row, dict):
                    continue
                ref = row.get("sku") or row.get("numero") or row.get("id") or "-"
                desc = row.get("nome") or row.get("produto") or row.get("situacao") or ""
                ncm = row.get("ncm")
                cest = row.get("cest")
                extra = f" | NCM {ncm or '-'} | CEST {cest or '-'}" if ncm or cest else ""
                lines.append(f"- {ref} | {desc}{extra}")


def _assistant_answer_analysis_lines(context: dict[str, Any], analysis: dict[str, Any], lines: list[str]) -> None:
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=12)
    if stockout_rows:
        lines.append("")
        lines.append("SKUs com risco de ruptura:")
        for idx, row in enumerate(stockout_rows[:8], 1):
            lines.append(
                f"{idx}. SKU {row.get('sku')} | {row.get('produto') or '-'} | risco {row.get('risco')} | "
                f"saldo loja {row.get('saldo_considerado')} | media/dia {row.get('media_dia')} | "
                f"dias ate ruptura {row.get('dias_ate_ruptura')} | motivo: {row.get('motivo')}"
            )
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    if kpis:
        lines.append("")
        lines.append("Indicadores principais:")
        for item in kpis[:6]:
            lines.append(f"- {item.get('label')}: {item.get('value')} ({item.get('detail')})")
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    findings = []
    for section in sections:
        for finding in section.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(finding)
    if findings:
        lines.append("")
        lines.append("Diagnostico especialista:")
        for item in findings[:6]:
            lines.append(f"- {item.get('title')}: {item.get('evidence')} Acao: {item.get('recommendation')}")
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []
    if actions:
        lines.append("")
        lines.append("Prioridades sugeridas:")
        for idx, action in enumerate(actions[:5], 1):
            lines.append(f"{idx}. {action}")


def _assistant_answer_footer_lines(
    suggestions: list[dict[str, Any]], sources: list[dict[str, Any]], warnings: list[str], lines: list[str],
) -> None:
    if suggestions:
        lines.append("")
        lines.append("Alertas automaticos:")
        for item in suggestions[:5]:
            lines.append(f"- {item.get('title')}: {item.get('detail')}")
    if sources:
        lines.append("")
        lines.append("Fontes consultadas:")
        for src in sources[:10]:
            lines.append(f"- {_assistant_source_display(src)} ({src.get('records')} registro(s))")
    if warnings:
        lines.append("")
        lines.append("Avisos:")
        for warning in warnings[:3]:
            lines.append(f"- {warning}")
    if not suggestions and sources:
        lines.append("")
        lines.append("Os dados estao disponiveis para o Codex responder com mais detalhes no chat principal.")

def _assistant_answer_from_context(message: str, context: dict[str, Any]) -> str:
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else {}
    registry_results = context.get("registry_results") if isinstance(context.get("registry_results"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    if sources:
        external = sum(1 for source in sources if source.get("external"))
        lines = [f"Consultei {len(sources)} fonte(s) read-only do JK Sistema" + (f", incluindo {external} externa(s)" if external else "") + "."]
    else:
        lines = ["Nao encontrei dados estruturados suficientes para responder com seguranca."]
    if tool_plan:
        periodo = tool_plan.get("periodo") if isinstance(tool_plan.get("periodo"), dict) else {}
        lines.append(
            "Escopo usado: "
            f"{periodo.get('data_inicio') or tool_plan.get('data_inicio') or '-'} a "
            f"{periodo.get('data_fim') or tool_plan.get('data_fim') or '-'}; "
            f"loja/conta: {tool_plan.get('loja') or 'todas'}."
        )
    _assistant_answer_registry_lines(registry_results, lines)
    _assistant_answer_analysis_lines(context, analysis, lines)
    _assistant_answer_footer_lines(suggestions, sources, warnings, lines)
    return "\n".join(lines)

def _assistant_advanced_report_chat_text(title: str, context: dict[str, Any], report_id: str = "") -> str:
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    actions = context.get("top_actions") if isinstance(context.get("top_actions"), list) else []
    stores = context.get("store_summaries") if isinstance(context.get("store_summaries"), list) else []

    def money_or_unavailable(value: Any) -> str:
        return _assistant_money(value) if value is not None else "indisponivel"

    margin_coverage_text = (
        _assistant_percent(coverage.get("complete_margin_by_revenue_pct"))
        if coverage.get("complete_margin_by_revenue_pct") is not None
        else "indisponivel"
    )

    lines = [
        f"# {str(title or 'Relatorio Black Jhon')[:140]}",
        "",
        (
            f"Escopo: {scope.get('period_start') or '-'} a {scope.get('period_end') or '-'} | "
            f"comparacao: {scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'} | "
            f"loja: {scope.get('selected_store') or 'consolidado com blocos por loja'}"
        ),
        f"Confiabilidade: {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100) | estoque em: {scope.get('stock_as_of') or 'indisponivel'}",
    ]
    if report_id:
        lines.append(f"Relatorio: {report_id}")
    lines.extend(
        [
            "",
            "## Resumo financeiro",
            f"- Receita bruta: {money_or_unavailable(financial.get('gross_revenue_brl'))}",
            f"- Devolucoes: {money_or_unavailable(financial.get('returns_brl'))}",
            f"- Receita liquida: {money_or_unavailable(financial.get('net_revenue_brl'))}",
            f"- Publicidade: {money_or_unavailable(financial.get('advertising_brl'))}",
            f"- Margem de contribuicao consolidada: {money_or_unavailable(financial.get('contribution_profit_brl'))}",
            f"- Resultado de contribuicao apos publicidade: {money_or_unavailable(financial.get('net_profit_after_ads_brl'))}",
            (
                f"- Cobertura de margem: {margin_coverage_text} "
                f"(minimo {_assistant_percent(coverage.get('minimum_required_pct') or 95)}); status {coverage.get('status') or 'insufficient'}."
            ),
            "",
            "## Decisoes prioritarias",
        ]
    )
    if actions:
        for index, action in enumerate(actions[:5], 1):
            skus = ", ".join(str(item) for item in (action.get("skus") or [])[:8]) or "-"
            lines.extend(
                [
                    f"{index}. **{action.get('title') or 'Acao recomendada'}**",
                    f"   Loja: {action.get('store') or '-'} | SKUs: {skus}",
                    f"   Impacto: {action.get('impact_label') or 'nao estimavel'} | urgencia: {action.get('urgency') or '-'} | confianca: {action.get('confidence') or '-'}",
                    f"   Responsavel: {action.get('owner_username') or action.get('owner_role') or '-'} | prazo: {action.get('due_at') or '-'}",
                    f"   Acao: {action.get('recommendation') or '-'}",
                ]
            )
    else:
        lines.append("- Nenhuma acao prioritaria foi produzida com os dados confiaveis disponiveis.")
    if stores:
        lines.extend(["", "## Lojas"])
        for item in stores[:12]:
            lines.append(
                f"- {item.get('store') or '-'}: receita liquida {money_or_unavailable(item.get('net_revenue_brl'))}; "
                f"variacao {_assistant_percent(item.get('trend_pct')) if item.get('trend_pct') is not None else 'indisponivel'}; "
                f"meta {_assistant_percent(item.get('target_attainment_pct')) if item.get('target_attainment_pct') is not None else 'nao cadastrada'}."
            )
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    if sources:
        lines.extend(["", "## Confiabilidade das fontes"])
        for item in sources[:10]:
            coverage_text = f" | cobertura {item.get('coverage_pct')}%" if item.get("coverage_pct") is not None else ""
            lines.append(f"- {item.get('source') or 'Fonte'}: {item.get('status') or 'indisponivel'} | {item.get('records') or 0} registro(s){coverage_text}.")
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Avisos de dados"])
        lines.extend(f"- {str(item)[:420]}" for item in warnings[:6])
    lines.extend(["", "Use Abrir relatorio para o HTML executivo ou baixe a Planilha XLSX para todos os SKUs."])
    return "\n".join(lines).strip()


def _assistant_report_clean(value: Any, limit: int = 900) -> str:
    text = str(value if value is not None else "").replace("\r", " ").strip()
    return re.sub(r"\s+", " ", text)[:limit].rstrip()


def _assistant_report_severity_label(value: Any) -> str:
    severity = str(value or "info").lower()
    return {"critical": "Critico", "warning": "Atencao", "ok": "OK", "info": "Info"}.get(severity, severity.title())


def _assistant_report_markdown(value: Any, limit: int = 260) -> str:
    return _assistant_report_clean(value, limit).replace("|", "/")


def _assistant_report_initial_lines(
    title: str, context: dict[str, Any], tool_plan: dict[str, Any], analysis: dict[str, Any], report_id: str,
) -> list[str]:
    periodo = tool_plan.get("periodo") if isinstance(tool_plan.get("periodo"), dict) else {}
    data_inicio = periodo.get("data_inicio") or tool_plan.get("data_inicio") or "-"
    data_fim = periodo.get("data_fim") or tool_plan.get("data_fim") or "-"
    loja = tool_plan.get("loja") or "todas"
    intent = tool_plan.get("intent") or tool_plan.get("modo") or "analise operacional"
    lines = [f"# {_assistant_report_clean(title, 120)}", "",
        f"Gerado em: {_assistant_report_clean(context.get('generated_at') or _assistant_now(), 80)}",
        f"Escopo: {_assistant_report_clean(data_inicio, 40)} a {_assistant_report_clean(data_fim, 40)} | loja/conta: {_assistant_report_clean(loja, 120)} | foco: {_assistant_report_clean(intent, 140)}"]
    if report_id:
        lines.append(f"ID: `{_assistant_report_clean(report_id, 80)}`")
    executive = analysis.get("executive_summary") if isinstance(analysis.get("executive_summary"), list) else []
    lines.extend(["", "## Resumo executivo"])
    lines.extend(f"- {_assistant_report_clean(item, 600)}" for item in executive[:8]) if executive else lines.append("- Nenhum resumo executivo foi montado com os dados disponiveis.")
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    if kpis:
        lines.extend(["", "## Indicadores", "| Indicador | Valor | Leitura |", "|---|---:|---|"])
        for item in kpis[:12]:
            lines.append(f"| {_assistant_report_clean(item.get('label'), 120)} | {_assistant_report_clean(item.get('value'), 80)} | {_assistant_report_severity_label(item.get('severity'))}: {_assistant_report_clean(item.get('detail'), 180)} |")
    return lines

def _assistant_append_diagnostic_rows(lines: list[str], diagnostic_rows: list[dict[str, Any]]) -> None:
    if diagnostic_rows:
        lines.extend(
            [
                "",
                "## Diagnostico especialista por SKU",
                "Cada linha cruza vendas, saldo de loja, margem/custo, ruptura e estoque parado. O estoque Full nao entra nas decisoes de ruptura/reposicao de loja.",
                "| SKU | Produto | Motivo | Risco | Impacto financeiro | Margem | Custo ausente | Acao recomendada | Prioridade | Responsavel |",
                "|---|---|---|---|---|---:|---|---|---|---|",
            ]
        )
        for row in diagnostic_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _assistant_report_markdown(row.get("SKU"), 80),
                        _assistant_report_markdown(row.get("Produto"), 170),
                        _assistant_report_markdown(row.get("Motivo"), 360),
                        _assistant_report_markdown(row.get("Risco"), 60),
                        _assistant_report_markdown(row.get("Impacto financeiro"), 220),
                        _assistant_report_markdown(row.get("Margem"), 80),
                        _assistant_report_markdown(row.get("Custo ausente"), 50),
                        _assistant_report_markdown(row.get("Acao recomendada"), 320),
                        _assistant_report_markdown(row.get("Prioridade"), 80),
                        _assistant_report_markdown(row.get("Responsavel"), 100),
                    ]
                )
                + " |"
            )

def _assistant_append_stockout_rows(lines: list[str], stockout_rows: list[dict[str, Any]]) -> None:
    if stockout_rows:
        lines.extend(
            [
                "",
                "## SKUs em risco de ruptura",
                "A analise cruza somente o saldo de loja com a media diaria de vendas. O estoque Full nao entra neste calculo.",
                "| SKU | Produto | Risco | Saldo loja considerado | Media/dia | Dias | Ruptura prevista | Motivo | Acao |",
                "|---|---|---|---:|---:|---:|---|---|---|",
            ]
        )
        for row in stockout_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _assistant_report_markdown(row.get("sku"), 80),
                        _assistant_report_markdown(row.get("produto"), 170),
                        _assistant_report_markdown(row.get("risco"), 50),
                        _assistant_report_markdown(row.get("saldo_considerado"), 40),
                        _assistant_report_markdown(row.get("media_dia"), 40),
                        _assistant_report_markdown(row.get("dias_ate_ruptura"), 40),
                        _assistant_report_markdown(row.get("ruptura_prevista"), 80),
                        _assistant_report_markdown(row.get("motivo"), 360),
                        _assistant_report_markdown(row.get("acao_recomendada"), 320),
                    ]
                )
                + " |"
            )

def _assistant_append_stale_rows(lines: list[str], stale_rows: list[dict[str, Any]]) -> None:
    if stale_rows:
        lines.extend(
            [
                "",
                "## Estoque parado com saldo de loja",
                "A fila esta ordenada por urgencia. O saldo Full aparece somente como contexto e nao reduz o estoque parado da loja.",
                "| Prioridade | SKU | Produto | Saldo loja | Saldo Full* | Situacao | Ultima venda | Capital a custo | Origem do custo | Motivo | Acao |",
                "|---|---|---|---:|---:|---|---|---:|---|---|---|",
            ]
        )
        for row in stale_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _assistant_report_markdown(row.get("prioridade"), 80),
                        _assistant_report_markdown(row.get("sku"), 80),
                        _assistant_report_markdown(row.get("produto"), 180),
                        _assistant_report_markdown(row.get("saldo_loja"), 50),
                        _assistant_report_markdown(row.get("saldo_full"), 50),
                        _assistant_report_markdown(row.get("situacao"), 100),
                        _assistant_report_markdown(row.get("ultima_venda"), 90),
                        _assistant_report_markdown(row.get("capital_custo"), 100),
                        _assistant_report_markdown(row.get("custo_origem"), 100),
                        _assistant_report_markdown(row.get("motivo"), 280),
                        _assistant_report_markdown(row.get("acao_recomendada"), 320),
                    ]
                )
                + " |"
            )

def _assistant_append_sales_rows(lines: list[str], sales_rows: list[dict[str, Any]]) -> None:
    if sales_rows:
        lines.extend(
            [
                "",
                "## SKUs que puxam vendas",
                "| SKU | Produto | Quantidade | Valor | Pedidos | Analise |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for row in sales_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _assistant_report_markdown(row.get("sku"), 80),
                        _assistant_report_markdown(row.get("produto"), 190),
                        _assistant_report_markdown(row.get("quantidade_vendida"), 60),
                        _assistant_report_markdown(row.get("valor_vendido"), 80),
                        _assistant_report_markdown(row.get("pedidos"), 60),
                        _assistant_report_markdown(row.get("analise"), 260),
                    ]
                )
                + " |"
            )

def _assistant_append_margin_rows(lines: list[str], margin_rows: list[dict[str, Any]]) -> None:
    if margin_rows:
        lines.extend(
            [
                "",
                "## Margem real por SKU vendido",
                "A margem usa custo/imposto do cadastro e a formula do Favoritos. Quando faltar custo, frete, tarifa ou imposto, o status fica incompleto.",
                "| SKU | Produto | Qtd vendida | Valor vendido | Custo unitario | Custo total | Imposto | Frete | Tarifa | Lucro estimado | Margem % | Status |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in margin_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _assistant_report_markdown(row.get("SKU"), 80),
                        _assistant_report_markdown(row.get("Produto"), 180),
                        _assistant_report_markdown(row.get("Qtd vendida"), 60),
                        _assistant_report_markdown(row.get("Valor vendido"), 80),
                        _assistant_report_markdown(row.get("Custo unitario"), 80),
                        _assistant_report_markdown(row.get("Custo total"), 80),
                        _assistant_report_markdown(row.get("Imposto"), 60),
                        _assistant_report_markdown(row.get("Frete"), 70),
                        _assistant_report_markdown(row.get("Tarifa"), 70),
                        _assistant_report_markdown(row.get("Lucro estimado"), 90),
                        _assistant_report_markdown(row.get("Margem %"), 70),
                        _assistant_report_markdown(row.get("Status da margem"), 120),
                    ]
                )
                + " |"
            )

def _assistant_append_report_footer(
    lines: list[str], analysis: dict[str, Any], suggestions: list[dict[str, Any]],
    sources: list[dict[str, Any]], warnings: list[str],
) -> None:
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    if actions:
        lines.extend(["", "## Prioridades recomendadas"])
        for idx, action in enumerate(actions[:10], 1):
            lines.append(f"{idx}. {_assistant_report_clean(action, 700)}")

    lines.extend(["", "## Diagnostico por area"])
    if sections:
        for section in sections[:10]:
            section_title = _assistant_report_clean(section.get("title"), 120)
            findings = section.get("findings") if isinstance(section.get("findings"), list) else []
            if not findings:
                continue
            lines.extend(["", f"### {section_title}"])
            for finding in findings[:8]:
                if not isinstance(finding, dict):
                    continue
                marker = _assistant_report_severity_label(finding.get("severity"))
                title_line = _assistant_report_clean(finding.get("title"), 180)
                evidence = _assistant_report_clean(finding.get("evidence"), 650)
                impact = _assistant_report_clean(finding.get("impact"), 420)
                recommendation = _assistant_report_clean(finding.get("recommendation"), 650)
                source = _assistant_report_clean(_assistant_human_source_label(finding.get("source"), finding.get("source")), 140)
                lines.append(f"- **{marker} - {title_line}**")
                if evidence:
                    lines.append(f"  Evidencia: {evidence}")
                if impact:
                    lines.append(f"  Impacto: {impact}")
                if recommendation:
                    lines.append(f"  Acao sugerida: {recommendation}")
                if source:
                    lines.append(f"  Fonte: `{source}`")
    else:
        lines.append("- Nenhum diagnostico por area foi produzido com as consultas executadas.")

    if suggestions:
        lines.extend(["", "## Alertas automaticos"])
        for item in suggestions[:12]:
            if not isinstance(item, dict):
                continue
            title_alert = _assistant_report_clean(item.get("title"), 160)
            detail = _assistant_report_clean(item.get("detail"), 520)
            recommendation = _assistant_report_clean(item.get("recommendation"), 520)
            line = f"- **{title_alert}**: {detail}"
            if recommendation:
                line += f" Acao: {recommendation}"
            lines.append(line)

    if sources:
        lines.extend(["", "## Fontes consultadas"])
        for src in sources[:18]:
            if not isinstance(src, dict):
                continue
            flags = []
            if src.get("external"):
                flags.append("externa")
            if src.get("read_only"):
                flags.append("read-only")
            suffix = f" ({', '.join(flags)})" if flags else ""
            lines.append(f"- {_assistant_report_clean(_assistant_source_display(src), 140)}: {_assistant_report_clean(src.get('records'), 40)} registro(s){suffix}.")

    if warnings:
        lines.extend(["", "## Avisos de dados"])
        for warning in warnings[:10]:
            lines.append(f"- {_assistant_report_clean(warning, 500)}")

    lines.extend(["", "Os botoes abaixo tambem deixam uma copia em PDF ou Planilha XLSX para baixar."])
    return "\n".join(lines).strip()

def _assistant_report_chat_text(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]], report_id: str = "") -> str:
    if str(context.get("report_type") or "").strip():
        return _assistant_advanced_report_chat_text(title, context, report_id)
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    limit = 500
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=limit)
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=limit)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=limit)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=limit)
    margin_rows = _assistant_collect_margin_rows(context, limit=limit)
    lines = _assistant_report_initial_lines(title, context, tool_plan, analysis, report_id)
    _assistant_append_diagnostic_rows(lines, diagnostic_rows)
    _assistant_append_stockout_rows(lines, stockout_rows)
    _assistant_append_stale_rows(lines, stale_rows)
    _assistant_append_sales_rows(lines, sales_rows)
    _assistant_append_margin_rows(lines, margin_rows)
    _assistant_append_report_footer(lines, analysis, suggestions, sources, warnings)
    return "\n".join(lines).strip()

def render_chat(
    title: str,
    context: dict[str, Any],
    suggestions: list[dict[str, Any]],
    report_id: str = "",
) -> str:
    """Render a report result for conversational channels."""

    return _assistant_report_chat_text(title, context, suggestions, report_id)
