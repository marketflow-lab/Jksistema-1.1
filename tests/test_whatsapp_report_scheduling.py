from datetime import datetime

import pytest

from backend.services import whatsapp_bridge
from backend.services.whatsapp import report_scheduling


@pytest.mark.parametrize(
    ("current", "week", "month"),
    [
        (datetime(2026, 7, 16, 10, 0), "2026-W29", "2026-07"),
        (datetime(2027, 1, 1, 10, 0), "2026-W53", "2027-01"),
    ],
)
def test_report_keys_component_match_facade(current: datetime, week: str, month: str) -> None:
    assert report_scheduling.week_key(current) == whatsapp_bridge._whatsapp_week_key(current) == week
    assert report_scheduling.month_key(current) == whatsapp_bridge._whatsapp_month_key(current) == month


def test_compact_alert_removes_metadata_and_limits_text() -> None:
    detail = (
        "**Estoque crítico** em 2026-07-16T12:30:00Z. "
        + "Repor produto imediatamente. " * 50
        + "\nFonte: cadastro interno"
    )
    component = report_scheduling.compact_alert_detail(detail, 180)
    assert component == whatsapp_bridge._whatsapp_compact_alert_detail(detail, 180)
    assert len(component) <= 180
    assert "Fonte:" not in component
    assert "2026-07-16T12:30:00Z" not in component
    assert component.endswith("…")


def test_weekly_operational_text_is_mobile_ready_and_limited() -> None:
    suggestions = [
        {
            "severity": "critical" if index == 0 else "warning",
            "title": f"Ponto {index + 1}",
            "detail": "Detalhe operacional " + ("longo " * 100),
        }
        for index in range(8)
    ]
    component = report_scheduling.weekly_operational_parts(suggestions, "2026-W29")
    assert component == whatsapp_bridge._whatsapp_weekly_operational_parts(suggestions, "2026-W29")
    assert 1 <= len(component) <= 4
    assert component[0].startswith("📅 *Resumo semanal*")
    assert "Semana 29" in component[0]
    assert sum("Ponto 7" in part for part in component) == 0
    assert all(len(part) <= 2100 for part in component)


@pytest.mark.parametrize(
    ("kind", "current", "expected"),
    [
        (
            "weekly",
            datetime(2026, 7, 13, 8, 0),
            {
                "key": "2026-W29",
                "title": "Relatório semanal",
                "event_type": "weekly_report",
                "start": "2026-07-06",
                "end": "2026-07-12",
            },
        ),
        (
            "monthly",
            datetime(2026, 3, 1, 8, 0),
            {
                "key": "2026-03",
                "title": "Relatório mensal",
                "event_type": "monthly_report",
                "start": "2026-02-01",
                "end": "2026-02-28",
            },
        ),
        (
            "monthly",
            datetime(2024, 3, 10, 8, 0),
            {
                "key": "2024-03",
                "title": "Relatório mensal",
                "event_type": "monthly_report",
                "start": "2024-02-01",
                "end": "2024-02-29",
            },
        ),
    ],
)
def test_scheduled_periods_match_facade(kind: str, current: datetime, expected: dict[str, str]) -> None:
    assert report_scheduling.scheduled_report_period(kind, current) == whatsapp_bridge._scheduled_report_period(
        kind, current
    ) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"success": True, "status": "sent"}, True),
        ({"success": True, "status": "duplicate"}, True),
        ({"success": True, "status": "ignored_low_severity"}, False),
        ({"success": True, "status": "rate_limited"}, False),
        ({"success": True, "status": "binding_missing"}, False),
        ({"success": False, "status": "failed"}, False),
        (None, False),
    ],
)
def test_scheduled_result_acceptance_matches_facade(result, expected: bool) -> None:
    assert report_scheduling.scheduled_report_result_accepted(result) == whatsapp_bridge._scheduled_report_result_accepted(
        result
    ) is expected
