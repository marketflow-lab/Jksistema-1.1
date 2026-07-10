"""Small operational memory store for the internal Codex assistant."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.services.runtime_bridge import bind_runtime_globals


MEMORY_LOCK = threading.RLock()
MEMORY_CATEGORIES = {
    "lojas_usadas",
    "skus_frequentes",
    "relatorios_anteriores",
    "decisoes",
    "regras_comerciais",
    "custos_pendentes",
    "alertas_ignorados",
    "preferencias_resposta",
    "propostas",
}
SECRET_KEY_RE = re.compile(r"(token|secret|senha|password|cookie|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)", re.I)
SKU_RE = re.compile(r"\bsku[:\s#-]*([a-zA-Z0-9._/-]{2,})\b", re.I)
COMMON_LOJAS = ("JK Pecas", "JK Peças", "Uai Mineirinho", "Carlos Jose", "Carlos José", "Deckas")


def configure_codex_operational_memory_runtime(runtime_module=None):
    return bind_runtime_globals(globals(), runtime_module)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_dir() -> str:
    base = str(globals().get("BASE_DIR") or os.getcwd()).strip()
    return os.path.abspath(base or os.getcwd())


def _info_base() -> Path:
    base_info = str(globals().get("PASTA_INFO") or os.path.join(_base_dir(), "info")).strip()
    path = Path(base_info)
    if not path.is_absolute():
        path = Path(_base_dir()) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_id(value: Any, fallback: str = "default") -> str:
    safe = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})[:80]
    return safe or fallback


def _memory_dir(client_id: str) -> Path:
    path = _info_base() / _safe_id(client_id) / "codex_assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_path(client_id: str) -> Path:
    return _memory_dir(client_id) / "operational_memory.json"


def _read(client_id: str) -> dict[str, Any]:
    path = _memory_path(client_id)
    try:
        if not path.exists():
            return {"version": 1, "entries": [], "updated_at": ""}
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("entries", [])
            return data
    except Exception:
        pass
    return {"version": 1, "entries": [], "updated_at": ""}


def _write(client_id: str, payload: dict[str, Any]) -> None:
    path = _memory_path(client_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    temp.replace(path)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[limite]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key or "")
            if SECRET_KEY_RE.search(key_text):
                out[key_text] = "[redigido]"
            else:
                out[key_text] = _redact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:80]]
    if isinstance(value, str):
        text = value
        text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [redigido]", text, flags=re.I)
        text = re.sub(r"(?i)(access_token|refresh_token|token|cookie|authorization)\s*[:=]\s*[^,\s;]+", r"\1=[redigido]", text)
        return text[:2400]
    return value


def _entry(
    *,
    category: str,
    content: str,
    source: str,
    metadata: Optional[dict[str, Any]] = None,
    importance: int = 3,
    entry_id: str = "",
) -> dict[str, Any]:
    category = str(category or "").strip() or "decisoes"
    if category not in MEMORY_CATEGORIES:
        category = "decisoes"
    try:
        importance_safe = max(1, min(int(importance), 5))
    except Exception:
        importance_safe = 3
    return {
        "entry_id": _safe_id(entry_id or uuid.uuid4().hex, uuid.uuid4().hex),
        "category": category,
        "content": str(content or "").strip()[:2400],
        "source": str(source or "manual").strip()[:80],
        "metadata": _redact(metadata or {}),
        "importance": importance_safe,
        "created_at": _now(),
        "updated_at": _now(),
    }


def add_memory(
    client_id: str,
    *,
    category: str,
    content: str,
    source: str = "manual",
    metadata: Optional[dict[str, Any]] = None,
    importance: int = 3,
    entry_id: str = "",
) -> dict[str, Any]:
    content = str(content or "").strip()
    if not content:
        return {"success": False, "message": "Conteudo vazio.", "entry": None}
    with MEMORY_LOCK:
        payload = _read(client_id)
        entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
        new_entry = _entry(
            category=category,
            content=content,
            source=source,
            metadata=metadata,
            importance=importance,
            entry_id=entry_id,
        )
        existing_idx = next((idx for idx, item in enumerate(entries) if str(item.get("entry_id") or "") == new_entry["entry_id"]), -1)
        if existing_idx >= 0:
            created_at = entries[existing_idx].get("created_at") or new_entry["created_at"]
            new_entry["created_at"] = created_at
            entries[existing_idx] = new_entry
        else:
            entries.append(new_entry)
        entries = sorted(entries, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)[:500]
        payload["entries"] = entries
        _write(client_id, payload)
        return {"success": True, "entry": new_entry, "total": len(entries)}


def delete_memory(client_id: str, entry_id: str) -> dict[str, Any]:
    with MEMORY_LOCK:
        payload = _read(client_id)
        before = len(payload.get("entries") or [])
        payload["entries"] = [
            item for item in payload.get("entries") or []
            if isinstance(item, dict) and str(item.get("entry_id") or "") != str(entry_id or "")
        ]
        _write(client_id, payload)
        return {"success": True, "deleted": before - len(payload["entries"]), "entry_id": entry_id}


def list_memory(client_id: str, category: str = "", limit: int = 100) -> dict[str, Any]:
    try:
        limit_safe = max(1, min(int(limit or 100), 500))
    except Exception:
        limit_safe = 100
    payload = _read(client_id)
    category = str(category or "").strip()
    entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
    if category:
        entries = [item for item in entries if str(item.get("category") or "") == category]
    return {
        "success": True,
        "client_id": str(client_id or "default"),
        "entries": entries[:limit_safe],
        "categories": sorted(MEMORY_CATEGORIES),
        "total": len(entries),
        "updated_at": payload.get("updated_at") or "",
    }


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def query_memory(client_id: str, message: str = "", category: str = "", limit: int = 20) -> dict[str, Any]:
    try:
        limit_safe = max(1, min(int(limit or 20), 100))
    except Exception:
        limit_safe = 20
    payload = _read(client_id)
    entries = [item for item in payload.get("entries") or [] if isinstance(item, dict)]
    category = str(category or "").strip()
    if category:
        entries = [item for item in entries if str(item.get("category") or "") == category]
    query = _norm(message)
    terms = [term for term in re.split(r"[^a-z0-9]+", query) if len(term) >= 3][:20]
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in entries:
        haystack = _norm(json.dumps(item, ensure_ascii=False, default=str))
        score = int(item.get("importance") or 1)
        for term in terms:
            if term in haystack:
                score += 4
        if not terms or score > int(item.get("importance") or 1):
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("updated_at") or pair[1].get("created_at") or "")), reverse=True)
    records = [item for _, item in scored[:limit_safe]]
    return {
        "success": True,
        "tool_id": "operational_memory_query",
        "records": len(records),
        "rows": records,
        "summary": {
            "message": str(message or "")[:300],
            "category": category,
            "total_memory_entries": len(entries),
        },
        "sources": ["operational_memory"],
        "warnings": [],
        "generated_at": _now(),
    }


def compact_context(client_id: str, message: str = "", limit_chars: int = 6000) -> dict[str, Any]:
    results = query_memory(client_id, message=message, limit=40)
    lines: list[str] = []
    for item in results.get("rows") or []:
        category = str(item.get("category") or "memoria")
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"- {category}: {content}")
        if sum(len(line) for line in lines) > limit_chars:
            break
    return {
        "success": True,
        "summary": "\n".join(lines)[:limit_chars],
        "record_count": int(results.get("records") or 0),
        "sources": ["operational_memory"],
    }


def remember_from_interaction(
    client_id: str,
    *,
    prompt: str,
    final_answer: str = "",
    trace: Optional[dict[str, Any]] = None,
    task: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    prompt_text = str(prompt or "")
    answer_text = str(final_answer or "")
    text = f"{prompt_text}\n{answer_text}"
    added: list[dict[str, Any]] = []
    for match in SKU_RE.finditer(text):
        sku = match.group(1).strip()
        if sku:
            res = add_memory(
                client_id,
                category="skus_frequentes",
                content=f"SKU citado em conversa do Black Jhon: {sku}",
                source="agent_interaction",
                metadata={"sku": sku, "task_id": (task or {}).get("task_id")},
                importance=2,
                entry_id=f"sku_{_safe_id(sku)}",
            )
            if res.get("success"):
                added.append(res["entry"])
    text_norm = _norm(text)
    for loja in COMMON_LOJAS:
        if _norm(loja) in text_norm:
            res = add_memory(
                client_id,
                category="lojas_usadas",
                content=f"Loja/conta recorrente nas conversas: {loja}",
                source="agent_interaction",
                metadata={"loja": loja, "task_id": (task or {}).get("task_id")},
                importance=2,
                entry_id=f"loja_{_safe_id(loja)}",
            )
            if res.get("success"):
                added.append(res["entry"])
    if re.search(r"\b(relatorio|relatório|analise completa|análise completa)\b", _norm(prompt_text)):
        res = add_memory(
            client_id,
            category="relatorios_anteriores",
            content=f"Relatorio solicitado: {prompt_text[:300]}",
            source="agent_interaction",
            metadata={
                "task_id": (task or {}).get("task_id"),
                "sources": list((trace or {}).get("sources") or [])[:20],
                "warnings": list((trace or {}).get("warnings") or [])[:10],
            },
            importance=3,
        )
        if res.get("success"):
            added.append(res["entry"])
    return {"success": True, "added": added, "added_count": len(added)}


configure_codex_operational_memory_runtime()


__all__ = [
    "MEMORY_CATEGORIES",
    "configure_codex_operational_memory_runtime",
    "add_memory",
    "delete_memory",
    "list_memory",
    "query_memory",
    "compact_context",
    "remember_from_interaction",
]
