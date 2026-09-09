"""Cadastro NCM/CEST synchronization jobs."""

from __future__ import annotations
from backend.services.central_accounts_client import with_request_context

import inspect
import logging
from typing import Optional

from fastapi import Header, HTTPException, Request

from backend.services.runtime_bridge import bind_runtime_globals

logger = logging.getLogger("jk_sistema")
_runtime_get_tenant_id = None


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_runtime_get_tenant_id):
        raise HTTPException(
            status_code=503,
            detail="Contexto de autenticacao do Cadastro ainda nao inicializado.",
        )
    resultado = _runtime_get_tenant_id(request, authorization)
    if inspect.isawaitable(resultado):
        return await resultado
    return resultado


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def _configure_runtime_globals(target_globals, runtime_module=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        runtime_logger = getattr(runtime, "logger", None)
        if runtime_logger is not None:
            target_globals["logger"] = runtime_logger
        runtime_get_tenant_id = getattr(runtime, "get_tenant_id", None)
        if (
            callable(runtime_get_tenant_id)
            and runtime_get_tenant_id is not target_globals.get("get_tenant_id")
        ):
            target_globals["_runtime_get_tenant_id"] = runtime_get_tenant_id
        if hasattr(runtime, "get_tenant_path"):
            target_globals["get_tenant_path"] = getattr(runtime, "get_tenant_path")
    return runtime


import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd
from fastapi import Depends, HTTPException

from backend.services import integracoes as integracoes_service
from backend.services.cadastro_common import *
from backend.services.integracoes import carregar_lojas, renovar_token_bling_loja
from backend.services.monofasico_rules import avaliar_monofasico
from backend.services.path_coordination import path_lock_for

SYNC_NCM_JOBS: dict[str, dict] = {}

MONOFASICO_CADASTRO_COLUNAS = (
    "monofasico",
    "monofasico_status",
    "monofasico_confianca",
    "monofasico_fundamento",
    "monofasico_fonte",
    "monofasico_motivo",
    "monofasico_verificado_em",
)

_SYNC_NCM_ESTOQUE_COLUNAS = (
    "ncm_bling",
    "cest_bling",
    *MONOFASICO_CADASTRO_COLUNAS,
)

_SYNC_NCM_CADASTRO_COLUNAS = (
    "ncm",
    "cest",
    *MONOFASICO_CADASTRO_COLUNAS,
)

_SYNC_NCM_CONFIG_ALTERADA = (
    "A configuracao da loja mudou durante a sincronizacao NCM; nenhum dado foi gravado."
)


class _SyncNcmConfiguracaoAlterada(RuntimeError):
    """Fail-closed signal that never carries credential values."""


def _sync_ncm_bling_valor(cfg: dict[str, Any], *chaves: str) -> str:
    for chave in chaves:
        valor = cfg.get(chave)
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    return ""


def _sync_ncm_bling_fingerprint(cfg: Any) -> tuple[str, ...]:
    """Return an in-memory comparison token without logging credential values."""

    dados = cfg if isinstance(cfg, dict) else {}
    return (
        _sync_ncm_bling_valor(dados, "id", "client_id"),
        _sync_ncm_bling_valor(dados, "secret", "client_secret"),
        _sync_ncm_bling_valor(dados, "access_token"),
        _sync_ncm_bling_valor(dados, "refresh_token"),
        _sync_ncm_bling_valor(dados, "connected"),
        _sync_ncm_bling_valor(dados, "oauth_invalid"),
        _sync_ncm_bling_valor(dados, "status"),
        _sync_ncm_bling_valor(dados, "_sync_version"),
    )


def _classificar_monofasico_cadastro(df_cad: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Apply the shared conservative classifier to every Cadastro row."""
    for coluna in MONOFASICO_CADASTRO_COLUNAS:
        if coluna not in df_cad.columns:
            df_cad[coluna] = ""

    alterados = 0
    contagens: dict[str, int] = {}
    verificado_em = datetime.now(timezone.utc).isoformat()
    campos_resultado = {
        "monofasico": "rotulo",
        "monofasico_status": "status",
        "monofasico_confianca": "confianca",
        "monofasico_fundamento": "fundamento",
        "monofasico_fonte": "fonte",
        "monofasico_motivo": "motivo",
    }

    for idx, row in df_cad.iterrows():
        descricao = " | ".join(
            str(row.get(coluna, "") or "").strip()
            for coluna in ("produto_bling", "nome", "produto", "descricao", "categoria")
            if str(row.get(coluna, "") or "").strip()
        )
        ncm_principal = str(row.get("ncm", "") or "").strip()
        ncm_auditoria = str(row.get("ncm_auditoria", "") or "").strip()
        resultado = avaliar_monofasico(ncm_principal or ncm_auditoria, descricao)
        fonte_auditoria = str(row.get("ncm_fonte_auditoria", "") or "").lower()
        correspondencia = str(row.get("ncm_correspondencia", "") or "").lower()
        if (not ncm_principal and "histórico" in fonte_auditoria) or correspondencia == "ambígua":
            resultado = {
                **resultado,
                "is_monofasico": None,
                "status": "revisao",
                "rotulo": "Revisão necessária",
                "confianca": "pendente",
                "motivo": "O NCM usado na auditoria ainda precisa ser reconfirmado na fonte atual.",
            }
        status = str(resultado.get("status") or "nao_verificado")
        contagens[status] = contagens.get(status, 0) + 1
        mudou_linha = False
        for coluna, chave in campos_resultado.items():
            novo = str(resultado.get(chave) or "")
            atual = str(row.get(coluna, "") or "")
            if atual != novo:
                df_cad.at[idx, coluna] = novo
                mudou_linha = True
        if mudou_linha:
            df_cad.at[idx, "monofasico_verificado_em"] = verificado_em
            alterados += 1

    return alterados, contagens


def configure_cadastro_sync_ncm_runtime(runtime_module=None):
    configure_cadastro_common_runtime(runtime_module)
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_sync_ncm_runtime()

def _sku_lookup_keys_sync_ncm(sku_val: str) -> tuple[str, str, str]:
    sku_norm = _normalizar_sku_mes(str(sku_val or "").strip())
    if re.match(r"^\d+\.0+$", sku_norm):
        sku_norm = str(int(float(sku_norm)))
    sku_compacto = re.sub(r"[^A-Z0-9]", "", sku_norm.upper())
    partes = re.split(r"([0-9]+)", sku_norm.upper())
    sku_numsoft = "".join(str(int(p)) if p.isdigit() else p for p in partes)
    sku_numsoft_compacto = re.sub(r"[^A-Z0-9]", "", sku_numsoft)
    return sku_norm, sku_compacto, sku_numsoft_compacto


def _sync_ncm_path_lock(caminho: str) -> threading.RLock:
    """Compatibility alias for the backend-wide canonical path lock."""

    return path_lock_for(caminho)


def _sync_ncm_texto(valor: Any) -> str:
    if pd.isna(valor):
        return ""
    return str(valor)


def _sync_ncm_nome_loja_chave(valor: Any) -> str:
    """Normalize display names without weakening exact store-id identity."""

    return _sync_ncm_texto(valor).strip().casefold()


def _sync_ncm_nomes_loja(loja: Any) -> tuple[str, ...]:
    """Return current and historical display names with stable casefold dedup."""

    if not isinstance(loja, dict):
        return ()
    historicos = loja.get("nomes_anteriores")
    if isinstance(historicos, (list, tuple, set)):
        candidatos = [loja.get("nome"), *historicos]
    else:
        candidatos = [loja.get("nome"), historicos]
    vistos: set[str] = set()
    nomes: list[str] = []
    for candidato in candidatos:
        nome = _sync_ncm_texto(candidato).strip()
        chave = _sync_ncm_nome_loja_chave(nome)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        nomes.append(nome)
    return tuple(nomes)


def _sync_ncm_mapa_colunas(df: pd.DataFrame) -> dict[str, Any]:
    mapa: dict[str, Any] = {}
    for coluna in df.columns:
        normalizada = str(coluna or "").strip().lower()
        if not normalizada or normalizada in mapa:
            raise RuntimeError("Cabecalho invalido no estoque durante o commit NCM.")
        mapa[normalizada] = coluna
    return mapa


def _sync_ncm_chave_linha(
    loja: Any,
    sku: Any,
    store_id: Any = None,
) -> tuple[str, str]:
    store_id_exato = _sync_ncm_texto(store_id).strip()
    loja_chave = _sync_ncm_nome_loja_chave(loja)
    identidade = (
        f"store_id:{store_id_exato}"
        if store_id_exato
        else (f"loja:{loja_chave}" if loja_chave else "")
    )
    sku_norm = _sku_lookup_keys_sync_ncm(_sync_ncm_texto(sku))[0]
    return identidade, sku_norm


def _sync_ncm_ler_estoque_commit(caminho: str) -> pd.DataFrame:
    try:
        return pd.read_csv(
            caminho,
            dtype=str,
            keep_default_na=False,
        ).fillna("")
    except Exception as exc:
        raise RuntimeError("Nao foi possivel reler o estoque no commit NCM.") from exc


def _sync_ncm_salvar_estoque_atomico(df: pd.DataFrame, caminho: str) -> None:
    destino = os.path.abspath(caminho)
    pasta = os.path.dirname(destino)
    temporario = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{os.path.basename(destino)}.",
            suffix=".tmp",
            dir=pasta,
            delete=False,
        ) as arquivo:
            temporario = arquivo.name
            df.to_csv(arquivo, index=False, lineterminator="\n")
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
        temporario = ""
    finally:
        if temporario:
            try:
                os.unlink(temporario)
            except OSError:
                pass


def _sync_ncm_commit_estoque(
    caminho: str,
    base: pd.DataFrame,
    desejado: pd.DataFrame,
    loja_alvo: str | None = None,
    store_id_alvo: str | None = None,
    permitir_fallback_nome: bool = True,
    nomes_fallback_alvo: Iterable[Any] | None = None,
) -> pd.DataFrame:
    if not base.index.equals(desejado.index):
        raise RuntimeError("O estoque mudou de estrutura durante a sincronizacao NCM.")

    loja_alvo_exata = str(loja_alvo or "").strip()
    loja_alvo_chave = _sync_ncm_nome_loja_chave(loja_alvo_exata)
    nomes_fallback_chaves = {
        _sync_ncm_nome_loja_chave(nome)
        for nome in (nomes_fallback_alvo or ())
        if _sync_ncm_nome_loja_chave(nome)
    }
    if permitir_fallback_nome and loja_alvo_chave and not nomes_fallback_chaves:
        nomes_fallback_chaves.add(loja_alvo_chave)
    store_id_alvo_exato = str(store_id_alvo or "").strip()
    colunas_base = _sync_ncm_mapa_colunas(base)
    colunas_desejado = _sync_ncm_mapa_colunas(desejado)
    if "sku" not in colunas_base or "sku" not in colunas_desejado:
        raise RuntimeError("Estoque sem SKU para commit NCM seguro.")
    if (
        loja_alvo_exata
        and not store_id_alvo_exato
        and ("loja_sync" not in colunas_base or "loja_sync" not in colunas_desejado)
    ):
        raise RuntimeError("Estoque sem chave loja/SKU para commit NCM seguro.")

    def _chave(row: Any, colunas: dict[str, Any]) -> tuple[str, str]:
        return _sync_ncm_chave_linha(
            row[colunas["loja_sync"]] if "loja_sync" in colunas else "",
            row[colunas["sku"]],
            row[colunas["store_id"]] if "store_id" in colunas else "",
        )

    def _selecionada(row: Any, colunas: dict[str, Any]) -> bool:
        row_store_id = (
            _sync_ncm_texto(row[colunas["store_id"]]).strip()
            if "store_id" in colunas
            else ""
        )
        row_loja = (
            _sync_ncm_texto(row[colunas["loja_sync"]]).strip()
            if "loja_sync" in colunas
            else ""
        )
        if store_id_alvo_exato:
            if row_store_id:
                return row_store_id == store_id_alvo_exato
            return bool(
                permitir_fallback_nome
                and nomes_fallback_chaves
                and _sync_ncm_nome_loja_chave(row_loja) in nomes_fallback_chaves
            )
        if loja_alvo_chave:
            return _sync_ncm_nome_loja_chave(row_loja) == loja_alvo_chave
        return True

    alteracoes: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
    for indice in desejado.index:
        linha_base = base.loc[indice]
        linha_desejada = desejado.loc[indice]
        if not _selecionada(linha_desejada, colunas_desejado):
            continue
        chave = _chave(linha_desejada, colunas_desejado)
        chave_base = _chave(linha_base, colunas_base)
        if chave != chave_base:
            raise RuntimeError("A identidade loja/SKU mudou durante a sincronizacao NCM.")

        campos: dict[str, tuple[str, str]] = {}
        for coluna in _SYNC_NCM_ESTOQUE_COLUNAS:
            coluna_desejada = colunas_desejado.get(coluna)
            if coluna_desejada is None:
                continue
            anterior = (
                _sync_ncm_texto(linha_base[colunas_base[coluna]])
                if coluna in colunas_base
                else ""
            )
            novo = _sync_ncm_texto(linha_desejada[coluna_desejada])
            if coluna not in colunas_base or anterior != novo:
                campos[coluna] = (anterior, novo)

        if not campos:
            continue
        if not chave[0] or not chave[1]:
            raise RuntimeError("Linha alterada sem chave loja/SKU no commit NCM.")
        if chave in alteracoes:
            raise RuntimeError("Chave loja/SKU duplicada no commit NCM.")
        alteracoes[chave] = campos

    lock = _sync_ncm_path_lock(caminho)
    with lock:
        atual = _sync_ncm_ler_estoque_commit(caminho)
        colunas_atual = _sync_ncm_mapa_colunas(atual)
        if "sku" not in colunas_atual or (
            loja_alvo_exata
            and not store_id_alvo_exato
            and "loja_sync" not in colunas_atual
        ):
            raise RuntimeError("Estoque atual sem chave loja/SKU no commit NCM.")

        indices_por_chave: dict[tuple[str, str], list[Any]] = {}
        for indice, linha in atual.iterrows():
            chave = _chave(linha, colunas_atual)
            indices_por_chave.setdefault(chave, []).append(indice)

        for chave, campos in alteracoes.items():
            indices = indices_por_chave.get(chave, [])
            if len(indices) != 1:
                raise RuntimeError("Conflito de chave loja/SKU no commit NCM.")
            indice = indices[0]
            for coluna, (anterior, novo) in campos.items():
                coluna_atual = colunas_atual.get(coluna)
                valor_atual = _sync_ncm_texto(atual.at[indice, coluna_atual]) if coluna_atual is not None else ""
                if valor_atual not in {anterior, novo}:
                    raise RuntimeError("Conflito concorrente nos campos NCM do estoque.")
                if coluna_atual is None:
                    atual[coluna] = ""
                    coluna_atual = coluna
                    colunas_atual[coluna] = coluna_atual
                atual.at[indice, coluna_atual] = novo

        if alteracoes:
            _sync_ncm_salvar_estoque_atomico(atual, caminho)

        normalizado = atual.copy()
        normalizado.columns = [str(coluna).strip().lower() for coluna in normalizado.columns]
        return normalizado


def _sync_ncm_commit_cadastro(
    caminho: str,
    base: pd.DataFrame,
    desejado: pd.DataFrame,
) -> pd.DataFrame:
    """Merge only fiscal fields by normalized SKU, preserving concurrent edits."""

    if not base.index.equals(desejado.index):
        raise RuntimeError("O cadastro mudou de estrutura durante a sincronizacao NCM.")
    colunas_base = _sync_ncm_mapa_colunas(base)
    colunas_desejado = _sync_ncm_mapa_colunas(desejado)
    if "sku" not in colunas_base or "sku" not in colunas_desejado:
        raise RuntimeError("Cadastro sem SKU para commit NCM seguro.")

    alteracoes: dict[str, dict[str, tuple[str, str]]] = {}
    for indice in desejado.index:
        sku_base = _sku_lookup_keys_sync_ncm(
            _sync_ncm_texto(base.at[indice, colunas_base["sku"]])
        )[0]
        sku_desejado = _sku_lookup_keys_sync_ncm(
            _sync_ncm_texto(desejado.at[indice, colunas_desejado["sku"]])
        )[0]
        if not sku_base or sku_base != sku_desejado:
            raise RuntimeError("A identidade SKU mudou durante a sincronizacao NCM.")
        if sku_desejado in alteracoes:
            raise RuntimeError("SKU duplicado no cadastro durante o commit NCM.")
        campos: dict[str, tuple[str, str]] = {}
        for coluna in _SYNC_NCM_CADASTRO_COLUNAS:
            coluna_desejada = colunas_desejado.get(coluna)
            if coluna_desejada is None:
                continue
            anterior = (
                _sync_ncm_texto(base.at[indice, colunas_base[coluna]])
                if coluna in colunas_base
                else ""
            )
            novo = _sync_ncm_texto(desejado.at[indice, coluna_desejada])
            if coluna not in colunas_base or anterior != novo:
                campos[coluna] = (anterior, novo)
        if campos:
            alteracoes[sku_desejado] = campos

    with path_lock_for(caminho):
        atual = _sync_ncm_ler_estoque_commit(caminho)
        colunas_atual = _sync_ncm_mapa_colunas(atual)
        if "sku" not in colunas_atual:
            raise RuntimeError("Cadastro atual sem SKU para commit NCM seguro.")
        indices_por_sku: dict[str, list[Any]] = {}
        for indice, linha in atual.iterrows():
            sku = _sku_lookup_keys_sync_ncm(
                _sync_ncm_texto(linha[colunas_atual["sku"]])
            )[0]
            if sku:
                indices_por_sku.setdefault(sku, []).append(indice)
        for sku, campos in alteracoes.items():
            indices = indices_por_sku.get(sku, [])
            if len(indices) != 1:
                raise RuntimeError("Conflito de SKU no commit NCM do cadastro.")
            indice = indices[0]
            for coluna, (anterior, novo) in campos.items():
                coluna_atual = colunas_atual.get(coluna)
                valor_atual = (
                    _sync_ncm_texto(atual.at[indice, coluna_atual])
                    if coluna_atual is not None
                    else ""
                )
                if valor_atual not in {anterior, novo}:
                    raise RuntimeError("Conflito concorrente nos campos NCM do cadastro.")
                if coluna_atual is None:
                    atual[coluna] = ""
                    coluna_atual = coluna
                    colunas_atual[coluna] = coluna
                atual.at[indice, coluna_atual] = novo
        if alteracoes:
            _sync_ncm_salvar_estoque_atomico(atual, caminho)
        normalizado = atual.copy()
        normalizado.columns = [str(coluna).strip().lower() for coluna in normalizado.columns]
        return normalizado

def _set_sync_ncm_job(job_id: str, **kwargs):
    atual = SYNC_NCM_JOBS.get(job_id, {})
    atual.update(kwargs)
    atual["updated_at"] = time.time()
    SYNC_NCM_JOBS[job_id] = atual


def _resolver_loja_sync_ncm(client_id: str, store_id: str) -> dict:
    store_id_norm = str(store_id or "").strip()
    lojas = carregar_lojas(client_id) or []
    correspondentes = [
        item
        for item in lojas
        if isinstance(item, dict)
        and str(item.get("store_id") or "").strip() == store_id_norm
    ]
    if not correspondentes:
        raise HTTPException(status_code=404, detail="Loja nao encontrada para este cliente.")
    if len(correspondentes) != 1:
        raise HTTPException(
            status_code=409,
            detail="store_id duplicado na configuracao de lojas.",
        )
    return correspondentes[0]


def _sync_ncm_revalidar_loja_bloqueada(
    client_id: str,
    store_id: str,
    nome_esperado: str,
    *,
    exigir_nome_unico: bool,
    nomes_fallback_esperados: Iterable[Any] | None = None,
) -> dict:
    """Re-resolve exact identity while the caller holds the config lock."""

    try:
        loja = _resolver_loja_sync_ncm(client_id, store_id)
    except HTTPException as exc:
        raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA) from exc

    store_id_atual = str(loja.get("store_id") or "").strip()
    nome_atual = str(loja.get("nome") or "").strip()
    if store_id_atual != str(store_id or "").strip() or nome_atual != nome_esperado:
        raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)

    nomes_fallback = tuple(nomes_fallback_esperados or ())
    if exigir_nome_unico and not nomes_fallback:
        nomes_fallback = (nome_esperado,)
    if nomes_fallback:
        chaves_esperadas = {
            _sync_ncm_nome_loja_chave(nome)
            for nome in nomes_fallback
            if _sync_ncm_nome_loja_chave(nome)
        }
        chaves_atuais = {
            _sync_ncm_nome_loja_chave(nome)
            for nome in _sync_ncm_nomes_loja(loja)
            if _sync_ncm_nome_loja_chave(nome)
        }
        if not chaves_esperadas.issubset(chaves_atuais):
            raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)
        lojas_atuais = [
            item
            for item in (carregar_lojas(client_id) or [])
            if isinstance(item, dict)
        ]
        for nome_chave in chaves_esperadas:
            correspondentes_nome = [
                item
                for item in lojas_atuais
                if nome_chave
                in {
                    _sync_ncm_nome_loja_chave(nome)
                    for nome in _sync_ncm_nomes_loja(item)
                }
            ]
            if (
                len(correspondentes_nome) != 1
                or str(correspondentes_nome[0].get("store_id") or "").strip()
                != store_id_atual
            ):
                raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)

    return loja


def _sync_ncm_adotar_refresh_persistido(
    client_id: str,
    store_id: str,
    nome_esperado: str,
    cfg_anterior: dict[str, Any],
    cfg_retornado: Any,
    *,
    exigir_nome_unico: bool,
    nomes_fallback_esperados: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Accept token rotation only after observing that exact persisted snapshot."""

    with integracoes_service._LOJAS_CONFIG_LOCK:
        loja = _sync_ncm_revalidar_loja_bloqueada(
            client_id,
            store_id,
            nome_esperado,
            exigir_nome_unico=exigir_nome_unico,
            nomes_fallback_esperados=nomes_fallback_esperados,
        )
        cfg_persistido = dict(((loja.get("integracoes") or {}).get("bling") or {}))
        identidade_anterior = _sync_ncm_bling_fingerprint(cfg_anterior)[:2]
        identidade_persistida = _sync_ncm_bling_fingerprint(cfg_persistido)[:2]
        if identidade_persistida != identidade_anterior:
            raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)
        if _sync_ncm_bling_fingerprint(cfg_persistido) != _sync_ncm_bling_fingerprint(
            cfg_retornado
        ):
            raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)
        return cfg_persistido


