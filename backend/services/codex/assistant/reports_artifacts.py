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
from .answers import _assistant_report_chat_text
from .margin_analysis import _assistant_collect_margin_rows, _assistant_specialist_sku_diagnostics
from .reports_html import _assistant_build_report_html, _assistant_flatten_rows, _assistant_report_dir
from .runtime import _assistant_info_base, _assistant_now
from .settings import REPORT_FORMATS
from .source_labels import _assistant_source_display
from .utils import _assistant_money

def _assistant_write_advanced_xlsx(path: str, context: dict[str, Any]) -> None:
    import pandas as pd

    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    summary_rows = [
        {"Indicador": "Perfil", "Valor": context.get("report_type") or ""},
        {"Indicador": "Periodo", "Valor": f"{scope.get('period_start') or '-'} a {scope.get('period_end') or '-'}"},
        {"Indicador": "Comparacao", "Valor": f"{scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'}"},
        {"Indicador": "Loja", "Valor": scope.get("selected_store") or "Consolidado"},
        {"Indicador": "Estoque em", "Valor": scope.get("stock_as_of") or "Indisponivel"},
        {"Indicador": "Confiabilidade", "Valor": f"{quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100)"},
    ]
    summary_rows.extend({"Indicador": str(key), "Valor": value if value is not None else "Indisponivel"} for key, value in financial.items())
    summary_rows.extend({"Indicador": f"cobertura_{key}", "Valor": value if value is not None else "Indisponivel"} for key, value in coverage.items())
    actions = []
    for action in context.get("top_actions") or []:
        if not isinstance(action, dict):
            continue
        actions.append({**action, "skus": ", ".join(str(item) for item in action.get("skus") or [])})
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else {}
    import_rows = []
    for item in import_analysis.get("items") or []:
        if isinstance(item, dict):
            import_rows.append({"record_type": "item", **item})
    for item in import_analysis.get("scenarios") or []:
        if isinstance(item, dict):
            import_rows.append({"record_type": "scenario", **item})
    sources = quality.get("source_health") if isinstance(quality.get("source_health"), list) else []
    warnings = [{"Aviso": str(item)} for item in (quality.get("warnings") if isinstance(quality.get("warnings"), list) else [])]
    sheets = {
        "Resumo": summary_rows,
        "Margens_MLB": context.get("listing_margin_rows") or [],
        "Historico_Margens": context.get("historical_margin_ledger") or [],
        "Ações": actions,
        "Lojas": context.get("store_summaries") or [],
        "Vendas_SKU": context.get("sales_rows") or [],
        "Estoque": context.get("inventory_rows") or [],
        "Compras_Transito": context.get("purchase_pipeline_rows") or [],
        "Importacao": import_rows,
        "Fontes": sources,
        "Fontes_ML": (context.get("marketplace_commercial") or {}).get("sources") if isinstance(context.get("marketplace_commercial"), dict) else [],
        "Avisos": warnings,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, rows in sheets.items():
            frame = pd.DataFrame(rows or [{"Informacao": "Sem dados disponiveis"}])
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            worksheet = writer.book[sheet_name]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                values = [str(cell.value or "") for cell in list(column_cells)[:200]]
                width = min(60, max(10, max((len(value) for value in values), default=10) + 2))
                worksheet.column_dimensions[column_cells[0].column_letter].width = width


def _assistant_write_xlsx(path: str, context: dict[str, Any], suggestions: list[dict[str, Any]]) -> None:
    if str(context.get("report_type") or "").strip():
        _assistant_write_advanced_xlsx(path, context)
        return
    import pandas as pd

    rows = _assistant_flatten_rows(context.get("tool_results") if isinstance(context.get("tool_results"), list) else [])
    sources = context.get("sources") if isinstance(context.get("sources"), list) else []
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else _assistant_management_analysis(context)
    kpis = analysis.get("kpis") if isinstance(analysis.get("kpis"), list) else []
    stockout_rows = _assistant_collect_stockout_rows(context, only_risky=True, limit=500)
    stale_rows = _assistant_collect_stale_stock_rows(context, limit=500)
    sales_rows = _assistant_collect_sales_rank_rows(context, limit=500)
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context, limit=500)
    margin_rows = [
        {key: value for key, value in row.items() if not str(key).startswith("_")}
        for row in _assistant_collect_margin_rows(context, limit=500)
    ]
    findings = []
    for section in analysis.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for finding in section.get("findings") or []:
            if isinstance(finding, dict):
                findings.append(finding)
    actions = [{"prioridade": idx, "acao": action} for idx, action in enumerate(analysis.get("priority_actions") or [], 1)]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(kpis or [{"info": "Sem indicadores"}]).to_excel(writer, sheet_name="indicadores", index=False)
        pd.DataFrame(diagnostic_rows or [{"info": "Sem diagnostico especialista por SKU"}]).to_excel(writer, sheet_name="diagnostico_skus", index=False)
        pd.DataFrame(stockout_rows or [{"info": "Sem SKUs com risco alto/critico de ruptura"}]).to_excel(writer, sheet_name="ruptura_skus", index=False)
        pd.DataFrame(stale_rows or [{"info": "Sem estoque parado com saldo de loja"}]).to_excel(writer, sheet_name="estoque_parado", index=False)
        pd.DataFrame(sales_rows or [{"info": "Sem ranking de SKUs vendido"}]).to_excel(writer, sheet_name="top_skus", index=False)
        pd.DataFrame(margin_rows or [{"info": "Sem margem calculavel por SKU vendido"}]).to_excel(writer, sheet_name="margens_skus", index=False)
        pd.DataFrame(findings or [{"info": "Sem diagnostico"}]).to_excel(writer, sheet_name="diagnostico", index=False)
        pd.DataFrame(actions or [{"info": "Sem acoes priorizadas"}]).to_excel(writer, sheet_name="acoes", index=False)
        pd.DataFrame(suggestions or []).to_excel(writer, sheet_name="sugestoes", index=False)
        pd.DataFrame(sources or []).to_excel(writer, sheet_name="fontes", index=False)
        pd.DataFrame(rows or [{"info": "Sem dados tabulares"}]).to_excel(writer, sheet_name="dados", index=False)


