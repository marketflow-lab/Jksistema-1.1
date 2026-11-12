import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import medias_compras_common as common


URL = "/api/medias-compras/listas-pedidos/preferencias-colunas"


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    tenant = ["tenant-a"]
    monkeypatch.setattr(common, "get_tenant_path", lambda client_id: str(tmp_path / client_id))
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: tenant[0]
    return TestClient(app), tenant, tmp_path


def test_visibilidade_persistida_reaberta_e_isolada_por_tenant(ambiente):
    client, tenant, raiz = ambiente
    assert client.get(URL).json()["colunas_ocultas"] == []
    resposta = client.put(URL, json={
        "ordem_colunas": ["sku", "margens_loja", "m3"],
        "larguras_colunas": {"sku": 110, "margens_loja": 185, "m3": 100},
        "colunas_ocultas": [" foto ", "m3", "foto", "", "  "],
    })
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["colunas_ocultas"] == ["foto", "m3"]
    assert dados["ordem_colunas"] == ["sku", "margens_loja", "m3"]
    assert dados["larguras_colunas"]["margens_loja"] == 185
    gravado = json.loads((raiz / "tenant-a" / "importacoes_lista_colunas_prefs.json").read_text(encoding="utf-8"))
    assert gravado["colunas_ocultas"] == ["foto", "m3"]
    assert client.get(URL).json()["colunas_ocultas"] == ["foto", "m3"]

    tenant[0] = "tenant-b"
    assert client.get(URL).json()["colunas_ocultas"] == []
    assert client.put(URL, json={"colunas_ocultas": ["valor_usd"]}).json()["colunas_ocultas"] == ["valor_usd"]
    tenant[0] = "tenant-a"
    assert client.get(URL).json()["colunas_ocultas"] == ["foto", "m3"]


def test_put_antigo_preserva_visibilidade_e_lista_vazia_restabelece_colunas(ambiente):
    client, _, _ = ambiente
    assert client.put(URL, json={"colunas_ocultas": ["foto", "ultimo_custo_cadastrado"]}).status_code == 200
    for extra in ({}, {"colunas_ocultas": None}):
        resposta = client.put(URL, json={
            "ordem_colunas": ["m3", "sku"], "larguras_colunas": {"m3": 240}, **extra,
        })
        assert resposta.status_code == 200
        assert resposta.json()["colunas_ocultas"] == ["foto", "ultimo_custo_cadastrado"]
        assert resposta.json()["ordem_colunas"] == ["m3", "sku"]
        assert client.get(URL).json()["larguras_colunas"] == {"m3": 240}
    resposta = client.put(URL, json={"colunas_ocultas": []})
    assert resposta.status_code == 200
    assert resposta.json()["colunas_ocultas"] == []
    assert client.get(URL).json()["colunas_ocultas"] == []


@pytest.mark.parametrize("conteudo", [
    {"ordem_colunas": ["sku"], "larguras_colunas": {"sku": 110}},
    {"colunas_ocultas": "foto"}, [], "arquivo-invalido",
])
def test_arquivo_antigo_ou_invalido_nao_oculta_colunas(ambiente, conteudo):
    client, _, raiz = ambiente
    pasta = raiz / "tenant-a"
    pasta.mkdir()
    arquivo = pasta / "importacoes_lista_colunas_prefs.json"
    arquivo.write_text(conteudo if isinstance(conteudo, str) else json.dumps(conteudo), encoding="utf-8")
    resposta = client.get(URL)
    assert resposta.status_code == 200
    assert resposta.json()["colunas_ocultas"] == []


def test_persistencia_limita_colunas_e_preserva_larguras_validas(ambiente):
    _, _, _ = ambiente
    resultado = common._salvar_preferencias_colunas_importacoes("tenant-a", {
        "colunas_ocultas": [f"coluna_{i}" for i in range(5001)],
        "larguras_colunas": {"estreita": 1, "larga": 5000, "invalida": "x"},
    })
    assert len(resultado["colunas_ocultas"]) == 5000
    assert resultado["larguras_colunas"] == {"estreita": 12, "larga": 1200}
    assert common._carregar_preferencias_colunas_importacoes("tenant-a")["colunas_ocultas"] == resultado["colunas_ocultas"]
