"""Internal helpers for admin usuarios login core."""

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

try:
    from google.cloud.firestore_v1 import ArrayUnion as _FirestoreArrayUnion
except Exception:  # Firebase is optional in local-only installations.
    _FirestoreArrayUnion = None

from backend.schemas import LoginResponse
from backend.services.admin_usuarios_context import configure_admin_usuarios_context
from backend.services.runtime_bridge import bind_runtime_globals


def configure_admin_usuarios_login_core_runtime(runtime_module=None):
    runtime = configure_admin_usuarios_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    return runtime


configure_admin_usuarios_login_core_runtime()

def _carregar_usuarios_local():
    """Fallback offline para login quando SQL/planilha estiverem indisponÃƒÂ­veis.
    Formato esperado em info/usuarios_local.json:
    [
      {
        "username": "admin",
        "password": "senha-ou-bcrypt",
        "name": "Administrador",
        "client_id": "default",
        "permissions": {"full": true},
        "active": true,
        "valid_until": "2030-12-31",
        "machine_id": "acesso-via-navegador"
      }
    ]
    """
    try:
        if not os.path.exists(ARQUIVO_USUARIOS_LOCAL):
            return None, None

        with open(ARQUIVO_USUARIOS_LOCAL, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            logger.warning("[LOGIN] usuarios_local.json invalido: esperado array.")
            return None, None

        usuarios = {}
        headers = []
        for i, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            username = str(item.get("username") or "").strip().lower()
            password = str(item.get("password") or "").strip()
            if not username or not password:
                continue
            usuarios[username] = {
                "password": password,
                "name": str(item.get("name") or username),
                "email": _normalizar_email(item.get("email") or item.get("google_email")),
                "client_id": str(item.get("client_id") or "default"),
                "permissions": _normalizar_permissoes(item.get("permissions") if isinstance(item.get("permissions"), dict) else {}),
                "original_row": [],
                "row_index": i,
                "source": "local",
                "active": bool(item.get("active", True)),
                "valid_until": str(item.get("valid_until") or "").strip() or None,
                "machine_id": str(item.get("machine_id") or "").strip() or None,
            }

        if not usuarios:
            return None, None

        logger.warning(f"[LOGIN] Usando usuÃƒÂ¡rios locais (arquivo offline): {len(usuarios)} usuÃƒÂ¡rio(s).")
        return usuarios, headers
    except Exception as e:
        logger.warning(f"[LOGIN] Falha ao carregar usuarios_local.json: {e}")
        return None, None

def verificar_validade_acesso(username, ws):
    """Verifica se a data de validade do usuÃƒÆ’Ã‚Â¡rio nÃƒÆ’Ã‚Â£o expirou."""
    try:
        todos_dados = ws.get_all_values()
        if not todos_dados: return True, "Planilha vazia", None
        
        headers = [str(h).lower().strip() for h in todos_dados[0]]
        
        # Tenta achar colunas
        idx_usuario = -1
        for k in ["usuario", "usuÃƒÆ’Ã‚Â¡rio", "user", "login", "nome"]:
            if k in headers: idx_usuario = headers.index(k); break
            
        idx_validade = -1
        for k in ["validade", "vencimento", "expira"]:
            if k in headers: idx_validade = headers.index(k); break
            
        # Busca dinÃƒÂ¢mica da coluna de ID da planilha do usuÃƒÂ¡rio
        idx_planilha = -1
        for k in [
            "planilha jk", "planilha", "spreadsheet id", "sheet id", "id planilha",
            "chave de acesso", "chave acesso", "chave de acesso planilha", "id da planilha"
        ]:
            if k in headers: 
                idx_planilha = headers.index(k)
                logger.info(f"[VALIDADE] Coluna de planilha encontrada no ÃƒÂ­ndice {idx_planilha} (coluna {chr(65+idx_planilha)})")
                break
        
        if idx_planilha == -1:
            logger.warning("[VALIDADE] Coluna de planilha do usuÃƒÂ¡rio NÃƒO encontrada nos headers.")
            logger.warning(f"[VALIDADE] Headers disponÃƒÂ­veis: {headers}")
            
        if idx_usuario == -1:
            return True, "Coluna usuÃƒÆ’Ã‚Â¡rio nÃƒÆ’Ã‚Â£o encontrada", None

        for row in todos_dados[1:]:
            if len(row) > idx_usuario and str(row[idx_usuario]).lower().strip() == username.lower():
                # Extrai ID Planilha do UsuÃƒÆ’Ã‚Â¡rio
                sheet_id = None
                if idx_planilha != -1 and len(row) > idx_planilha:
                    sheet_id = str(row[idx_planilha]).strip()
                    logger.info(f"[VALIDADE] UsuÃƒÂ¡rio '{username}': Planilha do usuÃƒÂ¡rio = '{sheet_id}' (coluna {chr(65+idx_planilha)})")
                elif idx_planilha != -1:
                    logger.warning(f"[VALIDADE] UsuÃƒÂ¡rio '{username}': Linha tem {len(row)} colunas, mas ÃƒÂ­ndice planilha ÃƒÂ© {idx_planilha}")

                if idx_validade == -1 or len(row) <= idx_validade: 
                    return True, "Sem data", sheet_id
                
                data_str = str(row[idx_validade]).strip()
                if not data_str: return False, "Data de validade nÃƒÆ’Ã‚Â£o configurada.", sheet_id
                
                try:
                    dt_val = datetime.strptime(data_str, "%d/%m/%Y")
                    if datetime.now() > dt_val:
                        return False, f"Acesso expirado em {data_str}.", sheet_id
                    return True, "Acesso vÃƒÆ’Ã‚Â¡lido", sheet_id
                except:
                    return False, f"Data invÃƒÆ’Ã‚Â¡lida: {data_str}", sheet_id
        return False, "UsuÃƒÆ’Ã‚Â¡rio nÃƒÆ’Ã‚Â£o encontrado para validaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o", None
    except Exception as e:
        print(f"Erro validade: {e}")
        return True, "Erro na verificaÃƒÆ’Ã‚Â§ÃƒÆ’Ã‚Â£o (Liberado)", None

def extrair_permissoes(row, headers):
    """Extrai permissÃƒÂµes dinamicamente a partir da planilha de usuÃƒÂ¡rios."""
    permissoes = {k: False for k in PERMISSION_KEYS}

    def _norm_header(val: str) -> str:
        val = str(val).strip().lower()
        val = unicodedata.normalize('NFKD', val)
        val = ''.join([c for c in val if not unicodedata.combining(c)])
        return val

    headers_norm = [_norm_header(h) for h in (headers or [])]

    map_headers = {
        'analise_promo': ['analise promocional ml', 'analise promocional ml', 'analise promo', 'promocao ml'],
        'renovacao_fixa': ['renovaÃƒÂ§ÃƒÂ£o fixa', 'renovacao fixa'],
        'vendas': ['vendas'],
        'estoque': ['estoque'],
        'integracao': ['integraÃƒÂ§ÃƒÂ£o', 'integracao', 'integraÃƒÂ§ÃƒÂµes', 'integracoes'],
        'etiquetas': ['etiquetas'],
        'full': ['full', 'admin total', 'administrador'],
        'favoritos': ['favoritos'],
        'avant': ['avant', 'avant pro', 'avantpro', 'navegador avant', 'modulo avant'],
        'perguntas_pos_venda': ['perguntas e pos venda', 'perguntas e pÃ³s venda', 'perguntas pos venda', 'pos venda', 'pÃ³s venda'],
        'anuncios_ml': ['anuncios mercado livre', 'anuncios mercado livre', 'anuncios ml'],
        'medias_compras': ['mÃƒÂ©dias e compras', 'medias e compras', 'media e compras', 'medias compras'],
        'mercado_full': ['modulo full', 'mercado full', 'mercado livre full', 'ml full'],
        'cadastro': ['cadastro', 'cadastro de produtos'],
        'impostos': ['impostos', 'imposto'],
        'configuracoes': ['configuraÃƒÂ§ÃƒÂµes', 'configuracoes', 'configuraÃƒÂ§ÃƒÂ£o', 'configuracao'],
        'importacoes': ['importaÃƒÂ§ÃƒÂµes', 'importacoes', 'importaÃƒÂ§ÃƒÂ£o', 'importacao'],
        'simulador': ['simulador', 'simulaÃƒÂ§ÃƒÂ£o', 'simulacao'],
        'sala_reuniao': ['sala de reuniao', 'sala reuniÃƒÂ£o', 'sala reuniao', 'reuniao', 'reunioes'],
    }

    fallback_map = {
        'analise_promo': 10, 'renovacao_fixa': 11, 'vendas': 12, 'estoque': 13,
        'integracao': 14, 'etiquetas': 15, 'full': 16, 'favoritos': 17,
        'avant': -1, 'perguntas_pos_venda': -1, 'anuncios_ml': 18, 'medias_compras': 19, 'mercado_full': -1,
        'cadastro': -1, 'impostos': -1, 'configuracoes': -1, 'importacoes': -1,
        'simulador': -1, 'sala_reuniao': -1,
    }

    for mod_name, candidates in map_headers.items():
        idx = -1
        for cand in candidates:
            cand_norm = _norm_header(cand)
            if cand_norm in headers_norm:
                idx = headers_norm.index(cand_norm)
                break
        if idx == -1:
            idx = fallback_map.get(mod_name, -1)

        if idx >= 0 and len(row) > idx:
            val = str(row[idx]).upper().strip()
            if val in ['VERDADEIRO', 'TRUE', 'SIM', '1', 'X', 'OK']:
                permissoes[mod_name] = True

    avant_tem_coluna = any(_norm_header(cand) in headers_norm for cand in map_headers.get('avant', []))
    if not avant_tem_coluna:
        permissoes['avant'] = bool(permissoes.get('favoritos'))

    return _normalizar_permissoes(permissoes)

def verificar_trava_seguranca(ws, row_index, headers, username, client_id=None):
    """Verifica trava de seguranÃƒÆ’Ã‚Â§a (MAC Address) igual ao login.py"""
    try:
        if client_id:
            mac_address = client_id.strip().upper()
        else:
            return False, "Acesso negado: ÃƒÆ’Ã¢â‚¬Â° necessÃƒÆ’Ã‚Â¡rio utilizar o Aplicativo Desktop oficial."
            
        data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        # Procura ÃƒÆ’Ã‚Â­ndices das colunas (case insensitive)
        headers_lower = [str(h).lower().strip() for h in headers]
        
        # Tenta achar coluna Maquina
        idx_maquina = -1
        if "maquina" in headers_lower: idx_maquina = headers_lower.index("maquina")
        
        # Tenta achar coluna Ultimo Acesso
        idx_acesso = -1
        for i, h in enumerate(headers_lower):
            if "ultimo acesso" in h or "ÃƒÆ’Ã‚Âºltimo acesso" in h: idx_acesso = i; break
            
        if idx_maquina == -1:
            return True, "Coluna Maquina nÃƒÆ’Ã‚Â£o encontrada (Ignorado)"

        # LÃƒÆ’Ã‚Âª valor atual da mÃƒÆ’Ã‚Â¡quina na planilha (row_index ÃƒÆ’Ã‚Â© 1-based)
        maquina_registrada = ws.cell(row_index, idx_maquina + 1).value
        maquina_registrada = str(maquina_registrada).strip() if maquina_registrada else ""

        if not maquina_registrada:
            # Vincula
            ws.update_cell(row_index, idx_maquina + 1, mac_address)
            if idx_acesso != -1: ws.update_cell(row_index, idx_acesso + 1, data_hora)
            return True, "MÃƒÆ’Ã‚Â¡quina vinculada com sucesso"
        
        elif maquina_registrada.lower() == mac_address.lower():
            # Autorizado
            if idx_acesso != -1: ws.update_cell(row_index, idx_acesso + 1, data_hora)
            return True, "Acesso autorizado"
        
        else:
            # Bloqueado
            return False, f"Bloqueado. Vinculado a: {maquina_registrada}"
            
    except Exception as e:
        print(f"Erro ao registrar acesso: {e}")
        return False, f"Erro seguranÃƒÆ’Ã‚Â§a: {e}"

def carregar_usuarios_sheets():
    firebase_primeiro = _env_config_bool(
        ("JK_FIREBASE_USERS_FIRST", "FIREBASE_USERS_FIRST"),
        default=False,
    )
    if firebase_primeiro:
        usuarios_firebase = _firebase_listar_usuarios(seed_if_empty=True) if _firebase_deve_usar() else None
        if isinstance(usuarios_firebase, dict):
            return usuarios_firebase, None, []

    usuarios_sql, headers_sql = _carregar_usuarios_sql()
    if usuarios_sql is not None:
        return usuarios_sql, None, headers_sql

    if not firebase_primeiro:
        usuarios_firebase = _firebase_listar_usuarios(seed_if_empty=True) if _firebase_deve_usar() else None
        if isinstance(usuarios_firebase, dict):
            return usuarios_firebase, None, []

    client = autenticar_google_sheets()
    if not client:
        usuarios_cache, headers_cache = _carregar_cache_usuarios()
        if usuarios_cache is not None:
            _salvar_usuarios_sql(usuarios_cache, source="cache")
            return usuarios_cache, None, headers_cache
        usuarios_local, headers_local = _carregar_usuarios_local()
        if usuarios_local is not None:
            _salvar_usuarios_sql(usuarios_local, source="local")
            return usuarios_local, None, headers_local
        return None, None, None

    try:
        last_error = None
        sh = None
        for tentativa in range(1, 4):
            try:
                sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
                break
            except Exception as e:
                last_error = e
                logger.warning(f"[LOGIN] Tentativa {tentativa}/3 ao abrir planilha falhou: {e}")
                time.sleep(1.2 * tentativa)
        if sh is None:
            raise last_error

        try:
            ws = sh.worksheet("Clientes")
        except:
            ws = sh.sheet1
            
        # Usa get_all_values para evitar erros de cabeÃƒÆ’Ã‚Â§alho duplicado do get_all_records
        rows = ws.get_all_values()
        if not rows: return {}, ws, []
        
        headers_raw = [str(h).strip() for h in rows[0]]
        headers = [h.lower() for h in headers_raw]
        usuarios = {}

        # Mapeamento de ÃƒÆ’Ã‚Â­ndices
        def _norm_header(h: str) -> str:
            h = unicodedata.normalize('NFKD', h)
            h = ''.join([c for c in h if not unicodedata.combining(c)])
            h = re.sub(r'[^a-zA-Z0-9]+', '', h).upper()
            return h

        headers_norm = [_norm_header(h) for h in headers_raw]

        def find_idx(keys):
            for k in keys:
                if k in headers:
                    return headers.index(k)
            return -1

        def find_idx_norm(keys_norm):
            for k in keys_norm:
                if k in headers_norm:
                    return headers_norm.index(k)
            return -1

        idx_user = find_idx(["usuÃƒÆ’Ã‚Â¡rio", "usuario", "user", "login"])
        idx_pass = find_idx(["senha", "password", "pass"])
        idx_name = find_idx(["nome", "name"])
        idx_email = find_idx(["email", "e-mail", "gmail", "google email", "email google", "e-mail google"])
        if idx_email == -1:
            idx_email = find_idx_norm(["EMAIL", "EMAILGOOGLE", "GOOGLEEMAIL", "GMAIL"])
        idx_client_id = find_idx(["nÃƒÆ’Ã‚Âºmero do cliente", "numero do cliente", "id cliente"])
        if idx_client_id == -1:
            idx_client_id = find_idx_norm(["NUMERODOCLIENTE", "IDCLIENTE", "CLIENTEID", "CODIGOCLIENTE", "CODCLIENTE"])

        if idx_user == -1 or idx_pass == -1:
            print("Colunas de UsuÃƒÆ’Ã‚Â¡rio/Senha nÃƒÆ’Ã‚Â£o encontradas.")
            return {}, ws, headers

        for i, row in enumerate(rows[1:], start=2):
            if len(row) <= idx_user or len(row) <= idx_pass: continue
            
            user = str(row[idx_user]).strip()
            raw_pw = str(row[idx_pass]).strip()
            name = str(row[idx_name]).strip() if idx_name != -1 and len(row) > idx_name else user
            email = str(row[idx_email]).strip() if idx_email != -1 and len(row) > idx_email else ""
            client_id = str(row[idx_client_id]).strip() if idx_client_id != -1 and len(row) > idx_client_id else None
            
            if user and raw_pw:
                permissoes_usuario = extrair_permissoes(row, headers_raw)
                usuarios[user.lower()] = {
                    "password": raw_pw,
                    "name": name,
                    "email": _normalizar_email(email),
                    "client_id": client_id,
                    "permissions": permissoes_usuario,
                    "original_row": row,
                    "row_index": i
                }

        _salvar_cache_usuarios(usuarios, headers)
        _salvar_usuarios_sql(usuarios, source="planilha")
        return usuarios, ws, headers
    except Exception as e:
        print(f"Erro ao carregar usuÃƒÆ’Ã‚Â¡rios: {e}")
        usuarios_cache, headers_cache = _carregar_cache_usuarios()
        if usuarios_cache is not None:
            return usuarios_cache, None, headers_cache
        usuarios_local, headers_local = _carregar_usuarios_local()
        if usuarios_local is not None:
            return usuarios_local, None, headers_local
        return None, None, None

def _login_senha_confere(senha_informada: str, senha_salva: str) -> bool:
    senha = str(senha_informada or "")
    salva = str(senha_salva or "")
    if not senha or not salva:
        return False
    if salva.startswith("$2"):
        try:
            return bcrypt.checkpw(senha.encode("utf-8"), salva.encode("utf-8"))
        except Exception:
            return False
    return secrets.compare_digest(senha, salva)

def _login_validade_ok(usuario: dict) -> tuple[bool, str]:
    data_txt = _normalizar_data_sistema((usuario or {}).get("valid_until"))
    if not data_txt:
        return True, ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            data_limite = datetime.strptime(data_txt[:10], fmt)
            if datetime.now() > data_limite.replace(hour=23, minute=59, second=59):
                return False, f"Acesso expirado em {data_txt}."
            return True, ""
        except Exception:
            continue
    return True, ""

def _login_usuario_ativo(usuario: dict) -> bool:
    valor = (usuario or {}).get("active", True)
    if isinstance(valor, str):
        return valor.strip().lower() not in {"0", "false", "falso", "nao", "nÃ£o", "inativo", "bloqueado"}
    return bool(valor)

def _firebase_validar_e_registrar_maquina(username: str, usuario: dict, permissoes: dict, machine_final: str) -> tuple[bool, str, str]:
    username_norm = str(username or "").strip().lower()
    unrestricted = _usuario_pode_logar_em_qualquer_dispositivo(username, permissoes)
    coll = _firebase_collection()
    if coll is None:
        if _firebase_access_obrigatorio():
            return False, "Firebase indisponivel para validar maquinas.", machine_final
        return (False, "", machine_final) if unrestricted else (True, "", machine_final)

    ref = coll.document(_firebase_doc_id(username_norm))

    try:
        snap = ref.get()
        if not snap.exists:
            return False, "Usuario nao encontrado no Firebase.", machine_final
        usuario_atual = _firebase_user_from_data(username_norm, snap.to_dict() or {})
        maquinas = _normalizar_lista_maquinas(usuario_atual.get("machine_ids"), usuario_atual.get("machine_id"))
        if machine_final in maquinas:
            return True, "", machine_final
        if unrestricted:
            if _FirestoreArrayUnion is None:
                return False, "Firebase indisponivel para registrar esta maquina.", machine_final
            ref.update({
                "machine_ids": _FirestoreArrayUnion([machine_final]),
                "updated_at": _firebase_now_iso(),
            })
            maquinas.append(machine_final)
            usuario_atualizado = dict(usuario)
            maquinas_locais = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
            if machine_final not in maquinas_locais:
                maquinas_locais.append(machine_final)
            usuario_atualizado["machine_id"] = maquinas_locais[0] if maquinas_locais else machine_final
            usuario_atualizado["machine_ids"] = maquinas_locais
            _salvar_usuarios_sql({username_norm: usuario_atualizado}, source="firebase-cache")
            return True, "", machine_final
        max_machines = _normalizar_max_machines(usuario_atual.get("max_machines", 1))
        if max_machines != 0 and len(maquinas) >= max_machines:
            return False, "Limite de dispositivos atingido para este usuario. Peca ao administrador para resetar os dispositivos.", machine_final
        maquinas.append(machine_final)
        ref.update({
            "machine_id": maquinas[0] if maquinas else machine_final,
            "machine_ids": maquinas,
            "updated_at": _firebase_now_iso(),
        })
        ok, msg = True, ""
        if ok:
            usuario_atualizado = dict(usuario)
            maquinas_locais = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
            if machine_final not in maquinas_locais:
                maquinas_locais.append(machine_final)
            usuario_atualizado["machine_id"] = maquinas_locais[0] if maquinas_locais else machine_final
            usuario_atualizado["machine_ids"] = maquinas_locais
            _salvar_usuarios_sql({username_norm: usuario_atualizado}, source="firebase-cache")
        return bool(ok), str(msg or ""), machine_final
    except Exception as exc:
        logger.warning("[FIREBASE-AUTH] Falha ao validar maquina no Firebase para %s: %s", username_norm, exc)
        if _firebase_access_obrigatorio():
            return False, f"Firebase indisponivel para validar maquinas: {exc}", machine_final
        return (False, "", machine_final) if unrestricted else (True, "", machine_final)

def _login_validar_e_registrar_maquina(username: str, usuario: dict, permissoes: dict, machine_id: str, request: Request) -> tuple[bool, str, str]:
    machine_final, _meta = _montar_machine_id_login(request, machine_id)
    unrestricted = _usuario_pode_logar_em_qualquer_dispositivo(username, permissoes)
    source_is_firebase = (usuario or {}).get("source") == "firebase"
    if _firebase_deve_usar() and (source_is_firebase or unrestricted):
        remote_result = _firebase_validar_e_registrar_maquina(username, usuario, permissoes, machine_final)
        if remote_result[0] or source_is_firebase or _firebase_access_obrigatorio():
            return remote_result

    maquinas = _normalizar_lista_maquinas(usuario.get("machine_ids"), usuario.get("machine_id"))
    if machine_final in maquinas:
        return True, "", machine_final

    max_machines = _normalizar_max_machines(usuario.get("max_machines", 1))
    if not unrestricted and max_machines != 0 and len(maquinas) >= max_machines:
        return False, "Limite de dispositivos atingido para este usuario. PeÃ§a ao administrador para resetar os dispositivos.", machine_final

    maquinas.append(machine_final)
    usuario_atualizado = dict(usuario)
    usuario_atualizado["machine_id"] = maquinas[0] if maquinas else machine_final
    usuario_atualizado["machine_ids"] = maquinas
    _salvar_usuarios_sql({username: usuario_atualizado}, source="sql-login")
    return True, "", machine_final

def _google_login_client_id() -> str:
    return _env_config_value("GOOGLE_LOGIN_CLIENT_ID", "GOOGLE_CLIENT_ID", cache_as="GOOGLE_LOGIN_CLIENT_ID")

def _google_login_client_secret() -> str:
    return _env_config_value("GOOGLE_LOGIN_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET", cache_as="GOOGLE_LOGIN_CLIENT_SECRET")

def _google_login_scopes() -> str:
    scopes = _env_config_value("GOOGLE_LOGIN_SCOPES", "GOOGLE_OAUTH_SCOPES")
    required_scopes = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/drive.file",
    ]
    scope_parts = [item.strip() for item in str(scopes or "").split() if item.strip()]
    for required_scope in required_scopes:
        if required_scope not in scope_parts:
            scope_parts.append(required_scope)
    return " ".join(scope_parts)