def _assistant_advanced_pdf_setup(title: str, context: dict[str, Any]) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.fonts import addMapping
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    font_candidates = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for regular_path, bold_path in font_candidates:
        if not (os.path.isfile(regular_path) and os.path.isfile(bold_path)):
            continue
        try:
            if "JKReport" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("JKReport", regular_path))
                pdfmetrics.registerFont(TTFont("JKReport-Bold", bold_path))
                addMapping("JKReport", 0, 0, "JKReport")
                addMapping("JKReport", 1, 0, "JKReport-Bold")
            regular_font = "JKReport"
            bold_font = "JKReport-Bold"
            break
        except Exception:
            continue

    styles = getSampleStyleSheet()
    for style_name in ("Normal", "BodyText"):
        styles[style_name].fontName = regular_font
    for style_name in ("Title", "Heading1", "Heading2", "Heading3"):
        styles[style_name].fontName = bold_font
    body = styles["BodyText"]
    small = styles["BodyText"].clone("Small")
    small.fontSize = 7
    small.leading = 9
    story: list[Any] = [Paragraph(html.escape(title), styles["Title"]), Spacer(1, 4 * mm)]
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
    financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
    story.append(
        Paragraph(
            html.escape(
                f"Período {scope.get('period_start') or '-'} a {scope.get('period_end') or '-'}; "
                f"comparação {scope.get('comparison_start') or '-'} a {scope.get('comparison_end') or '-'}; "
                f"confiabilidade {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100)."
            ),
            body,
        )
    )
    story.append(Spacer(1, 3 * mm))
    coverage = context.get("financial_coverage") if isinstance(context.get("financial_coverage"), dict) else {}
    story.append(
        Paragraph(
            html.escape(
                f"Cobertura financeira {coverage.get('complete_margin_by_revenue_pct') if coverage.get('complete_margin_by_revenue_pct') is not None else 'indisponível'}%; "
                f"mínimo {coverage.get('minimum_required_pct') or 95}%; receita coberta "
                f"{_assistant_money(coverage.get('covered_revenue_brl')) if coverage.get('covered_revenue_brl') is not None else 'indisponível'}; receita não coberta "
                f"{_assistant_money(coverage.get('uncovered_revenue_brl')) if coverage.get('uncovered_revenue_brl') is not None else 'indisponível'}. "
                "Campos ausentes não são zero."
            ),
            body,
        )
    )
    story.append(Spacer(1, 2 * mm))
    financial_cell = body.clone("FinancialCell")
    financial_cell.fontSize = 8
    financial_cell.leading = 10
    financial_header = financial_cell.clone("FinancialHeader")
    financial_header.fontName = bold_font
    financial_rows = [[Paragraph("Indicador", financial_header), Paragraph("Valor", financial_header)]]
    labels = {
        "gross_revenue_brl": "Receita bruta",
        "returns_brl": "Devoluções",
        "net_revenue_brl": "Receita líquida",
        "advertising_brl": "Publicidade",
        "contribution_profit_brl": "Margem de contribuição",
        "net_profit_after_ads_brl": "Resultado de contribuição após publicidade",
        "net_profit_after_ads_and_returns_brl": "Resultado de contribuição após publicidade e devoluções reconciliadas",
    }
    for key, label in labels.items():
        value = financial.get(key)
        financial_rows.append(
            [
                Paragraph(html.escape(label), financial_cell),
                Paragraph(html.escape(_assistant_money(value) if value is not None else "Indisponível"), financial_cell),
            ]
        )
    table = Table(financial_rows, colWidths=[100 * mm, 45 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")), ("GRID", (0, 0), (-1, -1), .35, colors.grey), ("FONTNAME", (0, 0), (-1, -1), regular_font), ("FONTNAME", (0, 0), (-1, 0), bold_font), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.extend([Paragraph("Resumo financeiro", styles["Heading2"]), table, Spacer(1, 3 * mm), Paragraph("Decisões prioritárias", styles["Heading2"])])
    for index, action in enumerate((context.get("top_actions") or [])[:5], 1):
        if not isinstance(action, dict):
            continue
        text_value = (
            f"<b>{index}. {html.escape(str(action.get('title') or 'Acao recomendada'))}</b><br/>"
            f"Loja {html.escape(str(action.get('store') or '-'))}; impacto {html.escape(str(action.get('impact_label') or 'nao estimavel'))}; "
            f"urgência {html.escape(str(action.get('urgency') or '-'))}; confiança {html.escape(str(action.get('confidence') or '-'))}.<br/>"
            f"{html.escape(str(action.get('recommendation') or '-'))}<br/>"
            f"Responsável {html.escape(str(action.get('owner_username') or action.get('owner_role') or '-'))}; prazo {html.escape(str(action.get('due_at') or '-'))}."
        )
        story.extend([Paragraph(text_value, body), Spacer(1, 2 * mm)])

    return {"colors": colors, "A4": A4, "landscape": landscape, "mm": mm,
        "Paragraph": Paragraph, "SimpleDocTemplate": SimpleDocTemplate, "Spacer": Spacer,
        "Table": Table, "TableStyle": TableStyle, "regular_font": regular_font,
        "bold_font": bold_font, "styles": styles, "body": body, "small": small,
        "story": story, "quality": quality}


def _assistant_advanced_pdf_add_table(
    state: dict[str, Any], title_value: str, rows: Any, columns: list[tuple[str, str]], limit: int = 20,
) -> None:
    story, styles, small = state["story"], state["styles"], state["small"]
    Paragraph, Spacer, Table, TableStyle = state["Paragraph"], state["Spacer"], state["Table"], state["TableStyle"]
    colors, A4, landscape, mm = state["colors"], state["A4"], state["landscape"], state["mm"]
    all_values = [item for item in (rows if isinstance(rows, list) else []) if isinstance(item, dict)]
    values = all_values[:limit]
    if not values:
        return
    displayed_title = (
        f"{title_value} - exibindo {len(values)} de {len(all_values)}; base completa no XLSX"
        if len(all_values) > len(values)
        else title_value
    )
    story.append(Paragraph(html.escape(displayed_title), styles["Heading2"]))
    data: list[list[Any]] = [[Paragraph(html.escape(label), small) for _, label in columns]]
    for row in values:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if (key.endswith("_brl") or key == "revenue") and value is not None:
                value = _assistant_money(value)
            cells.append(Paragraph(html.escape(str(value if value is not None else "Indisponível"))[:320], small))
        data.append(cells)
    widths = [landscape(A4)[0] / len(columns) - 8 * mm for _ in columns]
    output = Table(data, colWidths=widths, repeatRows=1)
    output.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")), ("GRID", (0, 0), (-1, -1), .25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    story.extend([output, Spacer(1, 3 * mm)])

def _assistant_advanced_pdf_tables(state: dict[str, Any], context: dict[str, Any]) -> None:
    quality = state["quality"]
    _assistant_advanced_pdf_add_table(state, "Lojas", context.get("store_summaries"), [("store", "Loja"), ("net_revenue_brl", "Receita líquida"), ("trend_pct", "Variação"), ("target_attainment_pct", "Meta")])
    _assistant_advanced_pdf_add_table(state,
        "Contribuição atual por anúncio e variação",
        context.get("listing_margin_rows"),
        [("store", "Loja"), ("mlb", "MLB"), ("variation_id", "Variação"), ("sku", "SKU"),
         ("current_price_brl", "Preço"), ("ml_fee_brl", "Tarifa"), ("seller_shipping_brl", "Frete"),
         ("unit_contribution_brl", "Contrib."), ("contribution_margin_pct", "Margem %"),
         ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        limit=40,
    )
    _assistant_advanced_pdf_add_table(state,
        "Resultado histórico reconciliado",
        context.get("historical_margin_ledger"),
        [("store", "Loja"), ("order_id", "Pedido"), ("line_number", "Linha"), ("pack_id", "Pack"), ("mlb", "MLB"),
         ("variation_id", "Variação"), ("sku", "SKU"), ("gross_amount_brl", "Receita"),
         ("contribution_total_brl", "Contrib."), ("shipping_scope_label", "Frete"),
         ("margin_status_label", "Status"), ("missing_components_text", "Ausentes")],
        limit=40,
    )
    _assistant_advanced_pdf_add_table(state, "Riscos de estoque", context.get("inventory_rows"), [("store", "Loja"), ("sku", "SKU"), ("abc", "ABC"), ("xyz", "XYZ"), ("coverage_days", "Cobertura"), ("suggested_purchase", "Comprar")])
    import_analysis = context.get("import_analysis") if isinstance(context.get("import_analysis"), dict) else {}
    _assistant_advanced_pdf_add_table(state, "Custo posto por SKU", import_analysis.get("items"), [("sku", "SKU"), ("quantity", "Qtd"), ("landed_unit_brl", "Custo posto"), ("minimum_sale_price_brl", "Preco minimo")])
    _assistant_advanced_pdf_add_table(state,
        "Saúde das fontes",
        quality.get("source_health"),
        [("source", "Fonte"), ("status", "Status"), ("records", "Registros"),
         ("last_sync_at", "Atualização"), ("coverage_pct", "Cobertura %")],
        limit=30,
    )
    marketplace_sources = context.get("marketplace_commercial") if isinstance(context.get("marketplace_commercial"), dict) else {}
    _assistant_advanced_pdf_add_table(state,
        "Recursos Mercado Livre consultados",
        marketplace_sources.get("sources"),
        [("provider", "Provedor"), ("resource", "Recurso"), ("method", "Método"),
         ("store", "Loja"), ("day", "Data")],
        limit=30,
    )

def _assistant_advanced_pdf_finish(path: str, context: dict[str, Any], state: dict[str, Any]) -> None:
    quality, story, styles = state["quality"], state["story"], state["styles"]
    Paragraph, SimpleDocTemplate = state["Paragraph"], state["SimpleDocTemplate"]
    colors, A4, landscape, mm = state["colors"], state["A4"], state["landscape"], state["mm"]
    regular_font, small = state["regular_font"], state["small"]
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    if warnings:
        story.append(Paragraph("Avisos de dados", styles["Heading2"]))
        for warning in warnings[:12]:
            story.append(Paragraph("- " + html.escape(str(warning)[:420]), small))
    marketplace = context.get("marketplace_commercial") if isinstance(context.get("marketplace_commercial"), dict) else {}
    story.extend(
        [
            Paragraph("Fontes e metodologia", styles["Heading2"]),
            Paragraph(
                html.escape(
                    f"Snapshot Mercado Livre: status {marketplace.get('status') or 'indisponível'}; coletado em "
                    f"{marketplace.get('collected_at') or 'indisponível'}; cache vencido: {'sim' if marketplace.get('stale') else 'não'}. "
                    "Preço, custo, imposto, tarifa e frete do vendedor são obrigatórios. Preços atuais nunca são apresentados como históricos. "
                    "Custo e imposto do cadastro só entram no histórico quando capturados no dia da venda ou em D+1; backfill posterior fica indisponível. "
                    "Frete de packs com vários produtos permanece no pack e não é rateado."
                ),
                small,
            ),
        ]
    )
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm)

    def draw_footer(canvas_obj: Any, document: Any) -> None:
        canvas_obj.saveState()
        page_width, _page_height = landscape(A4)
        canvas_obj.setStrokeColor(colors.HexColor("#d5dde6"))
        canvas_obj.line(12 * mm, 9 * mm, page_width - 12 * mm, 9 * mm)
        canvas_obj.setFont(regular_font, 7)
        canvas_obj.setFillColor(colors.HexColor("#526170"))
        canvas_obj.drawString(12 * mm, 5.5 * mm, "JK Peças - relatório gerencial somente leitura")
        canvas_obj.drawRightString(page_width - 12 * mm, 5.5 * mm, f"Página {document.page}")
        canvas_obj.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)

def _assistant_write_advanced_pdf(path: str, title: str, context: dict[str, Any]) -> None:
    state = _assistant_advanced_pdf_setup(title, context)
    _assistant_advanced_pdf_tables(state, context)
    _assistant_advanced_pdf_finish(path, context, state)


def _assistant_write_pdf(path: str, title: str, suggestions: list[dict[str, Any]], sources: list[dict[str, Any]], analysis: Optional[dict[str, Any]] = None, context: Optional[dict[str, Any]] = None) -> None:
    if isinstance(context, dict) and str(context.get("report_type") or "").strip():
        _assistant_write_advanced_pdf(path, title, context)
        return
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    y = height - 48
    c.setFont("Helvetica-Bold", 15)
    c.drawString(40, y, title[:80])
    y -= 26
    c.setFont("Helvetica", 9)
    c.drawString(40, y, f"Gerado em {_assistant_now()}")
    y -= 28

    def line(text: str, bold: bool = False) -> None:
        nonlocal y
        if y < 60:
            c.showPage()
            y = height - 48
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9 if not bold else 11)
        safe = str(text or "").encode("latin-1", "replace").decode("latin-1")
        c.drawString(40, y, safe[:110])
        y -= 15

    line("Principais achados", True)
    findings = []
    if isinstance(analysis, dict):
        for section in analysis.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for finding in section.get("findings") or []:
                if isinstance(finding, dict):
                    findings.append(finding)
    if findings:
        for item in findings[:16]:
            line(f"- {item.get('title')}: {item.get('recommendation')}")
    else:
        for item in suggestions[:16]:
            line(f"- {item.get('title')}: {item.get('detail')}")
    if isinstance(analysis, dict) and analysis.get("priority_actions"):
        y -= 8
        line("Prioridades de acao", True)
        for idx, action in enumerate((analysis.get("priority_actions") or [])[:8], 1):
            line(f"{idx}. {action}")
    diagnostic_rows = _assistant_specialist_sku_diagnostics(context or {}, limit=12)
    if diagnostic_rows:
        y -= 8
        line("Diagnostico especialista por SKU", True)
        for row in diagnostic_rows[:12]:
            line(
                f"- SKU {row.get('SKU')} | risco {row.get('Risco')} | prioridade {row.get('Prioridade')} | "
                f"responsavel {row.get('Responsavel')} | impacto {row.get('Impacto financeiro')}"
            )
    stockout_rows = _assistant_collect_stockout_rows(context or {}, only_risky=True, limit=12)
    if stockout_rows:
        y -= 8
        line("SKUs em risco de ruptura", True)
        for row in stockout_rows[:12]:
            line(
                f"- SKU {row.get('sku')} | risco {row.get('risco')} | saldo loja {row.get('saldo_considerado')} | "
                f"media/dia {row.get('media_dia')} | dias {row.get('dias_ate_ruptura')}"
            )
    stale_rows = _assistant_collect_stale_stock_rows(context or {}, limit=12)
    if stale_rows:
        y -= 8
        line("Estoque parado com saldo de loja", True)
        for row in stale_rows[:12]:
            line(
                f"- {row.get('prioridade')} | SKU {row.get('sku')} | saldo loja {row.get('saldo_loja')} | "
                f"{row.get('situacao')} | capital {row.get('capital_custo')} | acao {row.get('acao_recomendada')}"
            )
    margin_rows = _assistant_collect_margin_rows(context or {}, limit=12)
    if margin_rows:
        y -= 8
        line("Margem real por SKU vendido", True)
        for row in margin_rows[:12]:
            line(
                f"- SKU {row.get('SKU')} | qtd {row.get('Qtd vendida')} | valor {row.get('Valor vendido')} | "
                f"lucro {row.get('Lucro estimado') or '-'} | status {row.get('Status da margem')}"
            )
    y -= 8
    line("Fontes consultadas", True)
    for src in sources[:20]:
        line(f"- {_assistant_source_display(src)} ({src.get('records')} registros)")
    c.save()


