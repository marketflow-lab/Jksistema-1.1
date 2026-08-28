"""Internal helpers for admin usuarios firebase."""

from __future__ import annotations

import copy
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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import requests
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.schemas import LoginResponse
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_firebase_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_firebase_runtime()


FIREBASE_NONCRITICAL_WRITE_LOCK = threading.RLock()
FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL = 0.0
FIREBASE_USER_READ_CACHE_LOCK = threading.RLock()
FIREBASE_USER_READ_CACHE: dict[str, tuple[float, dict]] = {}


def _firebase_call_timeout_seconds() -> float:
    try:
        value = float(os.getenv("JK_FIREBASE_CALL_TIMEOUT_SECONDS", "5") or 5)
    except Exception:
        value = 5.0
    return max(1.0, min(value, 15.0))


def _firebase_quota_cooldown_seconds() -> float:
    try:
        value = float(os.getenv("JK_FIREBASE_QUOTA_COOLDOWN_SECONDS", "900") or 900)
    except Exception:
        value = 900.0
    return max(60.0, min(value, 3600.0))


def _firebase_user_cache_seconds() -> float:
    try:
        value = float(os.getenv("JK_FIREBASE_USER_CACHE_SECONDS", "10800") or 10800)
    except Exception:
        value = 10800.0
    return max(1.0, min(value, 10800.0))


