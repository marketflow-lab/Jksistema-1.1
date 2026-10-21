import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException

from backend.services import cadastro_compatibilidade as compat
from backend.services import cadastro_lojas_listagem as listing
from backend.services import cadastro_lojas_produtos as products
from backend.services import cadastro_listagem as legacy_listing
from backend.services import cadastro_sync_ncm as ncm
from backend.services.medias_compras_leitura import leitura_medias
from backend.services.retired_store_names import (
    TOMBSTONES_FILE, parse_retired_store_names, read_retired_store_names,
)
from test_cadastro_lojas_listagem import _configure, _write_csv
from test_cadastro_sync_ncm_store_scope import _configure_files
from test_cadastro_legacy_store_compatibility import _configurar


def _event(names=None, **changes):
    item = {
        "key": "store:retired:", "type": "store", "store_id": "retired",
        "service": "", "version": 2, "deleted_at": "2026-10-21T10:00:00Z",
        "store_names": ["Loja Igual", "Nome Anterior"] if names is None else names,
    }
    item.update(changes)
    return item


def _save(tenant, events):
    (Path(tenant) / TOMBSTONES_FILE).write_text(json.dumps(events), encoding="utf-8")


def test_reserves_current_and_historical_labels_but_ignores_old_events_and_restoration(tmp_path):
    old = _event(["Historico sem reserva"], store_id="old")
    old.pop("store_names")
    restored = _event(["Restaurada"], store_id="restored")
    restored["restored_at"] = restored.pop("deleted_at")
    _save(tmp_path, [_event(), old, restored])
    result = read_retired_store_names(tmp_path)
    assert result.blocks("  LOJA IGUAL ")
    assert result.blocks("nome anterior")
    assert not result.blocks("Historico sem reserva")
    assert not result.blocks("Restaurada")


@pytest.mark.parametrize("names", [None, "Loja Igual", [], [42], [""], ["Loja\nIgual"], {}])
def test_malformed_present_reservation_blocks_only_name_resolution(names):
    event = _event()
    event["store_names"] = names
    result = parse_retired_store_names(json.dumps([event]).encode())
    assert result.invalid
    assert result.blocks("qualquer nome")


def test_reserved_labels_are_tenant_local_and_do_not_follow_hardlinks(tmp_path):
    left, right = tmp_path / "a", tmp_path / "b"
    left.mkdir()
    right.mkdir()
    _save(left, [_event()])
    assert read_retired_store_names(left).blocks("Loja Igual")
    assert not read_retired_store_names(right).blocks("Loja Igual")
    (right / TOMBSTONES_FILE).hardlink_to(left / TOMBSTONES_FILE)
    assert read_retired_store_names(right).invalid


def _catalog(monkeypatch, tmp_path):
    stores = [{"store_id": "keeper", "nome": "Loja Igual", "nomes_anteriores": ["Nome Anterior"]}]
    root = _configure(monkeypatch, tmp_path, {"a": stores, "b": stores})
    for client in ("a", "b"):
        _write_csv(root / client / "cadastro_produtos.csv", [
            {"sku": "NAME", "loja_sync": "Loja Igual", "nome": "Legado sem dono comprovado"},
            {"sku": "OLD", "loja_sync": "Nome Anterior", "nome": "Historico ambiguo"},
            {"sku": "EXACT", "store_id": "keeper", "loja_sync": "Nome Anterior", "nome": "Exato preservado"},
            {"sku": "RETIRED", "store_id": "retired", "loja_sync": "Loja Igual", "nome": "Exato removido"},
        ])
    _save(root / "a", [_event()])
    return root, stores


def test_single_bulk_and_compatibility_reads_preserve_exact_ids_and_tenant_isolation(monkeypatch, tmp_path):
    root, stores = _catalog(monkeypatch, tmp_path)
    singles = products._listar_produtos_loja_sync("a", "keeper")
    bulk = listing.listar_produtos_lojas_snapshot_sync("a", view="full")["produtos"]
    assert [row["sku"] for row in singles] == ["EXACT"]
    assert [row["sku"] for row in bulk] == ["EXACT"]
    assert {row["sku"] for row in products._listar_produtos_loja_sync("b", "keeper")} == {"NAME", "OLD", "EXACT"}
    assert not compat.resolver_loja_ativa_para_leitura("a", "Loja Igual", lojas=stores)["loja_resolvida"]
    assert not compat.resolver_loja_ativa_para_leitura("a", "Nome Anterior", lojas=stores)["loja_resolvida"]
    assert compat.resolver_loja_ativa_para_leitura("a", store_id="keeper", lojas=stores)["store_id"] == "keeper"
    assert compat.resolver_loja_ativa_para_leitura("b", "Loja Igual", lojas=stores)["store_id"] == "keeper"


def test_invalid_reservation_leaves_exact_rows_and_exact_identity_available(monkeypatch, tmp_path):
    root, stores = _catalog(monkeypatch, tmp_path)
    _save(root / "a", [_event(store_names="invalid")])
    assert [row["sku"] for row in products._listar_produtos_loja_sync("a", "keeper")] == ["EXACT"]
    assert compat.resolver_loja_ativa_para_leitura("a", store_id="keeper", lojas=stores)["loja_resolvida"]


def test_compatibility_snapshot_keeps_its_reservation_bytes_after_live_restoration(monkeypatch, tmp_path):
    root, _ = _catalog(monkeypatch, tmp_path)
    records, stores, sources, _photos = compat._capturar_cadastro_para_leitura("a")
    assert TOMBSTONES_FILE in sources
    restored = _event()
    restored["restored_at"] = restored.pop("deleted_at")
    _save(root / "a", [restored])
    captured = products._listar_produtos_loja_sync(
        "a", "keeper", _registros_persistidos=records,
        _lojas_capturadas=stores, _fontes_capturadas=sources,
    )
    assert [row["sku"] for row in captured] == ["EXACT"]
    assert {row["sku"] for row in products._listar_produtos_loja_sync("a", "keeper")} == {"NAME", "OLD", "EXACT"}


