"""Low-level, secret-free agent performance logging."""

from __future__ import annotations

import logging
from typing import Any


LOGGER = logging.getLogger("jk_sistema")
_ALLOWED_STAGES = frozenset({
    "bling",
    "busca_web",
    "cadastro_interno",
    "commercial_generation",
    "ferramenta",
    "ferramentas_total",
    "memoria_sku",
    "mercado_livre",
    "total",
    "v3_orquestrador_codex",
})


def metadata(client_id: str, store: str, agent_input: dict | None) -> dict[str, str]:
    """Return only aggregate method metadata; identifiers are intentionally discarded."""

    del client_id, store
    data = agent_input if isinstance(agent_input, dict) else {}
    profile = data.get("seller_behavior_profile") if isinstance(data.get("seller_behavior_profile"), dict) else {}
    method_version = str(profile.get("method_version") or "seller-conversion-v1")
    try:
        profile_version = str(int(profile.get("profile_version") or 0))
    except (TypeError, ValueError):
        profile_version = "0"
    profile_scope = str(profile.get("profile_scope") or "none").strip().lower()
    return {
        "method_version": method_version if method_version == "seller-conversion-v1" else "unknown",
        "profile_version": profile_version if profile_version in {"0", "2"} else "0",
        "profile_active": "true" if profile.get("profile_active") is True else "false",
        "profile_scope": profile_scope if profile_scope in {"global", "store", "sku", "none"} else "none",
    }


def record(client_id: str, store: str, agent_input: dict | None, stage: str, elapsed: float, **details: Any) -> None:
    meta = metadata(client_id, store, agent_input)
    allowed_detail_keys = {
        "status",
        "commercial_state",
        "alternative_used",
        "research_attempted",
        "fallback_used",
    }
    parts: list[str] = []
    for key, value in details.items():
        if key not in allowed_detail_keys:
            continue
        if value is None:
            continue
        if key in {"alternative_used", "research_attempted", "fallback_used"}:
            text = "true" if value is True else "false"
        elif key == "commercial_state":
            normalized = str(value).strip().lower()
            text = normalized if normalized in {
                "fits", "variant", "partial", "insufficient", "incompatible", "not_applicable", "unclassified",
            } else "unclassified"
        else:
            normalized = str(value).strip().lower()
            text = normalized if normalized in {
                "ok", "erro", "revisao", "timeout", "vazio", "sem_consulta", "sem_ferramentas_permitidas",
            } else "other"
        if text:
            parts.append(f"{key}={text}")
    suffix = f" {' '.join(parts)}" if parts else ""
    normalized_stage = str(stage or "").strip().lower()
    safe_stage = normalized_stage if normalized_stage in _ALLOWED_STAGES else "other"
    LOGGER.info(
        "[ML PERGUNTAS PERF] metodo=%s perfil_versao=%s perfil_ativo=%s perfil_escopo=%s etapa=%s tempo=%.3fs%s",
        meta["method_version"], meta["profile_version"], meta["profile_active"],
        meta["profile_scope"], safe_stage, float(elapsed or 0), suffix,
    )


__all__ = ["metadata", "record"]