def _firebase_quota_excedida(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None
    code_name = str(getattr(code, "name", code) or "").strip().lower()
    message = str(exc or "").strip().lower()
    return (
        status_code == 429
        or code == 429
        or code_name in {"429", "resource_exhausted"}
        or "quota exceeded" in message
        or "resource_exhausted" in message
    )


def _firebase_noncritical_write_available() -> bool:
    with FIREBASE_NONCRITICAL_WRITE_LOCK:
        return time.monotonic() >= FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL


def _firebase_noncritical_write(operation, *args, **kwargs) -> bool:
    global FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL
    if not _firebase_noncritical_write_available():
        return False
    kwargs.setdefault("retry", None)
    kwargs.setdefault("timeout", _firebase_call_timeout_seconds())
    try:
        operation(*args, **kwargs)
        return True
    except Exception as exc:
        if _firebase_quota_excedida(exc):
            with FIREBASE_NONCRITICAL_WRITE_LOCK:
                FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL = max(
                    FIREBASE_NONCRITICAL_WRITE_COOLDOWN_UNTIL,
                    time.monotonic() + _firebase_quota_cooldown_seconds(),
                )
        raise


def _firebase_user_cache_get(username: str) -> Optional[dict]:
    username_norm = str(username or "").strip().lower()
    if not username_norm:
        return None
    with FIREBASE_USER_READ_CACHE_LOCK:
        cached = FIREBASE_USER_READ_CACHE.get(username_norm)
        if not cached:
            return None
        expires_at, usuario = cached
        if time.monotonic() >= float(expires_at or 0):
            FIREBASE_USER_READ_CACHE.pop(username_norm, None)
            return None
        return copy.deepcopy(usuario)


def _firebase_user_cache_set(username: str, usuario: dict) -> None:
    username_norm = str(username or "").strip().lower()
    if not username_norm or not isinstance(usuario, dict):
        return
    with FIREBASE_USER_READ_CACHE_LOCK:
        FIREBASE_USER_READ_CACHE[username_norm] = (
            time.monotonic() + _firebase_user_cache_seconds(),
            copy.deepcopy(usuario),
        )


def _firebase_user_cache_invalidate(username: str = "") -> None:
    username_norm = str(username or "").strip().lower()
    with FIREBASE_USER_READ_CACHE_LOCK:
        if username_norm:
            FIREBASE_USER_READ_CACHE.pop(username_norm, None)
        else:
            FIREBASE_USER_READ_CACHE.clear()

def _firebase_access_mode() -> str:
    return str(os.getenv("JK_ACCESS_BACKEND", "auto") or "auto").strip().lower()

def _firebase_access_obrigatorio() -> bool:
    return _firebase_access_mode() == "firebase"

def _firebase_access_desativado() -> bool:
    return _firebase_access_mode() in {"local", "sqlite", "sheets", "planilha"} or str(
        os.getenv("JK_FIREBASE_ACCESS_DISABLED", "") or ""
    ).strip().lower() in {"1", "true", "sim", "yes", "on"}

def _firebase_users_collection_name() -> str:
    return _env_texto("FIREBASE_USERS_COLLECTION", "JK_FIREBASE_USERS_COLLECTION") or "jk_sistema_usuarios"

def _firebase_users_index_collection_name() -> str:
    return _env_texto("FIREBASE_USERS_INDEX_COLLECTION", "JK_FIREBASE_USERS_INDEX_COLLECTION") or "users_index"

def _firebase_audit_collection_name() -> str:
    return _env_texto("FIREBASE_LOGIN_AUDIT_COLLECTION", "JK_FIREBASE_LOGIN_AUDIT_COLLECTION") or "jk_sistema_login_audit"

def _firebase_presence_collection_name() -> str:
    return _env_texto("FIREBASE_MACHINE_PRESENCE_COLLECTION", "JK_FIREBASE_MACHINE_PRESENCE_COLLECTION") or "jk_sistema_machine_presence"

def _firebase_admin_messages_collection_name() -> str:
    return _env_texto("FIREBASE_ADMIN_MESSAGES_COLLECTION", "JK_FIREBASE_ADMIN_MESSAGES_COLLECTION") or "jk_sistema_admin_messages"

def _firebase_user_chat_collection_name() -> str:
    return _env_texto("FIREBASE_USER_CHAT_COLLECTION", "JK_FIREBASE_USER_CHAT_COLLECTION") or "jk_sistema_user_chat_messages"

def _firebase_user_chat_typing_collection_name() -> str:
    return _env_texto("FIREBASE_USER_CHAT_TYPING_COLLECTION", "JK_FIREBASE_USER_CHAT_TYPING_COLLECTION") or "jk_sistema_user_chat_typing"

def _firebase_user_status_collection_name() -> str:
    return _env_texto("FIREBASE_USER_STATUS_COLLECTION", "JK_FIREBASE_USER_STATUS_COLLECTION") or "jk_sistema_user_status"

def _firebase_live_features_ativas() -> bool:
    if not _firebase_deve_usar():
        return False
    return _env_config_bool(
        (
            "JK_FIREBASE_LIVE_FEATURES",
            "FIREBASE_LIVE_FEATURES",
            "JK_FIREBASE_CHAT_PRESENCE_ENABLED",
        ),
        default=False,
    )

def _firebase_realtime_database_url() -> str:
    explicit = _env_texto(
        "FIREBASE_DATABASE_URL",
        "FIREBASE_REALTIME_DATABASE_URL",
        "JK_FIREBASE_DATABASE_URL",
        "JK_FIREBASE_REALTIME_DATABASE_URL",
    )
    if explicit:
        return explicit.rstrip("/")
    project_id = _firebase_project_id()
    if not project_id:
        return ""
    return f"https://{project_id}-default-rtdb.firebaseio.com"

def _firebase_web_api_key() -> str:
    return _env_texto(
        "FIREBASE_WEB_API_KEY",
        "FIREBASE_API_KEY",
        "JK_FIREBASE_WEB_API_KEY",
        "JK_FIREBASE_API_KEY",
    )

def _firebase_web_app_id() -> str:
    return _env_texto("FIREBASE_WEB_APP_ID", "FIREBASE_APP_ID", "JK_FIREBASE_WEB_APP_ID", "JK_FIREBASE_APP_ID")

def _firebase_web_auth_domain() -> str:
    explicit = _env_texto("FIREBASE_AUTH_DOMAIN", "FIREBASE_WEB_AUTH_DOMAIN", "JK_FIREBASE_AUTH_DOMAIN")
    if explicit:
        return explicit
    project_id = _firebase_project_id()
    return f"{project_id}.firebaseapp.com" if project_id else ""

def _firebase_presence_safe_key(value: str, fallback: str = "default") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    slug = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:40]
    return f"{slug or fallback}-{digest}"

def _firebase_presence_root_path() -> str:
    root = _env_texto("FIREBASE_RTDB_PRESENCE_ROOT", "JK_FIREBASE_RTDB_PRESENCE_ROOT") or "jk_sistema_presence_v1"
    root = str(root or "").replace("\\", "/").strip("/")
    root = re.sub(r"[.#$\[\]]+", "-", root)
    return root or "jk_sistema_presence_v1"

def _firebase_presence_client_key(client_id: str) -> str:
    return _firebase_presence_safe_key(client_id, "client")

def _firebase_presence_user_key(username: str) -> str:
    return _firebase_presence_safe_key(username, "user")

def _firebase_presence_machine_key_hash(machine_id: str) -> str:
    return _firebase_presence_safe_key(machine_id or "machine", "machine")

