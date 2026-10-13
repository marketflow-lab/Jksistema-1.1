"""Request-scoped Cadastro reuse, preserving fiscal values and photo boundaries."""

import asyncio
import copy
import csv
import json
import logging
import threading
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
                         "nome": "Produto de teste", "foto": ref, "m3 individual": {("cliente-a", "store-a"): "0,125", ("cliente-a", "store-b"): "0,900", ("cliente-b", "store-a"): "0,250"}[client_id, store_id], "row_version": "1",
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

    state["catalog"] = [{"sku": "001", "ncm": "8708.99.90", "cest": "01.001.00",
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

    def context(client_id, loja, **kwargs):
        state["counts"]["contexto_catalogo"] += 1
        return original_context(client_id, loja, **kwargs)

    def photo(client_id, sku, ref="", store_id=None, **kwargs):
        state["photo_calls"].append((client_id, store_id, sku, ref))
        return original_photo(client_id, sku, ref, store_id, **kwargs)

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
        expected_m3 = {("cliente-a", "store-a"): 0.125, ("cliente-a", "store-b"): 0.9, ("cliente-b", "store-a"): 0.25}[client_id, store_id]
        assert result["itens"][0]["M3 individual"] == expected_m3
        assert result["itens"][0]["M3"] == expected_m3 * 2
        assert result["itens"][0]["Cubagem loja"] == store_id
    assert ambiente["counts"]["contexto_catalogo"] == 3
    assert {call[:2] for call in ambiente["photo_calls"]} == {
        ("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a"),
    }


def test_novo_get_le_cadastro_foto_e_aliquota_atualizados(ambiente):
    old = _detail()["itens"][0]
    tenant = ambiente["root"] / "cliente-a"
    ref = ambiente["refs"]["cliente-a", "store-a"].replace(".png", "-nova.png")
    (tenant / ref).write_bytes(b"updated-synthetic-photo")
    _csv(tenant / "cadastro_produtos_lojas.csv", [{"store_id": "store-a", "sku": "001", "sku_normalizado": "001", "foto": ref, "m3 individual": "0,250"}])
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

    def unavailable(*args, **_kwargs):
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
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", lambda *_args, **_kwargs: {
        "scope": "store", "store_id": "store-a", "produtos": [], "skus_controlados": set(),
    })
    calls = []

    def photo(client_id, sku, ref="", store_id=None, **kwargs):
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
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", lambda *_args, **_kwargs: {
        "scope": "store", "store_id": "store-a", "produtos": [], "skus_controlados": set(),
    })
    calls = []

    def photo(client_id, sku, foto_ref="", store_id=None, **_kwargs):
        calls.append((client_id, store_id, sku, foto_ref))
        return foto_ref

    monkeypatch.setattr(fiscal, "_resolver_foto_cadastro_sku", photo)
    result = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", [{"SKU": "001", "Quantidade": 2, "OEM": "OEM-ITEM"}], loja="store-a")[0]
    assert result["Foto"] == ref
    assert result[common.TITULO_PRODUTO_INGLES_KEY] == "Titulo do cadastro auxiliar"
    assert result["Cubagem status"] == "pendente"
    assert result["M3 individual"] == ""
    assert result["M3"] == ""
    assert result["NCM"] == "87089990"
    assert result["OEM"] == "OEM-ITEM"
    assert result["II"] == 12.5
    assert calls == [("cliente-a", "store-a", "001", ""), ("cliente-a", "store-a", "001", ref)]


def test_tres_itens_sinteticos_resolvem_foto_negativa_uma_vez_por_get(ambiente, monkeypatch):
    skus = ["001", "002", "003"]
    ambiente["catalog"] = [{"sku": sku} for sku in skus]
    ambiente["lists"]["cliente-a"][0]["itens"] = [{"SKU": sku} for sku in skus]

    def context(_client, loja, **_kwargs):
        ambiente["counts"]["contexto_catalogo"] += 1
        return {"scope": "store", "store_id": loja, "produtos": [{"sku": sku} for sku in skus], "skus_controlados": set()}

    def photo(client_id, sku, ref="", store_id=None, **kwargs):
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