def _assistant_create_report(client_id: str, title: str, context: dict[str, Any], prompt: str) -> dict[str, Any]:
    suggestions = context.get("suggestions") if isinstance(context.get("suggestions"), list) else []
    advanced_report = bool(str(context.get("report_type") or "").strip())
    if advanced_report:
        financial = context.get("financial_summary") if isinstance(context.get("financial_summary"), dict) else {}
        quality = context.get("data_quality") if isinstance(context.get("data_quality"), dict) else {}
        net_revenue = financial.get("net_revenue_brl")
        context["management_analysis"] = {
            "generated_at": (context.get("scope") or {}).get("generated_at") if isinstance(context.get("scope"), dict) else _assistant_now(),
            "executive_summary": [
                f"Confiabilidade {quality.get('confidence') or 'baixa'} ({quality.get('score') or 0}/100).",
                f"Receita liquida {_assistant_money(net_revenue) if net_revenue is not None else 'indisponivel'}.",
            ],
            "kpis": [],
            "sections": [],
            "priority_actions": [str(item.get("recommendation") or item.get("title") or "") for item in (context.get("top_actions") or [])[:5]],
            "data_quality": quality,
        }
    elif not isinstance(context.get("management_analysis"), dict):
        context["management_analysis"] = _assistant_management_analysis(context)
    analysis = context.get("management_analysis") if isinstance(context.get("management_analysis"), dict) else {}
    context["specialist_sku_diagnostics"] = [] if advanced_report else _assistant_specialist_sku_diagnostics(context, limit=500)
    report_id = f"codex_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    report_dir = _assistant_report_dir(client_id, report_id)
    html_path = os.path.join(report_dir, "report.html")
    xlsx_path = os.path.join(report_dir, "report.xlsx")
    pdf_path = os.path.join(report_dir, "report.pdf")
    metadata_path = os.path.join(report_dir, "metadata.json")

    html_content = _assistant_build_report_html(title, context, suggestions)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    try:
        _assistant_write_xlsx(xlsx_path, context, suggestions)
    except Exception as exc:
        context.setdefault("warnings", []).append(f"Falha ao gerar XLSX: {exc}")
    try:
        _assistant_write_pdf(pdf_path, title, suggestions, context.get("sources") if isinstance(context.get("sources"), list) else [], analysis, context)
    except Exception as exc:
        context.setdefault("warnings", []).append(f"Falha ao gerar PDF: {exc}")

    chat_text = _assistant_report_chat_text(title, context, suggestions, report_id)
    metadata = {
        "report_id": report_id,
        "title": title,
        "prompt": prompt,
        "created_at": _assistant_now(),
        "formats": {
            "html": os.path.exists(html_path),
            "xlsx": os.path.exists(xlsx_path),
            "pdf": os.path.exists(pdf_path),
        },
        "downloads": {
            fmt: f"/api/admin/codex/assistant/reports/{report_id}/download?format={fmt}"
            for fmt in REPORT_FORMATS
            if os.path.exists(os.path.join(report_dir, f"report.{fmt}"))
        },
        "sources": context.get("sources") or [],
        "suggestions": suggestions,
        "management_analysis": analysis,
        "specialist_sku_diagnostics": context.get("specialist_sku_diagnostics") or [],
        "registry_results": (
            [
                {
                    "tool_id": item.get("tool_id"),
                    "tool_label": item.get("tool_label"),
                    "records": item.get("records"),
                    "sources_human": item.get("sources_human") or [],
                    "warnings": item.get("warnings") or [],
                }
                for item in (context.get("registry_results") or [])[:50]
                if isinstance(item, dict)
            ]
            if advanced_report
            else context.get("registry_results") or []
        ),
        "tool_plan": context.get("tool_plan") or {},
        "status_steps": context.get("status_steps") or [],
        "warnings": context.get("warnings") or [],
        "report_type": context.get("report_type") or "",
        "scope": context.get("scope") or {},
        "data_quality": context.get("data_quality") or {},
        "financial_coverage": context.get("financial_coverage") or {},
        "financial_summary": context.get("financial_summary") or {},
        "marketplace_commercial": context.get("marketplace_commercial") or {},
        "listing_margin_rows": context.get("listing_margin_rows") or [],
        "historical_margin_ledger": context.get("historical_margin_ledger") or [],
        "top_actions": (context.get("top_actions") or [])[:5],
        "store_summaries": context.get("store_summaries") or [],
        "sales_rows": context.get("sales_rows") or [],
        "inventory_rows": context.get("inventory_rows") or [],
        "purchase_pipeline_rows": context.get("purchase_pipeline_rows") or [],
        "import_analysis": context.get("import_analysis"),
        "supplier_performance": context.get("supplier_performance"),
        "chat_text": chat_text,
        "chat_download_formats": [fmt for fmt in ("pdf", "xlsx") if os.path.exists(os.path.join(report_dir, f"report.{fmt}"))],
    }
    saved = codex_assistant_storage.codex_assistant_report_save(_assistant_info_base(), client_id, metadata)
    return saved if isinstance(saved, dict) and saved else metadata