def _firebase_project_id() -> str:
    project_id = _env_texto("FIREBASE_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")
    if project_id:
        return project_id
    conta = _firebase_ler_service_account(_firebase_service_account_file())
    return str((conta or {}).get("project_id") or "").strip()

def _firebase_json_service_account_valido(caminho: str) -> bool:
    conta = _firebase_ler_service_account(caminho)
    return bool(
        conta
        and conta.get("type") == "service_account"
        and conta.get("client_email")
        and conta.get("private_key")
    )

def _firebase_ler_service_account(caminho: str) -> Optional[dict]:
    if not caminho or not os.path.exists(caminho):
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            data = json.load(arquivo)
        return data if isinstance(data, dict) else None
    except Exception:
        return None

def _firebase_service_account_candidates() -> list[str]:
    nomes_preferidos = [
        os.path.join(PASTA_INFO, "firebase-service-account.json"),
        os.path.join(PASTA_INFO, "firebase_service_account.json"),
        os.path.join(BASE_DIR, "firebase-service-account.json"),
        os.path.join(BASE_DIR, "firebase_service_account.json"),
    ]
    candidatos = []
    for caminho in nomes_preferidos:
        if caminho and caminho not in candidatos:
            candidatos.append(caminho)

    try:
        for nome in os.listdir(BASE_DIR):
            lower = nome.lower()
            if (
                re.fullmatch(r"jkjkjk-.*\.json", lower)
                or re.search(r"service[-_ ]?account.*\.json$", lower)
                or lower.startswith("firebase-") and lower.endswith(".json")
            ):
                caminho = os.path.join(BASE_DIR, nome)
                if caminho not in candidatos:
                    candidatos.append(caminho)
    except Exception:
        pass
    return candidatos

def _firebase_service_account_file() -> str:
    caminho = _env_texto("FIREBASE_SERVICE_ACCOUNT_FILE", "FIREBASE_CREDENTIALS_FILE")
    if not caminho:
        google_app_credentials = _env_texto("GOOGLE_APPLICATION_CREDENTIALS")
        if google_app_credentials and os.path.exists(google_app_credentials):
            caminho = google_app_credentials
    if caminho and not os.path.isabs(caminho):
        caminho = os.path.join(BASE_DIR, caminho)
    if caminho:
        return caminho
    for candidato in _firebase_service_account_candidates():
        if _firebase_json_service_account_valido(candidato):
            return candidato
    return caminho

def _firebase_tem_configuracao() -> bool:
    if firebase_admin is None or firebase_credentials is None or firebase_firestore is None:
        return False
    if _firebase_access_desativado():
        return False
    if _env_texto("FIREBASE_SERVICE_ACCOUNT_JSON", "FIREBASE_ADMIN_CREDENTIALS_JSON"):
        return True
    if _env_texto("FIREBASE_SERVICE_ACCOUNT_BASE64", "FIREBASE_ADMIN_CREDENTIALS_BASE64"):
        return True
    if _firebase_service_account_file():
        return True
    if str(os.getenv("FIREBASE_USE_APPLICATION_DEFAULT", "") or "").strip().lower() in {"1", "true", "sim", "yes", "on"}:
        return True
    return False

def _firebase_deve_usar() -> bool:
    if _firebase_access_desativado():
        return False
    if _firebase_access_obrigatorio():
        return True
    return _firebase_tem_configuracao()

def _firebase_credencial():
    json_raw = _env_texto("FIREBASE_SERVICE_ACCOUNT_JSON", "FIREBASE_ADMIN_CREDENTIALS_JSON")
    if json_raw:
        try:
            return firebase_credentials.Certificate(json.loads(json_raw))
        except Exception as exc:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_JSON invalido: {exc}")

    json_b64 = _env_texto("FIREBASE_SERVICE_ACCOUNT_BASE64", "FIREBASE_ADMIN_CREDENTIALS_BASE64")
    if json_b64:
        try:
            payload = base64.b64decode(json_b64).decode("utf-8")
            return firebase_credentials.Certificate(json.loads(payload))
        except Exception as exc:
            raise RuntimeError(f"FIREBASE_SERVICE_ACCOUNT_BASE64 invalido: {exc}")

    caminho = _firebase_service_account_file()
    if caminho:
        if not os.path.exists(caminho):
            raise RuntimeError(f"Arquivo Firebase nao encontrado: {caminho}")
        return firebase_credentials.Certificate(caminho)

    if str(os.getenv("FIREBASE_USE_APPLICATION_DEFAULT", "") or "").strip().lower() in {"1", "true", "sim", "yes", "on"}:
        return firebase_credentials.ApplicationDefault()

    raise RuntimeError("Firebase nao configurado. Informe FIREBASE_SERVICE_ACCOUNT_FILE ou FIREBASE_SERVICE_ACCOUNT_JSON.")

