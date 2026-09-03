"""Shared Sync package application and pull helpers."""

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


logger = logging.getLogger("jk_sistema")


def configure_shared_sync_apply_scope_runtime(runtime_module=None, peer_globals: dict[str, object] | None = None):
    runtime = configure_shared_sync_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    if peer_globals:
        globals().update(peer_globals)
    return runtime


def _shared_sync_favoritos_historico_legacy_json_username(rel: str) -> str:
    base = os.path.basename(str(rel or "")).lower()
    match = re.match(r"^favoritos_historico_(.+)\.json$", base)
    if not match:
        return ""
    return re.sub(r"[^a-z0-9_-]+", "_", match.group(1).strip()) or ""


def _shared_sync_validate_sqlite_payload(rel: str, data: bytes) -> None:
    if not (data or b"").startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}")
    fd, tmp_path = tempfile.mkstemp(prefix="shared_sync_validate_", suffix=".db")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as file:
            file.write(data or b"")
        conn = sqlite3.connect(tmp_path, timeout=max(5, SHARED_SYNC_SQLITE_BUSY_TIMEOUT_MS // 1000))
        try:
            _shared_sync_sqlite_configure(conn)
            _shared_sync_sqlite_quick_check(conn, rel)
        finally:
            conn.close()
    except HTTPException:
        raise
    except sqlite3.DatabaseError as exc:
        raise HTTPException(status_code=502, detail=f"Banco SQLite invalido no pacote: {rel}") from exc
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _shared_sync_read_validated_bundle(bundle: bytes, scope: str) -> tuple[dict, list[tuple[str, bytes]]]:
    max_file = _shared_sync_max_file_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            try:
                manifest_info = zf.getinfo("manifest.json")
            except KeyError as exc:
                raise HTTPException(status_code=502, detail="Backup remoto sem manifest.json.") from exc
            if manifest_info.file_size > 2 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Manifesto do Shared Sync maior que o limite permitido.")
            try:
                manifest = json.loads(zf.read(manifest_info).decode("utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Manifesto do Shared Sync invalido.") from exc
            if not isinstance(manifest, dict):
                raise HTTPException(status_code=502, detail="Manifesto do Shared Sync invalido.")
            schema = manifest.get("schema")
            if schema not in {None, 1, 2}:
                raise HTTPException(status_code=400, detail="Versao do pacote Shared Sync nao suportada.")
            if str(manifest.get("scope") or "") != scope:
                raise HTTPException(status_code=400, detail="Backup remoto pertence a outro escopo.")
            files = manifest.get("files") or []
            if not isinstance(files, list):
                raise HTTPException(status_code=502, detail="Lista de arquivos invalida no manifesto.")
            if schema == 2 and int(manifest.get("file_count") or 0) != len(files):
                raise HTTPException(status_code=502, detail="Contagem de arquivos divergente no manifesto.")

            fontes: list[tuple[str, bytes]] = []
            seen_rels = set()
            archive_names = zf.namelist()
            for item in files:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=502, detail="Entrada invalida no manifesto do Shared Sync.")
                rel = _shared_sync_relativo_seguro(item.get("relative_path"))
                rel_key = rel.lower()
                if rel_key in seen_rels:
                    raise HTTPException(status_code=502, detail=f"Arquivo duplicado no manifesto: {rel}")
                seen_rels.add(rel_key)
                if _shared_sync_path_permanently_excluded(rel):
                    raise HTTPException(status_code=400, detail="Arquivo permanentemente excluido do Shared Sync.")
                legacy_state = scope == "vendas" and rel_key == "vendas_sync_state.json"
                if not legacy_state and not _shared_sync_scope_match(scope, rel):
                    raise HTTPException(status_code=400, detail=f"Arquivo fora do escopo: {rel}")
                if scope == "lojas_integracoes":
                    canonical = {
                        "lojas_config.json": "lojas_config.json",
                        "integracoes.json": "integracoes.json",
                        "lojas_sync_tombstones.json": "lojas_sync_tombstones.json",
                    }.get(rel_key)
                    if canonical and rel != canonical:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Nome de arquivo nao canonico no pacote: {rel}",
                        )
                if _shared_sync_transient_filename(os.path.basename(rel)):
                    raise HTTPException(status_code=400, detail=f"Arquivo temporario nao permitido no pacote: {rel}")

                member = "files/" + rel
                if archive_names.count(member) != 1:
                    raise HTTPException(status_code=502, detail=f"Backup remoto sem arquivo unico esperado: {rel}")
                member_info = zf.getinfo(member)
                try:
                    expected_size = int(item.get("size"))
                except (TypeError, ValueError):
                    expected_size = -1
                if schema == 2 and expected_size < 0:
                    raise HTTPException(status_code=502, detail=f"Tamanho ausente no manifesto: {rel}")
                if expected_size > max_file or member_info.file_size > max_file:
                    raise HTTPException(status_code=413, detail=f"Arquivo maior que o limite de sincronizacao: {rel}")
                data = zf.read(member_info)
                if expected_size >= 0 and len(data) != expected_size:
                    raise HTTPException(status_code=502, detail=f"Tamanho divergente no pacote: {rel}")
                expected_hash = str(item.get("sha256") or "").strip().lower()
                if schema == 2 and not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                    raise HTTPException(status_code=502, detail=f"Hash ausente ou invalido no manifesto: {rel}")
                if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
                    raise HTTPException(status_code=502, detail=f"Hash divergente no pacote: {rel}")
                if rel_key.endswith((".db", ".sqlite", ".sqlite3")):
                    _shared_sync_validate_sqlite_payload(rel, data)
                if not legacy_state:
                    fontes.append((rel, data))
            return manifest, fontes
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Pacote Shared Sync invalido.") from exc


def _shared_sync_atomic_write(target_abs: str, data: bytes) -> None:
    # Reentrant by design: callers may hold this same canonical path lock
    # across backup + write, while direct users still coordinate with Cadastro,
    # NCM and stock writers instead of replacing the file concurrently.
    with _shared_sync_path_lock_for(target_abs):
        os.makedirs(os.path.dirname(target_abs), exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(target_abs)}.sharedsync_",
            suffix=".tmp",
            dir=os.path.dirname(target_abs),
        )
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data or b"")
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_path, target_abs)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass


def _shared_sync_cadastro_store_photo_key(scope: str, rel: str) -> str:
    if str(scope or "").strip().casefold() != "cadastro":
        return ""
    normalized = str(rel or "").strip().replace("\\", "/")
    if normalized.casefold().startswith("cadastro_fotos/lojas/"):
        return normalized
    return ""


def _shared_sync_cadastro_store_photo_should_write(
    target_abs: str,
    remote_data: bytes,
    decision: str,
) -> bool:
    if decision not in {"remote", "tie"}:
        return False
    if not os.path.exists(target_abs):
        return True
    if not os.path.isfile(target_abs):
        raise HTTPException(status_code=502, detail="Destino de foto por loja invalido.")

    remote_digest = hashlib.sha256(remote_data or b"").hexdigest()
    with open(target_abs, "rb") as file:
        local_digest = hashlib.sha256(file.read()).hexdigest()
    if remote_digest == local_digest:
        return False
    if decision == "remote":
        return True
    # With an exactly tied product row, image bytes have no row-level version
    # to break the tie.  The digest ordering is direction-independent, so two
    # peers converge instead of whichever pull happened last winning.
    return remote_digest > local_digest


def _shared_sync_remover_variantes_obsoletas_foto_store(
    tenant_abs: str,
    backup_dir: str,
    photo_rel: str,
) -> list[str]:
    """Remove extensoes antigas do mesmo SKU depois de publicar a vencedora."""

    from backend.services.cadastro_fotos import (
        CADASTRO_FOTOS_EXTENSOES,
        _cadastro_caminho_e_link,
    )

    rel = _shared_sync_cadastro_store_photo_key("cadastro", photo_rel)
    partes = rel.split("/")
    if len(partes) != 4:
        raise HTTPException(status_code=502, detail="Caminho de foto por loja invalido.")
    nome_alvo = partes[-1]
    base_alvo, extensao_alvo = os.path.splitext(nome_alvo)
    if not base_alvo or extensao_alvo.casefold() not in CADASTRO_FOTOS_EXTENSOES:
        raise HTTPException(status_code=502, detail="Extensao de foto por loja invalida.")

    alvo_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
    pasta_abs = os.path.dirname(alvo_abs)
    if not os.path.isdir(pasta_abs):
        return []

    removidos: list[str] = []
    for entrada in sorted(os.scandir(pasta_abs), key=lambda item: item.name.casefold()):
        base, extensao = os.path.splitext(entrada.name)
        if (
            base.casefold() != base_alvo.casefold()
            or extensao.casefold() not in CADASTRO_FOTOS_EXTENSOES
            or os.path.normcase(os.path.abspath(entrada.path))
            == os.path.normcase(os.path.abspath(alvo_abs))
        ):
            continue
        variante_rel = "/".join([*partes[:-1], entrada.name])
        variante_abs = _shared_sync_resolve_tenant_path(tenant_abs, variante_rel)
        with _shared_sync_path_lock_for(variante_abs):
            if not os.path.lexists(variante_abs):
                continue
            if _cadastro_caminho_e_link(variante_abs) or not os.path.isfile(
                variante_abs
            ):
                raise HTTPException(
                    status_code=502,
                    detail="Variante obsoleta de foto por loja insegura.",
                )
            _shared_sync_backup_target(
                tenant_abs,
                backup_dir,
                variante_rel,
                variante_abs,
            )
            os.unlink(variante_abs)
            removidos.append(variante_rel)
    return removidos


