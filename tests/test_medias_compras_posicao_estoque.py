from __future__ import annotations

import asyncio
import csv
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.routers.medias_compras import create_medias_compras_router
from backend.services import cadastro_compatibilidade as cadastro_compat
from backend.services import medias_compras_common as common
from backend.services import medias_compras_estoque as stock


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    lojas = {
        "tenant-a": [
            {"store_id": "store-a", "nome": "Loja A", "nomes_anteriores": ["Nome anterior"]},
            {"store_id": "store-b", "nome": "Loja B"},
        ],
        "tenant-b": [{"store_id": "store-a", "nome": "Loja A"}],
    }
    for tenant in lojas:
        (tmp_path / tenant).mkdir()
    monkeypatch.setattr(common, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(cadastro_compat._store, "get_tenant_path", lambda tenant: str(tmp_path / tenant))
    monkeypatch.setattr(cadastro_compat, "_lojas_atuais", lambda tenant: lojas.get(tenant, []))
    return tmp_path, lojas


def _estoque(ambiente, linhas, *, tenant="tenant-a", colunas=None):
    raiz, _ = ambiente
    colunas = colunas or list(dict.fromkeys(chave for linha in linhas for chave in linha))
    with (raiz / tenant / "produtos_compilado.csv").open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=colunas)
        escritor.writeheader()
        escritor.writerows(linhas)


def _pedidos(ambiente, listas, *, tenant="tenant-a"):
    raiz, _ = ambiente
    (raiz / tenant / "listas_pedidos.json").write_text(json.dumps(listas), encoding="utf-8")


def _lista(identidade, *, store_id="store-a", loja="Loja A", quantidade=5, status="Em trânsito", sku="001"):
    return {"id": identidade, "nome_lista": identidade, "store_id": store_id, "loja": loja,
            "status": status, "itens": [{"SKU": sku, "Quantidade": quantidade}]}


def _consultar(*, sku="001", loja="Loja A", store_id="store-a", tenant="tenant-a"):
    return asyncio.run(stock.api_medias_compras_posicao_estoque(
        sku=sku, loja=loja, store_id=store_id, client_id=tenant,
    ))


def test_zero_real_prioriza_identidade_e_nao_soma_sombra_full_ou_outro_tenant(ambiente):
    _estoque(ambiente, [
        {"sku": "001", "store_id": "store-a", "loja_sync": "Loja A", "saldo_loja": 0,
         "saldo_total": 20, "saldo_full": 20, "last_update": "13/10/2026 10:00"},
        {"sku": "001", "store_id": "store-b", "loja_sync": "Loja A", "saldo_loja": 100},
        {"sku": "001", "store_id": "", "loja_sync": "Loja A", "saldo_loja": 99},
        {"sku": "010", "store_id": "store-a", "loja_sync": "Loja A", "saldo_loja": 200},
    ])
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": 77}], tenant="tenant-b")
    _pedidos(ambiente, [_lista("a", quantidade=4), _lista("b", store_id="store-b", quantidade=100)])
    _pedidos(ambiente, [_lista("outro-tenant", quantidade=500)], tenant="tenant-b")

    resposta = _consultar()

    assert resposta == {"sku": "001", "loja": "Loja A", "store_id": "store-a",
                        "saldo_atual_estoque": 0, "estoque_em_transito": 4,
                        "estoque_em_transito_listas": [{"lista_id": "a", "nome_lista": "a", "quantidade": 4}],
                        "estoque_atualizado_em": "13/10/2026 10:00"}
    outro = _consultar(tenant="tenant-b")
    assert outro["saldo_atual_estoque"] == 77 and outro["estoque_em_transito"] == 500


def test_store_id_separa_lojas_homonimas_e_nome_ambiguo_nao_faz_fallback(ambiente):
    _, lojas = ambiente
    lojas["tenant-a"][1]["nome"] = "Loja A"
    _estoque(ambiente, [
        {"sku": "001", "store_id": "store-a", "loja_sync": "Loja A", "saldo_loja": 2},
        {"sku": "001", "store_id": "store-b", "loja_sync": "Loja A", "saldo_loja": 90},
        {"sku": "001", "store_id": "", "loja_sync": "Loja A", "saldo_loja": 300},
    ])
    _pedidos(ambiente, [_lista("a", quantidade=5), _lista("b", store_id="store-b", quantidade=90),
                        _lista("legado", store_id="", quantidade=300)])
    resposta = _consultar()
    assert resposta["saldo_atual_estoque"] == 2 and resposta["estoque_em_transito"] == 5
    with pytest.raises(HTTPException) as erro:
        _consultar(store_id="")
    assert erro.value.status_code == 409


