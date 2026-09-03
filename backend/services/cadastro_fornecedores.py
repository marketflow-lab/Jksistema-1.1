"""Tenant-scoped supplier records for Cadastro."""

from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from backend.schemas import CadastroFornecedorRequest
from backend.services.runtime_bridge import bind_runtime_globals


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    raise RuntimeError("Cadastro runtime was not configured.")


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def _configure_runtime_globals(target_globals, runtime_module=None):
    runtime = bind_runtime_globals(target_globals, runtime_module)
    if runtime is not None:
        if hasattr(runtime, "get_tenant_id"):
            target_globals["get_tenant_id"] = getattr(runtime, "get_tenant_id")
        if hasattr(runtime, "get_tenant_path"):
            target_globals["get_tenant_path"] = getattr(runtime, "get_tenant_path")
    return runtime


def configure_cadastro_fornecedores_runtime(runtime_module=None):
    return _configure_runtime_globals(globals(), runtime_module)


configure_cadastro_fornecedores_runtime()


FORNECEDOR_CAMPOS = (
    "nome_empresa",
    "nome_contato",
    "endereco_empresa",
    "telefone",
    "email",
    "moeda_pagamento",
    "conta_beneficiario",
    "swift",
    "pais_regiao_beneficiario",
    "nome_beneficiario",
    "endereco_beneficiario",
    "banco_beneficiario",
    "endereco_banco",
    "codigo_banco",
    "codigo_agencia",
    "observacao_pagamento",
)
FORNECEDOR_LIMITES = {
    "nome_empresa": 240,
    "nome_contato": 160,
    "endereco_empresa": 1200,
    "telefone": 80,
    "email": 254,
    "moeda_pagamento": 12,
    "conta_beneficiario": 120,
    "swift": 32,
    "pais_regiao_beneficiario": 120,
    "nome_beneficiario": 240,
    "endereco_beneficiario": 1200,
    "banco_beneficiario": 240,
    "endereco_banco": 1200,
    "codigo_banco": 40,
    "codigo_agencia": 40,
    "observacao_pagamento": 1600,
}
_FORNECEDORES_LOCK = threading.RLock()


def _arquivo_fornecedores(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "cadastro_fornecedores.json")


def _texto_fornecedor(valor, limite: int) -> str:
    texto = str(valor or "").replace("\x00", "").strip()
    if len(texto) > limite:
        raise HTTPException(status_code=400, detail=f"Campo excede o limite de {limite} caracteres.")
    return texto


def _chave_nome_fornecedor(valor: str) -> str:
    texto = unicodedata.normalize("NFKC", str(valor or "").strip()).casefold()
    return re.sub(r"\s+", " ", texto)


def _dados_request(req: CadastroFornecedorRequest) -> dict:
    if hasattr(req, "model_dump"):
        return req.model_dump()
    return req.dict()


def _normalizar_fornecedor(payload: dict, *, fornecedor_id: str = "", criado_em: str = "") -> dict:
    item = {
        campo: _texto_fornecedor((payload or {}).get(campo, ""), FORNECEDOR_LIMITES[campo])
        for campo in FORNECEDOR_CAMPOS
    }
    if not item["nome_empresa"]:
        raise HTTPException(status_code=400, detail="Nome da empresa é obrigatório.")
    if item["email"] and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", item["email"]):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    agora = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(fornecedor_id or uuid.uuid4().hex),
        **item,
        "criado_em": str(criado_em or agora),
        "atualizado_em": agora,
    }


def _carregar_fornecedores(client_id: str) -> list[dict]:
    arquivo = _arquivo_fornecedores(client_id)
    if not os.path.exists(arquivo):
        return []
    try:
        with open(arquivo, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Cadastro de fornecedores inválido.") from exc
    registros = payload.get("fornecedores", []) if isinstance(payload, dict) else payload
    if not isinstance(registros, list):
        raise HTTPException(status_code=500, detail="Cadastro de fornecedores inválido.")
    return [dict(item) for item in registros if isinstance(item, dict)]


def _salvar_fornecedores(client_id: str, fornecedores: list[dict]) -> None:
    arquivo = _arquivo_fornecedores(client_id)
    os.makedirs(os.path.dirname(arquivo), exist_ok=True)
    temporario = f"{arquivo}.{uuid.uuid4().hex}.tmp"
    payload = {"schema": "jk.cadastro.fornecedores.v1", "fornecedores": fornecedores}
    try:
        with open(temporario, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporario, arquivo)
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)


async def listar_fornecedores_cadastro(client_id: str = Depends(get_tenant_id)):
    with _FORNECEDORES_LOCK:
        fornecedores = _carregar_fornecedores(client_id)
    return sorted(fornecedores, key=lambda item: _chave_nome_fornecedor(item.get("nome_empresa", "")))


async def criar_fornecedor_cadastro(req: CadastroFornecedorRequest, client_id: str = Depends(get_tenant_id)):
    novo = _normalizar_fornecedor(_dados_request(req))
    chave_nova = _chave_nome_fornecedor(novo["nome_empresa"])
    with _FORNECEDORES_LOCK:
        fornecedores = _carregar_fornecedores(client_id)
        if any(_chave_nome_fornecedor(item.get("nome_empresa", "")) == chave_nova for item in fornecedores):
            raise HTTPException(status_code=409, detail="Fornecedor já cadastrado.")
        fornecedores.append(novo)
        fornecedores.sort(key=lambda item: _chave_nome_fornecedor(item.get("nome_empresa", "")))
        _salvar_fornecedores(client_id, fornecedores)
    return {"success": True, "fornecedor": novo}


async def atualizar_fornecedor_cadastro(
    fornecedor_id: str,
    req: CadastroFornecedorRequest,
    client_id: str = Depends(get_tenant_id),
):
    with _FORNECEDORES_LOCK:
        fornecedores = _carregar_fornecedores(client_id)
        indice = next((i for i, item in enumerate(fornecedores) if str(item.get("id", "")) == fornecedor_id), None)
        if indice is None:
            raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
        atual = fornecedores[indice]
        atualizado = _normalizar_fornecedor(
            _dados_request(req),
            fornecedor_id=fornecedor_id,
            criado_em=str(atual.get("criado_em", "")),
        )
        chave_nova = _chave_nome_fornecedor(atualizado["nome_empresa"])
        if any(
            i != indice and _chave_nome_fornecedor(item.get("nome_empresa", "")) == chave_nova
            for i, item in enumerate(fornecedores)
        ):
            raise HTTPException(status_code=409, detail="Fornecedor já cadastrado.")
        fornecedores[indice] = atualizado
        fornecedores.sort(key=lambda item: _chave_nome_fornecedor(item.get("nome_empresa", "")))
        _salvar_fornecedores(client_id, fornecedores)
    return {"success": True, "fornecedor": atualizado}


async def excluir_fornecedor_cadastro(fornecedor_id: str, client_id: str = Depends(get_tenant_id)):
    with _FORNECEDORES_LOCK:
        fornecedores = _carregar_fornecedores(client_id)
        restantes = [item for item in fornecedores if str(item.get("id", "")) != fornecedor_id]
        if len(restantes) == len(fornecedores):
            raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
        _salvar_fornecedores(client_id, restantes)
    return {"success": True, "total": len(restantes)}


__all__ = [
    "FORNECEDOR_CAMPOS",
    "listar_fornecedores_cadastro",
    "criar_fornecedor_cadastro",
    "atualizar_fornecedor_cadastro",
    "excluir_fornecedor_cadastro",
    "configure_cadastro_fornecedores_runtime",
]
