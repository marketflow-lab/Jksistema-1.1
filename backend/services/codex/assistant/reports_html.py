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
from .margin_analysis import _assistant_collect_margin_rows, _assistant_specialist_sku_diagnostics
from .runtime import _assistant_client_dir, _assistant_now, _assistant_safe_id
from .source_labels import _assistant_human_source_label, _assistant_source_display
from .utils import _assistant_float, _assistant_money, _assistant_percent

def _assistant_report_dir(client_id: str, report_id: str) -> str:
    path = os.path.join(_assistant_client_dir(client_id), "reports", _assistant_safe_id(report_id))
    os.makedirs(path, exist_ok=True)
    return path


def _assistant_flatten_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in results:
        raw_name = str(item.get("function") or "consulta")
        name = _assistant_human_source_label(raw_name, raw_name)
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        collections = []
        for key in ("vendas", "devolucoes", "por_sku", "por_loja", "items", "itens", "matches", "lojas", "anomalias", "top_skus"):
            value = result.get(key)
            if isinstance(value, list):
                collections.append((key, value))
        if not collections:
            rows.append(
                {
                    "fonte": name,
                    "tipo": "resumo",
                    "dados": codex_turn_context.bounded_json(result, max_bytes=1000),
                }
            )
            continue
        for key, values in collections:
            limit = len(values) if name == "sales_returns_query" and key in {"vendas", "devolucoes", "por_sku", "por_loja"} else min(len(values), 500)
            for value in values[:limit]:
                if isinstance(value, dict):
                    row = {"fonte": name, "tipo": key}
                    for k, v in value.items():
                        if isinstance(v, (dict, list)):
                            row[str(k)] = codex_turn_context.bounded_json(v, max_bytes=600)
                        else:
                            row[str(k)] = v
                    rows.append(row)
    return rows[:5000]