def _shared_sync_validar_conflitos_fotos_grupo_remoto(
    client_id: str,
    tenant_abs: str,
    cadastro_lojas_bytes: bytes,
) -> None:
    """Falha antes do merge se um SKU compartilhado tiver nomes divergentes."""

    config_path = os.path.join(tenant_abs, "cadastro_fotos_config.json")
    if not os.path.lexists(config_path):
        return
    from backend.services.cadastro_common import _normalizar_sku_mes
    from backend.services.cadastro_fotos import (
        CadastroFotosConfigInvalida,
        _cadastro_fotos_config_carregar,
        _cadastro_store_id_foto_segmento,
    )
    from backend.services.shared_sync_merge_sqlite import (
        _shared_sync_cadastro_lojas_canonical_df,
        _shared_sync_cadastro_lojas_photo_reference,
        _shared_sync_csv_read_bytes,
    )

    try:
        config = _cadastro_fotos_config_carregar(client_id)
    except CadastroFotosConfigInvalida as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos por loja e invalida.",
            },
        ) from exc
    if not config.get("strict_store_scope") or not config.get("shared_groups"):
        return

    grupo_por_store: dict[str, str] = {}
    for grupo in config["shared_groups"]:
        for store_id in grupo["store_ids"]:
            grupo_por_store[store_id] = grupo["group_id"]

    remoto_df = _shared_sync_cadastro_lojas_canonical_df(
        _shared_sync_csv_read_bytes(cadastro_lojas_bytes)
    )
    nomes_por_sku: dict[tuple[str, str], set[str]] = {}
    for _indice, serie in remoto_df.iterrows():
        row = {str(coluna): str(serie.get(coluna) or "") for coluna in remoto_df.columns}
        if str(row.get("deleted_at_utc") or "").strip():
            continue
        store_id = str(row.get("store_id") or "").strip()
        group_id = grupo_por_store.get(store_id)
        if not group_id:
            continue
        photo_rel = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        if not photo_rel:
            continue
        partes_foto = photo_rel.split("/")
        if (
            len(partes_foto) != 4
            or partes_foto[2] != _cadastro_store_id_foto_segmento(store_id)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_group_photo_path_noncanonical",
                    "message": (
                        "Shared Sync bloqueado: grupo compartilhado exige o caminho "
                        "canonico de fotos por loja."
                    ),
                },
            )
        sku = _normalizar_sku_mes(row.get("sku") or "").strip().upper()
        if not sku:
            continue
        nome = os.path.basename(photo_rel).casefold()
        nomes_por_sku.setdefault((group_id, sku), set()).add(nome)

    conflitos = sorted(
        sku
        for (_group_id, sku), nomes in nomes_por_sku.items()
        if len(nomes) > 1
    )
    if conflitos:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_group_photo_conflict",
                "message": (
                    "Shared Sync bloqueado: lojas do mesmo grupo apontam fotos "
                    "diferentes para o mesmo SKU."
                ),
                "skus": sorted(set(conflitos)),
            },
        )


def _shared_sync_foto_store_ler_segura(
    tenant_abs: str,
    photo_rel: str,
) -> bytes | None:
    """Read one physical store photo, rejecting every reparse/non-file target."""

    from backend.services.cadastro_fotos import _cadastro_caminho_e_link

    target = _shared_sync_resolve_tenant_path(tenant_abs, photo_rel)
    if not os.path.lexists(target):
        return None
    if _cadastro_caminho_e_link(target) or not os.path.isfile(target):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_photo_unsafe",
                "message": "Uma foto por loja possui destino local inseguro.",
                "file": photo_rel,
            },
        )
    with open(target, "rb") as arquivo:
        return arquivo.read()


