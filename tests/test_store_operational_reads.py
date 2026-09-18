import copy
import json
import threading
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from backend.services import integracoes, central_accounts_client, store_listing_service
from backend.services import store_public_snapshot as projection
from backend.services.store_oauth_refresh import refresh_ml


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(central_accounts_client, "current", lambda _client: None)
    monkeypatch.setattr(integracoes, "PASTA_INFO", str(tmp_path))
    monkeypatch.setattr(integracoes, "_get_tenant_path", lambda client: str(tmp_path / client))
    monkeypatch.setattr(store_listing_service, "_ensure_initialization", lambda *_args: True)
    row = {"store_id": "exact-a", "nome": "Store A", "integracoes": {
        "mercadolivre": {"user_id": "123", "site_id": "MLB", "id": "application-a", "secret": "secret",
                         "access_token": "old-access", "refresh_token": "old-refresh", "updated_at": "1"},
        "bling": {"oauth_connection_id": "bling-a", "id": "bling-app", "access_token": "bling-access"}}}
    path = tmp_path / "a"
    path.mkdir()
    def publish(value=row):
        (path / "lojas_config.json").write_text(json.dumps([value]), encoding="utf-8")
        projection.write_snapshot(path, projection.build_snapshot([value]))
    publish()
    return path, row, publish


def test_provider_identities_are_independent_and_current_tokens_are_read(stores):
    path, row, _ = stores
    updated = copy.deepcopy(row)
    updated["integracoes"]["mercadolivre"]["user_id"] = "changed"
    updated["integracoes"]["bling"]["access_token"] = "new-token"
    (path / "lojas_config.json").write_text(json.dumps([updated]), encoding="utf-8")
    read = integracoes.ler_lojas("a")[0]
    assert "mercadolivre" not in read["integracoes"]
    assert read["integracoes"]["bling"]["access_token"] == "new-token"
    assert read["_unavailable_providers"] == ["mercadolivre"]
    updated["integracoes"]["bling"]["oauth_connection_id"] = "other-account"
    (path / "lojas_config.json").write_text(json.dumps([updated]), encoding="utf-8")
    assert not integracoes.ler_lojas("a")[0]["integracoes"]


def test_projection_excludes_secrets_and_keeps_public_api_shape(stores):
    path, _, _ = stores
    content = (path / "lojas_public_snapshot.json").read_text(encoding="utf-8")
    assert "old-access" not in content and "old-refresh" not in content and "secret" not in content
    assert "provider_identities" not in store_listing_service.read_store_cards("a")["lojas"][0]


def test_bling_application_without_connection_epoch_never_authorizes_credentials(stores):
    path, row, publish = stores
    row["integracoes"]["bling"].pop("oauth_connection_id")
    publish(row)
    row["integracoes"]["bling"]["access_token"] = "different-account-same-app"
    (path / "lojas_config.json").write_text(json.dumps([row]), encoding="utf-8")
    read = integracoes.ler_lojas("a")[0]
    assert "bling" not in read["integracoes"]
    assert "bling" in read["_unavailable_providers"]


def test_reader_remains_fast_while_other_process_holds_lock_beyond_ten_seconds(stores):
    path, _, _ = stores
    script = "import sys; from backend.services.store_coordination import store_lock; "
    script += "\nwith store_lock(sys.argv[1]):\n print('ready', flush=True)\n sys.stdin.readline()\n"
    child = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        start = time.perf_counter()
        assert integracoes.ler_lojas("a")[0]["store_id"] == "exact-a"
        first = time.perf_counter() - start
        assert first < .5
        # The previous maintenance path consumes the whole acquisition budget.
        with pytest.raises(HTTPException) as exc:
            integracoes.carregar_lojas("a")
        assert exc.value.detail["code"] == "stores_busy"
        assert time.perf_counter() - start >= 10
        second_start = time.perf_counter()
        assert integracoes.ler_lojas("a")[0]["store_id"] == "exact-a"
        second = time.perf_counter() - second_start
        assert second < .5
        print(f"read_before_ms={first * 1000:.2f} read_after_ms={second * 1000:.2f} legacy_wait_ms={(second_start-start)*1000:.2f}")
    finally:
        child.communicate("release\n", timeout=5)


