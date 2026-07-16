import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services import codex_assistant
from backend.services import codex_assistant_storage
from backend.services import codex_console


def _db_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _db_payload(path: Path, table: str, key_col: str, key: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(f"SELECT payload_json FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else {}
    finally:
        conn.close()


class CodexAssistantSqliteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.info = Path(self.tmp.name) / "info"
        self.client_id = "000002"
        self.tenant = self.info / self.client_id / "codex_assistant"
        self.cache_dir = self.tenant / "cache"
        self.reports_dir = self.tenant / "reports"
        self.cache_dir.mkdir(parents=True)
        self.reports_dir.mkdir(parents=True)
        self.patches = [
            patch.object(codex_assistant, "PASTA_INFO", str(self.info), create=True),
            patch.object(codex_console, "PASTA_INFO", str(self.info), create=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        codex_console.CODEX_TASKS.clear()
        self.addCleanup(codex_console.CODEX_TASKS.clear)

    def _write_json(self, path: Path, payload: dict) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path.stat().st_mtime_ns

    def test_cache_importa_json_valido_ignora_expirado_e_salva_so_sqlite(self):
        now = time.time()
        valid_path = self.cache_dir / "validkey.json"
        valid_mtime = self._write_json(valid_path, {"_created_ts": now, "payload": {"answer": "ok"}})
        self._write_json(self.cache_dir / "expiredkey.json", {"_created_ts": now - 9999, "payload": {"answer": "old"}})

        payload = codex_assistant_storage.codex_assistant_cache_get(
            str(self.info),
            self.client_id,
            "validkey",
            3600,
        )
        expired = codex_assistant_storage.codex_assistant_cache_get(
            str(self.info),
            self.client_id,
            "expiredkey",
            60,
        )
        codex_assistant_storage.codex_assistant_cache_set(
            str(self.info),
            self.client_id,
            "newkey",
            {"fresh": True},
            3600,
        )

        self.assertEqual(payload, {"answer": "ok"})
        self.assertIsNone(expired)
        self.assertEqual(valid_path.stat().st_mtime_ns, valid_mtime)
        self.assertFalse((self.cache_dir / "newkey.json").exists())
        self.assertEqual(_db_count(self.cache_dir / "cache.db", "assistant_cache_entries"), 2)

    def test_scheduler_importa_json_rehidrata_relatorio_e_nao_regrava_json(self):
        scheduler_path = self.tenant / "scheduler_state.json"
        scheduler_mtime = self._write_json(
            scheduler_path,
            {
                "last_daily_date": "2026-07-08",
                "last_daily_report": {
                    "report_id": "daily-1",
                    "title": "Analise diaria",
                    "prompt": "daily",
                    "created_at": "2026-07-08T08:00:00",
                    "chat_text": "Resumo",
                    "formats": {"html": True},
                    "downloads": {"html": "/download"},
                },
            },
        )

        state = codex_assistant._assistant_scheduler_state(self.client_id)
        codex_assistant._assistant_save_scheduler_state(self.client_id, {**state, "last_proactive_at": "agora"})

        self.assertEqual(scheduler_path.stat().st_mtime_ns, scheduler_mtime)
        self.assertEqual(state["last_daily_report"]["report_id"], "daily-1")
        state_db = self.tenant / "codex_assistant.db"
        stored = _db_payload(state_db, "assistant_scheduler_state", "name", "scheduler_state")
        self.assertEqual(stored["last_daily_report_id"], "daily-1")
        self.assertNotIn("last_daily_report", stored)
        self.assertEqual(_db_count(state_db, "assistant_reports"), 1)

    def test_report_metadata_legado_e_novo_relatorio_usam_sqlite(self):
        legacy_dir = self.reports_dir / "legacy-1"
        legacy_path = legacy_dir / "metadata.json"
        legacy_mtime = self._write_json(
            legacy_path,
            {
                "report_id": "legacy-1",
                "title": "Relatorio legado",
                "prompt": "legado",
                "created_at": "2026-07-08T09:00:00",
                "chat_text": "texto legado",
                "formats": {"html": True},
                "downloads": {"html": "/download"},
            },
        )

        report = codex_assistant_storage.codex_assistant_report_get(str(self.info), self.client_id, "legacy-1")
        again = codex_assistant_storage.codex_assistant_report_get(str(self.info), self.client_id, "legacy-1")

        self.assertEqual(report["title"], "Relatorio legado")
        self.assertEqual(again["report_id"], "legacy-1")
        self.assertEqual(legacy_path.stat().st_mtime_ns, legacy_mtime)
        self.assertEqual(_db_count(self.tenant / "codex_assistant.db", "assistant_reports"), 1)

        def fake_xlsx(path, context, suggestions):
            Path(path).write_text("xlsx", encoding="utf-8")

        def fake_pdf(path, title, suggestions, sources, analysis=None, context=None):
            Path(path).write_text("pdf", encoding="utf-8")

        with patch.object(codex_assistant, "_assistant_write_xlsx", fake_xlsx), patch.object(
            codex_assistant,
            "_assistant_write_pdf",
            fake_pdf,
        ):
            created = codex_assistant._assistant_create_report(
                self.client_id,
                "Relatorio novo",
                {"sources": [], "suggestions": [], "tool_results": [], "management_analysis": {}},
                "prompt novo",
            )

        created_dir = self.reports_dir / created["report_id"]
        self.assertTrue((created_dir / "report.html").exists())
        self.assertTrue((created_dir / "report.xlsx").exists())
        self.assertTrue((created_dir / "report.pdf").exists())
        self.assertFalse((created_dir / "metadata.json").exists())
        loaded = codex_assistant_storage.codex_assistant_report_get(str(self.info), self.client_id, created["report_id"])
        self.assertEqual(loaded["title"], "Relatorio novo")

    def test_backfill_console_encontra_db_e_json_legado(self):
        db_report = {
            "report_id": "db-report",
            "created_by": "caio",
            "title": "Relatorio DB",
            "prompt": "db",
            "created_at": "2026-07-08T10:00:00",
            "chat_text": "texto db",
            "formats": {"html": True},
            "downloads": {"html": "/download"},
        }
        codex_assistant_storage.codex_assistant_report_save(str(self.info), self.client_id, db_report)
        legacy_dir = self.reports_dir / "legacy-backfill"
        self._write_json(
            legacy_dir / "metadata.json",
            {
                "report_id": "legacy-backfill",
                "created_by": "caio",
                "title": "Relatorio JSON",
                "prompt": "json",
                "created_at": "2026-07-08T11:00:00",
                "chat_text": "texto json",
                "formats": {"html": True},
                "downloads": {"html": "/download"},
            },
        )

        codex_console._codex_backfill_assistant_report_tasks(self.client_id, username="caio", limit=10)

        self.assertTrue((self.info / "codex_console" / "db-report.json").exists())
        self.assertTrue((self.info / "codex_console" / "legacy-backfill.json").exists())
        self.assertIn("db-report", codex_console.CODEX_TASKS)
        self.assertIn("legacy-backfill", codex_console.CODEX_TASKS)


if __name__ == "__main__":
    unittest.main()