def _shared_sync_planejar_fotos_grupo_compartilhado(
    client_id: str,
    tenant_abs: str,
    target_abs: str,
    cadastro_lojas_bytes: bytes,
    fontes: list[tuple[str, bytes]],
) -> dict[str, Any]:
    """Prove a precedencia de cada evento de foto antes de qualquer fan-out.

    ``row_version`` also changes for ordinary product edits, so it is not enough
    by itself to authorize destructive image replacement.  A conflicting
    remote state must be coherent for the group, cover every conflicting local
    store and dominate both its version and timestamp.  Otherwise the pull is
    rejected and the surrounding transaction restores config/CSV/photos.
    """

    from backend.services.cadastro_common import _normalizar_sku_mes
    from backend.services.cadastro_fotos import (
        CadastroFotosConfigInvalida,
        CADASTRO_FOTOS_EXTENSOES,
        _cadastro_fotos_config_carregar,
        _cadastro_store_id_foto_segmento,
    )
    from backend.services.shared_sync_delta import _shared_sync_texto_chave
    from backend.services.shared_sync_merge_sqlite import (
        _SHARED_SYNC_CADASTRO_LOJAS_COLUMNS,
        _shared_sync_cadastro_lojas_canonical_df,
        _shared_sync_cadastro_lojas_is_tombstone,
        _shared_sync_cadastro_lojas_photo_reference,
        _shared_sync_cadastro_lojas_photo_vote,
        _shared_sync_cadastro_lojas_priority,
        _shared_sync_cadastro_lojas_row_version,
        _shared_sync_cadastro_lojas_updated_at,
        _shared_sync_csv_read_bytes,
    )

    plano_vazio = {"events": {}, "lock_fontes": list(fontes)}
    try:
        config = _cadastro_fotos_config_carregar(client_id)
    except CadastroFotosConfigInvalida as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos por loja e invalida.",
            },
        ) from exc
    if not config.get("strict_store_scope") or not config.get("shared_groups"):
        return plano_vazio

    group_by_store: dict[str, str] = {}
    stores_by_group: dict[str, list[str]] = {}
    for grupo in config["shared_groups"]:
        group_id = str(grupo["group_id"])
        stores = [str(store_id) for store_id in grupo["store_ids"]]
        stores_by_group[group_id] = stores
        for store_id in stores:
            group_by_store[store_id] = group_id

    fontes_map = {
        str(rel or "").strip().replace("\\", "/"): bytes(data or b"")
        for rel, data in fontes
    }

    def tabela(data: bytes) -> pd.DataFrame:
        return _shared_sync_cadastro_lojas_canonical_df(
            _shared_sync_csv_read_bytes(data)
        )

    remoto_df = tabela(cadastro_lojas_bytes)
    if os.path.lexists(target_abs):
        if not os.path.isfile(target_abs):
            raise HTTPException(status_code=409, detail="Cadastro por loja local invalido.")
        with open(target_abs, "rb") as arquivo:
            local_df = tabela(arquivo.read())
    else:
        local_df = pd.DataFrame(columns=list(remoto_df.columns))

    # Match the merge's exact column union and insertion order so content
    # digests, equality and source votes cannot diverge from the later commit.
    columns: list[str] = []
    for coluna in [*local_df.columns, *remoto_df.columns]:
        if coluna not in columns:
            columns.append(str(coluna))
    for coluna in _SHARED_SYNC_CADASTRO_LOJAS_COLUMNS:
        if coluna not in columns:
            columns.append(coluna)
    for coluna in columns:
        if coluna not in local_df.columns:
            local_df[coluna] = ""
        if coluna not in remoto_df.columns:
            remoto_df[coluna] = ""
    local_df = local_df[columns].fillna("")
    remoto_df = remoto_df[columns].fillna("")

    def linhas(df: pd.DataFrame) -> list[dict[str, str]]:
        return [
            {coluna: str(serie.get(coluna) or "") for coluna in columns}
            for _indice, serie in df.iterrows()
        ]

    remoto_rows = linhas(remoto_df)
    local_rows = linhas(local_df)

    def chave_row(row: dict[str, str]) -> tuple[str, str] | None:
        store_id = str(row.get("store_id") or "").strip()
        sku = _shared_sync_texto_chave(
            _normalizar_sku_mes(row.get("sku") or "")
        )
        return (store_id, sku) if store_id and sku else None

    def vencedoras(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
        saida: dict[tuple[str, str], dict[str, str]] = {}
        for row in rows:
            chave = chave_row(row)
            if chave is None:
                continue
            atual = saida.get(chave)
            if atual is None or _shared_sync_cadastro_lojas_priority(
                row
            ) > _shared_sync_cadastro_lojas_priority(atual):
                saida[chave] = row
        return saida

    local_by_key = vencedoras(local_rows)
    remoto_by_key = vencedoras(remoto_rows)

    # Reproduce the versioned merge vote before touching the CSV.  A metadata-
    # only delta can still elect a remote photo row when its bytes already
    # exist locally.  Limiting group planning to files carried by the bundle
    # would let that row bypass the shared-group precedence barrier.
    vencedora_por_chave = dict(local_by_key)
    origem_por_chave = {chave: "local" for chave in local_by_key}
    remoto_rows_por_chave: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in remoto_rows:
        chave = chave_row(row)
        if chave is None:
            continue
        remoto_rows_por_chave.setdefault(chave, []).append(row)
        atual = vencedora_por_chave.get(chave)
        if atual is None:
            vencedora_por_chave[chave] = row
            origem_por_chave[chave] = "remote"
        elif atual == row:
            if origem_por_chave.get(chave) == "local":
                origem_por_chave[chave] = "tie"
        elif _shared_sync_cadastro_lojas_priority(
            row
        ) > _shared_sync_cadastro_lojas_priority(atual):
            vencedora_por_chave[chave] = row
            origem_por_chave[chave] = "remote"

    decisoes_previas: dict[str, str] = {}
    for chave, rows_chave in remoto_rows_por_chave.items():
        vencedora = vencedora_por_chave[chave]
        vencedora_ref = _shared_sync_cadastro_lojas_photo_reference(
            vencedora,
            client_id,
        )
        origem = origem_por_chave.get(chave, "local")
        for row in rows_chave:
            ref = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
            if not ref:
                continue
            if (
                ref != vencedora_ref
                or _shared_sync_cadastro_lojas_is_tombstone(vencedora)
            ):
                decisao = "local"
            elif origem in {"remote", "tie"}:
                decisao = origem
            else:
                decisao = "local"
            decisoes_previas[ref] = _shared_sync_cadastro_lojas_photo_vote(
                decisoes_previas.get(ref, ""),
                decisao,
            )

    impactados: dict[tuple[str, str], set[str]] = {}
    dono_por_ref: dict[str, tuple[str, str]] = {}
    remoto_row_por_ref: dict[str, dict[str, str]] = {}
    for row in remoto_rows:
        if _shared_sync_cadastro_lojas_is_tombstone(row):
            continue
        chave = chave_row(row)
        if chave is None:
            continue
        store_id, sku = chave
        group_id = group_by_store.get(store_id)
        if not group_id:
            continue
        ref = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        if not ref or decisoes_previas.get(ref) not in {"remote", "tie"}:
            continue
        group_sku = (group_id, sku)
        dono = dono_por_ref.get(ref)
        if dono is not None and dono != group_sku:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_group_photo_identity_conflict",
                    "message": "A mesma foto compartilhada esta ligada a SKUs diferentes.",
                },
            )
        dono_por_ref[ref] = group_sku
        remoto_row_por_ref[ref] = row
        impactados.setdefault(group_sku, set()).add(ref)

    if not impactados:
        return plano_vazio

    estado_cache: dict[tuple[str, bool], tuple[str, str, bytes] | None] = {}

    def estado_row(
        row: dict[str, str],
        *,
        remoto: bool,
    ) -> tuple[str, str, bytes] | None:
        ref = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        if not ref:
            return None
        cache_key = (ref, remoto)
        if cache_key in estado_cache:
            return estado_cache[cache_key]
        data = fontes_map.get(ref) if remoto else None
        if data is None:
            data = _shared_sync_foto_store_ler_segura(tenant_abs, ref)
        if data is None:
            estado_cache[cache_key] = None
            return None
        estado = (
            os.path.basename(ref).casefold(),
            hashlib.sha256(data).hexdigest(),
            data,
        )
        estado_cache[cache_key] = estado
        return estado

    def domina(remoto: dict[str, str], local: dict[str, str]) -> bool:
        rv_remoto = _shared_sync_cadastro_lojas_row_version(remoto)
        rv_local = _shared_sync_cadastro_lojas_row_version(local)
        ts_remoto = _shared_sync_cadastro_lojas_updated_at(remoto)
        ts_local = _shared_sync_cadastro_lojas_updated_at(local)
        return (
            rv_remoto >= rv_local
            and ts_remoto >= ts_local
            and (rv_remoto > rv_local or ts_remoto > ts_local)
        )

    events: dict[tuple[str, str], dict[str, Any]] = {}
    locks_map = dict(fontes_map)
    for (group_id, sku), refs_impactadas in sorted(impactados.items()):
        stores = stores_by_group[group_id]
        remote_group = {
            store_id: remoto_by_key[(store_id, sku)]
            for store_id in stores
            if (store_id, sku) in remoto_by_key
        }
        local_group = {
            store_id: local_by_key[(store_id, sku)]
            for store_id in stores
            if (store_id, sku) in local_by_key
        }

        estados_remotos: dict[str, tuple[str, str, bytes]] = {}
        for store_id, row in remote_group.items():
            if _shared_sync_cadastro_lojas_is_tombstone(row):
                continue
            ref = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
            if not ref:
                continue
            estado = estado_row(row, remoto=True)
            if estado is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "shared_sync_store_photo_bytes_required",
                        "message": "A foto vencedora nao acompanha o snapshot nem existe localmente.",
                        "file": ref,
                    },
                )
            estados_remotos[store_id] = estado

        identidades_remotas = {
            (estado[0], estado[1]) for estado in estados_remotos.values()
        }
        if len(identidades_remotas) != 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_group_photo_state_conflict",
                    "message": "O estado remoto das fotos compartilhadas nao e coerente.",
                    "sku": sku,
                },
            )
        nome, digest = next(iter(identidades_remotas))
        escolhido = next(
            estado[2]
            for estado in estados_remotos.values()
            if estado[0] == nome and estado[1] == digest
        )
        estados_impactados = [
            estado_row(remoto_row_por_ref[ref], remoto=True)
            for ref in refs_impactadas
        ]
        if any(
            estado is None
            or estado[1] != digest
            or estado[0] != nome
            for estado in estados_impactados
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_group_photo_state_conflict",
                    "message": "Os bytes remotos nao representam um unico evento de foto.",
                    "sku": sku,
                },
            )

        barreiras: list[tuple[str, dict[str, str]]] = []
        for store_id, row in local_group.items():
            if _shared_sync_cadastro_lojas_is_tombstone(row):
                barreiras.append((store_id, row))
                continue
            local_estado = estado_row(row, remoto=False)
            if (
                local_estado is None
                or (local_estado[0], local_estado[1]) != (nome, digest)
            ):
                barreiras.append((store_id, row))

        for store_id, local_row in barreiras:
            remote_row = remote_group.get(store_id)
            remote_estado = (
                estado_row(remote_row, remoto=True) if remote_row is not None else None
            )
            if (
                remote_row is None
                or _shared_sync_cadastro_lojas_is_tombstone(remote_row)
                or remote_estado is None
                or (remote_estado[0], remote_estado[1]) != (nome, digest)
                or not domina(remote_row, local_row)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "shared_group_photo_precedence_ambiguous",
                        "message": (
                            "A foto remota diverge de um estado local sem "
                            "precedencia comprovavel; nenhuma foto foi alterada."
                        ),
                        "sku": sku,
                    },
                )

        caminhos = [
            (
                f"cadastro_fotos/lojas/"
                f"{_cadastro_store_id_foto_segmento(store_id)}/{nome}"
            )
            for store_id in stores
        ]

        # An unreferenced variant has no version/timestamp able to prove that
        # it is obsolete.  Never delete different bytes in that situation.
        base, _ext = os.path.splitext(nome)
        for store_id, caminho in zip(stores, caminhos):
            segmento = _cadastro_store_id_foto_segmento(store_id)
            local_row = local_group.get(store_id)
            local_ref = (
                _shared_sync_cadastro_lojas_photo_reference(local_row, client_id)
                if local_row is not None
                and not _shared_sync_cadastro_lojas_is_tombstone(local_row)
                else ""
            )
            for extensao in CADASTRO_FOTOS_EXTENSOES:
                variante = f"cadastro_fotos/lojas/{segmento}/{base}{extensao}"
                variante_data = _shared_sync_foto_store_ler_segura(
                    tenant_abs,
                    variante,
                )
                if variante_data is None or hashlib.sha256(variante_data).hexdigest() == digest:
                    continue
                if not local_ref or variante.casefold() != local_ref.casefold():
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "shared_group_photo_orphan_conflict",
                            "message": (
                                "Uma variante local sem evento comprovavel impede "
                                "a substituicao da foto compartilhada."
                            ),
                            "sku": sku,
                        },
                    )

        for caminho in caminhos:
            locks_map[caminho] = escolhido
        events[(group_id, sku)] = {
            "refs": set(refs_impactadas),
            "name": nome,
            "digest": digest,
            "data": escolhido,
            "paths": caminhos,
        }

    return {
        "events": events,
        "lock_fontes": list(locks_map.items()),
    }


def _shared_sync_expandir_fotos_grupo_compartilhado(
    fontes: list[tuple[str, bytes]],
    decisoes: dict[str, str],
    plano: dict[str, Any],
) -> tuple[list[tuple[str, bytes]], dict[str, str]]:
    """Fan out only group events whose row winner was also authorized."""

    fontes_map = {
        str(rel or "").strip().replace("\\", "/"): bytes(data or b"")
        for rel, data in fontes
    }
    decisoes_map = {
        str(rel or "").strip().replace("\\", "/"): str(decisao or "local")
        for rel, decisao in decisoes.items()
    }
    ordem = list(fontes_map)
    for event in (plano.get("events") or {}).values():
        refs = set(event.get("refs") or set())
        if not any(decisoes_map.get(ref) in {"remote", "tie"} for ref in refs):
            continue
        for rel in event["paths"]:
            if rel not in fontes_map:
                ordem.append(rel)
            fontes_map[rel] = bytes(event["data"])
            decisoes_map[rel] = "remote"
    return [(rel, fontes_map[rel]) for rel in ordem], decisoes_map


def _shared_sync_exigir_bytes_fotos_vencedoras(
    tenant_abs: str,
    fontes: list[tuple[str, bytes]],
    decisoes: dict[str, str],
) -> None:
    """A winning row never authorizes an implicit/missing image revision."""

    presentes = {
        str(rel or "").strip().replace("\\", "/")
        for rel, _data in fontes
    }
    for photo_rel, decision in decisoes.items():
        rel = str(photo_rel or "").strip().replace("\\", "/")
        if decision not in {"remote", "tie"} or rel in presentes:
            continue
        if _shared_sync_foto_store_ler_segura(tenant_abs, rel) is not None:
            continue
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_photo_bytes_required",
                "message": (
                    "A linha vencedora referencia uma foto que nao acompanha "
                    "o snapshot e ainda nao existe neste peer."
                ),
                "file": rel,
            },
        )


