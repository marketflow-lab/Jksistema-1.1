"""Shared Sync lojas/integracoes sensitive merge helpers."""

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
import unicodedata
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
from backend.services.shared_sync_merge_user_data import _shared_sync_json_from_bytes


logger = logging.getLogger("jk_sistema")


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

def _shared_sync_loja_store_id(loja: Any) -> str:
    if not isinstance(loja, dict):
        return ""
    return str(loja.get("store_id") or "").strip()


def _shared_sync_store_id_deterministico(client_id: str, nome: Any) -> str:
    # Compatibilidade de chamada: IDs atuais sao opacos e nunca podem ser
    # derivados do tenant ou do nome da loja.
    return ""


def _shared_sync_materializar_store_ids_legados(
    lojas: list[dict],
    client_id: str,
) -> list[dict]:
    materializadas = [_shared_sync_json_clone(loja) for loja in lojas]
    for loja in materializadas:
        if not isinstance(loja, dict) or _shared_sync_loja_store_id(loja):
            continue
        store_id = _shared_sync_store_id_deterministico(
            client_id,
            loja.get("nome"),
        )
        if store_id:
            loja["store_id"] = store_id
    return materializadas


def _shared_sync_validar_nomes_legados_entre_fontes(
    client_id: str,
    *fontes: list[dict],
) -> None:
    # IDs atuais sao opacos e registros legados so podem casar pela chave de
    # nome normalizada. Variacoes de acento/pontuacao dessa mesma chave nao sao
    # uma divergencia de identidade; a ambiguidade dentro de cada snapshot ja
    # e bloqueada por ``_shared_sync_validar_identidades_lojas``.
    _ = client_id, fontes


def _shared_sync_loja_identity_key(loja: Any) -> str:
    store_id = _shared_sync_loja_store_id(loja)
    if store_id:
        return f"store_id:{store_id}"
    nome_key = _shared_sync_loja_key((loja or {}).get("nome") if isinstance(loja, dict) else "")
    # Keep the historical manifest key for legacy rows so existing peers do
    # not resend them merely because durable identities were introduced.
    return nome_key

def _shared_sync_sync_version(valor: Any) -> int:
    try:
        return max(0, int(valor or 0))
    except (TypeError, ValueError):
        return 0

def _shared_sync_remote_store_is_newer(atual: dict, remoto: dict) -> bool:
    atual_version = _shared_sync_sync_version((atual or {}).get("_sync_version"))
    remoto_version = _shared_sync_sync_version((remoto or {}).get("_sync_version"))
    if remoto_version != atual_version:
        return remoto_version > atual_version
    atual_updated = str((atual or {}).get("_sync_updated_at") or "").strip()
    remoto_updated = str((remoto or {}).get("_sync_updated_at") or "").strip()
    return bool(remoto_updated and remoto_updated > atual_updated)

def _shared_sync_remote_integration_is_newer(atual: dict, remoto: dict) -> bool:
    atual_version = _shared_sync_sync_version((atual or {}).get("_sync_version"))
    remoto_version = _shared_sync_sync_version((remoto or {}).get("_sync_version"))
    if remoto_version != atual_version:
        return remoto_version > atual_version
    atual_updated = str((atual or {}).get("_sync_updated_at") or "").strip()
    remoto_updated = str((remoto or {}).get("_sync_updated_at") or "").strip()
    if remoto_updated != atual_updated:
        return bool(remoto_updated and remoto_updated > atual_updated)
    return _shared_sync_timestamp((remoto or {}).get("updated_at")) > _shared_sync_timestamp(
        (atual or {}).get("updated_at")
    )

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
        texto = str(valor or "").strip()
        if not texto:
            return 0.0
        try:
            return datetime.fromisoformat(texto.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0


def _shared_sync_integracao_revisao(dados: Any) -> tuple[int, float]:
    if not isinstance(dados, dict):
        return 0, 0.0
    try:
        version = max(0, int(dados.get("_sync_version") or 0))
    except (TypeError, ValueError):
        version = 0
    updated = _shared_sync_timestamp(dados.get("updated_at"))
    if not updated:
        updated = _shared_sync_timestamp(dados.get("_sync_updated_at"))
    return version, updated


def _shared_sync_comparar_revisao_integracao(atual: Any, remoto: Any) -> int:
    """Retorna -1 quando o local e mais antigo, 1 quando e mais novo."""
    _atual_version, atual_ts = _shared_sync_integracao_revisao(atual)
    _remoto_version, remoto_ts = _shared_sync_integracao_revisao(remoto)
    # _sync_version e monotona apenas dentro de uma maquina. Entre maquinas,
    # somente timestamps presentes nos dois lados oferecem uma ordem comum.
    if atual_ts and remoto_ts and atual_ts != remoto_ts:
        return 1 if atual_ts > remoto_ts else -1
    return 0


def _shared_sync_oauth_em_andamento(dados: Any) -> bool:
    if not isinstance(dados, dict):
        return False
    return any(
        _shared_sync_valor_preenchido(dados.get(chave))
        for chave in ("oauth_draft", "oauth_pending_state")
    )


def _shared_sync_alias_unico(
    dados: dict,
    aliases: tuple[str, ...],
    *,
    status_code: int = 409,
    origem: str = "Integracao",
):
    preenchidos = [
        dados.get(alias)
        for alias in aliases
        if _shared_sync_valor_preenchido(dados.get(alias))
    ]
    valores = {str(valor).strip() for valor in preenchidos}
    if len(valores) > 1:
        raise HTTPException(
            status_code=status_code,
            detail=(
                f"{origem} contem aliases conflitantes para a mesma "
                "credencial; sincronizacao bloqueada."
            ),
        )
    return preenchidos[0] if preenchidos else None


def _shared_sync_validar_aliases_integracao(
    servico_key: str,
    dados: dict,
    *,
    status_code: int,
    origem: str,
) -> None:
    servico_key = _shared_sync_servico_key(servico_key)
    grupos: tuple[tuple[str, ...], ...] = ()
    if servico_key == "mercadolivre":
        grupos = (
            ("app_id", "client_id", "id"),
            ("client_secret", "secret_key", "secret"),
        )
    elif servico_key == "bling":
        grupos = (
            ("id", "client_id", "app_id"),
            ("secret", "client_secret", "secret_key"),
            ("access_token", "token"),
            ("api_key", "apikey"),
        )
    elif servico_key == "mercadoturbo":
        grupos = (("token", "access_token"),)
    for aliases in grupos:
        _shared_sync_alias_unico(
            dados,
            aliases,
            status_code=status_code,
            origem=origem,
        )


def _shared_sync_oauth_payload_relevante(
    dados: Any,
    *,
    incluir_user_id: bool = True,
    servico_key: str = "",
) -> dict:
    if not isinstance(dados, dict):
        return {}
    ignoradas = {
        "connected",
        "status",
        "motivo",
        "oauth_invalid",
        "shared_without_oauth_tokens",
        "oauth_draft",
        "oauth_pending_state",
        "_sync_version",
        "_sync_updated_at",
        "updated_at",
    }
    payload = {
        str(chave): _shared_sync_json_clone(valor)
        for chave, valor in dados.items()
        if str(chave or "").strip().lower() not in ignoradas
        and _shared_sync_valor_preenchido(valor)
    }
    servico_key = _shared_sync_servico_key(servico_key)

    def normalizar_alias(chave_canonica: str, *aliases: str) -> None:
        valor = _shared_sync_alias_unico(
            dados,
            tuple(aliases),
            status_code=409,
            origem="Integracao OAuth",
        )
        for alias in aliases:
            payload.pop(alias, None)
        if _shared_sync_valor_preenchido(valor):
            payload[chave_canonica] = str(valor).strip()

    if servico_key == "mercadolivre":
        normalizar_alias("app_id", "app_id", "client_id", "id")
        normalizar_alias(
            "client_secret",
            "client_secret",
            "secret_key",
            "secret",
        )
        normalizar_alias("access_token", "access_token")
        normalizar_alias("refresh_token", "refresh_token")
        normalizar_alias("user_id", "user_id")
    elif servico_key == "bling":
        normalizar_alias("id", "id", "client_id", "app_id")
        normalizar_alias(
            "secret",
            "secret",
            "client_secret",
            "secret_key",
        )
        normalizar_alias("access_token", "access_token", "token")
        normalizar_alias("refresh_token", "refresh_token")
        normalizar_alias("api_key", "api_key", "apikey")
    elif servico_key == "mercadoturbo":
        normalizar_alias("token", "token", "access_token")
    if not incluir_user_id:
        payload.pop("user_id", None)
    return payload


def _shared_sync_integracao_semanticamente_igual(
    atual: Any,
    base: Any,
    servico_key: str,
) -> bool:
    if not isinstance(atual, dict) or not isinstance(base, dict):
        return atual == base
    return _shared_sync_oauth_payload_relevante(
        atual,
        servico_key=servico_key,
    ) == _shared_sync_oauth_payload_relevante(
        base,
        servico_key=servico_key,
    )


def _shared_sync_loja_semanticamente_igual(atual: Any, base: Any) -> bool:
    if not isinstance(atual, dict) or not isinstance(base, dict):
        return atual == base

    def canonica(loja: dict) -> dict:
        saida = {
            str(chave): _shared_sync_json_clone(valor)
            for chave, valor in loja.items()
            if str(chave or "").strip().lower()
            not in {"_sync_version", "_sync_updated_at", "integracoes"}
        }
        integracoes = (
            loja.get("integracoes")
            if isinstance(loja.get("integracoes"), dict)
            else {}
        )
        saida["integracoes"] = {
            _shared_sync_servico_key(servico): _shared_sync_oauth_payload_relevante(
                dados,
                servico_key=servico,
            )
            for servico, dados in integracoes.items()
        }
        return saida

    return canonica(atual) == canonica(base)


def _shared_sync_validar_identidades_lojas(
    lojas: list[dict],
    *,
    origem: str,
    status_code: int,
) -> None:
    store_ids: set[str] = set()
    nomes_legados: set[str] = set()
    for loja in lojas:
        if not isinstance(loja, dict):
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem entrada de loja invalida.",
            )
        store_id = _shared_sync_loja_store_id(loja)
        nome_key = _shared_sync_loja_key(loja.get("nome"))
        if not nome_key:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem loja sem nome.",
            )
        if "integracoes" in loja and not isinstance(loja.get("integracoes"), dict):
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem integracoes de loja em formato invalido.",
            )
        if store_id:
            if store_id in store_ids:
                raise HTTPException(
                    status_code=status_code,
                    detail=f"{origem} contem store_id duplicado; sincronizacao bloqueada.",
                )
            store_ids.add(store_id)
        else:
            # Nomes sao apenas uma ponte para registros realmente legados.
            # Duas identidades duraveis podem ter o mesmo nome de exibicao;
            # somente duas linhas sem store_id seriam impossiveis de resolver
            # de forma deterministica durante o merge.
            if nome_key in nomes_legados:
                raise HTTPException(
                    status_code=status_code,
                    detail=(
                        f"{origem} contem lojas legadas homonimas; "
                        "sincronizacao bloqueada."
                    ),
                )
            nomes_legados.add(nome_key)
        integracoes = (
            loja.get("integracoes")
            if isinstance(loja.get("integracoes"), dict)
            else {}
        )
        servicos: set[str] = set()
        for servico, dados_servico in integracoes.items():
            if not isinstance(dados_servico, dict):
                raise HTTPException(
                    status_code=status_code,
                    detail=f"{origem} contem bloco de integracao em formato invalido.",
                )
            servico_key = _shared_sync_servico_key(servico)
            if servico_key in servicos:
                raise HTTPException(
                    status_code=status_code,
                    detail=(
                        f"{origem} contem aliases duplicados para a mesma integracao; "
                        "sincronizacao bloqueada."
                    ),
                )
            servicos.add(servico_key)
            _shared_sync_validar_aliases_integracao(
                servico_key,
                dados_servico,
                status_code=status_code,
                origem=origem,
            )
