"""Low-level, secret-free agent performance logging."""

from __future__ import annotations

import logging
import re
from typing import Any


LOGGER = logging.getLogger("jk_sistema")


def metadata(client_id: str, store: str, agent_input: dict | None) -> dict[str, str]:
    data = agent_input if isinstance(agent_input, dict) else {}
    question = data.get("question") if isinstance(data.get("question"), dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    return {
        "tenant": str(client_id or data.get("tenant_id") or "").strip()[:80],
        "loja": str(store or data.get("store") or data.get("loja") or "").strip()[:160],
        "pergunta": str(question.get("id") or question.get("question_id") or data.get("question_id") or "").strip()[:80],
        "item": str(item.get("id") or question.get("item_id") or context.get("item_id") or data.get("item_id") or "").strip()[:80],
        "sku": str(item.get("seller_sku") or item.get("sku") or context.get("sku") or context.get("seller_sku") or "").strip()[:120],
    }


def record(client_id: str, store: str, agent_input: dict | None, stage: str, elapsed: float, **details: Any) -> None:
    meta = metadata(client_id, store, agent_input)
    parts: list[str] = []
    for key, value in details.items():
        if value is None:
            continue
        text = "true" if value is True else "false" if value is False else f"{value:.3f}" if isinstance(value, float) else str(value)
        text = re.sub(r"\s+", " ", text).strip()[:220]
        if text:
            parts.append(f"{key}={text}")
    suffix = f" {' '.join(parts)}" if parts else ""
    LOGGER.info(
        "[ML PERGUNTAS PERF] tenant=%s loja=%s pergunta=%s item=%s sku=%s etapa=%s tempo=%.3fs%s",
        meta["tenant"] or "-", meta["loja"] or "-", meta["pergunta"] or "-",
        meta["item"] or "-", meta["sku"] or "-", str(stage or "-"), float(elapsed or 0), suffix,
    )


__all__ = ["metadata", "record"]