def _assistant_ensure_report_chat_text(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    chat_text_atual = str(metadata.get("chat_text") or "")
    tem_cobertura_tecnica_antiga = any(
        trecho in chat_text_atual
        for trecho in (
            "## Cobertura das ferramentas",
            "Escopo e cobertura das consultas",
            "| Ferramenta | Modulo | Registros | Fonte |",
        )
    )
    if metadata.get("chat_text") and metadata.get("chat_download_formats") and not tem_cobertura_tecnica_antiga:
        return metadata
    context = {
        "generated_at": metadata.get("created_at"),
        "sources": metadata.get("sources") or [],
        "suggestions": metadata.get("suggestions") or [],
        "management_analysis": metadata.get("management_analysis") or {},
        "specialist_sku_diagnostics": metadata.get("specialist_sku_diagnostics") or [],
        "registry_results": metadata.get("registry_results") or [],
        "tool_plan": metadata.get("tool_plan") or {},
        "status_steps": metadata.get("status_steps") or [],
        "warnings": metadata.get("warnings") or [],
        "report_type": metadata.get("report_type") or "",
        "scope": metadata.get("scope") or {},
        "data_quality": metadata.get("data_quality") or {},
        "financial_coverage": metadata.get("financial_coverage") or {},
        "financial_summary": metadata.get("financial_summary") or {},
        "marketplace_commercial": metadata.get("marketplace_commercial") or {},
        "listing_margin_rows": metadata.get("listing_margin_rows") or [],
        "historical_margin_ledger": metadata.get("historical_margin_ledger") or [],
        "top_actions": metadata.get("top_actions") or [],
        "store_summaries": metadata.get("store_summaries") or [],
        "sales_rows": metadata.get("sales_rows") or [],
        "inventory_rows": metadata.get("inventory_rows") or [],
        "purchase_pipeline_rows": metadata.get("purchase_pipeline_rows") or [],
        "import_analysis": metadata.get("import_analysis"),
        "supplier_performance": metadata.get("supplier_performance"),
    }
    suggestions = metadata.get("suggestions") if isinstance(metadata.get("suggestions"), list) else []
    metadata["chat_text"] = _assistant_report_chat_text(
        str(metadata.get("title") or "Relatorio Codex Assistente"),
        context,
        suggestions,
        str(metadata.get("report_id") or ""),
    )
    downloads = metadata.get("downloads") if isinstance(metadata.get("downloads"), dict) else {}
    metadata["chat_download_formats"] = [fmt for fmt in ("pdf", "xlsx") if fmt in downloads]
    return metadata


def _assistant_compact_scalar(value: Any, max_chars: int = 300) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_chars]

