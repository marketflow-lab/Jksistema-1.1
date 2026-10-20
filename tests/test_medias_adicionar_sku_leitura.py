"""One-SKU lookup and list mutation keep scoped semantics with bounded reads."""

import copy
import csv
from collections import Counter

import pytest
from fastapi import HTTPException

from backend.schemas.medias_compras import ListaPedidoAddSkuRequest
from backend.services import cadastro_compatibilidade as cadastro
from backend.services import cadastro_fotos as fotos
from backend.services import cadastro_lojas_produtos as produtos
from backend.services import cadastro_produtos as lookup
from backend.services import cadastro_listagem as listagem
from backend.services import medias_compras_common as common
from backend.services import medias_compras_fiscal as fiscal
from backend.services import medias_compras_leitura as leitura
from backend.services import medias_compras_listas as listas
from test_medias_compras_carregamento import ambiente, _csv


@pytest.fixture
def inclusao(ambiente, monkeypatch):
    state = ambiente
    tenant = state["root"] / "cliente-a"
    path = tenant / "cadastro_produtos_lojas.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    for store_id in ("store-a", "store-b"):
        for sku in ("375-1", "3751", "01", "001-JAN", "ABC-01", "OTHER-1", "OTHER-2", "DELETED"):
            rows.append({
                "store_id": store_id, "sku": sku, "sku_normalizado": produtos._normalizar_sku_chave(sku),
                "nome": "Product " + sku, "m3 individual": "0,25" if store_id == "store-a" else "0,90",
                "ncm": "8708.99.90", "foto": state["refs"]["cliente-a", store_id],
                "deleted_at_utc": "2026-01-01T00:00:00Z" if sku == "DELETED" else "",
            })
    _csv(path, rows)
    state["rows"] = rows
    state["catalog"].extend({
        "sku": sku, "ncm": "8708.99.90", "cest": "01.001.00", "oem": "OEM-" + sku,
        "color side": "Right", "link": "https://example.test/" + sku,
    } for sku in ("375-1", "3751", "01", "001-JAN", "LEGACY-1"))
    _csv(tenant / "cadastro_produtos.csv", [{"sku": "LEGACY-1", "nome": "Legacy item", "m3 individual": "0,4"}])
    for module in (lookup, listagem):
        monkeypatch.setattr(module, "get_tenant_path", produtos.get_tenant_path)
    monkeypatch.setattr(lookup, "_cadastro_mapa_custos_lojas", lambda _client: {})
    state["lists"]["cliente-a"][0]["itens"].extend(
        {"SKU": sku, "Quantidade": 3, "Valor unidade": 8, "Observacoes": "keep " + sku}
        for sku in ("01", "001-JAN", "3751")
    )
    return state


def _add(sku="375-1"):
    return listas._api_medias_compras_lista_pedido_adicionar_sku_sync(
        "lista", ListaPedidoAddSkuRequest(sku=sku, quantidade=20, valor_unitario=2), client_id="cliente-a",
    )


@pytest.mark.parametrize("sku", ["001", "1", "01", "001-JAN", "1-1", "abc-01", "375-1", "3751", "DELETED", "MISSING"])
def test_lookup_filtered_projection_is_identical_and_keeps_controlled_tombstones(inclusao, monkeypatch, sku):
    complete = cadastro.visao_compatibilidade_produtos_lojas("cliente-a")
    target = produtos._normalizar_sku_chave(sku)
    expected = next((row for row in complete["produtos"] if produtos._normalizar_sku_chave(row.get("sku")) == target), None)
    calls = Counter()
    original = produtos._enriquecer_produto
    def enrich(*args, **kwargs):
        calls["products"] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(produtos, "_enriquecer_produto", enrich)
    controlled, result = cadastro.obter_produto_controlado_para_compatibilidade("cliente-a", sku)
    assert controlled == (target in complete["skus_controlados"])
    assert result == expected
    assert calls["products"] <= 4  # At most the two stores and colliding public aliases.
    assert "DELETED" in complete["skus_controlados"]


