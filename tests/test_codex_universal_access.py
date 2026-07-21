import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend.routers.codex_console import create_codex_console_router
from backend.services import codex_console


def _request(path: str = "/api/codex/tasks") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


class CodexUniversalAccessTest(unittest.TestCase):
    def setUp(self):
        codex_console.CODEX_TASKS.clear()
        self.addCleanup(codex_console.CODEX_TASKS.clear)
        self.addCleanup(self._close_telemetry)

    @staticmethod
    def _close_telemetry():
        telemetry = codex_console.CODEX_AI_TELEMETRY
        if telemetry is not None:
            telemetry.close()
            codex_console.CODEX_AI_TELEMETRY = None

    def test_authenticated_session_fails_closed_without_identity(self):
        with patch.object(codex_console, "_codex_payload_sessao", return_value={"client_id": "000002"}):
            with self.assertRaises(HTTPException) as ctx:
                codex_console._codex_require_authenticated(_request(), "Bearer token")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_nonfull_task_is_forced_to_server_readonly_profile(self):
        sessao = {
            "username": "operador",
            "client_id": "000002",
            "permissions": {"vendas": True, "full": False},
            "is_full": False,
        }
        payload = codex_console.CodexTaskRequest(
            prompt="Resuma as vendas visiveis.",
            sandbox="read_only",
            approval_mode="read_only",
            model="modelo-nao-autorizado",
            reasoning_effort="low",
            speed="fast",
            service_tier="priority",
            cwd="C:/segredo",
            thread_id="thread-admin",
            goal="alterar o sistema",
            planning_mode=True,
            paths=[],
            conversation_id="conversa-operador",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            info_dir = Path(temp_dir) / "info"
            local_app_data = Path(temp_dir) / "local"
            with (
                patch.object(codex_console, "_codex_require_authenticated", return_value=sessao),
                patch.object(codex_console, "_codex_enabled", return_value=True),
                patch.object(codex_console, "_codex_sdk_installed", return_value=True),
                patch.object(codex_console, "_codex_cleanup_old_attachments", return_value=None),
                patch.object(codex_console, "_codex_start_thread") as start_thread,
                patch.object(codex_console, "PASTA_INFO", str(info_dir), create=True),
                patch.dict(
                    os.environ,
                    {
                        "LOCALAPPDATA": str(local_app_data),
                        "JK_CODEX_MODEL": "gpt-5.5",
                        "JK_CODEX_REASONING_EFFORT": "xhigh",
                        "JK_CODEX_SPEED": "standard",
                    },
                    clear=False,
                ),
            ):
                result = codex_console.codex_criar_tarefa(payload, _request(), "Bearer token")
                self._close_telemetry()

        task_id = result["task"]["task_id"]
        task = codex_console.CODEX_TASKS[task_id]
        start_thread.assert_called_once_with(task_id)
        self.assertEqual(task["sandbox"], "read_only")
        self.assertEqual(task["approval_mode"], "read_only")
        self.assertEqual(task["model"], "gpt-5.5")
        self.assertEqual(task["reasoning_effort"], "xhigh")
        self.assertEqual(task["speed"], "standard")
        self.assertEqual(task["service_tier"], "")
        self.assertEqual(task["thread_id"], "")
        self.assertEqual(task["goal"], "")
        self.assertFalse(task["planning_mode"])
        self.assertEqual(task["paths"], [])
        self.assertTrue(task["permissions"]["vendas"])
        self.assertFalse(task["permissions"]["full"])
        self.assertIn("black_jhon_readonly", task["cwd"].replace("\\", "/"))
        self.assertNotIn("C:/segredo", task["cwd"].replace("\\", "/"))

    def test_nonfull_cannot_supply_paths_or_upload_attachments(self):
        sessao = {
            "username": "operador",
            "client_id": "000002",
            "permissions": {},
            "is_full": False,
        }
        with self.assertRaises(HTTPException) as paths_ctx:
            codex_console._codex_resolver_paths_for_session(
                ["backend_api.py"], sessao, "conversa-operador"
            )
        self.assertEqual(paths_ctx.exception.status_code, 403)

        with patch.object(
            codex_console,
            "_codex_require_full_admin",
            side_effect=HTTPException(status_code=403, detail="full required"),
        ):
            with self.assertRaises(HTTPException) as upload_ctx:
                asyncio.run(
                    codex_console.codex_upload_attachments(
                        _request("/api/codex/attachments"),
                        files=[],
                        authorization="Bearer token",
                    )
                )
        self.assertEqual(upload_ctx.exception.status_code, 403)

    def test_task_ownership_is_client_and_username_scoped(self):
        task = {
            "task_id": "task-owner",
            "client_id": "000002",
            "created_by": "operador",
            "status": "completed",
        }
        codex_console.CODEX_TASKS["task-owner"] = task
        owner = {"client_id": "000002", "username": "operador"}
        other_user = {"client_id": "000002", "username": "outro"}
        other_client = {"client_id": "000003", "username": "operador"}

        self.assertIs(codex_console._codex_require_owned_task("task-owner", owner), task)
        for sessao in (other_user, other_client):
            with self.assertRaises(HTTPException) as ctx:
                codex_console._codex_require_owned_task("task-owner", sessao)
            self.assertEqual(ctx.exception.status_code, 404)

    def test_nonfull_status_hides_internal_paths(self):
        sensitive = {
            "success": True,
            "ready": True,
            "cli_path": "C:/codex.exe",
            "auth_file_path": "C:/Users/admin/.codex/auth.json",
            "cwd": "C:/repo",
            "defaults": {"sandbox": "workspace_write", "approval_mode": "request"},
        }
        with patch.object(codex_console, "_codex_status_payload", return_value=sensitive):
            result = codex_console._codex_status_for_session({"is_full": False})

        self.assertNotIn("cli_path", result)
        self.assertNotIn("auth_file_path", result)
        self.assertNotIn("cwd", result)
        self.assertEqual(result["access"]["mode"], "read_only")
        self.assertFalse(result["access"]["can_upload"])
        self.assertEqual(result["defaults"]["sandbox"], "read_only")
        self.assertEqual(result["defaults"]["approval_mode"], "read_only")

    def test_restricted_codex_profile_disables_external_capabilities(self):
        overrides = set(codex_console._codex_nonfull_config_overrides())
        expected = {
            'default_permissions="jk_black_jhon_readonly"',
            "features.shell_tool=false",
            "features.unified_exec=false",
            "features.apps=false",
            "features.browser_use=false",
            "features.computer_use=false",
            "features.image_generation=false",
            "features.in_app_browser=false",
            "features.plugins=false",
            "features.multi_agent=false",
            "features.memories=false",
            "features.goals=false",
            "features.hooks=false",
            "features.network_proxy=false",
            "apps._default.enabled=false",
            "tools.web_search=false",
            "tools.view_image=false",
            'web_search="disabled"',
            'history.persistence="none"',
        }
        self.assertTrue(expected.issubset(overrides))
        self.assertIn("features.fast_mode=false", overrides)
        self.assertTrue(any(item.startswith("mcp_servers.") and item.endswith(".enabled=false") for item in overrides))

    def test_restricted_codex_profile_can_enable_fast_mode_without_opening_capabilities(self):
        overrides = set(codex_console._codex_nonfull_config_overrides(fast_mode=True))

        self.assertIn("features.fast_mode=true", overrides)
        self.assertNotIn("features.fast_mode=false", overrides)
        self.assertIn("features.shell_tool=false", overrides)
        self.assertIn("features.apps=false", overrides)
        self.assertIn("tools.web_search=false", overrides)

    def test_universal_routes_exist_without_removing_admin_compatibility(self):
        paths = {route.path for route in create_codex_console_router().routes}
        self.assertIn("/api/codex/status", paths)
        self.assertIn("/api/codex/tasks", paths)
        self.assertIn("/api/codex/conversations/current/reset", paths)
        self.assertIn("/api/codex/tasks/{task_id}/cancel", paths)
        self.assertIn("/api/admin/codex/tasks", paths)
        self.assertIn("/api/admin/codex/actions", paths)
        self.assertIn("/api/admin/codex/assistant/weekly-analysis/run", paths)
        self.assertIn("/api/admin/codex/assistant/report-settings", paths)
        self.assertIn("/api/admin/codex/assistant/financial-adjustments", paths)
        self.assertIn("/api/admin/codex/assistant/action-queue", paths)


if __name__ == "__main__":
    unittest.main()
