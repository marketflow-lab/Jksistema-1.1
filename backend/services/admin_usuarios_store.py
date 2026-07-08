"""Internal helpers for admin usuarios store."""

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


def configure_admin_usuarios_store_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_store_runtime()

def _auth_db_conexao():
    os.makedirs(PASTA_INFO, exist_ok=True)
    conn = sqlite3.connect(ARQUIVO_AUTH_DB)
    conn.row_factory = sqlite3.Row
    return conn

def _init_auth_db():
    conn = _auth_db_conexao()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usuarios_auth (
                username TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                empresa TEXT,
                client_id TEXT NOT NULL,
                permissions_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                valid_until TEXT,
                machine_id TEXT,
                max_machines INTEGER NOT NULL DEFAULT 1,
                machine_ids_json TEXT NOT NULL DEFAULT '[]',
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                user_number INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS login_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                client_id TEXT,
                machine_id TEXT,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        colunas = {str(row['name']) for row in conn.execute("PRAGMA table_info(usuarios_auth)").fetchall()}
        if 'email' not in colunas:
            cur.execute("ALTER TABLE usuarios_auth ADD COLUMN email TEXT")
        if 'empresa' not in colunas:
            cur.execute("ALTER TABLE usuarios_auth ADD COLUMN empresa TEXT")
        if 'max_machines' not in colunas:
            cur.execute("ALTER TABLE usuarios_auth ADD COLUMN max_machines INTEGER NOT NULL DEFAULT 1")
        if 'machine_ids_json' not in colunas:
            cur.execute("ALTER TABLE usuarios_auth ADD COLUMN machine_ids_json TEXT NOT NULL DEFAULT '[]'")
        if 'user_number' not in colunas:
            cur.execute("ALTER TABLE usuarios_auth ADD COLUMN user_number INTEGER")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_auth_email ON usuarios_auth(email)")
        conn.commit()
    finally:
        conn.close()

def _auth_db_tem_usuarios() -> bool:
    _init_auth_db()
    conn = _auth_db_conexao()
    try:
        row = conn.execute("SELECT COUNT(1) AS total FROM usuarios_auth").fetchone()
        return bool(int((row["total"] if row else 0) or 0) > 0)
    finally:
        conn.close()

def _salvar_usuarios_sql(usuarios: dict, source: str = "importado"):
    if not isinstance(usuarios, dict) or not usuarios:
        return
    _init_auth_db()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _auth_db_conexao()
    try:
        cur = conn.cursor()
        for username, item in usuarios.items():
            if not isinstance(item, dict):
                continue
            username_norm = str(username or "").strip().lower()
            senha = _hash_password_se_preciso(item.get("password"))
            if not username_norm or not senha:
                continue
            nome = str(item.get("name") or username_norm).strip()
            email = _normalizar_email(item.get("email") or item.get("google_email"))
            empresa = _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name"))
            client_id = str(item.get("client_id") or "default").strip() or "default"
            permissoes = _normalizar_permissoes(item.get("permissions") or {})
            active = 1 if item.get("active", True) else 0
            valid_until = _normalizar_data_sistema(item.get("valid_until"))
            machine_id = str(item.get("machine_id") or "").strip() or None
            machine_ids = _normalizar_lista_maquinas(item.get("machine_ids"), machine_id)
            max_machines = _normalizar_max_machines(item.get("max_machines", 1))
            cur.execute(
                """
                INSERT INTO usuarios_auth (
                    username, password, name, email, empresa, client_id, permissions_json,
                    active, valid_until, machine_id, max_machines, machine_ids_json,
                    source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password=excluded.password,
                    name=excluded.name,
                    email=excluded.email,
                    empresa=excluded.empresa,
                    client_id=excluded.client_id,
                    permissions_json=excluded.permissions_json,
                    active=excluded.active,
                    valid_until=excluded.valid_until,
                    machine_id=excluded.machine_id,
                    max_machines=excluded.max_machines,
                    machine_ids_json=excluded.machine_ids_json,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    username_norm,
                    senha,
                    nome,
                    email,
                    empresa,
                    client_id,
                    json.dumps(permissoes, ensure_ascii=False),
                    active,
                    valid_until,
                    machine_id,
                    max_machines,
                    json.dumps(machine_ids, ensure_ascii=False),
                    source,
                    agora,
                    agora,
                ),
            )
            cur.execute(
                "UPDATE usuarios_auth SET user_number = (SELECT COALESCE(MAX(user_number), 0) + 1 FROM usuarios_auth) WHERE username = ? AND user_number IS NULL",
                (username_norm,)
            )
        conn.commit()
    finally:
        conn.close()

def _carregar_usuarios_sql(seed_if_empty: bool = True):
    _init_auth_db()

    if seed_if_empty and not _auth_db_tem_usuarios():
        usuarios_cache, _headers_cache = _carregar_cache_usuarios()
        if isinstance(usuarios_cache, dict) and usuarios_cache:
            _salvar_usuarios_sql(usuarios_cache, source="cache")
        else:
            usuarios_local, _headers_local = _carregar_usuarios_local()
            if isinstance(usuarios_local, dict) and usuarios_local:
                _salvar_usuarios_sql(usuarios_local, source="local")

    conn = _auth_db_conexao()
    try:
        rows = conn.execute(
            """
            SELECT username, password, name, email, empresa, client_id, permissions_json,
                   active, valid_until, machine_id, max_machines, machine_ids_json, source, user_number
            FROM usuarios_auth
            ORDER BY user_number, username
            """
        ).fetchall()
        if not rows:
            return None, []

        usuarios = {}
        cache_lookup_loaded = False
        cache_usuarios = {}
        cache_headers = []
        local_lookup_loaded = False
        local_usuarios = {}
        local_headers = []
        permissoes_reparadas = 0

        def _fallback_permissoes_armazenadas(username_norm: str) -> Optional[dict]:
            nonlocal cache_lookup_loaded, cache_usuarios, cache_headers, local_lookup_loaded, local_usuarios, local_headers

            if not cache_lookup_loaded:
                cache_lookup_loaded = True
                cache_usuarios, cache_headers = _carregar_cache_usuarios()
                if not isinstance(cache_usuarios, dict):
                    cache_usuarios = {}
                    cache_headers = []

            item_cache = cache_usuarios.get(username_norm) if isinstance(cache_usuarios, dict) else None
            if isinstance(item_cache, dict):
                if isinstance(item_cache.get("permissions"), dict):
                    permissoes_cache = _normalizar_permissoes(item_cache.get("permissions") or {})
                    if any(permissoes_cache.values()):
                        return permissoes_cache
                if item_cache.get("original_row"):
                    permissoes_cache = extrair_permissoes(item_cache.get("original_row") or [], cache_headers or [])
                    if any(permissoes_cache.values()):
                        return permissoes_cache

            if not local_lookup_loaded:
                local_lookup_loaded = True
                local_usuarios, local_headers = _carregar_usuarios_local()
                if not isinstance(local_usuarios, dict):
                    local_usuarios = {}
                    local_headers = []

            item_local = local_usuarios.get(username_norm) if isinstance(local_usuarios, dict) else None
            if isinstance(item_local, dict) and isinstance(item_local.get("permissions"), dict):
                permissoes_local = _normalizar_permissoes(item_local.get("permissions") or {})
                if any(permissoes_local.values()):
                    return permissoes_local

            return None

        for i, row in enumerate(rows, start=1):
            try:
                permissoes = json.loads(str(row["permissions_json"] or "{}"))
            except Exception:
                permissoes = {}
            username_norm = str(row["username"] or "").strip().lower()
            permissoes_norm = _normalizar_permissoes(permissoes)
            if not any(permissoes_norm.values()):
                permissoes_fallback = _fallback_permissoes_armazenadas(username_norm)
                if isinstance(permissoes_fallback, dict) and any(permissoes_fallback.values()):
                    permissoes_norm = _normalizar_permissoes(permissoes_fallback)
                    conn.execute(
                        "UPDATE usuarios_auth SET permissions_json = ?, updated_at = ? WHERE username = ?",
                        (
                            json.dumps(permissoes_norm, ensure_ascii=False),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            username_norm,
                        )
                    )
                    permissoes_reparadas += 1
            machine_id = str(row["machine_id"] or "").strip() or None
            machine_ids = _normalizar_lista_maquinas(row["machine_ids_json"], machine_id)
            usuarios[username_norm] = {
                "password": str(row["password"] or ""),
                "name": str(row["name"] or row["username"] or ""),
                "email": _normalizar_email(row["email"]),
                "empresa": _normalizar_empresa(row["empresa"]),
                "client_id": str(row["client_id"] or "default"),
                "permissions": permissoes_norm,
                "original_row": [],
                "row_index": i,
                "source": str(row["source"] or "sql"),
                "active": bool(int(row["active"] or 0)),
                "valid_until": str(row["valid_until"] or "").strip() or None,
                "machine_id": machine_id,
                "machine_ids": machine_ids,
                "max_machines": _normalizar_max_machines(row["max_machines"]),
                "user_number": int(row["user_number"]) if row["user_number"] is not None else None,
            }
        if permissoes_reparadas:
            conn.commit()
            logger.warning("[LOGIN] Permissoes SQL reparadas a partir do cache/local: %s usuario(s).", permissoes_reparadas)
        return usuarios, []
    finally:
        conn.close()

def _listar_usuarios_admin_sql(
    return_backend: bool = False,
    client_id: Optional[str] = None,
    prefer_index: bool = True,
):
    firebase_fallback_detail = ""
    usuarios_fb = None
    client_norm = str(client_id or "").strip()
    if _firebase_deve_usar():
        try:
            usuarios_fb = _firebase_listar_usuarios(seed_if_empty=True, client_id=client_norm or None, prefer_index=prefer_index)
        except HTTPException as exc:
            if not _firebase_http_exception_permite_fallback(exc):
                raise
            firebase_fallback_detail = str(exc.detail or "Firebase indisponivel.")
            logger.warning("[FIREBASE-AUTH] Listagem de usuarios usando fallback local: %s", firebase_fallback_detail)

    if isinstance(usuarios_fb, dict) and usuarios_fb:
        resultado = []
        for username in sorted(usuarios_fb.keys()):
            item = usuarios_fb.get(username) or {}
            item_client = str(item.get("client_id") or "default").strip() or "default"
            if client_norm and item_client != client_norm:
                continue
            resultado.append({
                "username": username,
                "name": str(item.get("name") or username),
                "email": _normalizar_email(item.get("email")),
                "empresa": _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name")),
                "client_id": item_client,
                "permissions": _normalizar_permissoes(item.get("permissions") or {}),
                "active": bool(item.get("active", True)),
                "valid_until": item.get("valid_until"),
                "machine_id": item.get("machine_id"),
                "machine_ids": _normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id")),
                "max_machines": _normalizar_max_machines(item.get("max_machines", 1)),
                "source": "firebase",
                "user_number": item.get("user_number"),
                "machine_count": len(_normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id"))),
            })
        return (resultado, "firebase") if return_backend else resultado

    usuarios_sql, _headers_sql = _carregar_usuarios_sql(seed_if_empty=True)
    if not isinstance(usuarios_sql, dict):
        backend = "firebase" if isinstance(usuarios_fb, dict) else "local"
        return ([], backend) if return_backend else []

    resultado = []
    for username in sorted(usuarios_sql.keys()):
        item = usuarios_sql.get(username) or {}
        item_client = str(item.get("client_id") or "default").strip() or "default"
        if client_norm and item_client != client_norm:
            continue
        resultado.append({
                "username": username,
                "name": str(item.get("name") or username),
                "email": _normalizar_email(item.get("email")),
                "empresa": _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name")),
                "client_id": item_client,
            "permissions": _normalizar_permissoes(item.get("permissions") or {}),
            "active": bool(item.get("active", True)),
            "valid_until": item.get("valid_until"),
            "machine_id": item.get("machine_id"),
            "machine_ids": _normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id")),
            "max_machines": _normalizar_max_machines(item.get("max_machines", 1)),
            "source": item.get("source") or "sql",
            "user_number": item.get("user_number"),
            "machine_count": len(_normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id"))),
        })
    backend = "local-fallback" if (firebase_fallback_detail or isinstance(usuarios_fb, dict)) else "local"
    return (resultado, backend) if return_backend else resultado

def _obter_usuario_sql(username: str) -> dict:
    username_norm = str(username or "").strip().lower()
    firebase_fallback_detail = ""
    if _firebase_deve_usar():
        try:
            usuario_fb = _firebase_obter_usuario(username_norm)
        except HTTPException as exc:
            if not _firebase_http_exception_permite_fallback(exc):
                raise
            firebase_fallback_detail = str(exc.detail or "Firebase indisponivel.")
            logger.warning("[FIREBASE-AUTH] Usuario '%s' usando fallback local: %s", username_norm, firebase_fallback_detail)
            usuario_fb = None
        if isinstance(usuario_fb, dict):
            return {
                "username": username_norm,
                "password": usuario_fb.get("password") or "",
                "name": str(usuario_fb.get("name") or username_norm),
                "email": _normalizar_email(usuario_fb.get("email")),
                "empresa": _normalizar_empresa(usuario_fb.get("empresa") or usuario_fb.get("company") or usuario_fb.get("company_name")),
                "client_id": str(usuario_fb.get("client_id") or "default"),
                "permissions": _normalizar_permissoes(usuario_fb.get("permissions") or {}),
                "active": bool(usuario_fb.get("active", True)),
                "valid_until": _normalizar_data_sistema(usuario_fb.get("valid_until")),
                "machine_id": usuario_fb.get("machine_id"),
                "machine_ids": _normalizar_lista_maquinas(usuario_fb.get("machine_ids"), usuario_fb.get("machine_id")),
                "max_machines": _normalizar_max_machines(usuario_fb.get("max_machines", 1)),
                "source": "firebase",
                "user_number": usuario_fb.get("user_number"),
            }
        if _firebase_access_obrigatorio() and not firebase_fallback_detail:
            raise HTTPException(status_code=404, detail="Usuario nao encontrado no Firebase.")

    usuarios_sql, _headers_sql = _carregar_usuarios_sql(seed_if_empty=True)
    if not isinstance(usuarios_sql, dict) or username_norm not in usuarios_sql:
        raise HTTPException(status_code=404, detail="UsuÃƒÂ¡rio nÃ£o encontrado.")
    item = usuarios_sql.get(username_norm) or {}
    return {
        "username": username_norm,
        "password": item.get("password") or "",
        "name": str(item.get("name") or username_norm),
        "email": _normalizar_email(item.get("email")),
        "empresa": _normalizar_empresa(item.get("empresa") or item.get("company") or item.get("company_name")),
        "client_id": str(item.get("client_id") or "default"),
        "permissions": _normalizar_permissoes(item.get("permissions") or {}),
        "active": bool(item.get("active", True)),
        "valid_until": _normalizar_data_sistema(item.get("valid_until")),
        "machine_id": item.get("machine_id"),
        "machine_ids": _normalizar_lista_maquinas(item.get("machine_ids"), item.get("machine_id")),
        "max_machines": _normalizar_max_machines(item.get("max_machines", 1)),
        "source": item.get("source") or "sql",
        "user_number": item.get("user_number"),
    }

def _salvar_usuario_admin_firebase(payload: AdminUserUpsertRequest) -> dict:
    username_norm = str(payload.username or "").strip().lower()
    original_username = str(payload.original_username or username_norm).strip().lower()
    if not username_norm:
        raise HTTPException(status_code=400, detail="Informe o usuario.")

    existente = _firebase_obter_usuario(original_username) if original_username else None
    if existente and original_username != username_norm and _firebase_obter_usuario(username_norm):
        raise HTTPException(status_code=400, detail="Ja existe outro usuario com esse login.")

    email_norm = _normalizar_email(payload.email if payload.email is not None else ((existente or {}).get("email") if existente else ""))
    if email_norm:
        email_client_id = str(payload.client_id or ((existente or {}).get("client_id") if existente else "default")).strip() or "default"
        usuarios_fb = _firebase_listar_usuarios(seed_if_empty=True, client_id=email_client_id, prefer_index=True) or {}
        for usuario_chave, usuario_item in usuarios_fb.items():
            if str(usuario_chave or "").strip().lower() == original_username:
                continue
            if _normalizar_email((usuario_item or {}).get("email")) == email_norm:
                raise HTTPException(status_code=400, detail="Ja existe outro usuario vinculado a esse e-mail Google.")

    senha_final = str(payload.password or "").strip() or ((existente or {}).get("password") if existente else "")
    if not senha_final:
        raise HTTPException(status_code=400, detail="Informe a senha para criar o usuario.")

    machine_principal = str(payload.machine_id or ((existente or {}).get("machine_id") if existente else "")).strip() or None
    machine_ids_existentes = ((existente or {}).get("machine_ids") if existente else []) or []
    if payload.machine_id is not None and str(payload.machine_id).strip() == "":
        machine_ids_existentes = []
    elif machine_principal:
        machine_ids_existentes = _normalizar_lista_maquinas(machine_ids_existentes, machine_principal)

    registro = {
        "username": username_norm,
        "password": senha_final,
        "name": str(payload.name or ((existente or {}).get("name") if existente else username_norm)).strip() or username_norm,
        "email": email_norm,
        "empresa": _normalizar_empresa(payload.empresa if _pydantic_campo_enviado(payload, "empresa") else ((existente or {}).get("empresa") if existente else "")),
        "client_id": str(payload.client_id or ((existente or {}).get("client_id") if existente else "default")).strip() or "default",
        "permissions": _normalizar_permissoes(payload.permissions or ((existente or {}).get("permissions") if existente else {})),
        "active": bool(payload.active),
        "valid_until": _normalizar_data_sistema(payload.valid_until),
        "machine_id": machine_principal,
        "machine_ids": machine_ids_existentes,
        "max_machines": _normalizar_max_machines(payload.max_machines if payload.max_machines is not None else ((existente or {}).get("max_machines") if existente else 1)),
        "user_number": (existente or {}).get("user_number"),
    }

    if existente and original_username != username_norm:
        _firebase_excluir_usuario(original_username)
        try:
            conn = _auth_db_conexao()
            try:
                conn.execute("DELETE FROM usuarios_auth WHERE username = ?", (original_username,))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    usuario = _firebase_salvar_usuario(username_norm, registro, source="firebase-admin")
    if not isinstance(usuario, dict):
        raise HTTPException(status_code=503, detail="Firebase nao configurado para salvar usuarios.")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(username_norm)

def _salvar_usuario_admin_sql(payload: AdminUserUpsertRequest) -> dict:
    if _firebase_deve_usar():
        return _salvar_usuario_admin_firebase(payload)

    username_norm = str(payload.username or "").strip().lower()
    original_username = str(payload.original_username or username_norm).strip().lower()
    if not username_norm:
        raise HTTPException(status_code=400, detail="Informe o usuÃƒÂ¡rio.")

    existente = None
    try:
        existente = _obter_usuario_sql(original_username)
    except HTTPException:
        existente = None

    if existente and original_username != username_norm:
        try:
            _obter_usuario_sql(username_norm)
            raise HTTPException(status_code=400, detail="JÃƒÂ¡ existe outro usuÃƒÂ¡rio com esse login.")
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

    email_norm = _normalizar_email(payload.email if payload.email is not None else (existente.get("email") if existente else ""))
    if email_norm:
        usuarios_sql, _headers_sql = _carregar_usuarios_sql(seed_if_empty=True)
        if isinstance(usuarios_sql, dict):
            for usuario_chave, usuario_item in usuarios_sql.items():
                if str(usuario_chave or "").strip().lower() == original_username:
                    continue
                if _normalizar_email((usuario_item or {}).get("email")) == email_norm:
                    raise HTTPException(status_code=400, detail="JÃƒÂ¡ existe outro usuÃƒÂ¡rio vinculado a esse e-mail Google.")

    senha_final = str(payload.password or "").strip() or (existente.get("password") if existente else "")
    if not senha_final:
        raise HTTPException(status_code=400, detail="Informe a senha para criar o usuÃƒÂ¡rio.")

    machine_principal = str(payload.machine_id or (existente.get("machine_id") if existente else "")).strip() or None
    machine_ids_existentes = (existente.get("machine_ids") if existente else []) or []
    if payload.machine_id is not None and str(payload.machine_id).strip() == "":
        machine_ids_existentes = []
    elif machine_principal:
        machine_ids_existentes = _normalizar_lista_maquinas(machine_ids_existentes, machine_principal)

    registro = {
        "password": senha_final,
        "name": str(payload.name or username_norm).strip() or username_norm,
        "email": email_norm,
        "empresa": _normalizar_empresa(payload.empresa if _pydantic_campo_enviado(payload, "empresa") else (existente.get("empresa") if existente else "")),
        "client_id": str(payload.client_id or (existente.get("client_id") if existente else "default")).strip() or "default",
        "permissions": _normalizar_permissoes(payload.permissions or (existente.get("permissions") if existente else {})),
        "active": bool(payload.active),
        "valid_until": _normalizar_data_sistema(payload.valid_until),
        "machine_id": machine_principal,
        "machine_ids": machine_ids_existentes,
        "max_machines": _normalizar_max_machines(payload.max_machines if payload.max_machines is not None else (existente.get("max_machines") if existente else 1)),
    }

    if existente and original_username != username_norm:
        conn = _auth_db_conexao()
        try:
            conn.execute("DELETE FROM usuarios_auth WHERE username = ?", (original_username,))
            conn.commit()
        finally:
            conn.close()

    _salvar_usuarios_sql({username_norm: registro}, source="sql-admin")
    _backend_cache_invalidate_user_views(registro.get("client_id"))
    return _obter_usuario_sql(username_norm)

def _atualizar_senha_usuario_sql(username: str, password: str) -> dict:
    usuario = _obter_usuario_sql(username)
    nova_senha = str(password or "").strip()
    if not nova_senha:
        raise HTTPException(status_code=400, detail="Informe a nova senha.")
    usuario["password"] = nova_senha
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        salvo = _firebase_salvar_usuario(usuario["username"], usuario, source="firebase-admin")
        _backend_cache_invalidate_user_views((salvo or usuario).get("client_id"))
        return _obter_usuario_sql(salvo["username"] if salvo else usuario["username"])
    _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-admin")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(usuario["username"])

def _atualizar_permissoes_usuario_sql(username: str, permissions: dict) -> dict:
    usuario = _obter_usuario_sql(username)
    usuario["permissions"] = _normalizar_permissoes(permissions or {})
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        salvo = _firebase_salvar_usuario(usuario["username"], usuario, source="firebase-admin")
        _backend_cache_invalidate_user_views((salvo or usuario).get("client_id"))
        return _obter_usuario_sql(salvo["username"] if salvo else usuario["username"])
    _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-admin")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(usuario["username"])

def _atualizar_status_usuario_sql(username: str, active: bool) -> dict:
    usuario = _obter_usuario_sql(username)
    usuario["active"] = bool(active)
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        salvo = _firebase_salvar_usuario(usuario["username"], usuario, source="firebase-admin")
        _backend_cache_invalidate_user_views((salvo or usuario).get("client_id"))
        return _obter_usuario_sql(salvo["username"] if salvo else usuario["username"])
    _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-admin")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(usuario["username"])

def _resetar_maquinas_usuario_sql(username: str) -> dict:
    usuario = _obter_usuario_sql(username)
    usuario["machine_id"] = None
    usuario["machine_ids"] = []
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        salvo = _firebase_salvar_usuario(usuario["username"], usuario, source="firebase-admin")
        _backend_cache_invalidate_user_views((salvo or usuario).get("client_id"))
        return _obter_usuario_sql(salvo["username"] if salvo else usuario["username"])
    _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-admin")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(usuario["username"])

def _atualizar_max_machines_usuario_sql(username: str, max_machines: int) -> dict:
    usuario = _obter_usuario_sql(username)
    usuario["max_machines"] = _normalizar_max_machines(max_machines)
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        salvo = _firebase_salvar_usuario(usuario["username"], usuario, source="firebase-admin")
        _backend_cache_invalidate_user_views((salvo or usuario).get("client_id"))
        return _obter_usuario_sql(salvo["username"] if salvo else usuario["username"])
    _salvar_usuarios_sql({usuario["username"]: usuario}, source="sql-admin")
    _backend_cache_invalidate_user_views(usuario.get("client_id"))
    return _obter_usuario_sql(usuario["username"])

def _remover_usuario_sql(username: str) -> None:
    usuario = _obter_usuario_sql(username)
    if _firebase_deve_usar() and usuario.get("source") == "firebase":
        _firebase_excluir_usuario(usuario["username"])
        _backend_cache_invalidate_user_views(usuario.get("client_id"))
        try:
            conn = _auth_db_conexao()
            try:
                conn.execute("DELETE FROM usuarios_auth WHERE username = ?", (usuario["username"],))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        return
    conn = _auth_db_conexao()
    try:
        conn.execute("DELETE FROM usuarios_auth WHERE username = ?", (usuario["username"],))
        conn.commit()
    finally:
        conn.close()
    _backend_cache_invalidate_user_views(usuario.get("client_id"))

def _resumo_usuario_admin(usuario: dict) -> dict:
    machine_ids = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
    return {
        "username": usuario.get("username"),
        "name": usuario.get("name"),
        "email": _normalizar_email(usuario.get("email")),
        "empresa": _normalizar_empresa(usuario.get("empresa") or usuario.get("company") or usuario.get("company_name")),
        "client_id": usuario.get("client_id"),
        "permissions": _normalizar_permissoes(usuario.get("permissions") or {}),
        "active": bool(usuario.get("active", True)),
        "valid_until": _normalizar_data_sistema(usuario.get("valid_until")),
        "machine_id": usuario.get("machine_id"),
        "machine_ids": machine_ids,
        "machine_count": len(machine_ids),
        "max_machines": _normalizar_max_machines(usuario.get("max_machines", 1)),
        "source": usuario.get("source") or "sql-admin",
        "user_number": usuario.get("user_number"),
    }


__all__ = [
    "configure_admin_usuarios_store_runtime",
    "_auth_db_conexao",
    "_init_auth_db",
    "_auth_db_tem_usuarios",
    "_salvar_usuarios_sql",
    "_carregar_usuarios_sql",
    "_listar_usuarios_admin_sql",
    "_obter_usuario_sql",
    "_salvar_usuario_admin_firebase",
    "_salvar_usuario_admin_sql",
    "_atualizar_senha_usuario_sql",
    "_atualizar_permissoes_usuario_sql",
    "_atualizar_status_usuario_sql",
    "_resetar_maquinas_usuario_sql",
    "_atualizar_max_machines_usuario_sql",
    "_remover_usuario_sql",
    "_resumo_usuario_admin",
]
