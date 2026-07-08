"""Auto-renew scheduling persistence for Renovacao."""

from __future__ import annotations

import datetime as dt
import json
import os

from fastapi import HTTPException

from backend.schemas.renovacao import RenovacaoAgendamentoRequest
from backend.services import renovacao_context as ctx
from backend.services.renovacao_promocoes_helpers import _renovacao_normalizar_nome

def _renovacao_remover_agendamento_campanha(client_id: str, loja: str, campanha_id: str) -> None:
    with ctx.RENOVACAO_AGENDAMENTO_LOCK:
        payload = _renovacao_agendamentos_carregar(client_id)
        chave = _renovacao_agendamento_key(loja, campanha_id)
        payload["agendamentos"] = [
            _renovacao_agendamento_sanitizar(entry)
            for entry in (payload.get("agendamentos") or [])
            if _renovacao_agendamento_key(entry.get("loja"), entry.get("campanha_id")) != chave
        ]
        _renovacao_agendamentos_salvar(client_id, payload)


def _renovacao_agendamento_path(client_id: str) -> str:
    return os.path.join(ctx.get_tenant_path(client_id), "renovacao_agendamentos.json")


def _renovacao_agendamento_key(loja: str, campanha_id: str) -> str:
    return f"{_renovacao_normalizar_nome(loja)}::{str(campanha_id or '').strip()}"


def _renovacao_agendamentos_carregar(client_id: str) -> dict:
    caminho = _renovacao_agendamento_path(client_id)
    if not os.path.exists(caminho):
        return {"agendamentos": []}
    try:
        with open(caminho, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh) or {}
        if isinstance(payload, dict):
            payload.setdefault("agendamentos", [])
            return payload
    except Exception as exc:
        ctx.logger.warning("[RENOVACAO AGENDAMENTO] Falha ao ler preferencias de %s: %s", client_id, exc)
    return {"agendamentos": []}


def _renovacao_agendamentos_salvar(client_id: str, payload: dict) -> None:
    caminho = _renovacao_agendamento_path(client_id)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(payload or {"agendamentos": []}, fh, ensure_ascii=False, indent=2)


def _renovacao_agendamento_sanitizar(entry: dict) -> dict:
    entry = dict(entry or {})
    return {
        "enabled": bool(entry.get("enabled")),
        "loja": str(entry.get("loja") or "").strip(),
        "campanha_id": str(entry.get("campanha_id") or "").strip(),
        "nome": str(entry.get("nome") or "").strip(),
        "promotion_type": str(entry.get("promotion_type") or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN",
        "updated_at": entry.get("updated_at"),
        "last_run_key": entry.get("last_run_key"),
        "last_result": entry.get("last_result") if isinstance(entry.get("last_result"), dict) else None,
        "last_error": str(entry.get("last_error") or "").strip(),
    }


def _renovacao_agendamento_upsert_lista(
    agendamentos: list[dict],
    *,
    loja: str,
    campanha_id: str,
    enabled: bool,
    nome: str = "",
    promotion_type: str = "SELLER_CAMPAIGN",
    updated_at: str | None = None,
    last_run_key: str | None = None,
    last_result: dict | None = None,
    last_error: str = "",
) -> list[dict]:
    loja = str(loja or "").strip()
    campanha_id = str(campanha_id or "").strip()
    chave = _renovacao_agendamento_key(loja, campanha_id)
    restantes = []
    atual = {}
    for entry in agendamentos or []:
        item = _renovacao_agendamento_sanitizar(entry)
        if _renovacao_agendamento_key(item.get("loja"), item.get("campanha_id")) == chave:
            atual = item
        else:
            restantes.append(item)

    atual.update({
        "enabled": bool(enabled),
        "loja": loja,
        "campanha_id": campanha_id,
        "nome": str(nome or atual.get("nome") or "").strip(),
        "promotion_type": str(promotion_type or atual.get("promotion_type") or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN",
        "updated_at": updated_at or dt.datetime.now().isoformat(timespec="seconds"),
        "last_error": str(last_error or "").strip(),
    })
    if last_run_key is not None:
        atual["last_run_key"] = last_run_key
    if last_result is not None:
        atual["last_result"] = last_result

    restantes.append(_renovacao_agendamento_sanitizar(atual))
    return restantes


def _renovacao_agendamento_atual(client_id: str, loja: str, campanha_id: str) -> dict:
    chave = _renovacao_agendamento_key(loja, campanha_id)
    payload = _renovacao_agendamentos_carregar(client_id)
    for entry in payload.get("agendamentos") or []:
        if _renovacao_agendamento_key(entry.get("loja"), entry.get("campanha_id")) == chave:
            return _renovacao_agendamento_sanitizar(entry)
    return {
        "enabled": False,
        "loja": str(loja or "").strip(),
        "campanha_id": str(campanha_id or "").strip(),
        "nome": "",
        "promotion_type": "SELLER_CAMPAIGN",
        "updated_at": None,
        "last_run_key": None,
        "last_result": None,
        "last_error": "",
    }


def _renovacao_agendamento_put_payload(client_id: str, req: RenovacaoAgendamentoRequest):
    loja = str(req.loja or "").strip()
    campanha_id = str(req.campanha_id or "").strip()
    if not loja or not campanha_id:
        raise HTTPException(status_code=400, detail="Selecione a loja e a campanha para agendar.")

    with ctx.RENOVACAO_AGENDAMENTO_LOCK:
        payload = _renovacao_agendamentos_carregar(client_id)
        agendamentos = [
            _renovacao_agendamento_sanitizar(entry)
            for entry in (payload.get("agendamentos") or [])
            if isinstance(entry, dict)
        ]
        chave = _renovacao_agendamento_key(loja, campanha_id)
        atual = None
        restantes = []
        for entry in agendamentos:
            if _renovacao_agendamento_key(entry.get("loja"), entry.get("campanha_id")) == chave:
                atual = entry
            else:
                restantes.append(entry)

        payload["agendamentos"] = _renovacao_agendamento_upsert_lista(
            restantes + ([atual] if atual else []),
            loja=loja,
            campanha_id=campanha_id,
            enabled=bool(req.enabled),
            nome=str(req.nome or "").strip(),
            promotion_type=str(req.promotion_type or "SELLER_CAMPAIGN").strip() or "SELLER_CAMPAIGN",
            last_error="" if req.enabled else str((atual or {}).get("last_error") or ""),
        )
        _renovacao_agendamentos_salvar(client_id, payload)

    return {"success": True, "agendamento": _renovacao_agendamento_atual(client_id, loja, campanha_id)}
