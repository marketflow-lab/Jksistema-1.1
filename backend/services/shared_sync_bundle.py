"""Shared Sync snapshot hashing and bundle assembly helpers."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import nullcontext
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
from backend.services.shared_sync_delta import _shared_sync_csv_read_bytes


def configure_shared_sync_bundle_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_snapshot_hash(entries: list[dict]) -> str:
    sha = hashlib.sha256()
    for item in entries:
        sha.update(str(item.get("relative_path") or "").encode("utf-8"))
        sha.update(b"\0")
        sha.update(str(item.get("size") or 0).encode("ascii"))
        sha.update(b"\0")
        sha.update(str(item.get("sha256") or "").encode("ascii"))
        sha.update(b"\n")
    return sha.hexdigest()


_SHARED_SYNC_TRANSIENT_OAUTH_KEYS = {
    "state", "code", "oauth_code", "authorization_code", "callback",
    "callback_url", "oauth_callback", "oauth_callback_url", "oauth_draft",
    "oauth_pending_state",
}


def _shared_sync_remove_transient_oauth(value):
    if isinstance(value, list):
        return [_shared_sync_remove_transient_oauth(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _shared_sync_remove_transient_oauth(item)
            for key, item in value.items()
            if str(key or "").strip().lower() not in _SHARED_SYNC_TRANSIENT_OAUTH_KEYS
        }
    return value


def _shared_sync_sanitize_transient_oauth_entries(scope: str, entries: list[dict]) -> list[dict]:
    if scope != "lojas_integracoes":
        return entries
    sanitized = []
    for item in entries:
        rel = str(item.get("relative_path") or "")
        if rel.lower() not in {"lojas_config.json", "integracoes.json"}:
            sanitized.append(item)
            continue
        data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
        try:
            payload = json.loads((data or b"").decode("utf-8-sig"))
            data = json.dumps(_shared_sync_remove_transient_oauth(payload), ensure_ascii=False, indent=2).encode("utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{rel} invalido para sincronizacao: {exc}")
        sanitized.append(_shared_sync_entry_from_bytes(rel, data, item.get("mtime") or time.time(), item.get("item_keys") or []))
    return sanitized


def _shared_sync_sanitize_strict_legacy_photo_entries(
    client_id: str,
    entries: list[dict],
) -> list[dict]:
    """Remove caminhos locais globais apenas da copia enviada pelo bundle."""

    alvos = {"cadastro_produtos.csv", "produtos_compilado.csv"}
    if not any(
        str(item.get("relative_path") or "").casefold() in alvos
        for item in entries
    ):
        return entries

    from backend.services.cadastro_fotos import (
        _cadastro_foto_coluna_candidata,
        _cadastro_foto_referencia_local_cadastro,
        _cadastro_fotos_escopo_estrito,
    )

    if not _cadastro_fotos_escopo_estrito(client_id):
        return entries
    sanitizadas: list[dict] = []
    for item in entries:
        rel = str(item.get("relative_path") or "")
        if rel.casefold() not in alvos:
            sanitizadas.append(item)
            continue
        data = (
            item.get("data")
            if "data" in item
            else _shared_sync_ler_arquivo_pacote(item["abs_path"])
        ) or b""
        if hashlib.sha256(data).hexdigest() != str(item.get("sha256") or ""):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_sync_snapshot_changed",
                    "message": "Um arquivo mudou durante a criacao do pacote.",
                    "file": rel,
                },
            )
        df = _shared_sync_csv_read_bytes(data, scope="", rel=rel)
        alterou = False
        for coluna in list(df.columns):
            if not _cadastro_foto_coluna_candidata(coluna):
                continue
            locais = df[coluna].apply(_cadastro_foto_referencia_local_cadastro)
            if bool(locais.any()):
                df.loc[locais, coluna] = ""
                alterou = True
        data_bundle = (
            df.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")
            if alterou
            else data
        )
        sanitizadas.append(
            _shared_sync_entry_from_bytes(
                rel,
                data_bundle,
                item.get("mtime") or time.time(),
                item.get("item_keys") or [],
            )
        )
    return sanitizadas


def _shared_sync_canonicalize_cadastro_photo_config(
    client_id: str,
    entries: list[dict],
) -> list[dict]:
    """Validate the producer config with the receiver's closed contract."""

    config_rel = "cadastro_fotos_config.json"
    configs = [
        item
        for item in entries
        if str(item.get("relative_path") or "")
        .strip()
        .replace("\\", "/")
        .casefold()
        == config_rel
    ]
    if not configs:
        return entries
    if len(configs) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_cadastro_snapshot_ambiguous",
                "message": "O snapshot contem configuracoes de fotos duplicadas.",
            },
        )

    from backend.services.shared_sync_apply_scope import (
        _shared_sync_cadastro_photo_config_bytes,
        _shared_sync_cadastro_photo_config_parse_locked,
    )

    item_config = configs[0]
    rel = str(item_config.get("relative_path") or config_rel)
    data = (
        item_config.get("data")
        if "data" in item_config
        else _shared_sync_ler_arquivo_pacote(item_config.get("abs_path") or "")
    ) or b""
    if hashlib.sha256(data).hexdigest() != str(item_config.get("sha256") or ""):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_snapshot_changed",
                "message": "A configuracao de fotos mudou durante a criacao do pacote.",
                "file": rel,
            },
        )
    config_abs = str(item_config.get("abs_path") or "").strip()
    if config_abs:
        tenant_abs = os.path.dirname(os.path.abspath(config_abs))
    else:
        tenant_resolver = globals().get("get_tenant_path")
        if not callable(tenant_resolver):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cadastro_photo_config_unsafe",
                    "message": "Nao foi possivel resolver o tenant da configuracao de fotos.",
                },
            )
        tenant_abs = os.path.abspath(tenant_resolver(client_id))
    config = _shared_sync_cadastro_photo_config_parse_locked(
        tenant_abs,
        data,
        label=rel,
    )
    canonical = _shared_sync_entry_from_bytes(
        config_rel,
        _shared_sync_cadastro_photo_config_bytes(config),
        item_config.get("mtime") or time.time(),
        item_config.get("item_keys") or [],
    )
    return [
        canonical if item is item_config else item
        for item in entries
    ]