def _firebase_app():
    global FIREBASE_AUTH_APP, FIREBASE_AUTH_LAST_ERROR
    if not _firebase_deve_usar():
        return None
    if firebase_admin is None or firebase_credentials is None:
        FIREBASE_AUTH_LAST_ERROR = "Pacote firebase-admin nao instalado."
        if _firebase_access_obrigatorio():
            raise HTTPException(status_code=503, detail=FIREBASE_AUTH_LAST_ERROR)
        return None

    with FIREBASE_AUTH_LOCK:
        if FIREBASE_AUTH_APP is not None:
            return FIREBASE_AUTH_APP
        try:
            project_id = _firebase_project_id()
            options = {}
            if project_id:
                options["projectId"] = project_id
            database_url = _firebase_realtime_database_url()
            if database_url:
                options["databaseURL"] = database_url
            app_name = "jk_sistema_access"
            try:
                app_fb = firebase_admin.get_app(app_name)
            except ValueError:
                cred = _firebase_credencial()
                if options:
                    app_fb = firebase_admin.initialize_app(cred, options=options, name=app_name)
                else:
                    app_fb = firebase_admin.initialize_app(cred, name=app_name)
            FIREBASE_AUTH_APP = app_fb
            FIREBASE_AUTH_LAST_ERROR = ""
            return FIREBASE_AUTH_APP
        except Exception as exc:
            FIREBASE_AUTH_LAST_ERROR = str(exc)
            logger.warning("[FIREBASE-AUTH] Nao foi possivel inicializar Firebase: %s", exc)
            if _firebase_access_obrigatorio():
                raise HTTPException(status_code=503, detail=f"Firebase indisponivel: {exc}")
            return None

def _firebase_db():
    global FIREBASE_AUTH_DB, FIREBASE_AUTH_LAST_ERROR
    if not _firebase_deve_usar():
        return None
    if firebase_firestore is None:
        FIREBASE_AUTH_LAST_ERROR = "Pacote firebase-admin nao instalado."
        if _firebase_access_obrigatorio():
            raise HTTPException(status_code=503, detail=FIREBASE_AUTH_LAST_ERROR)
        return None

    if _env_config_bool(("JK_FIREBASE_TRACE_CALLS",), default=False):
        stack = "".join(traceback.format_stack(limit=8)[:-1])
        logger.warning("[FIREBASE-TRACE] _firebase_db chamado por:\n%s", stack)

    with FIREBASE_AUTH_LOCK:
        if FIREBASE_AUTH_DB is not None:
            return FIREBASE_AUTH_DB
        try:
            app_fb = _firebase_app()
            if app_fb is None:
                return None
            FIREBASE_AUTH_DB = firebase_firestore.client(app_fb)
            FIREBASE_AUTH_LAST_ERROR = ""
            return FIREBASE_AUTH_DB
        except Exception as exc:
            FIREBASE_AUTH_LAST_ERROR = str(exc)
            logger.warning("[FIREBASE-AUTH] Nao foi possivel inicializar Firebase: %s", exc)
            if _firebase_access_obrigatorio():
                raise HTTPException(status_code=503, detail=f"Firebase indisponivel: {exc}")
            return None

def _firebase_doc_id(username: str) -> str:
    username_norm = str(username or "").strip().lower()
    username_norm = username_norm.replace("/", "_")
    return username_norm

def _firebase_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _firebase_bool(valor, padrao: bool = True) -> bool:
    if valor is None:
        return padrao
    if isinstance(valor, str):
        return valor.strip().lower() not in {"0", "false", "falso", "nao", "não", "inativo", "bloqueado", "off"}
    return bool(valor)

