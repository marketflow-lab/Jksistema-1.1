"""Authenticated loopback routes for Firebase credential provisioning."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException, MultiPartParser

from backend.schemas import FirebaseProvisioningResponse
from backend.services import firebase_provisioning


# Multipart framing for the neutral frontend filename is well below 1 KiB.  A
# bounded 8 KiB allowance keeps the wire limit close to the 64 KiB credential
# contract without trusting Content-Length as the only size control.
MAX_MULTIPART_BODY_BYTES = firebase_provisioning.MAX_UPLOAD_BYTES + (8 * 1024)


@dataclass(frozen=True)
class FirebaseProvisioningRouterConfig:
    base_dir: str
    info_dir: str
    authorize_recovery: Callable[[Optional[str]], dict]
    authorize_status: Optional[Callable[[Optional[str]], dict]] = None


def _request_is_loopback(request: Request) -> bool:
    client = request.client
    host = str(client.host if client is not None else "").strip().lower()
    return host in {"127.0.0.1", "::1", "localhost"}


def _json_result(result: firebase_provisioning.FirebaseProvisioningResult):
    if result.status_code == 200:
        return result.payload
    return JSONResponse(status_code=result.status_code, content=result.payload)


def _safe_error_response(status_code: int, code: str) -> JSONResponse:
    payload = {
        "success": False,
        "configured": False,
        "ready": False,
        "source": "none",
        "code": str(code or "request_rejected"),
        "restart_required": False,
        "can_migrate": False,
        "replacement_required": False,
    }
    return JSONResponse(status_code=int(status_code), content=payload)


def _local_only_response() -> JSONResponse:
    return _safe_error_response(403, "local_only")


def _authorization_error_response(error: StarletteHTTPException) -> JSONResponse:
    status_code = int(getattr(error, "status_code", 0) or 0)
    if status_code == 401:
        return _safe_error_response(401, "authentication_required")
    if status_code == 403:
        return _safe_error_response(403, "forbidden")
    return _safe_error_response(503, "authorization_unavailable")


def _raw_header_values(request: Request, name: bytes) -> list[str]:
    return [
        value.decode("latin-1").strip()
        for key, value in request.headers.raw
        if key.lower() == name
    ]


async def _bounded_multipart_body(request: Request) -> tuple[Optional[bytes], Optional[str], Optional[JSONResponse]]:
    content_lengths = _raw_header_values(request, b"content-length")
    transfer_encodings = _raw_header_values(request, b"transfer-encoding")
    if transfer_encodings or len(content_lengths) != 1 or not content_lengths[0].isdigit():
        return None, None, _safe_error_response(411, "content_length_required")
    declared_length = int(content_lengths[0])
    if declared_length <= 0:
        return None, None, _safe_error_response(400, "invalid_upload")
    if declared_length > MAX_MULTIPART_BODY_BYTES:
        return None, None, _safe_error_response(413, "oversize")

    content_types = _raw_header_values(request, b"content-type")
    if len(content_types) != 1 or len(content_types[0]) > 512:
        return None, None, _safe_error_response(415, "unsupported_media_type")
    content_type = content_types[0]
    if content_type.split(";", 1)[0].strip().lower() != "multipart/form-data":
        return None, None, _safe_error_response(415, "unsupported_media_type")

    body_parts: list[bytes] = []
    received = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            received += len(chunk)
            if received > MAX_MULTIPART_BODY_BYTES or received > declared_length:
                return None, None, _safe_error_response(413, "oversize")
            body_parts.append(bytes(chunk))
    except Exception:
        return None, None, _safe_error_response(400, "invalid_upload")
    if received != declared_length:
        return None, None, _safe_error_response(400, "invalid_upload")
    return b"".join(body_parts), content_type, None


async def _single_body_chunk(body: bytes):
    yield body


async def _single_file_from_multipart(request: Request) -> tuple[Optional[bytes], Optional[JSONResponse]]:
    body, content_type, rejected = await _bounded_multipart_body(request)
    if rejected is not None:
        return None, rejected

    form = None
    try:
        parser = MultiPartParser(
            Headers({"content-type": str(content_type or "")}),
            _single_body_chunk(body or b""),
            max_files=1,
            max_fields=0,
            max_part_size=firebase_provisioning.MAX_UPLOAD_BYTES,
        )
        form = await parser.parse()
        items = list(form.multi_items())
        if (
            len(items) != 1
            or items[0][0] != "file"
            or not isinstance(items[0][1], StarletteUploadFile)
        ):
            return None, _safe_error_response(400, "invalid_upload")
        raw = await items[0][1].read(firebase_provisioning.MAX_UPLOAD_BYTES + 1)
        if len(raw) > firebase_provisioning.MAX_UPLOAD_BYTES:
            return None, _safe_error_response(413, "oversize")
        return bytes(raw), None
    except (MultiPartException, ValueError):
        return None, _safe_error_response(400, "invalid_upload")
    except Exception:
        return None, _safe_error_response(400, "invalid_upload")
    finally:
        if form is not None:
            try:
                await form.close()
            except Exception:
                pass


def create_firebase_provisioning_router(config: FirebaseProvisioningRouterConfig) -> APIRouter:
    firebase_provisioning.initialize_firebase_provisioning_runtime(
        base_dir=config.base_dir,
        info_dir=config.info_dir,
    )
    router = APIRouter(tags=["firebase-provisioning"])

    def authorize(
        request: Request,
        authorization: Optional[str],
        authorizer: Optional[Callable[[Optional[str]], dict]] = None,
    ):
        if not _request_is_loopback(request):
            return _local_only_response()
        try:
            (authorizer or config.authorize_recovery)(authorization)
        except StarletteHTTPException as error:
            return _authorization_error_response(error)
        except Exception:
            return _safe_error_response(503, "authorization_unavailable")
        return None

    @router.get(
        "/api/admin/firebase-provisioning/status",
        response_model=FirebaseProvisioningResponse,
    )
    async def firebase_provisioning_status(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        denied = authorize(request, authorization, config.authorize_status)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(
            firebase_provisioning.firebase_provisioning_status,
            base_dir=config.base_dir,
            info_dir=config.info_dir,
        )
        return _json_result(result)

    @router.post(
        "/api/admin/firebase-provisioning/import",
        response_model=FirebaseProvisioningResponse,
    )
    async def firebase_provisioning_import(
        request: Request,
        replace: bool = Query(default=False),
        authorization: Optional[str] = Header(default=None),
    ):
        denied = authorize(request, authorization)
        if denied is not None:
            return denied
        raw, rejected = await _single_file_from_multipart(request)
        if rejected is not None:
            return rejected
        result = await asyncio.to_thread(
            firebase_provisioning.firebase_provisioning_import,
            raw or b"",
            replace=replace,
            base_dir=config.base_dir,
            info_dir=config.info_dir,
        )
        return _json_result(result)

    @router.post(
        "/api/admin/firebase-provisioning/migrate-legacy",
        response_model=FirebaseProvisioningResponse,
    )
    async def firebase_provisioning_migrate_legacy(
        request: Request,
        authorization: Optional[str] = Header(default=None),
    ):
        denied = authorize(request, authorization)
        if denied is not None:
            return denied
        result = await asyncio.to_thread(
            firebase_provisioning.firebase_provisioning_migrate_legacy,
            base_dir=config.base_dir,
            info_dir=config.info_dir,
        )
        return _json_result(result)

    return router


__all__ = [
    "MAX_MULTIPART_BODY_BYTES",
    "FirebaseProvisioningRouterConfig",
    "create_firebase_provisioning_router",
]
