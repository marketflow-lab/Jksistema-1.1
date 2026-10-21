"""Real composition collection -> Cadastro CSV -> Vendas projection, no network."""

from __future__ import annotations

from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.modules.vendas import composition_sync as sync, notes, repository
from backend.modules.vendas.compositions import load_composition_catalog
from backend.services import cadastro_catalogo_bling as provider, integracoes
from backend.services import cadastro_lojas_produtos as store
from test_cadastro_composition_refresh import catalog  # noqa: F401
from test_vendas_composition_summary import sales  # noqa: F401


SKUS = {101: "479", 102: "479-K", 103: "479-K3", 105: "479-K5"}
FACTORS = {102: 2, 103: 3, 105: 5}


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _record(env, sku, account="store-a"):
    return next((row for row in _rows(env.canonical) if row["store_id"] == account and row["sku"] == sku), None)


def _nonempty(row):
    return {key: value for key, value in row.items() if value}


def _summary():
    return repository.resumo_vendas("tenant-a", data_inicio="2026-10-01", data_fim="2026-10-31", loja="Loja A")


@pytest.fixture
def environment(catalog, sales, monkeypatch):
    db_path, _ = sales
    # Use the real Cadastro loader; the reused SQL fixture only supplies paths
    # and a synthetic existing sales/returns schema.
    monkeypatch.setattr(repository, "load_composition_catalog", load_composition_catalog)
    monkeypatch.setattr(notes, "load_composition_catalog", load_composition_catalog)
    stores = {}
    for tenant in ("tenant-a", "tenant-b"):
        stores[tenant] = [{
            "store_id": account, "nome": name, "integracoes": {"bling": {
                "id": "app-" + account, "secret": "fixture-secret", "access_token": "token-" + account,
                "refresh_token": "fixture-refresh", "oauth_connection_id": "connection-" + account,
                "connected": True,
            }},
        } for account, name in (("store-a", "Loja A"), ("store-b", "Loja B"))]
    monkeypatch.setattr(integracoes, "ler_lojas", lambda tenant: deepcopy(stores.get(tenant, [])))
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda tenant: deepcopy(stores.get(tenant, [])))
    for tenant, account, cost in (("tenant-a", "store-a", "17.25"), ("tenant-a", "store-b", "88"), ("tenant-b", "store-a", "99")):
        store.salvar_produto_loja(tenant, account, {"sku": "479", "nome": "Manual base", "custo": cost, "imposto": "5"})
    tenant = catalog / "tenant-a"
    compiled = tenant / "produtos_compilado.csv"
    with compiled.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "loja_sync", "id_bling", "nome_bling"])
        writer.writeheader()
        writer.writerows({"sku": sku, "loja_sync": "Loja A", "id_bling": ident, "nome_bling": sku} for ident, sku in SKUS.items())
    with sqlite3.connect(db_path) as db:
        # The latest screenshot has one extra direct sale: 21 units / 11 orders.
        db.execute("""INSERT INTO vendas
            (id_unico, data, loja_conta, store_id, sku, produto, quantidade, valor,
             devolucao, canal, numero, situacao, unidade_negocio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                "479:10", "2026-10-01", "Loja A", "store-a", "479", "Base", 1, 908.80,
                0, "Loja virtual", "479:10", "Faturado", "Canal A"))
        db.execute("INSERT INTO notas_entrada_itens VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "return-kit2", "2026-10-02", "Loja A", "store-a", "479-K", "Kit", 1, 150,
            1, "RETURN-1", "RETURN-1", 150, "", "Canal A", "", "Devolucao", ""))
    env = SimpleNamespace(
        root=catalog, canonical=tenant / "cadastro_produtos_lojas.csv", compiled=compiled,
        stores=stores, now=datetime.now(timezone.utc), calls=[], details={}, failures={},
        listing=[{"id": ident, "codigo": sku, "nome": sku, "formato": "S" if ident == 101 else "E"} for ident, sku in SKUS.items()],
        hook=None, list_failure_page=None, list_timeout_page=None,
        db_path=db_path, db_sha=hashlib.sha256(db_path.read_bytes()).hexdigest(),
    )
    for ident, factor in FACTORS.items():
        env.details[ident] = {"id": ident, "codigo": SKUS[ident], "formato": "E",
                              "estrutura": {"tipoEstoque": "F", "componentes": [{"produto": {"id": 101}, "quantidade": factor}]}}
    env.protected = {path: path.read_bytes() for path in (
        compiled, tenant / "cadastro_custos_lojas.csv", catalog / "tenant-b" / "cadastro_produtos_lojas.csv",
        catalog / "tenant-b" / "cadastro_custos_lojas.csv",
    )}
    env.other_store = _nonempty(_record(env, "479", "store-b"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return env.now if tz else env.now.replace(tzinfo=None)

    monkeypatch.setattr(sync, "datetime", Clock)
    monkeypatch.setattr(provider, "_preparar_credencial_coleta", lambda _tenant, _store, _name, cfg: dict(cfg))

    def execute(_tenant, _name, cfg, operation, **_kwargs):
        result, status = operation(cfg["access_token"])
        return result, status, dict(cfg)

    monkeypatch.setattr(provider, "_bling_executar_com_refresh", execute)
    monkeypatch.setattr(provider, "_bling_get_with_adaptive_limit", lambda *_a, **_k: pytest.fail("real network prohibited"))

    def get_json(token, path, *, params, deadline, **kwargs):
        assert token == "token-store-a"
        budget = provider.BLING_CATALOG_TIMEOUT_SECONDS if path == "/produtos" else 90
        assert 0 < deadline - provider.time.monotonic() <= budget
        kwargs["cancel_callback"]()
        env.calls.append((path, deepcopy(params)))
        if env.hook:
            env.hook(path)
        if path == "/produtos":
            assert params["tipo"] == "T" and params["criterio"] == 5
            page, limit = params["pagina"], params["limite"]
            if page == env.list_timeout_page:
                raise provider._CatalogoBlingPrazoExcedido()
            if page == env.list_failure_page:
                return None, 503
            return deepcopy(env.listing[(page - 1) * limit:page * limit]), 200
        assert path.startswith("/produtos/"), "composition collection must not fetch balances or categories"
        ident = int(path.rsplit("/", 1)[-1])
        assert not any(
            row["id"] == ident and row.get("formato") in {"S", "V"} for row in env.listing
        ), "simple component IDs are confirmed by the full listing"
        failure = env.failures.get(ident)
        if failure == "timeout":
            raise provider._CatalogoBlingPrazoExcedido()
        if failure:
            return None, failure
        return deepcopy(env.details[ident]), 200

    monkeypatch.setattr(provider, "_get_json", get_json)
    yield env
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == env.db_sha
    for path, content in env.protected.items():
        assert path.read_bytes() == content
    assert _nonempty(_record(env, "479", "store-b")) == env.other_store


def _refresh(env, cancel_callback=lambda: None):
    env.calls.clear()
    return sync.sincronizar_composicoes_vendas("tenant-a", "store-a", cancel_callback=cancel_callback)


def _detail_ids(env):
    return [int(path.rsplit("/", 1)[-1]) for path, _ in env.calls if path != "/produtos"]


def test_legacy_catalog_is_repaired_and_sales_returns_reconcile(environment):
    env = environment
    before = _summary()
    assert before["totals"]["itens"] == 35
    assert not _record(env, "479").get("id_bling")
    assert _record(env, "479-K") is None
    result = _refresh(env)
    assert result == {"updated": 4, "pending": False}
    assert _detail_ids(env) == [102, 103, 105]
    assert all(_record(env, sku)["id_bling"] == str(ident) for ident, sku in SKUS.items())
    resolved = load_composition_catalog("tenant-a")
    assert not resolved.warnings
    for ident, factor in FACTORS.items():
        assert [(item.sku, item.fator) for item in resolved.resolve(SKUS[ident], "store-a")] == [("479", Decimal(factor))]
    summary = _summary()
    base = next(row for row in summary["rows"] if row["sku"] == "479")
    assert (base["itensDiretos"], base["itensComposicao"], base["itens"]) == (21, 39, 60)
    assert summary["totals"]["itens"] == 60
    assert summary["totals"]["pedidos"] == before["totals"]["pedidos"] == 25
    assert summary["totals"]["valor"] == before["totals"]["valor"] == 57929.74
    assert (summary["totals"]["itensDevolucao"], summary["totals"]["itensLiquidos"]) == (2, 58)
    assert (summary["totals"]["devolucoes"], summary["totals"]["valorDevolucao"]) == (1, 150)
    assert (base["pedidos"], base["valor"], base["devolucoes"], base["valorDevolucao"]) == (11, 19281.57, 0, 0)
    detail = repository.listar_vendas("tenant-a", sku="479", loja="Loja A")
    assert sum(row["quantidade"] for row in detail) == 60
    assert sum(row.get("pedidos_contribuicao", 1) for row in detail) == 11
    returns = notes.listar_notas_entrada_por_sku("479", "tenant-a", loja="Loja A")
    assert sum(row["quantidade"] for row in returns) == 2
    assert sum(row.get("devolucoes_contribuicao", 1) for row in returns) == 0
    assert sum(row["valor_total"] for row in returns) == 0
    assert all(not any(key.startswith("__catalog_") for key in row) for row in _rows(env.canonical))


@pytest.mark.parametrize("failure", [503, "timeout"])
def test_completed_listing_commits_good_details_and_retries_only_missing(environment, failure):
    env = environment
    env.failures[105] = failure
    result = _refresh(env)
    assert result == {"updated": 3, "pending": True}
    assert _record(env, "479-K5") is None
    assert load_composition_catalog("tenant-a").resolve("479-K", "store-a")[0].fator == 2
    first_kit = dict(_record(env, "479-K"))
    env.failures.clear()
    resumed = _refresh(env)
    assert resumed["updated"] > 0 and resumed["pending"] is False
    assert _detail_ids(env) == [105]
    assert _record(env, "479-K") == first_kit
    assert _summary()["totals"]["itens"] == 60


def test_fresh_compositions_skip_details_but_refresh_after_24_hours(environment):
    env = environment
    _refresh(env)
    before = env.canonical.read_bytes()
    env.now += timedelta(hours=23)
    assert _refresh(env) == {"updated": 0, "pending": False}
    assert _detail_ids(env) == []
    assert env.canonical.read_bytes() == before
    env.now += timedelta(hours=2)
    env.details[102]["estrutura"]["componentes"][0]["quantidade"] = 4
    assert _refresh(env) == {"updated": 4, "pending": False}
    assert _detail_ids(env) == [102, 103, 105]
    assert _summary()["totals"]["itens"] == 78
    assert _summary()["totals"]["pedidos"] == 25
    assert _summary()["totals"]["valor"] == 57929.74


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_incomplete_listing_never_commits_partial_identity_evidence(environment, monkeypatch, failure):
    env = environment
    _refresh(env)
    before = env.canonical.read_bytes()
    env.now += timedelta(hours=25)
    monkeypatch.setattr(provider, "BLING_PAGE_SIZE", 2)
    if failure == "http":
        env.list_failure_page = 2
    else:
        env.list_timeout_page = 2
    assert _refresh(env) == {"updated": 0, "pending": True}
    assert env.canonical.read_bytes() == before


@pytest.mark.parametrize("invalid", ["id_mismatch", "sku_mismatch", "cycle", "duplicate_id", "duplicate_sku"])
def test_invalid_refresh_preserves_previous_verified_composition(environment, invalid):
    env = environment
    _refresh(env)
    before = dict(_record(env, "479-K"))
    env.now += timedelta(hours=25)
    if invalid == "id_mismatch":
        env.details[102]["id"] = 999
    elif invalid == "sku_mismatch":
        env.details[102]["codigo"] = "OTHER"
    elif invalid == "cycle":
        env.details[102]["estrutura"]["componentes"] = [{"produto": {"id": 102}, "quantidade": 1}]
    elif invalid == "duplicate_id":
        env.listing.append({"id": 102, "codigo": "OTHER", "formato": "E"})
    else:
        env.listing.append({"id": 999, "codigo": "479-K", "formato": "E"})
        env.details[999] = {**deepcopy(env.details[102]), "id": 999}
    result = _refresh(env)
    assert result["pending"] is True
    assert _record(env, "479-K") == before
    assert load_composition_catalog("tenant-a").resolve("479-K", "store-a")[0].fator == 2
    assert _summary()["totals"]["itens"] == 60


@pytest.mark.parametrize("change", ["store", "name", "connection"])
def test_store_or_connection_change_during_collection_aborts_commit(environment, change):
    env = environment
    before = env.canonical.read_bytes()

    def mutate(path):
        if path != "/produtos/102":
            return
        env.hook = None
        account = env.stores["tenant-a"][0]
        if change == "store":
            account["store_id"] = "store-replaced"
        elif change == "name":
            account["nome"] = "Renamed"
        else:
            account["integracoes"]["bling"]["oauth_connection_id"] = "other-connection"

    env.hook = mutate
    with pytest.raises(HTTPException) as error:
        _refresh(env)
    assert error.value.status_code in {404, 409}
    assert env.canonical.read_bytes() == before


def test_precommit_rechecks_connection_after_collection(environment):
    env = environment
    before = env.canonical.read_bytes()
    finished = False

    def mark_last_detail(path):
        nonlocal finished
        finished = path == "/produtos/105"

    def cancel_callback():
        if finished:
            env.stores["tenant-a"][0]["integracoes"]["bling"]["oauth_connection_id"] = "reconnected"

    env.hook = mark_last_detail
    with pytest.raises((HTTPException, sync.CompositionSyncConflict)) as error:
        _refresh(env, cancel_callback)
    assert error.value.status_code == 409
    assert env.canonical.read_bytes() == before


def test_cancel_during_details_does_not_write_completed_items(environment):
    env = environment
    before = env.canonical.read_bytes()
    cancelled = False

    def cancel_after_first_detail(path):
        nonlocal cancelled
        if path == "/produtos/102":
            cancelled = True

    env.hook = cancel_after_first_detail
    assert _refresh(env, lambda: cancelled) == {"updated": 0, "pending": True}
    assert env.canonical.read_bytes() == before


def test_concurrent_cadastro_edit_rejects_whole_batch(environment):
    env = environment
    after_edit = None

    def edit(path):
        nonlocal after_edit
        if path != "/produtos/102":
            return
        env.hook = None
        current = _record(env, "479")
        store.salvar_produto_loja("tenant-a", "store-a", {
            "sku": "479", "nome": "Concurrent manual edit", "row_version": int(current["row_version"]),
        })
        after_edit = env.canonical.read_bytes()

    env.hook = edit
    with pytest.raises(HTTPException) as error:
        _refresh(env)
    assert error.value.status_code == 409
    assert env.canonical.read_bytes() == after_edit
    assert _record(env, "479-K") is None
    assert not _record(env, "479").get("id_bling")



def test_fresh_kit_changed_to_simple_clears_previous_composition(environment):
    env = environment
    _refresh(env)
    next(row for row in env.listing if row["id"] == 102)["formato"] = "S"
    assert _refresh(env) == {"updated": 1, "pending": False}
    assert _detail_ids(env) == []
    saved = _record(env, "479-K")
    assert saved["formato_bling"] == "S"
    assert json.loads(saved["componentes_bling_json"]) == []
    assert json.loads(saved["estrutura_bling"]) == {}
    assert load_composition_catalog("tenant-a").resolve("479-K", "store-a")[0].sku == "479-K"
    summary = _summary()
    assert next(row for row in summary["rows"] if row["sku"] == "479")["itens"] == 42
    assert summary["totals"]["itens"] == 51
    assert summary["totals"]["itensDevolucao"] == 1


def test_unknown_component_with_failed_detail_cannot_be_assumed_simple(environment):
    env = environment
    _refresh(env)
    before = env.canonical.read_bytes()
    env.now += timedelta(hours=25)
    next(row for row in env.listing if row["id"] == 101).pop("formato")
    env.failures[101] = 503
    assert _refresh(env) == {"updated": 0, "pending": True}
    assert 101 in _detail_ids(env)
    assert env.canonical.read_bytes() == before
    assert _summary()["totals"]["itens"] == 60


def test_new_composition_observed_in_listing_wins_over_fresh_cached_bom(environment):
    env = environment
    _refresh(env)
    listing_kit = next(row for row in env.listing if row["id"] == 102)
    listing_kit["estrutura"] = {"componentes": [{"produto": {"id": 101}, "quantidade": 4}]}
    refreshed = _refresh(env)
    assert refreshed["updated"] > 0 and refreshed["pending"] is False
    assert _detail_ids(env) == []
    assert load_composition_catalog("tenant-a").resolve("479-K", "store-a")[0].fator == 4
    assert _summary()["totals"]["itens"] == 78


def test_cache_cannot_reuse_a_fresh_id_after_listed_sku_changes(environment):
    env = environment
    _refresh(env)
    before = env.canonical.read_bytes()
    next(row for row in env.listing if row["id"] == 102)["codigo"] = "OTHER"
    assert _refresh(env) == {"updated": 0, "pending": True}
    assert _detail_ids(env) == [102]
    assert env.canonical.read_bytes() == before
    assert _summary()["totals"]["itens"] == 60



def test_equal_legacy_overlay_bom_is_materialized_as_account_evidence(environment):
    env = environment
    rows = []
    for ident, sku in SKUS.items():
        components = [{"produto": {"id": 101}, "quantidade": FACTORS[ident]}] if ident in FACTORS else []
        rows.append({
            "sku": sku, "loja_sync": "Loja A", "id_bling": ident, "nome_bling": sku,
            "formato_bling": "E" if components else "S",
            "componentes_bling_json": json.dumps(components),
            "estrutura_bling": json.dumps({"tipoEstoque": "F", "componentes": components} if components else {}),
        })
    with env.compiled.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    env.protected[env.compiled] = env.compiled.read_bytes()
    assert not _record(env, "479").get("formato_bling")
    assert _record(env, "479-K") is None
    unresolved = load_composition_catalog("tenant-a")
    assert unresolved.resolve("479-K", "store-a")[0].sku == "479-K"
    assert unresolved.warnings
    assert _refresh(env) == {"updated": 4, "pending": False}
    for ident, sku in SKUS.items():
        saved = _record(env, sku)
        assert saved["id_bling"] == str(ident)
        assert saved["formato_bling"] == ("E" if ident in FACTORS else "S")
        if ident in FACTORS:
            assert json.loads(saved["componentes_bling_json"]) == [
                {"produto": {"id": 101}, "quantidade": FACTORS[ident]}
            ]
    resolved = load_composition_catalog("tenant-a")
    assert not resolved.warnings
    assert resolved.resolve("479-K", "store-a")[0].fator == 2
    assert _summary()["totals"]["itens"] == 60
    assert _summary()["totals"]["itensDevolucao"] == 2