def test_lookup_reuses_legacy_index_within_call_and_refreshes_next_call(inclusao, monkeypatch):
    calls = []
    original = produtos._indice_legado_de_tenant
    def index(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(produtos, "_indice_legado_de_tenant", index)
    old = lookup._obter_produto_cadastro_sync("375-1", "cliente-a")["produto"]
    assert calls == [1]
    for row in inclusao["rows"]:
        if row["sku"] == "375-1":
            row["nome"] = "Updated product"
    _csv(inclusao["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", inclusao["rows"])
    new = lookup._obter_produto_cadastro_sync("375-1", "cliente-a")["produto"]
    assert old["nome"] != new["nome"] == "Updated product"
    assert calls == [1, 1]
    assert leitura.leitura_atual() is None


def test_add_uses_one_capture_and_closes_read_context_before_commit(inclusao, monkeypatch):
    captures = []
    original_capture = cadastro._capturar_cadastro_para_leitura_sem_cache
    original_save = listas._salvar_listas_pedidos
    def capture(*args, **kwargs):
        captures.append(1)
        return original_capture(*args, **kwargs)
    def save(*args):
        assert leitura.leitura_atual() is None
        assert fotos._CADASTRO_FOTOS_LEITURA.get() is None
        return original_save(*args)
    monkeypatch.setattr(cadastro, "_capturar_cadastro_para_leitura_sem_cache", capture)
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", save)
    result = _add()
    assert result["acao"] == "adicionado"
    assert len(captures) == 1
    assert inclusao["counts"]["contexto_catalogo"] == 1
    assert result["lista"]["itens"][-1]["M3 individual"] == 0.25
    assert leitura.leitura_atual() is None


@pytest.mark.parametrize("scope,sku", [("store", "375-1"), ("global", "375-1"), ("global", "LEGACY-1")])
@pytest.mark.parametrize("historical", [False, True])
def test_prepared_context_matches_complete_original_fiscal_items(inclusao, monkeypatch, scope, sku, historical):
    current = inclusao["lists"]["cliente-a"][0]
    if scope == "global":
        current.pop("store_id")
        current["loja"] = "__todas"
    if historical:
        current["status"] = "Pedido Aprovado"
        current["itens"][0].update({"M3": 0.6, "M3 individual": 0.3, "Cubagem loja": "store-a"})
    captured = {}
    original = listas._recalcular_frete_internacional_itens_lista
    def recalculate(client_id, items, **kwargs):
        captured.update(client_id=client_id, items=copy.deepcopy(items),
                        options={k: v for k, v in kwargs.items() if k != "contexto_cadastro"})
        return original(client_id, items, **kwargs)
    monkeypatch.setattr(listas, "_recalcular_frete_internacional_itens_lista", recalculate)
    result = _add(sku)["lista"]["itens"]
    # This is the original fiscal call: full item set and no prepared context.
    expected = original(captured["client_id"], captured["items"], **captured["options"])
    assert result == expected
    assert len(result) == 5
    assert result[-1]["SKU"] == sku
    assert all(item["Observacoes"].startswith("keep ") for item in result[1:4])
    if scope == "store" and not historical:
        assert result[-1]["M3"] == 5
    if historical:
        assert result[0]["M3"] == 0.6


def test_conflicting_commit_recaptures_catalog_before_retry(inclusao, monkeypatch):
    captures = []
    commits = []
    original_capture = cadastro._capturar_cadastro_para_leitura_sem_cache
    original_save = listas._salvar_listas_pedidos
    def capture(*args, **kwargs):
        captures.append(leitura.leitura_atual())
        return original_capture(*args, **kwargs)
    def save(*args):
        assert leitura.leitura_atual() is None
        assert fotos._CADASTRO_FOTOS_LEITURA.get() is None
        commits.append(1)
        if len(commits) == 1:
            for row in inclusao["rows"]:
                if row["sku"] == "375-1" and row["store_id"] == "store-a":
                    row["m3 individual"] = "0,5"
            _csv(inclusao["root"] / "cliente-a" / "cadastro_produtos_lojas.csv", inclusao["rows"])
            raise common._ListaPedidoConflict()
        return original_save(*args)
    monkeypatch.setattr(cadastro, "_capturar_cadastro_para_leitura_sem_cache", capture)
    monkeypatch.setattr(listas, "_salvar_listas_pedidos", save)
    result = _add()
    assert len(captures) == 2
    assert all(snapshot is not None for snapshot in captures)
    assert captures[0] is not captures[1]
    assert commits == [1, 1]
    assert result["lista"]["itens"][-1]["M3 individual"] == 0.5
    assert len(result["lista"]["itens"]) == 5


@pytest.mark.parametrize("status", [401, 403, 503])
def test_lookup_authority_or_availability_errors_do_not_fall_back_to_full_catalog(inclusao, monkeypatch, status):
    before = copy.deepcopy(inclusao["lists"])
    def unavailable(*args, **kwargs):
        raise HTTPException(status, "synthetic error")
    def forbidden(**kwargs):
        pytest.fail("Full-catalog fallback must not mask authority/availability failures")
    monkeypatch.setattr(cadastro, "visao_produtos_cadastro_contexto_loja", unavailable)
    monkeypatch.setattr(listagem, "_listar_produtos_cadastro_sync", forbidden)
    with pytest.raises(HTTPException) as error:
        _add()
    assert error.value.status_code == status
    assert inclusao["lists"] == before