def _google_login_verify_id_token(id_token_value: str, client_id_google: str) -> dict:
    return google_id_token.verify_oauth2_token(
        id_token_value,
        GoogleAuthRequest(),
        client_id_google,
        clock_skew_in_seconds=60,
    )

def _google_login_redirect_uri(request: Optional[Request] = None) -> str:
    local_configured = _env_config_value("GOOGLE_LOGIN_REDIRECT_URI_LOCAL", "GOOGLE_LOCAL_REDIRECT_URI")
    public_configured = _env_config_value("GOOGLE_LOGIN_REDIRECT_URI_PUBLIC", "GOOGLE_PUBLIC_REDIRECT_URI")
    configured = _env_config_value("GOOGLE_LOGIN_REDIRECT_URI", "GOOGLE_REDIRECT_URI")
    if _request_eh_local(request) and local_configured:
        return local_configured.rstrip("/")
    if request is not None and not _request_eh_local(request) and public_configured:
        return public_configured.rstrip("/")
    if configured:
        return configured.rstrip("/")

    if request is not None:
        try:
            proto = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip() or "http"
            host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
            if host:
                return f"{proto}://{host}/auth/google/callback"
        except Exception:
            pass

    return "http://127.0.0.1:8001/auth/google/callback"

def _google_login_configurado() -> bool:
    return bool(_google_login_client_id() and _google_login_client_secret())