def _shared_sync_integracao_oauth_completa(servico_key: str, dados: Any) -> bool:
    if not isinstance(dados, dict):
        return False
    servico_key = _shared_sync_servico_key(servico_key)
    if servico_key == "mercadolivre":
        return bool(
            str(dados.get("access_token") or "").strip()
            and str(dados.get("refresh_token") or "").strip()
            and str(dados.get("app_id") or dados.get("id") or dados.get("client_id") or "").strip()
            and str(
                dados.get("client_secret")
                or dados.get("secret_key")
                or dados.get("secret")
                or ""
            ).strip()
        )
    if servico_key == "bling":
        return bool(
            str(dados.get("access_token") or dados.get("token") or "").strip()
            and str(dados.get("refresh_token") or "").strip()
            and str(
                dados.get("id")
                or dados.get("client_id")
                or dados.get("app_id")
                or ""
            ).strip()
            and str(
                dados.get("secret")
                or dados.get("client_secret")
                or dados.get("secret_key")
                or ""
            ).strip()
        )
    if servico_key == "mercadoturbo":
        return bool(
            str(dados.get("token") or dados.get("access_token") or "").strip()
        )
    return False


def _shared_sync_integracao_credencial_rank(servico_key: str, dados: Any) -> int:
    """Classifica forca recuperavel da credencial; merges nunca podem rebaixa-la."""
    if not isinstance(dados, dict):
        return 0
    servico_key = _shared_sync_servico_key(servico_key)
    if _shared_sync_integracao_oauth_completa(servico_key, dados):
        return 4

    access_token = str(
        dados.get("access_token")
        or (dados.get("token") if servico_key == "bling" else "")
        or ""
    ).strip()
    refresh_token = str(dados.get("refresh_token") or "").strip()
    api_key = str(dados.get("api_key") or dados.get("apikey") or "").strip()
    if servico_key in {"mercadolivre", "bling"} and (access_token or api_key):
        return 3

    if servico_key == "mercadolivre":
        client_id = str(
            dados.get("app_id") or dados.get("client_id") or dados.get("id") or ""
        ).strip()
        client_secret = str(
            dados.get("client_secret")
            or dados.get("secret_key")
            or dados.get("secret")
            or ""
        ).strip()
        if refresh_token and client_id and client_secret:
            return 2
    elif servico_key == "bling":
        client_id = str(
            dados.get("id") or dados.get("client_id") or dados.get("app_id") or ""
        ).strip()
        client_secret = str(
            dados.get("secret")
            or dados.get("client_secret")
            or dados.get("secret_key")
            or ""
        ).strip()
        if refresh_token and client_id and client_secret:
            return 2

    return 1 if _shared_sync_integracao_tem_dados(dados) else 0


def _shared_sync_integracao_tem_dados(dados: Any) -> bool:
    if not isinstance(dados, dict):
        return _shared_sync_valor_preenchido(dados)
    ignoradas = {
        "connected",
        "status",
        "motivo",
        "oauth_invalid",
        "shared_without_oauth_tokens",
        "_sync_version",
        "_sync_updated_at",
    }
    return any(
        str(chave or "").strip().lower() not in ignoradas
        and _shared_sync_valor_preenchido(valor)
        for chave, valor in dados.items()
    )


def _shared_sync_ml_legacy_identidade_compativel(
    bloco_a: Any,
    bloco_b: Any,
) -> bool:
    if not isinstance(bloco_a, dict) or not isinstance(bloco_b, dict):
        return True
    user_a = str(bloco_a.get("user_id") or "").strip()
    user_b = str(bloco_b.get("user_id") or "").strip()
    if user_a and user_b:
        return user_a == user_b
    if bool(user_a) != bool(user_b):
        return _shared_sync_oauth_payload_relevante(
            bloco_a,
            incluir_user_id=False,
            servico_key="mercadolivre",
        ) == _shared_sync_oauth_payload_relevante(
            bloco_b,
            incluir_user_id=False,
            servico_key="mercadolivre",
        )
    return _shared_sync_oauth_payload_relevante(
        bloco_a,
        incluir_user_id=False,
        servico_key="mercadolivre",
    ) == _shared_sync_oauth_payload_relevante(
        bloco_b,
        incluir_user_id=False,
        servico_key="mercadolivre",
    )


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
    completa = _shared_sync_integracao_oauth_completa(servico_key, saida)
    if completa:
        saida["oauth_invalid"] = False
        saida["connected"] = True
        saida["status"] = "conectado"
        saida["motivo"] = ""
        saida["shared_without_oauth_tokens"] = False
    return saida

def _shared_sync_merge_integracao_loja(
    atual: Any,
    remoto: Any,
    add_only: bool = False,
    servico_key: str = "",
    *,
    base: Any = None,
    strict_oauth_conflicts: bool = False,
) -> Any:
    if not isinstance(remoto, dict):
        return atual if _shared_sync_valor_preenchido(atual) else remoto
    if not isinstance(atual, dict):
        return _shared_sync_normalizar_integracao_conectada(servico_key, _shared_sync_json_clone(remoto))

    servico_key = _shared_sync_servico_key(servico_key)
    atual_ts = _shared_sync_timestamp(atual.get("updated_at"))
    remoto_ts = _shared_sync_timestamp(remoto.get("updated_at"))
    if servico_key in {"mercadolivre", "bling"} and _shared_sync_oauth_em_andamento(atual):
        # O bundle remove estados OAuth transitorios por seguranca. Um pull
        # durante o navegador aberto nao pode invalidar o callback local.
        if strict_oauth_conflicts:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ha uma autenticacao OAuth em andamento nesta maquina. "
                    "Conclua ou cancele a conexao antes de sincronizar."
                ),
            )
        return _shared_sync_normalizar_integracao_conectada(
            servico_key,
            _shared_sync_json_clone(atual),
        )
    if servico_key == "mercadolivre":
        atual_user_id = str(atual.get("user_id") or "").strip()
        remoto_user_id = str(remoto.get("user_id") or "").strip()
        if atual_user_id and remoto_user_id and atual_user_id != remoto_user_id:
            logger.warning(
                "[SHARED-SYNC] Conflito de conta Mercado Livre na mesma loja; "
                "a configuracao local foi preservada."
            )
            if strict_oauth_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A mesma loja aponta para contas Mercado Livre diferentes. "
                        "Revise a integracao antes de sincronizar novamente."
                    ),
                )
            return _shared_sync_normalizar_integracao_conectada(
                servico_key,
                _shared_sync_json_clone(atual),
            )
        if bool(atual_user_id) != bool(remoto_user_id):
            payload_atual_sem_id = _shared_sync_oauth_payload_relevante(
                atual,
                incluir_user_id=False,
                servico_key=servico_key,
            )
            payload_remoto_sem_id = _shared_sync_oauth_payload_relevante(
                remoto,
                incluir_user_id=False,
                servico_key=servico_key,
            )
            if payload_atual_sem_id == payload_remoto_sem_id:
                # Registros antigos nao tinham seller/user_id. Se todo o bloco
                # OAuth restante e identico, o ID ausente pode ser enriquecido
                # sem trocar a conta nem misturar credenciais.
                if remoto_user_id:
                    enriquecido = _shared_sync_json_clone(remoto)
                    return _shared_sync_normalizar_integracao_conectada(
                        servico_key,
                        enriquecido,
                    )
                return _shared_sync_normalizar_integracao_conectada(
                    servico_key,
                    _shared_sync_json_clone(atual),
                )

    if servico_key in {"mercadolivre", "bling", "mercadoturbo"}:
        atual_tem_dados = _shared_sync_integracao_tem_dados(atual)
        remoto_tem_dados = _shared_sync_integracao_tem_dados(remoto)
        if (
            _shared_sync_integracao_credencial_rank(servico_key, atual)
            > _shared_sync_integracao_credencial_rank(servico_key, remoto)
        ):
            logger.warning(
                "[SHARED-SYNC] Bloco OAuth remoto incompleto; a credencial "
                "local completa foi preservada."
            )
            if strict_oauth_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A sincronizacao tentou substituir uma integracao completa "
                        "por credenciais parciais. Conclua a autenticacao na outra "
                        "maquina ou descarte a configuracao incompleta."
                    ),
                )
            return _shared_sync_normalizar_integracao_conectada(
                servico_key,
                _shared_sync_json_clone(atual),
            )
        if servico_key == "mercadolivre" and atual_tem_dados and remoto_tem_dados:
            atual_user_id = str(atual.get("user_id") or "").strip()
            remoto_user_id = str(remoto.get("user_id") or "").strip()
            payload_atual = _shared_sync_oauth_payload_relevante(
                atual,
                incluir_user_id=False,
                servico_key=servico_key,
            )
            payload_remoto = _shared_sync_oauth_payload_relevante(
                remoto,
                incluir_user_id=False,
                servico_key=servico_key,
            )
            identidade_nao_confirmada = (
                (atual_user_id and not remoto_user_id)
                or (
                    (not atual_user_id or not remoto_user_id)
                    and payload_atual != payload_remoto
                )
            )
            if identidade_nao_confirmada:
                logger.warning(
                    "[SHARED-SYNC] Identidade da conta Mercado Livre nao pode ser "
                    "confirmada; a configuracao local foi preservada."
                )
                if strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Nao foi possivel confirmar a identidade da conta Mercado Livre. "
                            "Revise a integracao antes de sincronizar novamente."
                        ),
                    )
                return _shared_sync_normalizar_integracao_conectada(
                    servico_key,
                    _shared_sync_json_clone(atual),
                )
        if not atual_tem_dados and remoto_tem_dados:
            local_desconectada = atual.get("connected") is False
            if local_desconectada:
                payload_base = (
                    _shared_sync_oauth_payload_relevante(
                        base,
                        servico_key=servico_key,
                    )
                    if isinstance(base, dict)
                    else None
                )
                payload_remoto = _shared_sync_oauth_payload_relevante(
                    remoto,
                    servico_key=servico_key,
                )
                if payload_base is not None and payload_remoto == payload_base:
                    escolhido = atual
                elif payload_base is None and strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "A integracao foi desconectada localmente enquanto as "
                            "credenciais remotas tambem mudaram. Revise antes de sincronizar."
                        ),
                    )
                else:
                    escolhido = atual
            else:
                escolhido = remoto
        else:
            payload_atual = _shared_sync_oauth_payload_relevante(
                atual,
                servico_key=servico_key,
            )
            payload_remoto = _shared_sync_oauth_payload_relevante(
                remoto,
                servico_key=servico_key,
            )
            if (
                atual_tem_dados
                and remoto_tem_dados
                and payload_atual != payload_remoto
            ):
                payload_base = (
                    _shared_sync_oauth_payload_relevante(
                        base,
                        servico_key=servico_key,
                    )
                    if isinstance(base, dict)
                    else None
                )
                local_mudou = payload_base is None or payload_atual != payload_base
                remoto_mudou = payload_base is None or payload_remoto != payload_base
                if payload_base is None and strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Nao ha uma base causal para escolher entre duas "
                            "credenciais OAuth divergentes. Revise a integracao."
                        ),
                    )
                if payload_base is None:
                    escolhido = atual
                elif not local_mudou and remoto_mudou:
                    escolhido = remoto
                elif local_mudou and not remoto_mudou:
                    escolhido = atual
                else:
                    logger.warning(
                        "[SHARED-SYNC] Conflito causal entre blocos OAuth; "
                        "nenhuma credencial foi escolhida automaticamente."
                    )
                    if strict_oauth_conflicts:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "As credenciais OAuth foram alteradas nas duas maquinas. "
                                "Revise a integracao antes de sincronizar novamente."
                            ),
                        )
                    escolhido = atual
            else:
                escolhido = atual
        return _shared_sync_normalizar_integracao_conectada(
            servico_key,
            _shared_sync_json_clone(escolhido),
        )

    merged = dict(atual)
    remoto_mais_novo = remoto_ts > atual_ts

    for chave, valor in remoto.items():
        atual_valor = merged.get(chave)
        if chave == "connected":
            merged[chave] = bool(atual_valor) or bool(valor)
            continue
        if chave == "updated_at":
            if remoto_mais_novo:
                merged[chave] = valor
            elif "updated_at" not in merged and _shared_sync_valor_preenchido(valor):
                merged[chave] = valor
            continue
        if not _shared_sync_valor_preenchido(atual_valor) and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor
        elif not add_only and remoto_mais_novo and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor
    return _shared_sync_normalizar_integracao_conectada(servico_key, merged)