def test_retry_no_worker_recarrega_contexto_apos_troca_concorrente_da_loja(ambiente, monkeypatch):
    common._salvar_listas_pedidos("cliente-a", ambiente["lists"]["cliente-a"])
    monkeypatch.setattr(listas, "_carregar_listas_pedidos", common._carregar_listas_pedidos)
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", common._salvar_listas_pedidos)
    load_catalog = fiscal._carregar_cadastro_para_impostos
    caller_thread = threading.get_ident()
    worker_threads = []

    def catalog_during_concurrent_edit(client_id):
        worker_threads.append(threading.get_ident())
        if len(worker_threads) == 1:
            edited = common._carregar_listas_pedidos(client_id)
            edited[0]["store_id"] = "store-b"
            edited[0]["itens"][0]["Quantidade"] = 3
            edited[0]["itens"][0]["compra_aprovada"] = True
            common._salvar_listas_pedidos(client_id, edited)
        return load_catalog(client_id)

    monkeypatch.setattr(fiscal, "_carregar_cadastro_para_impostos", catalog_during_concurrent_edit)
    result = _detail()
    current = common._carregar_listas_pedidos("cliente-a")[0]
    assert result["store_id"] == current["store_id"] == "store-b"
    assert result["itens"] == current["itens"]
    assert result["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-b"]
    assert result["itens"][0]["Quantidade"] == 3
    assert result["itens"][0]["compra_aprovada"] is True
    assert result["itens"][0]["M3 individual"] == 0.9
    assert result["itens"][0]["M3"] == 2.7
    assert result["itens"][0]["Cubagem loja"] == "store-b"
    assert result["itens"][0]["Frete Internacional"] == 30.0
    assert ambiente["counts"]["contexto_catalogo"] == 2
    assert len(worker_threads) == 2
    assert all(thread != caller_thread for thread in worker_threads)
    assert {call[:2] for call in ambiente["photo_calls"]} == {("cliente-a", "store-a"), ("cliente-a", "store-b")}


def test_visao_filtra_antes_de_enriquecer_e_reutiliza_registros(ambiente, monkeypatch):
    tenant = ambiente["root"] / "cliente-a"
    _csv(tenant / "cadastro_produtos_lojas.csv", [
        {"store_id": store_id, "sku": sku, "sku_normalizado": sku, "nome": f"Produto {sku}"}
        for store_id, sku in (("store-a", "001"), ("store-a", "1.0"),
                              ("store-a", "002"), ("store-a", "001-A"),
                              ("store-a", "SKU:001"), ("store-b", "001"))
    ])
    full = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    reads, enriched = [], []
    original_read = produtos._ler_registros_persistidos
    original_enrich = produtos._enriquecer_produto

    def read(client_id):
        reads.append(client_id)
        return original_read(client_id)

    def enrich(record, store, context, **kwargs):
        enriched.append((store["store_id"], record["sku"]))
        return original_enrich(record, store, context, **kwargs)

    monkeypatch.setattr(cadastro, "_ler_registros_persistidos", read)
    monkeypatch.setattr(produtos, "_ler_registros_persistidos", read)
    monkeypatch.setattr(produtos, "_enriquecer_produto", enrich)
    filtered = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=["1"])
    assert filtered["produtos"] == [row for row in full["produtos"] if row["sku"] in {"001", "1.0"}]
    assert filtered["skus_controlados"] == full["skus_controlados"]
    assert reads == ["cliente-a"]
    assert enriched == [("store-a", "001"), ("store-a", "1.0")]
    assert cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=[])["produtos"] == []


