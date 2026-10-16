"""List summaries only enrich the store products used by current orders."""

import copy

import pytest

from backend.services import cadastro_compatibilidade as cadastro
from backend.services import cadastro_lojas_produtos as produtos
from backend.services import medias_compras_common as common
from backend.services import medias_compras_fiscal as fiscal
from backend.services import medias_compras_listas as listas

from test_medias_compras_carregamento import _csv, ambiente


def _resumos(client_id="cliente-a", **kwargs):
    return listas._api_medias_compras_listas_pedidos_sync(
        client_id=client_id, **kwargs,
    )["listas"]


def _contar_cadastro(monkeypatch):
    leituras, enriquecidos = [], []
    ler = produtos._ler_registros_persistidos
    enriquecer = produtos._enriquecer_produto

    def leitura(client_id):
        leituras.append(client_id)
        return ler(client_id)

    def enriquecimento(registro, loja, contexto, **kwargs):
        enriquecidos.append((loja["store_id"], registro["sku"]))
        return enriquecer(registro, loja, contexto, **kwargs)

    monkeypatch.setattr(cadastro, "_ler_registros_persistidos", leitura)
    monkeypatch.setattr(produtos, "_ler_registros_persistidos", leitura)
    monkeypatch.setattr(produtos, "_enriquecer_produto", enriquecimento)
    return leituras, enriquecidos


def test_resumo_filtra_cadastro_antes_de_enriquecer_e_reutiliza_leitura(ambiente, monkeypatch):
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": sku, "sku_normalizado": sku,
         "m3 individual": "0.125"}
        for sku in ("001", "002", "OUTRO")
    ])
    leituras, enriquecidos = _contar_cadastro(monkeypatch)

    resposta = _resumos()

    assert resposta[0]["total_m3"] == 0.25
    assert enriquecidos == [("store-a", "001")]
    assert leituras == ["cliente-a"]


def test_resumo_historico_nao_consulta_cadastro(ambiente, monkeypatch):
    lista = ambiente["lists"]["cliente-a"][0]
    lista["status"] = "Recebido"
    lista["itens"][0].update({"M3": "0.004123456", "M3 individual": "99"})
    original = copy.deepcopy(lista)
    leituras, enriquecidos = _contar_cadastro(monkeypatch)

    assert _resumos() == [common._resumo_lista_pedido(original, {})]
    assert leituras == enriquecidos == []
    assert ambiente["lists"]["cliente-a"][0] == original


def test_resumos_agrupam_skus_por_loja_e_preservam_saida_completa(ambiente, monkeypatch):
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", [
        {"store_id": store, "sku": sku, "sku_normalizado": sku, "m3 individual": volume}
        for store, sku, volume in (
            ("store-a", "001", "0.00003125"), ("store-a", "002", "0.0004"),
            ("store-a", "1.0", "0.75"), ("store-a", "OUTRO", "9"),
            ("store-a", "HIST", "8"), ("store-b", "001", "0.5"),
        )
    ])
    fonte = ambiente["lists"]["cliente-a"] = [
        {"id": "a1", "store_id": "store-a", "loja": "Loja A", "status": "Lista gerada",
         "itens": [{"SKU": "001", "Quantidade": 80, "Valor unidade": 2, "M3": 999}]},
        {"id": "a2", "store_id": "store-a", "loja": "Loja A", "status": "Em Orçamento",
         "itens": [{"SKU": "002", "Quantidade": 2, "Valor total": 7}]},
        {"id": "b1", "store_id": "store-b", "loja": "Loja B", "status": "Analisando orçamento",
         "itens": [{"SKU": "001", "Quantidade": 2}]},
        {"id": "historico", "store_id": "store-a", "loja": "Loja A", "status": "Recebido",
         "itens": [{"SKU": "HIST", "Quantidade": 10, "M3": "0.004123456", "M3 individual": "99"}]},
    ]
    mapas = {
        store: fiscal._construir_mapa_m3_sku("cliente-a", store_id=store)
        for store in ("store-a", "store-b")
    }
    esperado = [common._resumo_lista_pedido(lista, mapas[lista["store_id"]]) for lista in fonte]
    original = copy.deepcopy(fonte)
    construir = listas._construir_mapa_m3_sku
    consultas = []

    def mapa(client_id, **kwargs):
        consultas.append((client_id, kwargs["loja"], kwargs.get("skus")))
        return construir(client_id, **kwargs)

    monkeypatch.setattr(listas, "_construir_mapa_m3_sku", mapa)
    assert _resumos() == esperado
    assert consultas == [
        ("cliente-a", "store-a", ["001", "002"]),
        ("cliente-a", "store-b", ["001"]),
    ]
    assert fonte == original