def _shared_sync_filter_store_photos_by_canonical_rows(
    client_id: str,
    entries: list[dict],
) -> list[dict]:
    """Never advertise/store a photo revision before an active row owns it.

    User-share deltas remember every manifest item key after a successful
    apply.  Sending an orphan image would therefore make its bytes look known
    even though the receiver correctly refused to write them.  Filtering at
    bundle construction keeps late CRUD materialization able to publish the
    new row and the still-unknown image revision together.
    """

    fotos = [
        item
        for item in entries
        if str(item.get("relative_path") or "")
        .replace("\\", "/")
        .casefold()
        .startswith("cadastro_fotos/lojas/")
    ]
    from backend.services.cadastro_fotos import (
        _cadastro_caminho_e_link,
        _cadastro_fotos_escopo_estrito,
    )
    from backend.services.shared_sync_merge_sqlite import (
        _shared_sync_cadastro_lojas_canonical_df,
        _shared_sync_cadastro_lojas_photo_reference,
    )

    if not _cadastro_fotos_escopo_estrito(client_id):
        return entries
    cadastros = [
        item
        for item in entries
        if str(item.get("relative_path") or "")
        .replace("\\", "/")
        .casefold()
        == "cadastro_produtos_lojas.csv"
    ]
    if len(cadastros) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_canonical_photo_rows_ambiguous",
                "message": "O snapshot contem mais de um cadastro canonico por loja.",
            },
        )
    autorizadas: set[str] = set()
    autorizadas_paths: dict[str, str] = {}
    if cadastros:
        item = cadastros[0]
        data = (
            item.get("data")
            if "data" in item
            else _shared_sync_ler_arquivo_pacote(item["abs_path"])
        ) or b""
        if hashlib.sha256(data).hexdigest() != str(item.get("sha256") or ""):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_sync_snapshot_changed",
                    "message": "O cadastro canonico mudou durante a criacao do pacote.",
                    "file": "cadastro_produtos_lojas.csv",
                },
            )
        df = _shared_sync_cadastro_lojas_canonical_df(
            _shared_sync_csv_read_bytes(
                data,
                scope="cadastro",
                rel="cadastro_produtos_lojas.csv",
            )
        )
        for _indice, serie in df.iterrows():
            row = {
                str(coluna): str(serie.get(coluna) or "")
                for coluna in df.columns
            }
            if str(row.get("deleted_at_utc") or "").strip():
                continue
            referencia = _shared_sync_cadastro_lojas_photo_reference(
                row,
                str(client_id),
            )
            if referencia:
                referencia_normalizada = referencia.replace("\\", "/")
                chave_referencia = referencia_normalizada.casefold()
                autorizadas.add(chave_referencia)
                autorizadas_paths.setdefault(chave_referencia, referencia_normalizada)

    if autorizadas:
        canonical_abs = (
            str(cadastros[0].get("abs_path") or "").strip()
            if cadastros
            else ""
        )
        tenant_abs = (
            os.path.dirname(os.path.abspath(canonical_abs))
            if canonical_abs
            else os.path.abspath(get_tenant_path(client_id))
        )
        for chave_referencia in sorted(autorizadas):
            referencia = autorizadas_paths[chave_referencia]
            try:
                target = _shared_sync_resolve_tenant_path(tenant_abs, referencia)
            except HTTPException as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "shared_sync_store_photo_missing",
                        "message": "Uma linha ativa referencia uma foto local insegura.",
                        "file": referencia,
                    },
                ) from exc
            if (
                not os.path.lexists(target)
                or _cadastro_caminho_e_link(target)
                or not os.path.isfile(target)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "shared_sync_store_photo_missing",
                        "message": (
                            "Uma linha ativa referencia uma foto que nao existe "
                            "fisicamente; o pacote nao foi criado."
                        ),
                        "file": referencia,
                    },
                )

        fotos_coletadas: set[str] = set()
        for item in fotos:
            rel_foto = (
                str(item.get("relative_path") or "")
                .strip()
                .replace("\\", "/")
                .casefold()
            )
            if rel_foto in fotos_coletadas:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "shared_sync_canonical_photo_rows_ambiguous",
                        "message": "O snapshot contem uma foto por loja duplicada.",
                        "file": rel_foto,
                    },
                )
            fotos_coletadas.add(rel_foto)
        ausentes_do_pacote = sorted(autorizadas - fotos_coletadas)
        if ausentes_do_pacote:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_sync_store_photo_missing",
                    "message": (
                        "Uma linha ativa referencia uma foto que nao foi coletada; "
                        "o pacote nao foi criado."
                    ),
                    "files": [
                        autorizadas_paths[chave]
                        for chave in ausentes_do_pacote
                    ],
                },
            )

    return [
        item
        for item in entries
        if not str(item.get("relative_path") or "")
        .replace("\\", "/")
        .casefold()
        .startswith("cadastro_fotos/lojas/")
        or str(item.get("relative_path") or "")
        .replace("\\", "/")
        .casefold()
        in autorizadas
    ]