def test_mapa_de_fotos_carregado_uma_vez_sem_cruzar_escopos(ambiente, monkeypatch):
    maps = {}
    calls = []

    def load(client_id, store_id):
        calls.append((client_id, store_id))
        return {"exact:001": ambiente["refs"][client_id, store_id], "soft:1": ambiente["refs"][client_id, store_id]}

    monkeypatch.setattr(fotos, "_cadastro_mapa_fotos_locais", load)
    for client_id, store_id in (("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a")):
        expected = ambiente["refs"][client_id, store_id]
        assert common._resolver_foto_cadastro_sku(client_id, "001", store_id=store_id, mapas_fotos=maps) == expected
        assert common._resolver_foto_cadastro_sku(client_id, "1", store_id=store_id, mapas_fotos=maps) == expected
    assert calls == [("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a")]
    # Uma referencia explicita de outra loja continua sendo validada, mesmo com cache.
    assert common._resolver_foto_cadastro_sku(
        "cliente-a", "001", ambiente["refs"]["cliente-a", "store-b"], "store-a", mapas_fotos=maps,
    ) == ""
    common._resolver_foto_cadastro_sku("cliente-a", "001", store_id="store-a", mapas_fotos={})
    assert calls[-1] == ("cliente-a", "store-a") and len(calls) == 4


def test_lista_reutiliza_mapa_negativo_entre_skus_no_mesmo_get(ambiente, monkeypatch):
    ambiente["catalog"] = [{"sku": "001"}, {"sku": "002"}, {"sku": "003"}]
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", lambda *_args, **_kwargs: {
        "scope": "store", "store_id": "store-a", "produtos": [], "skus_controlados": set(),
    })
    calls = []
    monkeypatch.setattr(fotos, "_cadastro_mapa_fotos_locais", lambda *scope: calls.append(scope) or {})
    items = [{"SKU": sku} for sku in ("001", "002", "003", "1")]
    first = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", items, loja="store-a")
    second = fiscal._enriquecer_itens_lista_pedido_com_impostos("cliente-a", items, loja="store-a")
    assert first == second
    assert all(row["Foto"] == "" for row in first)
    assert calls == [("cliente-a", "store-a"), ("cliente-a", "store-a")]


def test_filtro_preserva_saida_fiscal_frete_e_fotos_completa(ambiente):
    tenant = ambiente["root"] / "cliente-a"
    own_ref = ambiente["refs"]["cliente-a", "store-a"]
    other_ref = ambiente["refs"]["cliente-a", "store-b"]
    _csv(tenant / "cadastro_produtos_lojas.csv", [
        {"store_id": "store-a", "sku": sku, "sku_normalizado": sku, "foto": own_ref,
         "m3 individual": {"1": "0.125", "002": "0.25"}.get(sku, "")}
        for sku in ("1", "002", "003", "001-A", "SKU:001")
    ] + [{"store_id": "store-b", "sku": "001", "sku_normalizado": "001", "foto": other_ref}])
    ambiente["catalog"].append({"sku": "002", "ncm": "87089990"})
    items = [{"SKU": "1", "Quantidade": 2, "Valor unidade": 10, "Foto": other_ref},
             {"SKU": "002", "Quantidade": 3, "Valor unidade": 20}]
    full = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a")
    filtered = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "store-a", skus=[item["SKU"] for item in items])
    assert len(full["produtos"]) == 5 and len(filtered["produtos"]) == 2
    baseline = fiscal._recalcular_frete_internacional_itens_lista(
        "cliente-a", items, loja="store-a", contexto_cadastro=fiscal._ContextoCadastroLista("cliente-a", "store-a", full),
    )
    optimized = fiscal._recalcular_frete_internacional_itens_lista(
        "cliente-a", items, loja="store-a", contexto_cadastro=fiscal._ContextoCadastroLista("cliente-a", "store-a", filtered),
    )
    assert optimized == baseline
    assert all(item["Foto"] == own_ref for item in optimized)
    assert optimized[0]["M3"] == 0.25 and optimized[1]["M3"] == 0.75
    assert [item["Frete Internacional"] for item in optimized] == [20, 60]


def test_projecao_global_filtrada_preserva_conflitos_com_iterador(ambiente):
    full = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "__todas")
    filtered = cadastro.visao_produtos_cadastro_contexto_loja("cliente-a", "__todas", skus=iter(["1"]))
    assert filtered == full
    assert filtered["produtos"][0]["foto"] == ""


