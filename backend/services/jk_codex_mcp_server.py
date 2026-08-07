"""Closed, read-only MCP server used by Black Jhon Codex tasks.

The parent backend signs the task context and passes it only to this local
stdio child.  No application credential is exposed to the model.  The server
publishes the permission-filtered JK data catalog and delegates execution to
the same validated read-only dispatcher used by the compatibility agent loop.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any


SERVER_NAME = "jk-system-readonly"
SERVER_VERSION = "2026.07.20-plan-v2"
MAX_RESULT_CHARS = 180_000
MCP_PROTOCOL_V2 = "mcp_v2"
MCP_PLAN_SCHEMA_VERSION = "2.0"
MAX_PLAN_CALLS = 8
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode_context() -> dict[str, Any]:
    encoded = str(os.getenv("JK_CODEX_MCP_CONTEXT_B64") or "").strip()
    signature = str(os.getenv("JK_CODEX_MCP_CONTEXT_SIGNATURE") or "").strip().lower()
    secret = str(os.getenv("JK_CODEX_MCP_CONTEXT_SECRET") or "")
    if not encoded or not signature or not secret:
        raise RuntimeError("signed_context_missing")
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise RuntimeError("signed_context_invalid")
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("signed_context_invalid")
    expires_at = int(payload.get("expires_at") or 0)
    if expires_at and expires_at < int(time.time()):
        raise RuntimeError("signed_context_expired")
    return payload


def _normalize_store(value: Any) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(ch for ch in text if not unicodedata.combining(ch)).lower().split())


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _plan_hash(plan: dict[str, Any]) -> str:
    return _sha256({key: value for key, value in plan.items() if key != "plan_hash"})


def _argument_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        properties = {str(key): _argument_schema(item) for key, item in value.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(properties),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        return {"type": "array", "const": value}
    if value is None:
        return {"type": "null", "const": None}
    value_type = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string"
    return {"type": value_type, "const": value}


def _closed_schema(arguments: Any) -> dict[str, Any]:
    return _argument_schema(arguments if isinstance(arguments, dict) else {})


def _stable_store_refs(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for raw in list(value or []):
        if not isinstance(raw, dict):
            continue
        item = {
            "store_id": str(raw.get("store_id") or "").strip(),
            "name": str(raw.get("name") or raw.get("store_name") or "").strip(),
            "seller_id": str(raw.get("seller_id") or "").strip(),
            "site_id": str(raw.get("site_id") or "").strip().upper(),
        }
        if item["store_id"] and item["name"] and item["seller_id"] and item["site_id"]:
            refs.append(item)
    return refs


def _arguments_match_store_refs(arguments: dict[str, Any], refs: list[dict[str, str]]) -> bool:
    checks = {
        "store_id": str(arguments.get("store_id") or "").strip(),
        "seller_id": str(arguments.get("seller_id") or "").strip(),
        "site_id": str(arguments.get("site_id") or "").strip().upper(),
        "name": str(arguments.get("loja") or arguments.get("conta") or arguments.get("store") or "").strip(),
    }
    supplied = {key: value for key, value in checks.items() if value}
    if not supplied:
        return True
    return any(
        all(
            (_normalize_store(ref[key]) == _normalize_store(value) if key == "name" else str(ref[key]) == value)
            for key, value in supplied.items()
        )
        for ref in refs
    )


def validate_plan_v2(context: dict[str, Any]) -> dict[str, Any]:
    if str(context.get("protocol") or "") != MCP_PROTOCOL_V2:
        raise RuntimeError("mcp_v2_plan_required")
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    if str(plan.get("schema_version") or "") != MCP_PLAN_SCHEMA_VERSION:
        raise RuntimeError("mcp_plan_schema_invalid")
    expected_hash = str(plan.get("plan_hash") or "").strip().lower()
    if not expected_hash or not hmac.compare_digest(expected_hash, _plan_hash(plan)):
        raise RuntimeError("mcp_plan_hash_invalid")
    client_id = str(context.get("client_id") or "").strip()
    if not client_id or str(plan.get("client_id") or "").strip() != client_id:
        raise RuntimeError("mcp_plan_tenant_invalid")
    permissions = context.get("permissions") if isinstance(context.get("permissions"), dict) else {}
    source_policy = context.get("source_policy") if isinstance(context.get("source_policy"), dict) else {}
    if str(plan.get("permissions_hash") or "") != _sha256(permissions):
        raise RuntimeError("mcp_plan_permissions_invalid")
    if str(plan.get("source_policy_hash") or "") != _sha256(source_policy):
        raise RuntimeError("mcp_plan_source_policy_invalid")
    if not str(context.get("idempotency_db_path") or "").strip():
        raise RuntimeError("mcp_idempotency_store_required")
    store_refs = _stable_store_refs(plan.get("stores"))
    if not store_refs:
        raise RuntimeError("mcp_plan_store_refs_required")
    calls = list(plan.get("calls") or [])
    if not calls or len(calls) > MAX_PLAN_CALLS:
        raise RuntimeError("mcp_plan_calls_invalid")
    normalized_calls: list[dict[str, Any]] = []
    for index, raw in enumerate(calls):
        if not isinstance(raw, dict):
            raise RuntimeError("mcp_plan_order_invalid")
        try:
            call_index = int(raw.get("index") if raw.get("index") is not None else -1)
        except (TypeError, ValueError):
            raise RuntimeError("mcp_plan_order_invalid") from None
        if call_index != index:
            raise RuntimeError("mcp_plan_order_invalid")
        tool_id = str(raw.get("tool_id") or "").strip()
        arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else None
        arguments_hash = str(raw.get("arguments_hash") or "").strip().lower()
        dependencies = list(raw.get("depends_on") or [])
        if not tool_id or arguments is None or arguments_hash != _sha256(arguments):
            raise RuntimeError("mcp_plan_call_invalid")
        if not _arguments_match_store_refs(arguments, store_refs):
            raise RuntimeError("mcp_plan_store_scope_invalid")
        if any(not isinstance(item, int) or item < 0 or item >= index for item in dependencies):
            raise RuntimeError("mcp_plan_dependency_invalid")
        normalized_calls.append(
            {
                "index": index,
                "tool_id": tool_id,
                "arguments": arguments,
                "arguments_hash": arguments_hash,
                "depends_on": dependencies,
                "required": raw.get("required") is not False,
            }
        )
    planned_tools = sorted({call["tool_id"] for call in normalized_calls})
    if sorted({str(item or "").strip() for item in list(plan.get("allowed_tools") or []) if str(item or "").strip()}) != planned_tools:
        raise RuntimeError("mcp_plan_tools_invalid")
    context_tools = sorted({str(item or "").strip() for item in list(context.get("allowed_tools") or []) if str(item or "").strip()})
    if context_tools != planned_tools:
        raise RuntimeError("mcp_context_tools_invalid")
    normalized = dict(plan)
    normalized["calls"] = normalized_calls
    normalized["stores"] = store_refs
    normalized["plan_hash"] = expected_hash
    return normalized


def build_plan_v2(context: dict[str, Any], *, calls: list[dict[str, Any]], stores: list[dict[str, Any]]) -> dict[str, Any]:
    """Backend helper for producing the canonical hash before outer signing."""

    normalized_calls = [
        {
            **dict(call),
            "index": index,
            "arguments_hash": _sha256(dict(call.get("arguments") or {})),
        }
        for index, call in enumerate(calls)
    ]
    plan = {
        "schema_version": MCP_PLAN_SCHEMA_VERSION,
        "client_id": str(context.get("client_id") or "").strip(),
        "permissions_hash": _sha256(context.get("permissions") if isinstance(context.get("permissions"), dict) else {}),
        "source_policy_hash": _sha256(context.get("source_policy") if isinstance(context.get("source_policy"), dict) else {}),
        "allowed_tools": sorted({str(call.get("tool_id") or "").strip() for call in normalized_calls if str(call.get("tool_id") or "").strip()}),
        "stores": stores,
        "calls": normalized_calls,
    }
    plan["plan_hash"] = _plan_hash(plan)
    return plan


class JKCodexMCP:
    def __init__(self, context: dict[str, Any]) -> None:
        # Importing backend_api binds the extracted service modules to the same
        # runtime configuration and data paths used by the desktop backend.
        import backend_api  # noqa: F401
        from backend.services import codex_mcp_rollout
        from backend.services.codex.assistant import execution as assistant_execution
        from backend.services.codex.assistant import runtime as assistant_runtime
        from backend.services.codex.console import execution as console_execution

        assistant_runtime.configure_codex_assistant_runtime(backend_api)
        self.assistant_execution = assistant_execution
        self.console_execution = console_execution
        self.codex_mcp_rollout = codex_mcp_rollout
        self.context = context
        self.task_id = str(context.get("task_id") or "").strip()
        self.execution_id = str(context.get("execution_id") or "").strip()
        self.permissions = context.get("permissions") if isinstance(context.get("permissions"), dict) else {}
        self.screen_context = context.get("screen_context") if isinstance(context.get("screen_context"), dict) else {}
        self.source_policy = context.get("source_policy") if isinstance(context.get("source_policy"), dict) else {}
        self.allowed_tools = {
            str(item or "").strip()
            for item in (context.get("allowed_tools") or [])
            if str(item or "").strip()
        }
        self.authorized_stores = {
            _normalize_store(item)
            for item in (context.get("authorized_stores") or [])
            if _normalize_store(item)
        }
        self.store_scope_valid = context.get("store_scope_valid") is True
        self.previous_results: list[dict[str, Any]] = []
        self.call_cache: dict[str, dict[str, Any]] = {}
        self.result_path = Path(str(context.get("result_path") or "")).resolve() if context.get("result_path") else None
        self.plan_error = ""
        if not self.task_id:
            self.plan = None
            self.plan_error = "mcp_task_id_required"
        elif not self.execution_id:
            self.plan = None
            self.plan_error = "mcp_execution_id_required"
        else:
            try:
                self.plan = validate_plan_v2(context)
            except RuntimeError as exc:
                self.plan = None
                self.plan_error = str(exc)
        self.plan_calls = list(self.plan.get("calls") or []) if self.plan else []
        self.completed_indexes: set[int] = set()
        self.successful_indexes: set[int] = set()
        self.external_call_count = 0
        self.rollout_policy = codex_mcp_rollout.resolve_policy(context.get("rollout"))
        rollout_db = str(context.get("rollout_policy_db_path") or "").strip()
        self.rollout_store = codex_mcp_rollout.MCPRolloutPolicyStore(Path(rollout_db).resolve()) if rollout_db else None
        db_value = str(context.get("idempotency_db_path") or "").strip()
        if not db_value and self.result_path is not None:
            db_value = str(self.result_path.parent / "mcp_idempotency.sqlite3")
        self.idempotency = codex_mcp_rollout.MCPIdempotencyStore(Path(db_value).resolve()) if db_value else None
        if self.plan_error:
            self._critical_rollback("plan_violation")

    def _critical_rollback(self, flag: str) -> None:
        self.rollout_policy = self.codex_mcp_rollout.resolve_policy({})
        if self.rollout_store is None:
            return
        try:
            if self.task_id and self.execution_id:
                self.rollout_store.record_metric(
                    task_id=self.task_id,
                    execution_id=self.execution_id,
                    event_id=self._metric_event_id("critical"),
                    status="critical_violation",
                    duration_ms=0,
                    protocol_error=False,
                    error_code=str(flag or "plan_violation"),
                )
        except Exception:
            pass
        try:
            self.rollout_store.evaluate_and_rollback({str(flag or "plan_violation"): True})
        except Exception:
            # The current server still fails closed even when durable rollback
            # auditing cannot be written.
            return

    def _metric_event_id(self, kind: str, discriminator: str = "") -> str:
        suffix = str(discriminator or uuid.uuid4().hex).strip()[:96]
        return f"{str(kind or 'event')[:32]}:{self.execution_id}:{suffix}"[:160]

    def _record_execution_metric(
        self,
        *,
        event_id: str,
        status: str,
        duration_ms: float = 0.0,
        protocol_error: bool = False,
        error_code: str = "",
        evaluate: bool = True,
    ) -> dict[str, Any]:
        if self.rollout_store is None:
            return {"rolled_back": False, "reason": "", "rollout": self.rollout_policy}
        recorder = (
            self.rollout_store.record_metric_and_evaluate
            if evaluate
            else self.rollout_store.record_metric
        )
        result = recorder(
            task_id=self.task_id,
            execution_id=self.execution_id,
            event_id=event_id,
            status=status,
            duration_ms=duration_ms,
            protocol_error=protocol_error,
            error_code=error_code,
        )
        if evaluate and result.get("rolled_back") is True:
            self.rollout_policy = self.codex_mcp_rollout.resolve_policy({})
        return result

    def record_protocol_error(self, error_code: str) -> None:
        """Record a real JSON-RPC/protocol failure without request content."""

        try:
            self._record_execution_metric(
                event_id=self._metric_event_id("protocol"),
                status="protocol_error",
                protocol_error=True,
                error_code=str(error_code or "jsonrpc_protocol_error"),
            )
        except Exception:
            # Metrics are fail-open for query execution; rollout mutations keep
            # their own mandatory audit transaction.
            return

    def tools(self) -> list[dict[str, Any]]:
        catalog = self.console_execution.tool_catalog(
            self.permissions,
            read_only_only=True,
            source_policy=self.source_policy,
        )
        tools: list[dict[str, Any]] = []
        planned_by_name: dict[str, list[dict[str, Any]]] = {}
        for call in self.plan_calls:
            planned_by_name.setdefault(str(call.get("tool_id") or ""), []).append(call)
        for item in catalog:
            name = str(item.get("id") or "").strip()
            if not name or name not in self.allowed_tools:
                continue
            planned = planned_by_name.get(name) or []
            schema = (
                _closed_schema(planned[0].get("arguments"))
                if len(planned) == 1
                else {"oneOf": [_closed_schema(call.get("arguments")) for call in planned]}
                if planned
                else {"type": "object", "properties": {}, "additionalProperties": False}
            )
            tools.append(
                {
                    "name": name,
                    "title": name.replace("_", " ").title(),
                    "description": str(item.get("description") or "Consulta read-only do JK Sistema")[:800],
                    "inputSchema": schema,
                    "annotations": {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": bool(item.get("external")),
                    },
                }
            )
        return tools

    def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        name = str(name or "").strip()
        args = dict(arguments or {}) if isinstance(arguments, dict) else {}
        client_id = str(self.context.get("client_id") or "").strip()
        if not client_id:
            self._critical_rollback("scope_violation")
            return self._tool_response(
                {"success": False, "tool_id": name, "error": "Contexto de tenant ausente.", "error_code": "data_selection_tenant_required"},
                is_error=True,
            )
        catalog_names = {item["name"] for item in self.tools()}
        if name not in catalog_names:
            self._critical_rollback("tool_violation")
            return self._tool_response(
                {"success": False, "tool_id": name, "error": "Ferramenta ausente ou nao autorizada."},
                is_error=True,
            )
        store = args.get("loja") or args.get("conta") or args.get("store")
        if store and (not self.store_scope_valid or _normalize_store(store) not in self.authorized_stores):
            self._critical_rollback("scope_violation")
            return self._tool_response(
                {"success": False, "tool_id": name, "error": "Loja fora do escopo autorizado desta conversa."},
                is_error=True,
            )
        if self.plan is None:
            return self._tool_response(
                {"success": False, "tool_id": name, "error_code": self.plan_error or "mcp_v2_plan_required", "error": "Plano MCP V2 assinado ausente ou invalido."},
                is_error=True,
            )
        completed_match = next(
            (
                call for call in self.plan_calls
                if call["index"] in self.completed_indexes
                and call["tool_id"] == name
                and call["arguments"] == args
            ),
            None,
        )
        if completed_match is not None:
            replay_key = _sha256(
                {
                    "plan_hash": self.plan["plan_hash"],
                    "index": completed_match["index"],
                    "arguments_hash": completed_match["arguments_hash"],
                }
            )
            replay = dict(self.call_cache.get(replay_key) or {})
            if replay:
                replay["idempotent_replay"] = True
                return self._tool_response(replay, is_error=replay.get("success") is not True)
        next_index = min((index for index in range(len(self.plan_calls)) if index not in self.completed_indexes), default=-1)
        if next_index < 0:
            return self._tool_response(
                {"success": False, "tool_id": name, "error_code": "mcp_plan_already_completed", "error": "Plano MCP ja concluido."},
                is_error=True,
            )
        planned = self.plan_calls[next_index]
        if name != planned["tool_id"] or args != planned["arguments"]:
            self._critical_rollback("plan_violation")
            return self._tool_response(
                {"success": False, "tool_id": name, "error_code": "mcp_plan_call_mismatch", "error": "Chamada diferente do plano assinado."},
                is_error=True,
            )
        if any(index not in self.successful_indexes for index in planned["depends_on"]):
            return self._tool_response(
                {"success": False, "tool_id": name, "error_code": "mcp_plan_dependency_pending", "error": "Dependencia do plano ainda nao concluida."},
                is_error=True,
            )
        policy_error = self.console_execution.source_policy_error(
            name,
            self.source_policy,
            self.previous_results,
        )
        if policy_error:
            self._critical_rollback("plan_violation")
            return self._tool_response(
                {"success": False, "tool_id": name, "error": str(policy_error), "error_code": "source_policy_blocked"},
                is_error=True,
            )
        execute_allowed, rollout_reason = self.codex_mcp_rollout.execution_decision(self.rollout_policy, tool_id=name)
        if rollout_reason == "mcp_shadow_no_execution":
            self.completed_indexes.add(next_index)
            self.successful_indexes.add(next_index)
            if self.rollout_store is not None:
                self._record_execution_metric(
                    event_id=self._metric_event_id("shadow", str(next_index)),
                    status="shadow_validated",
                    evaluate=False,
                )
            return self._tool_response(
                {"success": True, "tool_id": name, "status": "shadow_validated", "external_call_executed": False},
                is_error=False,
            )
        if not execute_allowed:
            return self._tool_response(
                {"success": False, "tool_id": name, "error_code": rollout_reason, "error": "MCP fora do rollout autorizado."},
                is_error=True,
            )
        call_key = _sha256({"plan_hash": self.plan["plan_hash"], "index": next_index, "arguments_hash": planned["arguments_hash"]})
        cached = self.call_cache.get(call_key)
        if cached is not None:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return self._tool_response(replay, is_error=replay.get("success") is not True)
        claim_token = ""
        if self.idempotency is not None:
            try:
                claim = self.idempotency.begin(
                    call_key=call_key,
                    plan_hash=self.plan["plan_hash"],
                    call_index=next_index,
                    tool_id=name,
                    arguments_hash=planned["arguments_hash"],
                )
            except RuntimeError:
                self._critical_rollback("idempotency_mismatch")
                return self._tool_response(
                    {"success": False, "tool_id": name, "error_code": "mcp_idempotency_mismatch", "retryable": False},
                    is_error=True,
                )
            if claim.get("state") != "claimed":
                replay = dict(claim.get("result") or {})
                replay.update({"tool_id": name, "idempotent_replay": True, "persisted_summary_only": True})
                if claim.get("state") == "running":
                    replay.update({"success": False, "error_code": "mcp_call_already_running", "retryable": True})
                else:
                    self.completed_indexes.add(next_index)
                if replay.get("success") is True:
                    self.successful_indexes.add(next_index)
                if self.rollout_store is not None:
                    try:
                        self._record_execution_metric(
                            event_id=self._metric_event_id("replay", str(next_index)),
                            status="idempotent_replay",
                            error_code=str(replay.get("error_code") or ""),
                        )
                    except Exception:
                        pass
                return self._tool_response(replay, is_error=replay.get("success") is not True)
            claim_token = str(claim.get("claim_token") or "")
            if not claim_token:
                self._critical_rollback("idempotency_mismatch")
                return self._tool_response(
                    {"success": False, "tool_id": name, "error_code": "mcp_idempotency_claim_missing", "retryable": False},
                    is_error=True,
                )
        tool_timeout_seconds = max(5, min(int(self.context.get("tool_timeout_seconds") or 60), 300))
        self.external_call_count += 1
        execution_started = time.perf_counter()
        try:
            result = self.assistant_execution.execute_tool_call(
                client_id=client_id,
                tool_id=name,
                args=dict(planned["arguments"]),
                screen_context=self.screen_context,
                previous_results=self.previous_results,
                permissions=self.permissions,
                audit_user=str(self.context.get("username") or "whatsapp"),
                query_deadline=time.monotonic() + tool_timeout_seconds,
            )
        except Exception:
            result = {
                "success": False,
                "tool_id": name,
                "error_code": "mcp_tool_execution_failed",
                "retryable": True,
            }
        result = result if isinstance(result, dict) else {"success": False, "tool_id": name, "error": "Resultado invalido."}
        self.call_cache[call_key] = result
        self.previous_results.append(result)
        self.completed_indexes.add(next_index)
        if result.get("success") is True:
            self.successful_indexes.add(next_index)
        if self.idempotency is not None:
            if not self.idempotency.finish(
                call_key=call_key,
                claim_token=claim_token,
                result=result,
            ):
                self._critical_rollback("idempotency_mismatch")
                result = {
                    "success": False,
                    "tool_id": name,
                    "error_code": "mcp_idempotency_lease_lost",
                    "retryable": False,
                }
                self.call_cache[call_key] = result
        if self.rollout_store is not None:
            try:
                self._record_execution_metric(
                    event_id=self._metric_event_id("call", str(next_index)),
                    status="completed" if result.get("success") is True else "failed",
                    duration_ms=(time.perf_counter() - execution_started) * 1000,
                    protocol_error=str(result.get("error_code") or "").startswith("mcp_protocol_"),
                    error_code=str(result.get("error_code") or ""),
                )
            except Exception:
                # Telemetry/automatic metrics are best effort.  Mandatory audit
                # remains enforced by the rollout policy store on mutations.
                pass
        return self._tool_response(result, is_error=result.get("success") is not True)

    @staticmethod
    def _tool_response(result: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
        serialized = _json_dump(result)
        if len(serialized) > MAX_RESULT_CHARS:
            preview = dict(result)
            preview.pop("data", None)
            preview.pop("rows", None)
            preview["result_truncated"] = True
            preview["result_characters"] = len(serialized)
            serialized = _json_dump(preview)[:MAX_RESULT_CHARS]
        return {
            "content": [{"type": "text", "text": serialized}],
            "isError": bool(is_error),
        }


def _read_message() -> dict[str, Any] | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    if line.lower().startswith(b"content-length:"):
        try:
            length = int(line.split(b":", 1)[1].strip())
        except Exception as exc:
            raise RuntimeError("invalid_content_length") from exc
        while True:
            header = sys.stdin.buffer.readline()
            if header in {b"\r\n", b"\n", b""}:
                break
        raw = sys.stdin.buffer.read(length)
    else:
        raw = line.strip()
    if not raw:
        return {}
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _write_message(value: dict[str, Any]) -> None:
    sys.stdout.write(_json_dump(value) + "\n")
    sys.stdout.flush()


def _response(request_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def _dispatch_message(server: JKCodexMCP, message: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and dispatch one JSON-RPC message while recording real errors."""

    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str) or not message.get("method"):
        server.record_protocol_error("jsonrpc_invalid_request")
        return (
            _response(request_id, error={"code": -32600, "message": "Invalid Request"})
            if request_id is not None
            else None
        )
    method = str(message["method"])
    if "params" in message and not isinstance(message.get("params"), dict):
        server.record_protocol_error("jsonrpc_invalid_params")
        return (
            _response(request_id, error={"code": -32602, "message": "Invalid params"})
            if request_id is not None
            else None
        )
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if request_id is None:
        if method in {"notifications/initialized", "notifications/cancelled", "notifications/progress"}:
            return None
        server.record_protocol_error("jsonrpc_request_id_required")
        return None
    try:
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "2025-06-18")
            result = {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": server.tools()}
        elif method == "tools/call":
            result = server.call_tool(str(params.get("name") or ""), params.get("arguments"))
        else:
            server.record_protocol_error("jsonrpc_method_not_found")
            return _response(request_id, error={"code": -32601, "message": "Method not found"})
        return _response(request_id, result=result)
    except Exception:
        server.record_protocol_error("jsonrpc_internal_error")
        return _response(request_id, error={"code": -32603, "message": "Internal error"})


def main() -> int:
    try:
        server = JKCodexMCP(_decode_context())
    except Exception as exc:
        print(f"JK MCP startup failed: {exc}", file=sys.stderr, flush=True)
        return 2
    while True:
        try:
            message = _read_message()
        except Exception:
            server.record_protocol_error("jsonrpc_parse_error")
            _write_message(_response(None, error={"code": -32700, "message": "Parse error"}))
            continue
        if message is None:
            return 0
        response = _dispatch_message(server, message)
        if response is not None:
            _write_message(response)


if __name__ == "__main__":
    raise SystemExit(main())
