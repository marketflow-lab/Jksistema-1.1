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
    backup_dir = os.path.join(
        tenant_abs,
        "_shared_sync_backups",
        f"{scope}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}",
    )
    escritos = []
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
            add_only=bool(user_share or share_between_users),
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

    for rel, data in fontes:
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
    "_shared_sync_aplicar_pacote",
    "_shared_sync_pull_scope",
]