def _shared_sync_merge_json_add_only(atual: Any, remoto: Any) -> Any:
    if isinstance(atual, dict) and isinstance(remoto, dict):
        merged = _shared_sync_json_clone(atual)
        for chave, valor in remoto.items():
            if chave not in merged:
                merged[chave] = _shared_sync_json_clone(valor)
            else:
                merged[chave] = _shared_sync_merge_json_add_only(merged[chave], valor)
        return merged
    if isinstance(atual, list) and isinstance(remoto, list):
        merged = _shared_sync_json_clone(atual)
        for item in remoto:
            if item not in merged:
                merged.append(_shared_sync_json_clone(item))
        return merged
    if _shared_sync_valor_preenchido(atual):
        return _shared_sync_json_clone(atual)
    return _shared_sync_json_clone(remoto)


def _shared_sync_legacy_loja_entry(
    payload: dict,
    loja_key: Any,
    *,
    exact_only: bool = False,
) -> tuple[Optional[str], Any]:
    if loja_key in payload:
        return str(loja_key), payload.get(loja_key)
    if exact_only:
        return None, None
    normalizada = _shared_sync_loja_key(loja_key)
    candidatas = [
        chave
        for chave in payload
        if _shared_sync_loja_key(chave) == normalizada
    ]
    if len(candidatas) == 1:
        chave = candidatas[0]
        return str(chave), payload.get(chave)
    return None, None


def _shared_sync_legacy_service_entry(
    loja: Any,
    servico: Any,
) -> tuple[Optional[str], Any]:
    if not isinstance(loja, dict):
        return None, None
    servico_key = _shared_sync_servico_key(servico)
    candidatas = [
        chave
        for chave in loja
        if _shared_sync_servico_key(chave) == servico_key
    ]
    if len(candidatas) == 1:
        chave = candidatas[0]
        return str(chave), loja.get(chave)
    return None, None


def _shared_sync_merge_integracoes_legacy_bytes(
    target_abs: str,
    remoto_bytes: bytes,
    *,
    base_bytes: Optional[bytes] = None,
    strict_oauth_conflicts: bool = False,
) -> bytes:
    remoto = _shared_sync_json_from_bytes(remoto_bytes, "integracoes.json")
    if not isinstance(remoto, dict):
        raise HTTPException(status_code=502, detail="integracoes.json remoto nao contem um objeto.")
    _shared_sync_validar_integracoes_legacy(
        remoto,
        origem="integracoes.json remoto",
        status_code=502,
    )
    atual: dict = {}
    base: Optional[dict] = None
    if os.path.exists(target_abs):
        with open(target_abs, "rb") as arquivo:
            atual = _shared_sync_json_from_bytes(arquivo.read(), "integracoes.json atual")
        if not isinstance(atual, dict):
            raise HTTPException(status_code=500, detail="integracoes.json local nao contem um objeto.")
        _shared_sync_validar_integracoes_legacy(
            atual,
            origem="integracoes.json local",
            status_code=409,
        )
    if isinstance(base_bytes, bytes) and base_bytes:
        base_payload = _shared_sync_json_from_bytes(
            base_bytes,
            "integracoes.json base",
        )
        if not isinstance(base_payload, dict):
            raise HTTPException(
                status_code=409,
                detail="integracoes.json base nao contem um objeto.",
            )
        base = base_payload
        _shared_sync_validar_integracoes_legacy(
            base,
            origem="integracoes.json base",
            status_code=409,
        )
    # O formato legado e loja -> servico -> bloco. Credenciais OAuth precisam
    # permanecer atomicas. A escolha e causal por bloco inteiro: nunca se
    # combinam access token, refresh token e client secret de snapshots distintos.
    merged = _shared_sync_json_clone(atual)
    for loja_key, loja_remota in remoto.items():
        chave_loja_atual, loja_atual = _shared_sync_legacy_loja_entry(
            merged,
            loja_key,
            exact_only=True,
        )
        if chave_loja_atual is None:
            chave_normalizada, _loja_normalizada = _shared_sync_legacy_loja_entry(
                merged,
                loja_key,
            )
            if chave_normalizada is not None:
                if strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Os snapshots legados usam nomes diferentes para a "
                            "mesma loja normalizada. Revise as lojas antes de "
                            "sincronizar."
                        ),
                    )
                # Sem identidade estavel, nomes crus diferentes nao autorizam
                # combinar credenciais. Preserve os dois registros separados.
                merged[loja_key] = _shared_sync_json_clone(loja_remota)
                continue
        if chave_loja_atual is None:
            merged[loja_key] = _shared_sync_json_clone(loja_remota)
            continue
        if not isinstance(loja_atual, dict) or not isinstance(loja_remota, dict):
            if not _shared_sync_valor_preenchido(loja_atual):
                merged[chave_loja_atual] = _shared_sync_json_clone(loja_remota)
            elif loja_atual != loja_remota and strict_oauth_conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Os dados legados da mesma loja divergiram entre as maquinas. "
                        "Revise a integracao antes de sincronizar novamente."
                    ),
                )
            continue
        loja_merged = _shared_sync_json_clone(loja_atual)
        for servico, bloco_remoto in loja_remota.items():
            servico_key = _shared_sync_servico_key(servico)
            chave_atual, bloco_atual = _shared_sync_legacy_service_entry(
                loja_merged,
                servico,
            )
            if chave_atual is None:
                loja_merged[servico_key or str(servico)] = _shared_sync_json_clone(
                    bloco_remoto
                )
                continue
            downgrade_oauth = (
                servico_key in {"mercadolivre", "bling", "mercadoturbo"}
                and isinstance(bloco_atual, dict)
                and isinstance(bloco_remoto, dict)
                and _shared_sync_integracao_credencial_rank(
                    servico_key,
                    bloco_atual,
                )
                > _shared_sync_integracao_credencial_rank(
                    servico_key,
                    bloco_remoto,
                )
            )
            identidade_ml_preferida = None
            if (
                servico_key == "mercadolivre"
                and isinstance(bloco_atual, dict)
                and isinstance(bloco_remoto, dict)
            ):
                if not _shared_sync_ml_legacy_identidade_compativel(
                    bloco_atual,
                    bloco_remoto,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "As integracoes legadas apontam para contas Mercado Livre "
                            "diferentes. Revise a conta antes de sincronizar."
                        ),
                    )
                atual_user_id = str(bloco_atual.get("user_id") or "").strip()
                remoto_user_id = str(bloco_remoto.get("user_id") or "").strip()
                if bool(atual_user_id) != bool(remoto_user_id):
                    identidade_ml_preferida = (
                        bloco_atual if atual_user_id else bloco_remoto
                    )
            if identidade_ml_preferida is not None:
                escolhido = identidade_ml_preferida
            elif downgrade_oauth:
                if strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "A sincronizacao tentou substituir uma integracao "
                            "legada completa por credenciais parciais. Revise a conta."
                        ),
                    )
                escolhido = bloco_atual
            elif _shared_sync_oauth_payload_relevante(
                bloco_atual,
                servico_key=servico_key,
            ) == _shared_sync_oauth_payload_relevante(
                bloco_remoto,
                servico_key=servico_key,
            ):
                escolhido = bloco_atual
            else:
                chave_loja_base, loja_base = _shared_sync_legacy_loja_entry(
                    base or {},
                    loja_key,
                    exact_only=True,
                )
                chave_servico_base, bloco_base = _shared_sync_legacy_service_entry(
                    loja_base,
                    servico,
                )
                if base is not None:
                    base_existe = (
                        chave_loja_base is not None
                        and chave_servico_base is not None
                    )
                    bloco_base_semantico = _shared_sync_oauth_payload_relevante(
                        bloco_base,
                        servico_key=servico_key,
                    )
                    local_mudou = not base_existe or (
                        _shared_sync_oauth_payload_relevante(
                            bloco_atual,
                            servico_key=servico_key,
                        )
                        != bloco_base_semantico
                    )
                    remoto_mudou = not base_existe or (
                        _shared_sync_oauth_payload_relevante(
                            bloco_remoto,
                            servico_key=servico_key,
                        )
                        != bloco_base_semantico
                    )
                    if not local_mudou and remoto_mudou:
                        escolhido = bloco_remoto
                    elif local_mudou and not remoto_mudou:
                        escolhido = bloco_atual
                    elif not local_mudou and not remoto_mudou:
                        escolhido = bloco_atual
                    elif strict_oauth_conflicts:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "A mesma integracao legada foi alterada nas duas maquinas. "
                                "Revise a conta antes de sincronizar novamente."
                            ),
                        )
                    else:
                        escolhido = bloco_atual
                else:
                    if strict_oauth_conflicts:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "Nao ha uma base causal para escolher entre duas "
                                "integracoes legadas divergentes. Revise a conta."
                            ),
                        )
                    escolhido = bloco_atual
            if chave_atual != servico_key:
                loja_merged.pop(chave_atual, None)
            loja_merged[servico_key or str(servico)] = (
                _shared_sync_normalizar_integracao_conectada(
                    servico_key,
                    _shared_sync_json_clone(escolhido),
                )
                if isinstance(escolhido, dict)
                else _shared_sync_json_clone(escolhido)
            )
        merged[chave_loja_atual] = loja_merged
    return json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8")


