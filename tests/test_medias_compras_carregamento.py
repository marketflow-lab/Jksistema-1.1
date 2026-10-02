"""Request-scoped Cadastro reuse, preserving fiscal values and photo boundaries."""

import asyncio
import copy
import csv
import json
import logging
from collections import Counter

import pandas as pd
import pytest

from backend.services import cadastro_compatibilidade as cadastro
from backend.services import cadastro_fotos as fotos
from backend.services import cadastro_lojas_produtos as produtos
from backend.services import integracoes, store_public_snapshot
from backend.services import medias_compras_common as common
from backend.services import medias_compras_fiscal as fiscal
from backend.services import medias_compras_listas as listas


def _csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    root = tmp_path / "info"
    stores = {
        "cliente-a": [
            {"store_id": "store-a", "nome": "Loja A", "nomes_anteriores": ["Loja Antiga"]},
            {"store_id": "store-b", "nome": "Loja B"},
        ],
        "cliente-b": [{"store_id": "store-a", "nome": "Loja A"}],
    }
    state = {"root": root, "stores": stores, "refs": {}, "lists": {}, "counts": Counter(), "photo_calls": []}

    def tenant_path(client_id):
        assert client_id in stores
        return str(root / client_id)

    def write_stores(client_id):
        tenant = root / client_id
        (tenant / "lojas_config.json").write_text(json.dumps(stores[client_id]), encoding="utf-8")
        store_public_snapshot.write_snapshot(tenant, store_public_snapshot.build_snapshot(stores[client_id]))

    state["write_stores"] = write_stores
    for client_id, client_stores in stores.items():
        tenant = root / client_id
        tenant.mkdir(parents=True)
        write_stores(client_id)
        (tenant / fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO).write_text(json.dumps({
            "schema": fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
            "strict_store_scope": True, "shared_groups": [],
        }), encoding="utf-8")
        rows = []
        for store in client_stores:
            store_id = store["store_id"]
            segment = fotos._cadastro_store_id_foto_segmento(store_id)
            ref = f"cadastro_fotos/lojas/{segment}/001-{client_id}.png"
            photo = tenant / ref
            photo.parent.mkdir(parents=True)
            photo.write_bytes(b"synthetic-photo")
            state["refs"][client_id, store_id] = ref
            rows.append({"store_id": store_id, "sku": "001", "sku_normalizado": "001",
                         "nome": "Produto de teste", "foto": ref, "row_version": "1",
                         "updated_at_utc": "2026-10-02T12:00:00Z", "deleted_at_utc": ""})
        _csv(tenant / "cadastro_produtos_lojas.csv", rows)
        state["lists"][client_id] = [{"id": "lista", "nome_lista": "Pedido de teste", "loja": "Loja A",
                                        "store_id": "store-a", "itens": [{"SKU": "001", "Quantidade": 2, "Valor unidade": 10}]}]

    for module in (common, fiscal, listas, fotos, produtos):
        monkeypatch.setattr(module, "get_tenant_path", tenant_path)
        monkeypatch.setattr(module, "logger", logging.getLogger(__name__), raising=False)
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(root))
    monkeypatch.setattr(integracoes, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(fotos, "PASTA_INFO", str(root), raising=False)
    monkeypatch.setattr(fiscal, "_caminhos_cadastros_meta", lambda _client: [])

    state["catalog"] = [{"sku": "001", "ncm": "8708.99.90", "cest": "01.001.00", "m3 individual": "0,125",
                         "titulo": "English product", "oem": "OEM-001", "color side": "Left", "link": "https://example.test/product"}]
    state["rates"] = {"87089990": {"ii": 12.5, "ipi": 5, "pis": 2.1, "cofins": 9.65}}
    state["ncm"] = {"87089990": {"descricao_completa": "Descricao fiscal de teste"}}

    def load_catalog(_client):
        state["counts"]["cadastro_principal"] += 1
        return pd.DataFrame(state["catalog"]), ""

    def index(kind):
        def load(_client):
            state["counts"][kind] += 1
            return copy.deepcopy(state["rates" if kind == "indice_aliquotas" else "ncm"])
        return load

    def save(client_id, rows):
        state["counts"]["persistencias"] += 1
        state["lists"][client_id] = copy.deepcopy(rows)

    original_context = cadastro.visao_produtos_cadastro_contexto_loja
    original_photo = fiscal._resolver_foto_cadastro_sku

    def context(client_id, loja):
        state["counts"]["contexto_catalogo"] += 1
        return original_context(client_id, loja)

    def photo(client_id, sku, ref="", store_id=None):
        state["photo_calls"].append((client_id, store_id, sku, ref))
        return original_photo(client_id, sku, ref, store_id)

    monkeypatch.setattr(fiscal, "_carregar_cadastro_para_impostos", load_catalog)
    monkeypatch.setattr(fiscal, "_indice_ncm_referencia_aliquotas", index("indice_aliquotas"))
    monkeypatch.setattr(fiscal, "_indice_ncm_referencia_por_ncm", index("indice_ncm"))
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", context)
    monkeypatch.setattr(fiscal, "_resolver_foto_cadastro_sku", photo)
    monkeypatch.setattr(listas, "_carregar_listas_pedidos", lambda client_id: copy.deepcopy(state["lists"][client_id]))
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", save)
    monkeypatch.setattr(listas, "_limpar_cache_lista_pedido", lambda *_args, **_kwargs: None)
    return state


def _detail(client_id="cliente-a"):
    return asyncio.run(listas.api_medias_compras_lista_pedido_detalhe("lista", client_id=client_id))["lista"]


def test_detalhe_preserva_valores_fiscais_frete_e_metadados(ambiente):
    response = _detail()
    item = response["itens"][0]
    assert item["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
    assert {key: item[key] for key in ("NCM", "CEST", "II", "IPI", "PIS", "COFINS", "M3", "M3 individual", "Frete Internacional")} == {
        "NCM": "87089990", "CEST": "0100100", "II": 12.5, "IPI": 5.0, "PIS": 2.1, "COFINS": 9.65,
        "M3": 0.25, "M3 individual": 0.125, "Frete Internacional": 20.0,
    }
    assert item["Descricao do NCM"] == "Descricao fiscal de teste"
    assert item[common.TITULO_PRODUTO_INGLES_KEY] == "English product"
    assert item[common.COR_LADO_LISTA_PEDIDO_KEY] == "Left"
    assert item["OEM"] == "OEM-001"
    assert item["Link"] == "https://example.test/product"
    assert item["Imposto"] == "II 12.50% | IPI 5.00% | PIS 2.10% | COFINS 9.65%"
    assert response["total_quantidade"] == 2
    assert response["total_usd"] == 20
    assert ambiente["counts"]["contexto_catalogo"] == 1
    assert ambiente["counts"]["persistencias"] == 1
    assert _detail()["itens"] == response["itens"]
    assert ambiente["counts"]["contexto_catalogo"] == 2
    assert ambiente["counts"]["persistencias"] == 1


def test_detalhes_nao_compartilham_contexto_ou_foto_entre_clientes_e_lojas(ambiente):
    for client_id, store_id in (("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a")):
        ambiente["lists"][client_id][0]["store_id"] = store_id
        # Uma referencia de outra loja no item nao substitui a foto canonica.
        ambiente["lists"][client_id][0]["itens"][0]["Foto"] = ambiente["refs"]["cliente-a", "store-b"]
        result = _detail(client_id)
        assert result["itens"][0]["Foto"] == ambiente["refs"][client_id, store_id]
        assert result["store_id"] == store_id
    assert ambiente["counts"]["contexto_catalogo"] == 3
    assert {call[:2] for call in ambiente["photo_calls"]} == {
        ("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a"),
    }


def test_novo_get_le_cadastro_foto_e_aliquota_atualizados(ambiente):
    old = _detail()["itens"][0]
    tenant = ambiente["root"] / "cliente-a"
    ref = ambiente["refs"]["cliente-a", "store-a"].replace(".png", "-nova.png")
    (tenant / ref).write_bytes(b"updated-synthetic-photo")
    _csv(tenant / "cadastro_produtos_lojas.csv", [{"store_id": "store-a", "sku": "001", "sku_normalizado": "001", "foto": ref}])
    ambiente["catalog"][0]["m3 individual"] = "0,250"
    ambiente["rates"]["87089990"]["ii"] = 15.0
    new = _detail()["itens"][0]
    assert old["Foto"] != new["Foto"] == ref
    assert old["M3"] == 0.25 and new["M3"] == 0.5
    assert old["II"] == 12.5 and new["II"] == 15
    assert ambiente["counts"]["contexto_catalogo"] == 2
    assert ambiente["counts"]["cadastro_principal"] == 2
    assert ambiente["counts"]["indice_aliquotas"] == 2
    assert ambiente["counts"]["indice_ncm"] == 2


def test_lista_legada_fixa_store_id_usando_um_contexto(ambiente):
    lista = ambiente["lists"]["cliente-a"][0]
    del lista["store_id"]
    lista["loja"] = "Loja Antiga"
    response = _detail()
    assert response["store_id"] == "store-a"
    assert response["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
    assert ambiente["lists"]["cliente-a"][0]["store_id"] == "store-a"
    assert ambiente["counts"]["contexto_catalogo"] == 1


def test_loja_ambigua_esvazia_resposta_e_preserva_foto_persistida(ambiente):
    ambiente["stores"]["cliente-a"][1]["nome"] = "Loja A"
    ambiente["write_stores"]("cliente-a")
    lista = ambiente["lists"]["cliente-a"][0]
    del lista["store_id"]
    lista["itens"][0]["Foto"] = ambiente["refs"]["cliente-a", "store-a"]
    response = _detail()
    assert response["itens"][0]["Foto"] == ""
    assert response["store_id"] == ""
    assert ambiente["lists"]["cliente-a"][0]["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
    assert ambiente["counts"]["contexto_catalogo"] == 1


def test_contexto_indisponivel_falha_fechado_e_e_recarregado_no_proximo_get(ambiente, monkeypatch):
    context = cadastro.visao_produtos_cadastro_contexto_loja
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise RuntimeError("synthetic unavailable context")

    original_ref = ambiente["refs"]["cliente-a", "store-a"]
    ambiente["lists"]["cliente-a"][0]["itens"][0]["Foto"] = original_ref
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", unavailable)
    assert _detail()["itens"][0]["Foto"] == ""
    assert calls == [("cliente-a", "store-a")]
    assert ambiente["lists"]["cliente-a"][0]["itens"][0]["Foto"] == original_ref
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", context)
    assert _detail()["itens"][0]["Foto"] == original_ref


@pytest.mark.parametrize("client_id,store_id", [("cliente-a", "store-b"), ("cliente-b", "store-a")])
def test_contexto_preparado_de_outro_escopo_e_ignorado(ambiente, client_id, store_id):
    context = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    prepared = fiscal._ContextoCadastroLista("cliente-a", "store-a", context)
    context["produtos"][0]["foto"] = "https://example.test/changed-outside-snapshot.png"
    enriched = fiscal._enriquecer_itens_lista_pedido_com_impostos(client_id, [{"SKU": "001"}], loja=store_id, contexto_cadastro=prepared)
    assert enriched[0]["Foto"] == ambiente["refs"][client_id, store_id]
    assert ambiente["counts"]["contexto_catalogo"] == 2
    matching = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", [{"SKU": "001"}], loja="store-a", contexto_cadastro=prepared)
    assert matching[0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
    assert ambiente["counts"]["contexto_catalogo"] == 2


def test_sem_loja_preserva_projecao_global_sem_cruzar_fotos(ambiente):
    lista = ambiente["lists"]["cliente-a"][0]
    del lista["store_id"]
    lista["loja"] = "__todas"
    lista["itens"][0]["Foto"] = ambiente["refs"]["cliente-a", "store-a"]
    assert _detail()["itens"][0]["Foto"] == ""
    assert ambiente["counts"]["contexto_catalogo"] == 1


def test_memo_foto_distingue_referencia_e_sku_exatos(ambiente, monkeypatch):
    ambiente["catalog"] = [{"sku": "001"}]
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", lambda *_args: {
        "scope": "store", "store_id": "store-a", "produtos": [], "skus_controlados": set(),
    })
    calls = []

    def photo(client_id, sku, ref="", store_id=None):
        calls.append((client_id, store_id, sku, ref))
        return ref or f"https://example.test/{sku}.png"

    monkeypatch.setattr(fiscal, "_resolver_foto_cadastro_sku", photo)
    rows = [{"SKU": "001"}, {"SKU": "1"}, {"SKU": "001", "Foto": "https://example.test/a.png"},
            {"SKU": "001", "Foto": "https://example.test/b.png"}, {"SKU": "001", "Foto": "https://example.test/a.png"}]
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", rows, loja="store-a")
    assert [row["Foto"] for row in result] == ["https://example.test/001.png", "https://example.test/1.png",
        "https://example.test/a.png", "https://example.test/b.png", "https://example.test/a.png"]
    assert calls == [("cliente-a", "store-a", "001", ""), ("cliente-a", "store-a", "1", ""),
                     ("cliente-a", "store-a", "001", "https://example.test/a.png"),
                     ("cliente-a", "store-a", "001", "https://example.test/b.png")]


def test_memo_preserva_precedencia_de_metadados_fiscais_e_foto_de_cadastro_auxiliar(ambiente, monkeypatch):
    ambiente["catalog"] = [{"sku": "001", "description": "Descricao principal", "ncm": "87089990", "m3 individual": "0.05"}]
    backup = ambiente["root"] / "cliente-a" / "cadastro_produtos_backup.csv"
    ref = "https://example.test/foto-backup.png"
    _csv(backup, [{"sku": "001", "titulo": "Titulo do cadastro auxiliar", "foto": ref, "m3 individual": "0.1"}])
    monkeypatch.setattr(fiscal, "_caminhos_cadastros_meta", lambda _client: [str(backup)])
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", lambda *_args: {
        "scope": "store", "store_id": "store-a", "produtos": [], "skus_controlados": set(),
    })
    calls = []

    def photo(client_id, sku, foto_ref="", store_id=None):
        calls.append((client_id, store_id, sku, foto_ref))
        return foto_ref

    monkeypatch.setattr(fiscal, "_resolver_foto_cadastro_sku", photo)
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", [{"SKU": "001", "Quantidade": 2}], loja="store-a")[0]
    assert result["Foto"] == ref
    assert result[common.TITULO_PRODUTO_INGLES_KEY] == "Titulo do cadastro auxiliar"
    assert result["M3 individual"] == 0.05
    assert result["M3"] == 0.1
    assert result["II"] == 12.5
    assert calls == [("cliente-a", "store-a", "001", ""), ("cliente-a", "store-a", "001", ref)]


def test_tres_itens_sinteticos_resolvem_foto_negativa_uma_vez_por_get(ambiente, monkeypatch):
    skus = ["001", "002", "003"]
    ambiente["catalog"] = [{"sku": sku} for sku in skus]
    ambiente["lists"]["cliente-a"][0]["itens"] = [{"SKU": sku} for sku in skus]

    def context(_client, loja):
        ambiente["counts"]["contexto_catalogo"] += 1
        return {"scope": "store", "store_id": loja, "produtos": [{"sku": sku} for sku in skus], "skus_controlados": set()}

    def photo(client_id, sku, ref="", store_id=None):
        ambiente["photo_calls"].append((client_id, store_id, sku, ref))
        return ""

    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", context)
    monkeypatch.setattr(fiscal, "_resolver_foto_cadastro_sku", photo)
    first = _detail()["itens"]
    second = _detail()["itens"]
    assert first == second
    assert ambiente["counts"] == Counter(contexto_catalogo=2, cadastro_principal=2, indice_aliquotas=2, indice_ncm=2, persistencias=1)
    assert len(ambiente["photo_calls"]) == 6
    assert Counter(ambiente["photo_calls"]) == Counter({("cliente-a", "store-a", sku, ""): 2 for sku in skus})
