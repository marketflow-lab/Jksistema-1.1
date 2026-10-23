from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest
from fastapi import HTTPException

from backend.services import bling_vendas
from backend.services import vendas_collection_checkpoint as checkpoint


DAY = "2026-07-18"


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self.headers = {}
        self.data = data

    def json(self):
        return {"data": self.data}


@pytest.fixture
def sales_api(monkeypatch):
    for name in ("_bling_map_canais_venda_basico", "_bling_map_lojas_virtuais", "_bling_map_unidades_por_canais"):
        monkeypatch.setattr(bling_vendas, name, lambda _token: ({}, 200))
    calls = {"pages": [], "details": [], "fail": True, "revision": 1}

    def request(url, **kwargs):
        if url.endswith("/pedidos/vendas"):
            assert url == "https://api.bling.com.br/Api/v3/pedidos/vendas"
            page = kwargs["params"]["pagina"]
            calls["pages"].append(page)
            assert kwargs["params"] == {"dataInicial": DAY, "dataFinal": DAY, "pagina": page, "limite": 100}
            return Response(200, [{"id": 10, "data": DAY, "versao": calls["revision"]}, {"id": 20, "data": DAY}] if page == 1 else [])
        document_id = int(url.rsplit("/", 1)[-1])
        assert url == f"https://api.bling.com.br/Api/v3/pedidos/vendas/{document_id}"
        calls["details"].append(document_id)
        if calls["fail"] and document_id == 20:
            return Response(503, {})
        return Response(200, {
            "data": DAY, "numero": document_id,
            "itens": [{"codigo": "SKU", "quantidade": 1, "valor": 10}, {"codigo": "SKU", "quantidade": 1, "valor": 10}],
        })

    monkeypatch.setattr(bling_vendas, "_bling_get_with_adaptive_limit", request)
    return calls


def collect_sales():
    return bling_vendas._bling_listar_vendas("SECRET-NEVER-PERSIST", DAY, DAY, "Loja")


def test_restart_reuses_complete_manifest_and_only_fetches_pending_documents(tmp_path, sales_api):
    scope = ("tenant", "store", "cycle", DAY)
    with checkpoint.collection_checkpoint(*scope, tenant_path=tmp_path):
        with pytest.raises(HTTPException):
            collect_sales()
    assert sales_api["details"] == [10, 20]
    assert checkpoint.collection_day_summary(*scope, tenant_path=tmp_path)["documentos_salvos"] == 1
    sales_api["fail"] = False
    with checkpoint.collection_checkpoint(*scope, tenant_path=tmp_path):
        rows, status = collect_sales()
    assert status == 200
    assert len(rows) == 4
    assert sales_api["pages"] == [1, 2]
    assert sales_api["details"] == [10, 20, 20]
    assert len({(row["origem_id"], row["item_id"]) for row in rows}) == 4
    summary = checkpoint.collection_day_summary(*scope, tenant_path=tmp_path)
    assert summary["documentos_salvos"] == 2
    assert summary["ultimo_id"] == "20"
    assert b"SECRET-NEVER-PERSIST" not in (tmp_path / "vendas_collection_checkpoint.db").read_bytes()


def test_new_period_reuses_day_and_explicit_resync_fetches_fresh_details(tmp_path, sales_api):
    sales_api["fail"] = False
    for cycle in ("one", "two"):
        with checkpoint.collection_checkpoint("tenant", "store", cycle, DAY, tenant_path=tmp_path):
            collect_sales()
    assert sales_api["details"] == [10, 20]
    sales_api["revision"] = 2
    with checkpoint.collection_checkpoint("tenant", "store", "refresh", DAY, tenant_path=tmp_path, force=True):
        collect_sales()
    assert sales_api["details"] == [10, 20, 10, 20]
    with checkpoint.collection_checkpoint("tenant", "store", "refresh", DAY, tenant_path=tmp_path, force=True):
        collect_sales()
    assert sales_api["details"] == [10, 20, 10, 20]
    assert sales_api["pages"] == [1, 2, 1, 2]


@pytest.mark.parametrize("corrupt", ["checksum", "shape"])
def test_invalid_checkpoint_is_refetched_not_counted_as_success(tmp_path, sales_api, corrupt):
    sales_api["fail"] = False
    with checkpoint.collection_checkpoint("tenant", "store", "cycle", DAY, tenant_path=tmp_path):
        collect_sales()
    with sqlite3.connect(tmp_path / "vendas_collection_checkpoint.db") as conn:
        if corrupt == "checksum":
            conn.execute("UPDATE collected_documents SET payload_hash='broken' WHERE document_id='10'")
        else:
            payload = json.dumps({"sales": [], "notes": [], "items": []})
            conn.execute("UPDATE collected_documents SET payload=?, payload_hash=? WHERE document_id='10'", (payload, checkpoint._hash(payload)))
    with checkpoint.collection_checkpoint("tenant", "store", "cycle", DAY, tenant_path=tmp_path):
        rows, _ = collect_sales()
    assert len(rows) == 4
    assert sales_api["details"] == [10, 20, 10]