def _sync_ncm_commit_estoque_loja_revalidado(
    client_id: str,
    store_id: str,
    nome_esperado: str,
    fingerprint_bling_esperado: tuple[str, ...],
    caminho: str,
    base: pd.DataFrame,
    desejado: pd.DataFrame,
    *,
    permitir_fallback_nome: bool,
    nomes_fallback_alvo: Iterable[Any] | None = None,
) -> pd.DataFrame:
    """Linearize config validation and the scoped stock commit."""

    with integracoes_service._LOJAS_CONFIG_LOCK:
        loja = _sync_ncm_revalidar_loja_bloqueada(
            client_id,
            store_id,
            nome_esperado,
            exigir_nome_unico=permitir_fallback_nome,
            nomes_fallback_esperados=nomes_fallback_alvo,
        )
        cfg_atual = ((loja.get("integracoes") or {}).get("bling") or {})
        if _sync_ncm_bling_fingerprint(cfg_atual) != fingerprint_bling_esperado:
            raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)
        return _sync_ncm_commit_estoque(
            caminho,
            base,
            desejado,
            nome_esperado,
            store_id,
            permitir_fallback_nome=permitir_fallback_nome,
            nomes_fallback_alvo=nomes_fallback_alvo,
        )


def _classificar_monofasico_compilado_loja(
    df_estoque: pd.DataFrame,
    loja_nome: str,
    store_id: str | None = None,
    permitir_fallback_nome: bool = True,
    nomes_fallback: Iterable[Any] | None = None,
) -> tuple[int, dict[str, int]]:
    store_id_exato = str(store_id or "").strip()
    loja_exata = str(loja_nome or "").strip()
    loja_chave = _sync_ncm_nome_loja_chave(loja_exata)
    lojas_fallback_chaves = {
        _sync_ncm_nome_loja_chave(nome)
        for nome in (nomes_fallback or ())
        if _sync_ncm_nome_loja_chave(nome)
    }
    if permitir_fallback_nome and loja_chave and not lojas_fallback_chaves:
        lojas_fallback_chaves.add(loja_chave)
    mask = pd.Series(False, index=df_estoque.index)
    if store_id_exato and "store_id" in df_estoque.columns:
        ids = df_estoque["store_id"].astype(str).str.strip()
        mask = ids.eq(store_id_exato)
        if permitir_fallback_nome and lojas_fallback_chaves and "loja_sync" in df_estoque.columns:
            nomes = df_estoque["loja_sync"].map(_sync_ncm_nome_loja_chave)
            mask |= ids.eq("") & nomes.isin(lojas_fallback_chaves)
    elif permitir_fallback_nome and lojas_fallback_chaves and "loja_sync" in df_estoque.columns:
        mask = df_estoque["loja_sync"].map(_sync_ncm_nome_loja_chave).isin(
            lojas_fallback_chaves
        )
    if not mask.any():
        return 0, {}

    selecionado = df_estoque.loc[mask].copy()
    if "ncm_bling" in selecionado.columns:
        selecionado["ncm"] = selecionado["ncm_bling"].astype(str)
    if "produto_bling" not in selecionado.columns and "nome_bling" in selecionado.columns:
        selecionado["produto_bling"] = selecionado["nome_bling"].astype(str)

    alterados, contagens = _classificar_monofasico_cadastro(selecionado)
    for coluna in MONOFASICO_CADASTRO_COLUNAS:
        if coluna not in selecionado.columns:
            continue
        if coluna not in df_estoque.columns:
            df_estoque[coluna] = ""
        df_estoque.loc[selecionado.index, coluna] = selecionado[coluna].astype(str)
    return alterados, contagens


