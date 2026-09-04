"""Compiled stock synchronization for Estoque."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
from fastapi import Depends, HTTPException

from backend.schemas.estoque import (
    EstoqueLancamentosSyncLoteRequest,
    EstoqueLancamentosSyncRequest,
    EstoquePreferenciasColunasRequest,
    EstoqueSyncRequest,
)
from backend.services import estoque_context, integracoes as integracoes_service
from backend.services.integracoes import carregar_lojas
from backend.services.path_coordination import path_lock_for
from backend.services.runtime_bridge import bind_runtime_globals


def _sync_context_names() -> None:
    for name in estoque_context.CONTEXT_EXPORTS:
        globals()[name] = getattr(estoque_context, name)


def configure_estoque_sync_runtime(runtime_module=None):
    runtime = estoque_context.configure_estoque_context(runtime_module)
    bind_runtime_globals(globals(), runtime)
    _sync_context_names()
    return runtime


configure_estoque_sync_runtime()

from backend.services.estoque_common import (
    _ESTOQUE_SYNC_STATE_LOCK,
    _cache_estoque_job_terminal,
    _criar_progresso,
    _estoque_log,
    _set_estoque_progresso,
)
from backend.services.estoque_historico import (
    _confirmar_evento_historico_estoque,
    _descartar_evento_pendente_estoque,
    _novo_event_id_estoque,
    _registrar_snapshot_historico_estoque,
)


_ESTOQUE_CSV_COLUNAS_CANONICAS = (
    "sku",
    "store_id",
    "loja_sync",
    "last_update",
    "id_bling",
    "nome_bling",
    "situacao_bling",
    "ncm_bling",
    "saldo_loja",
    "saldo_full",
)


class _EstoqueSyncCancelado(HTTPException):
    pass


class _EstoquePublicacaoInconclusiva(RuntimeError):
    pass


def _estoque_nome_loja_exato(valor: Any) -> str:
    return str(valor or "").strip()


def _estoque_nome_loja_chave_historica(valor: Any) -> str:
    return _estoque_nome_loja_exato(valor).casefold()


def _estoque_bling_valor(cfg: Any, *chaves: str) -> str:
    dados = cfg if isinstance(cfg, dict) else {}
    for chave in chaves:
        if chave not in dados:
            continue
        valor = str(dados.get(chave) or "").strip()
        if valor:
            return valor
    return ""


def _estoque_bling_fingerprint(cfg: Any) -> tuple[str, ...]:
    """Compare Bling state in memory without exposing credentials in metadata."""

    return (
        _estoque_bling_valor(cfg, "id", "client_id"),
        _estoque_bling_valor(cfg, "secret", "client_secret"),
        _estoque_bling_valor(cfg, "access_token"),
        _estoque_bling_valor(cfg, "refresh_token"),
        _estoque_bling_valor(cfg, "connected"),
        _estoque_bling_valor(cfg, "oauth_invalid"),
        _estoque_bling_valor(cfg, "status"),
        _estoque_bling_valor(cfg, "_sync_version"),
    )


def _estoque_http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _estoque_erro_configuracao_alterada() -> HTTPException:
    return _estoque_http_error(
        409,
        "estoque_bling_config_changed",
        "A configuracao Bling da loja foi alterada durante a atualizacao; tente novamente.",
    )


def _estoque_snapshot_loja(loja: dict[str, Any]) -> dict[str, Any]:
    integracoes = loja.get("integracoes")
    integracoes = integracoes if isinstance(integracoes, dict) else {}
    return {
        "store_id": str(loja.get("store_id") or "").strip(),
        "nome": _estoque_nome_loja_exato(loja.get("nome")),
        "_bling_fingerprint": _estoque_bling_fingerprint(integracoes.get("bling")),
    }


def _snapshot_lojas_estoque(client_id: str) -> list[dict[str, Any]]:
    """Materialize the tenant store set once, preserving exact configured IDs."""

    lojas_raw = carregar_lojas(client_id) or []
    if not isinstance(lojas_raw, (list, tuple)):
        raise _estoque_http_error(
            409, "estoque_stores_invalid", "A configuracao de lojas do cliente e invalida."
        )

    snapshots: list[dict[str, Any]] = []
    ids_vistos: set[str] = set()
    for loja_raw in lojas_raw:
        if not isinstance(loja_raw, dict):
            raise _estoque_http_error(
                409, "estoque_stores_invalid", "A configuracao de lojas do cliente e invalida."
            )
        snapshot = _estoque_snapshot_loja(loja_raw)
        store_id = snapshot["store_id"]
        if not store_id:
            raise _estoque_http_error(
                409, "estoque_store_id_empty", "Existe uma loja sem store_id persistido."
            )
        if store_id in ids_vistos:
            raise _estoque_http_error(
                409,
                "estoque_store_id_duplicate",
                "Existem lojas com store_id duplicado na configuracao.",
            )
        ids_vistos.add(store_id)
        snapshots.append(snapshot)

    if not snapshots:
        raise _estoque_http_error(
            404, "estoque_stores_empty", "Nenhuma loja configurada para o cliente."
        )
    return snapshots


def _resolver_loja_estoque(
    client_id: str,
    *,
    store_id: str | None = None,
    loja_nome: str | None = None,
) -> dict[str, Any]:
    """Resolve one store without ever choosing an arbitrary homonym."""

    lojas = [
        dict(loja)
        for loja in (carregar_lojas(client_id) or [])
        if isinstance(loja, dict)
    ]
    store_id_alvo = str(store_id or "").strip()
    if store_id_alvo:
        correspondentes = [
            loja
            for loja in lojas
            if str(loja.get("store_id") or "").strip() == store_id_alvo
        ]
        if not correspondentes:
            raise HTTPException(status_code=404, detail="Loja nao encontrada para o cliente.")
        if len(correspondentes) != 1:
            raise HTTPException(status_code=409, detail="store_id duplicado na configuracao de lojas.")
        loja = correspondentes[0]
    else:
        nome_alvo = _estoque_nome_loja_exato(loja_nome)
        if not nome_alvo or nome_alvo == "__todas":
            raise HTTPException(status_code=400, detail="Selecione uma loja para sincronizar.")
        correspondentes = [
            loja
            for loja in lojas
            if _estoque_nome_loja_exato(loja.get("nome")) == nome_alvo
        ]
        if not correspondentes:
            raise HTTPException(status_code=404, detail="Loja nao encontrada para o cliente.")
        if len(correspondentes) != 1:
            raise HTTPException(
                status_code=409,
                detail="Nome de loja ambiguo; informe o store_id exato.",
            )
        loja = correspondentes[0]

    nome_resolvido = _estoque_nome_loja_exato(loja.get("nome"))
    nomes_iguais = [
        item
        for item in lojas
        if _estoque_nome_loja_chave_historica(item.get("nome"))
        == _estoque_nome_loja_chave_historica(nome_resolvido)
    ]
    resultado = dict(loja)
    resultado["store_id"] = str(resultado.get("store_id") or "").strip()
    resultado["nome"] = nome_resolvido
    resultado["_nome_exato_unico"] = bool(nome_resolvido) and len(nomes_iguais) == 1
    if not resultado["store_id"]:
        raise HTTPException(status_code=409, detail="Loja sem store_id persistido.")
    return resultado


def _estoque_criar_sync_meta(
    job_id: str,
    snapshots: list[dict[str, Any]],
    *,
    todas_lojas: bool,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    unico = snapshots[0] if not todas_lojas else {}
    scope = scope or (
        {"type": "all"}
        if todas_lojas
        else {"type": "store", "store_id": unico.get("store_id")}
    )
    return {
        "job_id": job_id,
        "scope": scope,
        "todas_lojas": bool(todas_lojas),
        "loja": "__todas" if todas_lojas else unico.get("nome"),
        "store_id": None if todas_lojas else unico.get("store_id"),
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "outcome": None,
        "requires_reconciliation": False,
        "total_lojas": len(snapshots),
        "indice_loja": 0,
        "loja_atual": None,
        "store_id_atual": None,
        "resultados": [
            {
                "store_id": item["store_id"],
                "loja": item["nome"],
                "status": "queued",
                "total": None,
                "event_id": None,
                "historico_registrado": None,
                "requires_reconciliation": False,
                "erro": None,
            }
            for item in snapshots
        ],
        "contagens": {
            "success": 0,
            "failed": 0,
            "uncertain": 0,
            "cancelled": 0,
            "skipped": 0,
        },
    }


def _estoque_resultado(meta: dict[str, Any], store_id: str) -> dict[str, Any]:
    for resultado in meta.get("resultados") or []:
        if resultado.get("store_id") == store_id:
            return resultado
    raise RuntimeError("Resultado de loja ausente no job de estoque.")


def _estoque_finalizar_job(
    client_id: str,
    job_id: str,
    outcome: str,
    *,
    etapa: str,
    lote: int,
    total: int,
    mensagem: str,
) -> dict[str, Any]:
    progresso_final = _criar_progresso(etapa, lote, total, 100, mensagem)
    with _ESTOQUE_SYNC_STATE_LOCK:
        meta = ESTOQUE_SYNC_META.get(client_id) or {}
        if meta.get("job_id") != job_id:
            raise RuntimeError("Job de estoque substituido durante a execucao.")
        resultados = meta.get("resultados") or []
        meta.update({
            "outcome": outcome,
            "finished_at": datetime.now().isoformat(),
            "indice_loja": len(resultados),
            "loja_atual": None,
            "store_id_atual": None,
            "contagens": {
                status: sum(1 for item in resultados if item.get("status") == status)
                for status in ("success", "failed", "uncertain", "cancelled", "skipped")
            },
        })
        _set_estoque_progresso(client_id, progresso_final)
        _cache_estoque_job_terminal(
            client_id,
            job_id,
            progress=progresso_final,
            logs=ESTOQUE_SYNC_LOGS.get(client_id, []),
            sync_meta=meta,
        )
    return {
        "success": outcome == "completed",
        "job_id": job_id,
        "outcome": outcome,
        "requires_reconciliation": bool(meta.get("requires_reconciliation")),
        "resultados": resultados,
    }


def _estoque_progresso_loja(
    client_id: str,
    data: dict[str, Any],
    *,
    indice_loja: int,
    total_lojas: int,
    gerenciado_por_job: bool,
) -> None:
    progresso = dict(data)
    percentual_loja = max(0, min(100, int(progresso.get("percentual") or 0)))
    total = max(1, int(total_lojas or 1))
    indice = max(1, min(total, int(indice_loja or 1)))
    percentual_total = int(round((((indice - 1) * 100) + percentual_loja) / total))
    if gerenciado_por_job and percentual_total >= 100:
        percentual_total = 99
    progresso.update({
        "percentual_loja": percentual_loja,
        "percentual": percentual_total,
        "indice_loja": indice,
        "total_lojas": total,
    })
    _set_estoque_progresso(client_id, progresso)


def _estoque_erro_seguro(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, _EstoquePublicacaoInconclusiva):
        return {
            "code": "estoque_publication_uncertain",
            "message": (
                "Nao foi possivel confirmar a publicacao do estoque. "
                "A atualizacao foi interrompida para reconciliacao segura."
            ),
        }
    if isinstance(exc, requests.RequestException):
        return {
            "code": "bling_connection_error",
            "message": "Falha de conexao com a API do Bling.",
        }
    if isinstance(exc, HTTPException):
        detalhe = exc.detail if isinstance(exc.detail, dict) else {}
        codigo_detalhe = str(detalhe.get("code") or "").strip()
        codigo, mensagem = {
            400: ("store_configuration_error", "Configuracao Bling incompleta."),
            401: ("bling_auth_error", "Autenticacao Bling precisa ser refeita."),
            404: ("store_not_found", "Loja nao encontrada no tenant."),
            409: ("store_configuration_changed", "Configuracao da loja alterada."),
            429: ("bling_rate_limited", "Consultas temporariamente limitadas pelo Bling."),
            502: ("bling_invalid_response", "Resposta invalida do Bling."),
            503: ("bling_unavailable", "Bling temporariamente indisponivel."),
        }.get(
            int(exc.status_code or 500),
            ("store_sync_error", "Nao foi possivel atualizar esta loja."),
        )
        if codigo_detalhe and codigo_detalhe.replace("_", "").isalnum():
            codigo = codigo_detalhe
        return {
            "code": codigo,
            "message": mensagem,
            "status_code": int(exc.status_code or 500),
        }
    return {
        "code": "unexpected_store_sync_error",
        "message": "Ocorreu um erro inesperado ao atualizar esta loja.",
    }


def _publicar_csv_estoque_apos_historico(
    client_id: str,
    loja_sync: str,
    registros: list[dict],
    df_saida: pd.DataFrame,
    arquivo_cliente: str,
    event_id: str,
    *,
    registrar_historico: bool = True,
) -> int:
    # Direct callers are serialized too.  The full stock read/merge path takes
    # this same RLock before calling us, so reentrancy avoids a nested deadlock.
    with path_lock_for(arquivo_cliente):
        return _publicar_csv_estoque_apos_historico_locked(
            client_id,
            loja_sync,
            registros,
            df_saida,
            arquivo_cliente,
            event_id,
            registrar_historico=registrar_historico,
        )


def _publicar_csv_estoque_apos_historico_locked(
    client_id: str,
    loja_sync: str,
    registros: list[dict],
    df_saida: pd.DataFrame,
    arquivo_cliente: str,
    event_id: str,
    *,
    registrar_historico: bool = True,
) -> int:
    diretorio = os.path.dirname(arquivo_cliente)
    nome_temporario = f".produtos_compilado.{event_id}.tmp"
    arquivo_temporario = os.path.join(diretorio, nome_temporario)
    publicado = False
    try:
        df_saida.to_csv(arquivo_temporario, index=False)
        csv_hash = ""
        if registrar_historico:
            with open(arquivo_temporario, "rb") as arquivo_csv:
                csv_hash = hashlib.sha256(arquivo_csv.read()).hexdigest()
            _registrar_snapshot_historico_estoque(
                client_id,
                loja_sync,
                registros,
                event_id=event_id,
                status="pending",
                csv_hash=csv_hash,
            )
        try:
            os.replace(arquivo_temporario, arquivo_cliente)
        except Exception:
            if registrar_historico:
                _descartar_evento_pendente_estoque(client_id, event_id)
            raise
        publicado = True
        if not registrar_historico:
            return 0
        return _confirmar_evento_historico_estoque(
            client_id,
            event_id,
            csv_hash=csv_hash,
        )
    except _EstoquePublicacaoInconclusiva:
        raise
    except Exception as exc:
        if publicado:
            raise _EstoquePublicacaoInconclusiva(
                "Falha posterior a publicacao do estoque compilado."
            ) from exc
        raise
    finally:
        if os.path.exists(arquivo_temporario):
            try:
                os.remove(arquivo_temporario)
            except OSError:
                pass


def _atualizar_produtos_compilados_loja(
    client_id: str,
    loja_sync: str,
    registros: list[dict],
    arquivo_cliente: str,
    event_id: str,
    store_id: str | None = None,
    loja_nome_unico: bool = True,
) -> int:
    """Atomically replace one store slice without losing concurrent writers."""

    with path_lock_for(arquivo_cliente):
        store_id_alvo = str(store_id or "").strip()
        loja_alvo = _estoque_nome_loja_exato(loja_sync)
        df_loja_atual = pd.DataFrame(registros).fillna("")
        for coluna in _ESTOQUE_CSV_COLUNAS_CANONICAS:
            if coluna not in df_loja_atual.columns:
                df_loja_atual[coluna] = ""
        colunas_loja = list(_ESTOQUE_CSV_COLUNAS_CANONICAS)
        colunas_loja.extend(
            coluna for coluna in df_loja_atual.columns if coluna not in colunas_loja
        )
        df_loja_atual = df_loja_atual[colunas_loja]
        if store_id_alvo:
            df_loja_atual["store_id"] = store_id_alvo
            df_loja_atual["loja_sync"] = loja_alvo
        df_existente = pd.DataFrame()
        if os.path.exists(arquivo_cliente):
            try:
                df_existente = pd.read_csv(
                    arquivo_cliente,
                    dtype=str,
                    keep_default_na=False,
                ).fillna("")
            except Exception as exc:
                logger.warning(
                    "[ESTOQUE] Arquivo compilado existente invalido; "
                    "atualizacao abortada: %s",
                    type(exc).__name__,
                )
                raise RuntimeError(
                    "Estoque compilado existente invalido; nenhuma alteracao foi publicada."
                ) from exc

        fatia_anterior = pd.DataFrame()
        if not df_existente.empty:
            if "store_id" not in df_existente.columns:
                df_existente["store_id"] = ""
            ids_existentes = df_existente["store_id"].astype(str).str.strip()
            mask_remover = pd.Series(False, index=df_existente.index)
            if store_id_alvo:
                mask_remover |= ids_existentes == store_id_alvo

                # A row without store_id is a legacy shadow.  It can only be
                # replaced by name when that name identifies one current store.
                if loja_nome_unico and loja_alvo and "loja_sync" in df_existente.columns:
                    nomes_existentes = df_existente["loja_sync"].astype(str).str.strip()
                    mask_remover |= ids_existentes.eq("") & nomes_existentes.eq(loja_alvo)
            elif loja_alvo and "loja_sync" in df_existente.columns:
                # Compatibility for old direct helper callers.  Production
                # requests are resolved to store_id before reaching this path.
                nomes_existentes = df_existente["loja_sync"].astype(str).str.strip()
                mask_remover |= ids_existentes.eq("") & nomes_existentes.eq(loja_alvo)

            fatia_anterior = df_existente.loc[mask_remover].copy()
            df_existente = df_existente.loc[~mask_remover].copy()

        # Stock refresh owns the columns it fetched.  Store-specific enrichment
        # written by other flows (for example CEST/monofasico) survives only
        # when the old slice has one unambiguous row for the exact same SKU.
        colunas_extras = [
            coluna for coluna in fatia_anterior.columns
            if coluna not in df_loja_atual.columns
        ]
        for coluna in colunas_extras:
            df_loja_atual[coluna] = ""
        if (
            not fatia_anterior.empty
            and not df_loja_atual.empty
            and "sku" in fatia_anterior.columns
            and "sku" in df_loja_atual.columns
        ):
            chaves_antigas = fatia_anterior["sku"].astype(str).str.strip()
            contagens = chaves_antigas.value_counts(dropna=False)
            indices_unicos = {
                chave: fatia_anterior.index[chaves_antigas.eq(chave)][0]
                for chave, quantidade in contagens.items()
                if chave and int(quantidade) == 1
            }
            for indice_novo, sku_novo in df_loja_atual["sku"].astype(str).str.strip().items():
                indice_antigo = indices_unicos.get(sku_novo)
                if indice_antigo is None:
                    continue
                for coluna in colunas_extras:
                    df_loja_atual.at[indice_novo, coluna] = fatia_anterior.at[indice_antigo, coluna]

        colunas_saida = list(df_existente.columns)
        for coluna in df_loja_atual.columns:
            if coluna not in colunas_saida:
                colunas_saida.append(coluna)
        for coluna in colunas_saida:
            if coluna not in df_existente.columns:
                df_existente[coluna] = ""
            if coluna not in df_loja_atual.columns:
                df_loja_atual[coluna] = ""
        df_saida = pd.concat(
            [df_existente[colunas_saida], df_loja_atual[colunas_saida]],
            ignore_index=True,
        )

        return _publicar_csv_estoque_apos_historico(
            client_id,
            loja_sync,
            registros,
            df_saida,
            arquivo_cliente,
            event_id,
            registrar_historico=bool(loja_nome_unico),
        )

def _estoque_verificar_cancelamento(client_id: str, job_id: str | None = None):
    with _ESTOQUE_SYNC_STATE_LOCK:
        flag = ESTOQUE_SYNC_CANCEL_FLAGS.get(client_id)
        if not flag:
            return
        alvo = ""
        if isinstance(flag, dict):
            alvo = str(flag.get("job_id") or "").strip()
        elif isinstance(flag, str):
            alvo = flag.strip()
        if alvo and (not job_id or alvo != job_id):
            if job_id:
                ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
            return
        ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
        raise _EstoqueSyncCancelado(
            status_code=409,
            detail="Sincronização de estoque cancelada pelo usuário.",
        )


def _estoque_solicitar_cancelamento_job(client_id: str, job_id: str) -> bool:
    """Signal cancellation only while the exact job still owns the tenant slot."""

    job_id_exato = str(job_id or "").strip()
    if not job_id_exato:
        return False
    with _ESTOQUE_SYNC_STATE_LOCK:
        meta = ESTOQUE_SYNC_META.get(client_id) or {}
        if (
            not ESTOQUE_SYNC_ACTIVE.get(client_id)
            or str(meta.get("job_id") or "").strip() != job_id_exato
            or bool(meta.get("outcome"))
        ):
            return False
        ESTOQUE_SYNC_CANCEL_FLAGS[client_id] = {"job_id": job_id_exato}
        return True


def _estoque_snapshots_requisicao(
    req: EstoqueSyncRequest,
    client_id: str,
) -> list[dict[str, Any]]:
    if bool(getattr(req, "todas_lojas", False)):
        return _snapshot_lojas_estoque(client_id)
    loja = _resolver_loja_estoque(
        client_id,
        store_id=getattr(req, "store_id", None),
        loja_nome=req.loja,
    )
    req.store_id = loja["store_id"]
    req.loja = loja["nome"]
    return [_estoque_snapshot_loja(loja)]


def _estoque_scope_requisicao(req: EstoqueSyncRequest) -> dict[str, Any]:
    if bool(req.todas_lojas):
        return {"type": "all"}
    store_id = str(req.store_id or "").strip()
    if store_id:
        return {"type": "store", "store_id": store_id}
    return {"type": "legacy_name", "loja": _estoque_nome_loja_exato(req.loja)}


def _estoque_resposta_job_ativo(
    client_id: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    meta = ESTOQUE_SYNC_META.get(client_id) or {}
    if meta.get("scope") != scope:
        raise _estoque_http_error(
            409,
            "estoque_sync_scope_conflict",
            "Ja existe uma atualizacao de estoque em outro escopo para este cliente.",
        )
    return {
        "started": False,
        "already_running": True,
        "job_id": meta.get("job_id"),
        "message": "Atualização de estoque já em andamento.",
    }


def _estoque_preparar_job(
    client_id: str,
    job_id: str,
    snapshots: list[dict[str, Any]],
    *,
    todas_lojas: bool,
    scope: dict[str, Any] | None = None,
    ativar: bool,
) -> None:
    with _ESTOQUE_SYNC_STATE_LOCK:
        ESTOQUE_SYNC_META[client_id] = _estoque_criar_sync_meta(
            job_id, snapshots, todas_lojas=todas_lojas, scope=scope
        )
        ESTOQUE_SYNC_LOGS[client_id] = []
        ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
        if ativar:
            ESTOQUE_SYNC_ACTIVE[client_id] = True


def _sincronizar_estoque_thread_worker(
    req: "EstoqueSyncRequest",
    client_id: str,
    snapshots: list[dict[str, Any]] | None = None,
    job_id: str | None = None,
):
    scope = _estoque_scope_requisicao(req)
    if snapshots is None:
        snapshots = _estoque_snapshots_requisicao(req, client_id)
    if not job_id:
        job_id = uuid.uuid4().hex
        _estoque_preparar_job(
            client_id,
            job_id,
            snapshots,
            todas_lojas=bool(req.todas_lojas),
            scope=scope,
            ativar=True,
        )
    try:
        asyncio.run(_sincronizar_estoque_job(req, client_id, snapshots, job_id))
    except Exception:
        logger.exception("[ESTOQUE] Falha inesperada no orquestrador de estoque.")
        with _ESTOQUE_SYNC_STATE_LOCK:
            meta = ESTOQUE_SYNC_META.get(client_id) or {}
            for resultado in meta.get("resultados") or []:
                if resultado.get("status") in {"queued", "running"}:
                    resultado.update(
                        status="failed",
                        erro={
                            "code": "estoque_job_error",
                            "message": "A atualizacao foi interrompida por um erro inesperado.",
                        },
                    )
        _estoque_finalizar_job(
            client_id,
            job_id,
            "failed",
            etapa="Erro",
            lote=0,
            total=len(snapshots),
            mensagem="A atualizacao de estoque foi interrompida.",
        )
    finally:
        with _ESTOQUE_SYNC_STATE_LOCK:
            meta = ESTOQUE_SYNC_META.get(client_id)
            if not isinstance(meta, dict) or meta.get("job_id") == job_id:
                ESTOQUE_SYNC_ACTIVE.pop(client_id, None)

async def sincronizar_estoque(req: EstoqueSyncRequest, client_id: str = Depends(get_tenant_id)):
    scope = _estoque_scope_requisicao(req)
    with _ESTOQUE_SYNC_STATE_LOCK:
        if ESTOQUE_SYNC_ACTIVE.get(client_id):
            return _estoque_resposta_job_ativo(client_id, scope)

    snapshots = _estoque_snapshots_requisicao(req, client_id)
    job_id = uuid.uuid4().hex
    with _ESTOQUE_SYNC_STATE_LOCK:
        if ESTOQUE_SYNC_ACTIVE.get(client_id):
            return _estoque_resposta_job_ativo(client_id, scope)
        _estoque_preparar_job(
            client_id,
            job_id,
            snapshots,
            todas_lojas=bool(req.todas_lojas),
            scope=scope,
            ativar=True,
        )
        _set_estoque_progresso(
            client_id,
            _criar_progresso(
                "Preparando",
                0,
                len(snapshots),
                0,
                "Solicitacao recebida. Iniciando atualizacao de estoque...",
            ),
        )

        t = threading.Thread(
            target=_sincronizar_estoque_thread_worker,
            args=(req, client_id, snapshots, job_id),
            daemon=True,
        )
        try:
            t.start()
        except Exception:
            ESTOQUE_SYNC_ACTIVE.pop(client_id, None)
            meta = ESTOQUE_SYNC_META[client_id]
            for resultado in meta["resultados"]:
                resultado.update(
                    status="skipped",
                    erro={
                        "code": "estoque_worker_start_failed",
                        "message": "Nao foi possivel iniciar a atualizacao de estoque.",
                    },
                )
            _estoque_finalizar_job(
                client_id,
                job_id,
                "failed",
                etapa="Erro",
                lote=0,
                total=len(snapshots),
                mensagem="Nao foi possivel iniciar a atualizacao de estoque.",
            )
            raise HTTPException(status_code=500, detail="Nao foi possivel iniciar a atualizacao de estoque.")

    return {
        "started": True,
        "job_id": job_id,
        "total_lojas": len(snapshots),
        "message": "Atualização de estoque iniciada em background.",
    }


async def _sincronizar_estoque_job(
    req: EstoqueSyncRequest,
    client_id: str,
    snapshots: list[dict[str, Any]],
    job_id: str,
) -> dict[str, Any]:
    with _ESTOQUE_SYNC_STATE_LOCK:
        meta = ESTOQUE_SYNC_META.get(client_id) or {}
        if meta.get("job_id") != job_id:
            raise RuntimeError("Metadados do job de estoque nao encontrados.")
    total_lojas = len(snapshots)

    def pular_restantes(inicio: int, code: str, message: str):
        with _ESTOQUE_SYNC_STATE_LOCK:
            for item in snapshots[inicio:]:
                _estoque_resultado(meta, item["store_id"]).update(
                    status="skipped", erro={"code": code, "message": message}
                )

    def finalizar(outcome: str, etapa: str, mensagem: str):
        with _ESTOQUE_SYNC_STATE_LOCK:
            lote_atual = int(meta.get("indice_loja") or 0)
        return _estoque_finalizar_job(
            client_id, job_id, outcome, etapa=etapa,
            lote=lote_atual, total=total_lojas,
            mensagem=mensagem,
        )

    for indice, snapshot in enumerate(snapshots, start=1):
        store_id = snapshot["store_id"]
        loja_nome = snapshot["nome"]
        with _ESTOQUE_SYNC_STATE_LOCK:
            meta.update(
                indice_loja=indice,
                loja_atual=loja_nome,
                store_id_atual=store_id,
            )
            resultado_meta = _estoque_resultado(meta, store_id)
            resultado_meta.update(status="running", erro=None)
        _estoque_progresso_loja(
            client_id,
            _criar_progresso("Preparando", 0, 4, 0, f"Preparando loja {indice}/{total_lojas}..."),
            indice_loja=indice, total_lojas=total_lojas, gerenciado_por_job=True,
        )

        try:
            _estoque_verificar_cancelamento(client_id, job_id)
            resultado = await _sincronizar_estoque_loja_impl(
                EstoqueSyncRequest(loja=loja_nome, store_id=store_id),
                client_id,
                preparar_estado=False,
                snapshot_esperado=snapshot,
                indice_loja=indice,
                total_lojas=total_lojas,
                gerenciado_por_job=True,
                job_id=job_id,
            )
        except _EstoqueSyncCancelado:
            with _ESTOQUE_SYNC_STATE_LOCK:
                resultado_meta.update(
                    status="cancelled",
                    erro={
                        "code": "estoque_sync_cancelled",
                        "message": "Atualizacao cancelada.",
                    },
                )
            pular_restantes(indice, "estoque_job_cancelled", "Loja nao iniciada; job cancelado.")
            return finalizar("cancelled", "Cancelado", "Atualizacao de estoque cancelada.")
        except _EstoquePublicacaoInconclusiva as exc:
            erro = _estoque_erro_seguro(exc)
            with _ESTOQUE_SYNC_STATE_LOCK:
                resultado_meta.update(
                    status="uncertain",
                    requires_reconciliation=True,
                    erro=erro,
                )
                meta["requires_reconciliation"] = True
            _estoque_log(client_id, f"[ESTOQUE] Publicacao inconclusiva em {loja_nome}; lote interrompido.")
            pular_restantes(
                indice, "estoque_publication_blocked",
                "Loja nao iniciada por publicacao anterior inconclusiva.",
            )
            with _ESTOQUE_SYNC_STATE_LOCK:
                houve_sucesso = any(
                    item.get("status") == "success" for item in meta["resultados"]
                )
            outcome = "partial" if houve_sucesso else "failed"
            return finalizar(outcome, "Interrompido", erro["message"])
        except Exception as exc:
            erro = _estoque_erro_seguro(exc)
            with _ESTOQUE_SYNC_STATE_LOCK:
                resultado_meta.update(status="failed", erro=erro)
            _estoque_log(client_id, f"[ESTOQUE] {loja_nome} nao atualizada ({erro['code']}).")
            _estoque_progresso_loja(
                client_id,
                _criar_progresso("Falha na loja", 4, 4, 100, f"Loja {indice}/{total_lojas} falhou; continuando."),
                indice_loja=indice, total_lojas=total_lojas, gerenciado_por_job=True,
            )
            continue

        with _ESTOQUE_SYNC_STATE_LOCK:
            resultado_meta.update(
                status="success",
                total=resultado.get("total"),
                event_id=resultado.get("event_id"),
                historico_registrado=resultado.get("historico_registrado"),
                erro=None,
            )

    with _ESTOQUE_SYNC_STATE_LOCK:
        resultados = meta["resultados"]
        sucessos = sum(
            1 for item in resultados if item.get("status") == "success"
        )
    outcome = "completed" if sucessos == total_lojas else ("partial" if sucessos else "failed")
    mensagem = {
        "completed": (
            "Atualizacao de estoque concluida em todas as lojas."
            if req.todas_lojas
            else "Atualizacao de estoque concluida."
        ),
        "partial": "Atualizacao concluida parcialmente; consulte o resultado por loja.",
        "failed": "Nenhuma loja teve o estoque atualizado.",
    }[outcome]
    etapa = "Concluido" if outcome == "completed" else "Concluido com falhas"
    return finalizar(outcome, etapa, mensagem)


async def _sincronizar_estoque_impl(req: EstoqueSyncRequest, client_id: str):
    if bool(getattr(req, "todas_lojas", False)):
        snapshots = _snapshot_lojas_estoque(client_id)
        job_id = uuid.uuid4().hex
        _estoque_preparar_job(
            client_id,
            job_id,
            snapshots,
            todas_lojas=True,
            ativar=False,
        )
        return await _sincronizar_estoque_job(req, client_id, snapshots, job_id)
    return await _sincronizar_estoque_loja_impl(req, client_id)


async def _sincronizar_estoque_loja_impl(
    req: EstoqueSyncRequest,
    client_id: str,
    *,
    preparar_estado: bool = True,
    snapshot_esperado: dict[str, Any] | None = None,
    indice_loja: int = 1,
    total_lojas: int = 1,
    gerenciado_por_job: bool = False,
    job_id: str | None = None,
):
    sync_event_id = _novo_event_id_estoque()
    if preparar_estado:
        ESTOQUE_SYNC_CANCEL_FLAGS.pop(client_id, None)
        ESTOQUE_SYNC_LOGS[client_id] = []
        _estoque_progresso_loja(
            client_id,
            _criar_progresso(
                "Preparando",
                0,
                4,
                0,
                "Iniciando atualização de estoque.",
            ),
            indice_loja=indice_loja,
            total_lojas=total_lojas,
            gerenciado_por_job=gerenciado_por_job,
        )
    loja = _resolver_loja_estoque(
        client_id,
        store_id=getattr(req, "store_id", None),
        loja_nome=req.loja,
    )
    loja_nome = loja["nome"]
    store_id = loja["store_id"]
    integracoes = loja.get("integracoes")
    integracoes = integracoes if isinstance(integracoes, dict) else {}
    bling_cfg = integracoes.get("bling")
    bling_cfg = dict(bling_cfg) if isinstance(bling_cfg, dict) else {}
    if snapshot_esperado is not None:
        if (
            snapshot_esperado.get("store_id") != store_id
            or snapshot_esperado.get("nome") != loja_nome
            or snapshot_esperado.get("_bling_fingerprint")
            != _estoque_bling_fingerprint(bling_cfg)
        ):
            raise _estoque_erro_configuracao_alterada()
    _estoque_log(client_id, f"[ESTOQUE] Iniciando sincronização da loja {loja_nome}")

    if not bling_cfg:
        raise HTTPException(status_code=400, detail="Loja não possui integração Bling conectada.")

    access_token = bling_cfg.get("access_token")
    cid = bling_cfg.get("id")
    sec = bling_cfg.get("secret")
    identidade_bling_inicial = _estoque_bling_fingerprint(bling_cfg)[:2]

    if not (access_token and cid and sec):
        raise HTTPException(status_code=400, detail="Credenciais Bling incompletas para esta loja.")

    def definir_progresso(data: dict[str, Any]) -> None:
        _estoque_progresso_loja(
            client_id,
            data,
            indice_loja=indice_loja,
            total_lojas=total_lojas,
            gerenciado_por_job=gerenciado_por_job,
        )

    _estoque_verificar_cancelamento(client_id, job_id)
    definir_progresso(_criar_progresso("Bling", 1, 4, 15, "Buscando produtos no Bling..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 1/4: listando produtos")
    produtos, status_prod, bling_cfg = _bling_executar_com_refresh(
        client_id,
        loja_nome,
        bling_cfg,
        _bling_listar_produtos,
        on_refresh=lambda: definir_progresso(_criar_progresso("Bling", 1, 4, 20, "Renovando token Bling...")),
        store_id=store_id,
        require_owned_refresh=True,
    )
    if _estoque_bling_fingerprint(bling_cfg)[:2] != identidade_bling_inicial:
        raise _estoque_erro_configuracao_alterada()
    access_token = bling_cfg.get("access_token")

    if status_prod != 200:
        status_err = int(status_prod or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao listar produtos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao listar produtos. Verifique a conexão e tente novamente.")
        raise HTTPException(
            status_code=503,
            detail="Erro inesperado ao consultar produtos no Bling. Tente novamente em alguns minutos."
        )

    _estoque_verificar_cancelamento(client_id, job_id)
    definir_progresso(_criar_progresso("Bling", 2, 4, 45, "Mapeando depósitos da loja..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 2/4: mapeando depósitos")
    mapa_dep, status_dep, bling_cfg = _bling_executar_com_refresh(
        client_id,
        loja_nome,
        bling_cfg,
        _bling_map_depositos,
        on_refresh=lambda: definir_progresso(_criar_progresso("Bling", 2, 4, 48, "Renovando token Bling...")),
        store_id=store_id,
        require_owned_refresh=True,
    )
    if _estoque_bling_fingerprint(bling_cfg)[:2] != identidade_bling_inicial:
        raise _estoque_erro_configuracao_alterada()
    access_token = bling_cfg.get("access_token")
    if status_dep != 200:
        status_err = int(status_dep or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao mapear depósitos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao consultar depósitos. Tente novamente em alguns minutos.")
        raise HTTPException(status_code=503, detail="Erro inesperado ao consultar depósitos no Bling. Tente novamente em alguns minutos.")

    _estoque_verificar_cancelamento(client_id, job_id)
    definir_progresso(_criar_progresso("Bling", 3, 4, 70, "Calculando saldos de estoque..."))
    _estoque_log(client_id, "[ESTOQUE] Etapa 3/4: consultando saldos")
    ids_prod = [str(p.get("id_bling")).strip() for p in produtos if p.get("id_bling")]
    saldos, status_saldo, bling_cfg = _bling_executar_com_refresh(
        client_id,
        loja_nome,
        bling_cfg,
        lambda token: _bling_saldos(token, ids_prod, mapa_dep),
        on_refresh=lambda: definir_progresso(_criar_progresso("Bling", 3, 4, 72, "Renovando token Bling...")),
        store_id=store_id,
        require_owned_refresh=True,
    )
    if _estoque_bling_fingerprint(bling_cfg)[:2] != identidade_bling_inicial:
        raise _estoque_erro_configuracao_alterada()
    access_token = bling_cfg.get("access_token")
    if status_saldo != 200:
        status_err = int(status_saldo or 502)
        _estoque_log(client_id, f"[ESTOQUE] Falha ao consultar saldos no Bling: {status_err}")
        if status_err == 401:
            raise HTTPException(status_code=401, detail="Token Bling expirado. Refaça a conexão em Integrações.")
        if status_err == 503:
            raise HTTPException(status_code=503, detail="Falha de conexão com Bling ao consultar saldos. Tente novamente em alguns minutos.")
        raise HTTPException(status_code=503, detail="Erro inesperado ao consultar saldos no Bling. Tente novamente em alguns minutos.")

    _estoque_verificar_cancelamento(client_id, job_id)
    definir_progresso(_criar_progresso("Processamento", 4, 4, 85, "Consolidando dados do estoque..."))
    registros = []
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    for p in produtos:
        pid = str(p.get("id_bling") or "").strip()
        saldo = saldos.get(pid, {"loja": 0, "full": 0}) if saldos else {"loja": 0, "full": 0}
        registros.append({
            "sku": p.get("sku"),
            "store_id": store_id,
            "loja_sync": loja_nome,
            "last_update": agora,
            "id_bling": pid,
            "nome_bling": p.get("nome_bling"),
            "situacao_bling": p.get("situacao_bling"),
            "ncm_bling": p.get("ncm_bling"),
            "saldo_loja": saldo.get("loja", 0),
            "saldo_full": saldo.get("full", 0)
        })

    _estoque_verificar_cancelamento(client_id, job_id)
    definir_progresso(_criar_progresso("Finalizando", 4, 4, 95, "Salvando estoque atualizado..."))
    tenant_path = get_tenant_path(client_id)
    arquivo_cliente = os.path.join(tenant_path, "produtos_compilado.csv")

    with integracoes_service._LOJAS_CONFIG_LOCK:
        with integracoes_service._integracoes_bloquear_catalogo_e_transicao_fotos(
            client_id,
            tenant_path,
        ):
            loja_revalidada = _resolver_loja_estoque(
                client_id,
                store_id=store_id,
                loja_nome=loja_nome,
            )
            if str(loja_revalidada.get("nome") or "").strip() != loja_nome:
                raise HTTPException(
                    status_code=409,
                    detail="A loja foi renomeada durante a sincronização; tente novamente.",
                )
            integracoes_revalidadas = loja_revalidada.get("integracoes")
            integracoes_revalidadas = (
                integracoes_revalidadas
                if isinstance(integracoes_revalidadas, dict)
                else {}
            )
            bling_revalidado = integracoes_revalidadas.get("bling")
            if (
                _estoque_bling_fingerprint(bling_cfg)[:2] != identidade_bling_inicial
                or _estoque_bling_fingerprint(bling_revalidado)
                != _estoque_bling_fingerprint(bling_cfg)
            ):
                raise _estoque_erro_configuracao_alterada()
            historico_seguro = bool(loja_revalidada.get("_nome_exato_unico"))
            mensagem_finalizacao = (
                "Registrando histórico desta atualização..."
                if historico_seguro
                else "Salvando estoque sem agregar histórico de lojas homônimas..."
            )
            definir_progresso(
                _criar_progresso("Finalizando", 4, 4, 97, mensagem_finalizacao)
            )
            total_hist = _atualizar_produtos_compilados_loja(
                client_id,
                loja_nome,
                registros,
                arquivo_cliente,
                sync_event_id,
                store_id=store_id,
                loja_nome_unico=historico_seguro,
            )

    _estoque_log(client_id, f"[ESTOQUE] Sincronização concluída: {len(registros)} SKUs")
    if historico_seguro:
        _estoque_log(client_id, f"[ESTOQUE] Histórico atualizado ({total_hist} registros; evento {sync_event_id})")
    else:
        _estoque_log(client_id, "[ESTOQUE] Histórico não registrado: nome de loja homônimo.")
    definir_progresso(
        _criar_progresso(
            "Concluído",
            4,
            4,
            100,
            f"Estoque atualizado com {len(registros)} SKUs.",
        )
    )
    return {
        "success": True,
        "store_id": store_id,
        "loja": loja_nome,
        "total": len(registros),
        "event_id": sync_event_id if historico_seguro else None,
        "historico_registrado": historico_seguro,
    }


__all__ = [
    "configure_estoque_sync_runtime",
    "_resolver_loja_estoque",
    "_publicar_csv_estoque_apos_historico",
    "_atualizar_produtos_compilados_loja",
    "_estoque_verificar_cancelamento",
    "_estoque_solicitar_cancelamento_job",
    "_sincronizar_estoque_thread_worker",
    "sincronizar_estoque",
    "_sincronizar_estoque_impl",
]
