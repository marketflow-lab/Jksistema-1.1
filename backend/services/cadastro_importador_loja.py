"""Importer contact details scoped to a tenant and an exact store id."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException

from backend.schemas.cadastro import CadastroImportadorLojaRequest
from backend.services import cadastro_lojas_produtos as lojas


_LOCK = threading.RLock()
_CAMPOS = (
    "nome_empresa", "tax_id", "telefone", "email", "contato", "logradouro",
    "bairro", "cidade", "cep", "estado", "pais",
)
_LIMITES = {campo: 240 for campo in _CAMPOS}
_LIMITES.update(email=254, logradouro=500)


def _arquivo(client_id: str) -> str:
    return os.path.join(lojas.get_tenant_path(client_id), "cadastro_importador_loja.json")


def _carregar(client_id: str) -> dict:
    try:
        with open(_arquivo(client_id), encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Cadastro de importadores inválido.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "jk.cadastro.importador_loja.v1" or not isinstance(payload.get("lojas"), dict):
        raise HTTPException(status_code=500, detail="Cadastro de importadores inválido.")
    return payload["lojas"]


def _salvar(client_id: str, registros: dict) -> None:
    arquivo = _arquivo(client_id)
    os.makedirs(os.path.dirname(arquivo), exist_ok=True)
    temporario = f"{arquivo}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as handle:
            json.dump({"schema": "jk.cadastro.importador_loja.v1", "lojas": registros}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporario, arquivo)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)


def ler_importador_loja_por_store_id(client_id: str, store_id: str) -> dict:
    """Read only the record belonging to an already resolved store identity."""

    if not client_id or not store_id:
        return {}
    with _LOCK:
        registro = _carregar(client_id).get(store_id)
    return dict(registro) if isinstance(registro, dict) else {}


async def obter_importador_loja(store_id: str, client_id: str = Depends(lojas.get_tenant_id)):
    loja = lojas.resolver_loja_cadastro(client_id, store_id)
    with _LOCK:
        registro = _carregar(client_id).get(loja["store_id"])
    return registro or {}


async def salvar_importador_loja(
    store_id: str,
    req: CadastroImportadorLojaRequest,
    client_id: str = Depends(lojas.get_tenant_id),
):
    loja = lojas.resolver_loja_cadastro(client_id, store_id)
    dados = req.model_dump()
    for campo in _CAMPOS:
        valor = dados[campo].replace("\x00", "").strip()
        if len(valor) > _LIMITES[campo]:
            raise HTTPException(status_code=400, detail=f"{campo} excede o limite de {_LIMITES[campo]} caracteres.")
        dados[campo] = valor
    if dados["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", dados["email"]):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    dados["atualizado_em"] = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        lojas.resolver_loja_cadastro(client_id, loja["store_id"], somente_commit=True)
        registros = _carregar(client_id)
        registros[loja["store_id"]] = dados
        _salvar(client_id, registros)
    return dados
