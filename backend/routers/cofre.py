"""Authenticated Cofre import and archive endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError

from backend.modules.cofre import storage

_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}




async def _read_patch(request: Request, model):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Envie os campos da edicao em JSON.")
    limit = 2 * 1024 * 1024
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(413, "Edicao maior que 2 MB.")
        raw.extend(chunk)
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=storage._json_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return model.model_validate(payload)
    except (UnicodeError, ValueError, ValidationError, RecursionError) as exc:
        raise HTTPException(422, "Campos invalidos para esta edicao.") from exc


def create_cofre_router(*, info_root: str | Path, tenant_dependency: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/cofre", tags=["cofre"])
    root = Path(info_root)

    async def invoke(operation, *args, **kwargs):
        try:
            return await run_in_threadpool(operation, root, *args, **kwargs)
        except storage.CofreError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post("/importar", name="cofre_importar")
    async def importar(request: Request, client_id: str = Depends(tenant_dependency)):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "multipart/form-data":
            raise HTTPException(415, "Envie o arquivo no campo multipart arquivo.")
        body_limit = storage.MAX_UPLOAD_BYTES + 64 * 1024
        try:
            content_length = int(request.headers.get("content-length", "0"))
            if content_length < 0:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(400, "Tamanho da requisicao invalido.") from exc
        if content_length > body_limit:
            raise HTTPException(413, "Arquivo maior que 512 MB.")
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await request.receive()
            received += len(message.get("body", b""))
            if received > body_limit:
                raise HTTPException(413, "Arquivo maior que 512 MB.")
            return message

        bounded_request = Request(request.scope, bounded_receive)
        try:
            async with bounded_request.form(max_files=1, max_fields=0) as form:
                items = list(form.multi_items())
                if len(items) != 1 or items[0][0] != "arquivo" or not isinstance(items[0][1], UploadFile):
                    raise HTTPException(400, "Envie somente um arquivo no campo arquivo.")
                upload = items[0][1]
                result = await invoke(storage.import_archive, client_id, upload.file, upload.filename or "")
                return JSONResponse(result, headers=_HEADERS)
        except StarletteHTTPException as exc:
            raise HTTPException(exc.status_code, str(exc.detail)) from exc

    @router.get("/servidores", name="cofre_servidores")
    async def servidores(client_id: str = Depends(tenant_dependency)):
        return JSONResponse(await invoke(storage.list_guilds, client_id), headers=_HEADERS)

    @router.get("/servidores/{guild_id}/canais", name="cofre_canais")
    async def canais(guild_id: str, client_id: str = Depends(tenant_dependency)):
        return JSONResponse(await invoke(storage.list_channels, client_id, guild_id), headers=_HEADERS)

    @router.get("/canais/{channel_id}/mensagens", name="cofre_mensagens")
    async def mensagens(channel_id: str, servidor_id: str = Query(..., min_length=1, max_length=128),
                        q: str = Query("", max_length=500), offset: int = Query(0, ge=0),
                        limit: int = Query(100, ge=1, le=200), excluidos: bool = Query(False),
                        client_id: str = Depends(tenant_dependency)):
        result = await invoke(storage.list_messages, client_id, servidor_id, channel_id,
                              q=q, offset=offset, limit=limit, excluidos=excluidos)
        return JSONResponse(result, headers=_HEADERS)

    @router.patch("/canais/{channel_id}/mensagens/{message_id}", name="cofre_mensagem_editar")
    async def editar_mensagem(channel_id: str, message_id: str, request: Request,
                              servidor_id: str = Query(..., min_length=1, max_length=128),
                              client_id: str = Depends(tenant_dependency)):
        patch = await _read_patch(request, storage.MessagePatch)
        result = await invoke(storage.change_message, client_id, servidor_id, channel_id, message_id, patch=patch)
        return JSONResponse(result, headers=_HEADERS)

    @router.delete("/canais/{channel_id}/mensagens/{message_id}", name="cofre_mensagem_excluir")
    async def excluir_mensagem(channel_id: str, message_id: str,
                               servidor_id: str = Query(..., min_length=1, max_length=128),
                               client_id: str = Depends(tenant_dependency)):
        result = await invoke(storage.change_message, client_id, servidor_id, channel_id, message_id, deleted=True)
        return JSONResponse(result, headers=_HEADERS)

    @router.post("/canais/{channel_id}/mensagens/{message_id}/restaurar", name="cofre_mensagem_restaurar")
    async def restaurar_mensagem(channel_id: str, message_id: str,
                                 servidor_id: str = Query(..., min_length=1, max_length=128),
                                 client_id: str = Depends(tenant_dependency)):
        result = await invoke(storage.change_message, client_id, servidor_id, channel_id, message_id, deleted=False)
        return JSONResponse(result, headers=_HEADERS)

    @router.patch("/canais/{channel_id}", name="cofre_canal_editar")
    async def editar_canal(channel_id: str, request: Request,
                          servidor_id: str = Query(..., min_length=1, max_length=128),
                          client_id: str = Depends(tenant_dependency)):
        patch = await _read_patch(request, storage.ChannelPatch)
        result = await invoke(storage.change_channel, client_id, servidor_id, channel_id, patch)
        return JSONResponse(result, headers=_HEADERS)

    @router.get("/arquivos/{guild_id}/{digest}/{filename}", name="cofre_arquivo")
    async def arquivo(guild_id: str, digest: str, filename: str,
                      client_id: str = Depends(tenant_dependency)):
        path = await invoke(storage.get_asset, client_id, guild_id, digest, filename)
        # Always download untrusted imported bytes; the UI may preview known raster types as blobs.
        return FileResponse(path, media_type="application/octet-stream", filename=filename,
                            content_disposition_type="attachment", headers={
                                **_HEADERS, "Content-Security-Policy": "sandbox; default-src 'none'"})

    return router


__all__ = ["create_cofre_router"]