def _buscar_usuario_por_email_google(usuarios: dict, email: str) -> tuple[Optional[str], Optional[dict]]:
    email_norm = _normalizar_email(email)
    if not email_norm or not isinstance(usuarios, dict):
        return None, None

    fallback_username = None
    fallback_usuario = None
    for username, usuario in usuarios.items():
        if not isinstance(usuario, dict):
            continue
        username_norm = str(username or "").strip().lower()
        if _normalizar_email(usuario.get("email") or usuario.get("google_email")) == email_norm:
            return username_norm, usuario
        if username_norm == email_norm:
            fallback_username = username_norm
            fallback_usuario = usuario

    return fallback_username, fallback_usuario

def _montar_resposta_login_sucesso(username: str, usuario: dict, permissoes: dict, client_id: str, machine_final: str) -> LoginResponse:
    token = criar_access_token(username, client_id, machine_final)
    user_data = {
        "username": username,
        "name": str(usuario.get("name") or username),
        "email": _normalizar_email(usuario.get("email")),
        "client_id": client_id,
        "machine_id": machine_final,
    }
    return LoginResponse(
        success=True,
        message="Login realizado com sucesso.",
        user_data=user_data,
        permissions=_normalizar_permissoes(permissoes),
        access_token=token,
    )

def _autenticar_usuario_por_google_info(token_info: dict, machine_id: str, request: Request, app_version: Optional[str] = None) -> LoginResponse:
    app_version_ok = _validar_versao_minima_app_ou_426(app_version)
    issuer = str((token_info or {}).get("iss") or "")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        return LoginResponse(success=False, message="Origem da conta Google invalida.")

    email = _normalizar_email((token_info or {}).get("email"))
    email_verified = (token_info or {}).get("email_verified")
    if isinstance(email_verified, str):
        email_verified = email_verified.strip().lower() == "true"
    if not email or not bool(email_verified):
        return LoginResponse(success=False, message="Use uma conta Google com e-mail verificado.")

    usuarios, _ws, _headers = carregar_usuarios_sheets()
    username, usuario = _buscar_usuario_por_email_google(usuarios or {}, email)
    if not username or not isinstance(usuario, dict):
        return LoginResponse(
            success=False,
            message="Conta Google nao vinculada. Peça ao administrador para cadastrar esse e-mail no usuario.",
        )

    if not _login_usuario_ativo(usuario):
        return LoginResponse(success=False, message="Usuario inativo. Contate o administrador.")

    validade_ok, msg_validade = _login_validade_ok(usuario)
    if not validade_ok:
        return LoginResponse(success=False, message=msg_validade or "Acesso expirado ou invalido.")

    permissoes = _normalizar_permissoes(usuario.get("permissions") or {})
    client_id = str(usuario.get("client_id") or "default").strip() or "default"
    usuario["client_id"] = client_id
    if not _normalizar_email(usuario.get("email")):
        usuario["email"] = email

    maquina_ok, msg_maquina, machine_final = _login_validar_e_registrar_maquina(
        username,
        usuario,
        permissoes,
        machine_id,
        request,
    )
    if not maquina_ok:
        return LoginResponse(success=False, message=msg_maquina)

    try:
        _registrar_login_maquina(username, client_id, machine_final, request)
    except Exception as exc:
        logger.warning("[LOGIN] Nao foi possivel registrar auditoria de login Google para %s: %s", username, exc)
    try:
        _machine_presence_save(_machine_presence_record(username, client_id, machine_final, request, "login-google", app_version_ok))
    except Exception as exc:
        logger.warning("[LOGIN] Nao foi possivel registrar presenca inicial Google para %s: %s", username, exc)

    return _montar_resposta_login_sucesso(username, usuario, permissoes, client_id, machine_final)

