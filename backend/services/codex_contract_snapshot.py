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
from backend.services import jk_codex_mcp_server
from backend.services.codex.console import contracts as console_contracts
from backend.services.codex.console import execution as console_execution
from backend.services.codex.console import security as console_security
from backend.services.codex.console import state as console_state
from backend.services.codex.console import tasks as console_tasks


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
            console_contracts.CodexTaskRequest,
            console_contracts.CodexTaskSteerRequest,
            console_contracts.CodexTaskApprovalRequest,
            console_contracts.CodexTaskFeedbackRequest,
            console_contracts.CodexEvaluationRunAPIRequest,
            console_contracts.CodexMCPRolloutRequest,
            console_contracts.CodexConversationResetRequest,
            console_contracts.CodexActionProposalRequest,
            console_contracts.CodexActionApprovalRequest,
            console_contracts.CodexActionRevisionRequest,
            console_contracts.CodexAgentGuidanceRequest,
            console_contracts.CodexAgentGuidanceSimulationRequest,
            console_contracts.CodexCapabilityResolveRequest,
        )
    }
    source_contracts = {
        "public_task": _source_hash(console_tasks.public),
        "sandbox_policy": _source_hash(console_security.normalize_sandbox),
        "development_classifier": _source_hash(console_security.prompt_requests_development),
        "tool_catalog": _source_hash(console_execution.tool_catalog),
        "initial_prompt": _source_hash(console_execution.initial_prompt),
        "mcp_plan_builder": _source_hash(jk_codex_mcp_server.build_plan_v2),
        "mcp_plan_validator": _source_hash(jk_codex_mcp_server.validate_plan_v2),
    }
    contracts = {
        "routes": routes,
        "request_models": request_models,
        "security": {
            "execution_plane": console_state.CODEX_EXECUTION_PLANE,
            "development_write_enabled": console_state.CODEX_DEVELOPMENT_WRITE_ENABLED,
            "allowed_sandboxes": sorted(console_state.CODEX_INTERNAL_ALLOWED_SANDBOXES),
            "development_error_code": console_state.CODEX_DEVELOPMENT_ERROR_CODE,
        },
        "models": {
            "baseline": console_state.CODEX_DEFAULT_MODEL,
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