def test_legacy_index_cache_changes_when_reservation_changes(monkeypatch, tmp_path):
    root, stores = _catalog(monkeypatch, tmp_path)
    with leitura_medias():
        before = products._contexto_legado_de_tenant("a", stores[0], root / "a", stores, fotos={})
        _save(root / "a", [])
        after = products._contexto_legado_de_tenant("a", stores[0], root / "a", stores, fotos={})
    assert set(before["sombras"]) == {"EXACT"}
    assert set(after["sombras"]) == {"NAME", "OLD", "EXACT"}


def test_ncm_does_not_query_retired_alias_but_keeps_exact_id(monkeypatch, tmp_path):
    _cadastro, stock, stores = _configure_files(monkeypatch, tmp_path)
    stores[0]["nomes_anteriores"] = ["Nome Anterior"]
    frame = pd.read_csv(stock, dtype=str, keep_default_na=False)
    frame.loc[len(frame)] = {"sku": "LEGACY", "store_id": "", "loja_sync": "Nome Anterior", "id_bling": "legacy-1"}
    frame.fillna("").to_csv(stock, index=False)
    _save(stock.parent, [_event(["Loja A", "Nome Anterior"])])
    calls = []

    def lookup(token, product):
        calls.append((token, product))
        return {"ncm": "12345678", "cest": "1234567"}, 200

    monkeypatch.setattr(ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    ncm.SYNC_NCM_JOBS["retired-exact"] = {"client_id": "000002", "status": "running"}
    ncm._sync_ncm_cadastro_worker("000002", "retired-exact", "store-a")
    assert calls == [("token-a", "a-1")]
    result = pd.read_csv(stock, dtype=str, keep_default_na=False).set_index("sku")
    assert result.loc["LEGACY", "ncm_bling"] == ""
    assert ncm.SYNC_NCM_JOBS["retired-exact"]["status"] == "done"


@pytest.mark.parametrize("scope", ["store-a", None])
def test_ncm_rechecks_new_reservation_before_commit_without_touching_sources(monkeypatch, tmp_path, scope):
    catalog, stock, _stores = _configure_files(monkeypatch, tmp_path)
    frame = pd.read_csv(stock, dtype=str, keep_default_na=False)
    frame.loc[frame["store_id"] == "store-a", "store_id"] = ""
    frame.to_csv(stock, index=False)
    before = {path: path.read_bytes() for path in (catalog, stock)}

    def lookup(_token, _product):
        _save(stock.parent, [_event(["Loja A"])])
        return {"ncm": "12345678", "cest": "1234567"}, 200

    monkeypatch.setattr(ncm, "_bling_obter_ncm_cest_produto", lookup, raising=False)
    ncm.SYNC_NCM_JOBS["retired-race"] = {"client_id": "000002", "status": "running"}
    ncm._sync_ncm_cadastro_worker("000002", "retired-race", scope)
    assert ncm.SYNC_NCM_JOBS["retired-race"]["status"] == "error"
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("during_lookup", [False, True])
def test_inline_ncm_obeys_reservations_at_selection_and_commit(monkeypatch, tmp_path, during_lookup):
    stores = [{"store_id": "keeper", "nome": "Loja Igual", "integracoes": {
        "bling": {"access_token": "fixture-token", "id": "fixture-id", "secret": "fixture-secret"},
    }}]
    root, _ = _configurar(monkeypatch, tmp_path, {"cliente-a": stores})
    tenant = root / "cliente-a"
    catalog, stock = tenant / "cadastro_produtos.csv", tenant / "produtos_compilado.csv"
    _write_csv(catalog, [{"sku": "EXACT", "nome": "Produto"}])
    _write_csv(stock, [
        {"sku": "EXACT", "store_id": "keeper", "loja_sync": "Loja Igual", "id_bling": "exact", "ncm_bling": ""},
        {"sku": "LEGACY", "store_id": "", "loja_sync": "Loja Igual", "id_bling": "legacy", "ncm_bling": ""},
    ])
    before = {path: path.read_bytes() for path in (catalog, stock)}
    if not during_lookup:
        _save(tenant, [_event()])
    calls = []

    def lookup(_token, product):
        calls.append(product)
        if during_lookup:
            _save(tenant, [_event()])
        return "12345678", 200

    monkeypatch.setattr(legacy_listing, "carregar_lojas", lambda _: stores, raising=False)
    monkeypatch.setattr(legacy_listing, "_bling_obter_ncm_produto", lookup, raising=False)
    monkeypatch.setattr(legacy_listing, "_classificar_monofasico_cadastro", lambda _df: (0, {}))
    if during_lookup:
        with pytest.raises(HTTPException):
            asyncio.run(legacy_listing.listar_produtos_cadastro("cliente-a", sync_ncm=True))
        assert calls == ["exact", "legacy"]
        assert {path: path.read_bytes() for path in before} == before
    else:
        asyncio.run(legacy_listing.listar_produtos_cadastro("cliente-a", sync_ncm=True))
        assert calls == ["exact"]
        result = pd.read_csv(stock, dtype=str, keep_default_na=False).set_index("sku")
        assert result.loc["EXACT", "ncm_bling"] == "12345678"
        assert result.loc["LEGACY", "ncm_bling"] == ""
