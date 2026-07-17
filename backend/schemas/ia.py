"""Pydantic schemas for ia."""

from typing import Any, Optional

from pydantic import BaseModel


class IAChatAttachment(BaseModel):
    name: str
    mime_type: Optional[str] = "application/octet-stream"
    data_base64: str


class IAChatRequest(BaseModel):
    message: str
    page: Optional[str] = ""
    context: Optional[dict] = None
    history: Optional[list[dict]] = None
    attachments: Optional[list[IAChatAttachment]] = None
    model: Optional[str] = None  # Modelo de IA solicitado pelo chat
    tool_results: Optional[list[dict]] = None
    conversa_id: Optional[str] = None
    modulo: Optional[str] = None
    conversa_mensagens: Optional[list[dict]] = None
    fallback_read_only: bool = False


class IAAgentQueryRequest(BaseModel):
    input: Optional[Any] = None
    classMethod: Optional[str] = "query"


class IATreinamentoPerguntasPosVendaRequest(BaseModel):
    orientacoes: str = ""
    tipo: Optional[str] = "perguntas_anuncio"
    loja: Optional[str] = ""
    contexto_loja: Optional[str] = ""
    compatibilidade_autopecas: Optional[str] = ""
    proibicoes: Optional[str] = ""
    sku: Optional[str] = ""
    notas_sku: Optional[str] = ""
    exemplos: Optional[list[dict]] = None


class IATreinamentoPerguntasPosVendaSimularRequest(BaseModel):
    pergunta: str
    contexto: Optional[str] = ""
    sku: Optional[str] = ""
    tipo: Optional[str] = "perguntas_anuncio"
    loja: Optional[str] = ""
    model: Optional[str] = None


class IARagDocumento(BaseModel):
    content: str
    title: Optional[str] = ""
    source: Optional[str] = "manual"
    metadata: Optional[dict] = None


class IARagIndexRequest(BaseModel):
    documents: list[IARagDocumento]


class IARagReindexRequest(BaseModel):
    force: bool = False


class IASalvarConversaRequest(BaseModel):
    conversa_id: str
    modulo: str
    titulo: str
    mensagens: list[dict]


__all__ = [
    "IAChatAttachment",
    "IAChatRequest",
    "IAAgentQueryRequest",
    "IATreinamentoPerguntasPosVendaRequest",
    "IATreinamentoPerguntasPosVendaSimularRequest",
    "IARagDocumento",
    "IARagIndexRequest",
    "IARagReindexRequest",
    "IASalvarConversaRequest",
]