def _google_login_error_redirect(message: str):
    query = urlencode({"google_error": str(message or "Nao foi possivel entrar com Google.")})
    return RedirectResponse(url=f"/frontend_index.html?{query}", status_code=303)

def _google_login_success_html(resp: LoginResponse):
    user_json = json.dumps(resp.user_data or {}, ensure_ascii=False).replace("</", "<\\/")
    permissions_json = json.dumps(resp.permissions or {}, ensure_ascii=False).replace("</", "<\\/")
    token_json = json.dumps(resp.access_token or "", ensure_ascii=False).replace("</", "<\\/")
    html = f"""
<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>Entrando...</title>
</head>
<body>
    <script>
        localStorage.setItem('user_data', JSON.stringify({user_json}));
        localStorage.setItem('permissions', JSON.stringify({permissions_json}));
        const token = {token_json};
        if (token) {{
            localStorage.setItem('access_token', token);
        }} else {{
            localStorage.removeItem('access_token');
        }}
        window.location.replace('/dashboard.html');
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html)

def _google_login_poll_html(success: bool, message: str):
    titulo = html_lib.escape("Login concluido" if success else "Login nao autorizado")
    texto = html_lib.escape(str(message or ("Login concluido. Volte ao sistema." if success else "Nao foi possivel entrar com Google.")))
    html = f"""
