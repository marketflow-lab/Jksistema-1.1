from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import sqlite3
from pathlib import Path

from backend.services import codex_assistant_storage as storage_facade
from backend.services.codex.storage import customer_reply_state
from backend.services.codex.storage.schema import _ensure_state_schema


ROOT = Path(__file__).resolve().parents[1]
FACADE_PATH = ROOT / "backend" / "services" / "codex_assistant_storage.py"
PACKAGE_PATH = ROOT / "backend" / "services" / "codex" / "storage"

EXPECTED_PUBLIC_EXPORTS = (
    "codex_assistant_customer_reply_job_has_transient",
    "codex_assistant_customer_reply_jobs_cleanup",
    "codex_assistant_client_dir",
    "codex_assistant_cache_db_path",
    "codex_assistant_state_db_path",
    "codex_assistant_cache_get",
    "codex_assistant_cache_set",
    "codex_assistant_reports_import_legacy",
    "codex_assistant_report_save",
    "codex_assistant_report_get",
    "codex_assistant_reports_list",
    "codex_assistant_scheduler_get",
    "codex_assistant_scheduler_save",
    "codex_assistant_report_settings_get",
    "codex_assistant_report_settings_save",
    "codex_assistant_financial_adjustment_save",
    "codex_assistant_financial_adjustments_list",
    "codex_assistant_financial_adjustment_delete",
    "codex_assistant_action_queue_save",
    "codex_assistant_action_queue_list",
    "codex_assistant_action_queue_get",
    "codex_assistant_agent_plan_save",
    "codex_assistant_agent_plan_get",
    "codex_assistant_customer_reply_job_save",
    "codex_assistant_customer_reply_job_get",
    "codex_assistant_customer_reply_job_latest",
    "codex_assistant_customer_reply_jobs_list",
    "codex_assistant_customer_reply_queue_metrics",
    "codex_assistant_customer_reply_job_claim",
    "codex_assistant_customer_reply_job_heartbeat",
    "codex_assistant_customer_reply_job_request_cancel",
    "codex_assistant_agent_guidance_save",
    "codex_assistant_agent_guidance_list",
    "codex_assistant_action_proposal_save",
    "codex_assistant_action_proposal_get",
    "codex_assistant_action_proposal_list",
    "codex_assistant_action_run_save",
    "codex_assistant_action_run_get",
    "codex_assistant_action_approval_save",
    "codex_assistant_agent_audit_add",
    "codex_assistant_agent_audit_list",
)
EXPECTED_SIGNATURES_SHA256 = "7e3109bd2bf54a3ca674c9bf9d67de667240a267d4a47598327945e5e29be776"
EXPECTED_STATE_SCHEMA_SHA256 = "80ed7be4e2b27f8866f38f726ca41a9e546450f02c2e9ee00cb174a036654f5b"


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_storage_facade_preserves_public_contract() -> None:
    assert tuple(storage_facade.__all__) == EXPECTED_PUBLIC_EXPORTS
    signature_contract = "\n".join(
        f"{name}{inspect.signature(getattr(storage_facade, name))}"
        for name in EXPECTED_PUBLIC_EXPORTS
    )
    assert hashlib.sha256(signature_contract.encode("utf-8")).hexdigest() == EXPECTED_SIGNATURES_SHA256


def test_storage_facade_preserves_transient_state_identity() -> None:
    assert storage_facade._CUSTOMER_REPLY_TRANSIENT is customer_reply_state._CUSTOMER_REPLY_TRANSIENT
    assert storage_facade._CUSTOMER_REPLY_TRANSIENT_LOCK is customer_reply_state._CUSTOMER_REPLY_TRANSIENT_LOCK


def test_storage_state_schema_contract() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_state_schema(conn)
    schema = [
        [
            row["type"],
            row["name"],
            row["tbl_name"],
            re.sub(r"\s+", " ", row["sql"].strip()) if row["sql"] else None,
        ]
        for row in conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_autoindex_%' ORDER BY type, name"
        )
    ]
    meta = [list(row) for row in conn.execute("SELECT key, value FROM assistant_meta ORDER BY key")]
    conn.close()
    contract = json.dumps({"schema": schema, "meta": meta}, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(contract.encode("utf-8")).hexdigest() == EXPECTED_STATE_SCHEMA_SHA256


def test_storage_modules_stay_within_size_limits() -> None:
    assert len(FACADE_PATH.read_text(encoding="utf-8").splitlines()) <= 200
    for path in PACKAGE_PATH.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 800, path.name
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno - node.lineno + 1 <= 120, f"{path.name}:{node.name}"


def test_storage_modules_do_not_import_facade_or_form_cycles() -> None:
    module_paths = {
        path.stem: path for path in PACKAGE_PATH.glob("*.py") if path.name != "__init__.py"
    }
    dependencies = {name: set() for name in module_paths}
    for name, path in module_paths.items():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "backend.services.codex_assistant_storage"
                assert all(alias.name != "*" for alias in node.names)
                if node.level == 1 and node.module in module_paths:
                    dependencies[name].add(node.module)

    visited: set[str] = set()
    active: set[str] = set()

    def visit(module: str) -> None:
        assert module not in active, f"cyclic storage import at {module}"
        if module in visited:
            return
        active.add(module)
        for dependency in dependencies[module]:
            visit(dependency)
        active.remove(module)
        visited.add(module)

    for module in dependencies:
        visit(module)
