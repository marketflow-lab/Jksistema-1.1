"""Authenticated Cofre import and archive endpoints."""
from __future__ import annotations

import json
import inspect
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError

from backend.modules.cofre import posting, shared_assets, storage
from backend.modules.cofre.shared import CofreSyncUnavailable, SharedCofre

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


def create_cofre_router(*, info_root: str | Path, tenant_dependency: Callable,
                        sharing: bool = False, shared_service: SharedCofre | None = None,
                        remote_principal: Callable | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/cofre", tags=["cofre"])
    root = Path(info_root)
    shared = shared_service if shared_service is not None else SharedCofre(root) if sharing else None

    def principal(request: Request) -> dict:
        value = getattr(getattr(request, "state", None), "cofre_principal", None)
        if not isinstance(value, dict):
            raise HTTPException(401, "Entre novamente para acessar o Cofre compartilhado.")
        return value

    def sync_unavailable():
        return HTTPException(503, {"code": "cofre_sync_unavailable",
            "message": "A sincronizacao do Cofre esta indisponivel. Sua copia local foi preservada."})

    def operation_error(exc, *, cloud=False):
        if isinstance(exc, StarletteHTTPException):
            bearer = any(name.lower() == "www-authenticate" and "bearer" in value.lower()
                         for name, value in (exc.headers or {}).items())
            if cloud and not bearer and exc.status_code in {401, 403, 503}:
                return sync_unavailable()
            return exc
        if isinstance(exc, CofreSyncUnavailable) or cloud and exc.status_code in {401, 403, 503}:
            return sync_unavailable()
        return HTTPException(exc.status_code, str(exc))

    async def cloud_principal(request: Request, client_id: str, *, write=False):
        # Local policy can narrow cloud authority, never create or enlarge it.
        shared._assert_local_principal(client_id, principal(request), write=write)
        try:
            identity = principal(request) if remote_principal is None else await run_in_threadpool(remote_principal, request)
            if inspect.isawaitable(identity):
                identity = await identity
            shared._assert_principal(client_id, identity, write=write)
            return identity
        except (storage.CofreError, StarletteHTTPException) as exc:
            raise operation_error(exc, cloud=True) from exc
        except Exception:
            raise sync_unavailable() from None

    async def invoke(operation, *args, _request: Request | None = None, **kwargs):
        cloud = False
        try:
            identity = principal(_request)
            posting.require_actor(args[0], identity,
                admin=operation in {storage.import_archive, storage.change_message, storage.change_channel})
            if shared and operation in {storage.import_archive, storage.change_message, storage.change_channel}:
                identity = principal(_request)
                shared._assert_local_principal(args[0], identity, write=True)
                if shared.status(args[0], identity)["enabled"]:
                    identity = await cloud_principal(_request, args[0], write=True)
                    cloud = True
                return await run_in_threadpool(
                    getattr(shared, operation.__name__), args[0], identity, *args[1:], **kwargs
                )
            active_root = shared.read_root(args[0]) if shared else root
            if operation in {storage.list_channels, storage.list_messages}:
                kwargs["actor"] = identity
            # Originals remain available for existing local attachment URLs.
            if operation is storage.get_asset:
                active_root = shared.local_root(args[0]) if shared else root
            return await run_in_threadpool(operation, active_root, *args, **kwargs)
        except (storage.CofreError, StarletteHTTPException) as exc:
            raise operation_error(exc, cloud=cloud) from exc

    async def sharing_operation(name: str, request: Request, client_id: str):
        if shared is None:
            identity = principal(request)
            try:
                posting.require_actor(client_id, identity)
            except storage.CofreError as exc:
                raise operation_error(exc) from exc
            return JSONResponse({"success": True, "configured": False, "enabled": False,
                                 "revision": 0, "last_sync": None, "can_publish": posting.is_admin(identity),
                                 "can_post": True, "is_admin": posting.is_admin(identity),
                                 "changed": False}, headers=_HEADERS)
        identity = principal(request)
        cloud = False
        try:
            shared._assert_local_principal(client_id, identity, write=name == "publish")
            if name != "status":
                identity = await cloud_principal(request, client_id, write=name == "publish")
                cloud = True
            result = await run_in_threadpool(getattr(shared, name), client_id, identity)
            return JSONResponse(result, headers=_HEADERS)
        except (storage.CofreError, StarletteHTTPException) as exc:
            # Permission denials before cloud resolution retain their 403.
            raise operation_error(exc, cloud=cloud) from exc

    @router.get("/compartilhamento", name="cofre_compartilhamento")
    async def compartilhamento(request: Request, client_id: str = Depends(tenant_dependency)):
        return await sharing_operation("status", request, client_id)

    @router.post("/compartilhamento/sincronizar", name="cofre_sincronizar")
    async def sincronizar(request: Request, client_id: str = Depends(tenant_dependency)):
        return await sharing_operation("sync", request, client_id)

    @router.post("/compartilhamento/publicar", name="cofre_publicar")
    async def publicar(request: Request, client_id: str = Depends(tenant_dependency)):
        return await sharing_operation("publish", request, client_id)

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
                result = await invoke(storage.import_archive, client_id, upload.file, upload.filename or "",
                                      _request=request)
                return JSONResponse(result, headers=_HEADERS)
        except StarletteHTTPException as exc:
            raise HTTPException(exc.status_code, exc.detail, headers=exc.headers) from exc

    @router.get("/servidores", name="cofre_servidores")
    async def servidores(request: Request, client_id: str = Depends(tenant_dependency)):
        return JSONResponse(await invoke(storage.list_guilds, client_id, _request=request), headers=_HEADERS)

    @router.get("/servidores/{guild_id}/canais", name="cofre_canais")
    async def canais(guild_id: str, request: Request, client_id: str = Depends(tenant_dependency)):
        return JSONResponse(await invoke(storage.list_channels, client_id, guild_id, _request=request), headers=_HEADERS)

    @router.get("/canais/{channel_id}/mensagens", name="cofre_mensagens")
    async def mensagens(channel_id: str, request: Request, servidor_id: str = Query(..., min_length=1, max_length=128),
                        q: str = Query("", max_length=500), offset: int = Query(0, ge=0),
                        limit: int = Query(100, ge=1, le=200), excluidos: bool = Query(False),
                        client_id: str = Depends(tenant_dependency)):
        result = await invoke(storage.list_messages, client_id, servidor_id, channel_id,
                              q=q, offset=offset, limit=limit, excluidos=excluidos, _request=request)
        if shared:
            result = await run_in_threadpool(shared.project_messages, client_id, principal(request),
                                             servidor_id, channel_id, result)
        return JSONResponse(result, headers=_HEADERS)

    async def posting_operation(name, request, client_id, guild_id, channel_id, *args, **kwargs):
        identity = principal(request)
        try:
            posting.require_actor(client_id, identity, admin=name == "set_channel_deleted")
            if shared:
                if shared.status(client_id, identity)["enabled"]:
                    identity = await cloud_principal(request, client_id, write=name == "set_channel_deleted")
                result = await run_in_threadpool(getattr(shared, name), client_id, identity,
                                                guild_id, channel_id, *args, **kwargs)
            else:
                result = await run_in_threadpool(getattr(posting, name), root, client_id,
                                                guild_id, channel_id, *args, identity, **kwargs)
            return JSONResponse(result, headers=_HEADERS)
        except (storage.CofreError, StarletteHTTPException) as exc:
            # Worker permission decisions must stay 403; only missing cloud
            # credentials are a synchronization failure, never a JK logout.
            raise operation_error(exc) from exc

    @router.post("/canais/{channel_id}/mensagens", name="cofre_mensagem_enviar")
    async def enviar_mensagem(channel_id: str, request: Request,
                              servidor_id: str = Query(..., min_length=1, max_length=128),
                              client_id: str = Depends(tenant_dependency)):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
            raise HTTPException(415, "Envie a mensagem e os arquivos como multipart.")
        body_limit = posting.MAX_FILES * shared_assets.MAX_FILE_BYTES + 128 * 1024
        try:
            content_length = int(request.headers.get("content-length", "0"))
            if content_length < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(400, "Tamanho da requisicao invalido.") from None
        if content_length > body_limit:
            raise HTTPException(413, "Envio maior que o permitido.")
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await request.receive()
            received += len(message.get("body", b""))
            if received > body_limit:
                raise HTTPException(413, "Envio maior que o permitido.")
            return message

        try:
            async with Request(request.scope, bounded_receive).form(max_files=posting.MAX_FILES, max_fields=1) as form:
                payloads = form.getlist("payload")
                uploads = form.getlist("arquivos")
                if (len(payloads) != 1 or not isinstance(payloads[0], str)
                        or any(key not in {"payload", "arquivos"} for key in form)
                        or any(not isinstance(file, UploadFile) for file in uploads)):
                    raise HTTPException(422, "Campos invalidos para o envio.")
                try:
                    raw = payloads[0]
                    if len(raw.encode("utf-8")) > 128 * 1024:
                        raise ValueError
                    data = json.loads(raw, object_pairs_hook=storage._json_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                    payload = posting.CreateMessage.model_validate(data)
                except (ValueError, TypeError, RecursionError):
                    raise HTTPException(422, "Mensagem invalida.") from None
                if not payload.content.strip() and not payload.links and not uploads:
                    raise HTTPException(422, "Escreva uma mensagem ou envie um arquivo.")
                for file in uploads:
                    if file.size is not None and file.size > shared_assets.MAX_FILE_BYTES:
                        raise HTTPException(413, "Cada arquivo deve ter no maximo 10 MB.")
                attachments = tuple(posting.PreparedAttachment(file.file, file.filename or "", file.content_type)
                                    for file in uploads)
                return await posting_operation("create_message", request, client_id, servidor_id, channel_id,
                                               payload, attachments=attachments)
        except StarletteHTTPException as exc:
            raise HTTPException(exc.status_code, exc.detail, headers=exc.headers) from exc

    @router.patch("/canais/{channel_id}/mensagens/{message_id}", name="cofre_mensagem_editar")
    async def editar_mensagem(channel_id: str, message_id: str, request: Request,
                              servidor_id: str = Query(..., min_length=1, max_length=128),
                              client_id: str = Depends(tenant_dependency)):
        patch = await _read_patch(request, storage.MessagePatch)
        result = await invoke(storage.change_message, client_id, servidor_id, channel_id, message_id,
                              patch=patch, _request=request)
        return JSONResponse(result, headers=_HEADERS)

    @router.delete("/canais/{channel_id}/mensagens/{message_id}", name="cofre_mensagem_excluir")
    async def excluir_mensagem(channel_id: str, message_id: str, request: Request,
                               servidor_id: str = Query(..., min_length=1, max_length=128),
                               client_id: str = Depends(tenant_dependency)):
        return await posting_operation("set_message_deleted", request, client_id, servidor_id, channel_id,
                                       message_id, deleted=True)

    @router.post("/canais/{channel_id}/mensagens/{message_id}/restaurar", name="cofre_mensagem_restaurar")
    async def restaurar_mensagem(channel_id: str, message_id: str, request: Request,
                                 servidor_id: str = Query(..., min_length=1, max_length=128),
                                 client_id: str = Depends(tenant_dependency)):
        return await posting_operation("set_message_deleted", request, client_id, servidor_id, channel_id,
                                       message_id, deleted=False)

    @router.delete("/canais/{channel_id}", name="cofre_canal_excluir")
    async def excluir_canal(channel_id: str, request: Request,
                            servidor_id: str = Query(..., min_length=1, max_length=128),
                            client_id: str = Depends(tenant_dependency)):
        return await posting_operation("set_channel_deleted", request, client_id, servidor_id, channel_id)

    @router.patch("/canais/{channel_id}", name="cofre_canal_editar")
    async def editar_canal(channel_id: str, request: Request,
                          servidor_id: str = Query(..., min_length=1, max_length=128),
                          client_id: str = Depends(tenant_dependency)):
        patch = await _read_patch(request, storage.ChannelPatch)
        result = await invoke(storage.change_channel, client_id, servidor_id, channel_id, patch, _request=request)
        return JSONResponse(result, headers=_HEADERS)

    @router.get("/arquivos/{guild_id}/{digest}/{filename}", name="cofre_arquivo")
    async def arquivo(guild_id: str, digest: str, filename: str, request: Request,
                      client_id: str = Depends(tenant_dependency)):
        if shared:
            try:
                path = await run_in_threadpool(shared_assets.cached, shared, client_id, principal(request),
                                               guild_id, digest, filename)
                if path is None:
                    identity = await cloud_principal(request, client_id)
                    path = await run_in_threadpool(shared_assets.fetch, shared, client_id, identity,
                                                   guild_id, digest, filename)
            except (storage.CofreError, StarletteHTTPException) as exc:
                raise operation_error(exc) from exc
        else:
            path = await invoke(storage.get_asset, client_id, guild_id, digest, filename, _request=request)
        # Always download untrusted imported bytes; the UI may preview known raster types as blobs.
        return FileResponse(path, media_type="application/octet-stream", filename=filename,
                            content_disposition_type="attachment", headers={
                                **_HEADERS, "Content-Security-Policy": "sandbox; default-src 'none'"})

    return router


__all__ = ["create_cofre_router"]
