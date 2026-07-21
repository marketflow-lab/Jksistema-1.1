"""Deterministic contract inventory for the internal JK Sistema Codex.

The snapshot intentionally contains no tenant data.  It freezes public routes,
request schemas and the source-level contracts whose accidental drift would
change permissions, prompts, tools or MCP behaviour.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

from backend.routers.codex_console import create_codex_console_router
from backend.services import codex_console, jk_codex_mcp_server


SNAPSHOT_SCHEMA_VERSION = "1.0"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source_hash(value: Any) -> str:
    source = inspect.getsource(value).replace("\r\n", "\n").strip()
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _model_schema(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return dict(model.model_json_schema())
    return dict(model.schema())


def build_codex_contract_snapshot() -> dict[str, Any]:
    router = create_codex_console_router()
    routes = sorted(
        (
            {
                "path": str(route.path),
                "methods": sorted(str(method) for method in (route.methods or set())),
                "name": str(route.name or ""),
            }
            for route in router.routes
        ),
        key=lambda item: (item["path"], item["methods"], item["name"]),
    )
    request_models = {
        model.__name__: _model_schema(model)
        for model in (
            codex_console.CodexTaskRequest,
            codex_console.CodexTaskSteerRequest,
            codex_console.CodexTaskApprovalRequest,
            codex_console.CodexTaskFeedbackRequest,
            codex_console.CodexEvaluationRunAPIRequest,
            codex_console.CodexMCPRolloutRequest,
            codex_console.CodexActionProposalRequest,
            codex_console.CodexActionApprovalRequest,
            codex_console.CodexActionRevisionRequest,
        )
    }
    source_contracts = {
        "public_task": _source_hash(codex_console._codex_public_task),
        "sandbox_policy": _source_hash(codex_console._codex_normalizar_sandbox),
        "development_classifier": _source_hash(codex_console._codex_prompt_pede_desenvolvimento),
        "tool_catalog": _source_hash(codex_console._codex_agent_tool_catalog),
        "initial_prompt": _source_hash(codex_console._codex_agent_initial_prompt),
        "mcp_plan_builder": _source_hash(jk_codex_mcp_server.build_plan_v2),
        "mcp_plan_validator": _source_hash(jk_codex_mcp_server.validate_plan_v2),
    }
    contracts = {
        "routes": routes,
        "request_models": request_models,
        "security": {
            "execution_plane": codex_console.CODEX_EXECUTION_PLANE,
            "development_write_enabled": codex_console.CODEX_DEVELOPMENT_WRITE_ENABLED,
            "allowed_sandboxes": sorted(codex_console.CODEX_INTERNAL_ALLOWED_SANDBOXES),
            "development_error_code": codex_console.CODEX_DEVELOPMENT_ERROR_CODE,
        },
        "models": {
            "baseline": codex_console.CODEX_DEFAULT_MODEL,
            "explicit_candidates": ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"],
        },
        "mcp": {
            "protocol": jk_codex_mcp_server.MCP_PROTOCOL_V2,
            "plan_schema_version": jk_codex_mcp_server.MCP_PLAN_SCHEMA_VERSION,
            "server_version": jk_codex_mcp_server.SERVER_VERSION,
            "max_plan_calls": jk_codex_mcp_server.MAX_PLAN_CALLS,
        },
        "source_contract_hashes": source_contracts,
    }
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "contracts": contracts,
        "contracts_sha256": _sha256(contracts),
    }


__all__ = ["SNAPSHOT_SCHEMA_VERSION", "build_codex_contract_snapshot"]