def _shared_sync_tombstone_key(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    tipo = str(item.get("type") or "").strip().lower()
    store_id = str(item.get("store_id") or "").strip()
    servico = _shared_sync_servico_key(item.get("service"))
    if tipo == "store" and store_id and not servico:
        return f"store:{store_id}:"
    if tipo == "integration" and store_id and servico:
        return f"integration:{store_id}:{servico}"
    return ""


def _shared_sync_validar_tombstones(
    payload: Any,
    *,
    origem: str,
    status_code: int,
) -> None:
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise HTTPException(
            status_code=status_code,
            detail=f"{origem} nao contem uma lista valida.",
        )
    chaves: set[str] = set()
    for item in payload:
        chave = _shared_sync_tombstone_key(item)
        if not chave:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem uma exclusao sem identidade valida.",
            )
        explicita = str(item.get("key") or "").strip()
        if explicita and explicita != chave:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem uma chave de exclusao contraditoria.",
            )
        if chave in chaves:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem exclusoes duplicadas.",
            )
        chaves.add(chave)
        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem versao de exclusao invalida.",
            )
        deleted_at = str(item.get("deleted_at") or "").strip()
        restored_at = str(item.get("restored_at") or "").strip()
        if bool(deleted_at) == bool(restored_at):
            raise HTTPException(
                status_code=status_code,
                detail=(
                    f"{origem} precisa registrar exatamente uma exclusao "
                    "ou restauracao."
                ),
            )
        timestamp = deleted_at or restored_at
        try:
            instante = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem horario de exclusao invalido.",
            ) from exc
        deslocamento = instante.utcoffset()
        if (
            instante.tzinfo is None
            or deslocamento is None
            or deslocamento.total_seconds() != 0
        ):
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem horario de exclusao fora de UTC.",
            )


def _shared_sync_merge_tombstones_integracoes_bytes(
    target_abs: str,
    remoto_bytes: bytes,
    *,
    base_bytes: Optional[bytes] = None,
) -> bytes:
    remoto = _shared_sync_json_from_bytes(remoto_bytes, "lojas_sync_tombstones.json")
    _shared_sync_validar_tombstones(
        remoto,
        origem="lojas_sync_tombstones.json remoto",
        status_code=502,
    )
    atual: list = []
    base: list = []
    if os.path.exists(target_abs):
        with open(target_abs, "rb") as arquivo:
            atual = _shared_sync_json_from_bytes(
                arquivo.read(),
                "lojas_sync_tombstones.json atual",
            )
        _shared_sync_validar_tombstones(
            atual,
            origem="lojas_sync_tombstones.json local",
            status_code=500,
        )
    if isinstance(base_bytes, bytes) and base_bytes:
        base = _shared_sync_json_from_bytes(
            base_bytes,
            "lojas_sync_tombstones.json base",
        )
        _shared_sync_validar_tombstones(
            base,
            origem="lojas_sync_tombstones.json base",
            status_code=409,
        )
    base_por_chave = {
        _shared_sync_tombstone_key(item): item
        for item in base
    }

    merged = [_shared_sync_json_clone(item) for item in atual]
    indice = {
        _shared_sync_tombstone_key(item): pos
        for pos, item in enumerate(merged)
    }
    for item in remoto:
        chave = _shared_sync_tombstone_key(item)
        pos = indice.get(chave)
        if pos is None:
            merged.append(_shared_sync_json_clone(item))
            indice[chave] = len(merged) - 1
            continue
        atual_item = merged[pos]
        atual_restaurado = bool(str(atual_item.get("restored_at") or "").strip())
        remoto_restaurado = bool(str(item.get("restored_at") or "").strip())
        if atual_restaurado != remoto_restaurado:
            base_item = base_por_chave.get(chave)
            if isinstance(base_item, dict):
                local_mudou = atual_item != base_item
                remoto_mudou = item != base_item
                if not local_mudou and remoto_mudou:
                    merged[pos] = _shared_sync_json_clone(item)
                    continue
                if local_mudou and not remoto_mudou:
                    continue
            raise HTTPException(
                status_code=409,
                detail=(
                    "A exclusao e a recriacao da mesma loja/integracao "
                    "divergiram entre as maquinas. Revise o cadastro antes de "
                    "sincronizar novamente."
                ),
            )
        atual_version = _shared_sync_timestamp(atual_item.get("version"))
        remoto_version = _shared_sync_timestamp(item.get("version"))
        atual_deleted = str(atual_item.get("deleted_at") or "")
        remoto_deleted = str(item.get("deleted_at") or "")
        if remoto_version > atual_version or (
            remoto_version == atual_version and remoto_deleted > atual_deleted
        ):
            merged[pos] = _shared_sync_json_clone(item)
    return json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8")


def _shared_sync_merge_nomes_anteriores_loja(
    atual: dict,
    remoto: dict,
    nome_atual: Any,
) -> list[str]:
    """Preserve the stable alias history for one durable store identity."""

    candidatos: list[Any] = []
    for loja in (atual or {}, remoto or {}):
        aliases = loja.get("nomes_anteriores")
        if isinstance(aliases, (list, tuple)):
            candidatos.extend(aliases)
        elif _shared_sync_valor_preenchido(aliases):
            candidatos.append(aliases)

    # Whichever side loses the current-name decision still contributes its
    # previous name.  This is essential when a newer remote rename wins.
    candidatos.extend(((atual or {}).get("nome"), (remoto or {}).get("nome")))

    nome_atual_key = str(nome_atual or "").strip().casefold()
    vistos: set[str] = set()
    aliases_unidos: list[str] = []
    for candidato in candidatos:
        alias = str(candidato or "").strip()
        alias_key = alias.casefold()
        if not alias or alias_key == nome_atual_key or alias_key in vistos:
            continue
        vistos.add(alias_key)
        aliases_unidos.append(alias)
    return aliases_unidos


def _shared_sync_merge_loja_integracoes(
    atual: dict,
    remoto: dict,
    add_only: bool = False,
    *,
    base: Optional[dict] = None,
    strict_oauth_conflicts: bool = False,
) -> dict:
    merged = dict(_shared_sync_json_clone(atual or {}))
    atual_store_id = _shared_sync_loja_store_id(atual)
    remoto_store_id = _shared_sync_loja_store_id(remoto)
    mesma_identidade_duravel = bool(atual_store_id and remoto_store_id and atual_store_id == remoto_store_id)
    remoto_mais_novo = mesma_identidade_duravel and _shared_sync_remote_store_is_newer(atual, remoto)
    for chave, valor in (remoto or {}).items():
        if chave == "integracoes":
            continue
        if chave == "store_id":
            if not atual_store_id and remoto_store_id:
                merged[chave] = remoto_store_id
            continue
        if (
            isinstance(base, dict)
            and chave in base
            and not str(chave or "").startswith("_sync_")
        ):
            valor_base = base.get(chave)
            valor_local = merged.get(chave)
            local_mudou = valor_local != valor_base
            remoto_mudou = valor != valor_base
            if not local_mudou and remoto_mudou:
                merged[chave] = _shared_sync_json_clone(valor)
            elif local_mudou and remoto_mudou and valor_local != valor:
                if strict_oauth_conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Os dados da mesma loja foram alterados nas duas maquinas. "
                            "Revise a loja antes de sincronizar novamente."
                        ),
                    )
            continue
        if (
            not isinstance(base, dict)
            and strict_oauth_conflicts
            and _shared_sync_valor_preenchido(merged.get(chave))
            and _shared_sync_valor_preenchido(valor)
            and merged.get(chave) != valor
            and not str(chave or "").startswith("_sync_")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Os dados da mesma loja divergiram sem uma base causal. "
                    "Revise a loja antes de sincronizar novamente."
                ),
            )
        if chave == "nome" and mesma_identidade_duravel and _shared_sync_valor_preenchido(valor):
            if remoto_mais_novo:
                merged[chave] = valor
            continue
        if chave in {"_sync_version", "_sync_updated_at"} and remoto_mais_novo:
            merged[chave] = valor
            continue
        if (
            not add_only
            and remoto_mais_novo
            and _shared_sync_valor_preenchido(valor)
        ):
            merged[chave] = valor
        elif not _shared_sync_valor_preenchido(merged.get(chave)) and _shared_sync_valor_preenchido(valor):
            merged[chave] = valor

    if mesma_identidade_duravel:
        aliases_unidos = _shared_sync_merge_nomes_anteriores_loja(
            atual,
            remoto,
            merged.get("nome"),
        )
        if aliases_unidos or "nomes_anteriores" in merged or "nomes_anteriores" in (remoto or {}):
            merged["nomes_anteriores"] = aliases_unidos

    integracoes = merged.setdefault("integracoes", {})
    if not isinstance(integracoes, dict):
        integracoes = {}
        merged["integracoes"] = integracoes

    for servico, dados in ((remoto or {}).get("integracoes") or {}).items():
        servico_key = _shared_sync_servico_key(servico)
        base_integracoes = (
            base.get("integracoes")
            if isinstance(base, dict) and isinstance(base.get("integracoes"), dict)
            else {}
        )
        base_dados = next(
            (
                valor
                for chave, valor in base_integracoes.items()
                if _shared_sync_servico_key(chave) == servico_key
            ),
            None,
        )
        chave_local = next(
            (
                chave
                for chave in integracoes
                if _shared_sync_servico_key(chave) == servico_key
            ),
            None,
        )
        dados_locais = integracoes.get(chave_local) if chave_local is not None else None
        merged_integracao = _shared_sync_merge_integracao_loja(
            dados_locais,
            dados,
            add_only=add_only,
            servico_key=servico_key,
            base=base_dados,
            strict_oauth_conflicts=strict_oauth_conflicts,
        )
        if chave_local is not None and chave_local != servico_key:
            integracoes.pop(chave_local, None)
        integracoes[servico_key] = merged_integracao
    return merged

