import unittest
from unittest.mock import patch

from backend.services import codex_assistant, codex_console


class CodexToolPermissionsTest(unittest.TestCase):
    def test_every_registered_tool_has_an_explicit_permission_policy(self):
        registered = {str(tool.get("id") or "") for tool in codex_assistant.CODEX_DATA_TOOLS}
        covered = set(codex_assistant.ASSISTANT_TOOL_PERMISSION_REQUIREMENTS)
        covered.update(codex_assistant.ASSISTANT_FULL_ONLY_TOOLS)
        self.assertEqual(registered, covered)

    def test_catalog_only_contains_tools_allowed_for_user(self):
        tools = codex_assistant._assistant_tools_public({"vendas": True})
        ids = {str(tool.get("id") or "") for tool in tools}

        self.assertIn("sales_ranking", ids)
        self.assertIn("sales_summary", ids)
        self.assertNotIn("stock_data", ids)
        self.assertNotIn("source_discovery", ids)
        self.assertNotIn("program_functions_catalog", ids)
        self.assertNotIn("operational_memory_query", ids)

        sales = next(tool for tool in tools if tool.get("id") == "sales_ranking")
        self.assertEqual(sales.get("required_permissions"), ["vendas"])
        self.assertEqual(sales.get("fallbacks"), [])

    def test_catalog_is_fail_closed_without_permissions(self):
        self.assertEqual(codex_assistant._assistant_tools_public(), [])
        self.assertEqual(codex_console._codex_agent_tool_catalog({}), [])

    def test_full_catalog_keeps_all_registered_tools(self):
        tools = codex_assistant._assistant_tools_public({"full": True})
        self.assertEqual(len(tools), len(codex_assistant.CODEX_DATA_TOOLS))

    def test_non_full_prompt_does_not_read_shared_operational_memory(self):
        with patch.object(
            codex_console.codex_operational_memory,
            "compact_context",
            side_effect=AssertionError("shared memory must not be read"),
        ) as compact_context:
            prompt = codex_console._codex_agent_initial_prompt(
                "ranking de vendas",
                {},
                {},
                "tenant-test",
                {"vendas": True},
                "read_only",
                "gpt-5.5",
                "high",
                "standard",
                "read_only",
            )

        compact_context.assert_not_called()
        self.assertNotIn("Memoria operacional persistida", prompt)
        self.assertIn("sales_ranking", prompt)
        self.assertNotIn('"id": "source_discovery"', prompt)

    def test_legacy_context_is_disabled_for_non_full_user(self):
        with patch.object(
            codex_assistant,
            "codex_assistant_collect_context",
            side_effect=AssertionError("legacy collector must not run"),
            create=True,
        ) as collect_context:
            result = codex_console._codex_app_data_context(
                "liste vendas",
                {},
                "tenant-test",
                {"vendas": True},
            )

        collect_context.assert_not_called()
        self.assertTrue(result.get("permission_filtered"))
        self.assertEqual(result.get("tool_results_count"), 0)

    def test_cross_domain_tool_requires_every_permission(self):
        result = codex_assistant.codex_assistant_execute_tool_call(
            client_id="tenant-test",
            tool_id="stockout_forecast",
            args={"message": "risco de ruptura"},
            permissions={"estoque": True},
        )

        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("error_code"), "tool_permission_denied")
        self.assertEqual(result.get("missing_permissions"), ["vendas"])

    def test_full_only_universal_tool_is_denied_to_regular_user(self):
        result = codex_assistant.codex_assistant_execute_tool_call(
            client_id="tenant-test",
            tool_id="local_database_query",
            args={"message": "liste tudo"},
            permissions={"vendas": True, "estoque": True},
        )

        self.assertFalse(result.get("success"))
        self.assertEqual(result.get("error_code"), "tool_permission_denied")
        self.assertEqual(result.get("required_permissions"), ["full"])

    def test_executor_runs_allowed_tool_but_skips_forbidden_fallbacks(self):
        calls = []

        def fake_execute(_client_id, tool_id, _message, _screen_context, plan, _previous):
            calls.append(tool_id)
            records = 1 if tool_id == "sales_returns_query" else 0
            return (
                [],
                [
                    {
                        "tool_id": tool_id,
                        "tool_label": tool_id,
                        "module": "vendas",
                        "records": records,
                        "rows": [{"sku": "SKU-1"}] if records else [],
                        "summary": {},
                        "source": tool_id,
                        "sources_human": [],
                        "periodo": plan.get("periodo") or {},
                        "loja": plan.get("loja") or "",
                        "empty_reason": "sem registros",
                        "next_fallbacks": ["integrations_status", "source_discovery"],
                    }
                ],
                [],
            )

        with patch.object(codex_assistant, "_assistant_execute_registry_tool", side_effect=fake_execute):
            result = codex_assistant.codex_assistant_execute_tool_call(
                client_id="tenant-test",
                tool_id="sales_ranking",
                args={"message": "ranking de vendas"},
                permissions={"vendas": True},
            )

        self.assertTrue(result.get("success"))
        self.assertEqual(calls, ["sales_ranking", "sales_returns_query"])
        self.assertNotIn("integrations_status", calls)
        self.assertNotIn("source_discovery", calls)
        self.assertEqual(result.get("next_fallbacks"), [])


if __name__ == "__main__":
    unittest.main()
