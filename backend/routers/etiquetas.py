"""Etiquetas router definitions."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.schemas import EtiquetaQrCodePayload, ImpressaoAvulsaPayload, ImpressaoEditorPayload
from backend.services.etiquetas import (
    gerar_pdf_editor_livre,
    gerar_pdf_impressao_avulsa,
    gerar_pdf_qrcode_link,
    gerar_preview_pdf_bytes,
    obter_pdf_temporario,
    processar_etiquetas_loja,
)


@dataclass(frozen=True)
class EtiquetasRouterConfig:
    get_tenant_id: Callable
    temp_files_storage: dict


router = APIRouter(tags=["etiquetas"])


def create_etiquetas_router(config: EtiquetasRouterConfig) -> APIRouter:
    etiquetas_router = APIRouter(tags=["etiquetas"])

    @etiquetas_router.post("/api/etiquetas/impressao-editor")
    async def gerar_impressao_editor_endpoint(
        payload: ImpressaoEditorPayload,
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            pdf_bytes, qtd = gerar_pdf_editor_livre(payload.etiquetas, payload.tamanho)
            if not pdf_bytes:
                return {"success": False, "message": "Informe pelo menos uma etiqueta para imprimir."}
            file_id = str(uuid.uuid4())
            config.temp_files_storage[file_id] = pdf_bytes
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
            pdf_bytes, qtd = gerar_pdf_impressao_avulsa(payload.itens, payload.formato)
            if not pdf_bytes:
                return {"success": False, "message": "Informe pelo menos um item para imprimir."}
            file_id = str(uuid.uuid4())
            config.temp_files_storage[file_id] = pdf_bytes
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
            )
            if not pdf_bytes:
                return {"success": False, "message": "Informe o link para gerar o QR Code."}
            file_id = str(uuid.uuid4())
            config.temp_files_storage[file_id] = pdf_bytes
            return {"success": True, "qtd": qtd, "id_etiquetas": file_id}
        except Exception as e:
            print(f"Erro QR Code etiquetas: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.post("/api/etiquetas/processar")
    async def processar_etiquetas_endpoint(
        store: str = Form(...),
        file_etiquetas: UploadFile = File(None),
        file_lista: UploadFile = File(None),
        file_nota_fiscal: UploadFile = File(None),
        client_id: str = Depends(config.get_tenant_id),
    ):
        try:
            bytes_etiquetas = await file_etiquetas.read() if file_etiquetas else None
            bytes_lista = await file_lista.read() if file_lista else None
            bytes_nf = await file_nota_fiscal.read() if file_nota_fiscal else None
            resultado = processar_etiquetas_loja(store, bytes_etiquetas, bytes_lista, bytes_nf)
            if not resultado.get("success"):
                return resultado

            id_etiquetas = None
            id_lista = None

            out_etiquetas = resultado.get("out_etiquetas")
            if out_etiquetas:
                id_etiquetas = str(uuid.uuid4())
                config.temp_files_storage[id_etiquetas] = out_etiquetas

            out_lista = resultado.get("out_lista")
            if out_lista:
                id_lista = str(uuid.uuid4())
                config.temp_files_storage[id_lista] = out_lista

            return {
                "success": True,
                "qtd": resultado.get("qtd", 0),
                "id_etiquetas": id_etiquetas,
                "id_lista": id_lista,
            }
        except Exception as e:
            print(f"Erro etiquetas: {e}")
            return {"success": False, "message": str(e)}

    @etiquetas_router.get("/api/etiquetas/download/{file_id}")
    async def download_etiqueta(file_id: str, client_id: str = Depends(config.get_tenant_id)):
        file_bytes = obter_pdf_temporario(config.temp_files_storage, file_id)
        return StreamingResponse(io.BytesIO(file_bytes), media_type="application/pdf")

    @etiquetas_router.get("/api/etiquetas/preview/{file_id}")
    async def preview_etiqueta(
        file_id: str,
        page: int = 0,
        client_id: str = Depends(config.get_tenant_id),
    ):
        file_bytes = obter_pdf_temporario(config.temp_files_storage, file_id)
        png_bytes = gerar_preview_pdf_bytes(file_bytes, page)
        return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")

    return etiquetas_router
