"""Google Drive backup/sync helpers for Configuracoes."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta
from typing import Callable, Optional
from urllib.parse import quote

import requests
from fastapi import HTTPException

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:
    Fernet = None
    InvalidToken = Exception


DRIVE_SYNC_FOLDER_ROOT = "JK Sistema Backups"
DRIVE_SYNC_FILE_EXT = ".jksync"
DRIVE_SYNC_ALLOWED_EXTENSIONS = {".json", ".csv", ".db", ".sqlite", ".sqlite3"}
DRIVE_SYNC_MAX_FILE_BYTES = 80 * 1024 * 1024
DRIVE_SYNC_EXCLUDED_DIRS = {"__pycache__", "_drive_restore_backup", "_shared_sync_backups"}
DRIVE_SYNC_EXCLUDED_REL_PREFIXES = (
    "codex_assistant/cache",
    "favoritos_ml_cache",
)

logger = logging.getLogger("jk_sistema")
_payload_sessao_por_authorization: Callable[[Optional[str]], dict] = lambda authorization: {}
carregar_usuarios_sheets: Callable[[], tuple] = lambda: ({}, None, [])
_normalizar_email: Callable[[object], str] = lambda valor: str(valor or "").strip().lower()
_google_oauth_carregar_tokens: Callable[[object, object], Optional[dict]] = lambda username, client_id: None
_google_oauth_salvar_tokens_usuario: Callable[[object, object, object, dict], None] = lambda username, client_id, email, payload: None
_google_oauth_parse_expiry: Callable[[object], Optional[datetime]] = lambda expires_at: None
_google_login_client_id: Callable[[], str] = lambda: ""
_google_login_client_secret: Callable[[], str] = lambda: ""
get_tenant_path: Callable[[object], str] = lambda client_id: ""


def configure_configuracoes_drive_sync_context(
    *,
    logger_ref=None,
    payload_sessao_por_authorization: Callable[[Optional[str]], dict] | None = None,
    carregar_usuarios_sheets_fn: Callable[[], tuple] | None = None,
    normalizar_email_fn: Callable[[object], str] | None = None,
    google_oauth_carregar_tokens: Callable[[object, object], Optional[dict]] | None = None,
    google_oauth_salvar_tokens_usuario: Callable[[object, object, object, dict], None] | None = None,
    google_oauth_parse_expiry: Callable[[object], Optional[datetime]] | None = None,
    google_login_client_id: Callable[[], str] | None = None,
    google_login_client_secret: Callable[[], str] | None = None,
    get_tenant_path_fn: Callable[[object], str] | None = None,
) -> None:
    global logger, _payload_sessao_por_authorization, carregar_usuarios_sheets
    global _normalizar_email, _google_oauth_carregar_tokens, _google_oauth_salvar_tokens_usuario
    global _google_oauth_parse_expiry, _google_login_client_id, _google_login_client_secret, get_tenant_path

    if logger_ref is not None:
        logger = logger_ref
    if payload_sessao_por_authorization is not None:
        _payload_sessao_por_authorization = payload_sessao_por_authorization
    if carregar_usuarios_sheets_fn is not None:
        carregar_usuarios_sheets = carregar_usuarios_sheets_fn
    if normalizar_email_fn is not None:
        _normalizar_email = normalizar_email_fn
    if google_oauth_carregar_tokens is not None:
        _google_oauth_carregar_tokens = google_oauth_carregar_tokens
    if google_oauth_salvar_tokens_usuario is not None:
        _google_oauth_salvar_tokens_usuario = google_oauth_salvar_tokens_usuario
    if google_oauth_parse_expiry is not None:
        _google_oauth_parse_expiry = google_oauth_parse_expiry
    if google_login_client_id is not None:
        _google_login_client_id = google_login_client_id
    if google_login_client_secret is not None:
        _google_login_client_secret = google_login_client_secret
    if get_tenant_path_fn is not None:
        get_tenant_path = get_tenant_path_fn


def _drive_sync_contexto_usuario(authorization: Optional[str]) -> dict:
    payload = _payload_sessao_por_authorization(authorization)
    username = str(payload.get("username") or "").strip().lower()
    client_id = str(payload.get("client_id") or "").strip() or "default"

    usuarios, headers, _ = carregar_usuarios_sheets()
    usuario = (usuarios or {}).get(username) if isinstance(usuarios, dict) else None
    if not isinstance(usuario, dict):
        raise HTTPException(status_code=401, detail="Usuario da sessao nao encontrado. Faca login novamente.")
    client_usuario = str(usuario.get("client_id") or client_id or "default").strip() or "default"
    if client_usuario != client_id:
        raise HTTPException(status_code=403, detail="Token invalido para o cliente autenticado.")
    return {
        "username": username,
        "client_id": client_id,
        "email": _normalizar_email(usuario.get("email") or usuario.get("google_email")),
        "user": usuario,
    }

def _google_drive_token_linkado(ctx: dict) -> Optional[dict]:
    return _google_oauth_carregar_tokens(ctx.get("username"), ctx.get("client_id"))

def _google_drive_refresh_access_token(ctx: dict, registro: Optional[dict] = None) -> str:
    registro = registro or _google_drive_token_linkado(ctx)
    refresh_token = str((registro or {}).get("refresh_token") or "").strip()
    if not refresh_token:
        raise HTTPException(
            status_code=403,
            detail="Drive nao vinculado. Entre pelo botao Google uma vez para autorizar o backup automatico.",
        )

    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": _google_login_client_id(),
                "client_secret": _google_login_client_secret(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=25,
        )
        payload = resp.json() if resp.content else {}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Nao foi possivel renovar o acesso ao Google Drive: {exc}")

    if resp.status_code != 200:
        detalhe = ""
        if isinstance(payload, dict):
            detalhe = str(payload.get("error_description") or payload.get("error") or "").strip()
        raise HTTPException(status_code=502, detail=detalhe or "Google nao renovou o acesso ao Drive.")

    payload["refresh_token"] = refresh_token
    _google_oauth_salvar_tokens_usuario(
        ctx.get("username"),
        ctx.get("client_id"),
        (registro or {}).get("google_email") or ctx.get("email"),
        payload,
    )
    return str(payload.get("access_token") or "").strip()

def _google_drive_access_token(ctx: dict) -> str:
    registro = _google_drive_token_linkado(ctx)
    if not registro:
        raise HTTPException(
            status_code=403,
            detail="Drive nao vinculado. Entre pelo botao Google uma vez para autorizar o backup automatico.",
        )
    access_token = str(registro.get("access_token") or "").strip()
    expires_at = _google_oauth_parse_expiry(registro.get("expires_at"))
    if access_token and (expires_at is None or expires_at > datetime.utcnow() + timedelta(seconds=45)):
        return access_token
    return _google_drive_refresh_access_token(ctx, registro)

def _google_drive_request(ctx: dict, method: str, url: str, **kwargs) -> requests.Response:
    headers = dict(kwargs.pop("headers", {}) or {})
    token = _google_drive_access_token(ctx)
    headers["Authorization"] = f"Bearer {token}"
    kwargs.setdefault("timeout", 40)
    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code == 401:
        token = _google_drive_refresh_access_token(ctx)
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code >= 400:
        detalhe = ""
        try:
            payload = resp.json()
            detalhe = str(((payload.get("error") or {}).get("message") if isinstance(payload, dict) else "") or "")
        except Exception:
            detalhe = resp.text[:300]
        raise HTTPException(status_code=502, detail=detalhe or f"Google Drive retornou erro HTTP {resp.status_code}.")
    return resp

def _google_drive_query_literal(valor: str) -> str:
    return "'" + str(valor or "").replace("\\", "\\\\").replace("'", "\\'") + "'"

def _google_drive_get_or_create_folder(ctx: dict, name: str, parent_id: Optional[str] = None) -> str:
    parent_clause = f"{_google_drive_query_literal(parent_id)} in parents" if parent_id else "'root' in parents"
    query = " and ".join([
        "mimeType='application/vnd.google-apps.folder'",
        f"name={_google_drive_query_literal(name)}",
        "trashed=false",
        parent_clause,
    ])
    resp = _google_drive_request(
        ctx,
        "GET",
        "https://www.googleapis.com/drive/v3/files",
        params={"q": query, "spaces": "drive", "fields": "files(id,name)", "pageSize": 1},
    )
    data = resp.json() if resp.content else {}
    files = data.get("files") if isinstance(data, dict) else []
    if files:
        return str(files[0].get("id") or "")

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id] if parent_id else ["root"],
    }
    resp = _google_drive_request(
        ctx,
        "POST",
        "https://www.googleapis.com/drive/v3/files",
        json=metadata,
        params={"fields": "id,name"},
    )
    data = resp.json() if resp.content else {}
    folder_id = str(data.get("id") or "").strip()
    if not folder_id:
        raise HTTPException(status_code=502, detail="Google Drive nao retornou a pasta de backup.")
    return folder_id

def _drive_sync_safe_name(valor: str) -> str:
    texto = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(valor or "default").strip())
    return (texto or "default")[:80]

def _drive_sync_folder_id(ctx: dict) -> str:
    root_id = _google_drive_get_or_create_folder(ctx, DRIVE_SYNC_FOLDER_ROOT)
    client_id = _google_drive_get_or_create_folder(ctx, _drive_sync_safe_name(ctx.get("client_id")), root_id)
    return _google_drive_get_or_create_folder(ctx, _drive_sync_safe_name(ctx.get("username")), client_id)

def _drive_sync_state_path(client_id: str, username: str) -> str:
    tenant_path = get_tenant_path(client_id)
    return os.path.join(tenant_path, f"drive_sync_state_{_drive_sync_safe_name(username)}.json")

def _drive_sync_carregar_estado(client_id: str, username: str) -> dict:
    path = _drive_sync_state_path(client_id, username)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def _drive_sync_salvar_estado(client_id: str, username: str, data: dict):
    path = _drive_sync_state_path(client_id, username)
    estado = _drive_sync_carregar_estado(client_id, username)
    estado.update(data or {})
    estado["updated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

def _drive_sync_relativo_seguro(rel_path: str) -> str:
    rel = str(rel_path or "").replace("\\", "/").strip().lstrip("/")
    norm = os.path.normpath(rel).replace("\\", "/")
    if not norm or norm == "." or norm.startswith("../") or norm == ".." or os.path.isabs(norm):
        raise HTTPException(status_code=400, detail="Backup contem caminho invalido.")
    return norm

def _drive_sync_rel_path_excluido(rel_path: str) -> bool:
    rel = _drive_sync_relativo_seguro(rel_path).lower()
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in DRIVE_SYNC_EXCLUDED_REL_PREFIXES)

def _drive_sync_coletar_arquivos(client_id: str) -> list[dict]:
    tenant_path = get_tenant_path(client_id)
    tenant_abs = os.path.abspath(tenant_path)
    entries = []
    if not os.path.exists(tenant_abs):
        return entries

    for root, dirs, files in os.walk(tenant_abs):
        dirs[:] = []
        for dirname in dirs:
            if dirname in DRIVE_SYNC_EXCLUDED_DIRS or dirname.startswith("."):
                continue
            dir_abs = os.path.abspath(os.path.join(root, dirname))
            if not dir_abs.startswith(tenant_abs + os.sep):
                continue
            dir_rel = os.path.relpath(dir_abs, tenant_abs).replace("\\", "/")
            if _drive_sync_rel_path_excluido(dir_rel):
                continue
            dirs.append(dirname)
        for filename in files:
            name_lower = filename.lower()
            if name_lower.startswith("drive_sync_state_"):
                continue
            if name_lower.endswith((".tmp", ".log", ".bak")) or ".backup_" in name_lower:
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in DRIVE_SYNC_ALLOWED_EXTENSIONS:
                continue
            abs_path = os.path.abspath(os.path.join(root, filename))
            if not abs_path.startswith(tenant_abs + os.sep):
                continue
            rel = os.path.relpath(abs_path, tenant_abs).replace("\\", "/")
            if _drive_sync_rel_path_excluido(rel):
                continue
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                continue
            if size > DRIVE_SYNC_MAX_FILE_BYTES:
                logger.warning("[DRIVE-SYNC] Ignorando arquivo grande demais no backup: %s", abs_path)
                continue
            sha = hashlib.sha256()
            try:
                with open(abs_path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        sha.update(chunk)
                entries.append({
                    "relative_path": _drive_sync_relativo_seguro(rel),
                    "abs_path": abs_path,
                    "size": size,
                    "mtime": os.path.getmtime(abs_path),
                    "sha256": sha.hexdigest(),
                })
            except Exception as exc:
                logger.warning("[DRIVE-SYNC] Falha ao preparar arquivo para backup %s: %s", abs_path, exc)

    entries.sort(key=lambda item: item["relative_path"])
    return entries

def _drive_sync_snapshot_hash(entries: list[dict]) -> str:
    sha = hashlib.sha256()
    for item in entries:
        sha.update(str(item.get("relative_path") or "").encode("utf-8"))
        sha.update(b"\0")
        sha.update(str(item.get("size") or 0).encode("ascii"))
        sha.update(b"\0")
        sha.update(str(item.get("sha256") or "").encode("ascii"))
        sha.update(b"\n")
    return sha.hexdigest()

def _drive_sync_fernet(ctx: dict) -> Fernet:
    if Fernet is None:
        raise HTTPException(status_code=500, detail="Criptografia indisponivel neste instalador.")
    app_secret = _google_login_client_secret()
    if not app_secret:
        raise HTTPException(status_code=500, detail="Client Secret Google nao configurado para criptografar o backup.")
    username = str(ctx.get("username") or "").strip().lower()
    client_id = str(ctx.get("client_id") or "").strip() or "default"
    salt = f"jk-sistema-drive-sync|{client_id}|{username}".encode("utf-8")
    key = hashlib.pbkdf2_hmac("sha256", app_secret.encode("utf-8"), salt, 180000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(key))

def _drive_sync_montar_pacote(ctx: dict) -> tuple[bytes, dict]:
    client_id = str(ctx.get("client_id") or "default").strip() or "default"
    username = str(ctx.get("username") or "").strip().lower()
    entries = _drive_sync_coletar_arquivos(client_id)
    snapshot_hash = _drive_sync_snapshot_hash(entries)
    manifest_files = [
        {
            "relative_path": item["relative_path"],
            "size": item["size"],
            "mtime": item["mtime"],
            "sha256": item["sha256"],
        }
        for item in entries
    ]
    manifest = {
        "schema": 1,
        "app": "JK Sistema",
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client_id": client_id,
        "username": username,
        "google_email": ctx.get("email") or "",
        "snapshot_hash": snapshot_hash,
        "file_count": len(entries),
        "files": manifest_files,
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for item in entries:
            zf.write(item["abs_path"], "files/" + item["relative_path"])

    encrypted = _drive_sync_fernet(ctx).encrypt(zip_buffer.getvalue())
    return encrypted, manifest

def _drive_sync_listar_backups(ctx: dict, limit: int = 10) -> list[dict]:
    folder_id = _drive_sync_folder_id(ctx)
    query = f"{_google_drive_query_literal(folder_id)} in parents and trashed=false and name contains '{DRIVE_SYNC_FILE_EXT}'"
    resp = _google_drive_request(
        ctx,
        "GET",
        "https://www.googleapis.com/drive/v3/files",
        params={
            "q": query,
            "spaces": "drive",
            "orderBy": "modifiedTime desc",
            "pageSize": max(1, min(100, int(limit or 10))),
            "fields": "files(id,name,modifiedTime,size,md5Checksum)",
        },
    )
    data = resp.json() if resp.content else {}
    files = data.get("files") if isinstance(data, dict) else []
    return files if isinstance(files, list) else []

def _drive_sync_limpar_backups_antigos(ctx: dict, manter: int = 20):
    try:
        backups = _drive_sync_listar_backups(ctx, limit=100)
        for item in backups[int(manter or 20):]:
            file_id = str((item or {}).get("id") or "").strip()
            if file_id:
                _google_drive_request(
                    ctx,
                    "DELETE",
                    f"https://www.googleapis.com/drive/v3/files/{quote(file_id, safe='')}",
                    timeout=25,
                )
    except Exception as exc:
        logger.warning("[DRIVE-SYNC] Nao foi possivel limpar backups antigos: %s", exc)

def _drive_sync_upload_backup(ctx: dict) -> dict:
    encrypted, manifest = _drive_sync_montar_pacote(ctx)
    folder_id = _drive_sync_folder_id(ctx)
    filename = (
        f"jk-sistema-backup-{_drive_sync_safe_name(ctx.get('client_id'))}-"
        f"{_drive_sync_safe_name(ctx.get('username'))}-"
        f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}{DRIVE_SYNC_FILE_EXT}"
    )
    metadata = {
        "name": filename,
        "parents": [folder_id],
        "mimeType": "application/octet-stream",
        "description": "Backup criptografado do JK Sistema",
    }
    resp = _google_drive_request(
        ctx,
        "POST",
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", "fields": "id,name,modifiedTime,size"},
        files={
            "metadata": ("metadata", json.dumps(metadata, ensure_ascii=False), "application/json; charset=UTF-8"),
            "file": (filename, encrypted, "application/octet-stream"),
        },
        timeout=90,
    )
    data = resp.json() if resp.content else {}
    _drive_sync_salvar_estado(
        ctx.get("client_id"),
        ctx.get("username"),
        {
            "last_remote_file_id": data.get("id"),
            "last_remote_file_name": data.get("name"),
            "last_remote_modified_time": data.get("modifiedTime"),
            "last_snapshot_hash": manifest.get("snapshot_hash"),
            "last_backup_at": manifest.get("created_at"),
            "last_file_count": manifest.get("file_count"),
        },
    )
    _drive_sync_limpar_backups_antigos(ctx)
    return {"file": data, "manifest": manifest}

def _drive_sync_download_backup(ctx: dict, file_id: str) -> bytes:
    resp = _google_drive_request(
        ctx,
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{quote(str(file_id), safe='')}",
        params={"alt": "media"},
        timeout=90,
    )
    return resp.content or b""

def _drive_sync_restaurar_bytes(ctx: dict, encrypted: bytes) -> dict:
    try:
        zip_bytes = _drive_sync_fernet(ctx).decrypt(encrypted)
    except InvalidToken:
        raise HTTPException(status_code=403, detail="Nao foi possivel descriptografar o backup desta conta.")

    tenant_path = get_tenant_path(ctx.get("client_id"))
    tenant_abs = os.path.abspath(tenant_path)
    restore_stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(tenant_abs, "_drive_restore_backup", restore_stamp)

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Backup do Drive esta corrompido ou incompleto.")
        if str(manifest.get("client_id") or "").strip() != str(ctx.get("client_id") or "").strip():
            raise HTTPException(status_code=403, detail="Backup pertence a outro cliente do sistema.")
        arquivos = manifest.get("files")
        if not isinstance(arquivos, list):
            raise HTTPException(status_code=400, detail="Manifesto do backup invalido.")

        restaurados = 0
        for item in arquivos:
            if not isinstance(item, dict):
                continue
            rel = _drive_sync_relativo_seguro(item.get("relative_path"))
            zip_name = "files/" + rel
            try:
                content = zf.read(zip_name)
            except KeyError:
                logger.warning("[DRIVE-SYNC] Arquivo ausente no pacote: %s", rel)
                continue
            destino = os.path.abspath(os.path.join(tenant_abs, rel.replace("/", os.sep)))
            if not destino.startswith(tenant_abs + os.sep):
                raise HTTPException(status_code=400, detail="Backup contem destino invalido.")
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            if os.path.exists(destino):
                antigo = os.path.join(backup_dir, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(antigo), exist_ok=True)
                try:
                    shutil.copy2(destino, antigo)
                except Exception:
                    pass
            with open(destino, "wb") as f:
                f.write(content)
            restaurados += 1

    manifest["restored_file_count"] = restaurados
    return manifest

def _drive_sync_restaurar_ultimo(ctx: dict) -> dict:
    backups = _drive_sync_listar_backups(ctx, limit=1)
    if not backups:
        return {"restored": False, "message": "Nenhum backup encontrado no Google Drive.", "latest": None}
    latest = backups[0]
    encrypted = _drive_sync_download_backup(ctx, latest.get("id"))
    manifest = _drive_sync_restaurar_bytes(ctx, encrypted)
    _drive_sync_salvar_estado(
        ctx.get("client_id"),
        ctx.get("username"),
        {
            "last_remote_file_id": latest.get("id"),
            "last_remote_file_name": latest.get("name"),
            "last_remote_modified_time": latest.get("modifiedTime"),
            "last_snapshot_hash": manifest.get("snapshot_hash"),
            "last_restore_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_file_count": manifest.get("file_count"),
        },
    )
    return {"restored": True, "latest": latest, "manifest": manifest}

def _drive_sync_local_snapshot(client_id: str) -> tuple[str, int]:
    entries = _drive_sync_coletar_arquivos(client_id)
    return _drive_sync_snapshot_hash(entries), len(entries)


__all__ = [
    "configure_configuracoes_drive_sync_context",
    "DRIVE_SYNC_FOLDER_ROOT",
    "DRIVE_SYNC_FILE_EXT",
    "DRIVE_SYNC_ALLOWED_EXTENSIONS",
    "DRIVE_SYNC_MAX_FILE_BYTES",
    "_drive_sync_contexto_usuario",
    "_google_drive_token_linkado",
    "_google_drive_refresh_access_token",
    "_google_drive_access_token",
    "_google_drive_request",
    "_google_drive_query_literal",
    "_google_drive_get_or_create_folder",
    "_drive_sync_safe_name",
    "_drive_sync_folder_id",
    "_drive_sync_state_path",
    "_drive_sync_carregar_estado",
    "_drive_sync_salvar_estado",
    "_drive_sync_relativo_seguro",
    "_drive_sync_coletar_arquivos",
    "_drive_sync_snapshot_hash",
    "_drive_sync_fernet",
    "_drive_sync_montar_pacote",
    "_drive_sync_listar_backups",
    "_drive_sync_limpar_backups_antigos",
    "_drive_sync_upload_backup",
    "_drive_sync_download_backup",
    "_drive_sync_restaurar_bytes",
    "_drive_sync_restaurar_ultimo",
    "_drive_sync_local_snapshot",
]