def _firebase_user_from_data(username: str, data: dict, index: int = None) -> dict:
    payload = data or {}
    username_norm = str(payload.get("username") or username or "").strip().lower()
    machine_id = str(payload.get("machine_id") or "").strip() or None
    machine_ids = _normalizar_lista_maquinas(
        payload.get("machine_ids") or payload.get("maquinas") or payload.get("machines"),
        machine_id,
    )
    password_hash = str(
        payload.get("password_hash")
        or payload.get("password")
        or payload.get("senha")
        or ""
    ).strip()
    user_number = payload.get("user_number")
    try:
        user_number = int(user_number) if user_number is not None and str(user_number).strip() else None
    except Exception:
        user_number = index
    return {
        "username": username_norm,
        "password": password_hash,
        "name": str(payload.get("name") or payload.get("nome") or username_norm).strip() or username_norm,
        "email": _normalizar_email(payload.get("email") or payload.get("google_email")),
        "empresa": _normalizar_empresa(payload.get("empresa") or payload.get("company") or payload.get("company_name")),
        "client_id": str(payload.get("client_id") or payload.get("cliente") or payload.get("tenant_id") or "default").strip() or "default",
        "permissions": _normalizar_permissoes(payload.get("permissions") or payload.get("permissoes") or {}),
        "original_row": [],
        "row_index": index,
        "source": "firebase",
        "active": _firebase_bool(payload.get("active", payload.get("ativo", True)), True),
        "valid_until": _normalizar_data_sistema(payload.get("valid_until") or payload.get("validade") or payload.get("vencimento")),
        "machine_id": machine_ids[0] if machine_ids else machine_id,
        "machine_ids": machine_ids,
        "max_machines": _normalizar_max_machines(payload.get("max_machines", payload.get("limite_maquinas", 1))),
        "user_number": user_number,
        "firebase_doc_id": _firebase_doc_id(username_norm),
    }

def _firebase_user_to_data(username: str, usuario: dict, *, include_password: bool = True, source: str = "admin") -> dict:
    username_norm = str(username or usuario.get("username") or "").strip().lower()
    machine_ids = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
    data = {
        "username": username_norm,
        "name": str(usuario.get("name") or username_norm).strip() or username_norm,
        "email": _normalizar_email(usuario.get("email") or usuario.get("google_email")),
        "empresa": _normalizar_empresa(usuario.get("empresa") or usuario.get("company") or usuario.get("company_name")),
        "client_id": str(usuario.get("client_id") or "default").strip() or "default",
        "permissions": _normalizar_permissoes(usuario.get("permissions") or {}),
        "active": bool(usuario.get("active", True)),
        "valid_until": _normalizar_data_sistema(usuario.get("valid_until")),
        "machine_id": machine_ids[0] if machine_ids else None,
        "machine_ids": machine_ids,
        "max_machines": _normalizar_max_machines(usuario.get("max_machines", 1)),
        "source": source,
        "updated_at": _firebase_now_iso(),
    }
    if usuario.get("user_number") is not None:
        try:
            data["user_number"] = int(usuario.get("user_number"))
        except Exception:
            pass
    if include_password:
        senha = str(usuario.get("password") or usuario.get("password_hash") or "").strip()
        if senha:
            data["password_hash"] = _hash_password_se_preciso(senha)
    return data

def _firebase_collection():
    db = _firebase_db()
    if db is None:
        return None
    return db.collection(_firebase_users_collection_name())

def _firebase_users_index_doc_id(client_id: str) -> str:
    client_norm = str(client_id or "default").strip() or "default"
    client_norm = client_norm.replace("/", "_").replace("\\", "_")
    client_norm = re.sub(r"[.#$\[\]]+", "_", client_norm)
    return client_norm[:220] or "default"

def _firebase_users_index_ref(client_id: str):
    db = _firebase_db()
    if db is None:
        return None
    return db.collection(_firebase_users_index_collection_name()).document(_firebase_users_index_doc_id(client_id))

def _firebase_user_index_summary(username: str, usuario: dict) -> dict:
    item = usuario if isinstance(usuario, dict) else {}
    username_norm = str(item.get("username") or username or "").strip().lower()
    machine_ids = _normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id"))
    return {
        "username": username_norm,
        "name": str(item.get("name") or username_norm).strip() or username_norm,
        "email": _normalizar_email(item.get("email")),
        "empresa": _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name")),
        "client_id": str(item.get("client_id") or "default").strip() or "default",
        "permissions": _normalizar_permissoes(item.get("permissions") or {}),
        "active": _firebase_bool(item.get("active", True), True),
        "valid_until": _normalizar_data_sistema(item.get("valid_until")),
        "machine_id": machine_ids[0] if machine_ids else item.get("machine_id"),
        "machine_ids": machine_ids,
        "max_machines": _normalizar_max_machines(item.get("max_machines", 1)),
        "user_number": item.get("user_number"),
        "machine_count": len(machine_ids),
        "source": "firebase-index",
    }

