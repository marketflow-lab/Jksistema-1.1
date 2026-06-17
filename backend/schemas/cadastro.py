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


__all__ = [
    "CadastroProdutoRequest",
]
