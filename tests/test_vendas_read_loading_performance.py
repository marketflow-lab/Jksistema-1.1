"""Isolation and concurrent-read regressions for the Vendas loading path."""

from concurrent.futures import ThreadPoolExecutor
import copy
import csv
from decimal import Decimal
import json
import threading

import pytest

from backend.modules.vendas import performance
from backend.modules.vendas.compositions import load_composition_catalog


@pytest.fixture(autouse=True)
def clean_read_cache():
    performance.invalidate_vendas_cache()
    yield
    performance.invalidate_vendas_cache()


@pytest.mark.parametrize("failure", [False, True])
def test_identical_concurrent_reads_share_work_and_release_after_failure(monkeypatch, failure):
    entered = threading.Event()
    follower = threading.Event()
    release = threading.Event()
    original_result = performance.Future.result

    def waiting_result(self, *args, **kwargs):
        follower.set()
        return original_result(self, *args, **kwargs)

    monkeypatch.setattr(performance.Future, "result", waiting_result)
    calls = []

    @performance.cache_vendas_response("shared", lambda _args: [])
    def endpoint(client_id, filtro):
        calls.append((client_id, filtro))
        entered.set()
        assert release.wait(3)
        if failure and len(calls) == 1:
            raise ValueError("source unavailable")
        return {"rows": [{"itens": 2, "valor": 19.5}]}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(endpoint, "tenant-a", "Loja A")
        assert entered.wait(3)
        second = pool.submit(endpoint, "tenant-a", "Loja A")
        try:
            assert follower.wait(3)
        finally:
            release.set()
        if failure:
            with pytest.raises(ValueError, match="source unavailable"):
                first.result(timeout=3)
            with pytest.raises(ValueError, match="source unavailable"):
                second.result(timeout=3)
            assert len(calls) == 1
            assert endpoint("tenant-a", "Loja A")["rows"][0]["itens"] == 2
            assert len(calls) == 2
        else:
            left, right = first.result(timeout=3), second.result(timeout=3)
            assert left == right and len(calls) == 1
            left["rows"][0]["itens"] = 99
            assert right["rows"][0]["itens"] == 2
            assert endpoint("tenant-a", "Loja A")["rows"][0]["itens"] == 2
    assert not performance._RESPONSE_FLIGHTS