def test_legado_aceita_nome_historico_unico_e_identidade_exata_vence_nome_conflitante(ambiente):
    _estoque(ambiente, [{"sku": "001", "loja_sync": "Nome anterior", "saldo_loja": "1,5"}])
    _pedidos(ambiente, [_lista("antigo", store_id="", loja="Nome anterior", quantidade=2),
                        _lista("id", loja="Loja B", quantidade=3),
                        _lista("outro", store_id="store-b", loja="Loja A", quantidade=50)])
    resposta = _consultar(store_id="", loja="Nome anterior")
    assert resposta["loja"] == "Loja A" and resposta["store_id"] == "store-a"
    assert resposta["saldo_atual_estoque"] == 1.5 and resposta["estoque_em_transito"] == 5
    assert [lista["lista_id"] for lista in resposta["estoque_em_transito_listas"]] == ["antigo", "id"]


@pytest.mark.parametrize("loja,store_id", [("__todas", ""), ("Loja A", "STORE-A"), ("Loja ausente", ""), ("Loja A", "store-estranho")])
def test_escopo_nao_resolvido_rejeita_antes_de_ler_fontes(ambiente, monkeypatch, loja, store_id):
    def proibido(*_args):
        pytest.fail("Escopo invalido nao pode consultar nenhuma fonte.")
    monkeypatch.setattr(stock, "_ler_saldo_sku", proibido)
    monkeypatch.setattr(stock, "_ler_transito_sku", proibido)
    with pytest.raises(HTTPException) as erro:
        _consultar(loja=loja, store_id=store_id)
    assert erro.value.status_code in {400, 409}


@pytest.mark.parametrize("coluna", ["saldo", "saldo_total"])
def test_fallback_de_coluna_aceita_somente_base_antiga_sem_saldo_loja(ambiente, coluna):
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", coluna: "7.25"}])
    resposta = _consultar()
    assert resposta["saldo_atual_estoque"] == 7.25


@pytest.mark.parametrize("saldo", ["", "invalido", "nan", "inf", "-inf"])
def test_saldo_invalido_nao_vira_zero_nem_fallback_total(ambiente, saldo):
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": saldo, "saldo_total": 22}])
    resposta = _consultar()
    assert resposta["saldo_atual_estoque"] is None
    assert resposta["estoque_em_transito"] == 0


@pytest.mark.parametrize("cenario", ["ausente", "sku_ausente", "sem_saldo", "sem_loja", "csv_invalido", "legado_ambiguo"])
def test_estoque_indisponivel_preserva_transito_confirmado(ambiente, cenario):
    raiz, lojas = ambiente
    if cenario == "sku_ausente":
        _estoque(ambiente, [{"sku": "999", "store_id": "store-a", "saldo_loja": 3}])
    elif cenario == "sem_saldo":
        _estoque(ambiente, [{"sku": "001", "store_id": "store-a"}])
    elif cenario == "sem_loja":
        _estoque(ambiente, [{"sku": "001", "saldo_loja": 3}])
    elif cenario == "csv_invalido":
        (raiz / "tenant-a/produtos_compilado.csv").write_text('sku,store_id,saldo_loja\n"001,store-a,3', encoding="utf-8")
    elif cenario == "legado_ambiguo":
        lojas["tenant-a"][1]["nome"] = "Loja A"
        _estoque(ambiente, [{"sku": "001", "loja_sync": "Loja A", "saldo_loja": 300}])
    _pedidos(ambiente, [_lista("confirmado", quantidade=6)])
    resposta = _consultar()
    assert resposta["saldo_atual_estoque"] is None and resposta["estoque_em_transito"] == 6


def test_transito_corrompido_preserva_saldo_real(ambiente):
    raiz, _ = ambiente
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": 0}])
    (raiz / "tenant-a/listas_pedidos.json").write_text("invalid", encoding="utf-8")
    resposta = _consultar()
    assert resposta["saldo_atual_estoque"] == 0
    assert resposta["estoque_em_transito"] is None and resposta["estoque_em_transito_listas"] == []


@pytest.mark.parametrize("quantidade", ["invalido", None, "nan", "inf"])
def test_quantidade_em_transito_invalida_nao_vira_zero(ambiente, quantidade):
    _pedidos(ambiente, [_lista("invalida", quantidade=quantidade)])
    assert _consultar()["estoque_em_transito"] is None