def _report_escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _advanced_report_table(section_title: str, rows: Any, columns: list[tuple[str, str]]) -> str:
    values = [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict)][:20]
    if not values:
        return ""
    parts = [f"<h2>{_report_escape(section_title)}</h2><div class=\"table-wrap\"><table><thead><tr>"]
    parts.extend(f"<th>{_report_escape(label)}</th>" for key, label in columns)
    parts.append("</tr></thead><tbody>")
    for row in values:
        parts.append("<tr>")
        for key, _ in columns:
            value = row.get(key)
            if (key.endswith("_brl") or key in {"revenue", "impact_brl"}) and value is not None:
                value = _assistant_money(value)
            elif key.endswith("_pct") and value is not None:
                value = _assistant_percent(value)
            parts.append(f"<td>{_report_escape(value if value is not None else 'Indisponivel')}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)

def _advanced_report_state(title: str, context: dict[str, Any]) -> dict[str, Any]:
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    actions = context.get("top_actions") if isinstance(context.get("top_actions"), list) else []

    margin_coverage_text = (
        _assistant_percent(coverage.get("complete_margin_by_revenue_pct"))
        if coverage.get("complete_margin_by_revenue_pct") is not None else "Indisponivel"
    )
    money = lambda value: _assistant_money(value) if value is not None else "Indisponivel"
    quality_class = "ok" if quality.get("confidence") == "alta" else "warning" if quality.get("confidence") == "média" else "critical"
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{_report_escape(title)}</title>",
        "<style>body{font-family:Arial,sans-serif;margin:28px;color:#13202c;background:#fff;line-height:1.42}h1{font-size:25px;margin:0 0 8px}h2{font-size:18px;margin:24px 0 10px;color:#0f3557}.meta{color:#526170;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px}.card{border:1px solid #d5dde6;border-radius:9px;padding:11px;background:#f8fbff}.card strong{display:block;font-size:17px;margin-top:3px}.quality{border-left:6px solid #d97706}.quality.ok{border-left-color:#059669}.quality.critical{border-left-color:#dc2626}.action{border:1px solid #d5dde6;border-left:5px solid #2563eb;border-radius:9px;padding:12px;margin:9px 0;background:#fbfdff}.action.immediate{border-left-color:#dc2626}.action.high{border-left-color:#ea580c}.pill{display:inline-block;padding:3px 8px;border-radius:999px;background:#e8f5f2;color:#075d56;font-size:11px;font-weight:700;margin-right:5px}.table-wrap{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;font-size:12px}td,th{border:1px solid #d5dde6;padding:7px;text-align:left;vertical-align:top}th{background:#eef5ff;color:#0f3557;white-space:nowrap}.warning-text{color:#9a3412}.footer{margin-top:26px;border-top:1px solid #d5dde6;padding-top:10px;color:#526170;font-size:11px}</style></head><body>",
        f"<h1>{_report_escape(title)}</h1>",
        f"<p class=\"meta\">Gerado em {_report_escape(scope.get('generated_at') or _assistant_now())}. Periodo {_report_escape(scope.get('period_start') or '-')} a {_report_escape(scope.get('period_end') or '-')}; comparacao {_report_escape(scope.get('comparison_start') or '-')} a {_report_escape(scope.get('comparison_end') or '-')}.</p>",
        "<h2>Confiabilidade dos dados</h2>",
        f"<div class=\"card quality {quality_class}\"><span class=\"pill\">{_report_escape(quality.get('confidence') or 'baixa')}</span><strong>{_report_escape(quality.get('score') or 0)}/100</strong><span>Estoque em {_report_escape(scope.get('stock_as_of') or 'indisponivel')}; loja {_report_escape(scope.get('selected_store') or 'consolidado com blocos por loja')}.</span></div>",
        "<h2>Resumo financeiro</h2><div class=\"grid\">",
    ]
    financial_cards = [
        ("Receita bruta", financial.get("gross_revenue_brl")),
        ("Devolucoes", financial.get("returns_brl")),
        ("Receita liquida", financial.get("net_revenue_brl")),
        ("Publicidade", financial.get("advertising_brl")),
        ("Margem de contribuicao", financial.get("contribution_profit_brl")),
        ("Resultado de contribuicao apos publicidade", financial.get("net_profit_after_ads_brl")),
        ("Resultado de contribuicao apos publicidade e devolucoes reconciliadas", financial.get("net_profit_after_ads_and_returns_brl")),
    ]
    for label, value in financial_cards:
        parts.append(f"<div class=\"card\"><span>{_report_escape(label)}</span><strong>{_report_escape(money(value))}</strong></div>")
    parts.append("</div>")
    parts.append(
        f"<p class=\"meta\">Cobertura completa de margem: {_report_escape(margin_coverage_text)}; "
        f"minimo exigido: {_report_escape(_assistant_percent(coverage.get('minimum_required_pct') or 95))}. "
        f"Receita coberta: {_report_escape(money(coverage.get('covered_revenue_brl')))}; "
        f"receita nao coberta: {_report_escape(money(coverage.get('uncovered_revenue_brl')))}. "
        "Valores ausentes nao foram tratados como zero.</p>"
    )

    return {"parts": parts, "quality": quality, "actions": actions, "money": money}


