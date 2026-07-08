"""Local usage and room storage for Sala de Reuniao."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional

from backend.schemas import SalaReuniaoUsoAdicionarRequest
from backend.services import sala_reuniao_context as sala_context

SALA_REUNIAO_FREE_PARTICIPANT_MINUTES = 10000
SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES = 9950


def _sala_reuniao_uso_path(client_id: str) -> str:
    return os.path.join(sala_context.get_tenant_path(client_id), "sala_reuniao_uso.json")

def _sala_reuniao_mes_atual() -> str:
    return datetime.now().strftime("%Y-%m")

def _sala_reuniao_proximo_reset(month_key: Optional[str] = None) -> str:
    try:
        ano, mes = [int(parte) for parte in str(month_key or _sala_reuniao_mes_atual()).split("-", 1)]
        if mes == 12:
            ano += 1
            mes = 1
        else:
            mes += 1
        return datetime(ano, mes, 1).date().isoformat()
    except Exception:
        hoje = datetime.now()
        proximo = datetime(hoje.year + (1 if hoje.month == 12 else 0), 1 if hoje.month == 12 else hoje.month + 1, 1)
        return proximo.date().isoformat()

def _sala_reuniao_carregar_uso(client_id: str) -> dict[str, Any]:
    path = _sala_reuniao_uso_path(client_id)
    if not os.path.exists(path):
        return {"months": {}}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            months = payload.get("months")
            if isinstance(months, dict):
                return payload
    except Exception:
        sala_context.logger.exception("Erro ao carregar uso mensal da sala de reuniao")
    return {"months": {}}

def _sala_reuniao_salvar_uso(client_id: str, payload: dict[str, Any]) -> None:
    path = _sala_reuniao_uso_path(client_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

def _sala_reuniao_resumo_uso(client_id: str) -> dict[str, Any]:
    month_key = _sala_reuniao_mes_atual()
    payload = _sala_reuniao_carregar_uso(client_id)
    months = payload.setdefault("months", {})
    month_data = months.setdefault(month_key, {"participant_seconds": 0.0, "events": []})
    seconds = max(0.0, float(month_data.get("participant_seconds") or 0))
    minutes = seconds / 60.0
    limit = float(SALA_REUNIAO_FREE_PARTICIPANT_MINUTES)
    block_limit = float(SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES)
    return {
        "month": month_key,
        "participant_seconds": round(seconds, 3),
        "participant_minutes": round(minutes, 3),
        "free_participant_minutes": SALA_REUNIAO_FREE_PARTICIPANT_MINUTES,
        "block_participant_minutes": SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES,
        "remaining_participant_minutes": round(max(0.0, limit - minutes), 3),
        "remaining_until_block_minutes": round(max(0.0, block_limit - minutes), 3),
        "percent_used": round(min(100.0, (minutes / limit) * 100.0), 2) if limit else 0,
        "blocked": bool(block_limit and minutes >= block_limit),
        "resets_at": _sala_reuniao_proximo_reset(month_key),
        "updated_at": month_data.get("updated_at"),
        "events": month_data.get("events") or [],
    }

def _sala_reuniao_adicionar_uso(client_id: str, req: SalaReuniaoUsoAdicionarRequest) -> dict[str, Any]:
    seconds = max(0.0, float(req.participant_seconds or 0))
    seconds = min(seconds, 60 * 60 * 24 * 500)
    payload = _sala_reuniao_carregar_uso(client_id)
    months = payload.setdefault("months", {})
    month_key = _sala_reuniao_mes_atual()
    month_data = months.setdefault(month_key, {"participant_seconds": 0.0, "events": []})
    month_data["participant_seconds"] = max(0.0, float(month_data.get("participant_seconds") or 0)) + seconds
    month_data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    events = month_data.setdefault("events", [])
    if not isinstance(events, list):
        events = []
    if seconds > 0:
        events.append({
            "created_at": month_data["updated_at"],
            "room_name": (req.room_name or "")[:160],
            "room_url": (req.room_url or "")[:300],
            "participant_seconds": round(seconds, 3),
            "participant_minutes": round(seconds / 60.0, 3),
            "participant_count": req.participant_count,
            "reason": (req.reason or "")[:80],
        })
        month_data["events"] = events[-60:]
    payload["updated_at"] = month_data["updated_at"]
    _sala_reuniao_salvar_uso(client_id, payload)
    return _sala_reuniao_resumo_uso(client_id)

def _sala_reuniao_salas_path(client_id: str) -> str:
    return os.path.join(sala_context.get_tenant_path(client_id), "sala_reuniao_salas.json")

def _sala_reuniao_carregar_salas(client_id: str) -> dict[str, Any]:
    path = _sala_reuniao_salas_path(client_id)
    if not os.path.exists(path):
        return {"rooms": []}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict) and isinstance(payload.get("rooms"), list):
            return payload
    except Exception:
        sala_context.logger.exception("Erro ao carregar salas de reuniao")
    return {"rooms": []}

def _sala_reuniao_salvar_salas(client_id: str, payload: dict[str, Any]) -> None:
    path = _sala_reuniao_salas_path(client_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

__all__ = ['SALA_REUNIAO_FREE_PARTICIPANT_MINUTES', 'SALA_REUNIAO_BLOCK_PARTICIPANT_MINUTES', '_sala_reuniao_uso_path', '_sala_reuniao_mes_atual', '_sala_reuniao_proximo_reset', '_sala_reuniao_carregar_uso', '_sala_reuniao_salvar_uso', '_sala_reuniao_resumo_uso', '_sala_reuniao_adicionar_uso', '_sala_reuniao_salas_path', '_sala_reuniao_carregar_salas', '_sala_reuniao_salvar_salas']