@pytest.mark.parametrize("quantidade", [7, "invalido", "nan"])
def test_origem_unida_nao_duplica_nem_invalida_transito_ativo(ambiente, quantidade):
    _pedidos(ambiente, [
        {**_lista("origem", quantidade=quantidade, status="Analisando orçamento"),
         "unida_em_lista_id": "consolidada"},
        _lista("consolidada", quantidade=7, status="Analisando orçamento"),
        _lista("outra-loja", store_id="store-b", quantidade=200),
    ])
    resposta = _consultar()
    assert resposta["estoque_em_transito"] == 7
    assert [row["lista_id"] for row in resposta["estoque_em_transito_listas"]] == ["consolidada"]


def test_transito_soma_somente_sku_e_status_canonicos_sem_reler_snapshot(ambiente, monkeypatch):
    _pedidos(ambiente, [
        _lista("orcamento", status="Em Orçamento", quantidade=500),
        _lista("recebido", status="Recebido", quantidade=500),
        _lista("cancelado", status="Pedido cancelado", quantidade=500),
        _lista("gerada", status="Lista gerada", quantidade=500),
        _lista("analise", status="Analisando orçamento", quantidade=2),
        _lista("producao", status="Em produção", quantidade=3),
        _lista("outro-sku", sku="010", quantidade=800),
    ])
    original = common._carregar_listas_pedidos
    leituras = []
    def ler(tenant):
        leituras.append(tenant)
        return original(tenant)
    monkeypatch.setattr(common, "_carregar_listas_pedidos", ler)
    resposta = _consultar()
    assert resposta["estoque_em_transito"] == 5 and leituras == ["tenant-a"]
    assert [lista["lista_id"] for lista in resposta["estoque_em_transito_listas"]] == ["analise", "producao"]


def test_consulta_nao_migra_sincroniza_ou_analisa_vendas(ambiente, monkeypatch):
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": 0}])
    raiz, _ = ambiente
    arquivo = raiz / "tenant-a/produtos_compilado.csv"
    antes = arquivo.read_bytes()
    def proibido(*_args, **_kwargs):
        pytest.fail("Hover deve consultar somente fontes locais atuais.")
    monkeypatch.setattr(common, "_migrar_arquivo_legado_para_tenant", proibido, raising=False)
    monkeypatch.setattr(common, "_listar_bancos_vendas_tenant", proibido, raising=False)
    monkeypatch.setattr(common, "_salvar_listas_pedidos", proibido)
    from backend.services import medias_compras_visao, estoque_sync
    monkeypatch.setattr(medias_compras_visao, "api_medias_compras_visao", proibido)
    monkeypatch.setattr(estoque_sync, "sincronizar_estoque", proibido)
    assert _consultar()["saldo_atual_estoque"] == 0
    assert arquivo.read_bytes() == antes
    assert [path.name for path in (raiz / "tenant-a").iterdir()] == ["produtos_compilado.csv"]


def test_rota_get_aditiva_usa_tenant_autenticado_e_valida_sku(ambiente):
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": 8}])
    app = FastAPI()
    app.include_router(create_medias_compras_router())
    app.dependency_overrides[common.get_tenant_id] = lambda: "tenant-a"
    client = TestClient(app)
    resposta = client.get("/api/medias-compras/posicao-estoque", params={"sku": "001", "store_id": "store-a"})
    assert resposta.status_code == 200 and resposta.json()["saldo_atual_estoque"] == 8
    assert client.get("/api/medias-compras/posicao-estoque", params={"store_id": "store-a"}).status_code == 422
    assert client.get("/api/medias-compras/posicao-estoque", params={"sku": " " * 2, "store_id": "store-a"}).status_code == 400
    assert client.get("/api/medias-compras/posicao-estoque", params={"sku": "001"}).status_code == 400
    caminhos = {rota.path for rota in app.routes}
    assert "/api/medias-compras/visao" in caminhos and "/api/medias-compras/listas-pedidos/{lista_id}" in caminhos


def test_resposta_preserva_sku_solicitado_enquanto_lookup_usa_normalizacao_existente(ambiente):
    _estoque(ambiente, [{"sku": "001", "store_id": "store-a", "saldo_loja": 9}])
    resposta = _consultar(sku="1")
    assert resposta["sku"] == "1" and resposta["saldo_atual_estoque"] == 9
