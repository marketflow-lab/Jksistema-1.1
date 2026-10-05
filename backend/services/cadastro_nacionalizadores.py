"""Nationalization companies shared by stores within an authenticated tenant."""

from __future__ import annotations

import inspect
import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from backend.schemas import CadastroNacionalizadorRequest
from backend.services.runtime_bridge import bind_runtime_globals


_runtime_get_tenant_id = None


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if not callable(_runtime_get_tenant_id):
        raise HTTPException(
            status_code=503,
            detail="Contexto de autenticacao do Cadastro ainda nao inicializado.",
        )
    resultado = _runtime_get_tenant_id(request, authorization)
    return await resultado if inspect.isawaitable(resultado) else resultado


def get_tenant_path(client_id: str):
    raise RuntimeError("Cadastro runtime was not configured.")


def configure_cadastro_nacionalizadores_runtime(runtime_module=None):
    runtime = bind_runtime_globals(globals(), runtime_module)
    if runtime is not None:
        provider = getattr(runtime, "get_tenant_id", None)
        if callable(provider) and provider is not get_tenant_id:
            globals()["_runtime_get_tenant_id"] = provider
        if hasattr(runtime, "get_tenant_path"):
            globals()["get_tenant_path"] = runtime.get_tenant_path
    return runtime


configure_cadastro_nacionalizadores_runtime()


_CAMPOS = ("nome_empresa", "cnpj", "endereco_empresa", "nome_contato", "telefone")
_LIMITES = {
    "nome_empresa": 240,
    "cnpj": 40,
    "endereco_empresa": 1200,
    "nome_contato": 160,
    "telefone": 80,
}
_SCHEMA = "jk.cadastro.nacionalizadores.v1"
_LOCK = threading.RLock()


def _arquivo(client_id: str) -> str:
    return os.path.join(get_tenant_path(client_id), "cadastro_nacionalizadores.json")


def _chave_nome(valor: str) -> str:
    texto = unicodedata.normalize("NFKC", valor).casefold()
    return re.sub(r"\s+", " ", texto).strip()


def _chave_cnpj(valor: str) -> str:
    texto = unicodedata.normalize("NFKC", valor).casefold()
    return re.sub(r"[\s.,/\-]", "", texto)


def _normalizar(req: CadastroNacionalizadorRequest, *, identificador: str = "", criado_em: str = "") -> dict:
    dados = req.model_dump()
    for campo in _CAMPOS:
        dados[campo] = dados[campo].replace("\x00", "").strip()
        if len(dados[campo]) > _LIMITES[campo]:
            raise HTTPException(status_code=400, detail=f"{campo} excede o limite de {_LIMITES[campo]} caracteres.")
    if not dados["nome_empresa"]:
        raise HTTPException(status_code=400, detail="Nome da empresa é obrigatório.")
    agora = datetime.now(timezone.utc).isoformat()
    return {
        "id": identificador or uuid.uuid4().hex,
        **dados,
        "criado_em": criado_em or agora,
        "atualizado_em": agora,
    }


