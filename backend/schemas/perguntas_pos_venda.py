"""Pydantic schemas for perguntas pos venda."""

from typing import Any, Optional

from pydantic import BaseModel


class PerguntasLojaConfigRequest(BaseModel):
    loja: str
    responder_automaticamente: bool = False
    solicitar_aprovacao: bool = False
    notificar_whatsapp_aprovacoes: bool = False
    habilitar_pos_venda_automatico: bool = False
    intervalo_minutos: Optional[float] = 10


class PerguntasLojasConfigLoteRequest(BaseModel):
    intervalo_minutos: Optional[float] = 10
    somente_conectadas: bool = True


class PerguntasAprovacaoRequest(BaseModel):
    approval_id: str
    resposta: Optional[Any] = None
    texto: Optional[Any] = None


class PerguntasGerarRespostaRequest(BaseModel):
    loja: str
    pergunta: dict
    resposta_atual: Optional[str] = ""
    orientacao_usuario: Optional[str] = ""


class PerguntasEnviarRespostaRequest(BaseModel):
    loja: str
    question_id: str
    resposta: str
    pergunta: Optional[dict] = None
    sku: Optional[str] = ""
    item_id: Optional[str] = ""


class MLQuestionsV2ProcessRequest(BaseModel):
    loja: str
    pergunta: Optional[dict] = None
    item: Optional[dict] = None
    resposta_atual: Optional[str] = ""


class MLQuestionsV2ReviewActionRequest(BaseModel):
    resposta: Optional[Any] = None
    texto: Optional[Any] = None


class PosVendaMensagemRequest(BaseModel):
    loja: Any
    pack_id: Any
    order_id: Optional[Any] = ""
    buyer_id: Optional[Any] = ""
    texto: Any
    max_chars: Optional[int] = 350
    conversa: Optional[dict] = None


class PosVendaGerarRespostaRequest(BaseModel):
    loja: Any
    pack_id: Any
    order_id: Optional[Any] = ""
    buyer_id: Optional[Any] = ""
    max_chars: Optional[int] = 350
    resposta_atual: Optional[str] = ""
    orientacao_usuario: Optional[str] = ""


class MLDescricaoRequest(BaseModel):
    loja: str
    item_id: str
    plain_text: str


class MLRespostaPerguntaRequest(BaseModel):
    loja: str
    question_id: int
    text: str


__all__ = [
    "PerguntasLojaConfigRequest",
    "PerguntasLojasConfigLoteRequest",
    "PerguntasAprovacaoRequest",
    "PerguntasGerarRespostaRequest",
    "PerguntasEnviarRespostaRequest",
    "MLQuestionsV2ProcessRequest",
    "MLQuestionsV2ReviewActionRequest",
    "PosVendaMensagemRequest",
    "PosVendaGerarRespostaRequest",
    "MLDescricaoRequest",
    "MLRespostaPerguntaRequest",
]
