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


def test_leitura_fornecedor_isola_cliente_e_id_exato(monkeypatch, tmp_path):
    info_root = _configure_root(monkeypatch, tmp_path)
    fornecedor = {"id": "fornecedor-b", "nome_empresa": "Empresa B", "nome_contato": "Contato B"}
    pasta_tenant = info_root / "cliente-b"
    pasta_tenant.mkdir(parents=True)
    (pasta_tenant / "cadastro_fornecedores.json").write_text(
        json.dumps({"fornecedores": [fornecedor]}), encoding="utf-8",
    )

    assert cadastro_fornecedores.ler_fornecedor_por_id("cliente-a", fornecedor["id"]) is None
    assert not (info_root / "cliente-a").exists()
    assert cadastro_fornecedores.ler_fornecedor_por_id("cliente-b", fornecedor["id"]) == fornecedor
    assert cadastro_fornecedores.ler_fornecedor_por_id("cliente-b", fornecedor["id"].upper()) is None
    assert cadastro_fornecedores.ler_fornecedor_por_id("cliente-b", "id-inexistente") is None


@pytest.mark.parametrize("fornecedor_id", [None, "", "   "])
def test_leitura_fornecedor_sem_id_nao_consulta_cadastro(monkeypatch, fornecedor_id):
    def consulta_indevida(*_args, **_kwargs):
        raise AssertionError("ID vazio não precisa consultar o cadastro.")

    monkeypatch.setattr(cadastro_fornecedores, "_carregar_fornecedores", consulta_indevida)
    assert cadastro_fornecedores.ler_fornecedor_por_id("cliente-a", fornecedor_id) is None


def test_leitura_fornecedor_usa_pasta_explicita_sem_runtime_e_sem_gravar(monkeypatch, tmp_path):
    pasta_tenant = tmp_path / "cliente-a"
    pasta_tenant.mkdir()
    arquivo = pasta_tenant / "cadastro_fornecedores.json"
    fornecedor = {"id": "fornecedor-a", "nome_empresa": "Empresa A", "nome_contato": "Contato A"}
    arquivo.write_text(json.dumps({"fornecedores": [fornecedor]}), encoding="utf-8")
    conteudo_original = arquivo.read_bytes()
    modificacao_original = arquivo.stat().st_mtime_ns

    def operacao_indevida(*_args, **_kwargs):
        raise AssertionError("A leitura com pasta explícita não usa o runtime nem salva dados.")

    monkeypatch.setattr(cadastro_fornecedores, "get_tenant_path", operacao_indevida)
    monkeypatch.setattr(cadastro_fornecedores, "_salvar_fornecedores", operacao_indevida)
    lido = cadastro_fornecedores.ler_fornecedor_por_id(
        "cliente-a", "fornecedor-a", pasta_tenant=str(pasta_tenant),
    )
    assert lido == fornecedor
    lido["nome_empresa"] = "Nome alterado somente na cópia"
    assert cadastro_fornecedores.ler_fornecedor_por_id(
        "cliente-a", "fornecedor-a", pasta_tenant=str(pasta_tenant),
    ) == fornecedor
    assert arquivo.read_bytes() == conteudo_original
    assert arquivo.stat().st_mtime_ns == modificacao_original
    assert list(pasta_tenant.iterdir()) == [arquivo]


def test_leitura_fornecedor_devolve_copia_do_registro(monkeypatch):
    fornecedor = {"id": "fornecedor-a", "nome_empresa": "Empresa A"}
    monkeypatch.setattr(cadastro_fornecedores, "_carregar_fornecedores", lambda *_args, **_kwargs: [fornecedor])

    lido = cadastro_fornecedores.ler_fornecedor_por_id("cliente-a", "fornecedor-a")
    assert lido == fornecedor
    assert lido is not fornecedor
    lido["nome_empresa"] = "Outra empresa"
    assert fornecedor["nome_empresa"] == "Empresa A"


def test_resolver_fornecedor_preserva_retorno_com_leitor_completo(monkeypatch, tmp_path):
    _configure_root(monkeypatch, tmp_path)
    criado = asyncio.run(cadastro_fornecedores.criar_fornecedor_cadastro(_request(), "cliente-a"))
    fornecedor_id = criado["fornecedor"]["id"]

    assert cadastro_fornecedores.resolver_fornecedor_lista("cliente-a", f" {fornecedor_id} ") == {
        "fornecedor_id": fornecedor_id,
        "supplier": "Fornecedor Exemplo Ltd.",
    }
    with pytest.raises(HTTPException) as outro_cliente:
        cadastro_fornecedores.resolver_fornecedor_lista("cliente-b", fornecedor_id)
    assert outro_cliente.value.status_code == 400
    assert outro_cliente.value.detail == "Fornecedor não encontrado no cadastro deste cliente."


@pytest.mark.parametrize(
    "fornecedor_id,registro,status,detail",
    [
        (None, None, 400, "Selecione um fornecedor cadastrado para continuar."),
        ("", None, 400, "Selecione um fornecedor cadastrado para continuar."),
        ("fornecedor-a", None, 400, "Fornecedor não encontrado no cadastro deste cliente."),
        ("fornecedor-a", {"nome_empresa": " "}, 409, "O fornecedor cadastrado precisa ter o nome da empresa."),
    ],
)
def test_resolver_fornecedor_preserva_erros(monkeypatch, fornecedor_id, registro, status, detail):
    monkeypatch.setattr(cadastro_fornecedores, "ler_fornecedor_por_id", lambda *_args, **_kwargs: registro)
    with pytest.raises(HTTPException) as erro:
        cadastro_fornecedores.resolver_fornecedor_lista("cliente-a", fornecedor_id)
    assert erro.value.status_code == status
    assert erro.value.detail == detail


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
