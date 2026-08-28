"""Pydantic schemas for cadastro."""

from typing import Any, Optional

from pydantic import BaseModel


class CadastroProdutoRequest(BaseModel):
    sku: str
    nome: str
    categoria: str = ""
    marca: str = ""
    custo: float | None = None
    preco: float | None = None
    descricao: str = ""


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
    "CadastroFornecedorRequest",
]
