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
from pathlib import Path
from typing import Any


SERVER_NAME = "jk-system-readonly"
SERVER_VERSION = "2026.07.14"
MAX_RESULT_CHARS = 180_000
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


class JKCodexMCP:
    def __init__(self, context: dict[str, Any]) -> None:
        # Importing backend_api binds the extracted service modules to the same
        # runtime configuration and data paths used by the desktop backend.
        import backend_api  # noqa: F401
        from backend.services import codex_assistant, codex_console

        codex_assistant.configure_codex_assistant_runtime(backend_api)
        self.codex_assistant = codex_assistant
        self.codex_console = codex_console
        self.context = context
        self.permissions = context.get("permissions") if isinstance(context.get("permissions"), dict) else {}
        self.screen_context = context.get("screen_context") if isinstance(context.get("screen_context"), dict) else {}
        self.source_policy = context.get("source_policy") if isinstance(context.get("source_policy"), dict) else {}
        self.authorized_stores = {
            _normalize_store(item)
            for item in (context.get("authorized_stores") or [])
            if _normalize_store(item)
        }
        self.previous_results: list[dict[str, Any]] = []
        self.call_cache: dict[str, dict[str, Any]] = {}
        self.result_path = Path(str(context.get("result_path") or "")).resolve() if context.get("result_path") else None

    def tools(self) -> list[dict[str, Any]]:
        catalog = self.codex_console._codex_agent_tool_catalog(
            self.permissions,
            read_only_only=True,
            source_policy=self.source_policy,
        )
        tools: list[dict[str, Any]] = []
        for item in catalog:
            name = str(item.get("id") or "").strip()
            if not name:
                continue
            schema = item.get("input_schema") if isinstance(item.get("input_schema"), dict) else {}
            tools.append(
                {
                    "name": name,
                    "title": name.replace("_", " ").title(),
                    "description": str(item.get("description") or "Consulta read-only do JK Sistema")[:800],
                    "inputSchema": schema or {"type": "object", "additionalProperties": True},
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
        catalog_names = {item["name"] for item in self.tools()}
        if name not in catalog_names:
            return self._tool_response(
                {"success": False, "tool_id": name, "error": "Ferramenta ausente ou nao autorizada."},
                is_error=True,
            )
        store = args.get("loja") or args.get("conta") or args.get("store")
        if store and self.authorized_stores and _normalize_store(store) not in self.authorized_stores:
            return self._tool_response(
                {"success": False, "tool_id": name, "error": "Loja fora do escopo autorizado desta conversa."},
                is_error=True,
            )
        policy_error = self.codex_console._codex_agent_source_policy_error(
            name,
            self.source_policy,
            self.previous_results,
        )
        if policy_error:
            return self._tool_response(
                {"success": False, "tool_id": name, "error": str(policy_error), "error_code": "source_policy_blocked"},
                is_error=True,
            )
        call_key = hashlib.sha256(_json_dump({"name": name, "arguments": args}).encode("utf-8")).hexdigest()
        cached = self.call_cache.get(call_key)
        if cached is not None:
            replay = dict(cached)
            replay["idempotent_replay"] = True
            return self._tool_response(replay, is_error=replay.get("success") is not True)
        deadline_at = int(self.context.get("deadline_at_epoch") or 0)
        remaining = max(1, deadline_at - int(time.time())) if deadline_at else 60
        result = self.codex_assistant.codex_assistant_execute_tool_call(
            client_id=str(self.context.get("client_id") or "default"),
            tool_id=name,
            args=args,
            screen_context=self.screen_context,
            previous_results=self.previous_results,
            permissions=self.permissions,
            audit_user=str(self.context.get("username") or "whatsapp"),
            query_deadline=time.monotonic() + min(remaining, 300),
        )
        result = result if isinstance(result, dict) else {"success": False, "tool_id": name, "error": "Resultado invalido."}
        self.call_cache[call_key] = result
        self.previous_results.append(result)
        self._record_result(call_key, result)
        return self._tool_response(result, is_error=result.get("success") is not True)

    def _record_result(self, call_hash: str, result: dict[str, Any]) -> None:
        if self.result_path is None:
            return
        try:
            self.result_path.parent.mkdir(parents=True, exist_ok=True)
            with self.result_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    _json_dump(
                        {
                            "task_id": str(self.context.get("task_id") or ""),
                            "call_hash": call_hash,
                            "tool_id": str(result.get("tool_id") or ""),
                            "result": result,
                        }
                    )
                    + "\n"
                )
        except Exception as exc:
            print(f"JK MCP result audit failed: {exc}", file=sys.stderr, flush=True)

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


def main() -> int:
    try:
        server = JKCodexMCP(_decode_context())
    except Exception as exc:
        print(f"JK MCP startup failed: {exc}", file=sys.stderr, flush=True)
        return 2
    while True:
        try:
            message = _read_message()
        except Exception as exc:
            print(f"JK MCP invalid request: {exc}", file=sys.stderr, flush=True)
            continue
        if message is None:
            return 0
        method = str(message.get("method") or "")
        request_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if request_id is None:
            continue
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
                _write_message(_response(request_id, error={"code": -32601, "message": "Method not found"}))
                continue
            _write_message(_response(request_id, result=result))
        except Exception as exc:
            _write_message(_response(request_id, error={"code": -32000, "message": str(exc)[:500]}))


if __name__ == "__main__":
    raise SystemExit(main())