def _append_advanced_margin_tables(parts: list[str], context: dict[str, Any]) -> None:
    parts.append(
        _advanced_report_table(
            "Contribuicao unitaria atual por anuncio e variacao",
            context.get("listing_margin_rows"),
            [("store", "Loja"), ("mlb", "MLB"), ("variation_id", "Variacao"), ("sku", "SKU"),
             ("current_price_brl", "Preco atual"), ("unit_cost_brl", "Custo"), ("tax_pct", "Imposto %"),
             ("ml_fee_brl", "Tarifa ML"), ("seller_shipping_brl", "Frete vendedor"),
             ("unit_contribution_brl", "Contribuicao"), ("contribution_margin_pct", "Margem %"),
             ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        )
    )
    parts.append(
        _advanced_report_table(
            "Resultado historico reconciliado por anuncio",
            context.get("historical_margin_ledger"),
            [("store", "Loja"), ("order_id", "Pedido"), ("line_number", "Linha"), ("pack_id", "Pack"), ("mlb", "MLB"),
             ("variation_id", "Variacao"), ("sku", "SKU"), ("quantity", "Qtd"),
             ("gross_amount_brl", "Receita vendida"), ("contribution_total_brl", "Contribuicao"),
             ("shipping_scope_label", "Escopo frete"), ("margin_status_label", "Status"),
             ("missing_components_text", "Ausentes")],
        )
    )

def _append_advanced_actions(parts: list[str], actions: list[dict[str, Any]]) -> None:
    parts.append("<h2>Decisoes prioritarias</h2>")
    if not actions:
        parts.append("<p>Nenhuma acao prioritaria foi produzida com os dados confiaveis disponiveis.</p>")
    for action in actions[:5]:
        urgency = str(action.get("urgency") or "medium")
        parts.append(f"<div class=\"action {_report_escape(urgency)}\">")
        parts.append(f"<strong>{_report_escape(action.get('title') or 'Acao recomendada')}</strong>")
        parts.append(
            f"<p><span class=\"pill\">{_report_escape(action.get('impact_label') or 'nao estimavel')}</span>"
            f"<span class=\"pill\">urgencia {_report_escape(urgency)}</span><span class=\"pill\">confianca {_report_escape(action.get('confidence') or '-')}</span></p>"
        )
        parts.append(f"<p>{_report_escape(action.get('evidence') or '')}</p><p><strong>Acao:</strong> {_report_escape(action.get('recommendation') or '-')}</p>")
        parts.append(f"<p class=\"meta\">Loja {_report_escape(action.get('store') or '-')}; SKUs {_report_escape(', '.join(str(item) for item in (action.get('skus') or [])[:12]) or '-')}; responsavel {_report_escape(action.get('owner_username') or action.get('owner_role') or '-')}; prazo {_report_escape(action.get('due_at') or '-')}.</p></div>")

def _append_advanced_operational(parts: list[str], context: dict[str, Any]) -> None:
    parts.append(
        _advanced_report_table(
            "Desempenho por loja",
            context.get("store_summaries"),
            [("store", "Loja"), ("net_revenue_brl", "Receita liquida"), ("trend_pct", "Variacao"), ("target_attainment_pct", "Meta"), ("orders", "Pedidos"), ("units", "Unidades")],
        )
    )
    parts.append(
        _advanced_report_table(
            "SKUs que puxam vendas",
            context.get("sales_rows"),
            [("store", "Loja"), ("sku", "SKU"), ("product", "Produto"), ("abc", "ABC"), ("revenue", "Receita"), ("revenue_share_pct", "Participacao"), ("trend_pct", "Tendencia")],
        )
    )
    parts.append(
        _advanced_report_table(
            "Planejamento de estoque",
            context.get("inventory_rows"),
            [("store", "Loja"), ("sku", "SKU"), ("abc", "ABC"), ("xyz", "XYZ"), ("local_stock", "Local"), ("full_stock", "Full"), ("coverage_days", "Cobertura dias"), ("reorder_point", "Ponto reposicao"), ("suggested_purchase", "Compra sugerida"), ("capital_tied_brl", "Capital parado")],
        )
    )
    parts.append(
        _advanced_report_table(
            "Compras e mercadoria em fluxo",
            context.get("purchase_pipeline_rows"),
            [("store", "Loja"), ("order_name", "Pedido"), ("supplier", "Fornecedor"), ("status", "Status"), ("sku", "SKU"), ("quantity", "Quantidade"), ("eta", "Previsao")],
        )
    )

def _append_advanced_tail(
    parts: list[str], context: dict[str, Any], quality: dict[str, Any], money: Any,
) -> str:
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else None
    if import_analysis:
        parts.append("<h2>Importacao</h2><div class=\"grid\">")
        for label, value in (
            ("FOB", f"US$ {_assistant_float(import_analysis.get('fob_usd')):,.2f}"),
            ("Frete internacional", f"US$ {_assistant_float(import_analysis.get('freight_usd')):,.2f}"),
            ("Caixa necessario", money(import_analysis.get("cash_required_brl"))),
            ("Faixa tributaria", _assistant_percent(import_analysis.get("tax_band_pct") or 0)),
        ):
            parts.append(f"<div class=\"card\"><span>{_report_escape(label)}</span><strong>{_report_escape(value)}</strong></div>")
        parts.append("</div>")
        parts.append(
            _advanced_report_table(
                "Custo posto por SKU",
                import_analysis.get("items"),
                [("sku", "SKU"), ("quantity", "Quantidade"), ("fob_usd", "FOB USD"), ("landed_total_brl", "Custo posto total"), ("landed_unit_brl", "Custo posto unitario"), ("minimum_sale_price_brl", "Preco minimo")],
            )
        )
        parts.append(_advanced_report_table("Cenarios", import_analysis.get("scenarios"), [("kind", "Cenario"), ("change_pct", "Variacao"), ("delay_days", "Atraso dias"), ("cash_required_brl", "Caixa necessario")]))
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    parts.append(_advanced_report_table("Fontes consultadas", sources, [("source", "Fonte"), ("status", "Status"), ("records", "Registros"), ("last_sync_at", "Atualizacao"), ("coverage_pct", "Cobertura")]))
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        parts.append("<h2>Avisos</h2><ul class=\"warning-text\">")
        parts.extend(f"<li>{_report_escape(item)}</li>" for item in warnings[:20])
        parts.append("</ul>")
    parts.append("<p class=\"footer\">Relatorio executivo read-only. A Planilha XLSX contem todos os SKUs. Acoes internas exigem aprovacao de administrador full e nunca alteram anuncios ou estoque externo.</p></body></html>")
    return "".join(parts)

def _assistant_build_advanced_report_html(title: str, context: dict[str, Any]) -> str:
    state = _advanced_report_state(title, context)
    parts = state["parts"]
    _append_advanced_margin_tables(parts, context)
    _append_advanced_actions(parts, state["actions"])
    _append_advanced_operational(parts, context)
    return _append_advanced_tail(parts, context, state["quality"], state["money"])


def _severity_label(value: Any) -> str:
    severity = str(value or "info")
    return {"critical": "Critico", "warning": "Atencao", "ok": "OK", "info": "Info"}.get(severity, severity.title())


def _standard_report_state(
    title: str, context: dict[str, Any], suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    generated = html.escape(str(context.get("generated_at") or _assistant_now()))
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    warnings = context.get("warnings") if isinstance(context.get("warnings"), list) else []
    rows = _assistant_flatten_rows(context.get("tool_results") if isinstance(context.get("tool_results"), list) else [])
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    sections = analysis.get("sections") if isinstance(analysis.get("sections"), list) else []
    actions = analysis.get("priority_actions") if isinstance(analysis.get("priority_actions"), list) else []
    executive = analysis.get("executive_summary") if isinstance(analysis.get("executive_summary"), list) else []
    tool_plan = context.get("tool_plan") if isinstance(context.get("tool_plan"), dict) else {}
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=120)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=120)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=60)
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=500)
    margin_rows = _assistant_collect_margin_rows(context, limit=500)
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        f"<title>{_report_escape(title)}</title>",
        "<style>"
        "body{font-family:Arial,sans-serif;margin:28px;color:#13202c;background:#fff;line-height:1.42}"
        "h1{font-size:25px;margin:0 0 10px}h2{font-size:18px;margin:26px 0 10px;color:#0f3557}h3{font-size:15px;margin:18px 0 8px;color:#13202c}"
        "p{margin:7px 0}.meta{color:#516070;font-size:12px}.pill{display:inline-block;padding:4px 9px;border-radius:99px;background:#e8f5f2;color:#075d56;font-weight:700}"
        ".kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0 18px}"
        ".kpi{border:1px solid #d5dde6;border-left:5px solid #4d8fd9;border-radius:8px;padding:10px;background:#f8fbff}"
        ".kpi strong{display:block;font-size:18px;margin-top:2px}.kpi span{font-size:12px;color:#536271}"
        ".insight{border:1px solid #d5dde6;border-radius:10px;background:#f8fbff;padding:12px;margin:12px 0}.insight strong{color:#0f3557}"
        ".finding{border:1px solid #d5dde6;border-radius:8px;padding:10px 12px;margin:9px 0;background:#fbfdff}"
        ".finding.critical{border-left:5px solid #dc2626}.finding.warning{border-left:5px solid #f59e0b}.finding.ok{border-left:5px solid #059669}.finding.info{border-left:5px solid #3b82f6}"
        ".tag{display:inline-block;font-size:11px;font-weight:800;border-radius:99px;padding:2px 7px;background:#eef2ff;color:#3730a3;margin-right:6px}"
        ".action-list li{margin:7px 0}table{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0 18px}td,th{border:1px solid #d5dde6;padding:7px;text-align:left;vertical-align:top}th{background:#eef5ff;color:#0f3557}.warn{color:#9a3412}.num{text-align:right;white-space:nowrap}.critical-cell{color:#b91c1c;font-weight:800}.muted{color:#516070;font-size:12px}"
        "</style>",
        "</head><body>",
        f"<h1>{_report_escape(title)}</h1>",
        f"<p><span class=\"pill\">Gerado em {generated}</span></p>",
        "<p class=\"meta\">Relatorio gerencial read-only. Alteracoes em arquivos, anuncios, sincronizacoes ou respostas externas continuam exigindo aprovacao explicita.</p>",
        "<h2>Resumo executivo</h2>",
    ]
    if executive:
        parts.append("<ul>")
        for item in executive:
            parts.append(f"<li>{_report_escape(item)}</li>")
        parts.append("</ul>")
    else:
        parts.append("<p>Nao houve resumo executivo disponivel para os dados coletados.</p>")

    if tool_plan:
        parts.append("<h2>Escopo do relatorio</h2>")
        parts.append(
            f"<p>Periodo usado: <strong>{_report_escape(tool_plan.get('data_inicio') or '-')} a {_report_escape(tool_plan.get('data_fim') or '-')}</strong>. "
            f"Loja/conta: <strong>{_report_escape(tool_plan.get('loja') or 'todas')}</strong>. "
            f"Foco: <strong>{_report_escape(tool_plan.get('intent') or '-')}</strong>.</p>"
        )

    if kpis:
        parts.append("<h2>Indicadores-chave</h2><div class=\"kpis\">")
        for item in kpis:
            severity = _report_escape(item.get("severity") or "info")
            parts.append(
                f"<div class=\"kpi {severity}\"><span>{_report_escape(item.get('label'))}</span>"
                f"<strong>{_report_escape(item.get('value'))}</strong><span>{_report_escape(item.get('detail'))}</span></div>"
            )
        parts.append("</div>")

    return {"parts": parts, "sources": sources, "warnings": warnings, "rows": rows,
        "sections": sections, "actions": actions, "suggestions": suggestions,
        "diagnostic_rows": diagnostic_rows, "stockout_rows": stockout_rows,
        "stale_rows": stale_rows, "sales_rows": sales_rows, "margin_rows": margin_rows}