def _carregar(client_id: str, *, pasta_tenant: str | None = None) -> list[dict]:
    arquivo = (
        os.path.join(pasta_tenant, "cadastro_nacionalizadores.json")
        if pasta_tenant is not None
        else _arquivo(client_id)
    )
    try:
        with open(arquivo, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Cadastro de nacionalizadores inválido.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA or not isinstance(payload.get("nacionalizadores"), list):
        raise HTTPException(status_code=500, detail="Cadastro de nacionalizadores inválido.")
    registros = payload["nacionalizadores"]
    ids = set()
    for registro in registros:
        if (
            not isinstance(registro, dict)
            or not isinstance(registro.get("id"), str)
            or not registro["id"].strip()
            or registro["id"] in ids
            or any(not isinstance(registro.get(campo), str) for campo in _CAMPOS)
        ):
            raise HTTPException(status_code=500, detail="Cadastro de nacionalizadores inválido.")
        ids.add(registro["id"])
    return registros


def ler_nacionalizador_por_id(
    client_id: str,
    nacionalizador_id: str | None,
    *,
    pasta_tenant: str | None = None,
) -> dict | None:
    """Read one company exclusively from the already authenticated tenant."""
    identificador = str(nacionalizador_id or "").strip()
    if not identificador:
        return None
    with _LOCK:
        registros = _carregar(client_id, pasta_tenant=pasta_tenant)
        registro = next((item for item in registros if item["id"] == identificador), None)
        return dict(registro) if registro is not None else None


def resolver_nacionalizador_lista(
    client_id: str,
    nacionalizador_id: str | None,
    *,
    pasta_tenant: str | None = None,
) -> dict[str, str]:
    """Materialize the optional company id and its historical display name."""
    identificador = str(nacionalizador_id or "").strip()
    if not identificador:
        return {"nacionalizador_id": "", "nacionalizador": ""}
    registro = ler_nacionalizador_por_id(client_id, identificador, pasta_tenant=pasta_tenant)
    if registro is None:
        raise HTTPException(status_code=400, detail="Nacionalizador não encontrado no cadastro deste cliente.")
    nome = registro["nome_empresa"].strip()
    if not nome:
        raise HTTPException(status_code=409, detail="O nacionalizador cadastrado precisa ter o nome da empresa.")
    return {"nacionalizador_id": identificador, "nacionalizador": nome}


def _salvar(client_id: str, registros: list[dict]) -> None:
    arquivo = _arquivo(client_id)
    temporario = f"{arquivo}.{uuid.uuid4().hex}.tmp"
    try:
        os.makedirs(os.path.dirname(arquivo), exist_ok=True)
        with open(temporario, "w", encoding="utf-8") as handle:
            json.dump({"schema": _SCHEMA, "nacionalizadores": registros}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporario, arquivo)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Não foi possível salvar o cadastro de nacionalizadores.") from exc
    finally:
        try:
            os.remove(temporario)
        except FileNotFoundError:
            pass


def _validar_duplicidade(registros: list[dict], novo: dict) -> None:
    nome = _chave_nome(novo["nome_empresa"])
    cnpj = _chave_cnpj(novo["cnpj"])
    if any(
        item["id"] != novo["id"]
        and (_chave_nome(item["nome_empresa"]) == nome or bool(cnpj and _chave_cnpj(item["cnpj"]) == cnpj))
        for item in registros
    ):
        raise HTTPException(status_code=409, detail="Nacionalizador já cadastrado.")


async def listar_nacionalizadores_cadastro(client_id: str = Depends(get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_listar_sync, client_id=client_id)


def _listar_sync(client_id: str):
    with _LOCK:
        registros = _carregar(client_id)
    return sorted(registros, key=lambda item: _chave_nome(item["nome_empresa"]))


async def criar_nacionalizador_cadastro(req: CadastroNacionalizadorRequest, client_id: str = Depends(get_tenant_id)):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_criar_sync, req=req, client_id=client_id)


def _criar_sync(req: CadastroNacionalizadorRequest, client_id: str):
    novo = _normalizar(req)
    with _LOCK:
        registros = _carregar(client_id)
        _validar_duplicidade(registros, novo)
        registros.append(novo)
        _salvar(client_id, registros)
    return {"success": True, "nacionalizador": novo}


async def atualizar_nacionalizador_cadastro(
    nacionalizador_id: str,
    req: CadastroNacionalizadorRequest,
    client_id: str = Depends(get_tenant_id),
):
    from backend.services.blocking_workers import run_heavy

    return await run_heavy(_atualizar_sync, nacionalizador_id=nacionalizador_id, req=req, client_id=client_id)


def _atualizar_sync(nacionalizador_id: str, req: CadastroNacionalizadorRequest, client_id: str):
    with _LOCK:
        registros = _carregar(client_id)
        indice = next((i for i, item in enumerate(registros) if item["id"] == nacionalizador_id), None)
        if indice is None:
            raise HTTPException(status_code=404, detail="Nacionalizador não encontrado.")
        atualizado = _normalizar(req, identificador=nacionalizador_id, criado_em=registros[indice].get("criado_em", ""))
        _validar_duplicidade(registros, atualizado)
        registros[indice] = atualizado
        _salvar(client_id, registros)
    return {"success": True, "nacionalizador": atualizado}


__all__ = [
    "ler_nacionalizador_por_id",
    "resolver_nacionalizador_lista",
    "listar_nacionalizadores_cadastro",
    "criar_nacionalizador_cadastro",
    "atualizar_nacionalizador_cadastro",
    "configure_cadastro_nacionalizadores_runtime",
]