def _shared_sync_merge_loja_integracoes_authoritative(atual: dict, remoto: dict) -> dict:
    """Apply a snapshot without allowing an older durable clock to regress it."""

    remoto_tem_clock = any(
        chave in (remoto or {})
        for chave in ("_sync_version", "_sync_updated_at")
    )
    remoto_loja_vence = (
        not remoto_tem_clock
        or _shared_sync_remote_store_is_newer(atual, remoto)
    )
    merged = _shared_sync_json_clone(remoto if remoto_loja_vence else atual)
    aliases = _shared_sync_merge_nomes_anteriores_loja(
        atual,
        remoto,
        merged.get("nome"),
    )
    if aliases or "nomes_anteriores" in atual or "nomes_anteriores" in remoto:
        merged["nomes_anteriores"] = aliases

    atuais_integracoes = (
        atual.get("integracoes")
        if isinstance(atual.get("integracoes"), dict)
        else {}
    )
    remotas_integracoes = (
        remoto.get("integracoes")
        if isinstance(remoto.get("integracoes"), dict)
        else {}
    )
    # Store and integration clocks are independent.  A newer rename must not
    # erase an OAuth connection merely because that service was omitted from
    # the store snapshot.  Exact integration tombstones own disconnection.
    integracoes = _shared_sync_json_clone(atuais_integracoes)
    for servico, dados_remotos in remotas_integracoes.items():
        servico_key = _shared_sync_servico_key(servico)
        dados_atuais = atuais_integracoes.get(servico_key)
        if not isinstance(dados_atuais, dict):
            dados_atuais = atuais_integracoes.get(servico)
        if not isinstance(dados_remotos, dict):
            continue
        remoto_integracao_tem_clock = any(
            chave in dados_remotos
            for chave in ("_sync_version", "_sync_updated_at")
        )
        remoto_integracao_vence = (
            not isinstance(dados_atuais, dict)
            or (
                remoto_integracao_tem_clock
                and isinstance(dados_atuais, dict)
                and _shared_sync_remote_integration_is_newer(
                    dados_atuais,
                    dados_remotos,
                )
            )
        )
        if remoto_integracao_vence:
            integracoes[servico_key] = _shared_sync_normalizar_integracao_conectada(
                servico_key,
                _shared_sync_json_clone(dados_remotos),
            )
        elif isinstance(dados_atuais, dict):
            integracoes[servico_key] = _shared_sync_json_clone(dados_atuais)
        if servico_key != servico:
            integracoes.pop(servico, None)
    merged["integracoes"] = integracoes
    return merged

def _shared_sync_resumo_lojas_integracoes(lojas: list[dict]) -> dict:
    identidades = set()
    conectadas = set()
    for loja in lojas or []:
        if not isinstance(loja, dict):
            continue
        loja_key = _shared_sync_loja_identity_key(loja)
        if loja_key:
            identidades.add(loja_key)
        integracoes = loja.get("integracoes") if isinstance(loja.get("integracoes"), dict) else {}
        for servico, dados in (integracoes or {}).items():
            if not isinstance(dados, dict) or not dados.get("connected"):
                continue
            servico_key = _shared_sync_servico_key(servico)
            conectadas.add(f"{loja_key}:{servico_key}" if loja_key else servico_key)
    return {"lojas": identidades, "conectadas": conectadas}


def _shared_sync_loja_equivalente_para_push(remota: dict, locais: list[dict]) -> Optional[dict]:
    remoto_store_id = _shared_sync_loja_store_id(remota)
    if remoto_store_id:
        por_id = [
            loja
            for loja in locais
            if _shared_sync_loja_store_id(loja) == remoto_store_id
        ]
        if len(por_id) == 1:
            return por_id[0]
    nome_key = _shared_sync_loja_key((remota or {}).get("nome"))
    if not nome_key:
        return None
    por_nome = []
    for loja in locais:
        local_store_id = _shared_sync_loja_store_id(loja)
        if remoto_store_id and local_store_id:
            continue
        if _shared_sync_loja_key((loja or {}).get("nome")) == nome_key:
            por_nome.append(loja)
    return por_nome[0] if len(por_nome) == 1 else None


def _shared_sync_integracao_remota_presente(
    local: dict,
    servico: str,
    remoto: dict,
    *,
    permitir_atualizacao_oauth: bool = False,
) -> bool:
    integracoes = local.get("integracoes") if isinstance(local.get("integracoes"), dict) else {}
    servico_key = _shared_sync_servico_key(servico)
    local_dados = next(
        (
            dados
            for chave, dados in integracoes.items()
            if _shared_sync_servico_key(chave) == servico_key
        ),
        None,
    )
    if not isinstance(local_dados, dict):
        return False
    if servico_key == "mercadolivre":
        local_user_id = str(local_dados.get("user_id") or "").strip()
        remoto_user_id = str((remoto or {}).get("user_id") or "").strip()
        if remoto_user_id and local_user_id != remoto_user_id:
            return False
        if not remoto_user_id and (
            _shared_sync_oauth_payload_relevante(
                local_dados,
                incluir_user_id=False,
                servico_key=servico_key,
            )
            != _shared_sync_oauth_payload_relevante(
                remoto,
                incluir_user_id=False,
                servico_key=servico_key,
            )
        ):
            return False
    if servico_key in {"mercadolivre", "bling", "mercadoturbo"}:
        if (
            _shared_sync_integracao_credencial_rank(servico_key, remoto)
            > _shared_sync_integracao_credencial_rank(servico_key, local_dados)
        ):
            return False
        if _shared_sync_oauth_payload_relevante(
            local_dados,
            incluir_user_id=servico_key != "mercadolivre",
            servico_key=servico_key,
        ) != _shared_sync_oauth_payload_relevante(
            remoto,
            incluir_user_id=servico_key != "mercadolivre",
            servico_key=servico_key,
        ) and not permitir_atualizacao_oauth:
            return False
    return True

def _shared_sync_lojas_config_from_bundle(bundle: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            data = zf.read("files/lojas_config.json")
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Snapshot de Lojas e integracoes sem lojas_config.json.",
        ) from exc
    payload = _shared_sync_json_from_bytes(data, "lojas_config.json")
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=409,
            detail="lojas_config.json do snapshot nao contem uma lista.",
        )
    return payload


def _shared_sync_integracoes_legacy_from_bundle(bundle: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            data = zf.read("files/integracoes.json")
    except KeyError:
        return {}
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Snapshot de Lojas e integracoes invalido.",
        ) from exc
    payload = _shared_sync_json_from_bytes(data, "integracoes.json")
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=409,
            detail="integracoes.json do snapshot nao contem um objeto.",
        )
    return payload


def _shared_sync_validar_integracoes_legacy(
    payload: dict,
    *,
    origem: str,
    status_code: int,
) -> None:
    nomes: set[str] = set()
    for loja_nome, integracoes in payload.items():
        nome_key = _shared_sync_loja_key(loja_nome)
        if not nome_key:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem loja legada sem nome.",
            )
        if nome_key in nomes:
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem nomes legados duplicados.",
            )
        nomes.add(nome_key)
        if not isinstance(integracoes, dict):
            raise HTTPException(
                status_code=status_code,
                detail=f"{origem} contem integracoes legadas em formato invalido.",
            )
        servicos: set[str] = set()
        for servico, bloco in integracoes.items():
            servico_key = _shared_sync_servico_key(servico)
            if not servico_key or servico_key in servicos:
                raise HTTPException(
                    status_code=status_code,
                    detail=f"{origem} contem servico legado ambiguo.",
                )
            servicos.add(servico_key)
            if not isinstance(bloco, dict):
                raise HTTPException(
                    status_code=status_code,
                    detail=f"{origem} contem bloco legado em formato invalido.",
                )
            _shared_sync_validar_aliases_integracao(
                servico_key,
                bloco,
                status_code=status_code,
                origem=origem,
            )


def _shared_sync_tombstones_from_bundle(bundle: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
            data = zf.read("files/lojas_sync_tombstones.json")
    except KeyError:
        return []
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Snapshot de Lojas e integracoes invalido.",
        ) from exc
    payload = _shared_sync_json_from_bytes(data, "lojas_sync_tombstones.json")
    _shared_sync_validar_tombstones(
        payload,
        origem="lojas_sync_tombstones.json do snapshot",
        status_code=409,
    )
    return payload


def _shared_sync_tombstones_preservados(
    locais: list[dict],
    remotos: list[dict],
    *,
    permitir_atualizacao: bool = False,
) -> bool:
    _shared_sync_validar_tombstones(
        locais,
        origem="Tombstones locais",
        status_code=409,
    )
    _shared_sync_validar_tombstones(
        remotos,
        origem="Tombstones remotos",
        status_code=409,
    )
    locais_por_chave = {
        _shared_sync_tombstone_key(item): item
        for item in locais
        if _shared_sync_tombstone_key(item)
    }
    for remoto in remotos:
        chave = _shared_sync_tombstone_key(remoto)
        if not chave:
            continue
        local = locais_por_chave.get(chave)
        if not isinstance(local, dict):
            return False
        local_restaurado = bool(str(local.get("restored_at") or "").strip())
        remoto_restaurado = bool(str(remoto.get("restored_at") or "").strip())
        if local_restaurado != remoto_restaurado:
            if permitir_atualizacao:
                continue
            return False
        if local_restaurado and remoto_restaurado:
            continue
        local_version = _shared_sync_timestamp(local.get("version"))
        remoto_version = _shared_sync_timestamp(remoto.get("version"))
        if local_version < remoto_version:
            return False
        if (
            local_version == remoto_version
            and str(local.get("deleted_at") or "") < str(remoto.get("deleted_at") or "")
        ):
            return False
    return True


def _shared_sync_exclusao_local_e_causal(
    local: Any,
    remoto: Any,
) -> bool:
    """Autoriza delete somente quando ele sucede o evento remoto conhecido."""
    if not isinstance(local, dict):
        return False
    if not str(local.get("deleted_at") or "").strip():
        return False
    if str(local.get("restored_at") or "").strip():
        return False
    if not isinstance(remoto, dict):
        return True

    local_version = int(local.get("version") or 0)
    remoto_version = int(remoto.get("version") or 0)
    if str(remoto.get("restored_at") or "").strip():
        return local_version > remoto_version
    remoto_deleted_at = str(remoto.get("deleted_at") or "").strip()
    return bool(
        remoto_deleted_at
        and local_version >= remoto_version
        and _shared_sync_timestamp(local.get("deleted_at"))
        >= _shared_sync_timestamp(remoto_deleted_at)
    )


def _shared_sync_json_preserva_remoto(
    local: Any,
    remoto: Any,
    *,
    permitir_atualizacao: bool,
) -> bool:
    """Confirma que o novo snapshot nao omite dados legados ja publicados."""
    if isinstance(remoto, dict):
        if not isinstance(local, dict):
            return not _shared_sync_valor_preenchido(remoto)
        for chave, valor_remoto in remoto.items():
            if chave not in local:
                if _shared_sync_valor_preenchido(valor_remoto):
                    return False
                continue
            if not _shared_sync_json_preserva_remoto(
                local[chave],
                valor_remoto,
                permitir_atualizacao=permitir_atualizacao,
            ):
                return False
        return True
    if isinstance(remoto, list):
        if not isinstance(local, list):
            return not remoto
        return all(item in local for item in remoto)
    if not _shared_sync_valor_preenchido(remoto):
        return True
    return bool(permitir_atualizacao or local == remoto)


