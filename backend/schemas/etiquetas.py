"""Pydantic schemas for etiquetas."""

from typing import Any, Optional

from pydantic import BaseModel


class EtiquetaAvulsaItem(BaseModel):
    layout_planilha: Optional[bool] = False
    sequencia: Optional[str] = ""
    sku: Optional[str] = ""
    descricao: Optional[str] = ""
    quantidade: Optional[int] = 1
    caixa: Optional[str] = ""
    torre: Optional[str] = ""
    paletes: Optional[str] = ""
    corredor: Optional[str] = ""
    conta: Optional[str] = ""


class ImpressaoAvulsaPayload(BaseModel):
    formato: Optional[str] = "grande"
    itens: list[EtiquetaAvulsaItem]


class ImpressaoEditorPayload(BaseModel):
    tamanho: Optional[str] = "grande"
    etiquetas: list[str]


class EtiquetaQrCodePayload(BaseModel):
    link: str
    titulo: Optional[str] = ""
    quantidade: Optional[int] = 1
    tamanho: Optional[str] = "grande"
    layout: Optional[str] = "a4"


__all__ = [
    "EtiquetaAvulsaItem",
    "ImpressaoAvulsaPayload",
    "ImpressaoEditorPayload",
    "EtiquetaQrCodePayload",
]
