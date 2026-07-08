"""Core Sala de Reuniao room orchestration."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote, unquote, urlparse

from fastapi import HTTPException

from backend.schemas import SalaReuniaoCriarSalaRequest, SalaReuniaoEncerrarLocalRequest
from backend.services import sala_reuniao_context as sala_context
from backend.services.sala_reuniao_daily import *
from backend.services.sala_reuniao_store import *


def _sala_reuniao_username(valor: Any) -> str:
    return str(valor or "").strip().lower()

def _sala_reuniao_sessao(authorization: Optional[str], client_id: str) -> dict[str, Any]:
    sessao = sala_context.shared_sync_session(authorization, client_id)
    return {
        "username": _sala_reuniao_username(sessao.get("username")),
        "client_id": str(sessao.get("client_id") or client_id or "default").strip() or "default",
        "is_admin": bool(sessao.get("is_admin")),
        "permissions": sessao.get("permissions") if isinstance(sessao.get("permissions"), dict) else {},
    }

def _sala_reuniao_domain_host() -> str:
    domain = str(os.getenv("DAILY_DOMAIN") or os.getenv("JK_DAILY_DOMAIN") or "").strip()
    if not domain:
        return ""
    if domain.startswith(("http://", "https://")):
        try:
            domain = urlparse(domain).hostname or ""
        except Exception:
            domain = ""
    domain = domain.strip().strip("/")
    if domain and "." not in domain:
        domain = f"{domain}.daily.co"
    return domain

def _sala_reuniao_room_url(room_name: str, known_url: str = "") -> str:
    known_url = str(known_url or "").strip()
    if known_url:
        return known_url
    domain = _sala_reuniao_domain_host()
    if not domain:
        return ""
    room_name = str(room_name or "").strip()
    if not room_name:
        return ""
    return f"https://{domain}/{quote(room_name, safe='A-Za-z0-9_-')}"

def _sala_reuniao_room_name_from_url(room_url: Any) -> str:
    room_url = str(room_url or "").strip()
    if not room_url:
        return ""
    try:
        parsed = urlparse(room_url)
        path = (parsed.path or "").strip("/")
        if path:
            return unquote(path.split("/")[-1]).strip()
    except Exception:
        pass
    return ""

def _sala_reuniao_room_keys(room_name: Any = "", room_url: Any = "") -> set[str]:
    name = str(room_name or "").strip() or _sala_reuniao_room_name_from_url(room_url)
    url = str(room_url or "").strip()
    keys: set[str] = set()
    if name:
        keys.add(f"name:{name.lower()}")
    if url:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").strip().lower()
            path = (parsed.path or "").rstrip("/").lower()
            if host and path:
                keys.add(f"url:{host}{path}")
            else:
                keys.add(f"url:{url.split('?')[0].rstrip('/').lower()}")
        except Exception:
            keys.add(f"url:{url.split('?')[0].rstrip('/').lower()}")
    return keys

def _sala_reuniao_iso_from_timestamp(value: Any) -> str:
    try:
        ts = float(value)
        if ts > 100000000000:
            ts = ts / 1000.0
        return datetime.utcfromtimestamp(ts).isoformat() + "Z"
    except Exception:
        return ""

def _sala_reuniao_participant_count(participants: Any) -> int:
    if isinstance(participants, list):
        return len(participants)
    if isinstance(participants, dict):
        for key in ("count", "total", "participant_count", "participants_count", "present"):
            try:
                if key in participants:
                    return max(0, int(participants.get(key) or 0))
            except Exception:
                pass
        for key in ("data", "participants", "users"):
            nested = participants.get(key)
            if isinstance(nested, (list, dict)):
                return _sala_reuniao_participant_count(nested)
        return len(participants)
    return 0

def _sala_reuniao_participant_names(participants: Any, limit: int = 8) -> list[str]:
    names: list[str] = []

    def add_name(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if text.lower() in {"none", "null", "undefined"}:
            return
        if text not in names:
            names.append(text[:80])

    def walk(value: Any) -> None:
        if len(names) >= limit:
            return
        if isinstance(value, str):
            add_name(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
                if len(names) >= limit:
                    break
            return
        if isinstance(value, dict):
            for key in ("user_name", "userName", "name", "display_name", "displayName", "username", "user_id", "userId", "id"):
                if value.get(key):
                    add_name(value.get(key))
                    return
            for key in ("data", "participants", "users"):
                if key in value:
                    walk(value.get(key))
                    return
            for item in value.values():
                walk(item)
                if len(names) >= limit:
                    break

    walk(participants)
    return names[:limit]

def _sala_reuniao_iso_timestamp(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return int(parsed.timestamp())
        return int(time.mktime(parsed.timetuple()))
    except Exception:
        return 0

def _sala_reuniao_local_pending_seconds() -> int:
    try:
        seconds = int(os.getenv("JK_SALA_REUNIAO_LOCAL_PENDING_SECONDS", "900") or 900)
    except Exception:
        seconds = 900
    return max(120, min(seconds, 7200))

def _sala_reuniao_registrar_sala(client_id: str, room: dict[str, Any], host_token: Optional[str], exp_timestamp: int, created_by: str = "") -> None:
    room_name = str(room.get("name") or "").strip()
    room_url = str(room.get("url") or "").strip()
    if not room_name and not room_url:
        return
    payload = _sala_reuniao_carregar_salas(client_id)
    rooms = payload.setdefault("rooms", [])
    if not isinstance(rooms, list):
        rooms = []
    now_iso = datetime.now().isoformat(timespec="seconds")
    rooms = [item for item in rooms if str(item.get("name") or "") != room_name]
    expires_at = datetime.utcfromtimestamp(exp_timestamp).isoformat() + "Z" if exp_timestamp > 0 else ""
    rooms.append({
        "name": room_name,
        "url": room_url,
        "host_url": _sala_reuniao_url_com_token(room_url, host_token),
        "privacy": room.get("privacy") or "public",
        "expires_at": expires_at,
        "expires_at_ts": int(exp_timestamp or 0),
        "created_by": _sala_reuniao_username(created_by),
        "created_at": now_iso,
        "updated_at": now_iso,
    })
    now_ts = int(time.time())
    rooms = [
        item for item in rooms[-120:]
        if int(item.get("expires_at_ts") or 0) <= 0 or int(item.get("expires_at_ts") or 0) > now_ts
    ]
    payload["rooms"] = rooms
    payload["updated_at"] = now_iso
    _sala_reuniao_salvar_salas(client_id, payload)

def _sala_reuniao_encontrar_sala_local(client_id: str, room_name: Any = "", room_url: Any = "") -> Optional[dict[str, Any]]:
    target_keys = _sala_reuniao_room_keys(room_name, room_url)
    if not target_keys:
        return None
    payload = _sala_reuniao_carregar_salas(client_id)
    for item in payload.get("rooms") or []:
        if not isinstance(item, dict):
            continue
        item_keys = _sala_reuniao_room_keys(item.get("name") or "", item.get("url") or "")
        if item_keys and not target_keys.isdisjoint(item_keys):
            return item
    return None

def _sala_reuniao_usuario_pode_encerrar(room: Optional[dict[str, Any]], sessao: Optional[dict[str, Any]]) -> bool:
    if not sessao:
        return False
    if bool(sessao.get("is_admin")):
        return True
    if not isinstance(room, dict):
        return False
    created_by = _sala_reuniao_username(room.get("created_by") or room.get("creator") or room.get("created_by_username"))
    username = _sala_reuniao_username(sessao.get("username"))
    return bool(created_by and username and created_by == username)

def _sala_reuniao_exigir_permissao_encerrar(client_id: str, req: SalaReuniaoEncerrarLocalRequest, sessao: dict[str, Any]) -> None:
    room_url = str(req.room_url or "").strip()
    room_name = str(req.room_name or "").strip() or _sala_reuniao_room_name_from_url(room_url)
    local_room = _sala_reuniao_encontrar_sala_local(client_id, room_name, room_url)
    if _sala_reuniao_usuario_pode_encerrar(local_room, sessao):
        return
    raise HTTPException(status_code=403, detail="Apenas o criador da sala ou um administrador pode encerrar esta reuniao.")

def _sala_reuniao_marcar_sala_encerrada(client_id: str, req: SalaReuniaoEncerrarLocalRequest, sessao: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    room_url = str(req.room_url or "").strip()
    room_name = str(req.room_name or "").strip() or _sala_reuniao_room_name_from_url(room_url)
    if not room_name and not room_url:
        raise HTTPException(status_code=400, detail="Informe o nome ou link da sala para encerrar localmente.")

    payload = _sala_reuniao_carregar_salas(client_id)
    rooms = payload.setdefault("rooms", [])
    if not isinstance(rooms, list):
        rooms = []

    now_ts = int(time.time())
    now_iso = datetime.now().isoformat(timespec="seconds")
    try:
        suppress_seconds = int(req.suppress_seconds or 300)
    except Exception:
        suppress_seconds = 300
    suppress_seconds = max(30, min(suppress_seconds, 1800))
    target_keys = _sala_reuniao_room_keys(room_name, room_url)
    participant_count = max(0, int(req.participant_count or 0))
    matched = False

    for item in rooms:
        if not isinstance(item, dict):
            continue
        item_keys = _sala_reuniao_room_keys(item.get("name") or "", item.get("url") or "")
        if target_keys and item_keys and target_keys.isdisjoint(item_keys):
            continue
        item["ended_at"] = now_iso
        item["ended_at_ts"] = now_ts
        item["suppress_until_ts"] = now_ts + suppress_seconds
        item["last_left_participants"] = participant_count
        item["updated_at"] = now_iso
        matched = True

    if not matched:
        rooms.append({
            "name": room_name,
            "url": room_url,
            "host_url": "",
            "privacy": "public",
            "created_at": now_iso,
            "updated_at": now_iso,
            "ended_at": now_iso,
            "ended_at_ts": now_ts,
            "suppress_until_ts": now_ts + suppress_seconds,
            "last_left_participants": participant_count,
        })

    payload["rooms"] = rooms[-120:]
    payload["updated_at"] = now_iso
    _sala_reuniao_salvar_salas(client_id, payload)
    return _sala_reuniao_listar_reunioes_ativas(client_id, sessao)

def _sala_reuniao_encerrar_sala(client_id: str, req: SalaReuniaoEncerrarLocalRequest, sessao: dict[str, Any]) -> dict[str, Any]:
    room_url = str(req.room_url or "").strip()
    room_name = str(req.room_name or "").strip() or _sala_reuniao_room_name_from_url(room_url)
    if not room_name and not room_url:
        raise HTTPException(status_code=400, detail="Informe o nome ou link da sala para encerrar.")

    _sala_reuniao_exigir_permissao_encerrar(client_id, req, sessao)
    warning = ""
    daily_encerrada = False
    api_key = _sala_reuniao_daily_api_key()
    if api_key and room_name:
        try:
            _sala_reuniao_daily_delete(f"/rooms/{quote(room_name, safe='')}", api_key)
            daily_encerrada = True
        except HTTPException as exc:
            warning = str(exc.detail or "Nao foi possivel encerrar a sala na Daily.")
    elif not api_key:
        warning = "Sem chave Daily configurada; a sala foi encerrada apenas na lista local."

    data = _sala_reuniao_marcar_sala_encerrada(client_id, req, sessao)
    return {
        "rooms": data.get("rooms") or [],
        "warning": warning or data.get("warning") or "",
        "daily_encerrada": daily_encerrada,
    }

def _sala_reuniao_listar_reunioes_ativas(client_id: str, sessao: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    now_ts = int(time.time())
    payload = _sala_reuniao_carregar_salas(client_id)
    by_name: dict[str, dict[str, Any]] = {}
    local_by_name: dict[str, dict[str, Any]] = {}
    suppressed_keys: set[str] = set()
    for item in payload.get("rooms") or []:
        if not isinstance(item, dict):
            continue
        item_keys = _sala_reuniao_room_keys(item.get("name") or "", item.get("url") or "")
        try:
            suppress_until_ts = int(item.get("suppress_until_ts") or 0)
        except Exception:
            suppress_until_ts = 0
        if suppress_until_ts > now_ts:
            suppressed_keys.update(item_keys)

    for item in payload.get("rooms") or []:
        if not isinstance(item, dict):
            continue
        try:
            exp_ts = int(item.get("expires_at_ts") or 0)
        except Exception:
            exp_ts = 0
        if exp_ts and exp_ts <= now_ts:
            continue
        try:
            ended_ts = int(item.get("ended_at_ts") or 0)
        except Exception:
            ended_ts = 0
        if ended_ts > 0:
            continue
        created_ts = _sala_reuniao_iso_timestamp(item.get("created_at"))
        if not exp_ts and created_ts and now_ts - created_ts > _sala_reuniao_local_pending_seconds():
            continue
        name = str(item.get("name") or "").strip()
        url = _sala_reuniao_room_url(name, item.get("url") or "")
        if not name and not url:
            continue
        if name:
            local_by_name[name] = item
        item_keys = _sala_reuniao_room_keys(name, url)
        if item_keys and not item_keys.isdisjoint(suppressed_keys):
            continue
        key = name or url
        by_name[key] = {
            "name": name,
            "url": url,
            "host_url": item.get("host_url") or "",
            "privacy": item.get("privacy") or "public",
            "expires_at": item.get("expires_at") or "",
            "created_at": item.get("created_at") or "",
            "created_by": _sala_reuniao_username(item.get("created_by")),
            "participants_count": 0,
            "participant_names": [],
            "ongoing": False,
            "status": "Aberta",
            "source": "local",
        }

    warning = ""
    api_key = _sala_reuniao_daily_api_key()
    if api_key:
        try:
            data = _sala_reuniao_daily_get("/meetings", api_key, {
                "ongoing": "true",
                "limit": 50,
            })
            for meeting in data.get("data") or []:
                if not isinstance(meeting, dict):
                    continue
                name = str(meeting.get("room") or meeting.get("room_name") or "").strip()
                if not name:
                    continue
                current = by_name.get(name, {})
                participants_count = _sala_reuniao_participant_count(meeting.get("participants"))
                participant_names = _sala_reuniao_participant_names(meeting.get("participants"))
                try:
                    presence = _sala_reuniao_daily_get(f"/rooms/{quote(name, safe='')}/presence", api_key, {"limit": 12})
                    presence_names = _sala_reuniao_participant_names(presence)
                    if presence_names:
                        participant_names = presence_names
                    presence_count = _sala_reuniao_participant_count(presence)
                    if presence_count > 0:
                        participants_count = presence_count
                except Exception:
                    pass
                meeting_keys = _sala_reuniao_room_keys(name, current.get("url") or "")
                if meeting_keys and not meeting_keys.isdisjoint(suppressed_keys) and participants_count <= 1:
                    continue
                local_item = local_by_name.get(name) or {}
                local_url = _sala_reuniao_room_url(name, local_item.get("url") or current.get("url") or "")
                current.update({
                    "name": name,
                    "url": local_url,
                    "host_url": current.get("host_url") or local_item.get("host_url") or "",
                    "privacy": current.get("privacy") or local_item.get("privacy") or "public",
                    "expires_at": current.get("expires_at") or local_item.get("expires_at") or "",
                    "created_at": current.get("created_at") or local_item.get("created_at") or "",
                    "created_by": current.get("created_by") or _sala_reuniao_username(local_item.get("created_by")),
                    "participants_count": participants_count,
                    "participant_names": participant_names,
                    "ongoing": bool(meeting.get("ongoing", True)),
                    "status": "Em andamento",
                    "source": "daily",
                    "meeting_id": meeting.get("id") or "",
                    "started_at": _sala_reuniao_iso_from_timestamp(meeting.get("start_time")),
                    "duration_seconds": meeting.get("duration") or 0,
                })
                by_name[name] = current
        except HTTPException as exc:
            warning = str(exc.detail or "Nao foi possivel consultar reunioes ativas na Daily.")

    rooms = list(by_name.values())
    for room in rooms:
        room_name = str(room.get("name") or "").strip()
        local_item = local_by_name.get(room_name) or room
        room["can_end"] = _sala_reuniao_usuario_pode_encerrar(local_item, sessao)
    rooms.sort(key=lambda item: (
        0 if item.get("ongoing") else 1,
        str(item.get("started_at") or item.get("created_at") or ""),
    ), reverse=False)
    return {"rooms": rooms, "warning": warning}

def _sala_reuniao_criar_token_host(
    api_key: str,
    room_name: str,
    req: SalaReuniaoCriarSalaRequest,
    exp_timestamp: int,
    idioma: str,
) -> dict[str, Any]:
    nome_host = str(req.nome_host or "Anfitriao JK Sistema").strip() or "Anfitriao JK Sistema"
    properties: dict[str, Any] = {
        "room_name": room_name,
        "user_name": nome_host[:80],
        "is_owner": True,
        "enable_screenshare": _sala_reuniao_bool(req.habilitar_compartilhar_tela, True),
        "enable_recording_ui": False,
        "enable_prejoin_ui": _sala_reuniao_bool(req.habilitar_prejoin, True),
        "enable_live_captions_ui": False,
        "start_audio_off": _sala_reuniao_bool(req.iniciar_audio_desligado, True),
        "start_video_off": _sala_reuniao_bool(req.iniciar_video_desligado, True),
        "lang": idioma,
    }
    if exp_timestamp > 0:
        properties["exp"] = exp_timestamp
        properties["eject_at_token_exp"] = True
    if req.duracao_maxima_minutos:
        duracao = max(5, min(int(req.duracao_maxima_minutos), 480))
        properties["eject_after_elapsed"] = duracao * 60
    return _sala_reuniao_daily_post("/meeting-tokens", api_key, {"properties": properties})

__all__ = ['_sala_reuniao_username', '_sala_reuniao_sessao', '_sala_reuniao_domain_host', '_sala_reuniao_room_url', '_sala_reuniao_room_name_from_url', '_sala_reuniao_room_keys', '_sala_reuniao_iso_from_timestamp', '_sala_reuniao_participant_count', '_sala_reuniao_participant_names', '_sala_reuniao_iso_timestamp', '_sala_reuniao_local_pending_seconds', '_sala_reuniao_registrar_sala', '_sala_reuniao_encontrar_sala_local', '_sala_reuniao_usuario_pode_encerrar', '_sala_reuniao_exigir_permissao_encerrar', '_sala_reuniao_marcar_sala_encerrada', '_sala_reuniao_encerrar_sala', '_sala_reuniao_listar_reunioes_ativas', '_sala_reuniao_criar_token_host']
