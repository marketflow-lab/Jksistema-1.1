"""Shared Sync user-scoped add-only merge helpers."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import Depends, Header, HTTPException

from backend.schemas import (
    SharedSyncConfigRequest,
    SharedSyncMachineConfigRequest,
    SharedSyncRunRequest,
    SharedSyncUserInviteActionRequest,
    SharedSyncUserInviteCreateRequest,
    SharedSyncUserLinkRunRequest,
    SharedSyncUserLinkUpdateRequest,
)
from backend.services.runtime_bridge import bind_runtime_globals
from backend.services.favoritos_storage import (
    _favoritos_carregar_historico,
    _favoritos_historico_payload_from_bytes,
    _favoritos_normalizar_historico,
    _favoritos_salvar_historico,
)
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_merge_user_data_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_backup_target(tenant_abs: str, backup_dir: str, rel: str, target_abs: str) -> None:
    if not os.path.exists(target_abs):
        return
    backup_abs = os.path.abspath(os.path.join(backup_dir, rel))
    if not backup_abs.startswith(os.path.abspath(backup_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Backup local contem caminho invalido.")
    os.makedirs(os.path.dirname(backup_abs), exist_ok=True)
    shutil.copy2(target_abs, backup_abs)

def _shared_sync_target_rel_usuario(scope: str, username: str, source_rel: str = "") -> str:
    rels = _shared_sync_user_scoped_rels(scope, username)
    if len(rels) == 1:
        return rels[0]
    lower = os.path.basename(str(source_rel or "")).lower()
    for rel in rels:
        prefix = re.sub(r"_[^_]+\.json$", "_", rel.lower())
        if prefix and lower.startswith(prefix):
            return rel
    raise HTTPException(status_code=400, detail="Escopo nao permite compartilhamento entre usuarios.")

def _shared_sync_json_from_bytes(data: bytes, rel: str) -> Any:
    try:
        return json.loads((data or b"").decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Arquivo JSON invalido no pacote ({rel}): {exc}")

def _shared_sync_historico_key(item: dict) -> str:
    if not isinstance(item, dict):
        return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    base = str(item.get("id") or "").strip()
    if base:
        return base
    bruto = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()

def _shared_sync_merge_historico_usuario(client_id: str, username: str, fontes: list[tuple[str, bytes]]) -> dict:
    atual = _favoritos_carregar_historico(client_id, username)
    por_id: dict[str, dict] = {}
    for entrada in _favoritos_normalizar_historico((atual or {}).get("historico") or []):
        por_id[_shared_sync_historico_key(entrada)] = entrada
    for rel, data in fontes:
        payload = _favoritos_historico_payload_from_bytes(data, rel, username)
        historico_raw = payload.get("historico") if isinstance(payload, dict) else payload
        for entrada in _favoritos_normalizar_historico(historico_raw or []):
            chave = _shared_sync_historico_key(entrada)
            atual_item = por_id.get(chave)
            data_nova = str(entrada.get("data_iso") or entrada.get("updated_at") or "")
            data_atual = str((atual_item or {}).get("data_iso") or (atual_item or {}).get("updated_at") or "")
            if not atual_item or data_nova >= data_atual:
                por_id[chave] = entrada
    historico = sorted(por_id.values(), key=lambda item: str((item or {}).get("data_iso") or ""), reverse=True)
    return _favoritos_salvar_historico(client_id, username, historico)

def _shared_sync_pesquisas_from_payload(payload: Any) -> dict:
    pesquisas = payload.get("pesquisas") if isinstance(payload, dict) else {}
    if not isinstance(pesquisas, dict):
        return {}
    saida: dict[str, dict] = {}
    for chave, item in pesquisas.items():
        if not isinstance(item, dict):
            continue
        chave_txt = str(chave or "").strip()
        sku_raw = item.get("sku") or (chave_txt[5:] if chave_txt.lower().startswith("sku::") else chave_txt)
        sku_norm = _normalizar_sku_match_favoritos(str(sku_raw or "").strip())
        chave_norm = _favoritos_chave_pesquisa_usuario("", sku_norm)
        if not chave_norm:
            continue
        novo = {
            "loja": str(item.get("loja") or "").strip(),
            "sku": sku_norm,
            "produto": _favoritos_limpar_nome_produto(item.get("produto") or item.get("nome") or ""),
            "pesquisa_1": str(item.get("pesquisa_1") or "").strip(),
            "pesquisa_2": str(item.get("pesquisa_2") or "").strip(),
            "pesquisa_3": str(item.get("pesquisa_3") or "").strip(),
            "updated_at": item.get("updated_at"),
        }
        saida[chave_norm] = _favoritos_escolher_pesquisa_usuario(saida.get(chave_norm), novo)
    return saida

def _shared_sync_merge_pesquisas_usuario(client_id: str, username: str, fontes: list[tuple[str, bytes]]) -> dict:
    atual = _favoritos_carregar_pesquisas_usuario(client_id, username)
    pesquisas = dict((atual or {}).get("pesquisas") or {})
    for rel, data in fontes:
        payload = _shared_sync_json_from_bytes(data, rel)
        for chave, item in _shared_sync_pesquisas_from_payload(payload).items():
            pesquisas[chave] = _favoritos_escolher_pesquisa_usuario(pesquisas.get(chave), item)
    agora = datetime.now().isoformat(timespec="seconds")
    caminho = _favoritos_arquivo_pesquisas_usuario(client_id, username)
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump({"pesquisas": pesquisas, "updated_at": agora}, f, ensure_ascii=False, indent=2)
    return {"pesquisas": pesquisas, "updated_at": agora}

def _shared_sync_union_lista_texto(atual: list[str], remoto: list[str], normalizador) -> list[str]:
    saida = list(normalizador(atual))
    vistos = {_shared_sync_texto_chave(item) for item in saida}
    for item in normalizador(remoto):
        chave = _shared_sync_texto_chave(item)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        saida.append(item)
    return saida

def _shared_sync_merge_anuncios_ignorados_add_only(atual: Any, remoto: Any) -> dict[str, list[dict]]:
    merged = _favoritos_normalizar_anuncios_ignorados(atual)
    remoto_norm = _favoritos_normalizar_anuncios_ignorados(remoto)
    for sku, itens in remoto_norm.items():
        destino = merged.setdefault(sku, [])
        vistos = {_shared_sync_anuncio_ignorado_key(sku, item) for item in destino}
        for item in itens or []:
            chave = _shared_sync_anuncio_ignorado_key(sku, item)
            if not chave or chave in vistos:
                continue
            vistos.add(chave)
            destino.append(item)
    return merged

def _shared_sync_merge_anuncios_ml_usuario(
    client_id: str,
    username: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> list[str]:
    escritos = []
    for rel, data in fontes:
        lower = os.path.basename(str(rel or "")).lower()
        target_rel = _shared_sync_target_rel_usuario("anuncios_ml", username, rel)
        target_abs = os.path.abspath(os.path.join(tenant_abs, target_rel))
        if not target_abs.startswith(tenant_abs + os.sep):
            raise HTTPException(status_code=400, detail="Destino de usuario invalido.")
        _shared_sync_backup_target(tenant_abs, backup_dir, target_rel, target_abs)
        payload = _shared_sync_json_from_bytes(data, rel)
        if lower.startswith("favoritos_anuncios_ignorados_"):
            atual = _favoritos_carregar_anuncios_ignorados(client_id, username).get("anuncios_ignorados") or {}
            remoto = payload.get("anuncios_ignorados") if isinstance(payload, dict) and "anuncios_ignorados" in payload else payload
            merged = _shared_sync_merge_anuncios_ignorados_add_only(atual, remoto)
            _favoritos_salvar_anuncios_ignorados(client_id, username, merged)
        elif lower.startswith("favoritos_vendedores_ignorados_"):
            atual = _favoritos_carregar_vendedores_ignorados(client_id, username).get("vendedores_ignorados") or []
            remoto = _shared_sync_lista_str_payload(payload, "vendedores_ignorados", _favoritos_normalizar_vendedores_ignorados)
            merged = _shared_sync_union_lista_texto(atual, remoto, _favoritos_normalizar_vendedores_ignorados)
            _favoritos_salvar_vendedores_ignorados(client_id, username, merged)
        elif lower.startswith("favoritos_skus_ocultos_"):
            atual = _favoritos_carregar_skus_ocultos(client_id, username).get("skus_ocultos") or []
            remoto = _shared_sync_lista_str_payload(payload, "skus_ocultos", _favoritos_normalizar_skus_ocultos)
            merged = _shared_sync_union_lista_texto(atual, remoto, _favoritos_normalizar_skus_ocultos)
            _favoritos_salvar_skus_ocultos(client_id, username, merged)
        else:
            continue
        if target_rel not in escritos:
            escritos.append(target_rel)
    return escritos

def _shared_sync_aplicar_user_scoped_share(
    client_id: str,
    scope: str,
    username: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> dict:
    if not fontes:
        return {"file_count": 0, "files": []}
    if scope == "anuncios_ml":
        arquivos = _shared_sync_merge_anuncios_ml_usuario(client_id, username, fontes, tenant_abs, backup_dir)
        return {
            "file_count": len(arquivos),
            "files": arquivos,
            "shared_source_count": len(fontes),
        }
    target_rel = _shared_sync_target_rel_usuario(scope, username)
    target_abs = os.path.abspath(os.path.join(tenant_abs, target_rel))
    if not target_abs.startswith(tenant_abs + os.sep):
        raise HTTPException(status_code=400, detail="Destino de usuario invalido.")
    _shared_sync_backup_target(tenant_abs, backup_dir, target_rel, target_abs)
    if scope == "favoritos_historico":
        _shared_sync_merge_historico_usuario(client_id, username, fontes)
    elif scope == "sku_campos_pesquisa":
        _shared_sync_merge_pesquisas_usuario(client_id, username, fontes)
    else:
        raise HTTPException(status_code=400, detail="Escopo nao permite compartilhamento entre usuarios.")
    return {
        "file_count": 1,
        "files": [target_rel],
        "shared_source_count": len(fontes),
    }

configure_shared_sync_merge_user_data_runtime()

__all__ = [
    "configure_shared_sync_merge_user_data_runtime",
    "_shared_sync_backup_target",
    "_shared_sync_target_rel_usuario",
    "_shared_sync_json_from_bytes",
    "_shared_sync_historico_key",
    "_shared_sync_merge_historico_usuario",
    "_shared_sync_pesquisas_from_payload",
    "_shared_sync_merge_pesquisas_usuario",
    "_shared_sync_union_lista_texto",
    "_shared_sync_merge_anuncios_ignorados_add_only",
    "_shared_sync_merge_anuncios_ml_usuario",
    "_shared_sync_aplicar_user_scoped_share",
]
