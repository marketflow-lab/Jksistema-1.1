from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from backend.services import bling_vendas as bling
from backend.services import vendas_collection_checkpoint as checkpoint


DAY = "2026-07-18"
KINDS = ("pedido", "nfe_entrada", "nfe_saida")


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self.headers = {}
        self.data = data

    def json(self):
        return {"data": self.data}


@pytest.fixture
def collector_api(monkeypatch):
    state = {"listing": [1, 2], "pages": [], "details": [], "maps": [], "failed_detail": None,
             "failed_page": None, "list_status": 200, "detail_status": 503, "empty_items": False,
             "revision": 1, "bad_fields": False}
    for name in ("_bling_map_canais_venda_basico", "_bling_map_lojas_virtuais", "_bling_map_unidades_por_canais"):
        def map_call(_token, name=name):
            state["maps"].append(name)
            return {}, 200
        monkeypatch.setattr(bling, name, map_call)

    def listing_row(identity):
        return {"id": identity, "data": DAY, "dataEmissao": DAY, "versao": state["revision"] if identity == 1 else 1,
                "numero": identity + 100, "origem": {"tipo": "V", "numero": 500 + identity},
                "contato": {"nome": "Comprador"}, "naturezaOperacao": {"id": 1, "descricao": "Venda"},
                "loja": {"id": 33, "descricao": "Canal", "unidadeNegocio": {"id": 44, "nome": "Unidade"}},
                "finalidade": {"descricao": "Normal"}, "intermediador": {"nomeUsuario": "Canal", "cnpj": ""},
                "access_token": "SECRET-NOT-PERSISTED", "debug": {"response": "RAW-HTTP-NOT-PERSISTED"}}

    def detail_data(identity):
        items = [] if state["empty_items"] else [{"codigo": "SKU", "quantidade": 2, "valor": 5}, {"codigo": "SKU", "quantidade": 3, "valor": 5}]
        return {"id": identity, "numero": identity, "data": DAY, "dataEmissao": DAY, "itens": items}

    def invoice_detail(_token, identity, **_kwargs):
        identity = int(identity)
        state["details"].append(identity)
        if identity == state["failed_detail"]:
            return None, state["detail_status"]
        return detail_data(identity), 200

    def request(url, **kwargs):
        assert kwargs["headers"] == {"Authorization": "Bearer fake-token"}
        if url.endswith("/pedidos/vendas") or url.endswith("/nfe"):
            params = kwargs["params"]
            page = params["pagina"]
            state["pages"].append(page)
            if url.endswith("/nfe"):
                assert url == "https://api.bling.com.br/Api/v3/nfe"
                assert params == {"tipo": params["tipo"], "dataEmissaoInicial": DAY + " 00:00:00", "dataEmissaoFinal": DAY + " 23:59:59", "pagina": page, "limite": 100}
            else:
                assert url == "https://api.bling.com.br/Api/v3/pedidos/vendas"
                assert params == {"dataInicial": DAY, "dataFinal": DAY, "pagina": page, "limite": 100}
            if page == state["failed_page"]:
                return Response(503, {})
            if state["list_status"] != 200:
                return Response(state["list_status"], {})
            rows = [listing_row(identity) for identity in state["listing"]] if page == 1 else []
            if state["bad_fields"] and rows:
                rows[0]["numero"] = {"unexpected": "value"}
            return Response(200, rows)
        identity = int(url.rsplit("/", 1)[-1])
        assert url == f"https://api.bling.com.br/Api/v3/pedidos/vendas/{identity}"
        state["details"].append(identity)
        if identity == state["failed_detail"]:
            return Response(state["detail_status"], {})
        return Response(200, detail_data(identity))

    monkeypatch.setattr(bling, "_bling_get_with_adaptive_limit", request)
    monkeypatch.setattr(bling, "_bling_obter_detalhes_nf", invoice_detail)
    state["listing_row"] = listing_row
    return state


def collect(kind):
    if kind == "pedido":
        rows, status = bling._bling_listar_vendas("fake-token", DAY, DAY, "Loja")
        return rows, status
    if kind == "nfe_saida":
        return bling._bling_listar_vendas_fallback_nf_saida("fake-token", DAY, DAY, "Loja")
    _notes, rows, status = bling._bling_listar_notas_entrada("fake-token", DAY, DAY, {})
    return rows, status


def context(tmp_path, cycle="period-a"):
    return checkpoint.collection_checkpoint("tenant", "store", cycle, DAY, tenant_path=tmp_path)


@pytest.mark.parametrize("kind", KINDS)
def test_completed_enumeration_survives_restart_and_another_period_without_relisting(tmp_path, collector_api, kind):
    collector_api["failed_detail"] = 2
    with context(tmp_path):
        with pytest.raises(HTTPException):
            collect(kind)
        assert checkpoint.load_collection_manifest(kind) is not None
        assert checkpoint.collected_source_complete(kind) is False
    assert collector_api["pages"] == [1, 2]
    assert collector_api["details"] == [1, 2]
    collector_api["failed_detail"] = None
    collector_api["listing"] = [3, 2, 1]
    with context(tmp_path, "period-b"):
        rows, status = collect(kind)
        assert checkpoint.collected_source_complete(kind) is True
    assert status == 200 and len(rows) == 4
    assert sum(row["quantidade"] for row in rows) == 10
    assert collector_api["pages"] == [1, 2]
    assert collector_api["details"] == [1, 2, 2]
    collector_api["maps"].clear()
    with context(tmp_path, "period-c"):
        assert collect(kind) == (rows, 200)
    assert collector_api["pages"] == [1, 2]
    assert collector_api["details"] == [1, 2, 2]
    assert collector_api["maps"] == []
    data = (tmp_path / "vendas_collection_checkpoint.db").read_bytes()
    assert b"SECRET-NOT-PERSISTED" not in data
    assert b"RAW-HTTP-NOT-PERSISTED" not in data