def test_detalhe_filtra_sku_legado_minusculo(ambiente):
    ambiente["lists"]["cliente-a"][0]["itens"] = [{"sku": "001", "Quantidade": 2, "Valor unidade": 10}]
    assert _detail()["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]


def test_detalhe_salvo_preserva_snapshot_zeros_sem_cadastro_ou_gravacao(ambiente, monkeypatch):
    from backend.services import blocking_workers

    lista = ambiente["lists"]["cliente-a"][0]
    lista["updated_at"] = "2026-10-02T12:00:00"
    lista["itens"] = [{
        "SKU": "001", "Quantidade": 0, "Valor unidade": 0, "Valor total": 0,
        "M3": 0, "M3 individual": 0, "Frete Internacional": 0, "II": 0,
        "Foto": ambiente["refs"]["cliente-a", "store-a"],
        "OEM": "000", "Link": "https://example.test/salvo", "aprovado": False,
        "extra": {"valor": 0, "texto": " texto literal "},
    }, {"SKU": "002", "Quantidade": 2, "Valor unidade": 3.5, "Valor total": 7}]
    original = copy.deepcopy(lista)

    def forbidden(*_args, **_kwargs):
        pytest.fail("A abertura do snapshot executou trabalho de complemento ou gravacao")

    for module, name in (
        (blocking_workers, "run_heavy"),
        (cadastro, "visao_produtos_cadastro_contexto_loja"),
        (fiscal, "_carregar_cadastro_para_impostos"),
        (fiscal, "_caminhos_cadastros_meta"),
        (fiscal, "_resolver_foto_cadastro_sku"),
        (fiscal, "_indice_ncm_referencia_aliquotas"),
        (fiscal, "_indice_ncm_referencia_por_ncm"),
        (listas, "_recalcular_frete_internacional_itens_lista"),
        (listas, "_salvar_listas_pedidos"),
        (listas, "_limpar_cache_lista_pedido"),
    ):
        monkeypatch.setattr(module, name, forbidden)

    response = asyncio.run(listas.api_medias_compras_lista_pedido_detalhe(
        "lista", client_id="cliente-a", somente_salvos=True,
    ))["lista"]
    expected_items = copy.deepcopy(original["itens"])
    expected_items[0][common.CBM_LISTA_PEDIDO_KEY] = "0"
    assert response["itens"] == expected_items
    assert response["updated_at"] == original["updated_at"]
    assert response["total_quantidade"] == 2
    assert response["total_usd"] == 7
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter()
    assert ambiente["photo_calls"] == []


@pytest.mark.parametrize("client_id,store_id", [
    ("cliente-a", "store-a"), ("cliente-a", "store-b"), ("cliente-b", "store-a"),
])
def test_snapshot_salvo_valida_foto_por_tenant_e_loja(ambiente, client_id, store_id):
    lista = ambiente["lists"][client_id][0]
    lista["store_id"] = store_id
    ref = ambiente["refs"][client_id, store_id]
    lista["itens"][0]["Foto"] = ref
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id=client_id, somente_salvos=True,
    )["lista"]
    assert response["itens"][0]["Foto"] == ref
    assert response["store_id"] == store_id
    assert ambiente["counts"] == Counter()


@pytest.mark.parametrize("reference", [
    "cadastro_fotos/lojas/{other_segment}/001.png",
    "/api/cadastro/foto/cliente-b/lojas/{own_segment}/001.png",
    "https://example.test/api/cadastro/foto/cliente-b/lojas/{own_segment}/001.png",
    "%2Fapi%2Fcadastro%2Ffoto%2Fcliente-b%2Flojas%2F{own_segment}%2F001.png",
    "cadastro_fotos/001.png",
    "cadastro_fotos/lojas/{own_segment}/../001.png",
])
def test_snapshot_salvo_limpa_fotos_locais_fora_do_escopo_sem_persistir(ambiente, reference):
    lista = ambiente["lists"]["cliente-a"][0]
    ref = reference.format(
        own_segment=fotos._cadastro_store_id_foto_segmento("store-a"),
        other_segment=fotos._cadastro_store_id_foto_segmento("store-b"),
    )
    lista["itens"][0].update({"Foto": ref, "cg Foto": ref, "Quantidade": 0, "II": 0})
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    assert response["itens"][0]["Foto"] == response["itens"][0]["cg Foto"] == ""
    assert response["itens"][0]["Quantidade"] == response["itens"][0]["II"] == 0
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter()


