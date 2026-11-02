"""Etiquetas router definitions."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field
from typing import Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.schemas import EtiquetaQrCodePayload, ImpressaoAvulsaPayload, ImpressaoEditorPayload
from backend.services.etiquetas import (
    gerar_pdf_editor_livre,
    gerar_pdf_impressao_avulsa,
    gerar_pdf_qrcode_link,
    gerar_preview_pdf_bytes,
    obter_pdf_temporario,
    processar_etiquetas_loja,
)
from backend.services.etiquetas_layout import gerar_preview_layout, validar_layout


@dataclass(frozen=True)
class EtiquetasRouterConfig:
    get_tenant_id: Callable
    temp_files_storage: dict
    temp_file_owners: dict = field(default_factory=dict)


router = APIRouter(tags=["etiquetas"])


def create_etiquetas_router(config: EtiquetasRouterConfig) -> APIRouter:
    etiquetas_router = APIRouter(tags=["etiquetas"])

    def guardar_pdf(file_bytes: bytes, client_id: str) -> str:
        file_id = str(uuid.uuid4())
        config.temp_files_storage[file_id] = file_bytes
        config.temp_file_owners[file_id] = str(client_id)
        return file_id

    def ler_pdf(file_id: str, client_id: str) -> bytes:
        owner = config.temp_file_owners.get(file_id)
        if owner is not None and owner != str(client_id):
            raise HTTPException(status_code=404, detail="Arquivo nao encontrado ou expirado.")
        return obter_pdf_temporario(config.temp_files_storage, file_id)

    @etiquetas_router.post("/api/etiquetas/impressao-editor")
    async def gerar_impressao_editor_endpoint(
        payload: ImpressaoEditorPayload,
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            pdf_bytes, qtd = gerar_pdf_editor_livre(
                payload.etiquetas, payload.tamanho, orientacao=payload.orientacao
            )
            if not pdf_bytes:
                return {"success": False, "message": "Informe pelo menos uma etiqueta para imprimir."}
            file_id = guardar_pdf(pdf_bytes, client_id)
            return {"success": True, "qtd": qtd, "id_etiquetas": file_id}
        except Exception as e:
            print(f"Erro impressÃƒÂ£o editor: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.post("/api/etiquetas/impressao-avulsa")
    async def gerar_impressao_avulsa_endpoint(
        payload: ImpressaoAvulsaPayload,
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            pdf_bytes, qtd = gerar_pdf_impressao_avulsa(
                payload.itens, payload.formato, orientacao=payload.orientacao
            )
            if not pdf_bytes:
                return {"success": False, "message": "Informe pelo menos um item para imprimir."}
            file_id = guardar_pdf(pdf_bytes, client_id)
            return {"success": True, "qtd": qtd, "id_etiquetas": file_id}
        except Exception as e:
            print(f"Erro impressÃƒÂ£o avulsa: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.post("/api/etiquetas/qrcode-link")
    async def gerar_qrcode_link_endpoint(
        payload: EtiquetaQrCodePayload,
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            pdf_bytes, qtd = gerar_pdf_qrcode_link(
                payload.link,
                payload.titulo,
                payload.quantidade,
                payload.tamanho,
                payload.layout,
                orientacao=payload.orientacao,
            )
            if not pdf_bytes:
                return {"success": False, "message": "Informe o link para gerar o QR Code."}
            file_id = guardar_pdf(pdf_bytes, client_id)
            return {"success": True, "qtd": qtd, "id_etiquetas": file_id}
        except Exception as e:
            print(f"Erro QR Code etiquetas: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.post("/api/etiquetas/layout/preview")
    async def preview_layout_etiqueta(
        store: str = Form(...),
        layout: str = Form(None),
        file_etiquetas: UploadFile = File(None),
        file_lista: UploadFile = File(None),
        file_nota_fiscal: UploadFile = File(None),
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            normalized = validar_layout(layout, store)
            bytes_etiquetas = await file_etiquetas.read() if file_etiquetas else None
            bytes_lista = await file_lista.read() if file_lista else None
            bytes_nf = await file_nota_fiscal.read() if file_nota_fiscal else None
            # No server-side preview cache: uploaded content exists only in this request.
            return await run_in_threadpool(
                gerar_preview_layout, store, bytes_etiquetas, bytes_lista, bytes_nf, normalized,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            return {"success": False, "message": "Nao foi possivel ler o PDF para gerar a previa."}

    @etiquetas_router.post("/api/etiquetas/processar")
    async def processar_etiquetas_endpoint(
        store: str = Form(...),
        file_etiquetas: UploadFile = File(None),
        file_lista: UploadFile = File(None),
        file_nota_fiscal: UploadFile = File(None),
        layout: str = Form(None),
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            normalized = validar_layout(layout, store) if layout is not None else None
            bytes_etiquetas = await file_etiquetas.read() if file_etiquetas else None
            bytes_lista = await file_lista.read() if file_lista else None
            bytes_nf = await file_nota_fiscal.read() if file_nota_fiscal else None
            if normalized is None:
                resultado = processar_etiquetas_loja(store, bytes_etiquetas, bytes_lista, bytes_nf)
            else:
                resultado = await run_in_threadpool(
                    processar_etiquetas_loja, store, bytes_etiquetas, bytes_lista, bytes_nf, layout=normalized,
                )
            if not resultado.get("success"):
                return resultado

            id_etiquetas = None
            id_lista = None

            out_etiquetas = resultado.get("out_etiquetas")
            if out_etiquetas:
                id_etiquetas = guardar_pdf(out_etiquetas, client_id)

            out_lista = resultado.get("out_lista")
            if out_lista:
                id_lista = guardar_pdf(out_lista, client_id)

            return {
                "success": True,
                "qtd": resultado.get("qtd", 0),
                "id_etiquetas": id_etiquetas,
                "id_lista": id_lista,
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as e:
            print(f"Erro etiquetas: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.get("/api/etiquetas/download/{file_id}")
    async def download_etiqueta(file_id: str, client_id: str = Depends(config.get_tenant_id)):
        file_bytes = ler_pdf(file_id, client_id)
        return StreamingResponse(io.BytesIO(file_bytes), media_type="application/pdf")

    @etiquetas_router.get("/api/etiquetas/preview/{file_id}")
    async def preview_etiqueta(
        file_id: str,
        page: int = 0,
        client_id: str = Depends(config.get_tenant_id),
    ):
        file_bytes = ler_pdf(file_id, client_id)
        png_bytes = gerar_preview_pdf_bytes(file_bytes, page)
        return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")

    return etiquetas_router
