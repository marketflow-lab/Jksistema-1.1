"""Pydantic schemas for promocoes."""

from typing import Any, Optional

from pydantic import BaseModel


class PromoAnaliseApiRequest(BaseModel):
    loja: str
    promocao_a_id: str
    promocao_b_id: str | None = None
    promocao_b_ids: list[str] | None = None
    promocao_a_type: str | None = None
    promocao_b_type: str | None = None
    promocao_b_types: list[str] | None = None
    promocao_b_files: list[str] | None = None
    margem_minima: float = 15.0
    margem_tolerancia: float = 0.0


class PromoAplicarParticipacaoRequest(BaseModel):
    loja: str
    promocoes: list[dict]


class PromoAutomacaoConfigRequest(BaseModel):
    enabled: bool = False
    approval_required: bool = True
    interval_unit: str = "minutes"
    interval_value: int = 60
    interval_minutes: Optional[int] = None
    next_run_at: Optional[float] = None
    loja: str = ""
    promocao_a_id: str = ""
    promocao_a_type: Optional[str] = ""
    margem_minima: float = 15.0
    margem_tolerancia: float = 0.0
    promocoes_b_meta: Optional[list[dict]] = None


class PromoPreferenciasColunasRequest(BaseModel):
    ordem_colunas: list[str] | None = None
    colunas_visiveis: list[str] | None = None
    larguras_colunas: dict[str, int] | None = None
    versao: str | None = None


__all__ = [
    "PromoAnaliseApiRequest",
    "PromoAplicarParticipacaoRequest",
    "PromoAutomacaoConfigRequest",
    "PromoPreferenciasColunasRequest",
]
