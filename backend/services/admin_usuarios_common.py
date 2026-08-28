"""Internal helpers for admin usuarios common."""

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


def configure_admin_usuarios_common_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_common_runtime()

def _permissao_exigida_por_rota(path: str, method: str = "GET") -> Optional[Any]:
    """Mapeia cada grupo de rotas ao mÃƒÂ³dulo correspondente da planilha de usuÃƒÂ¡rios."""
    rota = str(path or "").lower()
    metodo = str(method or "GET").upper()

    if rota.startswith("/api/promo/"):
        return "analise_promo"
    if rota.startswith("/api/renovacao/"):
        return "renovacao_fixa"
    if rota.startswith("/api/mercadolivre/perguntas"):
        return "perguntas_pos_venda"
    if rota.startswith("/api/mercadolivre/pos-venda"):
        return "perguntas_pos_venda"
    if rota.startswith("/api/mercadolivre/ia-treinamento"):
        return "perguntas_pos_venda"
    if rota.startswith("/api/mercadolivre/promocoes"):
        return ("analise_promo", "anuncios_ml")
    if rota.startswith("/api/mercadolivre/"):
        return "anuncios_ml"
    if rota.startswith("/api/etiquetas/"):
        return "etiquetas"
    if rota.startswith("/api/integracoes/"):
        return "integracao"
    if rota.startswith("/api/lojas") and metodo in {"POST", "PUT", "PATCH", "DELETE"}:
        return "integracao"
    if rota.startswith("/api/cadastro/"):
        return "cadastro"
    if rota.startswith("/api/impostos/"):
        return "impostos"
    if rota.startswith("/api/configuracoes"):
        return "configuracoes"
    if rota.startswith("/api/importacoes"):
        return "importacoes"
    if rota.startswith("/api/sala-reuniao"):
        return "sala_reuniao"
    if rota.startswith("/api/ia/"):
        return None
    if rota == "/api/estoque" or rota.startswith("/api/estoque/"):
        return "estoque"
    if (
        rota.startswith("/api/vendas")
        or rota.startswith("/api/notas-entrada")
        or rota.startswith("/api/unidades-negocios")
    ):
        return "vendas"
    if rota.startswith("/api/medias-compras/"):
        return "medias_compras"
    if rota.startswith("/api/full/"):
        return "mercado_full"
    if rota.startswith("/api/favoritos/"):
        return "favoritos"
    if rota.startswith("/api/admin/"):
        return "admin_usuarios"
    return None

def _normalizar_permissoes_exigidas(permissao_necessaria: Any) -> tuple[str, ...]:
    if not permissao_necessaria:
        return tuple()
    if isinstance(permissao_necessaria, str):
        return (permissao_necessaria,)
    if isinstance(permissao_necessaria, (list, tuple, set)):
        return tuple(str(chave or "").strip() for chave in permissao_necessaria if str(chave or "").strip())
    chave = str(permissao_necessaria or "").strip()
    return (chave,) if chave else tuple()

def _permissoes_autorizam_rota(permissoes: dict, permissao_necessaria: Any) -> bool:
    if permissoes.get("full") is True:
        return True
    return any(permissoes.get(chave) is True for chave in _normalizar_permissoes_exigidas(permissao_necessaria))

def _carregar_permissoes_usuario(username: str, client_id: Optional[str] = None) -> dict:
    """Obtem permissoes na fonte autoritativa sem listar todos os usuarios."""
    username_norm = str(username or "").strip().lower()
    usuarios = None
    headers = []
    firebase_ativo = _firebase_deve_usar()

    if firebase_ativo:
        usuario_firebase = _firebase_obter_usuario(username_norm)
        if isinstance(usuario_firebase, dict):
            usuarios = {username_norm: usuario_firebase}
        elif _firebase_access_obrigatorio():
            raise HTTPException(status_code=401, detail="Usuario da sessao nao encontrado. Faca login novamente.")

    if usuarios is None:
        try:
            usuarios_aut, _ws_aut, headers_aut = carregar_usuarios_sheets(
                skip_firebase=firebase_ativo,
            )
        except HTTPException as exc:
            if not _firebase_http_exception_permite_fallback(exc):
                raise
            logger.warning("[LOGIN] Firebase indisponivel ao carregar permissoes; usando cache local para '%s'.", username_norm)
            usuarios_aut, headers_aut = None, []
        except Exception:
            usuarios_aut, headers_aut = None, []

        if isinstance(usuarios_aut, dict) and username_norm in usuarios_aut:
            usuarios = usuarios_aut
            headers = headers_aut or []
        else:
            usuarios_sql, headers_sql = _carregar_usuarios_sql()
            if isinstance(usuarios_sql, dict) and username_norm in usuarios_sql:
                usuarios = usuarios_sql
                headers = headers_sql or []
            else:
                usuarios_cache, headers_cache = _carregar_cache_usuarios()
                if isinstance(usuarios_cache, dict) and username_norm in usuarios_cache:
                    usuarios = usuarios_cache
                    headers = headers_cache or []
                else:
                    usuarios_local, headers_local = _carregar_usuarios_local()
                    if isinstance(usuarios_local, dict) and username_norm in usuarios_local:
                        usuarios = usuarios_local
                        headers = headers_local or []

    if not isinstance(usuarios, dict) or username_norm not in usuarios:
        raise HTTPException(status_code=401, detail="UsuÃ¡rio da sessÃ£o nÃ£o encontrado. FaÃ§a login novamente.")

    stored_user = usuarios[username_norm]
    client_sheet = str(stored_user.get("client_id") or "").strip()
    if client_id and client_sheet and client_sheet != str(client_id).strip():
        raise HTTPException(status_code=403, detail="Token invalido para o cliente autenticado.")

    permissoes = (
        stored_user.get("permissions")
        if isinstance(stored_user.get("permissions"), dict)
        else extrair_permissoes(stored_user.get("original_row") or [], headers or [])
    )
    return _normalizar_permissoes(permissoes)