def _shared_sync_caminhos_transacao_fotos_store(
    client_id: str,
    tenant_abs: str,
    fontes: list[tuple[str, bytes]],
) -> list[str]:
    """Materializa destinos e variantes antes do primeiro efeito da transacao."""

    if not fontes:
        return []

    from backend.services.cadastro_fotos import (
        CADASTRO_FOTOS_EXTENSOES,
        CadastroFotosConfigInvalida,
        _cadastro_caminho_e_link,
        _cadastro_fotos_config_carregar,
        _cadastro_store_id_foto_segmento,
    )

    segmentos_grupo: dict[str, list[str]] = {}
    if os.path.lexists(os.path.join(tenant_abs, "cadastro_fotos_config.json")):
        try:
            config = _cadastro_fotos_config_carregar(client_id)
        except CadastroFotosConfigInvalida as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cadastro_photo_config_invalid",
                    "message": "A configuracao de fotos por loja e invalida.",
                },
            ) from exc
        for grupo in config.get("shared_groups") or []:
            segmentos = [
                _cadastro_store_id_foto_segmento(store_id)
                for store_id in grupo["store_ids"]
            ]
            for segmento in segmentos:
                segmentos_grupo[segmento] = segmentos

    caminhos: dict[str, str] = {}
    for rel_bruto, _data in fontes:
        rel = _shared_sync_cadastro_store_photo_key("cadastro", rel_bruto)
        partes = rel.split("/")
        if len(partes) != 4:
            raise HTTPException(status_code=502, detail="Caminho de foto por loja invalido.")
        nome = partes[-1]
        base, extensao = os.path.splitext(nome)
        if not base or extensao.casefold() not in CADASTRO_FOTOS_EXTENSOES:
            raise HTTPException(status_code=502, detail="Extensao de foto por loja invalida.")
        for segmento in segmentos_grupo.get(partes[2], [partes[2]]):
            prefixo = f"cadastro_fotos/lojas/{segmento}"
            nomes_variantes = {nome, *(f"{base}{ext}" for ext in CADASTRO_FOTOS_EXTENSOES)}
            pasta = _shared_sync_resolve_tenant_path(tenant_abs, f"{prefixo}/{nome}")
            pasta = os.path.dirname(pasta)
            if os.path.isdir(pasta):
                with os.scandir(pasta) as entradas:
                    for entrada in entradas:
                        base_atual, ext_atual = os.path.splitext(entrada.name)
                        if (
                            base_atual.casefold() == base.casefold()
                            and ext_atual.casefold() in CADASTRO_FOTOS_EXTENSOES
                        ):
                            nomes_variantes.add(entrada.name)
            for nome_variante in nomes_variantes:
                variante_rel = f"{prefixo}/{nome_variante}"
                variante_abs = _shared_sync_resolve_tenant_path(
                    tenant_abs,
                    variante_rel,
                )
                if os.path.lexists(variante_abs) and (
                    _cadastro_caminho_e_link(variante_abs)
                    or not os.path.isfile(variante_abs)
                ):
                    raise HTTPException(
                        status_code=502,
                        detail="Destino de foto por loja inseguro.",
                    )
                caminhos[os.path.normcase(os.path.realpath(variante_abs))] = variante_abs
    return [caminhos[chave] for chave in sorted(caminhos)]


def _shared_sync_propagar_referencias_fotos_grupo(
    client_id: str,
    target_abs: str,
    cadastro_lojas_bytes: bytes,
    fontes_expandidas: list[tuple[str, bytes]],
    photo_decisions: dict[str, str],
) -> int:
    """Atualiza as linhas pares para a extensao fisica eleita no grupo."""

    if not fontes_expandidas:
        return 0

    from backend.services.cadastro_common import _normalizar_sku_mes
    from backend.services.cadastro_fotos import (
        _cadastro_fotos_config_carregar,
        _cadastro_store_id_foto_segmento,
    )
    from backend.services.cadastro_lojas_produtos import (
        _cadastro_propagar_referencias_fotos_preparadas,
    )
    from backend.services.shared_sync_merge_sqlite import (
        _shared_sync_cadastro_lojas_canonical_df,
        _shared_sync_cadastro_lojas_photo_reference,
        _shared_sync_csv_read_bytes,
        _shared_sync_write_csv_atomic,
    )

    config = _cadastro_fotos_config_carregar(client_id)
    grupo_por_store: dict[str, str] = {}
    store_por_segmento: dict[str, str] = {}
    for grupo in config.get("shared_groups") or []:
        for store_id in grupo["store_ids"]:
            grupo_por_store[store_id] = grupo["group_id"]
            store_por_segmento[_cadastro_store_id_foto_segmento(store_id)] = store_id
    if not grupo_por_store:
        return 0

    remoto_df = _shared_sync_cadastro_lojas_canonical_df(
        _shared_sync_csv_read_bytes(cadastro_lojas_bytes)
    )
    sku_por_grupo_nome: dict[tuple[str, str], str] = {}
    for _indice, serie in remoto_df.iterrows():
        row = {str(coluna): str(serie.get(coluna) or "") for coluna in remoto_df.columns}
        if str(row.get("deleted_at_utc") or "").strip():
            continue
        store_id = str(row.get("store_id") or "").strip()
        group_id = grupo_por_store.get(store_id)
        photo_rel = _shared_sync_cadastro_lojas_photo_reference(row, client_id)
        if (
            not group_id
            or not photo_rel
            or str(photo_decisions.get(photo_rel) or "local")
            not in {"remote", "tie"}
        ):
            continue
        sku = _normalizar_sku_mes(row.get("sku") or "").strip().upper()
        chave = (group_id, os.path.basename(photo_rel).casefold())
        anterior = sku_por_grupo_nome.get(chave)
        if anterior and anterior != sku:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_group_photo_identity_conflict",
                    "message": "A mesma foto compartilhada esta ligada a SKUs diferentes.",
                },
            )
        sku_por_grupo_nome[chave] = sku

    preparadas: list[dict[str, Any]] = []
    for photo_rel, photo_data in fontes_expandidas:
        if str(photo_decisions.get(photo_rel) or "local") not in {"remote", "tie"}:
            continue
        partes = photo_rel.split("/")
        if len(partes) != 4:
            continue
        store_id = store_por_segmento.get(partes[2])
        group_id = grupo_por_store.get(store_id or "")
        sku = sku_por_grupo_nome.get((group_id or "", partes[3].casefold()))
        if not store_id or not sku:
            continue
        preparadas.append(
            {
                "store_id": store_id,
                "sku": sku,
                "relativo": photo_rel,
                "conteudo": bytes(photo_data or b""),
            }
        )

    if not preparadas or not os.path.exists(target_abs):
        return 0
    with open(target_abs, "rb") as arquivo:
        atual_df = _shared_sync_cadastro_lojas_canonical_df(
            _shared_sync_csv_read_bytes(arquivo.read())
        )
    registros = [
        {str(coluna): str(serie.get(coluna) or "") for coluna in atual_df.columns}
        for _indice, serie in atual_df.iterrows()
    ]
    alterados = _cadastro_propagar_referencias_fotos_preparadas(
        registros,
        preparadas,
    )
    if alterados:
        _shared_sync_write_csv_atomic(
            target_abs,
            pd.DataFrame(registros, columns=list(atual_df.columns)).fillna(""),
        )
    return alterados


_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL = "cadastro_fotos_config.json"
_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_MAX_BYTES = 256 * 1024


class _SharedSyncCadastroPhotoConfigDuplicada(ValueError):
    pass


def _shared_sync_cadastro_photo_config_json(data: bytes, label: str) -> Any:
    if len(data or b"") > _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_MAX_BYTES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos excede o limite permitido.",
                "file": label,
            },
        )

    def pares_sem_duplicata(pares: list[tuple[str, Any]]) -> dict[str, Any]:
        saida: dict[str, Any] = {}
        for chave, valor in pares:
            if chave in saida:
                raise _SharedSyncCadastroPhotoConfigDuplicada(chave)
            saida[chave] = valor
        return saida

    try:
        return json.loads(
            (data or b"").decode("utf-8-sig"),
            object_pairs_hook=pares_sem_duplicata,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _SharedSyncCadastroPhotoConfigDuplicada) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos recebida e invalida.",
                "file": label,
            },
        ) from exc


def _shared_sync_cadastro_photo_config_store_ids_locked(
    tenant_abs: str,
) -> tuple[set[str], set[str]]:
    """Read exact active/tombstoned store identities under LOJAS_CONFIG_LOCK."""

    from backend.services.cadastro_fotos import _cadastro_caminho_e_link

    lojas_path = _shared_sync_resolve_tenant_path(tenant_abs, "lojas_config.json")
    tombstones_path = _shared_sync_resolve_tenant_path(
        tenant_abs,
        "lojas_sync_tombstones.json",
    )
    try:
        if (
            not os.path.lexists(lojas_path)
            or _cadastro_caminho_e_link(lojas_path)
            or not os.path.isfile(lojas_path)
            or os.path.getsize(lojas_path) > 4 * 1024 * 1024
        ):
            raise ValueError("lojas_config_inseguro_ou_ausente")
        with open(lojas_path, "r", encoding="utf-8-sig") as arquivo:
            lojas = json.load(arquivo)
        if not isinstance(lojas, list):
            raise ValueError("lojas_config_invalido")
        atuais: set[str] = set()
        for loja in lojas:
            if not isinstance(loja, dict):
                raise ValueError("loja_invalida")
            store_id = loja.get("store_id")
            if not isinstance(store_id, str) or not store_id or store_id != store_id.strip():
                continue
            if store_id in atuais:
                raise ValueError("store_id_duplicado")
            atuais.add(store_id)

        tombstonados: set[str] = set()
        if os.path.lexists(tombstones_path):
            if (
                _cadastro_caminho_e_link(tombstones_path)
                or not os.path.isfile(tombstones_path)
                or os.path.getsize(tombstones_path) > 4 * 1024 * 1024
            ):
                raise ValueError("tombstones_inseguro")
            with open(tombstones_path, "r", encoding="utf-8-sig") as arquivo:
                tombstones = json.load(arquivo)
            if not isinstance(tombstones, list):
                raise ValueError("tombstones_invalidos")
            for item in tombstones:
                if not isinstance(item, dict):
                    raise ValueError("tombstone_invalido")
                store_id = item.get("store_id")
                if not isinstance(store_id, str) or not store_id:
                    continue
                if (
                    str(item.get("type") or "").strip().casefold() == "store"
                    or str(item.get("key") or "").strip().casefold().startswith("store:")
                ):
                    tombstonados.add(store_id)
    except HTTPException:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_store_identity_invalid",
                "message": (
                    "As lojas precisam ser sincronizadas e validadas antes "
                    "da configuracao de fotos."
                ),
            },
        ) from exc
    return atuais, tombstonados


