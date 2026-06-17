"""Pydantic schemas for impostos."""

from typing import Any, Optional

from pydantic import BaseModel


class SiscomexConfigRequest(BaseModel):
    ambiente: str = "prod"
    client_id: str = ""
    client_secret: str = ""
    role_type: str = "IMPEXP"
    authorization_header_type: str = "raw"
    codigo_pais_padrao: int = 0
    tipo_operacao_padrao: str = "I"
    regime_tributario: str = "simples"  # simples | presumido | real
    persistir_secret: bool = True
    loja_vinculada: str = ""
    perfil_usuario: str = ""


class ImpostoRegraRequest(BaseModel):
    id: str | None = None
    ncm: str = ""
    cest: str = ""
    descricao: str = ""
    aliquota_federal: float = 0.0
    aliquota_estadual: float = 0.0
    aliquota_municipal: float = 0.0
    observacoes: str = ""


class ImpostosSimulacaoRequest(BaseModel):
    reserva_percentual: float = 0.0


class SimuladorCalculoRequest(BaseModel):
    ncm: str = ""
    valor_dolar: float
    quantidade: float
    cubagem: float
    cotacao_dolar: float = 5.0
    frete_por_m3: float = 0.0


class SiscomexFundamentoOpcionalRequest(BaseModel):
    codigoTributo: int
    codigoRegime: int
    codigoFundamentoLegal: int
    codigoNomenclaturaAlternativa: str = ""


class SiscomexConsultaRequest(BaseModel):
    ncm: str
    codigoPais: int
    dataFatoGerador: str = ""
    tipoOperacao: str = "I"
    fundamentosOpcionais: list[SiscomexFundamentoOpcionalRequest] = []
    loja_vinculada: str = ""
    perfil_usuario: str = ""


class SiscomexAliquotasRequest(BaseModel):
    loja_vinculada: str = ""
    perfil_usuario: str = ""
    ncm: str
    permitir_fallback_local: bool = True


__all__ = [
    "SiscomexConfigRequest",
    "ImpostoRegraRequest",
    "ImpostosSimulacaoRequest",
    "SimuladorCalculoRequest",
    "SiscomexFundamentoOpcionalRequest",
    "SiscomexConsultaRequest",
    "SiscomexAliquotasRequest",
]