def _firebase_http_exception_permite_fallback(exc: HTTPException) -> bool:
    detail = str(getattr(exc, "detail", "") or "").lower()
    return int(getattr(exc, "status_code", 0) or 0) == 503 and "firebase indisponivel" in detail

def _payload_sessao_por_authorization(authorization: Optional[str]) -> dict:
    if not authorization or not str(authorization).startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Token de autenticação ausente. Faça o login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = str(authorization)[len("Bearer "):].strip()
    try:
        payload = decodificar_access_token(token)
    except JWTError:
        raise HTTPException(
            status_code=401,
            detail="Sessão expirada ou inválida. Faça o login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = str(payload.get("sub") or "").strip().lower()
    client_id = str(payload.get("client_id") or "").strip()
    if not username or not client_id:
        raise HTTPException(status_code=401, detail="Sessão inválida. Faça o login novamente.")
    machine_id = str(payload.get("machine_id") or "").strip()
    return {"username": username, "client_id": client_id, "machine_id": machine_id}

def _require_full_admin_user_management(authorization: Optional[str], client_id: str) -> dict:
    sessao = _payload_sessao_por_authorization(authorization)
    username = str(sessao.get("username") or "").strip().lower()
    client_sessao = str(sessao.get("client_id") or "default").strip() or "default"
    client_norm = str(client_id or client_sessao or "default").strip() or "default"
    if client_sessao != client_norm:
        raise HTTPException(status_code=403, detail="Sessao invalida para esse cliente.")
    permissoes = _carregar_permissoes_usuario(username, client_norm)
    if permissoes.get("full") is not True:
        raise HTTPException(status_code=403, detail="Apenas administradores podem gerenciar usuarios.")
    return {
        "username": username,
        "client_id": client_norm,
        "permissions": permissoes,
    }

def _require_admin_usuarios_access(authorization: Optional[str], client_id: str) -> dict:
    sessao = _payload_sessao_por_authorization(authorization)
    username = str(sessao.get("username") or "").strip().lower()
    client_sessao = str(sessao.get("client_id") or "default").strip() or "default"
    client_norm = str(client_id or client_sessao or "default").strip() or "default"
    if client_sessao != client_norm:
        raise HTTPException(status_code=403, detail="Sessao invalida para esse cliente.")
    permissoes = _carregar_permissoes_usuario(username, client_norm)
    if not (permissoes.get("full") is True or permissoes.get("admin_usuarios") is True):
        raise HTTPException(status_code=403, detail="Acesso negado: usuario sem permissao para a Central de Usuarios.")
    return {
        "username": username,
        "client_id": client_norm,
        "permissions": permissoes,
    }

def _require_online_presence_access(authorization: Optional[str], client_id: str) -> dict:
    sessao = _payload_sessao_por_authorization(authorization)
    username = str(sessao.get("username") or "").strip().lower()
    client_sessao = str(sessao.get("client_id") or "default").strip() or "default"
    client_norm = str(client_id or client_sessao or "default").strip() or "default"
    if client_sessao != client_norm:
        raise HTTPException(status_code=403, detail="Sessao invalida para esse cliente.")
    permissoes = _carregar_permissoes_usuario(username, client_norm)
    return {
        "username": username,
        "client_id": client_norm,
        "permissions": permissoes,
    }

def _resolver_credencial_google_sheets() -> str:
    candidatos = [str(CREDENTIALS_FILE or "").strip()]
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if appdata:
        candidatos.append(
            os.path.join(
                appdata,
                "JK Sistema Cliente",
                "local_app",
                "info",
                "credentials.json",
            )
        )

    vistos: set[str] = set()
    for candidato in candidatos:
        if not candidato:
            continue
        caminho = os.path.abspath(os.path.expanduser(candidato))
        chave = os.path.normcase(caminho)
        if chave in vistos:
            continue
        vistos.add(chave)
        if os.path.isfile(caminho):
            return caminho
    return ""


def autenticar_google_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credentials_file = _resolver_credencial_google_sheets()
    if not credentials_file:
        return None
    try:
        creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Erro Auth Google: {e}")
        return None

def _salvar_cache_usuarios(usuarios: dict, headers: list):
    try:
        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "headers": headers or [],
            "usuarios": usuarios or {}
        }
        with open(ARQUIVO_CACHE_USUARIOS, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[LOGIN] NÃƒÂ£o foi possÃƒÂ­vel salvar cache de usuÃƒÂ¡rios: {e}")

def _carregar_cache_usuarios():
    try:
        if not os.path.exists(ARQUIVO_CACHE_USUARIOS):
            return None, None
        with open(ARQUIVO_CACHE_USUARIOS, "r", encoding="utf-8") as f:
            payload = json.load(f)
        usuarios = payload.get("usuarios") or {}
        headers = payload.get("headers") or []
        if not isinstance(usuarios, dict):
            return None, None
        logger.warning(f"[LOGIN] Usando cache local de usuÃƒÂ¡rios ({len(usuarios)} usuÃƒÂ¡rios).")
        return usuarios, headers
    except Exception as e:
        logger.warning(f"[LOGIN] Falha ao ler cache de usuÃƒÂ¡rios: {e}")
        return None, None

def _normalizar_permissoes(permissoes=None) -> dict:
    base = {k: False for k in PERMISSION_KEYS}
    if isinstance(permissoes, dict):
        for chave in PERMISSION_KEYS:
            valor = permissoes.get(chave)
            if isinstance(valor, str):
                valor = valor.strip().lower() in {"1", "true", "sim", "verdadeiro", "yes", "y"}
            elif valor is True:
                valor = True
            else:
                valor = bool(valor)
            base[chave] = valor
        if "avant" not in permissoes:
            base["avant"] = bool(base.get("favoritos"))
    if base.get("full"):
        for chave in PERMISSION_KEYS:
            base[chave] = True
    return base

def _normalizar_max_machines(valor) -> int:
    try:
        numero = int(valor)
    except Exception:
        numero = 1
    if numero < 0:
        return 0
    return numero

def _normalizar_lista_maquinas(machine_ids=None, machine_id: Optional[str] = None) -> list[str]:
    itens = []
    if isinstance(machine_ids, str):
        texto = machine_ids.strip()
        if texto:
            try:
                machine_ids = json.loads(texto)
            except Exception:
                machine_ids = [p.strip() for p in re.split(r"[\n,;|]+", texto) if p.strip()]
    if isinstance(machine_ids, (list, tuple, set)):
        for item in machine_ids:
            valor = str(item or "").strip()
            if valor and not _machine_id_eh_generico(valor):
                itens.append(valor)
    principal = str(machine_id or "").strip()
    if principal and not _machine_id_eh_generico(principal):
        itens.append(principal)

    vistos = set()
    resultado = []
    for item in itens:
        if item not in vistos:
            vistos.add(item)
            resultado.append(item)
    return resultado

def _usuario_pode_logar_em_qualquer_dispositivo(username: str, permissoes=None) -> bool:
    username_norm = str(username or "").strip().lower()
    perms_norm = _normalizar_permissoes(permissoes)
    return bool(perms_norm.get("full")) or username_norm in {"admin", "administrador"}

def _normalizar_data_sistema(valor) -> Optional[str]:
    texto = str(valor or "").strip()
    if not texto:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(texto[:19], fmt).strftime("%d/%m/%Y")
        except Exception:
            continue
    return texto

def _normalizar_email(valor) -> str:
    return str(valor or "").strip().lower()

def _normalizar_empresa(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "").strip())