def _shared_sync_integracoes_legacy_preserva_oauth_completo(
    local: dict,
    remoto: dict,
) -> bool:
    """Impede que chaves vazias disfarcem downgrade do formato legado."""
    for loja_key, loja_remota in (remoto or {}).items():
        if not isinstance(loja_remota, dict):
            continue
        _chave_loja, loja_local = _shared_sync_legacy_loja_entry(
            local or {},
            loja_key,
        )
        for servico, bloco_remoto in loja_remota.items():
            servico_key = _shared_sync_servico_key(servico)
            remoto_rank = _shared_sync_integracao_credencial_rank(
                servico_key,
                bloco_remoto,
            )
            if remoto_rank <= 0:
                continue
            _chave_servico, bloco_local = _shared_sync_legacy_service_entry(
                loja_local,
                servico,
            )
            if servico_key == "mercadolivre":
                if not _shared_sync_ml_legacy_identidade_compativel(
                    bloco_local,
                    bloco_remoto,
                ):
                    return False
                remoto_user_id = str(
                    ((bloco_remoto or {}).get("user_id") or "")
                    if isinstance(bloco_remoto, dict)
                    else ""
                ).strip()
                local_user_id = str(
                    ((bloco_local or {}).get("user_id") or "")
                    if isinstance(bloco_local, dict)
                    else ""
                ).strip()
                if remoto_user_id:
                    if local_user_id != remoto_user_id:
                        return False
            if _shared_sync_integracao_credencial_rank(
                servico_key,
                bloco_local,
            ) < remoto_rank:
                return False
    return True


def _shared_sync_loja_preserva_campos_remotos(
    local: dict,
    remoto: dict,
    *,
    permitir_atualizacao: bool,
) -> bool:
    for chave, valor_remoto in remoto.items():
        chave_texto = str(chave or "")
        if (
            chave_texto == "integracoes"
            or chave_texto == "store_id"
            or chave_texto.startswith("_sync_")
        ):
            continue
        if not _shared_sync_valor_preenchido(valor_remoto):
            continue
        if chave not in local or not _shared_sync_valor_preenchido(local.get(chave)):
            return False
        if local.get(chave) != valor_remoto and not permitir_atualizacao:
            return False
    return True