@pytest.mark.parametrize("kind", KINDS)
def test_interrupted_enumeration_is_restarted_before_details_and_retains_new_order(tmp_path, collector_api, kind):
    collector_api["failed_page"] = 2
    with context(tmp_path):
        with pytest.raises(HTTPException):
            collect(kind)
        assert checkpoint.load_collection_manifest(kind) is None
    assert collector_api["details"] == []
    collector_api["failed_page"] = None
    collector_api["listing"] = [3, 1, 2]
    with context(tmp_path, "period-b"):
        rows, status = collect(kind)
    assert status == 200 and len(rows) == 6
    assert collector_api["pages"] == [1, 2, 1, 2]
    assert collector_api["details"] == [3, 1, 2]


@pytest.mark.parametrize("kind", KINDS)
def test_empty_enumeration_is_prepared_and_reused_with_no_other_calls(tmp_path, collector_api, kind):
    collector_api["listing"] = []
    with context(tmp_path):
        assert collect(kind) == ([], 200)
        assert checkpoint.collected_source_complete(kind) is True
    with context(tmp_path, "new-period"):
        assert collect(kind) == ([], 200)
    assert collector_api["pages"] == [1]
    assert collector_api["details"] == []
    assert collector_api["maps"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_required_document_without_items_never_becomes_complete(tmp_path, collector_api, kind):
    collector_api["empty_items"] = True
    with context(tmp_path):
        with pytest.raises(HTTPException):
            collect(kind)
        assert checkpoint.collected_source_complete(kind) is False
        assert checkpoint.load_prepared_source(kind) is None
    collector_api["empty_items"] = False
    with context(tmp_path):
        rows, status = collect(kind)
    assert status == 200 and len(rows) == 4
    assert collector_api["pages"] == [1, 2]
    assert collector_api["details"] == [1, 1, 2]


@pytest.mark.parametrize("where", ["listing", "detail"])
def test_optional_output_403_does_not_attest_an_empty_source(tmp_path, collector_api, where):
    if where == "listing":
        collector_api["list_status"] = 403
    else:
        collector_api["failed_detail"] = 2
        collector_api["detail_status"] = 403
    with context(tmp_path):
        assert collect("nfe_saida") == ([], 403)
        assert checkpoint.collected_source_complete("nfe_saida") is False
        assert checkpoint.load_prepared_source("nfe_saida") is None
    collector_api["list_status"] = 200
    collector_api["failed_detail"] = None
    with context(tmp_path):
        rows, status = collect("nfe_saida")
    assert status == 200 and len(rows) == 4
    assert collector_api["details"] == ([1, 2] if where == "listing" else [1, 2, 2])


@pytest.mark.parametrize("kind", ("nfe_entrada", "nfe_saida"))
def test_malformed_normalized_fields_do_not_create_a_manifest(tmp_path, collector_api, kind):
    collector_api["bad_fields"] = True
    with context(tmp_path):
        with pytest.raises(HTTPException):
            collect(kind)
        assert checkpoint.load_collection_manifest(kind) is None
    assert collector_api["details"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_outside_checkpoint_keeps_original_source_and_collection_behavior(collector_api, kind):
    rows, status = collect(kind)
    assert status == 200 and len(rows) == 4
    assert collector_api["pages"] == [1, 2]
    assert collector_api["details"] == [1, 2]
    collect(kind)
    assert collector_api["pages"] == [1, 2, 1, 2]
    assert collector_api["details"] == [1, 2, 1, 2]


@pytest.mark.parametrize("kind", KINDS)
def test_open_day_refresh_uses_original_listing_hash_to_detail_only_changed_or_new_documents(tmp_path, collector_api, monkeypatch, kind):
    now = datetime(2026, 7, 18, 15, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(checkpoint, "_now", lambda: now)
    with checkpoint.collection_checkpoint("tenant", "store", "job-a", DAY, tenant_path=tmp_path, refresh_discovery=True):
        collect(kind)
    collector_api["revision"] = 2
    collector_api["listing"] = [2, 3, 1]
    with checkpoint.collection_checkpoint("tenant", "store", "job-b", DAY, tenant_path=tmp_path, refresh_discovery=True):
        rows, status = collect(kind)
    assert status == 200 and len(rows) == 6
    assert collector_api["pages"] == [1, 2, 1, 2]
    assert collector_api["details"] == [1, 2, 3, 1]


@pytest.mark.parametrize("kind", KINDS)
def test_corrupt_manifest_restarts_discovery_and_preserves_confirmed_details_even_after_discovery_failure(tmp_path, collector_api, kind):
    with context(tmp_path):
        collect(kind)
    with sqlite3.connect(tmp_path / "vendas_collection_checkpoint.db") as conn:
        conn.execute("UPDATE collection_manifests SET entries_hash='corrupt'")
    collector_api["failed_page"] = 2
    with context(tmp_path, "job-b"):
        with pytest.raises(HTTPException):
            collect(kind)
        assert checkpoint.load_collection_manifest(kind) is None
    collector_api["failed_page"] = None
    collector_api["listing"] = [2, 3, 1]
    with context(tmp_path, "job-c"):
        rows, status = collect(kind)
    assert status == 200 and len(rows) == 6
    assert collector_api["pages"] == [1, 2, 1, 2, 1, 2]
    assert collector_api["details"] == [1, 2, 3]
