import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from backend.services import codex_assistant, codex_console


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


def _report(chat_text: str) -> dict:
    return {
        "report_id": "report-1",
        "title": "Relatorio diario",
        "created_at": "2026-07-10T12:00:00Z",
        "formats": {"html": True, "pdf": True},
        "downloads": {"pdf": "/report-1.pdf"},
        "chat_download_formats": ["pdf"],
        "chat_text": chat_text,
    }


class CodexAssistantCompactResponseTest(unittest.TestCase):
    def test_weekly_is_due_once_after_monday_8am_even_if_app_opens_later(self):
        monday = datetime(2026, 7, 13, 9, 0, 0)
        with patch.object(codex_assistant, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = monday
            mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            self.assertTrue(codex_assistant._assistant_weekly_due({}))
            self.assertFalse(codex_assistant._assistant_weekly_due({"last_weekly_key": "2026-W29"}))
            self.assertTrue(codex_assistant._assistant_weekly_due({"last_weekly_key": "2026-W29"}, force=True))

        tuesday = datetime(2026, 7, 14, 14, 0, 0)
        with patch.object(codex_assistant, "datetime") as mocked_datetime:
            mocked_datetime.now.return_value = tuesday
            mocked_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            self.assertTrue(codex_assistant._assistant_weekly_due({}))
            self.assertFalse(codex_assistant._assistant_weekly_due({"last_weekly_key": "2026-W29"}))

    def test_daily_compact_omits_full_chat_text_without_mutating_scheduler_state(self):
        full_text = "R" * 1200
        state = {"last_daily_date": "2026-07-10", "last_daily_report": _report(full_text)}
        payload = codex_assistant.CodexAssistantRunRequest(compact=True)

        with (
            patch.object(codex_assistant, "_assistant_require_full_admin", return_value={"client_id": "000002", "username": "owner"}),
            patch.object(codex_assistant, "_assistant_scheduler_state", return_value=state),
            patch.object(codex_assistant, "_assistant_daily_due", return_value=False),
        ):
            result = codex_assistant.codex_assistant_daily_analysis_run(payload, _request("/daily"), "Bearer test")

        self.assertTrue(result["compact"])
        self.assertEqual(result["status"], "disabled_weekly_only")
        self.assertNotIn("report", result)
        self.assertNotIn("last_daily_report", result["scheduler"])
        self.assertEqual(result["scheduler"]["last_daily_report_id"], "report-1")
        self.assertEqual(state["last_daily_report"]["chat_text"], full_text)

    def test_daily_default_keeps_legacy_full_chat_text(self):
        full_text = "D" * 900
        state = {"last_daily_date": "2026-07-10", "last_daily_report": _report(full_text)}
        payload = codex_assistant.CodexAssistantRunRequest()

        with (
            patch.object(codex_assistant, "_assistant_require_full_admin", return_value={"client_id": "000002", "username": "owner"}),
            patch.object(codex_assistant, "_assistant_scheduler_state", return_value=state),
            patch.object(codex_assistant, "_assistant_daily_due", return_value=False),
        ):
            result = codex_assistant.codex_assistant_daily_analysis_run(payload, _request("/daily"), "Bearer test")

        self.assertNotIn("compact", result)
        self.assertEqual(result["status"], "disabled_weekly_only")
        self.assertIsNone(result["report"])

    def test_proactive_compact_also_compacts_nested_scheduler_report(self):
        full_text = "P" * 700
        state = {
            "last_proactive_ts": time.time(),
            "last_daily_report": _report(full_text),
        }
        payload = codex_assistant.CodexAssistantRunRequest(compact=True)

        with (
            patch.object(codex_assistant, "_assistant_require_full_admin", return_value={"client_id": "000002", "username": "owner"}),
            patch.object(codex_assistant, "_assistant_scheduler_state", return_value=state),
            patch.object(codex_assistant, "_assistant_read_json", return_value=[]),
        ):
            result = codex_assistant.codex_assistant_proactive_run(payload, _request("/proactive"), "Bearer test")

        self.assertNotIn("last_daily_report", result["scheduler"])
        self.assertEqual(result["scheduler"]["last_daily_report_id"], "report-1")
        self.assertEqual(state["last_daily_report"]["chat_text"], full_text)

    def test_realistic_payload_over_two_mb_projects_below_50kb_without_mutation(self):
        full_text = "T" * 163_003
        heavy_report = _report(full_text)
        heavy_report.update(
            {
                "generated_at": "2026-07-10T12:00:00Z",
                "kind": "daily",
                "status": "completed",
                "registry_results": [{"rows": "R" * 1_600_000}],
                "specialist_sku_diagnostics": [{"blob": "S" * 350_000}],
                "context": {"dataset": "C" * 300_000},
                "datasets": ["D" * 120_000],
                "suggestions": [{"internal_blob": "I" * 80_000}],
                "html": "<html>" + ("H" * 100_000) + "</html>",
                "status_steps": [{"status": "processando", "blob": "B" * 20_000}] * 40,
                "warnings": [("aviso " + ("W" * 2_000))] * 40,
            }
        )
        suggestions = [
            {
                "id": f"suggestion-{idx}",
                "kind": "stockout",
                "title": "Titulo " + ("X" * 500),
                "detail": "Detalhe " + ("Y" * 10_000),
                "recommendation": "Acao " + ("Z" * 10_000),
                "report_prompt": "Relatorio " + ("P" * 10_000),
                "internal_blob": "N" * 100_000,
            }
            for idx in range(30)
        ]
        persisted = {
            "success": True,
            "status": "completed",
            "due": True,
            "report": heavy_report,
            "scheduler": {
                "last_daily_date": "2026-07-10",
                "last_daily_report": heavy_report,
                "last_proactive_sources": [{"blob": "F" * 100_000}],
            },
            "suggestions": suggestions,
        }
        self.assertGreater(len(json.dumps(persisted).encode("utf-8")), 2_000_000)

        result = codex_assistant._assistant_compact_chat_text_response(persisted)
        compact_size = len(json.dumps(result).encode("utf-8"))

        self.assertLess(compact_size, 50_000)
        self.assertEqual(len(result["report"]["chat_text_preview"]), 600)
        self.assertLessEqual(len(result["report"]["status_steps"]), 12)
        self.assertLessEqual(len(result["report"]["warnings"]), 12)
        self.assertTrue(all(len(item) <= 300 for item in result["report"]["warnings"]))
        self.assertLessEqual(
            set(result["report"]),
            {
                "report_id",
                "title",
                "generated_at",
                "created_at",
                "kind",
                "status",
                "status_steps",
                "warnings",
                "formats",
                "downloads",
                "chat_download_formats",
                "chat_text_preview",
                "chat_text_length",
                "truncated",
                "report_type",
                "scope",
                "data_quality",
                "financial_coverage",
                "financial_summary",
                "top_actions",
            },
        )
        self.assertNotIn("registry_results", result["report"])
        self.assertNotIn("specialist_sku_diagnostics", result["report"])
        self.assertNotIn("context", result["report"])
        self.assertNotIn("datasets", result["report"])
        self.assertNotIn("suggestions", result["report"])
        self.assertNotIn("html", result["report"])
        self.assertNotIn("last_daily_report", result["scheduler"])
        self.assertLessEqual(len(result["suggestions"]), 20)
        self.assertNotIn("internal_blob", result["suggestions"][0])
        self.assertLessEqual(len(result["suggestions"][0].get("detail", "")), 900)
        self.assertLessEqual(len(result["suggestions"][0].get("report_prompt", "")), 1200)
        self.assertEqual(persisted["report"]["registry_results"][0]["rows"], "R" * 1_600_000)
        self.assertEqual(persisted["report"]["chat_text"], full_text)


class CodexTaskSummaryListTest(unittest.TestCase):
    def _task(self, task_id: str, client_id: str, owner: str) -> dict:
        return {
            "task_id": task_id,
            "client_id": client_id,
            "created_by": owner,
            "status": "completed",
            "created_at": "2026-07-10T10:00:00Z",
            "started_at": "2026-07-10T10:00:01Z",
            "completed_at": "2026-07-10T10:00:02Z",
            "model": "gpt-5.5",
            "conversation_id": "conversation-1",
            "thread_id": "thread-1",
            "prompt": "Q" * 500,
            "final_response": "A" * 1000,
            "message_kind": "report",
            "report_id": "report-1",
            "report_formats": ["pdf", "xlsx"],
            "context_stats": {"estimated_tokens": 123},
            "token_usage": {"input_tokens": 10, "output_tokens": 20},
            "logs": [{"text": "log sensivel"}],
            "screen_context": {"visible_text": "conteudo integral"},
            "history": [{"role": "user", "text": "historico"}],
        }

    def _list(self, directory: str, *, summary: bool = False) -> dict:
        session = {"client_id": "000002", "username": "owner", "is_full": False}
        with (
            patch.object(codex_console, "_codex_require_authenticated", return_value=session),
            patch.object(codex_console, "_codex_info_dir", return_value=directory),
            patch.object(codex_console, "_codex_deleted_conversation_ids", return_value=set()),
        ):
            return codex_console.codex_listar_tarefas(
                _request("/api/codex/tasks"),
                "Bearer test",
                limit=20,
                summary=summary,
            )

    def test_summary_is_light_and_preserves_ownership_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks = [
                self._task("owned", "000002", "owner"),
                self._task("other-user", "000002", "intruder"),
                self._task("other-client", "000003", "owner"),
            ]
            for task in tasks:
                Path(directory, f"{task['task_id']}.json").write_text(json.dumps(task), encoding="utf-8")

            result = self._list(directory, summary=True)

        self.assertEqual([item["task_id"] for item in result["tasks"]], ["owned"])
        task = result["tasks"][0]
        self.assertEqual(len(task["prompt_preview"]), 240)
        self.assertEqual(len(task["response_preview"]), 600)
        self.assertEqual(task["report_formats"], ["pdf", "xlsx"])
        self.assertEqual(task["context_stats"], {"estimated_tokens": 123})
        self.assertEqual(task["token_usage"]["output_tokens"], 20)
        for forbidden in ("prompt", "final_response", "live_answer", "logs", "screen_context", "history"):
            self.assertNotIn(forbidden, task)

    def test_default_list_keeps_full_legacy_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            task = self._task("owned", "000002", "owner")
            Path(directory, "owned.json").write_text(json.dumps(task), encoding="utf-8")

            result = self._list(directory)

        listed = result["tasks"][0]
        self.assertEqual(listed["prompt"], "Q" * 500)
        self.assertEqual(listed["final_response"], "A" * 1000)
        self.assertEqual(listed["logs"], [{"text": "log sensivel"}])
        self.assertEqual(listed["screen_context"], {"visible_text": "conteudo integral"})
        self.assertNotIn("prompt_preview", listed)


if __name__ == "__main__":
    unittest.main()