def _shared_sync_validar_push_lojas_integracoes(
    bundle_id: str,
    bundle: bytes,
    key_context: Optional[dict] = None,
    base_snapshot_id: str = "",
    return_guard_revision: bool = False,
    client_id: str = "",
) -> Any:
    from backend.services.shared_sync_remote import (
        _shared_sync_guard_expectation_from_meta,
        _shared_sync_obter_bundle_remoto_para_guard,
    )

    try:
        remoto_resultado = _shared_sync_obter_bundle_remoto_para_guard(
            bundle_id,
            key_context=key_context,
        )
    except HTTPException as exc:
        logger.warning("[SHARED-SYNC] Nao foi possivel validar regressao de lojas_integracoes: %s", exc.detail)
        raise HTTPException(
            status_code=409,
            detail=(
                "Envio de Lojas e integracoes bloqueado porque o snapshot remoto "
                "nao pôde ser validado com seguranca. Importe os dados remotos e tente novamente."
            ),
        ) from exc
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Nao foi possivel validar regressao de lojas_integracoes: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Envio de Lojas e integracoes bloqueado porque o snapshot remoto "
                "nao pôde ser validado com seguranca. Tente novamente."
            ),
        ) from exc
    try:
        lojas_locais = _shared_sync_lojas_config_from_bundle(bundle)
        _shared_sync_validar_identidades_lojas(
            lojas_locais,
            origem="Snapshot local",
            status_code=409,
        )
        integracoes_legadas_locais = _shared_sync_integracoes_legacy_from_bundle(bundle)
        _shared_sync_validar_integracoes_legacy(
            integracoes_legadas_locais,
            origem="Snapshot local",
            status_code=409,
        )
        tombstones_locais = _shared_sync_tombstones_from_bundle(bundle)
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Snapshot local de lojas_integracoes invalido: %s", exc)
        raise HTTPException(
            status_code=409,
            detail=(
                "Envio de Lojas e integracoes bloqueado porque o snapshot local "
                "nao pôde ser validado com seguranca."
            ),
        ) from exc

    if remoto_resultado is None:
        return None
    remoto_bundle, _meta = remoto_resultado
    expected_snapshot_id = str(
        (_meta or {}).get("snapshot_id")
        or (_meta or {}).get("id")
        or bundle_id
    ).strip()
    base_causal_atual = bool(
        str(base_snapshot_id or "").strip()
        and str(base_snapshot_id or "").strip() == expected_snapshot_id
    )

    try:
        lojas_remotas = _shared_sync_lojas_config_from_bundle(remoto_bundle)
        _shared_sync_validar_identidades_lojas(
            lojas_remotas,
            origem="Snapshot remoto",
            status_code=409,
        )
        integracoes_legadas_remotas = _shared_sync_integracoes_legacy_from_bundle(
            remoto_bundle
        )
        _shared_sync_validar_integracoes_legacy(
            integracoes_legadas_remotas,
            origem="Snapshot remoto",
            status_code=409,
        )
        tombstones_remotos = _shared_sync_tombstones_from_bundle(remoto_bundle)
    except Exception as exc:
        logger.warning("[SHARED-SYNC] Falha ao comparar lojas_integracoes antes do push: %s", exc)
        raise HTTPException(
            status_code=409,
            detail=(
                "Envio de Lojas e integracoes bloqueado porque os snapshots nao "
                "puderam ser comparados com seguranca."
            ),
        ) from exc

    tombstones_locais_ativos = {
        _shared_sync_tombstone_key(item): item
        for item in tombstones_locais
        if _shared_sync_tombstone_key(item)
        and str(item.get("deleted_at") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    tombstones_remotos_por_chave = {
        _shared_sync_tombstone_key(item): item
        for item in tombstones_remotos
        if _shared_sync_tombstone_key(item)
    }
    contradicao_tombstone_local = False
    for loja_local_atual in lojas_locais:
        store_id_local = _shared_sync_loja_store_id(loja_local_atual)
        if not store_id_local:
            continue
        if f"store:{store_id_local}:" in tombstones_locais_ativos:
            contradicao_tombstone_local = True
            break
        integracoes_locais = (
            loja_local_atual.get("integracoes")
            if isinstance(loja_local_atual.get("integracoes"), dict)
            else {}
        )
        for servico_local, dados_locais in integracoes_locais.items():
            servico_key_local = _shared_sync_servico_key(servico_local)
            chave_tombstone = (
                f"integration:{store_id_local}:{servico_key_local}"
                if servico_key_local
                else ""
            )
            if chave_tombstone not in tombstones_locais_ativos:
                continue
            if bool(
                isinstance(dados_locais, dict)
                and (
                    bool(dados_locais.get("connected"))
                    or _shared_sync_integracao_tem_dados(dados_locais)
                )
            ):
                contradicao_tombstone_local = True
                break
        if contradicao_tombstone_local:
            break

    perda_lojas = 0
    perda_conectadas = 0
    for loja_remota in lojas_remotas:
        store_id_remoto = (
            _shared_sync_loja_store_id(loja_remota)
            or _shared_sync_store_id_deterministico(
                client_id,
                loja_remota.get("nome"),
            )
        )
        loja_local = _shared_sync_loja_equivalente_para_push(loja_remota, lojas_locais)
        if loja_local is None:
            chave_tombstone_store = (
                f"store:{store_id_remoto}:" if store_id_remoto else ""
            )
            tombstone_local = tombstones_locais_ativos.get(
                chave_tombstone_store
            )
            if (
                base_causal_atual
                and tombstone_local
                and _shared_sync_exclusao_local_e_causal(
                    tombstone_local,
                    tombstones_remotos_por_chave.get(chave_tombstone_store),
                )
            ):
                continue
            perda_lojas += 1
            integracoes_remotas = (
                loja_remota.get("integracoes")
                if isinstance(loja_remota.get("integracoes"), dict)
                else {}
            )
            perda_conectadas += sum(
                1
                for dados in integracoes_remotas.values()
                if isinstance(dados, dict) and _shared_sync_integracao_tem_dados(dados)
            )
            continue
        if not _shared_sync_loja_preserva_campos_remotos(
            loja_local,
            loja_remota,
            permitir_atualizacao=base_causal_atual,
        ):
            perda_lojas += 1
        integracoes_remotas = (
            loja_remota.get("integracoes")
            if isinstance(loja_remota.get("integracoes"), dict)
            else {}
        )
        for servico, dados in integracoes_remotas.items():
            if not isinstance(dados, dict) or not _shared_sync_integracao_tem_dados(dados):
                continue
            servico_key = _shared_sync_servico_key(servico)
            integracoes_locais = (
                loja_local.get("integracoes")
                if isinstance(loja_local.get("integracoes"), dict)
                else {}
            )
            dados_locais = next(
                (
                    valor
                    for chave, valor in integracoes_locais.items()
                    if _shared_sync_servico_key(chave) == servico_key
                ),
                None,
            )
            if not _shared_sync_integracao_remota_presente(
                loja_local,
                servico,
                dados,
                permitir_atualizacao_oauth=base_causal_atual,
            ):
                chave_tombstone_integracao = (
                    f"integration:{store_id_remoto}:{servico_key}"
                    if store_id_remoto and servico_key
                    else ""
                )
                tombstone_local = tombstones_locais_ativos.get(
                    chave_tombstone_integracao
                )
                bloco_local_sem_payload = bool(
                    not isinstance(dados_locais, dict)
                    or (
                        not bool(dados_locais.get("connected"))
                        and not _shared_sync_integracao_tem_dados(dados_locais)
                    )
                )
                if (
                    base_causal_atual
                    and tombstone_local
                    and bloco_local_sem_payload
                    and _shared_sync_exclusao_local_e_causal(
                        tombstone_local,
                        tombstones_remotos_por_chave.get(
                            chave_tombstone_integracao
                        ),
                    )
                ):
                    continue
                perda_conectadas += 1
    regressao_lojas = perda_lojas > 0
    regressao_conexoes = perda_conectadas > 0
    regressao_legado = (
        not _shared_sync_integracoes_legacy_preserva_oauth_completo(
            integracoes_legadas_locais,
            integracoes_legadas_remotas,
        )
        or not _shared_sync_json_preserva_remoto(
            integracoes_legadas_locais,
            integracoes_legadas_remotas,
            permitir_atualizacao=base_causal_atual,
        )
    )
    # Desde a politica que mantem exclusoes apenas na maquina que as criou,
    # pacotes novos omitem ``lojas_sync_tombstones.json`` por contrato. Um
    # snapshot remoto produzido antes dessa politica ainda pode conter o
    # arquivo. Exigir que o pacote novo o replique cria um bloqueio impossivel
    # de resolver: o pull ignora os tombstones remotos e o push volta a omiti-los.
    # A protecao continua comparando lojas, conexoes, OAuth e o legado; apenas o
    # historico de exclusao remoto deixa de ser uma exigencia de exportacao.
    if (
        contradicao_tombstone_local
        or regressao_lojas
        or regressao_conexoes
        or regressao_legado
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Push de lojas_integracoes bloqueado: o snapshot local parece remover lojas "
                "ou conexoes Bling/Mercado Livre existentes no remoto, inclusive dados "
                "legados ou historico de exclusao. Importe os dados "
                "remotos nesta maquina antes de enviar novamente."
            ),
        )
    if return_guard_revision:
        return _shared_sync_guard_expectation_from_meta(_meta, bundle_id)
    return expected_snapshot_id

def _shared_sync_merge_lojas_integracoes_bytes(
    target_abs: str,
    remoto_bytes: bytes,
    add_only: bool = False,
    *,
    current_bytes: Optional[bytes] = None,
    base_bytes: Optional[bytes] = None,
    local_tombstones_bytes: Optional[bytes] = None,
    incoming_tombstones_bytes: Optional[bytes] = None,
    base_tombstones_bytes: Optional[bytes] = None,
    strict_oauth_conflicts: bool = False,
    client_id: str = "",
) -> bytes:
    remoto_payload = _shared_sync_json_from_bytes(remoto_bytes, "lojas_config.json")
    if not isinstance(remoto_payload, list):
        raise HTTPException(
            status_code=502,
            detail="lojas_config.json remoto nao contem uma lista.",
        )
    remoto_lojas = _shared_sync_materializar_store_ids_legados(
        remoto_payload,
        client_id,
    )
    _shared_sync_validar_identidades_lojas(
        remoto_lojas,
        origem="Snapshot remoto",
        status_code=502,
    )
    atual_lojas: list[dict] = []
    base_lojas: list[dict] = []
    base_payload_original: list[dict] = []
    atual_payload_original: list[dict] = []
    local_tombstones: list[dict] = []
    incoming_tombstones: list[dict] = []
    base_tombstones: list[dict] = []

    if isinstance(local_tombstones_bytes, bytes) and local_tombstones_bytes:
        tombstones_payload = _shared_sync_json_from_bytes(
            local_tombstones_bytes,
            "lojas_sync_tombstones.json local",
        )
        _shared_sync_validar_tombstones(
            tombstones_payload,
            origem="lojas_sync_tombstones.json local",
            status_code=409,
        )
        local_tombstones = tombstones_payload
    if isinstance(incoming_tombstones_bytes, bytes) and incoming_tombstones_bytes:
        incoming_payload = _shared_sync_json_from_bytes(
            incoming_tombstones_bytes,
            "lojas_sync_tombstones.json remoto",
        )
        _shared_sync_validar_tombstones(
            incoming_payload,
            origem="lojas_sync_tombstones.json remoto",
            status_code=502,
        )
        incoming_tombstones = incoming_payload
    if isinstance(base_tombstones_bytes, bytes) and base_tombstones_bytes:
        base_tombstones_payload = _shared_sync_json_from_bytes(
            base_tombstones_bytes,
            "lojas_sync_tombstones.json base",
        )
        _shared_sync_validar_tombstones(
            base_tombstones_payload,
            origem="lojas_sync_tombstones.json base",
            status_code=409,
        )
        base_tombstones = base_tombstones_payload
    incoming_tombstones_por_chave = {
        _shared_sync_tombstone_key(item): item
        for item in incoming_tombstones
        if _shared_sync_tombstone_key(item)
        and not str(item.get("restored_at") or "").strip()
    }
    base_tombstones_por_chave = {
        _shared_sync_tombstone_key(item): item
        for item in base_tombstones
        if _shared_sync_tombstone_key(item)
    }
    tombstones_lojas_ativos = {
        str(item.get("store_id") or "").strip()
        for item in local_tombstones
        if str(item.get("type") or "").strip().lower() == "store"
        and str(item.get("store_id") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    tombstones_integracoes_ativos = {
        (
            str(item.get("store_id") or "").strip(),
            _shared_sync_servico_key(item.get("service")),
        )
        for item in local_tombstones
        if str(item.get("type") or "").strip().lower() == "integration"
        and str(item.get("store_id") or "").strip()
        and _shared_sync_servico_key(item.get("service"))
        and not str(item.get("restored_at") or "").strip()
    }
    if (
        (tombstones_lojas_ativos or tombstones_integracoes_ativos)
        and any(not _shared_sync_loja_store_id(loja) for loja in remoto_lojas)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Snapshot legado sem store_id nao pode ser conciliado com "
                "exclusoes atuais. A sincronizacao foi bloqueada para impedir "
                "ressurreicao ou troca de identidade de loja."
            ),
        )

    def filtrar_tombstones(lojas: list[dict]) -> list[dict]:
        filtradas: list[dict] = []
        for loja in lojas:
            store_id = _shared_sync_loja_store_id(loja)
            if store_id and store_id in tombstones_lojas_ativos:
                continue
            copia = _shared_sync_json_clone(loja)
            integracoes = (
                copia.get("integracoes")
                if isinstance(copia.get("integracoes"), dict)
                else {}
            )
            copia["integracoes"] = {
                servico: dados
                for servico, dados in integracoes.items()
                if (
                    store_id,
                    _shared_sync_servico_key(servico),
                ) not in tombstones_integracoes_ativos
            }
            filtradas.append(copia)
        return filtradas

    if isinstance(base_bytes, bytes) and base_bytes:
        base_payload = _shared_sync_json_from_bytes(
            base_bytes,
            "lojas_config.json base",
        )
        if not isinstance(base_payload, list):
            raise HTTPException(
                status_code=409,
                detail="lojas_config.json base nao contem uma lista.",
            )
        base_payload_original = base_payload
        base_lojas = _shared_sync_materializar_store_ids_legados(
            base_payload,
            client_id,
        )
        _shared_sync_validar_identidades_lojas(
            base_lojas,
            origem="Snapshot base",
            status_code=409,
        )

    if isinstance(current_bytes, bytes):
        atual_payload = _shared_sync_json_from_bytes(
            current_bytes,
            "lojas_config.json atual",
        )
        if not isinstance(atual_payload, list):
            raise HTTPException(
                status_code=409,
                detail="lojas_config.json local nao contem uma lista.",
            )
        atual_payload_original = atual_payload
        atual_lojas = _shared_sync_materializar_store_ids_legados(
            atual_payload,
            client_id,
        )
        _shared_sync_validar_identidades_lojas(
            atual_lojas,
            origem="Configuracao local",
            status_code=409,
        )
    elif os.path.exists(target_abs):
        with open(target_abs, "rb") as f:
            atual_payload = _shared_sync_json_from_bytes(f.read(), "lojas_config.json atual")
        if not isinstance(atual_payload, list):
            raise HTTPException(
                status_code=409,
                detail="lojas_config.json local nao contem uma lista.",
            )
        atual_payload_original = atual_payload
        atual_lojas = _shared_sync_materializar_store_ids_legados(
            atual_payload,
            client_id,
        )
        _shared_sync_validar_identidades_lojas(
            atual_lojas,
            origem="Configuracao local",
            status_code=409,
        )

    _shared_sync_validar_nomes_legados_entre_fontes(
        client_id,
        remoto_payload,
        base_payload_original,
        atual_payload_original,
    )

    remocoes_causais_lojas: set[str] = set()
    remocoes_causais_integracoes: set[tuple[str, str]] = set()
    for loja_atual in atual_lojas:
        store_id_atual = _shared_sync_loja_store_id(loja_atual)
        if store_id_atual and store_id_atual in tombstones_lojas_ativos:
            chave_tombstone = f"store:{store_id_atual}:"
            loja_base = _shared_sync_loja_equivalente_para_push(
                loja_atual,
                base_lojas,
            ) if base_lojas else None
            evento_entrante = incoming_tombstones_por_chave.get(chave_tombstone)
            if (
                isinstance(evento_entrante, dict)
                and evento_entrante != base_tombstones_por_chave.get(chave_tombstone)
                and isinstance(loja_base, dict)
                and _shared_sync_loja_semanticamente_igual(
                    loja_atual,
                    loja_base,
                )
            ):
                remocoes_causais_lojas.add(store_id_atual)
                continue
            raise HTTPException(
                status_code=409,
                detail=(
                    "Uma exclusao recebida conflita com uma loja ainda presente "
                    "nesta maquina. A sincronizacao foi cancelada para preservar "
                    "a conta local."
                ),
            )
        integracoes_atuais = (
            loja_atual.get("integracoes")
            if isinstance(loja_atual.get("integracoes"), dict)
            else {}
        )
        for servico, dados in integracoes_atuais.items():
            servico_key = _shared_sync_servico_key(servico)
            if (
                store_id_atual,
                servico_key,
            ) not in tombstones_integracoes_ativos:
                continue
            contradiz = bool(
                isinstance(dados, dict)
                and (
                    bool(dados.get("connected"))
                    or _shared_sync_oauth_payload_relevante(
                        dados,
                        servico_key=servico_key,
                    )
                )
            )
            if contradiz:
                chave_tombstone = (
                    f"integration:{store_id_atual}:{servico_key}"
                )
                loja_base = _shared_sync_loja_equivalente_para_push(
                    loja_atual,
                    base_lojas,
                ) if base_lojas else None
                _chave_base, dados_base = _shared_sync_legacy_service_entry(
                    (loja_base or {}).get("integracoes")
                    if isinstance(loja_base, dict)
                    else {},
                    servico_key,
                )
                evento_entrante = incoming_tombstones_por_chave.get(
                    chave_tombstone
                )
                if (
                    isinstance(evento_entrante, dict)
                    and evento_entrante
                    != base_tombstones_por_chave.get(chave_tombstone)
                    and _shared_sync_integracao_semanticamente_igual(
                        dados,
                        dados_base,
                        servico_key,
                    )
                ):
                    remocoes_causais_integracoes.add(
                        (store_id_atual, servico_key)
                    )
                    continue
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Uma exclusao recebida conflita com uma integracao ainda "
                        "configurada nesta maquina. A sincronizacao foi cancelada "
                        "para preservar a conta local."
                    ),
                )

    if remocoes_causais_lojas or remocoes_causais_integracoes:
        atuais_filtradas: list[dict] = []
        for loja_atual in atual_lojas:
            store_id_atual = _shared_sync_loja_store_id(loja_atual)
            if store_id_atual in remocoes_causais_lojas:
                continue
            copia = _shared_sync_json_clone(loja_atual)
            integracoes_copia = (
                copia.get("integracoes")
                if isinstance(copia.get("integracoes"), dict)
                else {}
            )
            copia["integracoes"] = {
                servico: dados
                for servico, dados in integracoes_copia.items()
                if (
                    store_id_atual,
                    _shared_sync_servico_key(servico),
                ) not in remocoes_causais_integracoes
            }
            atuais_filtradas.append(copia)
        atual_lojas = atuais_filtradas

    # A exclusao monotona governa os dados entrantes. O estado atual nunca e
    # removido sem uma base causal que prove que a exclusao veio depois dele.
    # bundles v1/inconsistentes que ainda carreguem a loja junto do tombstone.
    remoto_lojas = filtrar_tombstones(remoto_lojas)

    if atual_lojas and not remoto_lojas:
        logger.warning("[SHARED-SYNC] Pacote remoto de lojas vazio ignorado para preservar integracoes locais.")
        merged = atual_lojas
    else:
        merged = [_shared_sync_json_clone(loja) for loja in atual_lojas]
        indice_store_id = {
            _shared_sync_loja_store_id(loja): idx
            for idx, loja in enumerate(merged)
            if isinstance(loja, dict) and _shared_sync_loja_store_id(loja)
        }
        indices_legado_nome: dict[str, list[int]] = {}
        for idx, loja in enumerate(merged):
            if not isinstance(loja, dict) or _shared_sync_loja_store_id(loja):
                continue
            chave_nome = _shared_sync_loja_key(loja.get("nome"))
            if chave_nome:
                indices_legado_nome.setdefault(chave_nome, []).append(idx)

        for loja_remota in remoto_lojas:
            remoto_store_id = _shared_sync_loja_store_id(loja_remota)
            chave_nome = _shared_sync_loja_key(loja_remota.get("nome"))
            if remoto_store_id:
                # Once a durable identity exists, names never participate in
                # matching.  This preserves homonymous stores and renames.
                idx = indice_store_id.get(remoto_store_id)
            else:
                # Name fallback is intentionally limited to unambiguous
                # legacy records on both sides, and it is always exact after
                # normalization (never substring/fuzzy matching).
                candidatos = indices_legado_nome.get(chave_nome, []) if chave_nome else []
                idx = candidatos[0] if len(candidatos) == 1 else None
            if idx is None:
                if remoto_store_id and remoto_store_id in tombstones_lojas_ativos:
                    logger.warning(
                        "[SHARED-SYNC] Loja remota ignorada por exclusao local mais nova."
                    )
                    continue
                loja_adicionada = _shared_sync_json_clone(loja_remota)
                integracoes_adicionadas = (
                    loja_adicionada.get("integracoes")
                    if isinstance(loja_adicionada.get("integracoes"), dict)
                    else {}
                )
                loja_adicionada["integracoes"] = {
                    servico: dados
                    for servico, dados in integracoes_adicionadas.items()
                    if (
                        remoto_store_id,
                        _shared_sync_servico_key(servico),
                    ) not in tombstones_integracoes_ativos
                }
                merged.append(loja_adicionada)
                novo_idx = len(merged) - 1
                if remoto_store_id:
                    indice_store_id.setdefault(remoto_store_id, novo_idx)
                elif chave_nome:
                    indices_legado_nome.setdefault(chave_nome, []).append(novo_idx)
                continue
            loja_remota_filtrada = _shared_sync_json_clone(loja_remota)
            store_id_tombstone = remoto_store_id or _shared_sync_loja_store_id(
                merged[idx]
            )
            integracoes_remotas = (
                loja_remota_filtrada.get("integracoes")
                if isinstance(loja_remota_filtrada.get("integracoes"), dict)
                else {}
            )
            loja_remota_filtrada["integracoes"] = {
                servico: dados
                for servico, dados in integracoes_remotas.items()
                if (
                    store_id_tombstone,
                    _shared_sync_servico_key(servico),
                ) not in tombstones_integracoes_ativos
            }
            loja_base = _shared_sync_loja_equivalente_para_push(
                loja_remota_filtrada,
                base_lojas,
            ) if base_lojas else None
            merged[idx] = _shared_sync_merge_loja_integracoes(
                merged[idx],
                loja_remota_filtrada,
                add_only=add_only,
                base=loja_base,
                strict_oauth_conflicts=strict_oauth_conflicts,
            )
            merged_store_id = _shared_sync_loja_store_id(merged[idx])
            if merged_store_id:
                indice_store_id.setdefault(merged_store_id, idx)

    _shared_sync_validar_identidades_lojas(
        merged,
        origem="Resultado mesclado",
        status_code=409,
    )
    return json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8")


