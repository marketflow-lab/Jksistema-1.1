"""Pydantic schemas for cadastro."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_validator


_CADASTRO_CAMPOS_RESERVADOS_LOJA = {
    "store_id",
    "loja_sync",
    "loja",
    "sku_normalizado",
    "row_version",
    "updated_at_utc",
    "deleted_at_utc",
    "scope_source",
}


class CadastroProdutoRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _rejeitar_vinculo_loja_em_rota_global(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            str(campo or "").strip().casefold()
            in _CADASTRO_CAMPOS_RESERVADOS_LOJA
            for campo in value
        ):
            raise ValueError(
                "Metadados reservados de loja exigem a rota de cadastro por loja "
                "com store_id."
            )
        return value

    sku: str
    nome: str
    categoria: str = ""
    marca: str = ""
    custo: float | None = None
    preco: float | None = None
    descricao: str = ""


class CadastroProdutoLojaRequest(BaseModel):
    """Dynamic store product payload with a required SKU."""

    model_config = ConfigDict(extra="allow")

    sku: str


class CadastroProdutoLojaAtualizacaoRequest(BaseModel):
    """Dynamic partial update; the path keeps the durable SKU key."""

    model_config = ConfigDict(extra="allow")

    sku: Optional[str] = None
    row_version: int


class CadastroFornecedorRequest(BaseModel):
    nome_empresa: str
    nome_contato: str = ""
    endereco_empresa: str = ""
    telefone: str = ""
    email: str = ""
    moeda_pagamento: str = ""
    conta_beneficiario: str = ""
    swift: str = ""
    pais_regiao_beneficiario: str = ""
    nome_beneficiario: str = ""
    endereco_beneficiario: str = ""
    banco_beneficiario: str = ""
    endereco_banco: str = ""
    codigo_banco: str = ""
    codigo_agencia: str = ""
    observacao_pagamento: str = ""


__all__ = [
    "CadastroProdutoRequest",
    "CadastroProdutoLojaRequest",
    "CadastroProdutoLojaAtualizacaoRequest",
    "CadastroFornecedorRequest",
]