def _sync_ncm_cadastro_worker(client_id: str, job_id: str, store_id: str | None = None):
    try:
        _set_sync_ncm_job(job_id, status="running", mensagem="Preparando sincronizaÃƒÂ§ÃƒÂ£o de NCM/CEST...", processados=0, total=0)

        store_id_alvo = str(store_id or "").strip()
        loja_alvo = None
        if store_id_alvo:
            try:
                loja_alvo = _resolver_loja_sync_ncm(client_id, store_id_alvo)
            except HTTPException as exc:
                _set_sync_ncm_job(job_id, status="error", mensagem=str(exc.detail))
                return
        else:
            # Defesa em profundidade para chamadas internas diretas do worker:
            # o modo global nao pode propagar um valor first-wins para SKUs que
            # ja possuem identidade duravel por loja.
            from backend.services.cadastro_compatibilidade import (
                exigir_mutacao_legada_sem_sku_controlado,
                skus_controlados_cadastro_lojas,
            )

            try:
                exigir_mutacao_legada_sem_sku_controlado(
                    client_id, skus_controlados_cadastro_lojas(client_id)
                )
            except HTTPException as exc:
                detalhe = exc.detail if isinstance(exc.detail, dict) else {}
                _set_sync_ncm_job(
                    job_id,
                    status="error",
                    mensagem=str(detalhe.get("message") or exc.detail),
                    code=str(detalhe.get("code") or "store_id_required"),
                )
                return

        arquivo_estoque = _migrar_arquivo_legado_para_tenant(client_id, "produtos_compilado.csv", ARQUIVO_DB_PRODUTOS)
        if not (arquivo_estoque and os.path.exists(arquivo_estoque)):
            _set_sync_ncm_job(job_id, status="error", mensagem="Arquivo de estoque nao encontrado para o cliente.")
            return

        arquivo_cadastro = ""
        df_cad = pd.DataFrame()
        if not store_id_alvo:
            arquivo_cadastro = _migrar_arquivo_legado_para_tenant(
                client_id,
                "cadastro_produtos.csv",
                ARQUIVO_DB_CADASTRO_PRODUTOS,
            )
            if not (arquivo_cadastro and os.path.exists(arquivo_cadastro)):
                _set_sync_ncm_job(
                    job_id,
                    status="error",
                    mensagem="Arquivo de cadastro nao encontrado para o cliente.",
                )
                return
            df_cad = pd.read_csv(arquivo_cadastro, dtype=str).fillna("")
            df_cad.columns = [c.strip().lower() for c in df_cad.columns]
            df_cad_base = df_cad.copy(deep=True)
        df_estoque = pd.read_csv(
            arquivo_estoque,
            dtype=str,
            keep_default_na=False,
        ).fillna("")
        df_estoque.columns = [c.strip().lower() for c in df_estoque.columns]
        skus_global_candidatos: set[str] = set()
        if not store_id_alvo:
            for frame in (df_cad, df_estoque):
                if "sku" in frame.columns:
                    skus_global_candidatos.update(
                        str(valor or "").strip()
                        for valor in frame["sku"].tolist()
                        if str(valor or "").strip()
                    )

        if "id_bling" not in df_estoque.columns:
            _set_sync_ncm_job(job_id, status="error", mensagem="Coluna id_bling nÃ£o encontrada no estoque.")
            return
        df_estoque_base = df_estoque.copy(deep=True)
        if "ncm_bling" not in df_estoque.columns:
            df_estoque["ncm_bling"] = ""
        if "cest_bling" not in df_estoque.columns:
            df_estoque["cest_bling"] = ""

        lojas = [
            dict(loja)
            for loja in (carregar_lojas(client_id) or [])
            if isinstance(loja, dict)
        ]
        lojas_por_id: dict[str, list[dict[str, Any]]] = {}
        lojas_por_nome: dict[str, list[dict[str, Any]]] = {}
        bling_por_store_id: dict[str, dict[str, Any]] = {}
        for loja in lojas:
            store_id_loja = str(loja.get("store_id") or "").strip()
            if store_id_loja:
                lojas_por_id.setdefault(store_id_loja, []).append(loja)
            for nome_loja in _sync_ncm_nomes_loja(loja):
                nome_loja_chave = _sync_ncm_nome_loja_chave(nome_loja)
                if nome_loja_chave:
                    lojas_por_nome.setdefault(nome_loja_chave, []).append(loja)
            cfg_bling = (loja.get("integracoes") or {}).get("bling") or {}
            if (
                store_id_loja
                and cfg_bling.get("access_token")
                and _sync_ncm_bling_valor(cfg_bling, "id", "client_id")
                and _sync_ncm_bling_valor(cfg_bling, "secret", "client_secret")
            ):
                bling_por_store_id[store_id_loja] = dict(cfg_bling)

        if not bling_por_store_id:
            _set_sync_ncm_job(job_id, status="error", mensagem="Nenhuma loja Bling conectada encontrada.")
            return

        loja_alvo_nome = str((loja_alvo or {}).get("nome") or "").strip()
        nomes_fallback_alvo = tuple(
            nome
            for nome in _sync_ncm_nomes_loja(loja_alvo)
            if len(lojas_por_nome.get(_sync_ncm_nome_loja_chave(nome), [])) == 1
            and str(
                lojas_por_nome[_sync_ncm_nome_loja_chave(nome)][0].get("store_id")
                or ""
            ).strip()
            == store_id_alvo
        )
        nome_alvo_unico = bool(nomes_fallback_alvo)
        fingerprint_bling_alvo = (
            _sync_ncm_bling_fingerprint(bling_por_store_id.get(store_id_alvo))
            if store_id_alvo and store_id_alvo in bling_por_store_id
            else None
        )

        def _loja_segura_da_linha(row: Any) -> dict[str, Any] | None:
            row_store_id = str(row.get("store_id", "") or "").strip()
            if row_store_id:
                correspondentes = lojas_por_id.get(row_store_id, [])
                if len(correspondentes) != 1:
                    return None
                loja_linha = correspondentes[0]
            else:
                row_nome = _sync_ncm_nome_loja_chave(row.get("loja_sync", ""))
                correspondentes = lojas_por_nome.get(row_nome, [])
                if len(correspondentes) != 1:
                    return None
                loja_linha = correspondentes[0]
            if store_id_alvo and str(loja_linha.get("store_id") or "").strip() != store_id_alvo:
                return None
            return loja_linha

        candidatos_linhas: list[tuple[Any, dict[str, Any]]] = []
        for idx_est, row_est in df_estoque.iterrows():
            pid = str(row_est.get("id_bling", "") or "").strip()
            loja_linha = _loja_segura_da_linha(row_est)
            if pid and loja_linha:
                candidatos_linhas.append((idx_est, loja_linha))

        total = len(candidatos_linhas)
        _set_sync_ncm_job(job_id, total=total, mensagem=f"Sincronizando NCM/CEST: 0/{total}")

        cache_ncm_cest = {}
        alterou_ncm_estoque = False
        encontrados = 0
        processados = 0

        for idx_est, loja_linha in candidatos_linhas:
            row_est = df_estoque.loc[idx_est]
            pid = str(row_est.get("id_bling", "") or "").strip()
            store_id_linha = str(loja_linha.get("store_id") or "").strip()
            nome_loja = str(loja_linha.get("nome") or "").strip()
            ncm_atual = str(row_est.get("ncm_bling", "") or "").strip()
            cest_atual = str(row_est.get("cest_bling", "") or "").strip()

            ncm_novo = ""
            cest_novo = ""
            if store_id_linha not in bling_por_store_id:
                processados += 1
                continue
            for _tentativa in range(1):
                chave_cache = (store_id_linha, pid)
                if chave_cache in cache_ncm_cest:
                    resp_cached = cache_ncm_cest[chave_cache]
                    ncm_cache = resp_cached.get("ncm", "")
                    cest_cache = resp_cached.get("cest", "")
                    if ncm_cache:
                        ncm_novo = ncm_cache
                    if cest_cache:
                        cest_novo = cest_cache
                    if ncm_novo or cest_novo:
                        break
                    continue

                cfg_loja = bling_por_store_id.get(store_id_linha) or {}
                access_token_ncm = cfg_loja.get("access_token")
                cid_ncm = _sync_ncm_bling_valor(cfg_loja, "id", "client_id")
                sec_ncm = _sync_ncm_bling_valor(cfg_loja, "secret", "client_secret")
                refresh_ncm = cfg_loja.get("refresh_token")
                if not (access_token_ncm and (cfg_loja.get("central") or (cid_ncm and sec_ncm))):
                    cache_ncm_cest[chave_cache] = {"ncm": "", "cest": ""}
                    continue

                resp_ncm_cest, status_ncm = _bling_obter_ncm_cest_produto(access_token_ncm, pid)
                if status_ncm == 401 and refresh_ncm:
                    try:
                        renovado = renovar_token_bling_loja(
                            client_id,
                            nome_loja,
                            cfg_loja,
                            store_id=store_id_linha,
                        )
                        if store_id_alvo and store_id_linha == store_id_alvo:
                            renovado = _sync_ncm_adotar_refresh_persistido(
                                client_id,
                                store_id_linha,
                                loja_alvo_nome,
                                cfg_loja,
                                renovado,
                                exigir_nome_unico=nome_alvo_unico,
                                nomes_fallback_esperados=nomes_fallback_alvo,
                            )
                            fingerprint_bling_alvo = _sync_ncm_bling_fingerprint(
                                renovado
                            )
                        bling_por_store_id[store_id_linha] = dict(renovado)
                        access_token_ncm = renovado.get("access_token") or access_token_ncm
                        resp_ncm_cest, status_ncm = _bling_obter_ncm_cest_produto(access_token_ncm, pid)
                    except _SyncNcmConfiguracaoAlterada:
                        raise
                    except Exception:
                        status_ncm = 500

                resp_ncm_cest = resp_ncm_cest if (status_ncm == 200 and resp_ncm_cest) else {"ncm": "", "cest": ""}
                cache_ncm_cest[chave_cache] = resp_ncm_cest
                ncm_resp = resp_ncm_cest.get("ncm", "")
                cest_resp = resp_ncm_cest.get("cest", "")
                if ncm_resp and not ncm_novo:
                    ncm_novo = ncm_resp
                if cest_resp and not cest_novo:
                    cest_novo = cest_resp
                if ncm_novo or cest_novo:
                    break

            processados += 1
            if ncm_novo or cest_novo:  # Atualizar se temos NCM ou CEST
                if ncm_novo and ncm_novo != ncm_atual:
                    df_estoque.at[idx_est, "ncm_bling"] = ncm_novo
                if cest_novo and cest_novo != cest_atual:
                    df_estoque.at[idx_est, "cest_bling"] = cest_novo
                alterou_ncm_estoque = True
                encontrados += 1

            _set_sync_ncm_job(
                job_id,
                processados=processados,
                encontrados=encontrados,
                mensagem=f"Sincronizando NCM/CEST: {processados}/{total}"
            )

        classificados_loja = 0
        contagens_monofasico_loja: dict[str, int] = {}
        if store_id_alvo:
            _set_sync_ncm_job(job_id, mensagem="Classificando tributacao monofasica da loja...")
            classificados_loja, contagens_monofasico_loja = _classificar_monofasico_compilado_loja(
                df_estoque,
                loja_alvo_nome,
                store_id_alvo,
                permitir_fallback_nome=nome_alvo_unico,
                nomes_fallback=nomes_fallback_alvo,
            )
            if classificados_loja:
                alterou_ncm_estoque = True

        commit_estoque_global_pendente = False
        if alterou_ncm_estoque:
            if store_id_alvo:
                if fingerprint_bling_alvo is None:
                    raise _SyncNcmConfiguracaoAlterada(_SYNC_NCM_CONFIG_ALTERADA)
                df_estoque = _sync_ncm_commit_estoque_loja_revalidado(
                    client_id,
                    store_id_alvo,
                    loja_alvo_nome,
                    fingerprint_bling_alvo,
                    arquivo_estoque,
                    df_estoque_base,
                    df_estoque,
                    permitir_fallback_nome=nome_alvo_unico,
                    nomes_fallback_alvo=nomes_fallback_alvo,
                )
            else:
                commit_estoque_global_pendente = True

        # O cadastro legado nao possui loja. Em uma sincronizacao de loja especifica,
        # gravar nele misturaria NCM/CEST entre contas; a nova listagem resolve os
        # valores diretamente da linha da mesma loja em produtos_compilado.csv.
        if store_id_alvo:
            _set_sync_ncm_job(
                job_id,
                status="done",
                mensagem=(
                    f"Sincronizacao NCM da loja concluida: {encontrados}/{total} preenchidos; "
                    f"{classificados_loja} classificacoes monofasicas atualizadas."
                ),
                processados=processados,
                encontrados=encontrados,
                monofasico_alterados=classificados_loja,
                monofasico_contagens=contagens_monofasico_loja,
            )
            return

        # Propaga NCM e CEST para o cadastro por SKU e persiste no arquivo do usuÃƒÂ¡rio.
        mapa_ncm = {}
        mapa_ncm_compacto = {}
        mapa_ncm_numsoft = {}
        mapa_cest = {}
        mapa_cest_compacto = {}
        mapa_cest_numsoft = {}
        for _, row_est in df_estoque[["sku", "ncm_bling", "cest_bling"]].iterrows():
            sku_est, sku_est_compacto, sku_est_numsoft = _sku_lookup_keys_sync_ncm(row_est.get("sku", ""))
            ncm_est = str(row_est.get("ncm_bling", "") or "").strip()
            cest_est = str(row_est.get("cest_bling", "") or "").strip()
            if sku_est and ncm_est and sku_est not in mapa_ncm:
                mapa_ncm[sku_est] = ncm_est
            if sku_est_compacto and ncm_est and sku_est_compacto not in mapa_ncm_compacto:
                mapa_ncm_compacto[sku_est_compacto] = ncm_est
            if sku_est_numsoft and ncm_est and sku_est_numsoft not in mapa_ncm_numsoft:
                mapa_ncm_numsoft[sku_est_numsoft] = ncm_est
            if sku_est and cest_est and sku_est not in mapa_cest:
                mapa_cest[sku_est] = cest_est
            if sku_est_compacto and cest_est and sku_est_compacto not in mapa_cest_compacto:
                mapa_cest_compacto[sku_est_compacto] = cest_est
            if sku_est_numsoft and cest_est and sku_est_numsoft not in mapa_cest_numsoft:
                mapa_cest_numsoft[sku_est_numsoft] = cest_est

        def _resolver_ncm_sku(sku_val: str) -> str:
            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys_sync_ncm(sku_val)
            return (
                mapa_ncm.get(sku_norm)
                or mapa_ncm_compacto.get(sku_compacto)
                or mapa_ncm_numsoft.get(sku_numsoft, "")
            )
        
        def _resolver_cest_sku(sku_val: str) -> str:
            sku_norm, sku_compacto, sku_numsoft = _sku_lookup_keys_sync_ncm(sku_val)
            return (
                mapa_cest.get(sku_norm)
                or mapa_cest_compacto.get(sku_compacto)
                or mapa_cest_numsoft.get(sku_numsoft, "")
            )

        mudou_cadastro = False
        if "sku" in df_cad.columns:
            ncm_series = df_cad["sku"].astype(str).apply(_resolver_ncm_sku).fillna("")
            cest_series = df_cad["sku"].astype(str).apply(_resolver_cest_sku).fillna("")
            criou_col_ncm = False
            criou_col_cest = False
            if "ncm" not in df_cad.columns:
                df_cad["ncm"] = ""
                criou_col_ncm = True
            if "cest" not in df_cad.columns:
                df_cad["cest"] = ""
                criou_col_cest = True

            ncm_atual_series = df_cad["ncm"].astype(str)
            cest_atual_series = df_cad["cest"].astype(str)
            ncm_series_str = ncm_series.astype(str)
            cest_series_str = cest_series.astype(str)
            mask_ncm_novo = ncm_series_str.str.strip() != ""
            mask_cest_novo = cest_series_str.str.strip() != ""

            ncm_atualizados = int((mask_ncm_novo & (ncm_atual_series != ncm_series_str)).sum())
            cest_atualizados = int((mask_cest_novo & (cest_atual_series != cest_series_str)).sum())
            ncm_preservados = int((~mask_ncm_novo & ncm_atual_series.str.strip().ne("")).sum())
            cest_preservados = int((~mask_cest_novo & cest_atual_series.str.strip().ne("")).sum())

            df_cad.loc[mask_ncm_novo, "ncm"] = ncm_series.loc[mask_ncm_novo]
            df_cad.loc[mask_cest_novo, "cest"] = cest_series.loc[mask_cest_novo]
            classificados_alterados, contagens_monofasico = _classificar_monofasico_cadastro(df_cad)

            mudou_cadastro = (
                criou_col_ncm or
                criou_col_cest or
                (ncm_atualizados > 0) or
                (cest_atualizados > 0) or
                (classificados_alterados > 0)
            )
            if mudou_cadastro:
                # SÃƒÂ³ sobrescreve quando hÃƒÂ¡ valor novo vindo do estoque/Bling;
                # evita apagar NCM/CEST jÃƒÂ¡ salvos no cadastro.
                df_cad.loc[mask_ncm_novo, "ncm"] = ncm_series.loc[mask_ncm_novo]
                df_cad.loc[mask_cest_novo, "cest"] = cest_series.loc[mask_cest_novo]

            logger.info(
                "[CADASTRO NCM/CEST][%s][job=%s] atualizados: ncm=%d, cest=%d | preservados: ncm=%d, cest=%d",
                client_id,
                job_id,
                ncm_atualizados,
                cest_atualizados,
                ncm_preservados,
                cest_preservados,
            )
            logger.info(
                "[CADASTRO MONOFASICO][%s][job=%s] linhas_alteradas=%d | contagens=%s",
                client_id,
                job_id,
                classificados_alterados,
                contagens_monofasico,
            )

        if commit_estoque_global_pendente or mudou_cadastro:
            from backend.services.cadastro_compatibilidade import (
                bloquear_mutacao_legada_sem_sku_controlado,
            )

            # The SKU candidates come from the independent legacy/compiled
            # snapshots, not from a pre-lock canonical snapshot.  Rechecking
            # them while holding the canonical lock closes the race where a
            # scoped row is created during the remote Bling calls.
            with bloquear_mutacao_legada_sem_sku_controlado(
                client_id, skus_global_candidatos
            ):
                if commit_estoque_global_pendente:
                    df_estoque = _sync_ncm_commit_estoque(
                        arquivo_estoque,
                        df_estoque_base,
                        df_estoque,
                        permitir_fallback_nome=True,
                    )
                if mudou_cadastro:
                    df_cad = _sync_ncm_commit_cadastro(
                        arquivo_cadastro,
                        df_cad_base,
                        df_cad,
                    )

        _set_sync_ncm_job(
            job_id,
            status="done",
            mensagem=f"SincronizaÃƒÂ§ÃƒÂ£o NCM concluida: {encontrados}/{total} preenchidos.",
            processados=processados,
            encontrados=encontrados,
        )
    except HTTPException as e:
        detalhe = e.detail if isinstance(e.detail, dict) else {}
        _set_sync_ncm_job(
            job_id,
            status="error",
            mensagem=str(detalhe.get("message") or e.detail),
            code=str(detalhe.get("code") or "sync_ncm_error"),
        )
    except Exception as e:
        _set_sync_ncm_job(job_id, status="error", mensagem=f"Erro na sincronizaÃƒÂ§ÃƒÂ£o de NCM: {e}")