def test_corrupt_credentials_are_temporarily_unavailable_not_empty(stores):
    path, _, _ = stores
    (path / "lojas_config.json").write_text("{", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        integracoes.ler_lojas("a")
    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "2"


def test_version_one_snapshot_schedules_upgrade_without_trusting_bling(stores, monkeypatch):
    path, row, _ = stores
    v1 = projection.build_snapshot([row])
    v1["schema_version"] = 1
    for item in v1["lojas"]:
        item.pop("provider_identities")
    projection.write_snapshot(path, v1)
    requested = []
    monkeypatch.setattr(store_listing_service, "_ensure_initialization", lambda *args: requested.append(args))
    read = integracoes.ler_lojas("a")[0]
    assert requested
    assert "mercadolivre" in read["integracoes"]
    assert "bling" not in read["integracoes"]


@pytest.fixture
def refresh_state(stores, monkeypatch):
    _, row, _ = stores
    state = [copy.deepcopy(row)]
    saved = []
    in_writer = []
    @contextmanager
    def writer(_client):
        in_writer.append(True)
        try:
            yield
        finally:
            in_writer.pop()
    monkeypatch.setattr(integracoes, "buscar_loja_snapshot", lambda *_args: copy.deepcopy(state[0]) if state else None)
    monkeypatch.setattr(integracoes, "carregar_lojas", lambda _client: state)
    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", writer)
    monkeypatch.setattr(integracoes, "salvar_lojas", lambda client, rows: saved.append(copy.deepcopy(rows)))
    hint = dict(row["integracoes"]["mercadolivre"], _store_id_context=row["store_id"])
    return state, saved, in_writer, hint


def test_refresh_network_runs_outside_writer_and_preserves_rename(refresh_state):
    state, saved, held, hint = refresh_state
    def exchange(_client, _name, cfg):
        assert not held
        state[0]["nome"] = "Renamed"
        return dict(cfg, access_token="new-access", refresh_token="new-refresh", updated_at="2")
    result = refresh_ml("a", "Store A", hint, exchange)
    assert result["access_token"] == "new-access"
    assert saved[0][0]["nome"] == "Renamed"
    assert "_store_id_context" not in saved[0][0]["integracoes"]["mercadolivre"]


@pytest.mark.parametrize("change", ["delete", "disconnect", "reconnect", "refresh"])
def test_refresh_does_not_overwrite_concurrent_connection_change(refresh_state, change):
    state, saved, _, hint = refresh_state
    def exchange(_client, _name, cfg):
        if change == "delete":
            state.clear()
        elif change == "disconnect":
            state[0]["integracoes"].pop("mercadolivre")
        elif change == "reconnect":
            state[0]["integracoes"]["mercadolivre"]["user_id"] = "other-seller"
        else:
            state[0]["integracoes"]["mercadolivre"]["access_token"] = "concurrent-token"
        return dict(cfg, access_token="obsolete-token")
    if change == "refresh":
        assert refresh_ml("a", "Store A", hint, exchange)["access_token"] == "concurrent-token"
    else:
        with pytest.raises(HTTPException) as exc:
            refresh_ml("a", "Store A", hint, exchange)
        assert exc.value.status_code == 409
    assert saved == []


def test_two_refresh_callers_share_one_exchange(refresh_state):
    _, saved, _, hint = refresh_state
    entered, release = threading.Event(), threading.Event()
    results, failures, calls = [], [], []
    def exchange(_client, _name, cfg):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return dict(cfg, access_token="shared-new", refresh_token="shared-refresh")
    def run():
        try:
            results.append(refresh_ml("a", "Store A", hint, exchange))
        except Exception as exc:
            failures.append(exc)
    first, second = threading.Thread(target=run), threading.Thread(target=run)
    first.start()
    assert entered.wait(3)
    second.start()
    release.set()
    first.join(4)
    second.join(4)
    assert not failures and len(results) == 2
    assert len(calls) == 1 and len(saved) == 1
    assert all(result["access_token"] == "shared-new" for result in results)


@pytest.mark.parametrize("status", [401, 403, 503])
def test_tools_do_not_bypass_unavailable_authorized_reader(monkeypatch, status):
    from backend.services import codex_bling_tools
    from backend.services.whatsapp import intent_runtime
    def fail(_client):
        raise HTTPException(status, "unavailable")
    monkeypatch.setattr(integracoes, "ler_lojas", fail)
    for reader in (lambda: codex_bling_tools._connected_stores("missing-tenant", None),
                   lambda: intent_runtime._whatsapp_load_store_configs("missing-tenant")):
        with pytest.raises(HTTPException) as exc:
            reader()
        assert exc.value.status_code == status


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_ml_write_revalidates_account_before_network(stores, monkeypatch, method):
    from backend.services import mercadolivre_legacy_api as api
    path, row, publish = stores
    hint = dict(row["integracoes"]["mercadolivre"], _store_id_context=row["store_id"])
    sent = []
    monkeypatch.setattr(api, "_ml_http_request", lambda *args, **kwargs: sent.append(args), raising=False)
    changed = copy.deepcopy(row)
    changed["integracoes"]["mercadolivre"]["user_id"] = "other-account"
    publish(changed)
    with pytest.raises(HTTPException) as exc:
        api._ml_api_request("a", "Store A", hint, method, "https://api.mercadolibre.com/test")
    assert exc.value.status_code == 409
    assert sent == []


def test_rotated_refresh_is_committed_despite_unrelated_metadata_edit(refresh_state):
    state, saved, _, hint = refresh_state
    def exchange(_client, _name, cfg):
        state[0]["integracoes"]["mercadolivre"].update(_sync_version=2, preference="keep")
        return dict(cfg, access_token="rotated-access", refresh_token="rotated-refresh")
    result = refresh_ml("a", "Store A", hint, exchange)
    assert result["refresh_token"] == "rotated-refresh"
    assert saved[0][0]["integracoes"]["mercadolivre"]["preference"] == "keep"


@pytest.mark.parametrize("row_site", ["MLB", ""])
def test_refresh_uses_confirmed_site_without_persisting_runtime_hint(refresh_state, row_site):
    state, saved, _, hint = refresh_state
    state[0]["integracoes"]["mercadolivre"].pop("site_id")
    if row_site:
        state[0]["site_id"] = row_site
    result = refresh_ml("a", "Store A", hint, lambda _c, _n, cfg: dict(cfg, access_token="new"))
    assert result["site_id"] == "MLB"
    assert "site_id" not in saved[0][0]["integracoes"]["mercadolivre"]


def test_oauth_gate_respects_remaining_read_deadline_and_reports_temporary_failure(monkeypatch):
    from backend.services import perguntas_loading_transport, store_coordination
    from backend.services.store_oauth_refresh import oauth_gate
    monkeypatch.setattr(perguntas_loading_transport, "remaining", lambda: 2.5)
    seen = []
    @contextmanager
    def fail(*args, **kwargs):
        seen.append(kwargs["timeout_seconds"])
        raise store_coordination.StoreCoordinationError("locked")
        yield
    monkeypatch.setattr(store_coordination, "oauth_refresh_lock", fail)
    with pytest.raises(HTTPException) as exc:
        with oauth_gate("synthetic", "s", "mercadolivre"):
            pass
    assert seen == [2.5]
    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "2"


def test_ml_write_revalidates_again_after_oauth_before_retry(stores, monkeypatch):
    from types import SimpleNamespace
    from backend.services import mercadolivre_legacy_api as api
    _, row, publish = stores
    hint = dict(row["integracoes"]["mercadolivre"], _store_id_context=row["store_id"])
    sent = []
    def request(*args, **kwargs):
        sent.append(args)
        return SimpleNamespace(status_code=401)
    def refresh(_client, _name, cfg):
        changed = copy.deepcopy(row)
        changed["integracoes"]["mercadolivre"]["user_id"] = "another-account"
        publish(changed)
        return cfg
    monkeypatch.setattr(api, "_ml_http_request", request, raising=False)
    monkeypatch.setattr(api, "_ml_http_invalidar_session", lambda *_args: None, raising=False)
    monkeypatch.setattr(api, "_ml_refresh_token", refresh)
    with pytest.raises(HTTPException) as exc:
        api._ml_api_request("a", "Store A", hint, "POST", "https://api.mercadolibre.com/test")
    assert exc.value.status_code == 409
    assert len(sent) == 1