def _empresa_chat_key(valor) -> str:
    texto = unicodedata.normalize("NFKD", _normalizar_empresa(valor))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto).strip().casefold()

def _pydantic_campo_enviado(modelo, campo: str) -> bool:
    enviados = getattr(modelo, "model_fields_set", None)
    if enviados is None:
        enviados = getattr(modelo, "__fields_set__", set())
    return campo in (enviados or set())

def _hash_password_se_preciso(password: str) -> str:
    senha = str(password or "").strip()
    if not senha:
        return senha
    if senha.startswith("$2"):
        return senha
    try:
        return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except Exception:
        return senha

def _env_texto(*nomes: str) -> str:
    for nome in nomes:
        valor = str(os.getenv(nome, "") or "").strip()
        if valor:
            return valor
    return ""


__all__ = [
    "configure_admin_usuarios_common_runtime",
    "_permissao_exigida_por_rota",
    "_normalizar_permissoes_exigidas",
    "_permissoes_autorizam_rota",
    "_carregar_permissoes_usuario",
    "_firebase_http_exception_permite_fallback",
    "_payload_sessao_por_authorization",
    "_require_full_admin_user_management",
    "_require_admin_usuarios_access",
    "_require_online_presence_access",
    "autenticar_google_sheets",
    "_salvar_cache_usuarios",
    "_carregar_cache_usuarios",
    "_normalizar_permissoes",
    "_normalizar_max_machines",
    "_normalizar_lista_maquinas",
    "_usuario_pode_logar_em_qualquer_dispositivo",
    "_normalizar_data_sistema",
    "_normalizar_email",
    "_normalizar_empresa",
    "_empresa_chat_key",
    "_pydantic_campo_enviado",
    "_hash_password_se_preciso",
    "_env_texto",
]
