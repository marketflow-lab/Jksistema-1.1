import asyncio

import pytest
from fastapi import HTTPException

from backend.schemas.cadastro import CadastroImportadorLojaRequest
from backend.services import cadastro_importador_loja as service


def test_importador_loja_isolado_por_cliente_e_store_id(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_arquivo", lambda client_id: str(tmp_path / client_id / "cadastro_importador_loja.json"))

    def resolver(client_id, store_id, *, somente_commit=False):
        if store_id not in {"store-a", "store-b"}:
            raise HTTPException(status_code=404, detail="Loja não encontrada")
        return {"store_id": store_id, "nome": store_id}

    monkeypatch.setattr(service.lojas, "resolver_loja_cadastro", resolver)
    registro = CadastroImportadorLojaRequest(nome_empresa="Empresa A", email="contato@example.com")
    salvo = asyncio.run(service.salvar_importador_loja("store-a", registro, "cliente-a"))
    assert salvo["nome_empresa"] == "Empresa A"
    assert asyncio.run(service.obter_importador_loja("store-a", "cliente-a"))["email"] == "contato@example.com"
    assert asyncio.run(service.obter_importador_loja("store-b", "cliente-a")) == {}
    assert asyncio.run(service.obter_importador_loja("store-a", "cliente-b")) == {}
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.salvar_importador_loja("store-x", registro, "cliente-a"))
    assert erro.value.status_code == 404


def test_importador_loja_rejeita_email_invalido(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "_arquivo", lambda _client_id: str(tmp_path / "dados.json"))
    monkeypatch.setattr(service.lojas, "resolver_loja_cadastro", lambda _client_id, store_id, **_kw: {"store_id": store_id})
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.salvar_importador_loja("store-a", CadastroImportadorLojaRequest(email="invalido"), "cliente-a"))
    assert erro.value.status_code == 400
    assert not (tmp_path / "dados.json").exists()