<!doctype html>
<html lang="pt-BR">
<head>
    <meta charset="utf-8">
    <title>{titulo}</title>
    <style>
        body {{
            margin: 0;
            min-height: 100vh;
            display: grid;
            place-items: center;
            font-family: Arial, sans-serif;
            background: #071326;
            color: #e8f3ff;
        }}
        main {{
            max-width: 480px;
            padding: 32px;
            text-align: center;
        }}
        h1 {{ margin: 0 0 12px; font-size: 26px; }}
        p {{ margin: 0; line-height: 1.5; color: #b8c7dc; }}
    </style>
</head>
<body>
    <main>
        <h1>{titulo}</h1>
        <p>{texto}</p>
    </main>
</body>
</html>
"""
    return HTMLResponse(content=html)

def _google_login_poll_response_payload(resp: LoginResponse) -> dict:
    return {
        "created_at": time.time(),
        "success": bool(resp.success),
        "message": resp.message,
        "user_data": resp.user_data or {},
        "permissions": resp.permissions or {},
        "access_token": resp.access_token or "",
    }

def _google_login_store_poll_result(state: str, payload: dict):
    state = str(state or "").strip()
    if not state:
        return
    payload = dict(payload or {})
    payload["created_at"] = time.time()
    with GOOGLE_LOGIN_STATE_LOCK:
        GOOGLE_LOGIN_RESULTS[state] = payload

def _google_login_finish_poll(state: str, resp: LoginResponse):
    _google_login_store_poll_result(state, _google_login_poll_response_payload(resp))
    if resp.success:
        return _google_login_poll_html(True, "Login concluido. Volte ao sistema para continuar.")
    return _google_login_poll_html(False, resp.message or "Conta Google nao autorizada.")

def _google_oauth_init_db():
    _init_auth_db()
    conn = _auth_db_conexao()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS google_oauth_tokens (
                username TEXT NOT NULL,
                client_id TEXT NOT NULL,
                google_email TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at TEXT,
                scopes TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (username, client_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def _google_oauth_carregar_tokens(username: str, client_id: str) -> Optional[dict]:
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "").strip() or "default"
    if not username_norm:
        return None
    _google_oauth_init_db()
    conn = _auth_db_conexao()
    try:
        row = conn.execute(
            """
            SELECT username, client_id, google_email, access_token, refresh_token,
                   expires_at, scopes, updated_at
            FROM google_oauth_tokens
            WHERE username = ? AND client_id = ?
            """,
            (username_norm, client_norm),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _google_oauth_salvar_tokens_usuario(username: str, client_id: str, google_email: str, token_payload: dict):
    username_norm = str(username or "").strip().lower()
    client_norm = str(client_id or "").strip() or "default"
    if not username_norm or not client_norm or not isinstance(token_payload, dict):
        return

    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token and not refresh_token:
        return

    existente = _google_oauth_carregar_tokens(username_norm, client_norm) or {}
    if not refresh_token:
        refresh_token = str(existente.get("refresh_token") or "").strip()

    expires_at = str(existente.get("expires_at") or "").strip()
    try:
        expires_in = int(token_payload.get("expires_in") or 0)
    except Exception:
        expires_in = 0
    if expires_in > 0:
        expires_at = (datetime.utcnow() + timedelta(seconds=max(60, expires_in - 60))).strftime("%Y-%m-%dT%H:%M:%SZ")

    scopes = str(token_payload.get("scope") or existente.get("scopes") or _google_login_scopes()).strip()
    email = _normalizar_email(google_email or existente.get("google_email"))
    agora = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    _google_oauth_init_db()
    conn = _auth_db_conexao()
    try:
        conn.execute(
            """
            INSERT INTO google_oauth_tokens (
                username, client_id, google_email, access_token, refresh_token,
                expires_at, scopes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username, client_id) DO UPDATE SET
                google_email=excluded.google_email,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at,
                scopes=excluded.scopes,
                updated_at=excluded.updated_at
            """,
            (
                username_norm,
                client_norm,
                email,
                access_token,
                refresh_token,
                expires_at,
                scopes,
                agora,
            ),
        )
        conn.commit()
    finally:
        conn.close()

def _google_oauth_parse_expiry(expires_at: str) -> Optional[datetime]:
    texto = str(expires_at or "").strip()
    if not texto:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(texto[:20].rstrip("Z"), fmt.replace("Z", ""))
        except Exception:
            continue
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


__all__ = [
    "configure_admin_usuarios_login_core_runtime",
    "_carregar_usuarios_local",
    "verificar_validade_acesso",
    "extrair_permissoes",
    "verificar_trava_seguranca",
    "carregar_usuarios_sheets",
    "_login_senha_confere",
    "_login_validade_ok",
    "_login_usuario_ativo",
    "_firebase_validar_e_registrar_maquina",
    "_login_validar_e_registrar_maquina",
    "_google_login_client_id",
    "_google_login_client_secret",
    "_google_login_scopes",
    "_google_login_verify_id_token",
    "_google_login_redirect_uri",
    "_google_login_configurado",
    "_buscar_usuario_por_email_google",
    "_montar_resposta_login_sucesso",
    "_autenticar_usuario_por_google_info",
    "_google_login_error_redirect",
    "_google_login_success_html",
    "_google_login_poll_html",
    "_google_login_poll_response_payload",
    "_google_login_store_poll_result",
    "_google_login_finish_poll",
    "_google_oauth_init_db",
    "_google_oauth_carregar_tokens",
    "_google_oauth_salvar_tokens_usuario",
    "_google_oauth_parse_expiry",
]
