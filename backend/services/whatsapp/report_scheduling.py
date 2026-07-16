"""Scheduled report keys, periods and operational message composition."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from backend.services.whatsapp import formatting


WHATSAPP_WEEKLY_REPORT_START_HOUR = 8


def week_key(current: datetime) -> str:
    iso = current.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key(current: datetime) -> str:
    return current.strftime("%Y-%m")


def compact_alert_detail(value: Any, limit: int = 520) -> str:
    text = formatting._whatsapp_clean_markdown(value)
    text = re.sub(r"(?im)^\s*(?:fonte|consultado em|atualizado em)\s*:.*$", "", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -•")
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    cut = cut if cut >= max(120, limit // 2) else limit - 1
    return text[:cut].rstrip(" ,;:-") + "…"


def weekly_operational_parts(suggestions: list[dict[str, Any]], week: str) -> list[str]:
    important = [item for item in suggestions if isinstance(item, dict)][:6]
    critical_count = sum(1 for item in important if str(item.get("severity") or "").lower() == "critical")
    high_count = sum(
        1 for item in important if str(item.get("severity") or "").lower() in {"high", "warning"}
    )
    lines = [
        "📅 *Resumo semanal*",
        f"_Semana {week.split('-W')[-1]}_",
        "",
        "*Panorama*",
        f"• {len(important)} ponto(s) importante(s)",
        f"• {critical_count} crítico(s) e {high_count} de atenção",
    ]
    for index, item in enumerate(important, 1):
        title = re.sub(r"\s+", " ", str(item.get("title") or "Ponto de atenção")).strip()[:120]
        detail = compact_alert_detail(item.get("detail") or "")
        marker = "🚨" if str(item.get("severity") or "").lower() == "critical" else "⚠️"
        lines.extend(["", f"{marker} *{index}. {title}*", detail or "Confira os detalhes no JK Sistema."])
    lines.extend(
        [
            "",
            "*Próximo passo*",
            "Comece pelos itens críticos e abra o JK Sistema para consultar a lista completa e os dados detalhados.",
            "",
            "_Fonte: dados operacionais consolidados do JK Sistema nesta semana._",
        ]
    )
    parts = formatting._whatsapp_split_body("\n".join(lines), formatting.WHATSAPP_REPORT_BODY_CHARS)
    return parts[:4]


def scheduled_report_period(kind: str, current: datetime) -> dict[str, str]:
    if kind == "monthly":
        current_month_start = current.replace(day=1)
        end = current_month_start - timedelta(days=1)
        start = end.replace(day=1)
        return {
            "key": month_key(current),
            "title": "Relatório mensal",
            "event_type": "monthly_report",
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        }
    end = current - timedelta(days=1)
    start = end - timedelta(days=6)
    return {
        "key": week_key(current),
        "title": "Relatório semanal",
        "event_type": "weekly_report",
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
    }


def scheduled_report_result_accepted(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").strip().lower()
    return result.get("success") is True and status not in {
        "ignored_low_severity",
        "rate_limited",
        "binding_missing",
    }
