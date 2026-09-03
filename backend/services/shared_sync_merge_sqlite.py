"""Shared Sync CSV and SQLite add-only merge helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlsplit

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
from backend.services.shared_sync_delta import (
    _SHARED_SYNC_CADASTRO_CUSTOS_REL,
    _SHARED_SYNC_CADASTRO_LOJAS_COLUMNS,
    _SHARED_SYNC_CADASTRO_LOJAS_REL,
    _shared_sync_cadastro_custos_columns,
    _shared_sync_cadastro_custos_content_digest,
    _shared_sync_cadastro_custos_row_key,
    _shared_sync_cadastro_lojas_content_digest,
    _shared_sync_cadastro_lojas_columns,
    _shared_sync_cadastro_lojas_row_key,
    _shared_sync_csv_read_bytes,
    _shared_sync_is_cadastro_custos_csv,
    _shared_sync_is_cadastro_lojas_csv,
)


_LOG = logging.getLogger(__name__)


@contextmanager
def _shared_sync_bloquear_writer_store_id(
    client_id: str,
    tenant_abs: str,
):
    """Hold store config, catalog, costs and photo transition in one order."""

    from backend.services import integracoes

    tenant_path = os.path.abspath(str(tenant_abs or ""))
    with integracoes._LOJAS_CONFIG_LOCK:
        with integracoes._integracoes_bloquear_catalogo_e_transicao_fotos(
            client_id,
            tenant_path,
        ):
            yield


def configure_shared_sync_merge_sqlite_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_merge_csv_add_only(target_abs: str, remoto_bytes: bytes, scope: str, rel: str) -> dict:
    # The complete read/merge/write cycle must share the canonical file lock
    # with Cadastro, stock and NCM; locking only the final replace can lose a
    # local row written after the initial read.
    with _shared_sync_path_lock_for(target_abs):
        return _shared_sync_merge_csv_add_only_locked(target_abs, remoto_bytes, scope, rel)


def _shared_sync_merge_csv_add_only_locked(target_abs: str, remoto_bytes: bytes, scope: str, rel: str) -> dict:
    remoto_df = _shared_sync_csv_read_bytes(remoto_bytes)
    if remoto_df.empty:
        return {"added": 0, "total": 0}
    if os.path.exists(target_abs):
        with open(target_abs, "rb") as f:
            atual_df = _shared_sync_csv_read_bytes(f.read())
    else:
        atual_df = pd.DataFrame(columns=list(remoto_df.columns))

    colunas = []
    for col in list(atual_df.columns) + list(remoto_df.columns):
        if col not in colunas:
            colunas.append(col)
    for col in colunas:
        if col not in atual_df.columns:
            atual_df[col] = ""
        if col not in remoto_df.columns:
            remoto_df[col] = ""
    atual_df = atual_df[colunas].fillna("")
    remoto_df = remoto_df[colunas].fillna("")

    existentes = {
        _shared_sync_csv_row_key(scope, rel, row, colunas)
        for _idx, row in atual_df.iterrows()
    }
    novas = []
    vistos_lote = set()
    for _idx, row in remoto_df.iterrows():
        chave = _shared_sync_csv_row_key(scope, rel, row, colunas)
        if chave in existentes or chave in vistos_lote:
            continue
        vistos_lote.add(chave)
        novas.append(row.to_dict())

    if not novas and os.path.exists(target_abs):
        return {"added": 0, "total": len(atual_df)}

    merged = pd.concat([atual_df, pd.DataFrame(novas, columns=colunas)], ignore_index=True) if novas else atual_df
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    merged.to_csv(target_abs, index=False, encoding="utf-8-sig")
    return {"added": len(novas), "total": len(merged)}


def _shared_sync_validar_store_ids_df_remoto_locked(
    tenant_abs: str,
    remoto_df: pd.DataFrame,
    rel: str,
    *,
    exigir_store_id: bool = False,
) -> None:
    """Reject rows for stores absent from the current config or tombstoned.

    The caller must retain ``integracoes._LOJAS_CONFIG_LOCK`` until the file
    merge/replace finishes.  This makes store deletion and Shared Sync writes
    linearizable: whichever acquires the configuration lock first wins, and
    the second operation observes the first one's persisted state.
    """

    store_column = next(
        (
            column
            for column in remoto_df.columns
            if str(column or "").strip().casefold() == "store_id"
        ),
        None,
    )
    if store_column is None:
        if exigir_store_id and not remoto_df.empty:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_sync_store_id_required",
                    "message": "Shared Sync bloqueado: store_id e obrigatorio neste arquivo.",
                    "file": str(rel or ""),
                },
            )
        return
    if exigir_store_id and any(
        not str(value or "").strip()
        for value in remoto_df[store_column].tolist()
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_id_required",
                "message": "Shared Sync bloqueado: ha linha sem store_id neste arquivo.",
                "file": str(rel or ""),
            },
        )
    store_ids_remotos = {
        str(value or "").strip()
        for value in remoto_df[store_column].tolist()
        if str(value or "").strip()
    }
    if not store_ids_remotos:
        return

    config_path = os.path.join(tenant_abs, "lojas_config.json")
    tombstones_path = os.path.join(tenant_abs, "lojas_sync_tombstones.json")
    try:
        with open(config_path, "r", encoding="utf-8-sig") as file:
            lojas = json.load(file)
        if not isinstance(lojas, list):
            raise ValueError("lojas_config_nao_e_lista")
        store_ids_atuais = {
            str((loja or {}).get("store_id") or "").strip()
            for loja in lojas
            if isinstance(loja, dict)
            and str((loja or {}).get("store_id") or "").strip()
        }

        tombstones: Any = []
        if os.path.exists(tombstones_path):
            with open(tombstones_path, "r", encoding="utf-8-sig") as file:
                tombstones = json.load(file)
            if not isinstance(tombstones, list):
                raise ValueError("tombstones_nao_e_lista")
        store_ids_tombstonados = {
            str((item or {}).get("store_id") or "").strip()
            for item in tombstones
            if isinstance(item, dict)
            and str((item or {}).get("store_id") or "").strip()
            and (
                str((item or {}).get("type") or "").strip().casefold() == "store"
                or str((item or {}).get("key") or "").strip().casefold().startswith("store:")
            )
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_identity_unverifiable",
                "message": "Shared Sync bloqueado: nao foi possivel validar as lojas atuais.",
                "file": str(rel or ""),
            },
        ) from exc

    tombstonados = store_ids_remotos & store_ids_tombstonados
    ausentes = store_ids_remotos - store_ids_atuais
    if ausentes or tombstonados:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_identity_invalid",
                "message": (
                    "Shared Sync bloqueado: o arquivo remoto referencia loja "
                    "removida, tombstonada ou inexistente."
                ),
                "file": str(rel or ""),
                "store_ids_ausentes": sorted(ausentes),
                "store_ids_tombstonados": sorted(tombstonados),
            },
        )


def _shared_sync_validar_store_ids_csv_remoto_locked(
    tenant_abs: str,
    remoto_bytes: bytes,
    rel: str,
    *,
    exigir_store_id: bool = False,
) -> None:
    remoto_df = _shared_sync_csv_read_bytes(remoto_bytes)
    _shared_sync_validar_store_ids_df_remoto_locked(
        tenant_abs,
        remoto_df,
        rel,
        exigir_store_id=exigir_store_id,
    )


def _shared_sync_exigir_produtos_compilado_sem_fotos_locais_locked(
    client_id: str,
    tenant_abs: str,
    remoto_bytes: bytes,
    rel: str,
) -> None:
    """Reject legacy local photo references received after store scope is strict.

    Current peers sanitize these fields while building the bundle. This guard
    protects receivers from older or malicious peers and must run before any
    backup or merge of ``produtos_compilado.csv``.
    """

    from backend.services.cadastro_fotos import (
        CADASTRO_FOTOS_CONFIG_ARQUIVO,
        _cadastro_foto_coluna_candidata,
        _cadastro_foto_referencia_local_cadastro,
        _cadastro_fotos_escopo_estrito,
    )

    if not os.path.lexists(
        os.path.join(tenant_abs, CADASTRO_FOTOS_CONFIG_ARQUIVO)
    ):
        return
    if not _cadastro_fotos_escopo_estrito(client_id):
        return

    remoto_df = _shared_sync_csv_read_bytes(remoto_bytes)
    foto_columns = [
        column
        for column in remoto_df.columns
        if _cadastro_foto_coluna_candidata(column)
    ]
    if not foto_columns:
        return

    sku_column = next(
        (
            column
            for column in remoto_df.columns
            if _shared_sync_col_norm(column) in {"sku", "codigosku", "codigo"}
        ),
        None,
    )
    campos: set[str] = set()
    skus: set[str] = set()
    for _index, row in remoto_df.iterrows():
        campos_linha = [
            str(column)
            for column in foto_columns
            if _cadastro_foto_referencia_local_cadastro(row.get(column))
        ]
        if not campos_linha:
            continue
        campos.update(campos_linha)
        if sku_column is not None:
            sku = str(row.get(sku_column) or "").strip()
            if sku:
                skus.add(sku)

    if not campos:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "shared_sync_compiled_local_photo_reference_forbidden",
            "message": (
                "Shared Sync bloqueado: produtos_compilado.csv contem "
                "referencia local de foto em escopo separado por loja."
            ),
            "file": str(rel or ""),
            "campos": sorted(campos),
            "skus": sorted(skus),
        },
    )


def _shared_sync_csv_skus(data: bytes) -> list[str]:
    df = _shared_sync_csv_read_bytes(data)
    sku_column = next(
        (
            column
            for column in df.columns
            if _shared_sync_col_norm(column) in {"sku", "codigosku", "codigo"}
        ),
        None,
    )
    if sku_column is None:
        return []
    return [str(value or "").strip() for value in df[sku_column].tolist() if str(value or "").strip()]


def _shared_sync_exigir_fotos_legadas_sem_referencia_local_locked(
    client_id: str,
    remoto_df: pd.DataFrame,
) -> None:
    """Reject local photo paths from a legacy CSV once store scope is strict."""

    from backend.services.cadastro_fotos import (
        _cadastro_foto_coluna_candidata,
        _cadastro_foto_referencia_local_cadastro,
        _cadastro_fotos_escopo_estrito,
    )

    foto_columns = [
        column
        for column in remoto_df.columns
        if _cadastro_foto_coluna_candidata(column)
    ]
    if not foto_columns:
        return
    if not _cadastro_fotos_escopo_estrito(client_id):
        return

    sku_column = next(
        (
            column
            for column in remoto_df.columns
            if _shared_sync_col_norm(column) in {"sku", "codigosku", "codigo"}
        ),
        None,
    )
    skus = sorted(
        {
            str(row.get(sku_column) or "").strip()
            for _index, row in remoto_df.iterrows()
            if any(
                _cadastro_foto_referencia_local_cadastro(row.get(column))
                for column in foto_columns
            )
            and sku_column is not None
            and str(row.get(sku_column) or "").strip()
        }
    )
    referencias_locais = any(
        _cadastro_foto_referencia_local_cadastro(row.get(column))
        for _index, row in remoto_df.iterrows()
        for column in foto_columns
    )
    if not referencias_locais:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "store_id_required",
            "message": (
                "Shared Sync bloqueado: referencias locais de foto exigem "
                "cadastro separado por loja e store_id."
            ),
            "skus": skus,
        },
    )


@contextmanager
def _shared_sync_guard_legacy_cadastro_csv_locked(
    client_id: str,
    target_abs: str,
    remoto_bytes: bytes,
):
    """Acquire canonical Cadastro before target and revalidate current rows."""

    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
        exigir_mutacao_legada_sem_campos_loja,
        exigir_mutacao_legada_sem_sku_controlado,
    )

    remoto_df = _shared_sync_csv_read_bytes(remoto_bytes)
    exigir_mutacao_legada_sem_campos_loja(remoto_df.columns)
    skus_remotos = _shared_sync_csv_skus(remoto_bytes)
    tenant_abs = os.path.dirname(os.path.abspath(target_abs))
    with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
        with bloquear_mutacao_legada_sem_sku_controlado(client_id, skus_remotos):
            _shared_sync_exigir_fotos_legadas_sem_referencia_local_locked(
                client_id,
                remoto_df,
            )
            with _shared_sync_path_lock_for(target_abs):
                skus_atuais: list[str] = []
                if os.path.exists(target_abs):
                    with open(target_abs, "rb") as file:
                        skus_atuais = _shared_sync_csv_skus(file.read())
                exigir_mutacao_legada_sem_sku_controlado(client_id, skus_atuais)
                yield


def _shared_sync_legacy_photo_sku(rel: str) -> str:
    from backend.services.cadastro_fotos import CADASTRO_FOTOS_EXTENSOES

    normalized = str(rel or "").strip().replace("\\", "/").strip("/")
    if not normalized.casefold().startswith("cadastro_fotos/"):
        return ""
    if normalized.casefold().startswith("cadastro_fotos/lojas/"):
        return ""
    basename = os.path.basename(normalized)
    stem, extension = os.path.splitext(basename)
    if not stem or extension.casefold() not in CADASTRO_FOTOS_EXTENSOES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_legacy_photo_identity_unverifiable",
                "message": "Shared Sync bloqueado: nao foi possivel provar o SKU da foto global.",
                "file": normalized,
            },
        )
    return stem


def _shared_sync_photo_referencia_local_equivale(
    raw: Any,
    rel: str,
    client_id: str = "",
) -> bool:
    referencia = str(raw or "").strip().replace("\\", "/")
    alvo = str(rel or "").strip().replace("\\", "/").strip("/")
    if not referencia or not alvo:
        return False
    for _ in range(3):
        decoded = unquote(referencia)
        if decoded == referencia:
            break
        referencia = decoded.replace("\\", "/")
    caminho = referencia.split("?", 1)[0].split("#", 1)[0].strip()
    if "://" in caminho or caminho.startswith("//"):
        try:
            caminho = str(
                urlsplit(caminho if not caminho.startswith("//") else f"https:{caminho}").path
                or ""
            )
        except ValueError:
            return False
    caminho_fold = caminho.casefold()
    prefixo_tenant = "/api/cadastro/foto/"
    prefixo_arquivo = "/api/cadastro/foto-arquivo/"
    if caminho_fold.startswith(prefixo_tenant):
        partes = caminho[len(prefixo_tenant) :].strip("/").split("/")
        if len(partes) < 2 or (client_id and partes[0] != str(client_id)):
            return False
        sufixo = "/".join(partes[1:])
        caminho = sufixo if sufixo.casefold().startswith("cadastro_fotos/") else f"cadastro_fotos/{sufixo}"
    elif caminho_fold.startswith(prefixo_arquivo):
        sufixo = caminho[len(prefixo_arquivo) :].strip("/")
        caminho = sufixo if sufixo.casefold().startswith("cadastro_fotos/") else f"cadastro_fotos/{sufixo}"
    elif caminho.startswith("/"):
        return False
    caminho = caminho.replace("\\", "/").strip("/")
    if caminho.casefold() == alvo.casefold():
        return True
    marker = "/cadastro_fotos/"
    marker_idx = caminho.casefold().find(marker)
    if marker_idx >= 0:
        local_suffix = caminho[marker_idx + 1 :]
        return local_suffix.casefold() == alvo.casefold()
    return False


@contextmanager
def _shared_sync_guard_legacy_photo_locked(
    client_id: str,
    tenant_abs: str,
    target_abs: str,
    rel: str,
):
    """Protect filename fallback and arbitrary scoped references together."""

    from backend.services.cadastro_compatibilidade import (
        bloquear_mutacao_legada_sem_sku_controlado,
        exigir_mutacao_legada_sem_sku_controlado,
    )
    from backend.services.cadastro_lojas_produtos import _ler_registros_persistidos
    from backend.services.cadastro_fotos import (
        _cadastro_foto_coluna_candidata,
        _cadastro_fotos_bloquear_mutacao_global,
    )

    stem = _shared_sync_legacy_photo_sku(rel)
    with (
        bloquear_mutacao_legada_sem_sku_controlado(client_id, [stem]),
        _cadastro_fotos_bloquear_mutacao_global(client_id, tenant_abs),
    ):
        registros, _colunas = _ler_registros_persistidos(client_id)
        skus_referenciados = [
            str(item.get("sku_normalizado") or item.get("sku") or "").strip()
            for item in registros
            if any(
                _shared_sync_photo_referencia_local_equivale(valor, rel, client_id)
                for campo, valor in item.items()
                if _cadastro_foto_coluna_candidata(campo)
            )
            and str(item.get("sku_normalizado") or item.get("sku") or "").strip()
        ]
        exigir_mutacao_legada_sem_sku_controlado(client_id, skus_referenciados)
        with _shared_sync_path_lock_for(target_abs):
            yield


def _shared_sync_aplicar_foto_legada_atomica(
    client_id: str,
    tenant_abs: str,
    backup_dir: str,
    rel: str,
    data: bytes,
    *,
    somente_se_ausente: bool = False,
) -> dict[str, Any]:
    """Publica uma foto global como identidade de SKU, incluindo variantes."""

    from backend.services.cadastro_fotos import (
        _cadastro_caminhos_variantes_fotos_preparadas,
        _salvar_fotos_preparadas_atomico,
        _validar_caminhos_variantes_fotos,
    )
    from backend.services.path_coordination import path_locks_for

    target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
    preparada = {
        "caminho": target_abs,
        "relativo": rel,
        "conteudo": bytes(data or b""),
        "store_id": "",
        "sku": _shared_sync_legacy_photo_sku(rel),
    }
    variantes = _cadastro_caminhos_variantes_fotos_preparadas([preparada])
    with path_locks_for(variantes):
        _validar_caminhos_variantes_fotos(variantes)
        existentes = [caminho for caminho in variantes if os.path.isfile(caminho)]
        if somente_se_ausente and existentes:
            return {"added": 0, "skipped_existing": True}
        for caminho in existentes:
            variante_rel = os.path.relpath(caminho, tenant_abs).replace("\\", "/")
            _shared_sync_backup_target(
                tenant_abs,
                backup_dir,
                variante_rel,
                caminho,
            )
        _salvar_fotos_preparadas_atomico([preparada])
    return {
        "added": 1,
        "merged": not somente_se_ausente,
        "skipped_existing": False,
    }


def _shared_sync_tombstone_key(item: dict) -> str:
    tipo = str((item or {}).get("type") or "").strip().casefold()
    store_id = str((item or {}).get("store_id") or "").strip()
    service = str((item or {}).get("service") or "").strip().casefold()
    if not tipo or not store_id:
        return ""
    return f"{tipo}:{store_id}:{service}"


def _shared_sync_tombstone_priority(item: dict) -> tuple[Any, ...]:
    try:
        version = max(0, int((item or {}).get("version") or 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Tombstone remoto contem versao invalida.") from exc
    deleted_at = str((item or {}).get("deleted_at") or "").strip()
    digest = hashlib.sha256(
        json.dumps(item or {}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return version, deleted_at, digest


def _shared_sync_parse_tombstones(data: bytes, label: str) -> list[dict]:
    try:
        payload = json.loads((data or b"[]").decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{label} invalido.") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail=f"{label} nao contem uma lista.")
    saida: list[dict] = []
    for item in payload:
        if not isinstance(item, dict) or not _shared_sync_tombstone_key(item):
            raise HTTPException(status_code=502, detail=f"{label} contem tombstone sem identidade.")
        key_persistida = str(item.get("key") or "").strip()
        key_canonica = _shared_sync_tombstone_key(item)
        if key_persistida and key_persistida != key_canonica:
            raise HTTPException(
                status_code=502,
                detail=f"{label} contem tombstone com key inconsistente.",
            )
        item = _shared_sync_json_clone(item)
        item["key"] = key_canonica
        saida.append(_shared_sync_json_clone(item))
    return saida


def _shared_sync_merge_tombstones_integracoes_payload(
    target_abs: str,
    remoto_bytes: bytes,
) -> list[dict]:
    atuais: list[dict] = []
    if os.path.exists(target_abs):
        with open(target_abs, "rb") as file:
            atuais = _shared_sync_parse_tombstones(file.read(), "lojas_sync_tombstones.json atual")
    remotos = _shared_sync_parse_tombstones(remoto_bytes, "lojas_sync_tombstones.json remoto")
    por_chave: dict[str, dict] = {}
    ordem: list[str] = []
    for item in [*atuais, *remotos]:
        key = _shared_sync_tombstone_key(item)
        atual = por_chave.get(key)
        if atual is None:
            ordem.append(key)
            por_chave[key] = item
        elif _shared_sync_tombstone_priority(item) > _shared_sync_tombstone_priority(atual):
            por_chave[key] = item
    return [por_chave[key] for key in ordem]


def _shared_sync_store_ids_tombstonados(tombstones: list[dict]) -> set[str]:
    return {
        str((item or {}).get("store_id") or "").strip()
        for item in tombstones
        if str((item or {}).get("store_id") or "").strip()
        and not str((item or {}).get("restored_at") or "").strip()
        and (
            str((item or {}).get("type") or "").strip().casefold() == "store"
            or _shared_sync_tombstone_key(item).casefold().startswith("store:")
        )
    }


def _shared_sync_aplicar_tombstones_integracao(
    lojas: list[dict],
    tombstones: list[dict],
) -> list[dict]:
    por_id = {
        str((loja or {}).get("store_id") or "").strip(): loja
        for loja in lojas
        if isinstance(loja, dict) and str((loja or {}).get("store_id") or "").strip()
    }
    for tombstone in tombstones:
        if str((tombstone or {}).get("type") or "").strip().casefold() != "integration":
            continue
        if str((tombstone or {}).get("restored_at") or "").strip():
            continue
        store_id = str((tombstone or {}).get("store_id") or "").strip()
        service = _shared_sync_servico_key((tombstone or {}).get("service"))
        loja = por_id.get(store_id)
        if not loja or not service:
            continue
        tombstone_version = _shared_sync_tombstone_priority(tombstone)[0]
        integracoes_loja = loja.get("integracoes")
        if not isinstance(integracoes_loja, dict):
            integracoes_loja = {}
            loja["integracoes"] = integracoes_loja
        integracao_atual = (
            integracoes_loja.get(service)
            if isinstance(integracoes_loja.get(service), dict)
            else {}
        )
        integracao_version = _shared_sync_sync_version(
            (integracao_atual or {}).get("_sync_version")
        )
        integracao_updated = str(
            (integracao_atual or {}).get("_sync_updated_at")
            or (integracao_atual or {}).get("updated_at")
            or ""
        ).strip()
        tombstone_updated = str((tombstone or {}).get("deleted_at") or "").strip()
        if (tombstone_version, tombstone_updated) <= (
            integracao_version,
            integracao_updated,
        ):
            continue
        integracoes_loja[service] = {
            "connected": False,
            "_sync_version": tombstone_version,
            "_sync_updated_at": tombstone_updated,
        }
    return lojas


def _shared_sync_aplicar_lojas_integracoes_atomico(
    client_id: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
    *,
    add_only: bool,
) -> dict:
    from backend.services import integracoes

    config_source = next((data for rel, data in fontes if rel.casefold() == "lojas_config.json"), None)
    tombstone_source = next(
        (data for rel, data in fontes if rel.casefold() == "lojas_sync_tombstones.json"),
        None,
    )
    config_target = os.path.join(tenant_abs, "lojas_config.json")
    tombstone_target = os.path.join(tenant_abs, "lojas_sync_tombstones.json")

    with (
        integracoes._LOJAS_CONFIG_LOCK,
        integracoes._integracoes_bloquear_catalogo_e_transicao_fotos(
            client_id,
            tenant_abs,
        ),
    ):
        remote_tombstones = (
            _shared_sync_parse_tombstones(
                tombstone_source,
                "lojas_sync_tombstones.json remoto",
            )
            if tombstone_source is not None
            else []
        )
        tombstone_bytes = tombstone_source if tombstone_source is not None else b"[]"
        tombstones = _shared_sync_merge_tombstones_integracoes_payload(
            tombstone_target,
            tombstone_bytes,
        )
        tombstonados = _shared_sync_store_ids_tombstonados(tombstones)

        lojas: Optional[list[dict]] = None
        if config_source is not None:
            if add_only:
                merged_bytes = _shared_sync_merge_lojas_integracoes_bytes(
                    config_target,
                    config_source,
                    add_only=True,
                )
                lojas = _shared_sync_lojas_from_payload(
                    _shared_sync_json_from_bytes(merged_bytes, "lojas_config.json mesclado")
                )
            else:
                remoto_payload = _shared_sync_json_from_bytes(
                    config_source,
                    "lojas_config.json remoto",
                )
                if not isinstance(remoto_payload, list):
                    raise HTTPException(
                        status_code=502,
                        detail="lojas_config.json remoto nao contem uma lista.",
                    )
                lojas = _shared_sync_lojas_from_payload(remoto_payload)
                atuais: list[dict] = []
                if os.path.exists(config_target):
                    with open(config_target, "rb") as file:
                        atual_payload = _shared_sync_json_from_bytes(
                            file.read(),
                            "lojas_config.json atual",
                        )
                    if not isinstance(atual_payload, list):
                        raise HTTPException(
                            status_code=502,
                            detail="lojas_config.json atual nao contem uma lista.",
                        )
                    atuais = _shared_sync_lojas_from_payload(atual_payload)
                atuais_por_id = {
                    str((loja or {}).get("store_id") or "").strip(): loja
                    for loja in atuais
                    if str((loja or {}).get("store_id") or "").strip()
                }
                lojas_mescladas: list[dict] = []
                for loja_remota in lojas:
                    store_id_remoto = str(
                        (loja_remota or {}).get("store_id") or ""
                    ).strip()
                    loja_atual = atuais_por_id.get(store_id_remoto)
                    if not loja_atual:
                        lojas_mescladas.append(loja_remota)
                        continue
                    lojas_mescladas.append(
                        _shared_sync_merge_loja_integracoes_authoritative(
                            loja_atual,
                            loja_remota,
                        )
                    )
                lojas = lojas_mescladas
                ids_atuais = {
                    str((loja or {}).get("store_id") or "").strip()
                    for loja in atuais
                    if str((loja or {}).get("store_id") or "").strip()
                }
                ids_remotos = {
                    str((loja or {}).get("store_id") or "").strip()
                    for loja in lojas
                    if str((loja or {}).get("store_id") or "").strip()
                }
                sem_tombstone = sorted(
                    (ids_atuais - ids_remotos)
                    - _shared_sync_store_ids_tombstonados(remote_tombstones)
                )
                if sem_tombstone:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "shared_sync_store_removal_requires_tombstone",
                            "message": (
                                "Shared Sync bloqueado: snapshot autoritativo remove loja "
                                "sem tombstone remoto correspondente."
                            ),
                            "store_ids": sem_tombstone,
                        },
                    )
            lojas = [
                loja
                for loja in lojas
                if str((loja or {}).get("store_id") or "").strip() not in tombstonados
            ]
            lojas = _shared_sync_aplicar_tombstones_integracao(lojas, tombstones)
        elif tombstone_source is not None and os.path.exists(config_target):
            with open(config_target, "rb") as file:
                atual_payload = _shared_sync_json_from_bytes(
                    file.read(),
                    "lojas_config.json atual",
                )
            if not isinstance(atual_payload, list):
                raise HTTPException(
                    status_code=502,
                    detail="lojas_config.json atual nao contem uma lista.",
                )
            atuais = _shared_sync_lojas_from_payload(atual_payload)
            filtradas = [
                loja
                for loja in atuais
                if str((loja or {}).get("store_id") or "").strip() not in tombstonados
            ]
            atualizadas = _shared_sync_aplicar_tombstones_integracao(
                _shared_sync_json_clone(filtradas),
                tombstones,
            )
            if atualizadas != atuais:
                lojas = atualizadas

        estados = {
            config_target: integracoes._integracoes_capturar_estado_arquivo(config_target),
            tombstone_target: integracoes._integracoes_capturar_estado_arquivo(tombstone_target),
        }
        escritos: list[str] = []
        try:
            if tombstone_source is not None:
                _shared_sync_backup_target(
                    tenant_abs,
                    backup_dir,
                    "lojas_sync_tombstones.json",
                    tombstone_target,
                )
                integracoes._integracoes_escrever_lojas_config_atomico(
                    tombstone_target,
                    tombstones,
                )
                escritos.append("lojas_sync_tombstones.json")
            if lojas is not None:
                _shared_sync_backup_target(
                    tenant_abs,
                    backup_dir,
                    "lojas_config.json",
                    config_target,
                )
                integracoes.salvar_lojas(
                    client_id,
                    lojas,
                    permitir_reducao_confirmada=not add_only,
                    _preservar_sync_metadata_validada=True,
                )
                escritos.append("lojas_config.json")
        except BaseException:
            integracoes._integracoes_rollback_estados_arquivo(estados)
            raise

    return {
        "files": escritos,
        "lojas": len(lojas) if lojas is not None else None,
    }


def _shared_sync_cadastro_lojas_canonical_df(df: pd.DataFrame) -> pd.DataFrame:
    mapped = _shared_sync_cadastro_lojas_columns(list(df.columns))
    rename = {
        actual: canonical
        for canonical, actual in mapped.items()
        if actual != canonical
    }
    return df.rename(columns=rename).fillna("")


def _shared_sync_cadastro_lojas_row_version(row: dict[str, Any]) -> int:
    raw = str(row.get("row_version") or "").strip()
    if not raw:
        return 0
    if not re.fullmatch(r"\+?\d+(?:\.0+)?", raw):
        raise HTTPException(
            status_code=502,
            detail=f"cadastro_produtos_lojas.csv contem row_version invalido: {raw[:80]}",
        )
    value = int(raw.lstrip("+").split(".", 1)[0])
    return value


def _shared_sync_cadastro_lojas_updated_at(row: dict[str, Any]) -> datetime:
    raw = str(row.get("updated_at_utc") or "").strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.upper().endswith("Z") else raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"cadastro_produtos_lojas.csv contem updated_at_utc invalido: {raw[:80]}",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _shared_sync_cadastro_lojas_is_tombstone(row: dict[str, Any]) -> bool:
    return bool(str(row.get("deleted_at_utc") or "").strip())


def _shared_sync_cadastro_lojas_priority(row: dict[str, Any]) -> tuple[Any, ...]:
    """Build a convergent priority without allowing stale resurrection.

    Version is authoritative.  At the same version a tombstone always wins an
    active row, regardless of either timestamp.  Rows with the same lifecycle
    state use timestamp and then their complete-content digest as deterministic
    tie-breakers.
    """

    return (
        _shared_sync_cadastro_lojas_row_version(row),
        _shared_sync_cadastro_lojas_is_tombstone(row),
        _shared_sync_cadastro_lojas_updated_at(row),
        _shared_sync_cadastro_lojas_content_digest(row, list(row)),
    )


def _shared_sync_cadastro_lojas_is_conflict(
    current: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    """Identify different active payloads tied on version and timestamp."""

    return (
        current != candidate
        and not _shared_sync_cadastro_lojas_is_tombstone(current)
        and not _shared_sync_cadastro_lojas_is_tombstone(candidate)
        and _shared_sync_cadastro_lojas_row_version(current)
        == _shared_sync_cadastro_lojas_row_version(candidate)
        and _shared_sync_cadastro_lojas_updated_at(current)
        == _shared_sync_cadastro_lojas_updated_at(candidate)
    )


def _shared_sync_cadastro_lojas_should_replace(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    return _shared_sync_cadastro_lojas_priority(candidate) > _shared_sync_cadastro_lojas_priority(current)


def _shared_sync_cadastro_lojas_photo_reference(
    row: dict[str, Any],
    client_id: str = "",
) -> str:
    """Return the normalized key for one store-scoped photo reference.

    Store photo paths are part of the versioned product payload.  A reference
    below another store would otherwise let a valid product row authorize a
    write across store boundaries, so scoped references are validated here
    before any merge or photo write takes place.  Legacy references outside
    ``cadastro_fotos/lojas`` intentionally remain outside this mechanism.
    """

    from backend.services.cadastro_fotos import (
        _cadastro_foto_cabecalho_normalizar,
        _cadastro_foto_coluna_candidata,
        _cadastro_foto_partes_loja_referencia_local,
        _cadastro_foto_referencia_local_cadastro,
        _cadastro_foto_referencias_invalidas_loja,
    )

    candidatas = [
        (column, str(value or "").strip().replace("\\", "/"))
        for column, value in row.items()
        if _cadastro_foto_coluna_candidata(column)
        and str(value or "").strip()
    ]
    if not candidatas:
        return ""

    store_id = str(row.get("store_id") or "").strip()
    if not store_id:
        raise HTTPException(
            status_code=502,
            detail="cadastro_produtos_lojas.csv contem foto associada a outra loja.",
        )
    if _cadastro_foto_referencias_invalidas_loja(client_id, store_id, row):
        raise HTTPException(
            status_code=502,
            detail="cadastro_produtos_lojas.csv contem foto associada a outra loja.",
        )

    raw = next(
        (
            value
            for column, value in candidatas
            if _cadastro_foto_cabecalho_normalizar(column) == "foto"
        ),
        "",
    )
    if not raw or not _cadastro_foto_referencia_local_cadastro(raw):
        return ""

    parts = _cadastro_foto_partes_loja_referencia_local(raw, client_id)
    if parts is None:
        # Global legacy paths and true external URLs are not coupled to store
        # photo bytes by the versioned merge.
        return ""
    if (
        len(parts) != 3
        or any(not part or part in {".", ".."} for part in parts)
    ):
        raise HTTPException(
            status_code=502,
            detail="cadastro_produtos_lojas.csv contem foto associada a outra loja.",
        )
    return "cadastro_fotos/" + "/".join(parts)


def _shared_sync_cadastro_lojas_photo_vote(current: str, candidate: str) -> str:
    if not current:
        return candidate
    if current == candidate:
        return current
    # One physical file referenced by different logical rows is ambiguous.
    # Skipping it is the only choice that cannot overwrite a winning local
    # version with bytes belonging to another row.
    return "local"


def _shared_sync_write_csv_atomic(target_abs: str, df: pd.DataFrame) -> None:
    target_dir = os.path.dirname(os.path.abspath(target_abs))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target_abs)}.sharedsync_",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as file:
            df.to_csv(file, index=False, lineterminator="\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, target_abs)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _shared_sync_merge_cadastro_lojas_versioned(
    target_abs: str,
    remoto_bytes: bytes,
    client_id: str = "",
    *,
    validar_store_ids: bool = False,
) -> dict:
    # Import lazily to avoid coupling Shared Sync module initialization to the
    # Cadastro facade.  This is the same per-path RLock used by every CRUD
    # read/write cycle in cadastro_lojas_produtos.py.
    from backend.services import integracoes

    if validar_store_ids:
        tenant_abs = os.path.dirname(os.path.abspath(target_abs))
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            with _shared_sync_path_lock_for(target_abs):
                remoto_df = _shared_sync_cadastro_lojas_canonical_df(
                    _shared_sync_csv_read_bytes(remoto_bytes)
                )
                _shared_sync_validar_store_ids_df_remoto_locked(
                    os.path.dirname(os.path.abspath(target_abs)),
                    remoto_df,
                    _SHARED_SYNC_CADASTRO_LOJAS_REL,
                    exigir_store_id=True,
                )
                return _shared_sync_merge_cadastro_lojas_versioned_locked(
                    target_abs,
                    remoto_bytes,
                    client_id,
                )

    with integracoes._LOJAS_CONFIG_LOCK:
        with _shared_sync_path_lock_for(target_abs):
            return _shared_sync_merge_cadastro_lojas_versioned_locked(
                target_abs,
                remoto_bytes,
                client_id,
            )


def _shared_sync_merge_cadastro_lojas_versioned_locked(
    target_abs: str,
    remoto_bytes: bytes,
    client_id: str = "",
) -> dict:
    remoto_df = _shared_sync_cadastro_lojas_canonical_df(_shared_sync_csv_read_bytes(remoto_bytes))
    target_exists = os.path.exists(target_abs)
    if target_exists:
        with open(target_abs, "rb") as file:
            atual_df = _shared_sync_cadastro_lojas_canonical_df(_shared_sync_csv_read_bytes(file.read()))
    else:
        atual_df = pd.DataFrame(columns=list(remoto_df.columns))

    original_columns = list(atual_df.columns)
    columns: list[str] = []
    for col in list(atual_df.columns) + list(remoto_df.columns):
        if col not in columns:
            columns.append(col)
    for required in _SHARED_SYNC_CADASTRO_LOJAS_COLUMNS:
        if required not in columns:
            columns.append(required)
    for col in columns:
        if col not in atual_df.columns:
            atual_df[col] = ""
        if col not in remoto_df.columns:
            remoto_df[col] = ""
    atual_df = atual_df[columns].fillna("")
    remoto_df = remoto_df[columns].fillna("")

    rows_by_key: dict[str, dict[str, Any]] = {}
    winner_source: dict[str, str] = {}
    remote_rows_by_key: dict[str, list[dict[str, Any]]] = {}
    ordered_keys: list[str] = []
    local_duplicates = 0
    conflicts = 0
    for _idx, series in atual_df.iterrows():
        row = {col: str(series.get(col) or "") for col in columns}
        _shared_sync_cadastro_lojas_row_version(row)
        _shared_sync_cadastro_lojas_updated_at(row)
        key = _shared_sync_cadastro_lojas_row_key(
            "cadastro", _SHARED_SYNC_CADASTRO_LOJAS_REL, row, columns,
        )
        _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        current = rows_by_key.get(key)
        if current is None:
            rows_by_key[key] = row
            winner_source[key] = "local"
            ordered_keys.append(key)
        else:
            local_duplicates += 1
            if _shared_sync_cadastro_lojas_is_conflict(current, row):
                conflicts += 1
            if _shared_sync_cadastro_lojas_should_replace(current, row):
                rows_by_key[key] = row

    added = 0
    updated = 0
    skipped_older = 0
    for _idx, series in remoto_df.iterrows():
        row = {col: str(series.get(col) or "") for col in columns}
        _shared_sync_cadastro_lojas_row_version(row)
        _shared_sync_cadastro_lojas_updated_at(row)
        key = _shared_sync_cadastro_lojas_row_key(
            "cadastro", _SHARED_SYNC_CADASTRO_LOJAS_REL, row, columns,
        )
        _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        remote_rows_by_key.setdefault(key, []).append(row)
        current = rows_by_key.get(key)
        if current is None:
            rows_by_key[key] = row
            winner_source[key] = "remote"
            ordered_keys.append(key)
            added += 1
            continue
        if current == row:
            if winner_source.get(key) == "local":
                winner_source[key] = "tie"
            continue
        if _shared_sync_cadastro_lojas_is_conflict(current, row):
            conflicts += 1
        if _shared_sync_cadastro_lojas_should_replace(current, row):
            rows_by_key[key] = row
            winner_source[key] = "remote"
            updated += 1
        elif current != row:
            skipped_older += 1

    rows = [rows_by_key[key] for key in ordered_keys]
    merged = pd.DataFrame(rows, columns=columns).fillna("")
    columns_changed = original_columns != columns
    changed = bool(added or updated or local_duplicates or columns_changed or not target_exists)
    if changed and (rows or not target_exists):
        _shared_sync_write_csv_atomic(target_abs, merged)

    photo_decisions: dict[str, str] = {}
    for key, remote_rows in remote_rows_by_key.items():
        winner = rows_by_key[key]
        winner_photo = _shared_sync_cadastro_lojas_photo_reference(winner, client_id)
        source = winner_source.get(key, "local")
        for remote_row in remote_rows:
            remote_photo = _shared_sync_cadastro_lojas_photo_reference(remote_row, client_id)
            if not remote_photo:
                continue
            if (
                remote_photo != winner_photo
                or _shared_sync_cadastro_lojas_is_tombstone(winner)
            ):
                decision = "local"
            elif source == "remote":
                decision = "remote"
            elif source == "tie":
                decision = "tie"
            else:
                decision = "local"
            photo_decisions[remote_photo] = _shared_sync_cadastro_lojas_photo_vote(
                photo_decisions.get(remote_photo, ""),
                decision,
            )

    deleted = sum(_shared_sync_cadastro_lojas_is_tombstone(row) for row in rows)
    return {
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "skipped_older": skipped_older,
        "conflicts": conflicts,
        "deduplicated": local_duplicates,
        "total": len(rows),
        "merged": changed,
        "photo_decisions": photo_decisions,
    }


def _shared_sync_cadastro_custos_canonical_df(df: pd.DataFrame) -> pd.DataFrame:
    mapped = _shared_sync_cadastro_custos_columns(list(df.columns))
    rename = {
        actual: canonical
        for canonical, actual in mapped.items()
        if actual != canonical
    }
    canonical = df.rename(columns=rename).fillna("")
    for column in ("store_id", "loja_sync", "sku", "updated_at"):
        if column not in canonical.columns:
            canonical[column] = ""
    return canonical


def _shared_sync_cadastro_custos_updated_at(row: dict[str, Any]) -> Optional[datetime]:
    raw = str(row.get("updated_at") or "").strip()
    if not raw:
        return None

    iso_value = raw[:-1] + "+00:00" if raw.upper().endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in (
            "%d/%m/%Y %H:%M:%S.%f",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _shared_sync_cadastro_custos_priority(row: dict[str, Any]) -> tuple[Any, ...]:
    updated_at = _shared_sync_cadastro_custos_updated_at(row)
    return (
        updated_at is not None,
        updated_at or datetime.min.replace(tzinfo=timezone.utc),
        _shared_sync_cadastro_custos_content_digest(row, list(row)),
    )


def _shared_sync_cadastro_custos_is_conflict(
    current: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    return (
        current != candidate
        and _shared_sync_cadastro_custos_updated_at(current)
        == _shared_sync_cadastro_custos_updated_at(candidate)
    )


def _shared_sync_merge_cadastro_custos_versioned(
    client_id: str,
    target_abs: str,
    remoto_bytes: bytes,
    *,
    validar_store_ids: bool = False,
) -> dict:
    # The canonical cost service owns this lock.  Holding it for the complete
    # read/merge/replace cycle prevents an import or inline cost edit from being
    # overwritten by a concurrent Shared Sync pull.
    from backend.services import integracoes
    from backend.services.cadastro_custos import _cadastro_custos_lock

    if validar_store_ids:
        tenant_abs = os.path.dirname(os.path.abspath(target_abs))
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            with _shared_sync_path_lock_for(target_abs):
                remote_df = _shared_sync_cadastro_custos_canonical_df(
                    _shared_sync_csv_read_bytes(remoto_bytes)
                )
                _shared_sync_validar_store_ids_df_remoto_locked(
                    os.path.dirname(os.path.abspath(target_abs)),
                    remote_df,
                    _SHARED_SYNC_CADASTRO_CUSTOS_REL,
                    exigir_store_id=True,
                )
                return _shared_sync_merge_cadastro_custos_versioned_locked(
                    target_abs,
                    remoto_bytes,
                )

    with integracoes._LOJAS_CONFIG_LOCK:
        with _cadastro_custos_lock(client_id):
            return _shared_sync_merge_cadastro_custos_versioned_locked(target_abs, remoto_bytes)


def _shared_sync_merge_cadastro_custos_versioned_locked(target_abs: str, remoto_bytes: bytes) -> dict:
    remote_df = _shared_sync_cadastro_custos_canonical_df(_shared_sync_csv_read_bytes(remoto_bytes))
    target_exists = os.path.exists(target_abs)
    if target_exists:
        with open(target_abs, "rb") as file:
            current_df = _shared_sync_cadastro_custos_canonical_df(_shared_sync_csv_read_bytes(file.read()))
    else:
        current_df = pd.DataFrame(columns=list(remote_df.columns))

    original_columns = list(current_df.columns)
    columns: list[str] = []
    for column in list(current_df.columns) + list(remote_df.columns):
        if column not in columns:
            columns.append(column)
    for required in ("store_id", "loja_sync", "sku", "updated_at"):
        if required not in columns:
            columns.append(required)
    for column in columns:
        if column not in current_df.columns:
            current_df[column] = ""
        if column not in remote_df.columns:
            remote_df[column] = ""
    current_df = current_df[columns].fillna("")
    remote_df = remote_df[columns].fillna("")

    rows_by_key: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    local_duplicates = 0
    conflicts = 0
    for _idx, series in current_df.iterrows():
        row = {column: str(series.get(column) or "") for column in columns}
        key = _shared_sync_cadastro_custos_row_key(
            "cadastro", _SHARED_SYNC_CADASTRO_CUSTOS_REL, row, columns,
        )
        current = rows_by_key.get(key)
        if current is None:
            rows_by_key[key] = row
            ordered_keys.append(key)
            continue
        local_duplicates += 1
        if _shared_sync_cadastro_custos_is_conflict(current, row):
            conflicts += 1
        if _shared_sync_cadastro_custos_priority(row) > _shared_sync_cadastro_custos_priority(current):
            rows_by_key[key] = row

    added = 0
    updated = 0
    skipped_older = 0
    for _idx, series in remote_df.iterrows():
        row = {column: str(series.get(column) or "") for column in columns}
        key = _shared_sync_cadastro_custos_row_key(
            "cadastro", _SHARED_SYNC_CADASTRO_CUSTOS_REL, row, columns,
        )
        current = rows_by_key.get(key)
        if current is None:
            rows_by_key[key] = row
            ordered_keys.append(key)
            added += 1
            continue
        if current == row:
            continue
        if _shared_sync_cadastro_custos_is_conflict(current, row):
            conflicts += 1
        if _shared_sync_cadastro_custos_priority(row) > _shared_sync_cadastro_custos_priority(current):
            rows_by_key[key] = row
            updated += 1
        else:
            skipped_older += 1

    rows = [rows_by_key[key] for key in ordered_keys]
    merged = pd.DataFrame(rows, columns=columns).fillna("")
    columns_changed = original_columns != columns
    changed = bool(added or updated or local_duplicates or columns_changed or not target_exists)
    if changed and (rows or not target_exists):
        _shared_sync_write_csv_atomic(target_abs, merged)

    return {
        "added": added,
        "updated": updated,
        "skipped_older": skipped_older,
        "conflicts": conflicts,
        "deduplicated": local_duplicates,
        "total": len(rows),
        "merged": changed,
    }

def _shared_sync_sql_ident(nome: str) -> str:
    return '"' + str(nome or "").replace('"', '""') + '"'

def _shared_sync_sqlite_temp_from_bytes(data: bytes, prefix: str = "shared_sync_") -> str:
    fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=".db")
    os.close(fd)
    with open(tmp_path, "wb") as f:
        f.write(data or b"")
    return tmp_path

def _shared_sync_sqlite_is_locked(exc: Exception) -> bool:
    texto = str(exc or "").lower()
    return "database is locked" in texto or "database table is locked" in texto or "database is busy" in texto

def _shared_sync_sqlite_retry_locked(operation: Callable[[], Any], label: str) -> Any:
    delay = 0.35
    last_exc: Optional[Exception] = None
    for tentativa in range(1, SHARED_SYNC_SQLITE_LOCK_RETRIES + 1):
        try:
            return operation()
        except sqlite3.OperationalError as exc:
            if not _shared_sync_sqlite_is_locked(exc):
                raise
            last_exc = exc
            globals().get("logger", _LOG).warning(
                "[SHARED-SYNC] Banco SQLite ocupado em %s; tentativa %s/%s.",
                label,
                tentativa,
                SHARED_SYNC_SQLITE_LOCK_RETRIES,
            )
            time.sleep(delay)
            delay = min(delay * 1.8, 4.0)
    raise HTTPException(
        status_code=423,
        detail=f"Banco de vendas em uso no momento ({label}). Tente sincronizar novamente em instantes.",
    ) from last_exc

SHARED_SYNC_VENDAS_TABLES = ("vendas", "notas_entrada", "notas_entrada_itens")


def _shared_sync_sqlite_backup_to_path(source_abs: str, destination_abs: str, label: str) -> None:
    os.makedirs(os.path.dirname(destination_abs), exist_ok=True)
    src = sqlite3.connect(
        f"file:{os.path.abspath(source_abs)}?mode=ro",
        uri=True,
        timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000),
    )
    dst = sqlite3.connect(destination_abs, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
    try:
        _shared_sync_sqlite_configure(src)
        _shared_sync_sqlite_configure(dst)
        src.backup(dst)
        _shared_sync_sqlite_quick_check(dst, label)
    finally:
        dst.close()
        src.close()


def _shared_sync_vendas_merge_table(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
) -> dict:
    table_ident = _shared_sync_sql_ident(table)
    src_cur = src.cursor()
    dst_cur = dst.cursor()
    exists_src = src_cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists_src:
        return {"added": 0, "total": 0, "present": False, "schema_changed": False}

    src_cols_info = src_cur.execute(f"PRAGMA table_info({table_ident})").fetchall()
    src_cols = [str(row[1]) for row in src_cols_info]
    if "id_unico" not in src_cols:
        raise HTTPException(status_code=502, detail=f"Tabela {table} sem id_unico no banco recebido.")

    schema_changed = False
    exists_dst = dst_cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists_dst:
        create_sql = str(exists_src[0] or "").strip()
        if not create_sql:
            raise HTTPException(status_code=502, detail=f"Schema da tabela {table} ausente no banco recebido.")
        dst_cur.execute(create_sql)
        schema_changed = True

    dst_cols_info = dst_cur.execute(f"PRAGMA table_info({table_ident})").fetchall()
    dst_cols = [str(row[1]) for row in dst_cols_info]
    if "id_unico" not in dst_cols:
        raise HTTPException(status_code=502, detail=f"Tabela local {table} sem id_unico.")

    for col in src_cols_info:
        name = str(col[1])
        if name in dst_cols:
            continue
        col_type = str(col[2] or "TEXT")
        dst_cur.execute(f"ALTER TABLE {table_ident} ADD COLUMN {_shared_sync_sql_ident(name)} {col_type}")
        dst_cols.append(name)
        schema_changed = True

    common_cols = [col for col in src_cols if col in dst_cols]
    id_idx = common_cols.index("id_unico")
    existing_ids = {
        str(row[0] or "").strip()
        for row in dst_cur.execute(f"SELECT id_unico FROM {table_ident}").fetchall()
        if str(row[0] or "").strip()
    }
    select_sql = f"SELECT {', '.join(_shared_sync_sql_ident(col) for col in common_cols)} FROM {table_ident}"
    insert_sql = (
        f"INSERT OR IGNORE INTO {table_ident} "
        f"({', '.join(_shared_sync_sql_ident(col) for col in common_cols)}) "
        f"VALUES ({', '.join(['?'] * len(common_cols))})"
    )
    inserted = 0
    for row in src_cur.execute(select_sql):
        row_id = str(row[id_idx] or "").strip()
        if not row_id:
            raise HTTPException(status_code=502, detail=f"Tabela {table} contem id_unico vazio.")
        if row_id in existing_ids:
            continue
        dst_cur.execute(insert_sql, tuple(row))
        if dst_cur.rowcount > 0:
            inserted += 1
            existing_ids.add(row_id)

    total = int(dst_cur.execute(f"SELECT COUNT(*) FROM {table_ident}").fetchone()[0] or 0)
    return {
        "added": inserted,
        "total": total,
        "present": True,
        "schema_changed": schema_changed,
    }


def _shared_sync_stage_vendas_db_add_only(target_abs: str, remoto_bytes: bytes, rel: str) -> dict:
    if not (remoto_bytes or b"").startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}")
    remoto_tmp = _shared_sync_sqlite_temp_from_bytes(remoto_bytes, "shared_sync_vendas_remote_")
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    fd, stage_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(target_abs)}.sharedsync_",
        suffix=".tmp",
        dir=os.path.dirname(target_abs),
    )
    os.close(fd)
    try:
        if os.path.exists(target_abs):
            _shared_sync_sqlite_backup_to_path(target_abs, stage_path, os.path.basename(target_abs))

        src = sqlite3.connect(remoto_tmp, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        dst = sqlite3.connect(stage_path, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        try:
            _shared_sync_sqlite_configure(src)
            _shared_sync_sqlite_configure(dst)
            _shared_sync_sqlite_quick_check(src, rel)
            dst.execute("BEGIN IMMEDIATE")
            try:
                tables = {
                    table: _shared_sync_vendas_merge_table(src, dst, table)
                    for table in SHARED_SYNC_VENDAS_TABLES
                }
                dst.commit()
            except BaseException:
                dst.rollback()
                raise
            _shared_sync_sqlite_quick_check(dst, rel)
        finally:
            dst.close()
            src.close()

        added = sum(int(info.get("added") or 0) for info in tables.values())
        changed = (
            not os.path.exists(target_abs)
            or added > 0
            or any(bool(info.get("schema_changed")) for info in tables.values())
        )
        return {
            "target": target_abs,
            "stage": stage_path,
            "rel": rel,
            "added": added,
            "changed": changed,
            "tables": tables,
        }
    except BaseException:
        try:
            os.remove(stage_path)
        except OSError:
            pass
        raise
    finally:
        try:
            os.remove(remoto_tmp)
        except OSError:
            pass


def _shared_sync_backup_staged_original(stage: dict, backup_dir: str) -> None:
    target_abs = str(stage["target"])
    if not os.path.exists(target_abs) or not stage.get("changed"):
        return
    backup_abs = os.path.abspath(os.path.join(backup_dir, str(stage["rel"])))
    backup_root = os.path.abspath(backup_dir)
    if not backup_abs.startswith(backup_root + os.sep):
        raise HTTPException(status_code=400, detail="Backup local contem caminho invalido.")
    os.makedirs(os.path.dirname(backup_abs), exist_ok=True)
    _shared_sync_sqlite_backup_to_path(target_abs, backup_abs, os.path.basename(target_abs))
    stage["backup"] = backup_abs


def _shared_sync_prepare_target_for_replace(target_abs: str, label: str) -> None:
    """Consolida WAL e remove sidecars antes de substituir o arquivo principal.

    Um ``os.replace`` apenas do ``.db`` com um WAL antigo ao lado pode fazer o
    SQLite reaplicar paginas do banco anterior sobre o arquivo novo. O lock por
    caminho impede novas conexoes coordenadas enquanto o checkpoint e a troca
    acontecem; se outro processo ainda estiver usando o banco, a operacao falha
    com 423 antes de qualquer arquivo principal ser promovido.
    """

    if not os.path.exists(target_abs):
        return
    conn = sqlite3.connect(
        target_abs,
        timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000),
    )
    try:
        _shared_sync_sqlite_configure(conn)
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0] or 0) != 0:
            raise sqlite3.OperationalError("database is locked during WAL checkpoint")
        _shared_sync_sqlite_quick_check(conn, label)
    finally:
        conn.close()

    for suffix in ("-shm", "-wal"):
        sidecar = f"{target_abs}{suffix}"
        if os.path.exists(sidecar):
            os.remove(sidecar)


def _shared_sync_rollback_promotions(promotions: list[dict]) -> list[str]:
    failures: list[str] = []
    for item in reversed(promotions):
        target = item["target"]
        rollback = item.get("rollback") or ""
        backup = item.get("backup") or ""
        try:
            if item.get("promoted") and os.path.exists(target):
                os.remove(target)
            restored = not item.get("moved")
            if item.get("moved") and rollback and os.path.exists(rollback):
                try:
                    os.replace(rollback, target)
                    restored = True
                except OSError:
                    restored = False
            if not restored and backup and os.path.exists(backup):
                _shared_sync_sqlite_backup_to_path(backup, target, os.path.basename(target))
                restored = True
            if item.get("moved") and not os.path.exists(target):
                raise OSError("arquivo original nao foi restaurado")
        except Exception as exc:
            failures.append(f"{os.path.basename(target)}: {exc}")
    return failures


def _shared_sync_apply_vendas_dbs_add_only(
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> dict:
    prepared_sources = []
    for rel, data in fontes:
        safe_rel = _shared_sync_relativo_seguro(rel)
        if not safe_rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
            continue
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, safe_rel)
        prepared_sources.append((safe_rel, data, target_abs))
    if not prepared_sources:
        return {"file_count": 0, "files": [], "added": 0, "details": []}

    targets = [item[2] for item in prepared_sources]
    stages: list[dict] = []
    promotions: list[dict] = []
    try:
        with _shared_sync_sqlite_locks_for_paths(targets):
            for rel, data, target_abs in prepared_sources:
                stage = _shared_sync_sqlite_retry_locked(
                    lambda target_abs=target_abs, data=data, rel=rel: _shared_sync_stage_vendas_db_add_only(
                        target_abs, data, rel,
                    ),
                    rel,
                )
                stages.append(stage)

            for stage in stages:
                _shared_sync_backup_staged_original(stage, backup_dir)

            # Prepare todos os destinos antes da primeira promocao. Assim, um
            # banco ocupado nunca deixa apenas parte do conjunto substituida.
            for stage in stages:
                if not stage.get("changed"):
                    continue
                _shared_sync_sqlite_retry_locked(
                    lambda stage=stage: _shared_sync_prepare_target_for_replace(
                        str(stage["target"]), str(stage["rel"]),
                    ),
                    str(stage["rel"]),
                )

            for stage in stages:
                if not stage.get("changed"):
                    continue
                target = str(stage["target"])
                rollback = f"{target}.sharedsync_{uuid.uuid4().hex}.rollback"
                record = {
                    "target": target,
                    "rollback": rollback,
                    "backup": str(stage.get("backup") or ""),
                    "moved": False,
                    "promoted": False,
                }
                promotions.append(record)
                if os.path.exists(target):
                    os.replace(target, rollback)
                    record["moved"] = True
                os.replace(str(stage["stage"]), target)
                record["promoted"] = True

            for item in promotions:
                rollback = item.get("rollback") or ""
                if rollback and os.path.exists(rollback):
                    try:
                        os.remove(rollback)
                    except OSError:
                        # O arquivo e apenas a copia transitoria; o backup
                        # recuperavel em _shared_sync_backups ja foi criado.
                        pass
    except BaseException as exc:
        with _shared_sync_sqlite_locks_for_paths(targets):
            rollback_failures = _shared_sync_rollback_promotions(promotions)
        if rollback_failures:
            globals().get("logger", _LOG).critical(
                "[SHARED-SYNC] Falha ao reverter promocao de bancos: %s",
                "; ".join(rollback_failures[:5]),
            )
            raise HTTPException(
                status_code=500,
                detail="Falha critica ao reverter bancos de vendas; backups recuperaveis foram preservados.",
            ) from exc
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {32, 33}:
            raise HTTPException(
                status_code=423,
                detail="Banco de vendas em uso no momento. Tente sincronizar novamente em instantes.",
            ) from exc
        raise
    finally:
        for stage in stages:
            try:
                if os.path.exists(str(stage.get("stage") or "")):
                    os.remove(str(stage["stage"]))
            except OSError:
                pass

    changed = [stage for stage in stages if stage.get("changed")]
    return {
        "file_count": len(changed),
        "files": [str(stage["rel"]) for stage in changed][:250],
        "added": sum(int(stage.get("added") or 0) for stage in stages),
        "details": [
            {
                "file": stage["rel"],
                "added": stage["added"],
                "changed": bool(stage["changed"]),
                "tables": stage["tables"],
            }
            for stage in stages
        ][:250],
    }


def _shared_sync_merge_vendas_db_add_only(target_abs: str, remoto_bytes: bytes, rel: str) -> dict:
    safe_rel = _shared_sync_relativo_seguro(rel or os.path.basename(target_abs))
    tenant_abs = os.path.abspath(target_abs)
    for _part in safe_rel.split("/"):
        tenant_abs = os.path.dirname(tenant_abs)
    backup_dir = os.path.join(tenant_abs, "_shared_sync_backups", f"vendas_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}")
    result = _shared_sync_apply_vendas_dbs_add_only(
        [(safe_rel, remoto_bytes)], tenant_abs, backup_dir,
    )
    details = result.get("details") or []
    info = details[0] if details else {"added": 0, "tables": {}}
    total = sum(int(table.get("total") or 0) for table in (info.get("tables") or {}).values())
    return {"added": int(info.get("added") or 0), "total": total, "tables": info.get("tables") or {}}

def _shared_sync_write_missing_file(target_abs: str, data: bytes) -> dict:
    if os.path.exists(target_abs):
        return {"added": 0, "skipped_existing": True}
    os.makedirs(os.path.dirname(target_abs), exist_ok=True)
    with open(target_abs, "wb") as f:
        f.write(data or b"")
    return {"added": 1, "skipped_existing": False}

def _shared_sync_aplicar_user_share_add_only(
    client_id: str,
    scope: str,
    username: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
) -> dict:
    if not fontes:
        return {"file_count": 0, "files": [], "added": 0}
    if scope == "lojas_integracoes":
        resultado = _shared_sync_aplicar_lojas_integracoes_atomico(
            client_id,
            fontes,
            tenant_abs,
            backup_dir,
            add_only=True,
        )
        arquivos = resultado.get("files") or []
        return {
            "file_count": len(arquivos),
            "files": arquivos,
            "added": len(arquivos),
            "details": [
                {"file": arquivo, "merged": True}
                for arquivo in arquivos
            ],
        }
    if bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped")):
        return _shared_sync_aplicar_user_scoped_share(client_id, scope, username, fontes, tenant_abs, backup_dir)

    escritos = []
    added = 0
    details = []
    vendas_dbs = [
        (rel, data)
        for rel, data in fontes
        if scope == "vendas" and _shared_sync_vendas_history_db(rel)
    ]
    if vendas_dbs:
        merged_dbs = _shared_sync_apply_vendas_dbs_add_only(vendas_dbs, tenant_abs, backup_dir)
        escritos.extend(merged_dbs.get("files") or [])
        added += int(merged_dbs.get("added") or 0)
        details.extend(merged_dbs.get("details") or [])

    from backend.services.shared_sync_apply_scope import (
        _shared_sync_aplicar_cadastro_lojas_fotos_transacional,
        _shared_sync_aplicar_config_fotos_write_once,
        _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL,
        _shared_sync_cadastro_store_photo_key,
    )

    store_photo_fontes = [
        (rel, data)
        for rel, data in fontes
        if _shared_sync_cadastro_store_photo_key(scope, rel)
    ]
    cadastro_lojas_fontes = [
        (rel, data)
        for rel, data in fontes
        if _shared_sync_is_cadastro_lojas_csv(scope, rel)
    ]
    photo_config_fontes = [
        (rel, data)
        for rel, data in fontes
        if scope == "cadastro"
        and str(rel or "").strip().replace("\\", "/").casefold()
        == _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL.casefold()
    ]
    transacionados: set[str] = set()
    if len(photo_config_fontes) > 1 or len(cadastro_lojas_fontes) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_cadastro_snapshot_ambiguous",
                "message": "O compartilhamento contem arquivos canonicos duplicados.",
            },
        )
    if store_photo_fontes and len(cadastro_lojas_fontes) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_photo_row_required",
                "message": (
                    "Shared Sync bloqueado: fotos por loja exigem exatamente "
                    "um cadastro_produtos_lojas.csv no mesmo compartilhamento."
                ),
            },
        )
    photo_config_data = photo_config_fontes[0][1] if photo_config_fontes else None
    if cadastro_lojas_fontes:
        rel_cadastro, data_cadastro = cadastro_lojas_fontes[0]
        target_cadastro = _shared_sync_resolve_tenant_path(
            tenant_abs,
            rel_cadastro,
        )
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            info_cadastro, escritos_cadastro = (
                _shared_sync_aplicar_cadastro_lojas_fotos_transacional(
                    client_id,
                    scope,
                    tenant_abs,
                    backup_dir,
                    rel_cadastro,
                    data_cadastro,
                    target_cadastro,
                    store_photo_fontes,
                    photo_config_data,
                )
            )
        for arquivo in escritos_cadastro:
            if arquivo not in escritos:
                escritos.append(arquivo)
        added += int(info_cadastro.get("added") or 0)
        details.append({"file": rel_cadastro, **info_cadastro})
        escritos_set = set(escritos_cadastro)
        details.extend(
            {
                "file": photo_rel,
                "merged": photo_rel in escritos_set,
            }
            for photo_rel, _photo_data in store_photo_fontes
        )
        transacionados = {
            str(rel_cadastro or "").strip().replace("\\", "/").casefold(),
            *(
                str(photo_rel or "").strip().replace("\\", "/").casefold()
                for photo_rel, _photo_data in store_photo_fontes
            ),
        }
        if photo_config_fontes:
            transacionados.add(_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL.casefold())
    elif photo_config_data is not None:
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            escritos_config = _shared_sync_aplicar_config_fotos_write_once(
                tenant_abs,
                photo_config_data,
            )
        for arquivo in escritos_config:
            if arquivo not in escritos:
                escritos.append(arquivo)
        transacionados.add(_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL.casefold())
        details.append(
            {
                "file": _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL,
                "merged": bool(escritos_config),
            }
        )

    for rel, data in fontes:
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
        lower = rel.lower()
        if scope == "vendas" and _shared_sync_vendas_history_db(rel):
            continue
        if str(rel or "").strip().replace("\\", "/").casefold() in transacionados:
            continue
        if _shared_sync_is_cadastro_custos_csv(scope, rel):
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                info = _shared_sync_merge_cadastro_custos_versioned(
                    client_id,
                    target_abs,
                    data,
                    validar_store_ids=True,
                )
        elif _shared_sync_is_cadastro_lojas_csv(scope, rel):
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                info = _shared_sync_merge_cadastro_lojas_versioned(
                    target_abs,
                    data,
                    client_id,
                    validar_store_ids=True,
                )
        elif scope == "cadastro" and lower == "cadastro_produtos.csv":
            with _shared_sync_guard_legacy_cadastro_csv_locked(
                client_id,
                target_abs,
                data,
            ):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                info = _shared_sync_merge_csv_add_only(target_abs, data, scope, rel)
        elif lower == "produtos_compilado.csv" and scope in {"cadastro", "vendas"}:
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_validar_store_ids_csv_remoto_locked(
                    tenant_abs,
                    data,
                    rel,
                    exigir_store_id=True,
                )
                _shared_sync_exigir_produtos_compilado_sem_fotos_locais_locked(
                    client_id,
                    tenant_abs,
                    data,
                    rel,
                )
                with _shared_sync_path_lock_for(target_abs):
                    _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                    info = _shared_sync_merge_csv_add_only(target_abs, data, scope, rel)
        elif scope == "cadastro" and lower.endswith(".csv"):
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_validar_store_ids_csv_remoto_locked(tenant_abs, data, rel)
                with _shared_sync_path_lock_for(target_abs):
                    _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                    info = _shared_sync_merge_csv_add_only(target_abs, data, scope, rel)
        elif scope == "lojas_integracoes" and lower == "lojas_config.json":
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
            merged = _shared_sync_merge_lojas_integracoes_bytes(
                target_abs,
                data,
                add_only=True,
                client_id=client_id,
            )
            os.makedirs(os.path.dirname(target_abs), exist_ok=True)
            with open(target_abs, "wb") as f:
                f.write(merged)
            info = {"added": 1, "merged": True}
        elif scope == "vendas" and lower.endswith(".json"):
            info = {"added": 0, "skipped_state": True}
        else:
            legacy_photo_sku = _shared_sync_legacy_photo_sku(rel) if scope == "cadastro" else ""
            if legacy_photo_sku:
                from backend.services import integracoes

                with integracoes._LOJAS_CONFIG_LOCK:
                    with _shared_sync_guard_legacy_photo_locked(
                        client_id,
                        tenant_abs,
                        target_abs,
                        rel,
                    ):
                        info = _shared_sync_aplicar_foto_legada_atomica(
                            client_id,
                            tenant_abs,
                            backup_dir,
                            rel,
                            data,
                            somente_se_ausente=True,
                        )
            else:
                info = _shared_sync_write_missing_file(target_abs, data)
        if int(info.get("added") or 0) > 0 or info.get("merged"):
            escritos.append(rel)
            added += int(info.get("added") or 0)
        details.append({"file": rel, **info})
    return {
        "file_count": len(escritos),
        "files": escritos[:250],
        "added": added,
        "details": details[:250],
    }

configure_shared_sync_merge_sqlite_runtime()

__all__ = [
    "configure_shared_sync_merge_sqlite_runtime",
    "_shared_sync_bloquear_writer_store_id",
    "_shared_sync_merge_csv_add_only",
    "_shared_sync_validar_store_ids_df_remoto_locked",
    "_shared_sync_validar_store_ids_csv_remoto_locked",
    "_shared_sync_exigir_produtos_compilado_sem_fotos_locais_locked",
    "_shared_sync_guard_legacy_cadastro_csv_locked",
    "_shared_sync_exigir_fotos_legadas_sem_referencia_local_locked",
    "_shared_sync_legacy_photo_sku",
    "_shared_sync_guard_legacy_photo_locked",
    "_shared_sync_aplicar_foto_legada_atomica",
    "_shared_sync_merge_tombstones_integracoes_payload",
    "_shared_sync_store_ids_tombstonados",
    "_shared_sync_aplicar_tombstones_integracao",
    "_shared_sync_aplicar_lojas_integracoes_atomico",
    "_shared_sync_cadastro_lojas_canonical_df",
    "_shared_sync_cadastro_lojas_row_version",
    "_shared_sync_cadastro_lojas_updated_at",
    "_shared_sync_cadastro_lojas_is_tombstone",
    "_shared_sync_cadastro_lojas_priority",
    "_shared_sync_cadastro_lojas_is_conflict",
    "_shared_sync_cadastro_lojas_should_replace",
    "_shared_sync_write_csv_atomic",
    "_shared_sync_merge_cadastro_lojas_versioned",
    "_shared_sync_cadastro_custos_canonical_df",
    "_shared_sync_cadastro_custos_updated_at",
    "_shared_sync_cadastro_custos_priority",
    "_shared_sync_cadastro_custos_is_conflict",
    "_shared_sync_merge_cadastro_custos_versioned",
    "_shared_sync_sql_ident",
    "_shared_sync_sqlite_temp_from_bytes",
    "_shared_sync_sqlite_lock_for_path",
    "_shared_sync_sqlite_is_locked",
    "_shared_sync_sqlite_configure",
    "_shared_sync_sqlite_retry_locked",
    "SHARED_SYNC_VENDAS_TABLES",
    "_shared_sync_sqlite_backup_to_path",
    "_shared_sync_vendas_merge_table",
    "_shared_sync_stage_vendas_db_add_only",
    "_shared_sync_apply_vendas_dbs_add_only",
    "_shared_sync_merge_vendas_db_add_only",
    "_shared_sync_write_missing_file",
    "_shared_sync_aplicar_user_share_add_only",
]