def _shared_sync_recuperar_backup_lojas_integracoes_bytes(
    current_bytes: bytes,
    backup_bytes: bytes,
    *,
    client_id: str = "",
    local_tombstones_bytes: Optional[bytes] = None,
) -> bytes:
    """Recupera somente lojas/servicos ausentes; nunca reescreve blocos atuais."""
    atual = _shared_sync_json_from_bytes(current_bytes, "lojas_config.json atual")
    backup = _shared_sync_json_from_bytes(backup_bytes, "lojas_config.json.bak")
    if not isinstance(atual, list) or not isinstance(backup, list):
        raise HTTPException(
            status_code=409,
            detail="Backup de lojas nao contem uma lista valida.",
        )
    _shared_sync_validar_identidades_lojas(
        atual,
        origem="Configuracao local",
        status_code=409,
    )
    _shared_sync_validar_identidades_lojas(
        backup,
        origem="Backup local",
        status_code=409,
    )
    _shared_sync_validar_nomes_legados_entre_fontes(
        client_id,
        atual,
        backup,
    )

    tombstones: list[dict] = []
    if isinstance(local_tombstones_bytes, bytes) and local_tombstones_bytes:
        payload = _shared_sync_json_from_bytes(
            local_tombstones_bytes,
            "lojas_sync_tombstones.json local",
        )
        _shared_sync_validar_tombstones(
            payload,
            origem="Tombstones locais de lojas",
            status_code=409,
        )
        tombstones = payload
    stores_excluidas = {
        str(item.get("store_id") or "").strip()
        for item in tombstones
        if str(item.get("type") or "").strip().lower() == "store"
        and str(item.get("store_id") or "").strip()
        and not str(item.get("restored_at") or "").strip()
    }
    integracoes_excluidas = {
        (
            str(item.get("store_id") or "").strip(),
            _shared_sync_servico_key(item.get("service")),
        )
        for item in tombstones
        if str(item.get("type") or "").strip().lower() == "integration"
        and str(item.get("store_id") or "").strip()
        and _shared_sync_servico_key(item.get("service"))
        and not str(item.get("restored_at") or "").strip()
    }
    if (
        (stores_excluidas or integracoes_excluidas)
        and any(not _shared_sync_loja_store_id(loja) for loja in backup)
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Backup legado sem store_id nao pode ser conciliado com "
                "exclusoes atuais. A recuperacao foi bloqueada para impedir "
                "ressurreicao ou troca de identidade de loja."
            ),
        )

    merged = [_shared_sync_json_clone(loja) for loja in atual]
    indice_store_id = {
        _shared_sync_loja_store_id(loja): idx
        for idx, loja in enumerate(merged)
        if isinstance(loja, dict) and _shared_sync_loja_store_id(loja)
    }
    indices_nome: dict[str, list[int]] = {}
    for idx, loja in enumerate(merged):
        nome_key = _shared_sync_loja_key((loja or {}).get("nome"))
        if nome_key:
            indices_nome.setdefault(nome_key, []).append(idx)

    for loja_backup in backup:
        nome_key = _shared_sync_loja_key(loja_backup.get("nome"))
        store_id_explicito = _shared_sync_loja_store_id(loja_backup)
        store_id = store_id_explicito
        if not store_id:
            store_id = _shared_sync_store_id_deterministico(
                client_id,
                loja_backup.get("nome"),
            )
        idx = indice_store_id.get(store_id) if store_id else None
        if idx is None:
            candidatos = []
            for pos in indices_nome.get(nome_key, []) if nome_key else []:
                atual_store_id = _shared_sync_loja_store_id(merged[pos])
                if store_id and atual_store_id and store_id != atual_store_id:
                    continue
                candidatos.append(pos)
            if len(candidatos) == 1:
                idx = candidatos[0]

        if (
            idx is not None
            and not store_id_explicito
            and str(merged[idx].get("nome") or "").strip()
            != str(loja_backup.get("nome") or "").strip()
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "O backup antigo usa nome ambiguo para uma loja local. "
                    "A recuperacao foi bloqueada para preservar as contas."
                ),
            )

        if idx is None:
            if store_id and store_id in stores_excluidas:
                continue
            recuperada = _shared_sync_json_clone(loja_backup)
            if store_id and not _shared_sync_loja_store_id(recuperada):
                recuperada["store_id"] = store_id
            integracoes_backup = (
                recuperada.get("integracoes")
                if isinstance(recuperada.get("integracoes"), dict)
                else {}
            )
            recuperada["integracoes"] = {
                servico: dados
                for servico, dados in integracoes_backup.items()
                if (
                    store_id,
                    _shared_sync_servico_key(servico),
                ) not in integracoes_excluidas
            }
            merged.append(recuperada)
            novo_idx = len(merged) - 1
            if store_id:
                indice_store_id[store_id] = novo_idx
            if nome_key:
                indices_nome.setdefault(nome_key, []).append(novo_idx)
            continue

        loja_atual = merged[idx]
        store_id_atual = store_id or _shared_sync_loja_store_id(loja_atual)
        integracoes_atuais = loja_atual.get("integracoes")
        if not isinstance(integracoes_atuais, dict):
            integracoes_atuais = {}
            loja_atual["integracoes"] = integracoes_atuais
        servicos_presentes = {
            _shared_sync_servico_key(servico)
            for servico in integracoes_atuais
        }
        for servico, dados in ((loja_backup.get("integracoes") or {})).items():
            servico_key = _shared_sync_servico_key(servico)
            if (
                not servico_key
                or servico_key in servicos_presentes
                or (store_id_atual, servico_key) in integracoes_excluidas
            ):
                continue
            integracoes_atuais[servico_key] = _shared_sync_json_clone(dados)
            servicos_presentes.add(servico_key)

    _shared_sync_validar_identidades_lojas(
        merged,
        origem="Recuperacao do backup local",
        status_code=409,
    )
    return json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8")

configure_shared_sync_merge_integracoes_runtime()

__all__ = [
    "configure_shared_sync_merge_integracoes_runtime",
    "_shared_sync_valor_preenchido",
    "_shared_sync_lojas_from_payload",
    "_shared_sync_loja_key",
    "_shared_sync_loja_store_id",
    "_shared_sync_loja_identity_key",
    "_shared_sync_sync_version",
    "_shared_sync_remote_store_is_newer",
    "_shared_sync_remote_integration_is_newer",
    "_shared_sync_servico_key",
    "_shared_sync_timestamp",
    "_shared_sync_integracao_revisao",
    "_shared_sync_comparar_revisao_integracao",
    "_shared_sync_oauth_em_andamento",
    "_shared_sync_oauth_payload_relevante",
    "_shared_sync_validar_identidades_lojas",
    "_shared_sync_integracao_oauth_completa",
    "_shared_sync_integracao_tem_dados",
    "_shared_sync_normalizar_integracao_conectada",
    "_shared_sync_merge_integracao_loja",
    "_shared_sync_merge_json_add_only",
    "_shared_sync_merge_integracoes_legacy_bytes",
    "_shared_sync_tombstone_key",
    "_shared_sync_validar_tombstones",
    "_shared_sync_merge_tombstones_integracoes_bytes",
    "_shared_sync_merge_nomes_anteriores_loja",
    "_shared_sync_merge_loja_integracoes",
    "_shared_sync_merge_loja_integracoes_authoritative",
    "_shared_sync_resumo_lojas_integracoes",
    "_shared_sync_loja_equivalente_para_push",
    "_shared_sync_integracao_remota_presente",
    "_shared_sync_lojas_config_from_bundle",
    "_shared_sync_validar_push_lojas_integracoes",
    "_shared_sync_merge_lojas_integracoes_bytes",
    "_shared_sync_recuperar_backup_lojas_integracoes_bytes",
]
