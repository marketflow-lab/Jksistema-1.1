"""Pydantic schemas for renovacao."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class RenovacaoCampanhaCriarRequest(BaseModel):
    loja: str
    campanha_id: str
    nome: str = ""
    promotion_type: str | None = None
    quantidade_origem_esperada: int | None = Field(default=None, ge=0, le=20000)


class RenovacaoCampanhaManualCriarRequest(BaseModel):
    loja: str
    nome: str
    start_date: str
    finish_date: str


class RenovacaoCampanhaSincronizarRequest(BaseModel):
    loja: str
    campanha_origem_id: str
    campanha_destino_id: str
    promotion_type_origem: str | None = None
    promotion_type_destino: str | None = None
    quantidade_origem_esperada: int | None = Field(default=None, ge=0, le=20000)
    somente_conferir: bool = False


class RenovacaoCampanhaPeriodoRequest(BaseModel):
    loja: str
    campanha_id: str
    start_date: str | None = None
    finish_date: str | None = None
    promotion_type: str | None = None
    nome: str | None = None


class RenovacaoCampanhaExcluirRequest(BaseModel):
    loja: str
    campanha_id: str
    promotion_type: str | None = None


class RenovacaoAgendamentoRequest(BaseModel):
    loja: str
    campanha_id: str | None = None
    nome: str | None = None
    promotion_type: str | None = None
    enabled: bool = False


__all__ = [
    "RenovacaoCampanhaCriarRequest",
    "RenovacaoCampanhaManualCriarRequest",
    "RenovacaoCampanhaSincronizarRequest",
    "RenovacaoCampanhaPeriodoRequest",
    "RenovacaoCampanhaExcluirRequest",
    "RenovacaoAgendamentoRequest",
]
