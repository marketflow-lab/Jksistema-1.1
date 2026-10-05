import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.cadastro import create_cadastro_router
from backend.schemas import CadastroNacionalizadorRequest
from backend.services import cadastro_nacionalizadores as service
from backend.services.admin_usuarios_common import _permissao_exigida_por_rota


def _request(**overrides):
    dados = {
        "nome_empresa": "Empresa Sintética Ltda.",
        "cnpj": "12.345.678/0001-00",
        "endereco_empresa": "Rua de Exemplo, 100",
        "nome_contato": "Contato de Exemplo",
        "telefone": "+00 0000-0000",
    }
    dados.update(overrides)
    return CadastroNacionalizadorRequest(**dados)


@pytest.fixture
def registry(monkeypatch, tmp_path):
    root = tmp_path / "info"
    monkeypatch.setattr(service, "get_tenant_path", lambda client_id: str(root / client_id))
    return root


def _provider(_request, authorization=None):
    if authorization not in {"Bearer tenant-a", "Bearer tenant-b"}:
        raise HTTPException(status_code=401, detail="Token inválido.")
    return authorization.removeprefix("Bearer ")


def _api(monkeypatch, provider=_provider):
    monkeypatch.setattr(service, "_runtime_get_tenant_id", provider)
    app = FastAPI()
    app.include_router(create_cadastro_router())
    return TestClient(app, raise_server_exceptions=False)


def test_roundtrip_compartilhado_por_lojas_e_isolado_por_tenant(monkeypatch, registry):
    with _api(monkeypatch) as client:
        headers = {"Authorization": "Bearer tenant-a"}
        criado = client.post("/api/cadastro/nacionalizadores", headers=headers, json=_request().model_dump())
        assert criado.status_code == 200
        registro = criado.json()["nacionalizador"]
        assert criado.json()["success"] is True
        loja_a = client.get("/api/cadastro/nacionalizadores?store_id=store-a", headers=headers)
        loja_b = client.get("/api/cadastro/nacionalizadores?store_id=store-b", headers=headers)
        outro_tenant = client.get("/api/cadastro/nacionalizadores", headers={"Authorization": "Bearer tenant-b"})
        assert loja_a.json() == loja_b.json() == [registro]
        assert outro_tenant.json() == []
        atualizado = client.put(
            f"/api/cadastro/nacionalizadores/{registro['id']}",
            headers=headers,
            json=_request(nome_contato="Novo Contato").model_dump(),
        )
        assert atualizado.status_code == 200
        novo = atualizado.json()["nacionalizador"]
        assert novo["id"] == registro["id"]
        assert novo["criado_em"] == registro["criado_em"]
        assert novo["nome_contato"] == "Novo Contato"
        assert novo["atualizado_em"] >= registro["atualizado_em"]
        estrangeiro = client.put(
            f"/api/cadastro/nacionalizadores/{registro['id']}",
            headers={"Authorization": "Bearer tenant-b"},
            json=_request().model_dump(),
        )
        assert estrangeiro.status_code == 404
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {"schema": "jk.cadastro.nacionalizadores.v1", "nacionalizadores": [novo]}
    assert not (registry / "tenant-b").exists()
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/cadastro/nacionalizadores"),
    ("POST", "/api/cadastro/nacionalizadores"),
    ("PUT", "/api/cadastro/nacionalizadores/foreign-id"),
])
@pytest.mark.parametrize("status", [401, 403])
def test_auth_impede_acesso_ao_arquivo(monkeypatch, method, path, status):
    def deny(_request, _authorization=None):
        raise HTTPException(status_code=status, detail="Acesso negado.")

    monkeypatch.setattr(service, "get_tenant_path", lambda *_args: pytest.fail("Não deve acessar dados sem autorização"))
    assert _permissao_exigida_por_rota(path, method) == "cadastro"
    with _api(monkeypatch, deny) as client:
        kwargs = {"json": _request().model_dump()} if method != "GET" else {}
        resposta = client.request(method, path, **kwargs)
    assert resposta.status_code == status
    assert resposta.json() == {"detail": "Acesso negado."}