@pytest.mark.parametrize("sku", ["001", "01", "1", "1.0", "001-A", "SKU:001"])
def test_mapa_filtrado_preserva_identidade_exata_e_aliases(ambiente, sku):
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": value, "sku_normalizado": value,
         "m3 individual": str((index + 1) / 1000)}
        for index, value in enumerate((sku, "OUTRO"))
    ])
    completo = fiscal._construir_mapa_m3_sku("cliente-a", store_id="store-a")
    filtrado = fiscal._construir_mapa_m3_sku("cliente-a", store_id="store-a", skus=[sku])
    assert filtrado[sku] == completo[sku]
    assert set(filtrado) == {sku}
    assert all(valor == completo[chave] for chave, valor in filtrado.items())
    assert "OUTRO" not in filtrado


@pytest.mark.parametrize("itens", [[], [{"SKU": "", "Quantidade": 0}], [{"sku": "001", "Quantidade": 0}]])
def test_filtro_vazio_e_sku_legado_nao_enriquecem_catalogo_inteiro(ambiente, monkeypatch, itens):
    lista = ambiente["lists"]["cliente-a"][0]
    lista["itens"] = itens
    mapa_completo = fiscal._construir_mapa_m3_sku("cliente-a", store_id="store-a")
    esperado = common._resumo_lista_pedido(lista, mapa_completo)
    _, enriquecidos = _contar_cadastro(monkeypatch)

    assert _resumos() == [esperado]
    assert enriquecidos == ([("store-a", "001")] if itens and itens[0].get("sku") else [])


def test_filtro_preserva_sku_zero_e_quantidade_zero(ambiente):
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "0", "sku_normalizado": "0", "m3 individual": "0.125"},
    ])
    lista = ambiente["lists"]["cliente-a"][0]
    lista["itens"] = [{"SKU": "0", "Quantidade": 2}, {"SKU": "AUSENTE", "Quantidade": 0}]
    resumo = _resumos()[0]
    assert resumo["total_m3"] == 0.25
    assert resumo["m3_completo"] is True


def test_resumos_releem_cadastro_entre_gets_e_isolam_tenant(ambiente):
    assert _resumos()[0]["total_m3"] == 0.25
    assert _resumos("cliente-b")[0]["total_m3"] == 0.5
    _csv(ambiente["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "001", "sku_normalizado": "001", "m3 individual": "0.75"},
    ])
    assert _resumos()[0]["total_m3"] == 1.5
    assert _resumos("cliente-b")[0]["total_m3"] == 0.5


def test_resumos_preservam_pendencia_exclusao_e_alias_de_loja(ambiente):
    tenant = ambiente["root"] / "cliente-a"
    _csv(tenant / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": "001", "sku_normalizado": "001", "m3 individual": "0.125"},
        {"store_id": "store-a", "sku": "DEL", "sku_normalizado": "DEL",
         "m3 individual": "0.7", "deleted_at_utc": "2026-10-13"},
    ])
    _csv(tenant / "cadastro_produtos.csv", [
        {"store_id": "store-a", "sku": "DEL", "m3 individual": "99"},
    ])
    lista = ambiente["lists"]["cliente-a"][0]
    lista.pop("store_id")
    lista["loja"] = "Loja Antiga"
    lista["itens"].extend([
        {"SKU": "DEL", "Quantidade": 1, "M3": 99},
        {"SKU": "AUSENTE", "Quantidade": 1, "M3": 99},
    ])
    completo = fiscal._construir_mapa_m3_sku("cliente-a", loja="Loja Antiga")
    esperado = common._resumo_lista_pedido(lista, completo)
    assert _resumos(store_id="store-a") == [esperado]
    assert esperado["total_m3"] == 0.25
    assert esperado["m3_pendente_itens"] == 2
    assert esperado["m3_completo"] is False
    lista["loja"] = "Loja inexistente"
    assert _resumos()[0]["m3_pendente_itens"] == 3
