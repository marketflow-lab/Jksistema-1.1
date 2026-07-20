"""Shared Sync CSV, JSON, favoritos, lojas, and SQLite delta helpers."""

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
    _favoritos_historico_payload_from_bytes,
    _favoritos_historico_sqlite_bytes_from_payload,
    _favoritos_normalizar_historico,
)
from backend.services.shared_sync_common import *
from backend.services.shared_sync_context import configure_shared_sync_context, get_tenant_id


def configure_shared_sync_delta_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_texto_chave(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto.strip().lower())

def _shared_sync_col_norm(coluna: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(coluna or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", texto.lower())

def _shared_sync_csv_read_bytes(data: bytes) -> pd.DataFrame:
    tentativas = [
        {"sep": None, "encoding": "utf-8-sig", "engine": "python"},
        {"sep": None, "encoding": "utf-8", "engine": "python"},
        {"sep": None, "encoding": "latin1", "engine": "python"},
        {"sep": ";", "encoding": "utf-8-sig", "engine": "python"},
        {"sep": ";", "encoding": "utf-8", "engine": "python"},
        {"sep": ";", "encoding": "latin1", "engine": "python"},
        {"sep": ",", "encoding": "utf-8-sig", "engine": "python"},
        {"sep": ",", "encoding": "utf-8", "engine": "python"},
        {"sep": ",", "encoding": "latin1", "engine": "python"},
    ]
    melhor_df = None
    melhor_score = -1
    ultimo_erro = None
    for cfg in tentativas:
        try:
            df = pd.read_csv(io.BytesIO(data or b""), dtype=str, on_bad_lines="skip", **cfg).fillna("")
            score = len(df.columns) + (1000 if any(_shared_sync_col_norm(c) == "sku" for c in df.columns) else 0)
            if score > melhor_score:
                melhor_df = df
                melhor_score = score
            if not df.empty and any(_shared_sync_col_norm(c) == "sku" for c in df.columns):
                break
        except Exception as exc:
            ultimo_erro = exc
    if melhor_df is None:
        raise HTTPException(status_code=502, detail=f"CSV invalido no pacote de compartilhamento: {ultimo_erro}")
    return melhor_df.fillna("")

def _shared_sync_csv_key_columns(columns: list[Any]) -> tuple[Optional[str], Optional[str]]:
    sku_col = None
    loja_col = None
    for col in columns:
        norm = _shared_sync_col_norm(col)
        if sku_col is None and norm in {"sku", "codigosku", "codigo"}:
            sku_col = str(col)
        if loja_col is None and norm in {"loja", "lojaconta", "lojavirtual", "lojanome", "marketplace", "conta"}:
            loja_col = str(col)
    return sku_col, loja_col

def _shared_sync_csv_row_key(scope: str, rel: str, row: Any, columns: list[Any]) -> str:
    sku_col, loja_col = _shared_sync_csv_key_columns(columns)
    if sku_col and _shared_sync_texto_chave(row.get(sku_col)):
        partes = [scope, rel, "sku", _shared_sync_texto_chave(row.get(sku_col))]
        if loja_col and _shared_sync_texto_chave(row.get(loja_col)):
            partes.extend(["loja", _shared_sync_texto_chave(row.get(loja_col))])
        return ":".join(partes)
    bruto = json.dumps({str(col): str(row.get(col) or "") for col in columns}, ensure_ascii=False, sort_keys=True)
    return f"{scope}:{rel}:row:{hashlib.sha256(bruto.encode('utf-8')).hexdigest()}"

def _shared_sync_csv_delta_bytes(scope: str, rel: str, data: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    df = _shared_sync_csv_read_bytes(data)
    if df.empty:
        return None, []
    selected_idx = []
    selected_keys = []
    vistos_lote = set()
    columns = list(df.columns)
    for idx, row in df.iterrows():
        chave = _shared_sync_csv_row_key(scope, rel, row, columns)
        if chave in known_keys or chave in vistos_lote:
            continue
        vistos_lote.add(chave)
        selected_idx.append(idx)
        selected_keys.append(chave)
    if not selected_idx:
        return None, []
    saida = df.loc[selected_idx].copy()
    return saida.to_csv(index=False).encode("utf-8-sig"), selected_keys

def _shared_sync_json_dump_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

def _shared_sync_sanitizar_oauth_integracao_usuario(servico: str, dados: Any) -> Any:
    if not isinstance(dados, dict):
        return _shared_sync_json_clone(dados)
    servico_key = _shared_sync_servico_key(servico)
    saida = _shared_sync_json_clone(dados)
    if servico_key not in {"bling", "mercadolivre"}:
        return saida
    token_keys = {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_at",
        "expires_in",
        "authorization",
        "jwt",
    }
    tinha_token = any(_shared_sync_valor_preenchido(saida.get(chave)) for chave in token_keys)
    for chave in token_keys:
        saida.pop(chave, None)
    if tinha_token or saida.get("connected"):
        saida["connected"] = False
        saida["status"] = "reautenticacao_necessaria"
        saida["motivo"] = (
            "Loja recebida por compartilhamento. Tokens OAuth da integracao nao sao reutilizados "
            "entre usuarios; autentique esta loja nesta instalacao."
        )
        saida["shared_without_oauth_tokens"] = True
    return saida

def _shared_sync_sanitizar_lojas_integracoes_user_share_bytes(data: bytes) -> bytes:
    payload = _shared_sync_json_from_bytes(data, "lojas_config.json")
    lojas = _shared_sync_lojas_from_payload(payload)
    sanitizadas = []
    for loja in lojas:
        nova_loja = _shared_sync_json_clone(loja)
        integracoes = nova_loja.get("integracoes") if isinstance(nova_loja.get("integracoes"), dict) else {}
        novas_integracoes = {}
        for servico, dados in (integracoes or {}).items():
            servico_key = _shared_sync_servico_key(servico)
            novas_integracoes[servico_key] = _shared_sync_sanitizar_oauth_integracao_usuario(servico_key, dados)
        if integracoes:
            nova_loja["integracoes"] = novas_integracoes
        sanitizadas.append(nova_loja)
    if isinstance(payload, dict) and isinstance(payload.get("lojas"), list):
        saida = _shared_sync_json_clone(payload)
        saida["lojas"] = sanitizadas
        return _shared_sync_json_dump_bytes(saida)
    if isinstance(payload, dict) and (payload.get("nome") or payload.get("integracoes")):
        return _shared_sync_json_dump_bytes(sanitizadas[0] if sanitizadas else payload)
    return _shared_sync_json_dump_bytes(sanitizadas)

def _shared_sync_favoritos_delta_bytes(scope: str, rel: str, data: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    payload = _favoritos_historico_payload_from_bytes(data, rel)
    historico_raw = payload.get("historico") if isinstance(payload, dict) else payload
    historico = []
    keys = []
    vistos_lote = set()
    for entrada in _favoritos_normalizar_historico(historico_raw or []):
        chave = f"{scope}:{_shared_sync_historico_key(entrada)}"
        if chave in known_keys or chave in vistos_lote:
            continue
        vistos_lote.add(chave)
        historico.append(entrada)
        keys.append(chave)
    if not historico:
        return None, []
    out = dict(payload) if isinstance(payload, dict) else {}
    out["historico"] = historico
    out["updated_at"] = _shared_sync_now_iso()
    if str(rel or "").lower().endswith((".db", ".sqlite", ".sqlite3")):
        return _favoritos_historico_sqlite_bytes_from_payload(out), keys
    return _shared_sync_json_dump_bytes(out), keys

def _shared_sync_pesquisas_delta_bytes(scope: str, rel: str, data: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    pesquisas = _shared_sync_pesquisas_from_payload(_shared_sync_json_from_bytes(data, rel))
    filtradas = {}
    keys = []
    for chave_pesquisa, item in pesquisas.items():
        chave = f"{scope}:{chave_pesquisa}"
        if chave in known_keys:
            continue
        filtradas[chave_pesquisa] = item
        keys.append(chave)
    if not filtradas:
        return None, []
    return _shared_sync_json_dump_bytes({"pesquisas": filtradas, "updated_at": _shared_sync_now_iso()}), keys

def _shared_sync_anuncio_ignorado_key(sku: str, item: dict) -> str:
    if not isinstance(item, dict):
        bruto = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()
    item_id = str(item.get("id") or "").strip().upper()
    if item_id:
        return f"id:{item_id}"
    chaves = item.get("chaves") if isinstance(item.get("chaves"), list) else []
    for chave in chaves:
        chave_txt = str(chave or "").strip().lower()
        if chave_txt:
            return f"chave:{chave_txt}"
    base = "|".join([
        str(sku or "").strip().lower(),
        str(item.get("titulo") or "").strip().lower(),
        str(item.get("vendedor") or "").strip().lower(),
        str(item.get("url") or "").strip().lower(),
    ])
    return "hash:" + hashlib.sha256(base.encode("utf-8")).hexdigest()

def _shared_sync_lista_str_payload(payload: Any, key: str, normalizador) -> list[str]:
    bruto = payload.get(key) if isinstance(payload, dict) and key in payload else payload
    return normalizador(bruto)

def _shared_sync_anuncios_ml_delta_bytes(scope: str, rel: str, data: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    payload = _shared_sync_json_from_bytes(data, rel)
    lower = os.path.basename(str(rel or "")).lower()
    if lower.startswith("favoritos_anuncios_ignorados_"):
        anuncios = _favoritos_normalizar_anuncios_ignorados(
            payload.get("anuncios_ignorados") if isinstance(payload, dict) and "anuncios_ignorados" in payload else payload
        )
        filtrados: dict[str, list[dict]] = {}
        keys = []
        vistos_lote = set()
        for sku, itens in anuncios.items():
            for item in itens or []:
                item_key = _shared_sync_anuncio_ignorado_key(sku, item)
                chave = f"{scope}:anuncio_ignorado:{sku}:{item_key}"
                if chave in known_keys or chave in vistos_lote:
                    continue
                vistos_lote.add(chave)
                filtrados.setdefault(sku, []).append(item)
                keys.append(chave)
        if not filtrados:
            return None, []
        return _shared_sync_json_dump_bytes({"anuncios_ignorados": filtrados, "updated_at": _shared_sync_now_iso()}), keys

    if lower.startswith("favoritos_vendedores_ignorados_"):
        vendedores = _shared_sync_lista_str_payload(payload, "vendedores_ignorados", _favoritos_normalizar_vendedores_ignorados)
        filtrados = []
        keys = []
        for vendedor in vendedores:
            chave = f"{scope}:vendedor_ignorado:{_shared_sync_texto_chave(vendedor)}"
            if chave in known_keys:
                continue
            filtrados.append(vendedor)
            keys.append(chave)
        if not filtrados:
            return None, []
        return _shared_sync_json_dump_bytes({"vendedores_ignorados": filtrados, "updated_at": _shared_sync_now_iso()}), keys

    if lower.startswith("favoritos_skus_ocultos_"):
        skus = _shared_sync_lista_str_payload(payload, "skus_ocultos", _favoritos_normalizar_skus_ocultos)
        filtrados = []
        keys = []
        for sku in skus:
            chave = f"{scope}:sku_oculto:{_normalizar_sku_match_favoritos(sku)}"
            if chave in known_keys:
                continue
            filtrados.append(sku)
            keys.append(chave)
        if not filtrados:
            return None, []
        return _shared_sync_json_dump_bytes({"skus_ocultos": filtrados, "updated_at": _shared_sync_now_iso()}), keys

    return None, []

def _shared_sync_lojas_delta_bytes(scope: str, rel: str, data: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    payload = _shared_sync_json_from_bytes(data, rel)
    lojas = _shared_sync_lojas_from_payload(payload)
    if not lojas:
        return None, []
    filtradas = []
    keys = []
    for loja in lojas:
        loja_key = _shared_sync_loja_key(loja.get("nome"))
        loja_chave = f"{scope}:loja:{loja_key}" if loja_key else ""
        nova_loja = None
        if loja_chave and loja_chave not in known_keys:
            nova_loja = _shared_sync_json_clone(loja)
            keys.append(loja_chave)
        else:
            integracoes = loja.get("integracoes") if isinstance(loja.get("integracoes"), dict) else {}
            novas_integracoes = {}
            for servico, dados in integracoes.items():
                servico_key = _shared_sync_servico_key(servico)
                chave = f"{scope}:integracao:{loja_key}:{servico_key}"
                if chave in known_keys:
                    continue
                novas_integracoes[servico_key] = _shared_sync_json_clone(dados)
                keys.append(chave)
            if novas_integracoes:
                nova_loja = _shared_sync_json_clone({k: v for k, v in loja.items() if k != "integracoes"})
                nova_loja["integracoes"] = novas_integracoes
        if nova_loja:
            filtradas.append(nova_loja)
    if not filtradas:
        return None, []
    return _shared_sync_json_dump_bytes(filtradas), keys

_SHARED_SYNC_VENDAS_DELTA_TABLES = ("vendas", "notas_entrada", "notas_entrada_itens")


def _shared_sync_vendas_delta_key(rel: str, table: str, row_id: str) -> str:
    # Mantem a chave historica da tabela vendas para nao reenviar itens que ja
    # constam em manifests antigos. As duas tabelas novas recebem namespace.
    if table == "vendas":
        return f"vendas:{rel}:id:{row_id}"
    return f"vendas:{rel}:{table}:id:{row_id}"


def _shared_sync_vendas_delta_db_bytes(rel: str, sqlite_bytes: bytes, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    if not sqlite_bytes:
        return None, []
    src_tmp = _shared_sync_sqlite_temp_from_bytes(sqlite_bytes, "shared_sync_vendas_source_")
    tmp_path = ""
    src = None
    try:
        src = sqlite3.connect(src_tmp, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        _shared_sync_sqlite_configure(src)
        _shared_sync_sqlite_quick_check(src, rel)
        cur = src.cursor()
        selected_tables = []
        keys: list[str] = []
        batch_keys = set()
        for table in _SHARED_SYNC_VENDAS_DELTA_TABLES:
            table_ident = '"' + table.replace('"', '""') + '"'
            create_row = cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,),
            ).fetchone()
            if not create_row:
                continue
            cols_info = cur.execute(f"PRAGMA table_info({table_ident})").fetchall()
            cols = [str(row[1]) for row in cols_info]
            if "id_unico" not in cols:
                raise HTTPException(status_code=502, detail=f"Tabela {table} sem id_unico no banco de vendas.")
            quoted_cols = ", ".join('"' + col.replace('"', '""') + '"' for col in cols)
            id_idx = cols.index("id_unico")
            selected_rows = []
            table_keys = []
            for row in cur.execute(f"SELECT {quoted_cols} FROM {table_ident}"):
                row_id = str(row[id_idx] or "").strip()
                if not row_id:
                    raise HTTPException(status_code=502, detail=f"Tabela {table} contem id_unico vazio.")
                key = _shared_sync_vendas_delta_key(rel, table, row_id)
                if key in known_keys or key in batch_keys:
                    continue
                batch_keys.add(key)
                selected_rows.append(tuple(row))
                table_keys.append(key)
            if selected_rows:
                selected_tables.append((table, str(create_row[0] or ""), cols, selected_rows))
                keys.extend(table_keys)

        if not selected_tables:
            return None, []

        fd, tmp_path = tempfile.mkstemp(prefix="shared_sync_vendas_delta_", suffix=".db")
        os.close(fd)
        dst = sqlite3.connect(tmp_path)
        try:
            _shared_sync_sqlite_configure(dst)
            dst.execute("BEGIN IMMEDIATE")
            try:
                for table, create_sql, cols, rows in selected_tables:
                    if not create_sql:
                        raise HTTPException(status_code=502, detail=f"Schema da tabela {table} ausente no banco de vendas.")
                    dst.execute(create_sql)
                    table_ident = '"' + table.replace('"', '""') + '"'
                    quoted_cols = ", ".join('"' + col.replace('"', '""') + '"' for col in cols)
                    placeholders = ", ".join(["?"] * len(cols))
                    dst.executemany(
                        f"INSERT OR IGNORE INTO {table_ident} ({quoted_cols}) VALUES ({placeholders})",
                        rows,
                    )
                dst.commit()
            except BaseException:
                dst.rollback()
                raise
            _shared_sync_sqlite_quick_check(dst, rel)
        finally:
            dst.close()
        with open(tmp_path, "rb") as file:
            return file.read(), keys
    finally:
        if src is not None:
            src.close()
        try:
            os.remove(src_tmp)
        except OSError:
            pass
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def _shared_sync_delta_for_entry(scope: str, entry: dict, known_keys: set[str]) -> tuple[Optional[bytes], list[str]]:
    rel = entry.get("relative_path") or ""
    abs_path = entry.get("abs_path") or ""
    data = entry.get("data") if "data" in entry else _shared_sync_ler_arquivo_pacote(abs_path)
    lower = rel.lower()
    if scope == "cadastro" and lower.endswith(".csv"):
        return _shared_sync_csv_delta_bytes(scope, rel, data, known_keys)
    if scope == "vendas":
        if lower.endswith((".db", ".sqlite", ".sqlite3")):
            return _shared_sync_vendas_delta_db_bytes(rel, data or b"", known_keys)
        return None, []
    if scope == "favoritos_historico":
        return _shared_sync_favoritos_delta_bytes(scope, rel, data, known_keys)
    if scope == "sku_campos_pesquisa":
        return _shared_sync_pesquisas_delta_bytes(scope, rel, data, known_keys)
    if scope == "anuncios_ml":
        return _shared_sync_anuncios_ml_delta_bytes(scope, rel, data, known_keys)
    if scope == "lojas_integracoes" and lower == "lojas_config.json":
        return _shared_sync_lojas_delta_bytes(scope, rel, data, known_keys)

    chave = f"{scope}:file:{rel.lower()}"
    if chave in known_keys:
        return None, []
    return data, [chave]

def _shared_sync_coletar_arquivos_delta(
    client_id: str,
    scope: str,
    username: str = "",
    user_only: bool = False,
    known_keys: Optional[set[str]] = None,
) -> tuple[list[dict], list[str], list[str]]:
    entries, warnings = _shared_sync_coletar_arquivos(client_id, scope, username=username, user_only=user_only)
    conhecidos = set(known_keys or set())
    saida = []
    item_keys = []
    for entry in entries:
        rel = entry.get("relative_path") or ""
        try:
            data_filtrada, keys = _shared_sync_delta_for_entry(scope, entry, conhecidos)
        except HTTPException:
            raise
        except sqlite3.DatabaseError as exc:
            raise HTTPException(status_code=502, detail=f"{rel} invalido para sincronizacao: {exc}") from exc
        except Exception as exc:
            warnings.append(f"{rel} ignorado no delta: {exc}")
            continue
        if not data_filtrada and not keys:
            continue
        keys = [str(key or "").strip() for key in (keys or []) if str(key or "").strip()]
        item_keys.extend(keys)
        conhecidos.update(keys)
        saida.append(_shared_sync_entry_from_bytes(rel, data_filtrada or b"", entry.get("mtime") or time.time(), keys))
    saida.sort(key=lambda item: item["relative_path"])
    return saida, warnings, item_keys

configure_shared_sync_delta_runtime()

__all__ = [
    "configure_shared_sync_delta_runtime",
    "_shared_sync_texto_chave",
    "_shared_sync_col_norm",
    "_shared_sync_csv_read_bytes",
    "_shared_sync_csv_key_columns",
    "_shared_sync_csv_row_key",
    "_shared_sync_csv_delta_bytes",
    "_shared_sync_json_dump_bytes",
    "_shared_sync_sanitizar_oauth_integracao_usuario",
    "_shared_sync_sanitizar_lojas_integracoes_user_share_bytes",
    "_shared_sync_favoritos_delta_bytes",
    "_shared_sync_pesquisas_delta_bytes",
    "_shared_sync_anuncio_ignorado_key",
    "_shared_sync_lista_str_payload",
    "_shared_sync_anuncios_ml_delta_bytes",
    "_shared_sync_lojas_delta_bytes",
    "_shared_sync_vendas_delta_key",
    "_shared_sync_vendas_delta_db_bytes",
    "_shared_sync_delta_for_entry",
    "_shared_sync_coletar_arquivos_delta",
]