async def iniciar_sync_ncm_cadastro(
    store_id: Optional[str] = None,
    client_id: str = Depends(get_tenant_id),
):
    store_id_norm = str(store_id or "").strip()
    if store_id_norm:
        _resolver_loja_sync_ncm(client_id, store_id_norm)
    else:
        from backend.services.cadastro_compatibilidade import (
            exigir_mutacao_legada_sem_sku_controlado,
            skus_controlados_cadastro_lojas,
        )

        exigir_mutacao_legada_sem_sku_controlado(
            client_id, skus_controlados_cadastro_lojas(client_id)
        )

    # Reaproveita job em execuÃƒÂ§ÃƒÂ£o para o mesmo cliente.
    for job_id, job in SYNC_NCM_JOBS.items():
        if job.get("client_id") == client_id and job.get("status") == "running":
            if str(job.get("store_id") or "") == store_id_norm:
                return {"job_id": job_id, "status": "running"}
            raise HTTPException(status_code=409, detail="Ja existe uma sincronizacao NCM em andamento para este cliente.")

    job_id = uuid.uuid4().hex
    SYNC_NCM_JOBS[job_id] = {
        "client_id": client_id,
        "store_id": store_id_norm,
        "status": "running",
        "mensagem": "Inicializando...",
        "processados": 0,
        "total": 0,
        "encontrados": 0,
        "updated_at": time.time(),
    }
    t = threading.Thread(
            target=with_request_context(_sync_ncm_cadastro_worker),
        args=(client_id, job_id, store_id_norm or None),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id, "status": "running"}

async def progresso_sync_ncm_cadastro(job_id: str, client_id: str = Depends(get_tenant_id)):
    job = SYNC_NCM_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job de sincronizaÃƒÂ§ÃƒÂ£o nÃ£o encontrado.")
    if job.get("client_id") != client_id:
        raise HTTPException(status_code=403, detail="Acesso negado a este job.")
    return {k: v for k, v in job.items() if k != "client_id"}

__all__ = ['SYNC_NCM_JOBS', 'MONOFASICO_CADASTRO_COLUNAS', '_classificar_monofasico_cadastro', '_classificar_monofasico_compilado_loja', '_sku_lookup_keys_sync_ncm', '_sync_ncm_path_lock', '_sync_ncm_commit_estoque', '_set_sync_ncm_job', '_resolver_loja_sync_ncm', '_sync_ncm_cadastro_worker', 'iniciar_sync_ncm_cadastro', 'progresso_sync_ncm_cadastro', 'configure_cadastro_sync_ncm_runtime']