def test_auth_nao_configurada_falha_sem_dados(monkeypatch):
    monkeypatch.setattr(service, "get_tenant_path", lambda *_args: pytest.fail("Não deve acessar dados sem runtime"))
    with _api(monkeypatch, None) as client:
        resposta = client.get("/api/cadastro/nacionalizadores")
    assert resposta.status_code == 503


@pytest.mark.parametrize("extra", ["client_id", "store_id", "id", "atualizado_em"])
def test_schema_rejeita_identidades_injetadas(monkeypatch, registry, extra):
    with _api(monkeypatch) as client:
        resposta = client.post(
            "/api/cadastro/nacionalizadores",
            headers={"Authorization": "Bearer tenant-a"},
            json={**_request().model_dump(), extra: "foreign-value"},
        )
    assert resposta.status_code == 422
    assert not registry.exists()


@pytest.mark.parametrize("campo,valor", [
    ("nome_empresa", " \x00 "),
    ("nome_empresa", "A" * 241),
    ("cnpj", "A" * 41),
    ("endereco_empresa", "A" * 1201),
    ("nome_contato", "A" * 161),
    ("telefone", "A" * 81),
])
def test_validacao_falha_antes_de_gravar(registry, campo, valor):
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.criar_nacionalizador_cadastro(_request(**{campo: valor}), "tenant-a"))
    assert erro.value.status_code == 400
    assert not registry.exists()


def test_preserva_texto_do_cnpj_e_limpa_espacos_e_nul(registry):
    salvo = asyncio.run(service.criar_nacionalizador_cadastro(
        _request(nome_empresa=" \x00Empresa Sintética Ltda. ", cnpj=" AB.345.678/0001-00 "), "tenant-a",
    ))["nacionalizador"]
    assert salvo["nome_empresa"] == "Empresa Sintética Ltda."
    assert salvo["cnpj"] == "AB.345.678/0001-00"


@pytest.mark.parametrize("overrides", [
    {"nome_empresa": "  empresa   sintética ltda. ", "cnpj": "outro-documento"},
    {"nome_empresa": "Outra Empresa", "cnpj": "12345678000100"},
])
def test_duplicata_nao_substitui_cadastro(registry, overrides):
    asyncio.run(service.criar_nacionalizador_cadastro(_request(), "tenant-a"))
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    antes = path.read_bytes()
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.criar_nacionalizador_cadastro(_request(**overrides), "tenant-a"))
    assert erro.value.status_code == 409
    assert path.read_bytes() == antes
    # O mesmo cadastro em outro tenant pertence a outra lista.
    assert asyncio.run(service.criar_nacionalizador_cadastro(_request(), "tenant-b"))["success"] is True


def test_edicao_rejeita_duplicata_sem_alterar_registros(registry):
    asyncio.run(service.criar_nacionalizador_cadastro(_request(), "tenant-a"))
    segundo = asyncio.run(service.criar_nacionalizador_cadastro(
        _request(nome_empresa="Outra Empresa", cnpj=""), "tenant-a",
    ))["nacionalizador"]
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    antes = path.read_bytes()
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.atualizar_nacionalizador_cadastro(segundo["id"], _request(), "tenant-a"))
    assert erro.value.status_code == 409
    assert path.read_bytes() == antes


def test_cadastros_sem_cnpj_podem_coexistir_e_lista_ordenada(registry):
    for nome in ["Zulu Sintética", "Alfa Sintética"]:
        asyncio.run(service.criar_nacionalizador_cadastro(_request(nome_empresa=nome, cnpj=""), "tenant-a"))
    assert [item["nome_empresa"] for item in asyncio.run(service.listar_nacionalizadores_cadastro("tenant-a"))] == [
        "Alfa Sintética", "Zulu Sintética",
    ]


