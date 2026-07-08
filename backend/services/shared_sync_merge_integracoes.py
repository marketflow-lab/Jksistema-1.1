"""Shared Sync lojas/integracoes sensitive merge helpers."""

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
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_merge_integracoes_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_valor_preenchido(valor: Any) -> bool:
    return valor is not None and str(valor).strip() != ""

def _shared_sync_lojas_from_payload(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        lojas = payload.get("lojas")
        if isinstance(lojas, list):
            return [item for item in lojas if isinstance(item, dict)]
        if payload.get("nome") or payload.get("integracoes"):
            return [payload]
    return []

def _shared_sync_loja_key(nome: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(nome or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", texto.lower())

def _shared_sync_servico_key(servico: Any) -> str:
    chave = _shared_sync_loja_key(servico)
    if chave in {"ml", "mercadolivre", "mercadolibre"}:
        return "mercadolivre"
    if chave in {"turbo", "mercadoturbo"}:
        return "mercadoturbo"
    if chave == "bling":
        return "bling"
    return str(servico or "").strip()

def _shared_sync_timestamp(valor: Any) -> float:
    try:
        return float(valor or 0)
    except Exception:
        return 0.0

def _shared_sync_normalizar_integracao_conectada(servico_key: str, dados: Any) -> Any:
    if not isinstance(dados, dict):
        return dados
    servico_key = _shared_sync_servico_key(servico_key)
    saida = dict(dados)
    if saida.get("oauth_invalid"):
        saida["connected"] = False
        if not str(saida.get("status") or "").strip():
            saida["status"] = "reautenticacao_necessaria"
        if not str(saida.get("motivo") or "").strip():
            saida["motivo"] = "Token OAuth invalido. Refaça a conexão em Integrações."
        return saida
    if servico_key == "mercadolivre":
        completa = bool(
            str(saida.get("access_token") or "").strip()
            and str(saida.get("refresh_token") or "").strip()
            and str(saida.get("app_id") or saida.get("id") or saida.get("client_id") or "").strip()
            and str(saida.get("client_secret") or saida.get("secret") or "").strip()
        )
    elif servico_key == "bling":
        completa = bool(
            str(saida.get("access_token") or "").strip()
            and str(saida.get("refresh_token") or "").strip()
            and str(saida.get("id") or saida.get("client_id") or "").strip()
            and str(saida.get("secret") or saida.get("client_secret") or "").strip()
        )
    else:
        completa = False
    if completa:
        saida["oauth_invalid"] = False
        saida["connected"] = True
        saida["status"] = "conectado"
        saida["motivo"] = ""
        saida["shared_without_oauth_tokens"] = False
    return saida

def _shared_sync_merge_integracao_loja(atual: Any, remoto: Any, add_only: bool = False, servico_key: str = "") -> Any:
    if not isinstance(remoto, dict):
        return atual if _shared_sync_valor_preenchido(atual) else remoto
    if not isinstance(atual, dict):
        return _shared_sync_normalizar_integracao_conectada(servico_key, _shared_sync_json_clone(remoto))

    merged = dict(atual)
    atual_ts = _shared_sync_timestamp(atual.get("updated_at"))
    remoto_ts = _shared_sync_timestamp(remoto.get("updated_at"))
    remoto_mais_novo = remoto_ts > atual_ts

    for chave, valor in remoto.items():
        atual_valor = merged.get(chave)
        if chave == "connected":
            merged[chave] = bool(atual_valor) or bool(valor)
            continue
        if chave == "updated_at":
            if remoto_ts > atual_ts:
                merged[chave] = valor
            elif "updated_at" not in merged and _shared_sync_valor_preenchido(valor):
                merged[chave] = valor
            continue
        if not _shared_sync_valor_preenchido(atual_valor) and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor
        elif not add_only and remoto_mais_novo and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor
    if servico_key == "bling":
        remoto_access = str(remoto.get("access_token") or "").strip()
        remoto_refresh = str(remoto.get("refresh_token") or "").strip()
        if (
            (remoto_access and remoto_access != str((atual or {}).get("access_token") or "").strip())
            or (remoto_refresh and remoto_refresh != str((atual or {}).get("refresh_token") or "").strip())
        ):
            merged["oauth_invalid"] = False
    return _shared_sync_normalizar_integracao_conectada(servico_key, merged)

def _shared_sync_merge_loja_integracoes(atual: dict, remoto: dict, add_only: bool = False) -> dict:
    merged = dict(_shared_sync_json_clone(atual or {}))
    for chave, valor in (remoto or {}).items():
        if chave == "integracoes":
            continue
        if not _shared_sync_valor_preenchido(merged.get(chave)) and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor

    integracoes = merged.setdefault("integracoes", {})
    if not isinstance(integracoes, dict):
        integracoes = {}
        merged["integracoes"] = integracoes

    for servico, dados in ((remoto or {}).get("integracoes") or {}).items():
        servico_key = _shared_sync_servico_key(servico)
        integracoes[servico_key] = _shared_sync_merge_integracao_loja(integracoes.get(servico_key), dados, add_only=add_only, servico_key=servico_key)
    return merged

def _shared_sync_resumo_lojas_integracoes(lojas: list[dict]) -> dict:
    nomes = set()
    conectadas = set()
    for loja in lojas or []:
        if not isinstance(loja, dict):
            continue
        loja_key = _shared_sync_loja_key(loja.get("nome"))
        if loja_key:
            nomes.add(loja_key)
        integracoes = loja.get("integracoes") if isinstance(loja.get("integracoes"), dict) else {}
        for servico, dados in (integracoes or {}).items():
            if not isinstance(dados, dict) or not dados.get("connected"):
                continue
            servico_key = _shared_sync_servico_key(servico)
            conectadas.add(f"{loja_key}:{servico_key}" if loja_key else servico_key)
    return {"lojas": nomes, "conectadas": conectadas}

def _shared_sync_lojas_config_from_bundle(bundle: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            data = zf.read("files/lojas_config.json")
    except KeyError:
        return []
    payload = _shared_sync_json_from_bytes(data, "lojas_config.json")
    return _shared_sync_lojas_from_payload(payload)

def _shared_sync_validar_push_lojas_integracoes(bundle_id: str, bundle: bytes) -> None:
    try:
        remoto_bundle, _meta = _shared_sync_obter_bundle_por_id(bundle_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return
        logger.warning("[SHARED-SYNC] Nao foi possivel validar regressao de lojas_integracoes: %s", exc.detail)
        return
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Nao foi possivel validar regressao de lojas_integracoes: %s", exc)
        return

    try:
        local = _shared_sync_resumo_lojas_integracoes(_shared_sync_lojas_config_from_bundle(bundle))
        remoto = _shared_sync_resumo_lojas_integracoes(_shared_sync_lojas_config_from_bundle(remoto_bundle))
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao comparar lojas_integracoes antes do push: %s", exc)
        return
    lojas_remotas = remoto.get("lojas") or set()
    lojas_locais = local.get("lojas") or set()
    conectadas_remotas = remoto.get("conectadas") or set()
    conectadas_locais = local.get("conectadas") or set()

    perda_lojas = len(lojas_remotas - lojas_locais)
    perda_conectadas = len(conectadas_remotas - conectadas_locais)
    regressao_lojas = len(lojas_remotas) >= 3 and len(lojas_locais) < len(lojas_remotas) and perda_lojas >= 2
    regressao_conexoes = len(conectadas_remotas) >= 3 and len(conectadas_locais) < len(conectadas_remotas) and perda_conectadas >= 2
    if regressao_lojas or regressao_conexoes:
        raise HTTPException(
            status_code=409,
            detail=(
                "Push de lojas_integracoes bloqueado: o snapshot local parece remover lojas "
                "ou conexoes Bling/Mercado Livre existentes no remoto. Restaure ou confirme "
                "as lojas antes de sincronizar."
            ),
        )

def _shared_sync_merge_lojas_integracoes_bytes(target_abs: str, remoto_bytes: bytes, add_only: bool = False) -> bytes:
    remoto_payload = _shared_sync_json_from_bytes(remoto_bytes, "lojas_config.json")
    remoto_lojas = _shared_sync_lojas_from_payload(remoto_payload)
    atual_lojas: list[dict] = []

    if os.path.exists(target_abs):
        with open(target_abs, "rb") as f:
            atual_payload = _shared_sync_json_from_bytes(f.read(), "lojas_config.json atual")
        atual_lojas = _shared_sync_lojas_from_payload(atual_payload)

    if atual_lojas and not remoto_lojas:
        logger.warning("[SHARED-SYNC] Pacote remoto de lojas vazio ignorado para preservar integracoes locais.")
        merged = atual_lojas
    else:
        merged = [_shared_sync_json_clone(loja) for loja in atual_lojas]
        indice = {
            _shared_sync_loja_key(loja.get("nome")): idx
            for idx, loja in enumerate(merged)
            if isinstance(loja, dict) and _shared_sync_loja_key(loja.get("nome"))
        }

        for loja_remota in remoto_lojas:
            chave = _shared_sync_loja_key(loja_remota.get("nome"))
            idx = indice.get(chave)
            if idx is None:
                idx = next(
                    (
                        pos
                        for pos, loja_atual in enumerate(merged)
                        if _shared_sync_loja_key(loja_atual.get("nome"))
                        and chave
                        and (
                            _shared_sync_loja_key(loja_atual.get("nome")) in chave
                            or chave in _shared_sync_loja_key(loja_atual.get("nome"))
                        )
                    ),
                    None,
                )
            if idx is None:
                merged.append(_shared_sync_json_clone(loja_remota))
                if chave:
                    indice[chave] = len(merged) - 1
                continue
            merged[idx] = _shared_sync_merge_loja_integracoes(merged[idx], loja_remota, add_only=add_only)

    return json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8")

configure_shared_sync_merge_integracoes_runtime()

__all__ = [
    "configure_shared_sync_merge_integracoes_runtime",
    "_shared_sync_valor_preenchido",
    "_shared_sync_lojas_from_payload",
    "_shared_sync_loja_key",
    "_shared_sync_servico_key",
    "_shared_sync_timestamp",
    "_shared_sync_normalizar_integracao_conectada",
    "_shared_sync_merge_integracao_loja",
    "_shared_sync_merge_loja_integracoes",
    "_shared_sync_resumo_lojas_integracoes",
    "_shared_sync_lojas_config_from_bundle",
    "_shared_sync_validar_push_lojas_integracoes",
    "_shared_sync_merge_lojas_integracoes_bytes",
]