def _firebase_users_index_payload(client_id: str, usuarios: dict) -> dict:
    client_norm = str(client_id or "default").strip() or "default"
    users = []
    for username in sorted((usuarios or {}).keys()):
        item = usuarios.get(username) or {}
        item_client = str(item.get("client_id") or "default").strip() or "default"
        if item_client != client_norm:
            continue
        users.append(_firebase_user_index_summary(username, item))
    return {
        "id": _firebase_users_index_doc_id(client_norm),
        "client_id": client_norm,
        "count": len(users),
        "users": users,
        "updated_at": _firebase_now_iso(),
        "updated_ts": int(time.time()),
    }

def _firebase_users_index_save(client_id: str, usuarios: dict) -> bool:
    ref = _firebase_users_index_ref(client_id)
    if ref is None:
        return False
    payload = _firebase_users_index_payload(client_id, usuarios)
    saved = _firebase_noncritical_write(ref.set, payload, merge=False)
    _backend_cache_invalidate_user_views(payload.get("client_id") or client_id)
    return saved

def _firebase_users_index_get(client_id: str, *, raise_on_error: bool = False) -> Optional[dict]:
    client_norm = str(client_id or "default").strip() or "default"
    ref = _firebase_users_index_ref(client_norm)
    if ref is None:
        return None
    try:
        snap = ref.get(retry=None, timeout=_firebase_call_timeout_seconds())
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        users_raw = data.get("users") if isinstance(data.get("users"), list) else []
        usuarios: dict[str, dict] = {}
        for idx, raw in enumerate(users_raw, start=1):
            if not isinstance(raw, dict):
                continue
            username = str(raw.get("username") or "").strip().lower()
            if not username:
                continue
            item = _firebase_user_index_summary(username, raw)
            item["row_index"] = idx
            usuarios[username] = item
        return usuarios
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao ler users_index do cliente %s: %s", client_norm, exc)
        if raise_on_error:
            raise
        return None

def _firebase_users_index_all() -> Optional[dict]:
    db = _firebase_db()
    if db is None:
        return None
    try:
        usuarios: dict[str, dict] = {}
        for snap in db.collection(_firebase_users_index_collection_name()).stream(
            retry=None,
            timeout=_firebase_call_timeout_seconds(),
        ):
            data = snap.to_dict() or {}
            users_raw = data.get("users") if isinstance(data.get("users"), list) else []
            for idx, raw in enumerate(users_raw, start=1):
                if not isinstance(raw, dict):
                    continue
                username = str(raw.get("username") or "").strip().lower()
                if not username:
                    continue
                item = _firebase_user_index_summary(username, raw)
                item["row_index"] = idx
                usuarios[username] = item
        return usuarios if usuarios else None
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao listar users_index: %s", exc)
        return None

def _firebase_users_index_rebuild(usuarios: dict, client_id: Optional[str] = None) -> None:
    if not isinstance(usuarios, dict) or not usuarios:
        return
    if not _firebase_noncritical_write_available():
        return
    clientes = {str(client_id or "").strip()} if client_id else set()
    if not clientes:
        clientes = {
            str((item or {}).get("client_id") or "default").strip() or "default"
            for item in usuarios.values()
            if isinstance(item, dict)
        }
    for client in clientes:
        if client:
            try:
                _firebase_users_index_save(client, usuarios)
            except Exception as exc:
                logger.warning("[FIREBASE-AUTH] Falha ao atualizar users_index/%s: %s", client, exc)

def _firebase_users_index_update_user(username: str, usuario: dict, old_client_id: str = "") -> None:
    username_norm = str(username or "").strip().lower()
    if not username_norm:
        return
    if not _firebase_noncritical_write_available():
        return
    new_client = str((usuario or {}).get("client_id") or "default").strip() or "default"
    old_client = str(old_client_id or "").strip()
    clientes = {new_client}
    if old_client and old_client != new_client:
        clientes.add(old_client)
    for client in clientes:
        usuarios = _firebase_users_index_get(client, raise_on_error=True) or {}
        if client == new_client:
            usuarios[username_norm] = _firebase_user_index_summary(username_norm, usuario)
        else:
            usuarios.pop(username_norm, None)
        _firebase_users_index_save(client, usuarios)

def _firebase_users_index_remove_user(username: str, client_id: str = "") -> None:
    username_norm = str(username or "").strip().lower()
    if not username_norm:
        return
    if not _firebase_noncritical_write_available():
        return
    client_norm = str(client_id or "").strip()
    clientes = [client_norm] if client_norm else []
    if not clientes:
        all_index = _firebase_users_index_all() or {}
        clientes = sorted({
            str((item or {}).get("client_id") or "default").strip() or "default"
            for key, item in all_index.items()
            if str(key or "").strip().lower() == username_norm
        })
    for client in clientes:
        usuarios = _firebase_users_index_get(client, raise_on_error=True) or {}
        if username_norm in usuarios:
            usuarios.pop(username_norm, None)
            _firebase_users_index_save(client, usuarios)

