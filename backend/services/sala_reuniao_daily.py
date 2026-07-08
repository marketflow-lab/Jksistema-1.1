"""Daily API helpers for the Sala de Reuniao module."""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import requests
from fastapi import HTTPException

DAILY_API_BASE_URL = "https://api.daily.co/v1"
DAILY_RECORDING_MODES = {"cloud", "cloud-audio-only", "local", "raw-tracks"}
DAILY_LANGS = {"da", "de", "en", "es", "fi", "fr", "it", "jp", "ka", "nl", "no", "pt", "pt-BR", "pl", "ru", "sv", "tr", "user"}


def _sala_reuniao_daily_api_key() -> str:
    return str(os.getenv("DAILY_API_KEY") or os.getenv("JK_DAILY_API_KEY") or "").strip()

def _sala_reuniao_bool(valor: Any, padrao: bool = False) -> bool:
    return padrao if valor is None else bool(valor)

def _sala_reuniao_privacidade(valor: Optional[str]) -> str:
    texto = str(valor or "public").strip().lower()
    mapa = {
        "public": "public",
        "publica": "public",
        "publico": "public",
        "private": "private",
        "privada": "private",
        "privado": "private",
    }
    if texto not in mapa:
        raise HTTPException(status_code=400, detail="Privacidade da sala deve ser public ou private.")
    return mapa[texto]

def _sala_reuniao_idioma(valor: Optional[str]) -> str:
    texto = str(valor or "pt-BR").strip()
    return texto if texto in DAILY_LANGS else "pt-BR"

def _sala_reuniao_modo_gravacao(valor: Optional[str]) -> str:
    texto = str(valor or "").strip().lower()
    if texto in {"", "sem", "none", "off", "false", "desativada", "desativado"}:
        return ""
    if texto not in DAILY_RECORDING_MODES:
        raise HTTPException(status_code=400, detail="Modo de gravacao invalido. Use cloud, cloud-audio-only, local ou raw-tracks.")
    return texto

def _sala_reuniao_daily_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def _sala_reuniao_daily_room_name(nome: Optional[str] = None) -> str:
    base = str(nome or "sala-reuniao").strip() or "sala-reuniao"
    base_ascii = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    base_ascii = re.sub(r"[^A-Za-z0-9_-]+", "-", base_ascii).strip("-_").lower()
    base_ascii = base_ascii or "sala-reuniao"
    suffix = f"{datetime.utcnow().strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6]}"
    max_base = max(1, 128 - len(suffix) - 1)
    return f"{base_ascii[:max_base].strip('-_') or 'sala-reuniao'}-{suffix}"

def _sala_reuniao_daily_error(resp) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            return str(payload.get("info") or payload.get("error") or payload.get("message") or resp.text or "").strip()
    except Exception:
        pass
    return str(getattr(resp, "text", "") or "").strip()

def _sala_reuniao_daily_get(path: str, api_key: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        resp = requests.get(
            f"{DAILY_API_BASE_URL}{path}",
            headers=_sala_reuniao_daily_headers(api_key),
            params=params or {},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Daily indisponivel: {exc}")
    if resp.status_code < 200 or resp.status_code >= 300:
        detalhe = _sala_reuniao_daily_error(resp) or f"Daily retornou HTTP {resp.status_code}."
        raise HTTPException(status_code=502, detail=detalhe)
    try:
        return resp.json() or {}
    except Exception:
        return {}

def _sala_reuniao_daily_post(path: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = requests.post(
            f"{DAILY_API_BASE_URL}{path}",
            headers=_sala_reuniao_daily_headers(api_key),
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Daily indisponivel: {exc}")
    if resp.status_code < 200 or resp.status_code >= 300:
        detalhe = _sala_reuniao_daily_error(resp) or f"Daily retornou HTTP {resp.status_code}."
        raise HTTPException(status_code=502, detail=detalhe)
    try:
        return resp.json() or {}
    except Exception:
        return {}

def _sala_reuniao_daily_delete(path: str, api_key: str) -> dict[str, Any]:
    try:
        resp = requests.delete(
            f"{DAILY_API_BASE_URL}{path}",
            headers=_sala_reuniao_daily_headers(api_key),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Daily indisponivel: {exc}")
    if resp.status_code == 404:
        return {"not_found": True}
    if resp.status_code < 200 or resp.status_code >= 300:
        detalhe = _sala_reuniao_daily_error(resp) or f"Daily retornou HTTP {resp.status_code}."
        raise HTTPException(status_code=502, detail=detalhe)
    try:
        return resp.json() or {}
    except Exception:
        return {}

def _sala_reuniao_url_com_token(room_url: Optional[str], token: Optional[str]) -> Optional[str]:
    if not room_url or not token:
        return room_url
    separador = "&" if "?" in room_url else "?"
    return f"{room_url}{separador}t={quote(str(token), safe='')}"

__all__ = ['DAILY_API_BASE_URL', 'DAILY_RECORDING_MODES', 'DAILY_LANGS', '_sala_reuniao_daily_api_key', '_sala_reuniao_bool', '_sala_reuniao_privacidade', '_sala_reuniao_idioma', '_sala_reuniao_modo_gravacao', '_sala_reuniao_daily_headers', '_sala_reuniao_daily_room_name', '_sala_reuniao_daily_error', '_sala_reuniao_daily_get', '_sala_reuniao_daily_post', '_sala_reuniao_daily_delete', '_sala_reuniao_url_com_token']