def payload(document="1"):
    return {"sales": [{"data": DAY, "origem_tipo": "pedido", "origem_id": document, "item_id": "1", "quantidade": 1, "valor": 10}], "notes": [], "items": []}


def test_checkpoint_isolates_tenants_stores_cycles_and_threads(tmp_path):
    def worker(tenant, store, cycle):
        with checkpoint.collection_checkpoint(tenant, store, cycle, DAY, tenant_path=tmp_path):
            checkpoint.save_collected_document("pedido", "1", {"id": 1}, payload())
            return checkpoint.load_collected_document("pedido", "1", {"id": 1})
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda args: worker(*args), [("a", "s", "c"), ("b", "s", "c"), ("a", "t", "c")]))
    assert len(results) == 3 and all(result == payload() for result in results)
    with checkpoint.collection_checkpoint("a", "s", "other-cycle", DAY, tenant_path=tmp_path, force=True):
        assert checkpoint.load_collected_document("pedido", "1", {"id": 1}) is None
    checkpoint.finish_collection_day("a", "s", "c", DAY, tenant_path=tmp_path)
    assert checkpoint.collection_day_summary("a", "s", "c", DAY, tenant_path=tmp_path)["documentos_salvos"] == 0
    assert checkpoint.collection_day_summary("b", "s", "c", DAY, tenant_path=tmp_path)["documentos_salvos"] == 1
    assert checkpoint.collection_day_summary("a", "t", "c", DAY, tenant_path=tmp_path)["documentos_salvos"] == 1


def test_checkpoint_rejects_credentials_or_incomplete_rows(tmp_path):
    with checkpoint.collection_checkpoint("tenant", "store", "cycle", DAY, tenant_path=tmp_path):
        invalid = payload()
        invalid["sales"][0]["access_token"] = "secret"
        with pytest.raises(ValueError):
            checkpoint.save_collected_document("pedido", "1", {"id": 1}, invalid)
    assert checkpoint.collection_day_summary("tenant", "store", "cycle", DAY, tenant_path=tmp_path)["documentos_salvos"] == 0


@pytest.mark.parametrize("kind", ["nfe_entrada", "nfe_saida"])
def test_invoice_resume_retains_every_same_sku_line(tmp_path, monkeypatch, kind):
    calls = []
    failure = [True]
    def request(_url, **kwargs):
        params = kwargs["params"]
        assert _url == "https://api.bling.com.br/Api/v3/nfe"
        assert params == {
            "tipo": 0 if kind == "nfe_entrada" else 1,
            "dataEmissaoInicial": DAY + " 00:00:00",
            "dataEmissaoFinal": DAY + " 23:59:59",
            "pagina": params["pagina"], "limite": 100,
        }
        return Response(200, [{"id": 1, "dataEmissao": DAY}, {"id": 2, "dataEmissao": DAY}] if params["pagina"] == 1 else [])
    def details(_token, invoice_id, **_kwargs):
        calls.append(str(invoice_id))
        if str(invoice_id) == "2" and failure[0]:
            return None, 503
        return {"dataEmissao": DAY, "itens": [{"codigo": "SKU", "quantidade": 2, "valor": 5}, {"codigo": "SKU", "quantidade": 3, "valor": 5}]}, 200
    monkeypatch.setattr(bling_vendas, "_bling_get_with_adaptive_limit", request)
    monkeypatch.setattr(bling_vendas, "_bling_obter_detalhes_nf", details)
    def collect():
        if kind == "nfe_entrada":
            return bling_vendas._bling_listar_notas_entrada("token", DAY, DAY, {})[1]
        return bling_vendas._bling_listar_vendas_fallback_nf_saida("token", DAY, DAY, "Loja")[0]
    with checkpoint.collection_checkpoint("tenant", "store", "cycle", DAY, tenant_path=tmp_path):
        with pytest.raises(HTTPException):
            collect()
    failure[0] = False
    with checkpoint.collection_checkpoint("tenant", "store", "cycle", DAY, tenant_path=tmp_path):
        items = collect()
    assert calls == ["1", "2", "2"]
    assert len(items) == 4
    assert sum(item["quantidade"] for item in items) == 10