@pytest.mark.parametrize("invalidate", ["tenant", "all", "file"])
def test_change_during_calculation_does_not_repopulate_old_cache(tmp_path, invalidate):
    source = tmp_path / "source.csv"
    source.write_text("before", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    calls = []

    @performance.cache_vendas_response("changing", lambda _args: [source])
    def endpoint(client_id):
        version = source.read_text(encoding="utf-8")
        calls.append(version)
        call = len(calls)
        if call == 1:
            entered.set()
            assert release.wait(3)
        return {"version": version, "call": call}

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(endpoint, "tenant-a")
        assert entered.wait(3)
        if invalidate == "file":
            source.write_text("after-change", encoding="utf-8")
        else:
            performance.invalidate_vendas_cache("tenant-a" if invalidate == "tenant" else None)
        try:
            current = endpoint("tenant-a")
            assert current["version"] == ("after-change" if invalidate == "file" else "before")
            assert current["call"] == 2
        finally:
            release.set()
        assert first.result(timeout=3) == {"version": "before", "call": 1}
    assert endpoint("tenant-a") == current
    assert calls == ["before", "after-change" if invalidate == "file" else "before"]


def test_filters_tenants_and_namespaces_never_share_responses():
    calls = []

    @performance.cache_vendas_response("scope", lambda _args: [])
    def endpoint(client_id, loja):
        calls.append((client_id, loja))
        return {"client": client_id, "loja": loja, "call": len(calls)}

    for tenant, store in [("tenant-a", "A"), ("tenant-b", "A"), ("tenant-a", "B")]:
        assert endpoint(tenant, store) == endpoint(tenant, store)
    assert len(calls) == 3


def test_expired_responses_are_released_when_new_results_arrive(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(performance.time, "monotonic", lambda: clock[0])

    @performance.cache_vendas_response("expiry", lambda _args: [], ttl_seconds=1)
    def endpoint(client_id, filtro):
        return {"rows": [{"filtro": filtro}]}

    endpoint("tenant-a", "old")
    clock[0] = 2.0
    endpoint("tenant-a", "new")
    assert performance.vendas_cache_stats()["response_entries"] == 1
    assert endpoint("tenant-a", "new") == {"rows": [{"filtro": "new"}]}


def _configure_catalog(monkeypatch, tmp_path):
    from backend.services import cadastro_fotos, cadastro_lojas_produtos as catalog, integracoes

    stores = [{"store_id": "store-a", "nome": "Loja A"}]
    tenant_path = lambda client_id: str(tmp_path / client_id)
    monkeypatch.setattr(catalog, "get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "_get_tenant_path", tenant_path)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "ler_lojas", lambda _client: copy.deepcopy(stores))
    monkeypatch.setattr(cadastro_fotos, "get_tenant_path", tenant_path)
    monkeypatch.setattr(cadastro_fotos, "_cadastro_carregar_lojas_foto", lambda _client: stores)
    tenant = tmp_path / "tenant-a"
    tenant.mkdir()
    records = [
        {"store_id": "store-a", "sku": "BASE", "id_bling": "1", "produto_bling": "Base"},
        {"store_id": "store-a", "sku": "KIT", "id_bling": "2", "produto_bling": "Kit",
         "formato_bling": "E", "componentes_bling_json": json.dumps([
             {"produto": {"id": 1}, "quantidade": 2},
         ])},
    ]
    path = tenant / "cadastro_produtos_lojas.csv"

    def write():
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted({key for row in records for key in row}))
            writer.writeheader()
            writer.writerows(records)

    write()
    return catalog, path, records, write


def test_composition_reads_skip_only_photo_work_and_refresh_source_revision(monkeypatch, tmp_path):
    catalog, path, records, write = _configure_catalog(monkeypatch, tmp_path)
    reads = []
    project = catalog._listar_produtos_loja_sync

    def count(*args, **kwargs):
        reads.append(kwargs.get("_incluir_fotos", True))
        return project(*args, **kwargs)

    monkeypatch.setattr(catalog, "_listar_produtos_loja_sync", count)
    before = path.read_bytes()
    first = load_composition_catalog("tenant-a")
    first._warn("request_only_warning", "store-a", "BASE")
    second = load_composition_catalog("tenant-a")
    assert reads == [False]
    assert second.resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert all(w["code"] != "request_only_warning" for w in second.warnings)
    assert path.read_bytes() == before
    records[1]["componentes_bling_json"] = json.dumps([
        {"produto": {"id": 1}, "quantidade": 125},
    ])
    write()
    third = load_composition_catalog("tenant-a")
    assert third.resolve("KIT", "store-a")[0].fator == Decimal(125)
    assert reads == [False, False]


def test_full_cadastro_projection_keeps_photo_validation_default(monkeypatch):
    from backend.services import cadastro_lojas_produtos as catalog

    validations = []
    monkeypatch.setattr(catalog, "_cadastro_resolver_foto_local", lambda *_: "")
    monkeypatch.setattr(catalog, "_cadastro_foto_referencia_pertence_loja", lambda *_: True)
    monkeypatch.setattr(catalog, "_cadastro_foto_referencias_invalidas_loja", lambda *_: validations.append(True) or [])
    record = {"sku": "KIT", "produto_bling": "Kit", "componentes_bling_json": "[]"}
    store = {"store_id": "store-a", "nome": "Loja A"}
    full = catalog._enriquecer_produto(record, store, {})
    minimal = catalog._enriquecer_produto(record, store, {}, incluir_fotos=False)
    assert full == minimal
    assert validations == [True]


def _write_retired_names(path, *, restored=False):
    event = {
        "key": "store:retired:", "type": "store", "store_id": "retired",
        "service": "", "version": 3 if restored else 2,
        "store_names": ["Loja A", "Nome Anterior"],
        "restored_at" if restored else "deleted_at": "2026-10-21T10:00:00Z",
    }
    path.write_text(json.dumps([event], indent=4), encoding="utf-8")


def _sale(*, store_id=""):
    return {"sku": "KIT", "store_id": store_id, "loja_conta": "Loja A",
            "id_unico": "fixture-sale", "quantidade": 3, "valor": 10}


def test_indented_tombstone_metadata_does_not_invalidate_canonical_composition_proofs(monkeypatch, tmp_path):
    _catalog, canonical, _records, _write = _configure_catalog(monkeypatch, tmp_path)
    tombstones = canonical.parent / "lojas_sync_tombstones.json"
    _write_retired_names(tombstones)
    original = {path: path.read_bytes() for path in (canonical, tombstones)}

    result = load_composition_catalog("tenant-a")

    assert result.resolve("KIT", "store-a")[0].sku == "BASE"
    assert result.resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert result.warnings == []
    assert {path: path.read_bytes() for path in original} == original


def test_composition_cache_tracks_delete_restore_and_preserves_exact_sale_store(monkeypatch, tmp_path):
    catalog, canonical, _records, _write = _configure_catalog(monkeypatch, tmp_path)
    tombstones = canonical.parent / "lojas_sync_tombstones.json"
    canonical_before = canonical.read_bytes()
    calls = []
    project = catalog._listar_produtos_loja_sync

    def count(*args, **kwargs):
        calls.append(True)
        return project(*args, **kwargs)

    monkeypatch.setattr(catalog, "_listar_produtos_loja_sync", count)
    before = load_composition_catalog("tenant-a")
    assert len(before.project_rows([_sale()])) == 2
    _write_retired_names(tombstones)
    deleted = load_composition_catalog("tenant-a")
    unresolved = deleted.project_rows([_sale()])
    assert len(unresolved) == 1 and unresolved[0]["quantidade"] == 3
    exact = deleted.project_rows([_sale(store_id="store-a")])
    assert exact[1]["sku"] == "BASE" and exact[1]["quantidade"] == 6
    assert load_composition_catalog("tenant-a").resolve("KIT", "Loja A")[0].sku == "KIT"
    assert len(calls) == 2
    _write_retired_names(tombstones, restored=True)
    restored = load_composition_catalog("tenant-a")
    assert len(restored.project_rows([_sale()])) == 2
    assert len(calls) == 3
    assert canonical.read_bytes() == canonical_before


def test_composition_reservation_uses_captured_bytes_and_isolates_tenants(monkeypatch, tmp_path):
    from backend.modules.vendas import compositions

    _catalog, canonical, _records, _write = _configure_catalog(monkeypatch, tmp_path)
    other_tenant = tmp_path / "tenant-b"
    other_tenant.mkdir()
    (other_tenant / canonical.name).write_bytes(canonical.read_bytes())
    tombstones = canonical.parent / "lojas_sync_tombstones.json"
    _write_retired_names(tombstones)
    prove = compositions._store_field_proofs

    def restore_after_capture(*args, **kwargs):
        result = prove(*args, **kwargs)
        _write_retired_names(tombstones, restored=True)
        return result

    monkeypatch.setattr(compositions, "_store_field_proofs", restore_after_capture)
    captured = load_composition_catalog("tenant-a")
    assert len(captured.project_rows([_sale()])) == 1
    assert len(load_composition_catalog("tenant-b").project_rows([_sale()])) == 2
    assert len(load_composition_catalog("tenant-a").project_rows([_sale()])) == 2


def test_temporary_catalog_failure_is_not_retained_after_recovery(monkeypatch, tmp_path):
    from backend.services import cadastro_compatibilidade

    _configure_catalog(monkeypatch, tmp_path)
    capture = cadastro_compatibilidade._capturar_cadastro_para_leitura
    calls = []

    def transient(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("temporarily unavailable")
        return capture(*args, **kwargs)

    monkeypatch.setattr(cadastro_compatibilidade, "_capturar_cadastro_para_leitura", transient)
    assert any(w["code"] == "catalog_unavailable" for w in load_composition_catalog("tenant-a").warnings)
    assert load_composition_catalog("tenant-a").resolve("KIT", "store-a")[0].fator == Decimal(2)
    assert len(calls) == 2