def test_snapshot_sem_store_id_nao_resolve_nome_ambiguo_nem_foto_local(ambiente):
    lista = ambiente["lists"]["cliente-a"][0]
    lista.pop("store_id")
    ambiente["stores"]["cliente-a"][1]["nome"] = "Loja A"
    ambiente["write_stores"]("cliente-a")
    lista["itens"][0]["Foto"] = ambiente["refs"]["cliente-a", "store-a"]
    lista["itens"].append({"SKU": "002", "Quantidade": 1, "Foto": "https://example.test/photo.png"})
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    assert response["store_id"] == ""
    assert response["itens"][0]["Foto"] == ""
    assert response["itens"][1]["Foto"] == "https://example.test/photo.png"
    assert "store_id" not in lista
    assert ambiente["counts"] == Counter()


def test_query_snapshot_e_opt_in_e_default_continua_enriquecendo(ambiente):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.dependency_overrides[common.get_tenant_id] = lambda: "cliente-a"
    app.add_api_route("/listas/{lista_id}", listas.api_medias_compras_lista_pedido_detalhe)
    with TestClient(app) as client:
        saved = client.get("/listas/lista?somente_salvos=true")
        assert saved.status_code == 200
        assert saved.json()["lista"]["itens"] == ambiente["lists"]["cliente-a"][0]["itens"]
        assert ambiente["counts"] == Counter()
        full = client.get("/listas/lista")
        assert full.status_code == 200
        assert full.json()["lista"]["itens"][0]["Foto"] == ambiente["refs"]["cliente-a", "store-a"]
        assert ambiente["counts"]["cadastro_principal"] == 1
        assert ambiente["counts"]["persistencias"] == 1


def test_snapshot_inexistente_mantem_404(ambiente):
    with pytest.raises(listas.HTTPException) as error:
        listas._api_medias_compras_lista_pedido_detalhe_sync(
            "inexistente", client_id="cliente-a", somente_salvos=True,
        )
    assert error.value.status_code == 404
    assert ambiente["counts"] == Counter()


@pytest.mark.parametrize("field", ["Picture", "image"])
def test_snapshot_legado_materializa_aliases_do_editor_sem_gravar(ambiente, field):
    lista = ambiente["lists"]["cliente-a"][0]
    lista["itens"] = [{
        "sku": "001", "quantity": 2, "Cost": "10.00", "Sub-total(USD)": "20.00",
        field: ambiente["refs"]["cliente-a", "store-a"],
        "Description": " Legacy product ", "OEM/ Model": "OEM-001",
        "Color side": "Left", "Link Aliexpress": "https://example.test/salvo",
        "Estimated CBM": "0.25", "Estimated Weight": "2.50", "Packaging": "Box",
        "CBM individual": "0.125", "extra": {"literal": " unchanged "},
    }]
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    item = response["itens"][0]
    assert {key: item[key] for key in original["itens"][0]} == original["itens"][0]
    assert {key: item[key] for key in (
        "SKU", "Quantidade", "Valor unidade", "Valor total", "Foto", "OEM", "Link",
        common.TITULO_PRODUTO_INGLES_KEY, common.COR_LADO_LISTA_PEDIDO_KEY,
        common.CBM_LISTA_PEDIDO_KEY, common.PESO_LISTA_PEDIDO_KEY,
        common.EMBALAGEM_LISTA_PEDIDO_KEY, "M3 individual",
    )} == {
        "SKU": "001", "Quantidade": 2, "Valor unidade": 10, "Valor total": 20,
        "Foto": ambiente["refs"]["cliente-a", "store-a"], "OEM": "OEM-001",
        "Link": "https://example.test/salvo", common.TITULO_PRODUTO_INGLES_KEY: "Legacy product",
        common.COR_LADO_LISTA_PEDIDO_KEY: "Left", common.CBM_LISTA_PEDIDO_KEY: "0.25",
        common.PESO_LISTA_PEDIDO_KEY: "2.50", common.EMBALAGEM_LISTA_PEDIDO_KEY: "Box",
        "M3 individual": "0.125",
    }
    assert response["total_quantidade"] == 2 and response["total_usd"] == 20
    assert "Frete Internacional" not in item and "MOQ" not in item
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter()
    assert ambiente["photo_calls"] == []