def _shared_sync_cadastro_photo_config_parse_locked(
    tenant_abs: str,
    data: bytes,
    *,
    label: str = _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL,
) -> dict[str, Any]:
    bruto = _shared_sync_cadastro_photo_config_json(data, label)
    chaves_topo = {"schema", "strict_store_scope", "shared_groups"}
    if not isinstance(bruto, dict) or set(bruto) != chaves_topo:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos possui campos invalidos.",
                "file": label,
            },
        )
    if bruto.get("schema") != "jk.cadastro.fotos.v1" or not isinstance(
        bruto.get("strict_store_scope"), bool
    ) or not isinstance(bruto.get("shared_groups"), list):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_invalid",
                "message": "A configuracao de fotos possui schema ou tipos invalidos.",
                "file": label,
            },
        )

    usados: set[str] = set()
    group_ids: set[str] = set()
    grupos: list[dict[str, Any]] = []
    for grupo in bruto["shared_groups"]:
        if not isinstance(grupo, dict) or set(grupo) != {"group_id", "store_ids"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cadastro_photo_config_invalid",
                    "message": "Um grupo de fotos possui campos invalidos.",
                    "file": label,
                },
            )
        group_id = grupo.get("group_id")
        store_ids = grupo.get("store_ids")
        if (
            not isinstance(group_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", group_id)
            or group_id in group_ids
            or not isinstance(store_ids, list)
            or len(store_ids) < 2
            or any(
                not isinstance(store_id, str)
                or not store_id
                or store_id != store_id.strip()
                for store_id in store_ids
            )
            or len(set(store_ids)) != len(store_ids)
            or any(store_id in usados for store_id in store_ids)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cadastro_photo_config_invalid",
                    "message": "Um grupo de fotos e invalido ou ambiguo.",
                    "file": label,
                },
            )
        group_ids.add(group_id)
        usados.update(store_ids)
        grupos.append(
            {
                "group_id": group_id,
                "store_ids": sorted(store_ids),
            }
        )

    atuais, tombstonados = _shared_sync_cadastro_photo_config_store_ids_locked(
        tenant_abs
    )
    ausentes = usados - atuais
    removidos = usados & tombstonados
    if ausentes or removidos:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_store_identity_invalid",
                "message": (
                    "A configuracao de fotos referencia loja inexistente, "
                    "removida ou tombstonada."
                ),
                "store_ids_ausentes": sorted(ausentes),
                "store_ids_tombstonados": sorted(removidos),
            },
        )
    return {
        "schema": "jk.cadastro.fotos.v1",
        "strict_store_scope": bruto["strict_store_scope"],
        "shared_groups": sorted(grupos, key=lambda item: item["group_id"]),
    }


def _shared_sync_cadastro_photo_config_bytes(config: dict[str, Any]) -> bytes:
    return (
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _shared_sync_cadastro_photo_config_preparar_locked(
    tenant_abs: str,
    remoto_bytes: bytes | None,
) -> dict[str, Any]:
    """Validate write-once semantics before any target or backup mutation."""

    from backend.services.cadastro_fotos import _cadastro_caminho_e_link

    try:
        target = _shared_sync_resolve_tenant_path(
            tenant_abs,
            _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL,
        )
    except HTTPException as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_unsafe",
                "message": "O destino local da configuracao de fotos e inseguro.",
            },
        ) from exc
    if os.path.lexists(target) and (
        _cadastro_caminho_e_link(target) or not os.path.isfile(target)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_unsafe",
                "message": "O destino local da configuracao de fotos e inseguro.",
            },
        )

    remoto = (
        _shared_sync_cadastro_photo_config_parse_locked(
            tenant_abs,
            remoto_bytes,
        )
        if remoto_bytes is not None
        else None
    )
    local = None
    if os.path.lexists(target):
        try:
            if os.path.getsize(target) > _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_MAX_BYTES:
                raise ValueError("config_local_excede_limite")
            with open(target, "rb") as arquivo:
                local_bytes = arquivo.read(
                    _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_MAX_BYTES + 1
                )
            local = _shared_sync_cadastro_photo_config_parse_locked(
                tenant_abs,
                local_bytes,
                label="cadastro_fotos_config.json local",
            )
        except HTTPException:
            raise
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cadastro_photo_config_unsafe",
                    "message": "A configuracao local de fotos e insegura.",
                },
            ) from exc
    if remoto is None:
        return {"target": target, "action": "keep" if local is not None else "missing"}
    if local is not None and local != remoto:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_conflict",
                "message": "A configuracao local de fotos diverge do compartilhamento.",
            },
        )
    return {
        "target": target,
        "action": "keep" if local is not None else "create",
        "config": remoto,
        "data": _shared_sync_cadastro_photo_config_bytes(remoto),
    }