def _firebase_listar_usuarios(
    seed_if_empty: bool = True,
    client_id: Optional[str] = None,
    prefer_index: bool = False,
) -> Optional[dict]:
    if prefer_index:
        usuarios_index = _firebase_users_index_get(client_id) if client_id else _firebase_users_index_all()
        if isinstance(usuarios_index, dict):
            return usuarios_index

    coll = _firebase_collection()
    if coll is None:
        return None
    try:
        docs = list(coll.stream(retry=None, timeout=_firebase_call_timeout_seconds()))
        if not docs and seed_if_empty and str(os.getenv("FIREBASE_SEED_LOCAL_USERS", "true") or "").strip().lower() in {"1", "true", "sim", "yes", "on"}:
            usuarios_seed, _headers_seed = _carregar_usuarios_sql(seed_if_empty=True)
            if isinstance(usuarios_seed, dict) and usuarios_seed:
                for username, usuario in usuarios_seed.items():
                    _firebase_salvar_usuario(username, usuario, source="bootstrap-local")
                docs = list(coll.stream(retry=None, timeout=_firebase_call_timeout_seconds()))

        usuarios = {}
        for idx, doc in enumerate(docs, start=1):
            data = doc.to_dict() or {}
            username = str(data.get("username") or doc.id or "").strip().lower()
            if not username:
                continue
            usuarios[username] = _firebase_user_from_data(username, data, idx)
        if usuarios:
            _salvar_usuarios_sql(usuarios, source="firebase-cache")
            if client_id:
                client_norm = str(client_id or "default").strip() or "default"
                usuarios = {
                    username: item
                    for username, item in usuarios.items()
                    if (str((item or {}).get("client_id") or "default").strip() or "default") == client_norm
                }
        return usuarios
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao listar usuarios: %s", exc)
        if _firebase_access_obrigatorio():
            raise HTTPException(status_code=503, detail=f"Firebase indisponivel: {exc}")
        return None

def _firebase_obter_usuario(username: str) -> Optional[dict]:
    coll = _firebase_collection()
    username_norm = str(username or "").strip().lower()
    if coll is None or not username_norm:
        return None
    cached = _firebase_user_cache_get(username_norm)
    if isinstance(cached, dict):
        return cached
    try:
        snap = coll.document(_firebase_doc_id(username_norm)).get(
            retry=None,
            timeout=_firebase_call_timeout_seconds(),
        )
        if not snap.exists:
            return None
        usuario = _firebase_user_from_data(username_norm, snap.to_dict() or {})
        _firebase_user_cache_set(username_norm, usuario)
        return usuario
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao obter usuario '%s': %s", username_norm, exc)
        if _firebase_access_obrigatorio():
            raise HTTPException(status_code=503, detail=f"Firebase indisponivel: {exc}")
        return None

def _firebase_salvar_usuario(username: str, usuario: dict, *, source: str = "admin", merge: bool = True) -> Optional[dict]:
    coll = _firebase_collection()
    username_norm = str(username or usuario.get("username") or "").strip().lower()
    if coll is None or not username_norm:
        return None
    _firebase_user_cache_invalidate(username_norm)
    data = _firebase_user_to_data(username_norm, usuario, source=source)
    existing_data = {}
    old_client_id = ""
    if merge:
        snap = coll.document(_firebase_doc_id(username_norm)).get(
            retry=None,
            timeout=_firebase_call_timeout_seconds(),
        )
        if snap.exists:
            existing_data = snap.to_dict() or {}
            old_client_id = str(existing_data.get("client_id") or "").strip()
        else:
            data["created_at"] = _firebase_now_iso()
    else:
        data["created_at"] = usuario.get("created_at") or _firebase_now_iso()
    coll.document(_firebase_doc_id(username_norm)).set(
        data,
        merge=merge,
        retry=None,
        timeout=_firebase_call_timeout_seconds(),
    )
    merged_data = dict(existing_data or {})
    if merge:
        merged_data.update(data)
    else:
        merged_data = dict(data)
    salvo = _firebase_user_from_data(username_norm, merged_data)
    if salvo:
        _firebase_user_cache_set(username_norm, salvo)
        _salvar_usuarios_sql({username_norm: salvo}, source="firebase-cache")
        try:
            _firebase_users_index_update_user(username_norm, salvo, old_client_id=old_client_id)
        except Exception as exc:
            logger.warning("[FIREBASE-AUTH] Falha ao atualizar users_index para %s: %s", username_norm, exc)
    return salvo

