"""The automatic repair uses only the existing Bling catalog endpoints."""

import pytest
from types import SimpleNamespace

from backend.services import cadastro_catalogo_bling as provider


def _configure(monkeypatch, *, central=False):
    cfg = {"access_token": "token-a", "refresh_token": "refresh-a",
           "oauth_connection_id": "epoch-a", "connected": True}
    if central:
        cfg = {"central": True, "connected": True, "user_id": "owner-a", "site_id": "BR",
               "access_token": "jk-central:session-a:store-a:bling", "refresh_token": ""}
    monkeypatch.setattr(provider.integracoes, "ler_lojas", lambda _: [
        {"store_id": "store-a", "nome": "Multimarcas", "integracoes": {"bling": cfg}}
    ])
    monkeypatch.setattr(provider, "_preparar_credencial_coleta", lambda *args: dict(args[-1]))
    monkeypatch.setattr(provider, "_bling_executar_com_refresh", lambda _tenant, _name, config, call, **kw: (*call(config["access_token"]), config))
    return cfg


def test_only_structures_and_unclassified_products_need_detail_and_no_stock_or_categories(monkeypatch):
    _configure(monkeypatch)
    calls = []

    def get(_token, path, *, params, **kwargs):
        calls.append(path)
        assert kwargs["deadline"] - provider.time.monotonic() <= (
            provider.BLING_CATALOG_TIMEOUT_SECONDS if path == "/produtos" else 90
        )
        if path == "/produtos":
            assert params == {"pagina": 1, "limite": 100, "tipo": "T", "criterio": 5}
            return [
                {"id": 1, "codigo": "BASE", "formato": "S"},
                {"id": 2, "codigo": "KIT", "formato": "E"},
                {"id": 3, "codigo": "PARENT", "formato": "V"},
                {"id": 4, "codigo": "UNKNOWN"},
                {"id": 5, "codigo": "CACHED", "formato": "E"},
            ], 200
        if path == "/produtos/2":
            return {"id": 2, "codigo": "KIT", "formato": "E", "estrutura": {
                "componentes": [{"produto": {"id": 1}, "quantidade": 2}]}}, 200
        if path == "/produtos/4":
            return {"id": 4, "codigo": "UNKNOWN", "formato": "S"}, 200
        pytest.fail(f"unexpected request: {path}")

    monkeypatch.setattr(provider, "_get_json", get)
    result = provider.coletar_catalogo_bling("tenant-a", "store-a", compositions_only=True, skip_detail_ids=frozenset({"5"}))
    assert calls == ["/produtos", "/produtos/2", "/produtos/4"]
    assert result["coverage_complete"] is True
    assert result["stats"]["composition_listing_complete"] is True
    assert result["stats"]["details_complete"] == 2


def test_detail_deadline_keeps_complete_listing_and_already_confirmed_items(monkeypatch):
    _configure(monkeypatch)

    def get(_token, path, **kwargs):
        if path == "/produtos":
            return [{"id": 1, "codigo": "BASE", "formato": "S"},
                    {"id": 2, "codigo": "KIT", "formato": "E"},
                    {"id": 3, "codigo": "LATER", "formato": "E"}], 200
        if path == "/produtos/2":
            return {"id": 2, "codigo": "KIT", "formato": "E", "estrutura": {
                "componentes": [{"produto": {"id": 1}, "quantidade": 2}]}}, 200
        raise provider._CatalogoBlingPrazoExcedido()

    monkeypatch.setattr(provider, "_get_json", get)
    result = provider.coletar_catalogo_bling("tenant-a", "store-a", compositions_only=True)
    assert result["coverage_complete"] is False
    assert result["stats"]["composition_listing_complete"] is True
    assert result["stats"]["details_complete"] == 1
    assert result["stats"]["deadline_exceeded"] is True


def test_large_listing_does_not_restart_forever_before_detail_repair(monkeypatch):
    _configure(monkeypatch)
    clock = [0.0]
    monkeypatch.setattr(provider, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def get(_token, path, *, params, **kwargs):
        if path == "/produtos":
            clock[0] += 120
            if params["pagina"] == 1:
                return [{"id": i, "codigo": f"BASE-{i}", "formato": "S"} for i in range(1, 101)], 200
            return [{"id": 101, "codigo": "KIT", "formato": "E"}], 200
        assert path == "/produtos/101"
        assert kwargs["deadline"] == clock[0] + 90
        return {"id": 101, "codigo": "KIT", "formato": "E", "estrutura": {
            "componentes": [{"produto": {"id": 1}, "quantidade": 2}]}}, 200

    monkeypatch.setattr(provider, "_get_json", get)
    result = provider.coletar_catalogo_bling("tenant-a", "store-a", compositions_only=True)
    assert result["coverage_complete"] is True
    assert result["stats"]["composition_listing_complete"] is True
    assert result["stats"]["details_complete"] == 1


def test_central_exact_session_snapshot_works_without_local_oauth_epoch(monkeypatch):
    cfg = _configure(monkeypatch, central=True)
    monkeypatch.setattr(provider, "_get_json", lambda *_a, **_kw: ([], 200))
    assert provider.coletar_catalogo_bling("tenant-a", "store-a", compositions_only=True)["coverage_complete"] is True
    for field, value in [("user_id", "owner-b"), ("access_token", "jk-central:other:store-a:bling"), ("central", False), ("connected", False)]:
        changed = {**cfg, field: value}
        assert not provider._mesma_identidade_estavel_bling("store-a", "Multimarcas", cfg, changed)
    assert not provider._mesma_identidade_estavel_bling("store-a", "Multimarcas", {"access_token": "legacy"}, {"access_token": "legacy"})