def _shared_sync_aplicar_cadastro_lojas_fotos_transacional(
    client_id: str,
    scope: str,
    tenant_abs: str,
    backup_dir: str,
    rel: str,
    data: bytes,
    target_abs: str,
    store_photo_fontes: list[tuple[str, bytes]],
    photo_config_data: bytes | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Apply config, CSV, fan-out and photo bytes as one reversible unit."""

    from backend.services.cadastro_lojas_produtos import (
        _capturar_estados_arquivos,
        _rollback_arquivos,
    )
    from backend.services.path_coordination import path_locks_for

    config = _shared_sync_cadastro_photo_config_preparar_locked(
        tenant_abs,
        photo_config_data,
    )
    if config["action"] == "missing":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cadastro_photo_config_required",
                "message": (
                    "A configuracao de fotos precisa acompanhar o primeiro "
                    "cadastro por loja deste peer."
                ),
            },
        )
    config_target = str(config["target"])
    with path_locks_for([config_target]):
        estados = _capturar_estados_arquivos([config_target, target_abs])
        escritos: list[str] = []
        try:
            if config["action"] == "create":
                _shared_sync_atomic_write(config_target, config["data"])
                escritos.append(_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL)

            # Every helper below reads the local config.  It must observe the
            # validated write-once value while the same rollback scope is live.
            _shared_sync_validar_conflitos_fotos_grupo_remoto(
                client_id,
                tenant_abs,
                data,
            )
            photo_plan = _shared_sync_planejar_fotos_grupo_compartilhado(
                client_id,
                tenant_abs,
                target_abs,
                data,
                store_photo_fontes,
            )
            caminhos_fotos = _shared_sync_caminhos_transacao_fotos_store(
                client_id,
                tenant_abs,
                list(photo_plan.get("lock_fontes") or store_photo_fontes),
            )
            with path_locks_for(caminhos_fotos):
                estados.update(_capturar_estados_arquivos(caminhos_fotos))
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                merge_info = _shared_sync_merge_cadastro_lojas_versioned(
                    target_abs,
                    data,
                    client_id,
                    validar_store_ids=True,
                )

                photo_decisions = dict(merge_info.get("photo_decisions") or {})
                _shared_sync_exigir_bytes_fotos_vencedoras(
                    tenant_abs,
                    store_photo_fontes,
                    photo_decisions,
                )
                fontes_expandidas, photo_decisions = (
                    _shared_sync_expandir_fotos_grupo_compartilhado(
                        store_photo_fontes,
                        photo_decisions,
                        photo_plan,
                    )
                )
                referencias_atualizadas = _shared_sync_propagar_referencias_fotos_grupo(
                    client_id,
                    target_abs,
                    data,
                    fontes_expandidas,
                    photo_decisions,
                )
                if referencias_atualizadas:
                    merge_info["merged"] = True
                    merge_info["photo_rows_updated"] = referencias_atualizadas
                if merge_info.get("merged"):
                    escritos.append(rel)
                for photo_rel, photo_data in fontes_expandidas:
                    decision = str(
                        photo_decisions.get(
                            _shared_sync_cadastro_store_photo_key(scope, photo_rel),
                            "local",
                        )
                        or "local"
                    )
                    if decision not in {"remote", "tie"}:
                        continue
                    photo_target = _shared_sync_resolve_tenant_path(tenant_abs, photo_rel)
                    if _shared_sync_cadastro_store_photo_should_write(
                        photo_target,
                        photo_data,
                        decision,
                    ):
                        _shared_sync_backup_target(
                            tenant_abs,
                            backup_dir,
                            photo_rel,
                            photo_target,
                        )
                        _shared_sync_atomic_write(photo_target, photo_data)
                        escritos.append(photo_rel)
                    _shared_sync_remover_variantes_obsoletas_foto_store(
                        tenant_abs,
                        backup_dir,
                        photo_rel,
                    )
                return merge_info, escritos
        except BaseException:
            _rollback_arquivos(estados)
            raise


def _shared_sync_aplicar_config_fotos_write_once(
    tenant_abs: str,
    photo_config_data: bytes,
) -> list[str]:
    """Install a config-only snapshot without ever overwriting divergence."""

    from backend.services.cadastro_lojas_produtos import (
        _capturar_estados_arquivos,
        _rollback_arquivos,
    )
    from backend.services.path_coordination import path_locks_for

    config = _shared_sync_cadastro_photo_config_preparar_locked(
        tenant_abs,
        photo_config_data,
    )
    target = str(config["target"])
    if config["action"] == "keep":
        return []
    with path_locks_for([target]):
        estados = _capturar_estados_arquivos([target])
        try:
            _shared_sync_atomic_write(target, config["data"])
            return [_SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL]
        except BaseException:
            _rollback_arquivos(estados)
            raise


@contextmanager
def _shared_sync_bloquear_transicao_fotos_tenant(tenant_abs: str):
    from backend.services.cadastro_fotos_coordenacao import (
        CadastroFotosCoordenacaoErro,
        bloquear_transicao_fotos_tenant,
    )

    try:
        with bloquear_transicao_fotos_tenant(tenant_abs, timeout_seconds=10):
            yield
    except CadastroFotosCoordenacaoErro as exc:
        indisponivel = exc.code != "locked"
        raise HTTPException(
            status_code=409,
            detail={
                "code": (
                    "cadastro_photo_transition_unavailable"
                    if indisponivel
                    else "cadastro_photo_transition_busy"
                ),
                "message": (
                    "Nao foi possivel coordenar as fotos deste cliente."
                    if indisponivel
                    else "As fotos deste cliente estao sendo atualizadas."
                ),
            },
        ) from exc


def _shared_sync_legacy_root_pertence_ao_cliente(
    integracoes_service: Any,
    client_id: str,
    legacy_bytes: bytes,
) -> bool:
    """Confirma ownership antes de mesclar o antigo arquivo global em um tenant."""
    return bool(
        integracoes_service._integracoes_lojas_bytes_pertencem_ao_cliente(
            client_id,
            legacy_bytes,
        )
    )


def _shared_sync_aplicar_lojas_integracoes(
    client_id: str,
    fontes: list[tuple[str, bytes]],
    tenant_abs: str,
    backup_dir: str,
    *,
    add_only: bool = False,
    base_lojas_bytes: Optional[bytes] = None,
    base_integracoes_bytes: Optional[bytes] = None,
    base_tombstones_bytes: Optional[bytes] = None,
    strict_oauth_conflicts: bool = False,
) -> dict:
    """Mescla e grava o escopo inteiro sem expor estado parcialmente aplicado."""
    canonical_order = (
        "lojas_config.json",
        "integracoes.json",
        "lojas_sync_tombstones.json",
    )
    por_rel = {rel: data for rel, data in fontes}
    if set(por_rel) - set(canonical_order):
        raise HTTPException(
            status_code=502,
            detail="O snapshot remoto de Lojas e integracoes contem arquivo inesperado.",
        )
    if "lojas_config.json" not in por_rel:
        raise HTTPException(
            status_code=502,
            detail="O snapshot remoto de Lojas e integracoes nao contem lojas_config.json.",
        )

    from backend.services import integracoes as integracoes_service
    from backend.services.shared_sync_merge_integracoes import (
        _shared_sync_merge_integracoes_legacy_bytes,
        _shared_sync_merge_lojas_integracoes_bytes,
        _shared_sync_recuperar_backup_lojas_integracoes_bytes,
        _shared_sync_merge_tombstones_integracoes_bytes,
    )

    try:
        lojas_remotas = json.loads(
            por_rel["lojas_config.json"].decode("utf-8-sig")
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"lojas_config.json remoto invalido: {exc}",
        ) from exc
    if not isinstance(lojas_remotas, list):
        raise HTTPException(
            status_code=502,
            detail="lojas_config.json remoto nao contem uma lista.",
        )

    rels = [rel for rel in canonical_order if rel in por_rel]
    targets = {
        rel: _shared_sync_resolve_tenant_path(tenant_abs, rel)
        for rel in rels
    }
    preimages: dict[str, Optional[bytes]] = {}
    prepared: dict[str, bytes] = {}
    recovered_local_lojas: Optional[bytes] = None
    legacy_recovery_bytes: Optional[bytes] = None
    legacy_lojas_path = str(integracoes_service.ARQUIVO_LOJAS or "").strip()
    tenant_lojas_path = targets["lojas_config.json"]
    immediate_backup_path = f"{tenant_lojas_path}.bak"
    immediate_backup_existed = False
    immediate_backup_preimage: Optional[bytes] = None
    legacy_distinto = bool(
        legacy_lojas_path
        and os.path.abspath(legacy_lojas_path) != os.path.abspath(tenant_lojas_path)
    )
    legacy_scope_permitido = bool(
        integracoes_service._integracoes_pode_criar_lojas_legadas(client_id)
    )

    with integracoes_service._LOJAS_CONFIG_LOCK:
        # Uma operacao local interrompida e causalmente anterior a este pull.
        # Conclua seu journal antes de capturar preimages ou o novo merge
        # poderia sobrescreve-lo e ressuscitar a conta que ele removia.
        integracoes_service._integracoes_recuperar_transacao_pendente(
            client_id
        )
        # Materialize primeiro a migracao do antigo info/lojas_config.json e a
        # incorporacao de integracoes.json. Se o pull criasse o arquivo tenant
        # antes disso, carregar_lojas deixaria de migrar as contas preexistentes
        # e elas pareceriam ter sido apagadas no upgrade.
        legacy_pending = bool(
            legacy_scope_permitido
            and
            legacy_distinto
            and os.path.exists(legacy_lojas_path)
            and not os.path.exists(tenant_lojas_path)
        )
        if legacy_pending:
            with open(legacy_lojas_path, "rb") as legacy_file:
                legacy_pending_bytes = legacy_file.read()
            if not _shared_sync_legacy_root_pertence_ao_cliente(
                integracoes_service,
                client_id,
                legacy_pending_bytes,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Existe uma configuracao global antiga de lojas sem vinculo "
                        "comprovado com este cliente. A sincronizacao foi cancelada "
                        "para impedir mistura de contas; recupere ou migre esse "
                        "arquivo explicitamente."
                    ),
                )
        # Recupera tambem maquinas que ja sofreram o bug antigo, mas somente
        # quando o arquivo global pode ser atribuido deterministicamente a este
        # tenant. A coexistencia, sozinha, nao prova ownership e poderia vazar
        # lojas/credenciais entre clientes numa maquina reutilizada.
        if (
            legacy_scope_permitido
            and
            legacy_distinto
            and os.path.exists(legacy_lojas_path)
            and os.path.exists(tenant_lojas_path)
        ):
            with open(legacy_lojas_path, "rb") as legacy_file:
                legacy_bytes = legacy_file.read()
            if not _shared_sync_legacy_root_pertence_ao_cliente(
                integracoes_service,
                client_id,
                legacy_bytes,
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Existe uma configuracao global antiga de lojas sem vinculo "
                        "comprovado com este cliente. A sincronizacao foi cancelada "
                        "para impedir mistura de contas; recupere ou migre esse "
                        "arquivo explicitamente."
                    ),
                )
            legacy_recovery_bytes = legacy_bytes

        # O salvar_lojas atualiza o .bak imediato. Preserve a preimage antes de
        # qualquer migracao/escrita para que a ultima copia recuperavel nunca
        # seja apagada por um pull nem por um rollback incompleto.
        immediate_backup_existed = os.path.exists(immediate_backup_path)
        if immediate_backup_existed:
            with open(immediate_backup_path, "rb") as backup_file:
                immediate_backup_preimage = backup_file.read()
            _shared_sync_backup_target(
                tenant_abs,
                backup_dir,
                "lojas_config.json.bak",
                immediate_backup_path,
            )

        if legacy_pending:
            integracoes_service.carregar_lojas(client_id)
            if not os.path.exists(tenant_lojas_path):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Nao foi possivel migrar as lojas locais antigas para o cliente. "
                        "A sincronizacao foi cancelada para preservar as contas."
                    ),
                )

        # Todos os JSONs sao validados e mesclados antes da primeira escrita.
        # Isso evita que um arquivo posterior invalido deixe apenas parte do
        # snapshot aplicada.
        for rel in rels:
            target_abs = targets[rel]
            if os.path.exists(target_abs):
                with open(target_abs, "rb") as arquivo:
                    preimages[rel] = arquivo.read()
            else:
                preimages[rel] = None

        local_tombstones_bytes = preimages.get("lojas_sync_tombstones.json")
        if "lojas_sync_tombstones.json" not in preimages:
            local_tombstones_path = _shared_sync_resolve_tenant_path(
                tenant_abs,
                "lojas_sync_tombstones.json",
            )
            if os.path.exists(local_tombstones_path):
                with open(local_tombstones_path, "rb") as arquivo:
                    local_tombstones_bytes = arquivo.read()

        effective_tombstones_bytes = local_tombstones_bytes
        if "lojas_sync_tombstones.json" in por_rel:
            prepared["lojas_sync_tombstones.json"] = (
                _shared_sync_merge_tombstones_integracoes_bytes(
                    targets["lojas_sync_tombstones.json"],
                    por_rel["lojas_sync_tombstones.json"],
                    base_bytes=base_tombstones_bytes,
                )
            )
            effective_tombstones_bytes = prepared[
                "lojas_sync_tombstones.json"
            ]

        recovered_local_lojas = preimages.get("lojas_config.json")
        if immediate_backup_preimage:
            try:
                recovered_local_lojas = _shared_sync_recuperar_backup_lojas_integracoes_bytes(
                    recovered_local_lojas or b"[]",
                    immediate_backup_preimage,
                    client_id=client_id,
                    local_tombstones_bytes=effective_tombstones_bytes,
                )
            except HTTPException as backup_exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "O backup local de lojas diverge da configuracao atual. "
                        "A sincronizacao foi cancelada para preservar as contas."
                    ),
                ) from backup_exc
            except Exception as backup_exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "O backup local de lojas nao pôde ser validado. "
                        "A sincronizacao foi cancelada para preservar as contas."
                    ),
                ) from backup_exc

        if legacy_recovery_bytes is not None:
            recovered_local_lojas = _shared_sync_merge_lojas_integracoes_bytes(
                tenant_lojas_path,
                legacy_recovery_bytes,
                add_only=True,
                current_bytes=recovered_local_lojas,
                local_tombstones_bytes=effective_tombstones_bytes,
                strict_oauth_conflicts=True,
                client_id=client_id,
            )

        for rel in rels:
            target_abs = targets[rel]
            if rel == "lojas_config.json":
                prepared[rel] = _shared_sync_merge_lojas_integracoes_bytes(
                    target_abs,
                    por_rel[rel],
                    add_only=add_only,
                    current_bytes=recovered_local_lojas,
                    base_bytes=base_lojas_bytes,
                    local_tombstones_bytes=effective_tombstones_bytes,
                    incoming_tombstones_bytes=por_rel.get(
                        "lojas_sync_tombstones.json"
                    ),
                    base_tombstones_bytes=base_tombstones_bytes,
                    strict_oauth_conflicts=strict_oauth_conflicts,
                    client_id=client_id,
                )
            elif rel == "integracoes.json":
                prepared[rel] = _shared_sync_merge_integracoes_legacy_bytes(
                    target_abs,
                    por_rel[rel],
                    base_bytes=base_integracoes_bytes,
                    strict_oauth_conflicts=strict_oauth_conflicts,
                )
            else:
                if rel not in prepared:
                    prepared[rel] = _shared_sync_merge_tombstones_integracoes_bytes(
                        target_abs,
                        por_rel[rel],
                    )

        try:
            lojas = json.loads(prepared["lojas_config.json"].decode("utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Merge de lojas_config.json invalido: {exc}",
            ) from exc

        if not isinstance(lojas, list):
            raise HTTPException(
                status_code=502,
                detail="Merge de lojas_config.json nao contem uma lista.",
            )

        # Os backups sao materializados antes de qualquer alteracao. Se uma
        # escrita falhar, as preimages sao restauradas ainda sob o mesmo lock.
        for rel in rels:
            _shared_sync_backup_target(tenant_abs, backup_dir, rel, targets[rel])
        try:
            if "lojas_sync_tombstones.json" in prepared:
                tombstones_finais = json.loads(
                    prepared["lojas_sync_tombstones.json"].decode("utf-8-sig")
                )
            else:
                tombstones_finais = (
                    integracoes_service._integracoes_ler_tombstones_estrito(
                        client_id
                    )
                )
            integracoes_service._integracoes_commit_lojas_tombstones(
                client_id,
                lojas,
                tombstones_finais,
            )
            for rel in rels:
                if rel not in {
                    "lojas_config.json",
                    "lojas_sync_tombstones.json",
                }:
                    _shared_sync_atomic_write(targets[rel], prepared[rel])
            # Depois que todo o escopo foi confirmado, o backup imediato deve
            # refletir a uniao final. Manter nele a preimage reduzida faria uma
            # restauracao posterior perder novamente as contas recuperadas.
            integracoes_service._integracoes_espelhar_backup_final_seguro(
                client_id,
                tenant_lojas_path,
                lojas,
            )
        except Exception as exc:
            rollback_errors = []
            for rel in reversed(rels):
                target_abs = targets[rel]
                try:
                    original = preimages[rel]
                    if original is None:
                        if os.path.exists(target_abs):
                            os.remove(target_abs)
                    else:
                        _shared_sync_atomic_write(target_abs, original)
                except Exception as rollback_exc:
                    rollback_errors.append(type(rollback_exc).__name__)
            try:
                if immediate_backup_existed:
                    _shared_sync_atomic_write(
                        immediate_backup_path,
                        immediate_backup_preimage or b"",
                    )
                elif os.path.exists(immediate_backup_path):
                    os.remove(immediate_backup_path)
            except Exception as rollback_exc:
                rollback_errors.append(type(rollback_exc).__name__)
            try:
                integracoes_service._integracoes_abortar_transacao_pendente(
                    client_id
                )
            except Exception as rollback_exc:
                rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                logger.error(
                    "[SHARED-SYNC] Falha ao restaurar escopo lojas_integracoes: %s",
                    ",".join(rollback_errors),
                )
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Falha ao aplicar Lojas e integracoes e ao restaurar o estado anterior. "
                        "Use o backup local criado para esta operacao."
                    ),
                ) from exc
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=500,
                detail=(
                    "Falha ao aplicar Lojas e integracoes; o estado anterior foi restaurado."
                ),
            ) from exc

        if legacy_recovery_bytes is not None and os.path.exists(legacy_lojas_path):
            try:
                os.makedirs(backup_dir, exist_ok=True)
                shutil.move(
                    legacy_lojas_path,
                    os.path.join(backup_dir, "legacy_root_lojas_config.json"),
                )
            except Exception as archive_exc:
                logger.warning(
                    "[SHARED-SYNC] Lojas legadas foram recuperadas, mas o arquivo "
                    "global nao pôde ser arquivado: %s",
                    archive_exc,
                )

    _shared_sync_prune_local_backups(tenant_abs)
    return {
        "file_count": len(rels),
        "files": rels,
        "backup_dir": backup_dir,
        "stores_count": len(lojas),
        "snapshot_stores_count": len(lojas_remotas),
    }


def _shared_sync_aplicar_pacote(
    client_id: str,
    scope: str,
    bundle: bytes,
    username: str = "",
    scope_config: Optional[dict] = None,
) -> dict:
    tenant_path = get_tenant_path(client_id)
    tenant_abs = os.path.abspath(tenant_path)
    tenant_real = os.path.realpath(tenant_abs)
    if os.path.normcase(os.path.normpath(tenant_abs)) != os.path.normcase(
        os.path.normpath(tenant_real)
    ):
        from backend.services.cadastro_fotos import (
            _cadastro_tenant_path_fotos_seguro,
        )

        tenant_confiavel = _cadastro_tenant_path_fotos_seguro(client_id)
        if (
            not tenant_confiavel
            or os.path.normcase(os.path.normpath(tenant_confiavel))
            != os.path.normcase(os.path.normpath(tenant_real))
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "shared_sync_tenant_alias_untrusted",
                    "message": "O alias local do cliente nao esta autorizado.",
                },
            )
        tenant_abs = tenant_confiavel
    backup_dir = os.path.join(
        tenant_abs,
        "_shared_sync_backups",
        f"{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
    )
    escritos = []
    conflicts = 0
    lojas_aplicadas: Optional[int] = None
    lojas_snapshot: Optional[int] = None
    user_scoped_fontes: list[tuple[str, bytes]] = []
    user_share_fontes: list[tuple[str, bytes]] = []
    legacy_favoritos_fontes: list[tuple[str, bytes]] = []
    user_share = bool((scope_config or {}).get("user_share"))
    share_between_users = bool((scope_config or {}).get("share_between_users")) and bool((SHARED_SYNC_SCOPES.get(scope) or {}).get("user_scoped"))
    _manifest, fontes = _shared_sync_read_validated_bundle(bundle, scope)

    if scope == "lojas_integracoes":
        base_lojas_bytes = None
        base_integracoes_bytes = None
        base_tombstones_bytes = None
        base_bundle = (scope_config or {}).get("base_bundle")
        if isinstance(base_bundle, bytes) and base_bundle:
            _base_manifest, base_fontes = _shared_sync_read_validated_bundle(
                base_bundle,
                scope,
            )
            base_lojas_bytes = next(
                (
                    data
                    for rel, data in base_fontes
                    if rel == "lojas_config.json"
                ),
                None,
            )
            base_integracoes_bytes = next(
                (
                    data
                    for rel, data in base_fontes
                    if rel == "integracoes.json"
                ),
                None,
            )
            base_tombstones_bytes = next(
                (
                    data
                    for rel, data in base_fontes
                    if rel == "lojas_sync_tombstones.json"
                ),
                None,
            )
        return _shared_sync_aplicar_lojas_integracoes(
            client_id,
            fontes,
            tenant_abs,
            backup_dir,
            # Ausencia em outra maquina nao representa exclusao; somente um
            # tombstone explicito pode remover a identidade da loja.
            add_only=True,
            base_lojas_bytes=base_lojas_bytes,
            base_integracoes_bytes=base_integracoes_bytes,
            base_tombstones_bytes=base_tombstones_bytes,
            strict_oauth_conflicts=bool(
                (scope_config or {}).get("strict_oauth_conflicts")
            ),
        )

    if scope == "vendas":
        vendas_dbs = [(rel, data) for rel, data in fontes if _shared_sync_vendas_history_db(rel)]
        if vendas_dbs:
            merged = _shared_sync_apply_vendas_dbs_add_only(vendas_dbs, tenant_abs, backup_dir)
            escritos.extend(merged.get("files") or [])
        fontes = [(rel, data) for rel, data in fontes if not _shared_sync_vendas_history_db(rel)]

    store_photo_fontes = [
        (rel, data)
        for rel, data in fontes
        if _shared_sync_cadastro_store_photo_key(scope, rel)
    ]
    photo_config_fontes = [
        (rel, data)
        for rel, data in fontes
        if scope == "cadastro"
        and str(rel or "").strip().replace("\\", "/").casefold()
        == _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL.casefold()
    ]
    cadastro_lojas_fontes = [
        (rel, data)
        for rel, data in fontes
        if _shared_sync_is_cadastro_lojas_csv(scope, rel)
    ]
    if len(photo_config_fontes) > 1 or len(cadastro_lojas_fontes) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_cadastro_snapshot_ambiguous",
                "message": "O snapshot de Cadastro contem arquivos canonicos duplicados.",
            },
        )
    if store_photo_fontes and len(cadastro_lojas_fontes) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "shared_sync_store_photo_row_required",
                "message": (
                    "Shared Sync bloqueado: fotos por loja exigem exatamente um "
                    "cadastro_produtos_lojas.csv no mesmo snapshot."
                ),
            },
        )
    photo_config_data = photo_config_fontes[0][1] if photo_config_fontes else None
    if cadastro_lojas_fontes:
        rel, data = cadastro_lojas_fontes[0]
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
        # The outer lock intentionally spans the CSV merge and every selected
        # store photo write.  Cadastro CRUD holds this same per-path RLock while
        # committing its row and image, preventing a newer local edit from
        # landing between the merge decision and the corresponding photo.
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            merge_info, escritos_cadastro = (
                _shared_sync_aplicar_cadastro_lojas_fotos_transacional(
                    client_id,
                    scope,
                    tenant_abs,
                    backup_dir,
                    rel,
                    data,
                    target_abs,
                    store_photo_fontes,
                    photo_config_data,
                )
            )
            conflicts += int(merge_info.get("conflicts") or 0)
            escritos.extend(escritos_cadastro)
    elif photo_config_data is not None:
        # Even a config-only delta must observe the same lock order as the
        # combined Cadastro transaction: lojas -> catalog/cost -> transition ->
        # config path.  The parser then validates every exact store identity.
        with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
            escritos.extend(
                _shared_sync_aplicar_config_fotos_write_once(
                    tenant_abs,
                    photo_config_data,
                )
            )

    for rel, data in fontes:
        if _shared_sync_is_cadastro_lojas_csv(scope, rel):
            continue
        if (
            scope == "cadastro"
            and str(rel or "").strip().replace("\\", "/").casefold()
            == _SHARED_SYNC_CADASTRO_PHOTO_CONFIG_REL.casefold()
        ):
            continue
        if _shared_sync_cadastro_store_photo_key(scope, rel):
            # Store-scoped photos are never generic files: without a matching
            # versioned product row in this bundle there is no safe winner.
            continue
        if _shared_sync_is_cadastro_custos_csv(scope, rel):
            target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                merge_info = _shared_sync_merge_cadastro_custos_versioned(
                    client_id,
                    target_abs,
                    data,
                    validar_store_ids=True,
                )
            conflicts += int(merge_info.get("conflicts") or 0)
            if merge_info.get("merged"):
                escritos.append(rel)
            continue
        if (
            scope == "favoritos_historico"
            and not user_share
            and not share_between_users
            and _shared_sync_favoritos_historico_legacy_json_username(rel)
        ):
            legacy_favoritos_fontes.append((rel, data))
            continue
        if user_share:
            user_share_fontes.append((rel, data))
            continue
        if share_between_users:
            user_scoped_fontes.append((rel, data))
            continue
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, rel)
        # Keep the backup and authoritative replace in one per-path critical
        # section.  In particular, produtos_compilado.csv now serializes with
        # the stock/NCM read-modify-write cycle using the same canonical lock.
        rel_lower = str(rel or "").casefold()
        legacy_photo_sku = _shared_sync_legacy_photo_sku(rel)
        if rel_lower == "cadastro_produtos.csv":
            with _shared_sync_guard_legacy_cadastro_csv_locked(
                client_id,
                target_abs,
                data,
            ):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                _shared_sync_atomic_write(target_abs, data)
        elif legacy_photo_sku:
            from backend.services import integracoes

            with integracoes._LOJAS_CONFIG_LOCK:
                with _shared_sync_guard_legacy_photo_locked(
                    client_id,
                    tenant_abs,
                    target_abs,
                    rel,
                ):
                    _shared_sync_aplicar_foto_legada_atomica(
                        client_id,
                        tenant_abs,
                        backup_dir,
                        rel,
                        data,
                    )
        elif rel_lower.endswith(".csv"):
            with _shared_sync_bloquear_writer_store_id(client_id, tenant_abs):
                _shared_sync_validar_store_ids_csv_remoto_locked(
                    tenant_abs,
                    data,
                    rel,
                    exigir_store_id=(
                        rel_lower == "produtos_compilado.csv"
                        and scope in {"cadastro", "vendas"}
                    ),
                )
                if (
                    rel_lower == "produtos_compilado.csv"
                    and scope in {"cadastro", "vendas"}
                ):
                    from backend.services.shared_sync_merge_sqlite import (
                        _shared_sync_exigir_produtos_compilado_sem_fotos_locais_locked,
                    )

                    _shared_sync_exigir_produtos_compilado_sem_fotos_locais_locked(
                        client_id,
                        tenant_abs,
                        data,
                        rel,
                    )
                with _shared_sync_path_lock_for(target_abs):
                    _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                    _shared_sync_atomic_write(target_abs, data)
        else:
            with _shared_sync_path_lock_for(target_abs):
                _shared_sync_backup_target(tenant_abs, backup_dir, rel, target_abs)
                _shared_sync_atomic_write(target_abs, data)
        escritos.append(rel)
    for rel, data in legacy_favoritos_fontes:
        destino_username = _shared_sync_favoritos_historico_legacy_json_username(rel) or username
        target_rel = _shared_sync_target_rel_usuario(scope, destino_username, rel)
        target_abs = _shared_sync_resolve_tenant_path(tenant_abs, target_rel)
        _shared_sync_backup_target(tenant_abs, backup_dir, target_rel, target_abs)
        _shared_sync_merge_historico_usuario(client_id, destino_username, [(rel, data)])
        escritos.append(target_rel)
    if user_share:
        resultado_share = _shared_sync_aplicar_user_share_add_only(client_id, scope, username, user_share_fontes, tenant_abs, backup_dir)
        escritos.extend(resultado_share.get("files") or [])
    if share_between_users:
        resultado_share = _shared_sync_aplicar_user_scoped_share(client_id, scope, username, user_scoped_fontes, tenant_abs, backup_dir)
        escritos.extend(resultado_share.get("files") or [])
    if escritos:
        _shared_sync_prune_local_backups(tenant_abs)
    return {
        "file_count": len(escritos),
        "files": escritos[:250],
        "backup_dir": backup_dir if escritos else "",
        "stores_count": lojas_aplicadas if lojas_aplicadas is not None else 0,
        "snapshot_stores_count": lojas_snapshot if lojas_snapshot is not None else 0,
        "conflicts": conflicts,
    }

def _shared_sync_pull_scope(client_id: str, scope: str, sessao: dict, machine_id: str = "", scope_config: Optional[dict] = None) -> dict:
    with _shared_sync_pull_lock(
        "destination",
        client_id,
        scope,
    ):
        return _shared_sync_pull_scope_serialized(
            client_id,
            scope,
            sessao,
            machine_id,
            scope_config,
        )


def _shared_sync_pull_scope_serialized(client_id: str, scope: str, sessao: dict, machine_id: str = "", scope_config: Optional[dict] = None) -> dict:
    bundle_id = _shared_sync_doc_id(client_id, scope)
    meta = _shared_sync_remote_meta_by_id(bundle_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Nenhum backup remoto encontrado para esse compartilhamento.")
    initial_remote_fingerprint = _shared_sync_remote_fingerprint_from_meta(meta)
    if _shared_sync_pull_already_current(client_id, sessao.get("username") or "", scope, meta):
        return _shared_sync_pull_skip_payload(scope, meta)
    if scope == "lojas_integracoes":
        remoto = _shared_sync_obter_bundle_remoto_para_guard(bundle_id)
        if remoto is None:
            raise HTTPException(
                status_code=404,
                detail="Nenhum backup remoto encontrado para esse compartilhamento.",
            )
        bundle, meta = remoto
    else:
        bundle, meta = _shared_sync_obter_bundle_por_id(bundle_id, meta)
    if _shared_sync_remote_fingerprint_from_meta(meta) != initial_remote_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=(
                "O snapshot remoto mudou durante a importacao; "
                "confira novamente."
            ),
        )
    result = _shared_sync_aplicar_pacote(client_id, scope, bundle, sessao.get("username") or "", scope_config)
    _shared_sync_state_update(client_id, sessao.get("username") or "", scope, meta, "pull")
    return {
        "scope": scope,
        "success": True,
        "direction": "pull",
        "file_count": result.get("file_count") or 0,
        "conflicts": result.get("conflicts") or 0,
        "backup_dir": result.get("backup_dir") or "",
        "snapshot_hash": meta.get("snapshot_hash") or "",
        "remote_updated_at": meta.get("updated_at") or "",
        "remote_updated_by": meta.get("updated_by") or "",
        "remote_machine_id": meta.get("machine_id") or "",
    }

configure_shared_sync_apply_scope_runtime()

__all__ = [
    "configure_shared_sync_apply_scope_runtime",
    "_shared_sync_favoritos_historico_legacy_json_username",
    "_shared_sync_validate_sqlite_payload",
    "_shared_sync_read_validated_bundle",
    "_shared_sync_atomic_write",
    "_shared_sync_cadastro_store_photo_key",
    "_shared_sync_cadastro_store_photo_should_write",
    "_shared_sync_aplicar_cadastro_lojas_fotos_transacional",
    "_shared_sync_aplicar_pacote",
    "_shared_sync_pull_scope",
]
