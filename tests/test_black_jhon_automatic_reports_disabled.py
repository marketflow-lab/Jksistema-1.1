import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from backend.services import codex_assistant
from backend.services.codex.assistant import api as assistant_api


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


class BlackJhonAutomaticReportsDisabledTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = {"client_id": "000002", "username": "owner"}
        self.state = {"last_weekly_key": "2026-W33"}

    def test_saved_suggestions_are_not_returned(self) -> None:
        with (
            patch.object(assistant_api, "_assistant_require_full_admin", return_value=self.session),
            patch.object(assistant_api, "_assistant_scheduler_state", return_value=self.state),
            patch.object(assistant_api, "_assistant_read_json") as read_json,
        ):
            result = codex_assistant.codex_assistant_suggestions(
                _request("/api/admin/codex/assistant/suggestions"),
                "Bearer test",
            )

        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["due"])
        self.assertEqual(result["suggestions"], [])
        read_json.assert_not_called()

    def test_proactive_and_weekly_endpoints_do_not_collect_or_create_reports(self) -> None:
        payload = codex_assistant.CodexAssistantRunRequest(force=True)
        with (
            patch.object(assistant_api, "_assistant_require_full_admin", return_value=self.session),
            patch.object(assistant_api, "_assistant_scheduler_state", return_value=self.state),
            patch.object(assistant_api, "_assistant_collect_data") as collect_data,
            patch.object(assistant_api, "_assistant_create_report") as create_report,
            patch.object(assistant_api, "_assistant_save_scheduler_state") as save_state,
        ):
            proactive = codex_assistant.codex_assistant_proactive_run(
                payload,
                _request("/api/admin/codex/assistant/proactive/run"),
                "Bearer test",
            )
            weekly = codex_assistant.codex_assistant_weekly_analysis_run(
                payload,
                _request("/api/admin/codex/assistant/weekly-analysis/run"),
                "Bearer test",
            )

        for result in (proactive, weekly):
            self.assertEqual(result["status"], "disabled")
            self.assertFalse(result["due"])
            self.assertEqual(result["suggestions"], [])
        self.assertIsNone(weekly["report"])
        collect_data.assert_not_called()
        create_report.assert_not_called()
        save_state.assert_not_called()

    def test_manual_report_endpoint_remains_available(self) -> None:
        payload = codex_assistant.CodexAssistantReportRequest(prompt="Gerar relatorio solicitado")
        context = {"suggestions": [], "sources": []}
        expected_report = {"report_id": "manual-report", "title": "Relatorio Codex Assistente"}
        with (
            patch.object(assistant_api, "_assistant_require_full_admin", return_value=self.session),
            patch.object(assistant_api, "_assistant_collect_data", return_value=context) as collect_data,
            patch.object(assistant_api, "_assistant_create_report", return_value=expected_report) as create_report,
            patch("backend.services.codex.console.tasks.register_report_history", return_value=None),
        ):
            result = codex_assistant.codex_assistant_report_create(
                payload,
                _request("/api/admin/codex/assistant/reports"),
                "Bearer test",
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["report"], expected_report)
        self.assertEqual(collect_data.call_args.kwargs["mode"], "report")
        create_report.assert_called_once_with(
            "000002",
            "Relatorio Codex Assistente",
            context,
            "Gerar relatorio solicitado",
        )

    def test_legacy_automatic_alert_cards_are_hidden(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        ui_source = (project_root / "static" / "ia-sidebar" / "02-ui-modelos.part.js").read_text(encoding="utf-8")
        self.assertIn(".jk-codex-msg.codex-alert{display:none!important;", ui_source)

    def test_sidebar_does_not_start_or_call_automatic_coordinator(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        sidebar_source = (project_root / "static" / "ia-sidebar" / "03-init-shell-codex.part.js").read_text(encoding="utf-8")
        bootstrap_source = (project_root / "static" / "ia-sidebar" / "09-chat-bootstrap.part.js").read_text(encoding="utf-8")

        for automatic_path in (
            "/api/admin/codex/assistant/suggestions",
            "/api/admin/codex/assistant/proactive/run",
            "/api/admin/codex/assistant/daily-analysis/run",
            "/api/admin/codex/assistant/weekly-analysis/run",
        ):
            self.assertNotIn(automatic_path, sidebar_source)
        self.assertNotIn("_codexIniciarAssistenteProativo", sidebar_source + bootstrap_source)
        self.assertNotIn("_codexExecutarCoordenador", sidebar_source + bootstrap_source)
        self.assertIn("_codexRemoverAlertasAutomaticosDoHistoricoLocal();", bootstrap_source)

    def test_legacy_automatic_messages_are_filtered_without_removing_manual_reports(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        sidebar_source = (project_root / "static" / "ia-sidebar" / "03-init-shell-codex.part.js").read_text(encoding="utf-8")

        self.assertIn("function _codexEhAlertaAutomatico(item)", sidebar_source)
        self.assertIn("classExtra || item.class_name", sidebar_source)
        self.assertIn("startsWith('codex_assistant|')", sidebar_source)
        self.assertIn("filter(item => !_codexEhAlertaAutomatico(item))", sidebar_source)
        self.assertIn("function _codexGerarRelatorioAssistente(prompt = '')", sidebar_source)
        self.assertIn("/api/admin/codex/assistant/reports", sidebar_source)


if __name__ == "__main__":
    unittest.main()