def _append_standard_diagnostic_stockout(parts: list[str], state: dict[str, Any]) -> None:
    diagnostic_rows, stockout_rows = state["diagnostic_rows"], state["stockout_rows"]
    if any(state.get(key) for key in ("diagnostic_rows", "stockout_rows", "stale_rows", "sales_rows", "margin_rows")):
        parts.append("<h2>Tabelas operacionais</h2>")
    if diagnostic_rows:
        parts.append(
            "<div class=\"insight\"><strong>Diagnostico especialista:</strong> esta tabela cruza vendas, saldo de loja, margem/custo, ruptura e estoque parado. "
            "O estoque Full nao entra nas decisoes de ruptura ou reposicao de loja.</div>"
        )
        parts.append(
            "<h3>Diagnostico especialista por SKU</h3>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Motivo</th><th>Risco</th><th>Impacto financeiro</th><th>Margem</th><th>Custo ausente</th><th>Acao recomendada</th><th>Prioridade</th><th>Responsavel</th></tr></thead><tbody>"
        )
        for row in diagnostic_rows:
            risk_class = "critical-cell" if str(row.get("Risco") or "").lower() in {"critico", "alto"} else ""
            parts.append(
                "<tr>"
                f"<td>{_report_escape(row.get('SKU'))}</td>"
                f"<td>{_report_escape(row.get('Produto'))}</td>"
                f"<td>{_report_escape(row.get('Motivo'))}</td>"
                f"<td class=\"{risk_class}\">{_report_escape(row.get('Risco'))}</td>"
                f"<td>{_report_escape(row.get('Impacto financeiro'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Margem'))}</td>"
                f"<td>{_report_escape(row.get('Custo ausente'))}</td>"
                f"<td>{_report_escape(row.get('Acao recomendada'))}</td>"
                f"<td>{_report_escape(row.get('Prioridade'))}</td>"
                f"<td>{_report_escape(row.get('Responsavel'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if stockout_rows:
        parts.append(
            "<div class=\"insight\"><strong>Leitura da ruptura:</strong> a tabela abaixo cruza apenas o saldo de loja "
            "com a velocidade de venda. O estoque Full nao entra no calculo deste relatorio.</div>"
        )
        parts.append(
            "<h3>SKUs em risco de ruptura</h3>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Risco</th><th>Saldo loja considerado</th><th>Media/dia</th><th>Dias</th><th>Previsao</th><th>Motivo</th><th>Acao recomendada</th></tr></thead><tbody>"
        )
        for row in stockout_rows:
            risk_class = "critical-cell" if str(row.get("risco") or "").lower() in {"critico", "alto"} else ""
            parts.append(
                "<tr>"
                f"<td>{_report_escape(row.get('sku'))}</td>"
                f"<td>{_report_escape(row.get('produto'))}</td>"
                f"<td class=\"{risk_class}\">{_report_escape(row.get('risco'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('saldo_considerado'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('media_dia'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('dias_ate_ruptura'))}</td>"
                f"<td>{_report_escape(row.get('ruptura_prevista'))}</td>"
                f"<td>{_report_escape(row.get('motivo'))}</td>"
                f"<td>{_report_escape(row.get('acao_recomendada'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

def _append_standard_stale_sales_margin(parts: list[str], state: dict[str, Any]) -> None:
    stale_rows, sales_rows, margin_rows = state["stale_rows"], state["sales_rows"], state["margin_rows"]
    if stale_rows:
        parts.append(
            "<h3>Estoque parado com saldo de loja</h3>"
            "<p class=\"muted\">Fila ordenada por urgencia. O saldo Full aparece somente como contexto e nao reduz o estoque parado da loja. Capital indisponivel significa custo ausente, nunca custo zero presumido.</p>"
            "<table><thead><tr><th>Prioridade</th><th>SKU</th><th>Produto</th><th>Saldo loja</th><th>Saldo Full*</th><th>Situacao</th><th>Ultima venda</th><th>Capital a custo</th><th>Origem do custo</th><th>Motivo</th><th>Acao recomendada</th></tr></thead><tbody>"
        )
        for row in stale_rows:
            priority_class = "critical-cell" if str(row.get("prioridade") or "").startswith("1") else ""
            parts.append(
                "<tr>"
                f"<td class=\"{priority_class}\">{_report_escape(row.get('prioridade'))}</td>"
                f"<td>{_report_escape(row.get('sku'))}</td>"
                f"<td>{_report_escape(row.get('produto'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('saldo_loja'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('saldo_full'))}</td>"
                f"<td>{_report_escape(row.get('situacao'))}</td>"
                f"<td>{_report_escape(row.get('ultima_venda'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('capital_custo'))}</td>"
                f"<td>{_report_escape(row.get('custo_origem'))}</td>"
                f"<td>{_report_escape(row.get('motivo'))}</td>"
                f"<td>{_report_escape(row.get('acao_recomendada'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if sales_rows:
        parts.append(
            "<h3>SKUs que puxam vendas</h3>"
            "<p class=\"muted\">Use esta lista para cruzar giro, reposicao, margem e investimento em anuncio.</p>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Quantidade</th><th>Valor</th><th>Pedidos</th><th>Analise</th></tr></thead><tbody>"
        )
        for row in sales_rows:
            parts.append(
                "<tr>"
                f"<td>{_report_escape(row.get('sku'))}</td>"
                f"<td>{_report_escape(row.get('produto'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('quantidade_vendida'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('valor_vendido'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('pedidos'))}</td>"
                f"<td>{_report_escape(row.get('analise'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    if margin_rows:
        parts.append(
            "<h3>Margem real por SKU vendido</h3>"
            "<p class=\"muted\">A margem usa custo/imposto do cadastro e a formula do Favoritos. Status incompleto indica dados que nao devem ser inventados.</p>"
            "<table><thead><tr><th>SKU</th><th>Produto</th><th>Qtd vendida</th><th>Valor vendido</th><th>Custo unitario</th><th>Custo total</th><th>Imposto</th><th>Frete</th><th>Tarifa</th><th>Lucro estimado</th><th>Margem %</th><th>Status</th></tr></thead><tbody>"
        )
        for row in margin_rows:
            parts.append(
                "<tr>"
                f"<td>{_report_escape(row.get('SKU'))}</td>"
                f"<td>{_report_escape(row.get('Produto'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Qtd vendida'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Valor vendido'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Custo unitario'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Custo total'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Imposto'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Frete'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Tarifa'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Lucro estimado'))}</td>"
                f"<td class=\"num\">{_report_escape(row.get('Margem %'))}</td>"
                f"<td>{_report_escape(row.get('Status da margem'))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

def _append_standard_tail(parts: list[str], state: dict[str, Any]) -> str:
    actions, sections, suggestions = state["actions"], state["sections"], state["suggestions"]
    sources, warnings, rows = state["sources"], state["warnings"], state["rows"]
    if actions:
        parts.append("<h2>Prioridades de acao</h2><ol class=\"action-list\">")
        for action in actions[:10]:
            parts.append(f"<li>{_report_escape(action)}</li>")
        parts.append("</ol>")

    if sections:
        parts.append("<h2>Diagnostico por area</h2>")
        for section in sections:
            parts.append(f"<h3>{_report_escape(section.get('title'))}</h3>")
            for finding in (section.get("findings") or [])[:12]:
                severity = str(finding.get("severity") or "info")
                parts.append(f"<div class=\"finding {_report_escape(severity)}\">")
                parts.append(f"<p><span class=\"tag\">{_report_escape(_severity_label(severity))}</span><strong>{_report_escape(finding.get('title'))}</strong></p>")
                if finding.get("evidence"):
                    parts.append(f"<p><strong>Evidencia:</strong> {_report_escape(finding.get('evidence'))}</p>")
                if finding.get("impact"):
                    parts.append(f"<p><strong>Impacto:</strong> {_report_escape(finding.get('impact'))}</p>")
                if finding.get("recommendation"):
                    parts.append(f"<p><strong>Recomendacao:</strong> {_report_escape(finding.get('recommendation'))}</p>")
                if finding.get("source"):
                    parts.append(f"<p class=\"meta\">Fonte: {_report_escape(_assistant_human_source_label(finding.get('source'), finding.get('source')))}</p>")
                parts.append("</div>")
    elif suggestions:
        parts.append("<h2>Alertas automaticos</h2><ul>")
        for item in suggestions[:12]:
            parts.append(f"<li><strong>{_report_escape(item.get('title'))}</strong>: {_report_escape(item.get('detail'))}</li>")
        parts.append("</ul>")
    else:
        parts.append("<h2>Diagnostico por area</h2><p>Nenhuma oportunidade critica foi detectada pelos leitores automaticos.</p>")

    parts.append("<h2>Fontes consultadas</h2><ul>")
    for src in sources:
        ext = " externa" if src.get("external") else ""
        readonly = " read-only" if src.get("read_only") else ""
        parts.append(f"<li>{_report_escape(_assistant_source_display(src))}: {_report_escape(src.get('records'))} registro(s){ext}{readonly}</li>")
    parts.append("</ul>")
    if warnings:
        parts.append("<h2>Avisos</h2><ul>")
        for warning in warnings:
            parts.append(f"<li class=\"warn\">{_report_escape(warning)}</li>")
        parts.append("</ul>")
    if rows:
        headers = sorted({key for row in rows[:200] for key in row.keys()})[:24]
        parts.append("<h2>Dados estruturados</h2><table><thead><tr>")
        for header in headers:
            parts.append(f"<th>{_report_escape(header)}</th>")
        parts.append("</tr></thead><tbody>")
        for row in rows[:500]:
            parts.append("<tr>")
            for header in headers:
                parts.append(f"<td>{_report_escape(row.get(header, ''))}</td>")
            parts.append("</tr>")
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "".join(parts)

def _assistant_build_report_html(title: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> str:
    if str(context.get("report_type") or "").strip():
        return _assistant_build_advanced_report_html(title, context)
    state = _standard_report_state(title, context, suggestions)
    parts = state["parts"]
    _append_standard_diagnostic_stockout(parts, state)
    _append_standard_stale_sales_margin(parts, state)
    return _append_standard_tail(parts, state)
