"""Internal helpers for admin usuarios presence core."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import requests
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.schemas import LoginResponse
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_presence_core_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_presence_core_runtime()

def _extrair_ip_request(request: Optional[Request]) -> str:
    try:
        if not request:
            return ""
        forwarded_for = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded_for:
            return forwarded_for
        real_ip = str(request.headers.get("x-real-ip") or "").strip()
        if real_ip:
            return real_ip
        if getattr(request, "client", None) and request.client.host:
            return str(request.client.host)
    except Exception:
        pass
    return ""

def _normalizar_machine_tag(texto: str) -> str:
    valor = str(texto or "").strip()
    if not valor:
        return ""
    valor = re.sub(r"\s+", "-", valor)
    valor = re.sub(r"[^a-zA-Z0-9._:-]", "-", valor)
    valor = re.sub(r"-+", "-", valor).strip("-._:")
    return valor.upper()

def _machine_id_eh_generico(machine_id: Optional[str]) -> bool:
    valor = str(machine_id or "").strip().lower()
    if not valor:
        return True
    if valor in {"none", "null", "undefined", "acesso-via-navegador", "login-desconhecido"}:
        return True
    return valor.startswith("browser-") or valor.startswith("browser:") or valor.startswith("srv-")

def _resolver_nome_computador_por_ip(ip_address: str) -> str:
    ip = str(ip_address or "").strip()
    if not ip:
        return ""
    try:
        host, _aliases, _ips = socket.gethostbyaddr(ip)
        return _normalizar_machine_tag(host.split('.')[0])
    except Exception:
        return ""

def _resolver_mac_por_ip(ip_address: str) -> str:
    ip = str(ip_address or "").strip()
    if not ip:
        return ""
    try:
        saida = subprocess.check_output(["arp", "-a", ip], stderr=subprocess.DEVNULL, text=True, encoding="utf-8", timeout=3)
        match = re.search(r"([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2})", saida)
        if match:
            return match.group(1).replace('-', ':').upper()
    except Exception:
        pass
    return ""

def _montar_machine_id_login(request: Optional[Request], machine_id: Optional[str] = None) -> tuple[str, dict]:
    informado = str(machine_id or "").strip()
    ip_address = _extrair_ip_request(request)
    user_agent = ""
    try:
        user_agent = str((request.headers.get("user-agent") if request else "") or "").strip()
    except Exception:
        user_agent = ""

    host_name = _resolver_nome_computador_por_ip(ip_address)
    mac_address = _resolver_mac_por_ip(ip_address)

    if informado and not _machine_id_eh_generico(informado):
        final_id = informado
    else:
        partes = []
        if host_name:
            partes.append(f"pc:{host_name}")
        if mac_address:
            partes.append(f"mac:{mac_address}")
        if ip_address:
            partes.append(f"ip:{ip_address}")
        if informado:
            partes.append(f"id:{informado}")
        if not partes:
            base = f"{ip_address}|{user_agent}" if (ip_address or user_agent) else "login-desconhecido"
            partes.append("srv-" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:20])
        final_id = " | ".join(partes)

    return final_id, {
        "ip_address": ip_address or None,
        "user_agent": (user_agent[:500] if user_agent else None),
        "host_name": host_name or None,
        "mac_address": mac_address or None,
    }


def _authenticated_session_machine_id(sessao: dict, requested_machine_id: Optional[str] = None) -> str:
    """Bind an authenticated request to the machine identity carried by its JWT."""
    authenticated = str((sessao or {}).get("machine_id") or "").strip()
    if not authenticated:
        raise HTTPException(
            status_code=401,
            detail="Entre novamente para autenticar esta maquina.",
        )
    requested = str(requested_machine_id or "").strip()
    if requested and requested != authenticated:
        raise HTTPException(
            status_code=403,
            detail="A sessao atual pertence a outra maquina.",
        )
    return authenticated

def _vincular_maquina_ao_usuario_se_vazia(username: str, machine_id: str):
    machine_final = str(machine_id or "").strip()
    if not machine_final:
        return
    try:
        usuario = _obter_usuario_sql(username)
    except HTTPException:
        return

    atuais = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
    if not atuais:
        usuario["machine_id"] = machine_final
        usuario["machine_ids"] = [machine_final]
        _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-login")

def _registrar_login_maquina(username: str, client_id: str, machine_id: str, request: Optional[Request] = None):
    machine_final, meta = _montar_machine_id_login(request, machine_id)
    conn = _auth_db_conexao()
    try:
        conn.execute(
            """
            INSERT INTO login_audit (username, client_id, machine_id, ip_address, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(username or "").strip().lower(),
                str(client_id or "default").strip() or "default",
                machine_final,
                meta.get("ip_address"),
                meta.get("user_agent"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        conn.commit()
    finally:
        conn.close()
    if _firebase_deve_usar():
        _firebase_registrar_login(username, client_id, machine_final, request, meta)
    return machine_final, meta

def _machine_presence_write_interval_seconds() -> int:
    try:
        valor = int(float(os.getenv("JK_MACHINE_PRESENCE_WRITE_INTERVAL_SECONDS", "300") or 300))
        return max(60, min(valor, 1800))
    except Exception:
        return 300

def _machine_presence_timeout_seconds() -> int:
    try:
        valor = int(float(os.getenv("JK_MACHINE_ONLINE_TIMEOUT_SECONDS", "360") or 360))
        return max(_machine_presence_write_interval_seconds() + 30, min(valor, 3600))
    except Exception:
        return 360

def _machine_presence_doc_id(username: str, client_id: str, machine_id: str) -> str:
    raw = f"{str(username or '').strip().lower()}|{str(client_id or '').strip()}|{str(machine_id or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _machine_presence_label(machine_id: str) -> str:
    valor = str(machine_id or "").strip()
    if not valor:
        return "Maquina sem nome"
    partes = []
    for segmento in valor.split("|"):
        segmento = segmento.strip()
        if not segmento:
            continue
        if segmento.lower().startswith("pc:"):
            partes.append(segmento[3:].strip())
        elif segmento.lower().startswith("mac:"):
            partes.append(segmento.strip())
        elif segmento.lower().startswith("ip:") and not partes:
            partes.append(segmento.strip())
    if partes:
        return " | ".join(partes[:2])
    return valor[:80]

def _machine_presence_machine_key(machine_id: str) -> str:
    valor = str(machine_id or "").strip()
    if not valor:
        return ""
    partes = [segmento.strip() for segmento in valor.split("|") if segmento.strip()]
    mac = next((segmento for segmento in partes if segmento.lower().startswith("mac:")), "")
    if mac:
        return re.sub(r"[^a-z0-9]+", "", mac.lower())
    pc = next((segmento for segmento in partes if segmento.lower().startswith("pc:")), "")
    if pc:
        return re.sub(r"[^a-z0-9]+", "", pc.lower())
    return re.sub(r"[^a-z0-9]+", "", valor.lower())

def _machine_presence_sanitize_page(page: str) -> str:
    value = str(page or "").strip()
    value = value.replace("\\", "/")
    if len(value) > 180:
        value = value[:180]
    return value

def _machine_presence_normalize_app_version(value: Optional[Any]) -> str:
    texto = str(value or "").strip()
    if not texto or texto.lower() in {"none", "null", "undefined"}:
        return ""
    texto = re.sub(r"^v\s*", "", texto, flags=re.IGNORECASE)
    match = re.search(r"\d+(?:\.\d+){0,5}(?:[-+][0-9A-Za-z._-]+)?", texto)
    if match:
        return match.group(0)[:60]
    return re.sub(r"\s+", " ", texto)[:60]

def _machine_presence_app_version_from_user_agent(user_agent: Optional[str]) -> str:
    ua = str(user_agent or "").strip()
    if not ua:
        return ""
    match = re.search(r"\bjk-sistema-desktop/([0-9A-Za-z._+-]+)", ua, re.IGNORECASE)
    if match:
        return _machine_presence_normalize_app_version(match.group(1))
    return ""

def _machine_presence_resolve_app_version(app_version: Optional[Any], user_agent: Optional[str]) -> str:
    return (
        _machine_presence_normalize_app_version(app_version)
        or _machine_presence_app_version_from_user_agent(user_agent)
    )

def _machine_presence_preserve_app_version(record: dict, existing: Optional[dict] = None) -> dict:
    if not isinstance(record, dict):
        return record
    out = dict(record)
    current_version = _machine_presence_resolve_app_version(out.get("app_version"), out.get("user_agent"))
    if current_version:
        out["app_version"] = current_version
        return out
    if isinstance(existing, dict):
        existing_version = _machine_presence_resolve_app_version(existing.get("app_version"), existing.get("user_agent"))
        if existing_version:
            out["app_version"] = existing_version
    return out

def _machine_presence_record(username: str, client_id: str, machine_id: str, request: Optional[Request], page: str = "", app_version: Optional[str] = None) -> dict:
    machine_final, meta = _montar_machine_id_login(request, machine_id)
    now_ts = int(time.time())
    timeout_s = _machine_presence_timeout_seconds()
    app_version_final = _machine_presence_resolve_app_version(app_version, meta.get("user_agent"))
    machine_key = _machine_presence_machine_key(machine_final)
    return {
        "id": _machine_presence_doc_id(username, client_id, machine_final),
        "username": str(username or "").strip().lower(),
        "client_id": str(client_id or "default").strip() or "default",
        "machine_id": machine_final,
        "machine_key": machine_key,
        "label": _machine_presence_label(machine_final),
        "page": _machine_presence_sanitize_page(page),
        "app_version": app_version_final,
        "ip_address": meta.get("ip_address"),
        "host_name": meta.get("host_name"),
        "user_agent": meta.get("user_agent"),
        "last_seen_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_ts": now_ts,
        "expires_at_ts": now_ts + timeout_s,
        "online_timeout_seconds": timeout_s,
    }

def _machine_presence_auto_touch(username: str, client_id: str, request: Optional[Request], machine_id: str = "") -> None:
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"
    if not username_norm or not client_norm:
        return
    try:
        path = str(getattr(getattr(request, "url", None), "path", "") or "")
        machine_final, _meta = _montar_machine_id_login(request, machine_id)
        if not machine_final:
            return
        doc_id = _machine_presence_doc_id(username_norm, client_norm, machine_final)
        now = time.time()
        min_interval = _machine_presence_write_interval_seconds()
        with MACHINE_PRESENCE_LOCK:
            last_touch = float(MACHINE_PRESENCE_AUTO_TOUCH_LAST.get(doc_id) or 0)
            if now - last_touch < min_interval:
                return
            MACHINE_PRESENCE_AUTO_TOUCH_LAST[doc_id] = now
            if len(MACHINE_PRESENCE_AUTO_TOUCH_LAST) > 2000:
                cutoff = now - max(_machine_presence_timeout_seconds() * 8, 1800)
                for key, value in list(MACHINE_PRESENCE_AUTO_TOUCH_LAST.items()):
                    if float(value or 0) < cutoff:
                        MACHINE_PRESENCE_AUTO_TOUCH_LAST.pop(key, None)
        _machine_presence_save(_machine_presence_record(username_norm, client_norm, machine_final, request, path))
    except Exception as exc:
        logger.debug("[MACHINES] Falha ao renovar presenca autenticada: %s", exc)

def _machine_presence_local_read() -> dict:
    with MACHINE_PRESENCE_LOCK:
        try:
            if not os.path.exists(ARQUIVO_MACHINE_PRESENCE):
                return {}
            with open(ARQUIVO_MACHINE_PRESENCE, "r", encoding="utf-8") as arquivo:
                data = json.load(arquivo)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("[MACHINES] Falha ao ler presenca local: %s", exc)
            return {}

def _machine_presence_local_write(data: dict) -> None:
    with MACHINE_PRESENCE_LOCK:
        try:
            os.makedirs(os.path.dirname(ARQUIVO_MACHINE_PRESENCE), exist_ok=True)
            with open(ARQUIVO_MACHINE_PRESENCE, "w", encoding="utf-8") as arquivo:
                json.dump(data if isinstance(data, dict) else {}, arquivo, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[MACHINES] Falha ao salvar presenca local: %s", exc)

def _machine_presence_prune_local(data: dict, now_ts: Optional[int] = None) -> dict:
    now_ts = int(now_ts or time.time())
    limite_antigo = now_ts - max(_machine_presence_timeout_seconds() * 8, 1800)
    result = {}
    for chave, item in (data or {}).items():
        if not isinstance(item, dict):
            continue
        try:
            last_seen = int(float(item.get("last_seen_ts") or 0))
        except Exception:
            last_seen = 0
        if last_seen >= limite_antigo:
            result[str(chave)] = item
    return result

def _machine_presence_save(record: dict) -> dict:
    if not isinstance(record, dict) or not record.get("id"):
        return record

    record_id = str(record["id"])
    local_data = _machine_presence_prune_local(_machine_presence_local_read())
    record_to_save = _machine_presence_preserve_app_version(record, local_data.get(record_id))
    local_data[record_id] = record_to_save
    _machine_presence_local_write(local_data)

    if _firebase_live_features_ativas():
        try:
            db = _firebase_db()
            if db is not None:
                firebase_record = dict(record_to_save)
                if not _machine_presence_resolve_app_version(firebase_record.get("app_version"), firebase_record.get("user_agent")):
                    firebase_record.pop("app_version", None)
                db.collection(_firebase_presence_collection_name()).document(record_id).set(firebase_record, merge=True)
        except Exception as exc:
            logger.warning("[MACHINES] Falha ao salvar presenca no Firebase: %s", exc)
    return record_to_save

def _machine_presence_list_firebase(username: str, client_id: str) -> list[dict]:
    if not _firebase_live_features_ativas():
        return []
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"
    cache_key = f"presence-user:{client_norm}:{username_norm}:v1"
    cached = _backend_cache_get(cache_key)
    if isinstance(cached, list):
        return cached
    try:
        db = _firebase_db()
        if db is None:
            return []
        coll = db.collection(_firebase_presence_collection_name())
        docs = coll.where("username", "==", username_norm).stream()
        registros = []
        for snap in docs:
            data = snap.to_dict() or {}
            if str(data.get("client_id") or "").strip() != client_norm:
                continue
            registros.append(data)
        _backend_cache_set(cache_key, registros, ttl_seconds=90)
        return registros
    except Exception as exc:
        logger.warning("[MACHINES] Falha ao listar presenca no Firebase: %s", exc)
        return []

def _machine_presence_list_firebase_client(client_id: str) -> list[dict]:
    if not _firebase_live_features_ativas():
        return []
    client_norm = str(client_id or "default").strip() or "default"
    cache_key = f"presence-client:{client_norm}:v1"
    cached = _backend_cache_get(cache_key)
    if isinstance(cached, list):
        return cached
    try:
        db = _firebase_db()
        if db is None:
            return []
        coll = db.collection(_firebase_presence_collection_name())
        registros = [(snap.to_dict() or {}) for snap in coll.where("client_id", "==", client_norm).stream()]
        _backend_cache_set(cache_key, registros, ttl_seconds=90)
        return registros
    except Exception as exc:
        logger.warning("[MACHINES] Falha ao listar presenca do cliente no Firebase: %s", exc)
        return []

def _machine_presence_list_from_records(username: str, client_id: str, registros: list[dict]) -> list[dict]:
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"
    now_ts = int(time.time())
    timeout_s = _machine_presence_timeout_seconds()
    maquinas = []
    por_chave = {}
    for item in registros:
        if not isinstance(item, dict):
            continue
        if str(item.get("username") or "").strip().lower() != username_norm:
            continue
        if str(item.get("client_id") or "").strip() != client_norm:
            continue
        machine_id = str(item.get("machine_id") or "").strip()
        if not machine_id:
            continue
        chave = _machine_presence_machine_key(machine_id) or _machine_presence_doc_id(username_norm, client_norm, machine_id)
        atual = por_chave.get(chave) or {}
        try:
            last_seen = int(float(item.get("last_seen_ts") or 0))
        except Exception:
            last_seen = 0
        try:
            last_seen_atual = int(float(atual.get("last_seen_ts") or 0))
        except Exception:
            last_seen_atual = 0
        if atual and last_seen < last_seen_atual:
            continue
        if atual:
            item = _machine_presence_preserve_app_version(item, atual)
        por_chave[chave] = item

    for item in por_chave.values():
        machine_id = str(item.get("machine_id") or "").strip()
        machine_key = _machine_presence_machine_key(machine_id)
        try:
            last_seen = int(float(item.get("last_seen_ts") or 0))
        except Exception:
            last_seen = 0
        online = last_seen >= (now_ts - timeout_s)
        maquinas.append({
            "machine_id": machine_id,
            "machine_key": machine_key,
            "label": str(item.get("label") or _machine_presence_label(machine_id)),
            "page": _machine_presence_sanitize_page(item.get("page") or ""),
            "app_version": _machine_presence_resolve_app_version(item.get("app_version"), item.get("user_agent")),
            "last_seen_at": item.get("last_seen_at") or "",
            "last_seen_ts": last_seen,
            "seconds_since_seen": max(0, now_ts - last_seen) if last_seen else None,
            "online": online,
            "current": False,
        })

    maquinas.sort(key=lambda item: (not bool(item.get("online")), -(int(item.get("last_seen_ts") or 0))))
    return maquinas

def _machine_presence_list(username: str, client_id: str) -> list[dict]:
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "default").strip() or "default"
    registros = []
    registros.extend(_machine_presence_list_firebase(username_norm, client_norm))
    registros.extend(list(_machine_presence_local_read().values()))
    return _machine_presence_list_from_records(username_norm, client_norm, registros)

def _machine_presence_mark_current(maquinas: list[dict], current_machine_id: str) -> list[dict]:
    current = str(current_machine_id or "").strip()
    if not current:
        return maquinas
    current_key = _machine_presence_machine_key(current)
    for item in maquinas:
        item_id = str(item.get("machine_id") or "").strip()
        item_key = str(item.get("machine_key") or _machine_presence_machine_key(item_id) or "").strip()
        if item_id == current or (current_key and item_key == current_key):
            item["current"] = True
    return maquinas


__all__ = [
    "configure_admin_usuarios_presence_core_runtime",
    "_extrair_ip_request",
    "_normalizar_machine_tag",
    "_machine_id_eh_generico",
    "_resolver_nome_computador_por_ip",
    "_resolver_mac_por_ip",
    "_montar_machine_id_login",
    "_authenticated_session_machine_id",
    "_vincular_maquina_ao_usuario_se_vazia",
    "_registrar_login_maquina",
    "_machine_presence_write_interval_seconds",
    "_machine_presence_timeout_seconds",
    "_machine_presence_doc_id",
    "_machine_presence_label",
    "_machine_presence_machine_key",
    "_machine_presence_sanitize_page",
    "_machine_presence_normalize_app_version",
    "_machine_presence_app_version_from_user_agent",
    "_machine_presence_resolve_app_version",
    "_machine_presence_preserve_app_version",
    "_machine_presence_record",
    "_machine_presence_auto_touch",
    "_machine_presence_local_read",
    "_machine_presence_local_write",
    "_machine_presence_prune_local",
    "_machine_presence_save",
    "_machine_presence_list_firebase",
    "_machine_presence_list_firebase_client",
    "_machine_presence_list_from_records",
    "_machine_presence_list",
    "_machine_presence_mark_current",
]