def test_snapshot_legado_materializa_zeros_e_campos_vazios_sem_sobrescrever_canonicos(ambiente):
    lista = ambiente["lists"]["cliente-a"][0]
    lista["itens"] = [{
        "SKU": " 001 ", "sku": "999", "Quantidade": 0, "quantity": 99,
        "Valor unidade": 0, "Cost": 88, "Valor total": 0, "Sub-total(USD)": 77,
        "OEM": " texto literal ", "OEM/ Model": "outro",
        common.CBM_LISTA_PEDIDO_KEY: 0, "Estimated CBM": 55,
        common.PESO_LISTA_PEDIDO_KEY: " 0.00 ", "Estimated Weight": 44,
    }, {
        "SKU": " ", "sku": "002", "Quantidade": None, "quantity": 0,
        "Valor unidade": "", "Cost": 0, "Valor total": " ", "Sub-total(USD)": 0,
    }]
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    assert response["itens"][0] == original["itens"][0]
    assert response["itens"][1] == {
        **original["itens"][1], "SKU": "002", "Quantidade": 0,
        "Valor unidade": 0, "Valor total": 0,
    }
    assert response["total_quantidade"] == 0 and response["total_usd"] == 0
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter()


@pytest.mark.parametrize("field", ["Picture", "image"])
def test_snapshot_valida_foto_apos_materializar_alias_legado(ambiente, field):
    lista = ambiente["lists"]["cliente-a"][0]
    ref = ambiente["refs"]["cliente-a", "store-b"]
    lista["itens"] = [{"sku": "001", "quantity": 2, "Cost": 10, field: ref}]
    original = copy.deepcopy(lista)
    item = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]["itens"][0]
    assert item["Foto"] == item[field] == ""
    assert item["SKU"] == "001" and item["Quantidade"] == 2 and item["Valor unidade"] == 10
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter()