def _assistant_compact_status_steps(value: Any) -> list[Any]:
    output: list[Any] = []
    for item in (value if isinstance(value, list) else [])[:12]:
        if isinstance(item, dict):
            projected = {}
            for key in ("status", "label", "at", "tool_id", "records", "success"):
                if key in item and isinstance(item.get(key), (str, bool, int, float, type(None))):
                    projected[key] = _assistant_compact_scalar(item.get(key), 240)
            if projected:
                output.append(projected)
        else:
            output.append(_assistant_compact_scalar(item, 240))
    return output

def _assistant_compact_report(value: Any, limit: int) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    chat_text = str(value.get("chat_text") or value.get("chat_text_preview") or "")
    original_length = value.get("chat_text_length")
    try:
        chat_text_length = max(len(chat_text), int(original_length or 0))
    except Exception:
        chat_text_length = len(chat_text)
    report: dict[str, Any] = {
        "report_id": _assistant_compact_scalar(value.get("report_id"), 160) or "",
        "title": _assistant_compact_scalar(value.get("title"), 240) or "",
        "generated_at": _assistant_compact_scalar(value.get("generated_at"), 80) or "",
        "created_at": _assistant_compact_scalar(value.get("created_at"), 80) or "",
        "kind": _assistant_compact_scalar(value.get("kind"), 80) or "",
        "status": _assistant_compact_scalar(value.get("status"), 80) or "",
        "status_steps": _assistant_compact_status_steps(value.get("status_steps")),
        "warnings": [_assistant_compact_scalar(item, 300) for item in (value.get("warnings") if isinstance(value.get("warnings"), list) else [])[:12]],
        "formats": {
            str(key)[:24]: bool(enabled)
            for key, enabled in list((value.get("formats") if isinstance(value.get("formats"), dict) else {}).items())[:8]
        },
        "downloads": {
            str(key)[:24]: _assistant_compact_scalar(url, 600)
            for key, url in list((value.get("downloads") if isinstance(value.get("downloads"), dict) else {}).items())[:8]
            if isinstance(url, (str, int, float))
        },
        "chat_download_formats": [
            str(item)[:24]
            for item in (value.get("chat_download_formats") if isinstance(value.get("chat_download_formats"), list) else [])[:8]
        ],
        "chat_text_preview": chat_text[:limit],
        "chat_text_length": chat_text_length,
        "truncated": bool(value.get("truncated")) or chat_text_length > limit,
    }
    report_type = str(value.get("report_type") or "").strip()
    if report_type:
        report["report_type"] = report_type[:80]
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    if scope:
        report["scope"] = {
            key: ([_assistant_compact_scalar(item, 120) for item in field[:20]] if isinstance(field, list) else _assistant_compact_scalar(field, 240))
            for key in (
                "period_start", "period_end", "comparison_start", "comparison_end",
                "selected_store", "stores", "stock_as_of", "generated_at",
            )
            if (field := scope.get(key)) is not None
        }
    quality = value.get("data_quality") if isinstance(value.get("data_quality"), dict) else {}
    if quality:
        report["data_quality"] = {
            "status": _assistant_compact_scalar(quality.get("status"), 40),
            "score": quality.get("score"),
            "confidence": _assistant_compact_scalar(quality.get("confidence"), 40),
            "source_health": [
                {
                    key: _assistant_compact_scalar(item.get(key), 180)
                    for key in ("source", "status", "records", "last_sync_at", "coverage_pct")
                    if item.get(key) is not None
                }
                for item in (quality.get("source_health") if isinstance(quality.get("source_health"), list) else [])[:8]
                if isinstance(item, dict)
            ],
            "warnings": [_assistant_compact_scalar(item, 260) for item in (quality.get("warnings") if isinstance(quality.get("warnings"), list) else [])[:6]],
        }
    for key in ("financial_coverage", "financial_summary"):
        source_dict = value.get(key) if isinstance(value.get(key), dict) else {}
        if source_dict:
            report[key] = {
                str(item_key)[:80]: _assistant_compact_scalar(item_value, 160)
                for item_key, item_value in list(source_dict.items())[:30]
                if isinstance(item_value, (str, bool, int, float, type(None)))
            }
    actions = value.get("top_actions") if isinstance(value.get("top_actions"), list) else []
    if actions:
        report["top_actions"] = [
            {
                key: (
                    [_assistant_compact_scalar(entry, 80) for entry in item.get(key, [])[:20]]
                    if key == "skus" and isinstance(item.get(key), list)
                    else _assistant_compact_scalar(item.get(key), 500)
                )
                for key in (
                    "action_id", "action_type", "title", "store", "skus", "impact_brl",
                    "impact_label", "urgency", "confidence", "owner_username", "owner_role",
                    "due_at", "evidence", "recommendation", "queueable",
                )
                if item.get(key) is not None
            }
            for item in actions[:5]
            if isinstance(item, dict)
        ]
    return report

