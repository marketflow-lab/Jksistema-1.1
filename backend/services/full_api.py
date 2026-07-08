"""HTTP-facing operations for the Full module."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from backend.schemas.full import FullEnvioTransitoPayload, FullEnvioTransitoUpdate
from backend.services.full import (
    _full_envio_row_to_dict,
    _full_envios_agrupar_itens,
    _full_envios_data_iso,
    _full_envios_db_path,
    _full_envios_files_dir,
    _full_envios_inserir_registro,
    _full_envios_parse_pdf,
    _full_envios_status_norm,
    _full_numero,
    _garantir_tabela_full_envios_transito,
)
from backend.services.full_calendario import calendario_comercial_full_payload
from backend.services.full_mercadolivre import (
    listar_anuncios_full_mercadolivre_payload,
    listar_lojas_full_mercadolivre_payload,
)


logger = logging.getLogger("jk_sistema")
_TENANT_DEPENDENCY = None
_listar_estoque: Callable[[str], object] | None = None


def configure_full_api_context(
    *,
    get_tenant_id_fn,
    listar_estoque_fn: Callable[[str], object] | None = None,
    logger_ref=None,
) -> None:
    global _TENANT_DEPENDENCY, _listar_estoque, logger
    _TENANT_DEPENDENCY = get_tenant_id_fn
    if listar_estoque_fn is not None:
        _listar_estoque = listar_estoque_fn
    if logger_ref is not None:
        logger = logger_ref


async def get_tenant_id(request: Request, authorization: Optional[str] = Header(default=None)):
    if _TENANT_DEPENDENCY is None:
        raise RuntimeError("Full API context was not configured.")
    result = _TENANT_DEPENDENCY(request, authorization)
    if inspect.isawaitable(result):
        return await result
    return result


async def listar_estoque_full(client_id: str = Depends(get_tenant_id)):
    """Retorna somente itens com saldo no Full."""
    if _listar_estoque is None:
        raise RuntimeError("Full API context was not configured.")
    registros = _listar_estoque(client_id)
    if inspect.isawaitable(registros):
        registros = await registros

    def _saldo_full(row: dict) -> float:
        for campo in ("saldo_full", "estoque_full", "full", "fulfillment", "quantidade_full"):
            if campo in row and row.get(campo) not in (None, ""):
                return _full_numero(row.get(campo))
        return 0.0

    return [
        {**row, "saldo_full": _saldo_full(row)}
        for row in (registros or [])
        if isinstance(row, dict) and _saldo_full(row) > 0
    ]


async def listar_lojas_full_mercadolivre(client_id: str = Depends(get_tenant_id)):
    return listar_lojas_full_mercadolivre_payload(client_id)


async def listar_anuncios_full_mercadolivre(
    loja: str,
    limite: int = 10000,
    client_id: str = Depends(get_tenant_id),
):
    return listar_anuncios_full_mercadolivre_payload(client_id, loja, limite)


async def calendario_comercial_full(
    ano: int | None = None,
    mes: int | None = None,
    client_id: str = Depends(get_tenant_id),
):
    del client_id
    return calendario_comercial_full_payload(ano, mes)


async def listar_full_envios_transito(status: str = "all", client_id: str = Depends(get_tenant_id)):
    _garantir_tabela_full_envios_transito(client_id)
    status_norm = str(status or "all").strip().lower()
    where = []
    params: list[Any] = []
    if status_norm in {"ativos", "active"}:
        where.append("ativo = 1 AND status <> 'inativo'")
    elif status_norm in {"inativos", "inactive"}:
        where.append("(ativo = 0 OR status = 'inativo')")
    elif status_norm not in {"all", "todos"}:
        where.append("status = ?")
        params.append(_full_envios_status_norm(status_norm))
    sql = "SELECT * FROM envios_transito"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date(data_envio) DESC, id DESC"

    conn = sqlite3.connect(_full_envios_db_path(client_id))
    conn.row_factory = sqlite3.Row
    try:
        rows = [_full_envio_row_to_dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    resumo: dict[str, Any] = {"total": len(rows), "unidades": 0, "por_status": {}}
    for row in rows:
        resumo["unidades"] += _full_numero(row.get("total_unidades"))
        st = str(row.get("status") or "aguardando_inicio")
        resumo["por_status"][st] = resumo["por_status"].get(st, 0) + 1
    return {"success": True, "total": len(rows), "results": rows, "resumo": resumo}


async def upload_full_envios_transito(
    files: list[UploadFile] = File(...),
    loja: str = Form(""),
    client_id: str = Depends(get_tenant_id),
):
    if not files:
        raise HTTPException(status_code=400, detail="Envie ao menos um PDF.")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Envie no maximo 20 PDFs por vez.")

    registros = []
    pasta = _full_envios_files_dir(client_id)
    for upload in files:
        nome_original = str(getattr(upload, "filename", "") or "envio.pdf").strip()
        if not nome_original.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"Arquivo nao e PDF: {nome_original}")
        conteudo = await upload.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail=f"Arquivo vazio: {nome_original}")
        try:
            parsed = await asyncio.to_thread(_full_envios_parse_pdf, nome_original, conteudo)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Nao foi possivel ler o PDF {nome_original}: {e}")

        nome_seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", nome_original)[:120] or "envio.pdf"
        nome_salvo = f"{uuid.uuid4().hex}_{nome_seguro}"
        caminho = os.path.join(pasta, nome_salvo)
        with open(caminho, "wb") as f:
            f.write(conteudo)

        parsed.update({
            "loja": str(loja or "").strip(),
            "arquivo_pdf": nome_salvo,
            "arquivo_nome": nome_original,
            "ativo": True,
        })
        registros.append(_full_envios_inserir_registro(client_id, parsed))

    return {"success": True, "total": len(registros), "results": registros}


async def criar_full_envio_transito_manual(payload: FullEnvioTransitoPayload, client_id: str = Depends(get_tenant_id)):
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    data["status"] = _full_envios_status_norm(data.get("status"))
    registro = _full_envios_inserir_registro(client_id, data)
    return {"success": True, "result": registro}


async def atualizar_full_envio_transito(envio_id: int, payload: FullEnvioTransitoUpdate, client_id: str = Depends(get_tenant_id)):
    _garantir_tabela_full_envios_transito(client_id)
    campos = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    if not campos:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar.")
    if "status" in campos and campos["status"] is not None:
        campos["status"] = _full_envios_status_norm(campos["status"])
        if campos["status"] == "inativo":
            campos["ativo"] = False
    if "data_envio" in campos and campos["data_envio"] is not None:
        campos["data_envio"] = _full_envios_data_iso(campos["data_envio"])
    if "data_recebimento" in campos and campos["data_recebimento"] is not None:
        campos["data_recebimento"] = _full_envios_data_iso(campos["data_recebimento"])
    if "itens" in campos and campos["itens"] is not None:
        itens = _full_envios_agrupar_itens([dict(item) for item in campos["itens"]])
        campos["itens_json"] = json.dumps(itens, ensure_ascii=False)
        campos["total_produtos"] = len(itens)
        if campos.get("total_unidades") is None:
            campos["total_unidades"] = sum(_full_numero(item.get("quantidade")) for item in itens)
        campos.pop("itens", None)
    if "ativo" in campos and campos["ativo"] is not None:
        campos["ativo"] = 1 if campos["ativo"] else 0
        if campos["ativo"] == 1 and campos.get("status") == "inativo":
            campos["status"] = "aguardando_inicio"
    campos["updated_at"] = datetime.now().isoformat(timespec="seconds")

    permitidos = {
        "codigo_envio", "loja", "status", "situacao_ml", "data_envio", "data_recebimento",
        "total_unidades", "total_produtos", "itens_json", "observacoes", "ativo", "updated_at",
    }
    sets = []
    params = []
    for campo, valor in campos.items():
        if campo in permitidos:
            sets.append(f"{campo} = ?")
            params.append(valor)
    if not sets:
        raise HTTPException(status_code=400, detail="Nenhum campo valido para atualizar.")
    params.append(envio_id)

    conn = sqlite3.connect(_full_envios_db_path(client_id))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE envios_transito SET {', '.join(sets)} WHERE id = ?", params)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Envio nao encontrado.")
        conn.commit()
        row = cur.execute("SELECT * FROM envios_transito WHERE id = ?", (envio_id,)).fetchone()
        return {"success": True, "result": _full_envio_row_to_dict(row)}
    finally:
        conn.close()


async def excluir_full_envio_transito(envio_id: int, client_id: str = Depends(get_tenant_id)):
    _garantir_tabela_full_envios_transito(client_id)
    conn = sqlite3.connect(_full_envios_db_path(client_id))
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT arquivo_pdf FROM envios_transito WHERE id = ?", (envio_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Envio nao encontrado.")
        arquivo_pdf = str(row[0] or "").strip()
        cur.execute("DELETE FROM envios_transito WHERE id = ?", (envio_id,))
        conn.commit()
        if arquivo_pdf:
            caminho = os.path.join(_full_envios_files_dir(client_id), arquivo_pdf)
            if os.path.exists(caminho):
                try:
                    os.remove(caminho)
                except Exception:
                    logger.warning("[FULL TRANSITO] Nao foi possivel remover PDF %s", caminho)
        return {"success": True}
    finally:
        conn.close()


async def baixar_pdf_full_envio_transito(envio_id: int, client_id: str = Depends(get_tenant_id)):
    _garantir_tabela_full_envios_transito(client_id)
    conn = sqlite3.connect(_full_envios_db_path(client_id))
    try:
        row = conn.execute("SELECT arquivo_pdf, arquivo_nome FROM envios_transito WHERE id = ?", (envio_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="PDF nao encontrado para este envio.")
    caminho = os.path.join(_full_envios_files_dir(client_id), str(row[0]))
    if not os.path.exists(caminho):
        raise HTTPException(status_code=404, detail="Arquivo PDF nao encontrado.")
    return FileResponse(caminho, media_type="application/pdf", filename=str(row[1] or row[0]))


__all__ = [
    "configure_full_api_context",
    "listar_estoque_full",
    "listar_lojas_full_mercadolivre",
    "listar_anuncios_full_mercadolivre",
    "calendario_comercial_full",
    "listar_full_envios_transito",
    "upload_full_envios_transito",
    "criar_full_envio_transito_manual",
    "atualizar_full_envio_transito",
    "excluir_full_envio_transito",
    "baixar_pdf_full_envio_transito",
]