def _bloquear_complementos_cubagem_snapshot(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("O snapshot rapido consultou cubagem atual ou gravou dados")

    for module, name in (
        (cadastro, "visao_produtos_cadastro_contexto_loja"),
        (fiscal, "resolver_cubagem_produtos"),
        (fiscal, "_carregar_cadastro_para_impostos"),
        (fiscal, "_caminhos_cadastros_meta"),
        (listas, "_recalcular_frete_internacional_itens_lista"),
        (listas, "_salvar_listas_pedidos"),
    ):
        monkeypatch.setattr(module, name, forbidden)


def test_snapshot_rapido_cubagem_historica_preserva_total_e_metadados(ambiente, monkeypatch):
    _bloquear_complementos_cubagem_snapshot(monkeypatch)
    lista = ambiente["lists"]["cliente-a"][0]
    lista["status"] = "Recebido"
    lista["itens"] = [{
        "SKU": "001", "Quantidade": 10, "Valor unidade": 2,
        "M3": "0.00347", "M3 individual": "0.00009",
        "Cubagem status": "historico", "Cubagem fonte": " Packing List gravada ",
        "Cubagem unidade": "par", "Cubagem data": "2026-10-02T12:00:00Z",
        "Cubagem loja": "store-a",
    }]
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    item = response["itens"][0]
    assert {key: item[key] for key in original["itens"][0]} == original["itens"][0]
    assert item[common.CBM_LISTA_PEDIDO_KEY] == "0.00347"
    assert response["total_m3"] == 0.00347
    assert response["m3_completo"] is True and response["m3_pendente_itens"] == 0
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter() and ambiente["photo_calls"] == []


def test_snapshot_rapido_cubagem_pendente_nao_reanima_aliases_residuais(ambiente, monkeypatch):
    _bloquear_complementos_cubagem_snapshot(monkeypatch)
    lista = ambiente["lists"]["cliente-a"][0]
    lista["status"] = "Lista gerada"
    lista["itens"] = [{
        "SKU": "001", "Quantidade": 2, "Valor unidade": 10,
        "M3": "", "M3 individual": "", common.CBM_LISTA_PEDIDO_KEY: "",
        "Cubagem status": "pendente", "Cubagem fonte": "", "Cubagem loja": "store-a",
        "CBM individual": "99", "Estimated CBM": "198",
    }]
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    assert response["itens"] == original["itens"]
    assert response["total_m3"] == 0
    assert response["m3_completo"] is False and response["m3_pendente_itens"] == 1
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter() and ambiente["photo_calls"] == []


def test_snapshot_rapido_cubagem_aliases_preserva_precisao_sem_cadastro(ambiente, monkeypatch):
    _bloquear_complementos_cubagem_snapshot(monkeypatch)
    lista = ambiente["lists"]["cliente-a"][0]
    lista["status"] = "Lista gerada"
    lista["itens"] = [{
        "sku": "001", "quantity": 80, "Cost": 2,
        "CBM individual": "3.125e-5", "Estimated CBM": "0.0025",
    }]
    original = copy.deepcopy(lista)
    response = listas._api_medias_compras_lista_pedido_detalhe_sync(
        "lista", client_id="cliente-a", somente_salvos=True,
    )["lista"]
    item = response["itens"][0]
    assert {key: item[key] for key in original["itens"][0]} == original["itens"][0]
    assert item["M3 individual"] == "3.125e-5"
    assert item[common.CBM_LISTA_PEDIDO_KEY] == "0.0025"
    assert response["total_m3"] == 0.0025
    assert response["total_quantidade"] == 80 and response["total_usd"] == 160
    assert response["m3_completo"] is True and response["m3_pendente_itens"] == 0
    assert ambiente["lists"]["cliente-a"][0] == original
    assert ambiente["counts"] == Counter() and ambiente["photo_calls"] == []


@pytest.mark.parametrize("fields,expected", [
    ({"link": "https://example.test/generic", "Link Aliexpress": "https://example.test/preferred"},
     "https://example.test/preferred"),
    ({"link aliexpress": "MLB123", "cg Link Aliexpress": "https://example.test/cg", "url aliexpress": "https://example.test/url"},
     "https://example.test/cg"),
    ({"LINK ALIEXPRESS": " ", "Link_Aliexpress": "https://example.test/later"}, "https://example.test/later"),
    ({"LINK ALIEXPRESS": "MLB123", "Link_Aliexpress": "https://example.test/duplicate", "Link": "https://example.test/generic"},
     "https://example.test/generic"),
    ({"foto": "https://example.test/photo.png", "monofasico_verificado_em": "2026-09-04", "link": "MLB123"}, ""),
    ({"link aliexpress": "https://user:pass@example.test/product", "url": "https://example.test/valid"},
     "https://example.test/valid"),
    ({"link": "javascript:alert(1)", "url": "https://example.test/with space"}, ""),
    ({"link": "https://[", "url": "HTTPS://example.test/product?value=0"}, "HTTPS://example.test/product?value=0"),
])
def test_link_cadastro_preserva_prioridade_e_validacao(fields, expected):
    assert fiscal._pick_cadastro_link(fields) == expected


def test_link_cadastro_normaliza_linha_uma_vez_por_busca(monkeypatch):
    row = {f"campo extra {index}": "valor" for index in range(100)}
    row.update({"Link": "MLB123", "cg Link": "https://example.test/product"})
    counts = Counter()
    original = fiscal._normalizar_coluna_cadastro_meta

    def normalize(value):
        counts[value] += 1
        return original(value)

    monkeypatch.setattr(fiscal, "_normalizar_coluna_cadastro_meta", normalize)
    assert fiscal._pick_cadastro_link(row) == "https://example.test/product"
    assert all(counts[f"campo extra {index}"] == 1 for index in range(100))
    assert sum(counts.values()) <= len(row) + 2 * len(fiscal._META_LINK_CANDIDATOS)