def _assistant_compact_scheduler(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scheduler: dict[str, Any] = {}
    for key in (
        "last_proactive_ts",
        "last_proactive_at",
        "last_proactive_tool_results_count",
        "last_daily_date",
        "last_daily_at",
        "last_daily_suggestions_count",
        "last_weekly_key",
        "last_weekly_at",
    ):
        item = value.get(key)
        if isinstance(item, (str, bool, int, float, type(None))):
            scheduler[key] = _assistant_compact_scalar(item, 120)
    nested_report = value.get("last_daily_report")
    if isinstance(nested_report, dict) and nested_report.get("report_id"):
        scheduler["last_daily_report_id"] = _assistant_compact_scalar(nested_report.get("report_id"), 160)
    weekly_report = value.get("last_weekly_report")
    if isinstance(weekly_report, dict) and weekly_report.get("report_id"):
        scheduler["last_weekly_report_id"] = _assistant_compact_scalar(weekly_report.get("report_id"), 160)
    sources = value.get("last_proactive_sources")
    if isinstance(sources, list):
        scheduler["last_proactive_sources_count"] = len(sources)
    return scheduler

def _assistant_compact_suggestion(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    suggestion: dict[str, Any] = {}
    limits = {
        "id": 120,
        "kind": 80,
        "title": 160,
        "detail": 900,
        "severity": 40,
        "source": 160,
        "recommendation": 600,
        "report_prompt": 1200,
        "created_at": 80,
    }
    for key, max_chars in limits.items():
        if key in value and isinstance(value.get(key), (str, bool, int, float, type(None))):
            suggestion[key] = _assistant_compact_scalar(value.get(key), max_chars)
    return suggestion or None

def _assistant_compact_chat_text_response(payload: Any, preview_limit: int = 600) -> Any:
    """Project proactive/daily responses without copying their large datasets."""
    limit = max(1, min(int(preview_limit or 600), 600))
    source = payload if isinstance(payload, dict) else {}
    result: dict[str, Any] = {"compact": True}
    for key in ("success", "status", "due"):
        if key in source and isinstance(source.get(key), (str, bool, int, float, type(None))):
            result[key] = _assistant_compact_scalar(source.get(key), 120)
    report = _assistant_compact_report(source.get("report"), limit)
    if report is not None:
        result["report"] = report
    result["scheduler"] = _assistant_compact_scheduler(source.get("scheduler"))
    suggestions = []
    for item in (source.get("suggestions") if isinstance(source.get("suggestions"), list) else [])[:12]:
        projected = _assistant_compact_suggestion(item)
        if projected:
            suggestions.append(projected)
    if "suggestions" in source:
        result["suggestions"] = suggestions
    return result