def _shared_sync_montar_pacote_locked(
    client_id: str,
    scope: str,
    username: str,
    machine_id: str = "",
    user_only: bool = False,
    known_keys: Optional[set[str]] = None,
    sanitize_user_share_oauth: bool = False,
) -> tuple[bytes, dict, list[str]]:
    item_keys: list[str] = []
    arquivos_sensiveis_obrigatorios: set[str] = set()
    if scope == "lojas_integracoes":
        from backend.services import integracoes as integracoes_service
        snapshot_lock = integracoes_service._LOJAS_CONFIG_LOCK
    else:
        snapshot_lock = nullcontext()

    with snapshot_lock:
        if scope == "lojas_integracoes":
            try:
                # Materializa migracoes e a recuperacao aditiva do .bak antes
                # de congelar os bytes. Assim uma maquina ja afetada nunca
                # publica o arquivo principal reduzido sobre as outras.
                integracoes_service._integracoes_validar_estado_atual_para_envio(
                    client_id,
                )
                lojas_materializadas = integracoes_service.carregar_lojas(client_id)
                integracoes_service._integracoes_validar_tombstones_contra_lojas_para_envio(
                    client_id,
                    lojas_materializadas,
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Nao foi possivel validar as lojas locais antes do envio. "
                        "A sincronizacao foi cancelada para preservar as contas."
                    ),
                ) from exc
            if known_keys is None:
                tenant_abs = os.path.abspath(
                    integracoes_service._tenant_path(client_id)
                )
                arquivos_sensiveis_obrigatorios = {
                    rel
                    for rel in (
                        "lojas_config.json",
                        "integracoes.json",
                        "lojas_sync_tombstones.json",
                    )
                    if os.path.exists(os.path.join(tenant_abs, rel))
                }
        if known_keys is None:
            entries, warnings = _shared_sync_coletar_arquivos(
                client_id,
                scope,
                username=username,
                user_only=user_only,
            )
        else:
            entries, warnings, item_keys = _shared_sync_coletar_arquivos_delta(
                client_id,
                scope,
                username=username,
                user_only=user_only,
                known_keys=known_keys,
            )
        # Mantem o conjunto de arquivos, bytes, hash e tamanho no mesmo
        # instante. Antes, um tombstone podia nascer entre a coleta e o ZIP.
        materializadas = []
        if scope == "lojas_integracoes":
            for item in entries:
                data = (
                    item.get("data")
                    if "data" in item
                    else _shared_sync_ler_arquivo_pacote(item["abs_path"])
                )
                materializadas.append(
                    _shared_sync_entry_from_bytes(
                        item.get("relative_path") or "",
                        data or b"",
                        item.get("mtime") or time.time(),
                        item.get("item_keys") or [],
                    )
                )
            entries = materializadas
            if known_keys is None:
                # O coletor generico tolera OSError e arquivos acima do limite.
                # Para credenciais/tombstones isso seria um snapshot parcial
                # perigoso: um primeiro push poderia perder a autoridade de
                # exclusao. Todo arquivo canonico existente e obrigatorio.
                tenant_abs = os.path.abspath(
                    integracoes_service._tenant_path(client_id)
                )
                rels_materializados = {
                    str(item.get("relative_path") or "").replace("\\", "/").lower()
                    for item in entries
                }
                # A uniao antes+depois fecha tanto o desaparecimento durante
                # a coleta quanto um tombstone que nasce enquanto ela ocorre.
                arquivos_sensiveis_obrigatorios.update(
                    rel
                    for rel in (
                        "lojas_config.json",
                        "integracoes.json",
                        "lojas_sync_tombstones.json",
                    )
                    if os.path.exists(os.path.join(tenant_abs, rel))
                )
                for rel_canonico in arquivos_sensiveis_obrigatorios:
                    if rel_canonico not in rels_materializados:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "Um arquivo local essencial de lojas e integracoes "
                                "nao pôde ser incluido no pacote. O envio foi "
                                "cancelado para preservar as contas."
                            ),
                        )
    if sanitize_user_share_oauth and scope == "lojas_integracoes":
        sanitizadas = []
        for item in entries:
            rel = item.get("relative_path") or ""
            if rel.lower() != "lojas_config.json":
                sanitizadas.append(item)
                continue
            data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
            data = _shared_sync_sanitizar_lojas_integracoes_user_share_bytes(data or b"")
            sanitizadas.append(_shared_sync_entry_from_bytes(rel, data, item.get("mtime") or time.time(), item.get("item_keys") or []))
        entries = sanitizadas
    entries = _shared_sync_sanitize_transient_oauth_entries(scope, entries)
    if scope == "cadastro":
        entries = _shared_sync_canonicalize_cadastro_photo_config(
            client_id,
            entries,
        )
    entries = _shared_sync_sanitize_strict_legacy_photo_entries(client_id, entries)
    if scope == "cadastro":
        entries = _shared_sync_filter_store_photos_by_canonical_rows(
            client_id,
            entries,
        )
    manifest = {
        "schema": 2,
        "app": "JK Sistema",
        "scope": scope,
        "client_id": str(client_id or "default").strip() or "default",
        "created_at": _shared_sync_now_iso(),
        "created_by": str(username or "").strip().lower(),
        "machine_id": str(machine_id or "").strip(),
        "snapshot_hash": _shared_sync_snapshot_hash(entries),
        "file_count": len(entries),
        "delta": known_keys is not None,
        "item_count": len(item_keys),
        "item_keys": item_keys,
        "files": [
            {
                "relative_path": item["relative_path"],
                "size": item["size"],
                "mtime": item["mtime"],
                "sha256": item["sha256"],
                "item_count": len(item.get("item_keys") or []),
            }
            for item in entries
        ],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for item in entries:
            data = item.get("data") if "data" in item else _shared_sync_ler_arquivo_pacote(item["abs_path"])
            zf.writestr("files/" + item["relative_path"], data or b"")
    bundle = buffer.getvalue()
    if len(bundle) > _shared_sync_max_bundle_bytes():
        raise HTTPException(
            status_code=413,
            detail="Pacote maior que o limite de sincronizacao. Reduza arquivos antigos ou aumente JK_SHARED_SYNC_MAX_BUNDLE_BYTES.",
        )
    return bundle, manifest, warnings


def _shared_sync_montar_pacote(
    client_id: str,
    scope: str,
    username: str,
    machine_id: str = "",
    user_only: bool = False,
    known_keys: Optional[set[str]] = None,
    sanitize_user_share_oauth: bool = False,
) -> tuple[bytes, dict, list[str]]:
    if scope in {"lojas_integracoes", "cadastro", "vendas"}:
        from backend.services import integracoes

        with integracoes._integracoes_bloquear_rmw_lojas(client_id):
            return _shared_sync_montar_pacote_locked(
                client_id,
                scope,
                username,
                machine_id=machine_id,
                user_only=user_only,
                known_keys=known_keys,
                sanitize_user_share_oauth=sanitize_user_share_oauth,
            )
    return _shared_sync_montar_pacote_locked(
        client_id,
        scope,
        username,
        machine_id=machine_id,
        user_only=user_only,
        known_keys=known_keys,
        sanitize_user_share_oauth=sanitize_user_share_oauth,
    )

configure_shared_sync_bundle_runtime()

__all__ = [
    "configure_shared_sync_bundle_runtime",
    "_shared_sync_snapshot_hash",
    "_shared_sync_remove_transient_oauth",
    "_shared_sync_sanitize_transient_oauth_entries",
    "_shared_sync_sanitize_strict_legacy_photo_entries",
    "_shared_sync_montar_pacote",
]
