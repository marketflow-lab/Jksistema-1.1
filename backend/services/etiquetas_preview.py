"""Temporary PDF download and preview helpers for Etiquetas."""

from __future__ import annotations

import fitz
from fastapi import HTTPException


def obter_pdf_temporario(temp_files_storage: dict, file_id: str) -> bytes:
    if file_id in temp_files_storage:
        return temp_files_storage[file_id]
    raise HTTPException(status_code=404, detail="Arquivo nao encontrado ou expirado.")


def gerar_preview_pdf_bytes(file_bytes: bytes, page: int = 0) -> bytes:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if doc.page_count <= 0:
            doc.close()
            raise HTTPException(status_code=400, detail="PDF sem paginas para pre-visualizar.")
        page_index = max(0, min(int(page or 0), doc.page_count - 1))
        pagina = doc.load_page(page_index)
        pix = pagina.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar previa: {exc}")
