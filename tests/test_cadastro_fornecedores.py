import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.schemas import CadastroFornecedorRequest
from backend.services import cadastro_fornecedores


def _configure_root(monkeypatch, tmp_path):
    info_root = tmp_path / "info"
    monkeypatch.setattr(
        cadastro_fornecedores,
        "get_tenant_path",
        lambda client_id: str(info_root / str(client_id)),
    )
    return info_root


def _request(**overrides):
    payload = {
        "nome_empresa": "Fornecedor Exemplo Ltd.",
        "nome_contato": "Contato Exemplo",
        "endereco_empresa": "Rua de Exemplo, 100",
        "telefone": "+00 0000-0000",
        "email": "contato@example.com",
        "moeda_pagamento": "USD",
        "conta_beneficiario": "123456789",
        "swift": "TESTBRXX",
        "pais_regiao_beneficiario": "País Exemplo",
        "nome_beneficiario": "Beneficiário Exemplo Ltd.",
        "endereco_beneficiario": "Avenida de Exemplo, 200",
        "banco_beneficiario": "Banco Exemplo",
        "endereco_banco": "Praça de Exemplo, 300",
        "codigo_banco": "001",
        "codigo_agencia": "999",
        "observacao_pagamento": "Referência de exemplo",
    }
    payload.update(overrides)
    return CadastroFornecedorRequest(**payload)


def test_fornecedores_crud_isolado_por_cliente(monkeypatch, tmp_path):
    info_root = _configure_root(monkeypatch, tmp_path)

    criado = asyncio.run(cadastro_fornecedores.criar_fornecedor_cadastro(_request(), "cliente-a"))
    fornecedor_id = criado["fornecedor"]["id"]

    lista_a = asyncio.run(cadastro_fornecedores.listar_fornecedores_cadastro("cliente-a"))
    lista_b = asyncio.run(cadastro_fornecedores.listar_fornecedores_cadastro("cliente-b"))
    assert len(lista_a) == 1
    assert lista_b == []
    assert lista_a[0]["nome_beneficiario"] != lista_a[0]["nome_empresa"]

    arquivo = info_root / "cliente-a" / "cadastro_fornecedores.json"
    payload = json.loads(arquivo.read_text(encoding="utf-8"))
    assert payload["schema"] == "jk.cadastro.fornecedores.v1"
    assert payload["fornecedores"][0]["conta_beneficiario"] == "123456789"
    assert not list(arquivo.parent.glob("*.tmp"))

    atualizado = asyncio.run(
        cadastro_fornecedores.atualizar_fornecedor_cadastro(
            fornecedor_id,
            _request(nome_contato="Contato Atualizado"),
            "cliente-a",
        )
    )
    assert atualizado["fornecedor"]["nome_contato"] == "Contato Atualizado"
    assert atualizado["fornecedor"]["id"] == fornecedor_id

    excluido = asyncio.run(cadastro_fornecedores.excluir_fornecedor_cadastro(fornecedor_id, "cliente-a"))
    assert excluido == {"success": True, "total": 0}
    assert asyncio.run(cadastro_fornecedores.listar_fornecedores_cadastro("cliente-a")) == []


def test_fornecedor_valida_campos_e_duplicidade(monkeypatch, tmp_path):
    _configure_root(monkeypatch, tmp_path)
    asyncio.run(cadastro_fornecedores.criar_fornecedor_cadastro(_request(), "cliente-a"))

    with pytest.raises(HTTPException) as duplicado:
        asyncio.run(
            cadastro_fornecedores.criar_fornecedor_cadastro(
                _request(nome_empresa="  fornecedor exemplo ltd.  "),
                "cliente-a",
            )
        )
    assert duplicado.value.status_code == 409

    with pytest.raises(HTTPException) as email_invalido:
        asyncio.run(
            cadastro_fornecedores.criar_fornecedor_cadastro(
                _request(nome_empresa="Outro Fornecedor", email="email-invalido"),
                "cliente-a",
            )
        )
    assert email_invalido.value.status_code == 400


def test_router_expoe_crud_de_fornecedores():
    from backend.routers.cadastro import create_cadastro_router

    routes = {
        (route.path, method)
        for route in create_cadastro_router().routes
        for method in (route.methods or set())
    }
    assert ("/api/cadastro/fornecedores", "GET") in routes
    assert ("/api/cadastro/fornecedores", "POST") in routes
    assert ("/api/cadastro/fornecedores/{fornecedor_id}", "PUT") in routes
    assert ("/api/cadastro/fornecedores/{fornecedor_id}", "DELETE") in routes
