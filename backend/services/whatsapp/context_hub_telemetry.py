"""Sanitized observability for WhatsApp Context Hub retrievals."""

from __future__ import annotations

import hashlib
import re
from typing import Any


_INTERNAL_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")


def _hashed(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "ignore")).hexdigest()


def safe_internal_client_id(value: object) -> str:
    """Return only a server-derived tenant key suitable for internal filtering."""

    client_id = str(value or "").strip()
    return client_id if _INTERNAL_CLIENT_ID_RE.fullmatch(client_id) else ""


def _safe_document(row: dict[str, Any]) -> dict[str, str]:
    doc_id = str(row.get("doc_id") or "").strip()
    chunk_id = str(row.get("chunk_id") or "").strip()
    source_hash = str(row.get("source_hash") or "").strip().lower()
    safe: dict[str, str] = {}
    if doc_id:
        safe["doc_id"] = (
            doc_id[:240]
            if re.fullmatch(r"jk:[A-Za-z0-9:_./-]{1,236}", doc_id)
            else f"sha256:{_hashed(doc_id)}"
        )
    if chunk_id:
        safe["chunk_id_hash"] = _hashed(chunk_id)
    if source_hash:
        safe["source_hash"] = (
            source_hash[:128]
            if re.fullmatch(r"[a-f0-9]{32,128}", source_hash)
            else _hashed(source_hash)
        )
    return safe


def context_hub_diagnostic_summary(
    pending: dict[str, Any],
    plan: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    hub_call = next((item for item in list(plan.get("tool_calls") or [])
                     if isinstance(item, dict) and item.get("tool_id") == "context_hub_search"), None)
    hub_result = next((item for item in list(evidence.get("tool_results") or [])
                       if isinstance(item, dict) and item.get("tool_id") == "context_hub_search"), None)
    if not hub_call and not hub_result:
        return {}
    arguments = hub_call.get("arguments") if isinstance(hub_call, dict) and isinstance(hub_call.get("arguments"), dict) else {}
    query = str(arguments.get("query") or arguments.get("message") or pending.get("request_text") or "").strip()
    result = hub_result if isinstance(hub_result, dict) else {}
    generation = result.get("context_hub_generation") if isinstance(result.get("context_hub_generation"), dict) else {}
    if not generation and isinstance(result.get("summary"), dict):
        generation = result["summary"]
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    documents = [safe for safe in (_safe_document(row) for row in rows[:12] if isinstance(row, dict)) if safe]
    guard = plan.get("manager_guard") if isinstance(plan.get("manager_guard"), dict) else {}
    generation_id = str(generation.get("generation_id") or result.get("generation_id") or "").strip()
    source_version = str(generation.get("source_version") or result.get("source_version") or "").strip()
    return {
        "query_hash": _hashed(query) if query else "",
        "intent": str(guard.get("context_hub_reason") or "knowledge")[:80],
        "generation_id": generation_id[:160] if re.fullmatch(r"[A-Za-z0-9._-]{1,160}", generation_id) else "",
        "source_version": source_version[:120] if re.fullmatch(r"[A-Za-z0-9._+-]{1,120}", source_version) else "",
        "result_count": max(0, min(12, int(result.get("records") or len(rows) or 0))),
        "documents": documents,
        "latency_ms": max(0, int(pending.get("manager_tools_duration_ms") or 0)),
        "status": "ok" if result.get("success") is True and rows else "empty_or_failed",
    }