@pytest.mark.parametrize("operation", ["create", "update"])
def test_falha_na_publicacao_preserva_arquivo_e_limpa_temporario(monkeypatch, registry, operation):
    original = asyncio.run(service.criar_nacionalizador_cadastro(_request(), "tenant-a"))["nacionalizador"]
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    antes = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("Falha sintética de publicação")

    monkeypatch.setattr(service.os, "replace", fail_replace)
    with pytest.raises(HTTPException) as erro:
        if operation == "create":
            asyncio.run(service.criar_nacionalizador_cadastro(_request(nome_empresa="Outra Empresa", cnpj=""), "tenant-a"))
        else:
            asyncio.run(service.atualizar_nacionalizador_cadastro(original["id"], _request(nome_contato="Novo Contato"), "tenant-a"))
    assert erro.value.status_code == 500
    assert path.read_bytes() == antes
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("payload", [
    "json inválido",
    {"schema": "outro-schema", "nacionalizadores": []},
    {"schema": "jk.cadastro.nacionalizadores.v1", "nacionalizadores": [None]},
])
def test_cadastro_corrompido_falha_sem_sobrescrever(registry, payload):
    path = registry / "tenant-a" / "cadastro_nacionalizadores.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    antes = path.read_bytes()
    with pytest.raises(HTTPException) as erro:
        asyncio.run(service.criar_nacionalizador_cadastro(_request(), "tenant-a"))
    assert erro.value.status_code == 500
    assert path.read_bytes() == antes


def test_inclusoes_concorrentes_preservam_todos_os_registros(monkeypatch, registry):
    barrier = threading.Barrier(8)
    carregar = service._carregar

    def slow_load(client_id):
        dados = carregar(client_id)
        time.sleep(0.005)
        return dados

    monkeypatch.setattr(service, "_carregar", slow_load)

    def create(index):
        barrier.wait(timeout=5)
        return service._criar_sync(_request(nome_empresa=f"Empresa Sintética {index}", cnpj=""), "tenant-a")

    with ThreadPoolExecutor(max_workers=8) as executor:
        resultados = list(executor.map(create, range(8)))
    registros = service._listar_sync("tenant-a")
    assert len(registros) == 8
    assert {item["id"] for item in registros} == {item["nacionalizador"]["id"] for item in resultados}


def test_duplicata_concorrente_grava_uma_empresa(registry):
    barrier = threading.Barrier(8)

    def create(_index):
        barrier.wait(timeout=5)
        try:
            service._criar_sync(_request(), "tenant-a")
            return 200
        except HTTPException as erro:
            return erro.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(create, range(8)))
    assert statuses.count(200) == 1
    assert statuses.count(409) == 7
    assert len(service._listar_sync("tenant-a")) == 1


def test_io_executa_em_worker(registry, monkeypatch):
    caller_thread = threading.get_ident()
    threads = []
    tenant_path = service.get_tenant_path

    def observed_path(client_id):
        threads.append(threading.get_ident())
        return tenant_path(client_id)

    monkeypatch.setattr(service, "get_tenant_path", observed_path)

    async def scenario():
        criado = await service.criar_nacionalizador_cadastro(_request(), "tenant-a")
        await service.listar_nacionalizadores_cadastro("tenant-a")
        await service.atualizar_nacionalizador_cadastro(criado["nacionalizador"]["id"], _request(), "tenant-a")

    asyncio.run(scenario())
    assert threads
    assert all(thread != caller_thread for thread in threads)


def test_rotas_expoem_apenas_listagem_inclusao_e_edicao():
    routes = {
        (route.path, method)
        for route in create_cadastro_router().routes
        for method in route.methods or set()
        if "/nacionalizadores" in route.path
    }
    assert routes == {
        ("/api/cadastro/nacionalizadores", "GET"),
        ("/api/cadastro/nacionalizadores", "POST"),
        ("/api/cadastro/nacionalizadores/{nacionalizador_id}", "PUT"),
    }