def _firebase_excluir_usuario(username: str) -> bool:
    coll = _firebase_collection()
    username_norm = str(username or "").strip().lower()
    if coll is None or not username_norm:
        return False
    _firebase_user_cache_invalidate(username_norm)
    client_id = ""
    try:
        snap = coll.document(_firebase_doc_id(username_norm)).get(
            retry=None,
            timeout=_firebase_call_timeout_seconds(),
        )
        if snap.exists:
            client_id = str((snap.to_dict() or {}).get("client_id") or "").strip()
    except Exception:
        client_id = ""
    coll.document(_firebase_doc_id(username_norm)).delete(
        retry=None,
        timeout=_firebase_call_timeout_seconds(),
    )
    try:
        _firebase_users_index_remove_user(username_norm, client_id)
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao remover %s do users_index: %s", username_norm, exc)
    _backend_cache_invalidate_user_views(client_id)
    return True

def _firebase_registrar_login(username: str, client_id: str, machine_id: str, request: Optional[Request], meta: Optional[dict] = None):
    db = _firebase_db()
    if db is None:
        return
    try:
        meta = meta or {}
        payload = {
            "username": str(username or "").strip().lower(),
            "client_id": str(client_id or "default").strip() or "default",
            "machine_id": str(machine_id or "").strip(),
            "ip_address": meta.get("ip_address") or _extrair_ip_request(request),
            "user_agent": meta.get("user_agent") or str((request.headers.get("user-agent") if request else "") or "")[:500],
            "created_at": _firebase_now_iso(),
        }
        _firebase_noncritical_write(db.collection(_firebase_audit_collection_name()).add, payload)
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Nao foi possivel registrar auditoria no Firebase: %s", exc)


__all__ = [
    "configure_admin_usuarios_firebase_runtime",
    "_firebase_access_mode",
    "_firebase_access_obrigatorio",
    "_firebase_access_desativado",
    "_firebase_call_timeout_seconds",
    "_firebase_quota_cooldown_seconds",
    "_firebase_user_cache_seconds",
    "_firebase_quota_excedida",
    "_firebase_noncritical_write_available",
    "_firebase_noncritical_write",
    "_firebase_user_cache_get",
    "_firebase_user_cache_set",
    "_firebase_user_cache_invalidate",
    "_firebase_users_collection_name",
    "_firebase_users_index_collection_name",
    "_firebase_audit_collection_name",
    "_firebase_presence_collection_name",
    "_firebase_admin_messages_collection_name",
    "_firebase_user_chat_collection_name",
    "_firebase_user_chat_typing_collection_name",
    "_firebase_user_status_collection_name",
    "_firebase_live_features_ativas",
    "_firebase_realtime_database_url",
    "_firebase_web_api_key",
    "_firebase_web_app_id",
    "_firebase_web_auth_domain",
    "_firebase_presence_safe_key",
    "_firebase_presence_root_path",
    "_firebase_presence_client_key",
    "_firebase_presence_user_key",
    "_firebase_presence_machine_key_hash",
    "_firebase_project_id",
    "_firebase_json_service_account_valido",
    "_firebase_ler_service_account",
    "_firebase_service_account_candidates",
    "_firebase_service_account_file",
    "_firebase_tem_configuracao",
    "_firebase_deve_usar",
    "_firebase_credencial",
    "_firebase_app",
    "_firebase_db",
    "_firebase_doc_id",
    "_firebase_now_iso",
    "_firebase_bool",
    "_firebase_user_from_data",
    "_firebase_user_to_data",
    "_firebase_collection",
    "_firebase_users_index_doc_id",
    "_firebase_users_index_ref",
    "_firebase_user_index_summary",
    "_firebase_users_index_payload",
    "_firebase_users_index_save",
    "_firebase_users_index_get",
    "_firebase_users_index_all",
    "_firebase_users_index_rebuild",
    "_firebase_users_index_update_user",
    "_firebase_users_index_remove_user",
    "_firebase_listar_usuarios",
    "_firebase_obter_usuario",
    "_firebase_salvar_usuario",
    "_firebase_excluir_usuario",
    "_firebase_registrar_login",
]
