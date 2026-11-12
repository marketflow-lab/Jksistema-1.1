from __future__ import annotations

import asyncio
import copy
import json
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import central_accounts_client as client
from backend.services import central_accounts_store_index as index
from backend.services import central_provider_overlay as overlay
from backend.services import integracoes, integracoes_api


STORE_ID = "a" * 32
PUBLIC = {"store_id": STORE_ID, "nome": "Central shop", "owner_client_id": "tenant-a", "access": "owner",
          "integracoes": {"bling": {"central": True, "connected": True, "user_id": "company-a", "site_id": ""}}}
LOCAL = {"store_id": STORE_ID, "nome": "Local shop", "custom": {"photo_group": "keep"}, "integracoes": {
    "bling": {"id": "app", "secret": "fixture-secret", "access_token": "fixture-access",
              "refresh_token": "fixture-refresh", "oauth_connection_id": "fixture-connection", "connected": True},
    "mercadolivre": {"app_id": "ml-app", "client_secret": "ml-secret", "access_token": "ml-access",
                     "refresh_token": "ml-refresh", "user_id": "seller", "site_id": "MLB"},
    "mercadoturbo": {"token": "turbo-local", "connected": True}}}


def test_legacy_public_contracts_remain_compatible():
    contract = json.loads(Path("tests/contracts/bling_central_provider_v1.json").read_text())
    for filename, expected in contract["unchanged_contracts"].items():
        assert hashlib.sha256(Path(filename).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == expected


def scoped(stores=None):
    return client.CentralClient(
        {"sync_mode": "provider", "providers": ["bling"], "session": "opaque-fixture",
         "expires_at": int(time.time()) + 600, "stores": copy.deepcopy([PUBLIC] if stores is None else stores)},
        {"client_id": "tenant-a", "username": "owner", "machine_id": "machine-a"}, {},
        configuration=SimpleNamespace(url="https://central.example.test/api/auth/login"))


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    info = tmp_path / "info"
    target = info / "tenant-a"
    target.mkdir(parents=True)
    integracoes.configure_integracoes_context(pasta_info=str(info), get_tenant_path=lambda name: str(info / name),
                                            normalizar_integracao_conectada=lambda _service, cfg: cfg)
    token = client._current.set(None)
    path = target / "lojas_config.json"
    integracoes.salvar_lojas("tenant-a", [copy.deepcopy(LOCAL)])
    yield path
    client._current.reset(token)


def test_provider_scope_keeps_legacy_workflows_and_binds_tenant():
    value = scoped()
    token = client._current.set(value)
    try:
        assert client.current("tenant-a") is None
        assert client.session_client("tenant-a") is value
        assert client.current_for_provider("tenant-a", "bling", STORE_ID) is value
        assert client.current_for_provider("tenant-a", "bling", "b" * 32) is None
        assert client.current_for_provider("tenant-a", "mercadolivre") is None
        client.assert_manual_path("/api/shared-sync/auto-pull")
        with pytest.raises(HTTPException) as exc:
            client.session_client("tenant-b")
        assert exc.value.status_code == 403
    finally:
        client._current.reset(token)


def test_index_and_reads_preserve_other_integrations_and_pilot_stores(tenant):
    second = {**copy.deepcopy(LOCAL), "store_id": "b" * 32, "nome": "Other shop"}
    integracoes.salvar_lojas("tenant-a", [copy.deepcopy(LOCAL), second])
    before = json.loads(tenant.read_text())
    value = scoped()
    client._current.set(value)
    index.materialize_store_index("tenant-a", [PUBLIC], providers=["bling"])
    disk = json.loads(tenant.read_text())
    assert disk[0]["integracoes"]["mercadolivre"] == before[0]["integracoes"]["mercadolivre"]
    assert disk[0]["integracoes"]["mercadoturbo"] == before[0]["integracoes"]["mercadoturbo"]
    assert disk[0]["custom"] == LOCAL["custom"] and disk[0]["nome"] == LOCAL["nome"]
    assert disk[1]["integracoes"]["bling"] == second["integracoes"]["bling"]
    assert "fixture-refresh" not in json.dumps(disk[0]["integracoes"]["bling"])
    index.assert_legacy_sync_allowed("tenant-a")
    operational = integracoes.buscar_loja_snapshot("tenant-a", LOCAL["nome"], STORE_ID)
    assert client.is_marker(operational["integracoes"]["bling"]["access_token"])
    assert operational["integracoes"]["mercadolivre"] == before[0]["integracoes"]["mercadolivre"]
    public = integracoes_api._get_lojas_sync("tenant-a")
    assert "jk-central:" not in json.dumps(public)
    assert public[0]["integracoes"]["bling"]["central"]


def test_local_ml_token_update_never_persists_bling_marker_or_revives_stale_token(tenant):
    value = scoped()
    client._current.set(value)
    index.materialize_store_index("tenant-a", [PUBLIC], providers=["bling"])
    integracoes.atualizar_api_loja("tenant-a", LOCAL["nome"], "mercadolivre",
                                 {"access_token": "ml-new", "refresh_token": "ml-rotated"}, store_id=STORE_ID)
    # A delayed whole-store save from before the migration must retain the guard.
    old = copy.deepcopy(LOCAL)
    old["integracoes"]["mercadolivre"]["access_token"] = "ml-next"
    integracoes.salvar_lojas("tenant-a", [old])
    disk = json.loads(tenant.read_text())[0]
    assert disk["integracoes"]["mercadolivre"]["access_token"] == "ml-next"
    assert disk["integracoes"]["bling"]["central_migrated"]
    assert "fixture-refresh" not in json.dumps(disk["integracoes"]["bling"])
    assert "jk-central:" not in tenant.read_text()


@pytest.mark.parametrize("session_state", ["absent", "expired", "revoked"])
def test_migrated_bling_never_falls_back_to_local_tokens(tenant, session_state):
    before = json.loads(tenant.read_text())[0]
    index.materialize_store_index("tenant-a", [PUBLIC], providers=["bling"])
    value = scoped([] if session_state == "revoked" else None)
    if session_state == "expired":
        value.expires_at = 1
    if session_state != "absent":
        client._current.set(value)
    # Only already migrated rows are unavailable without an authorized session.
    actual = integracoes.buscar_loja_snapshot("tenant-a", LOCAL["nome"], STORE_ID)
    cfg = actual["integracoes"]["bling"]
    assert not cfg["connected"] and "access_token" not in cfg
    assert actual["integracoes"]["mercadolivre"] == before["integracoes"]["mercadolivre"]
    with pytest.raises(HTTPException):
        integracoes.atualizar_api_loja("tenant-a", LOCAL["nome"], "bling", LOCAL["integracoes"]["bling"], store_id=STORE_ID)
    with pytest.raises(HTTPException):
        integracoes.renovar_token_bling_loja("tenant-a", LOCAL["nome"], LOCAL["integracoes"]["bling"], store_id=STORE_ID)


def test_scope_rejects_ml_central_payload_without_altering_local_state(tenant):
    original = tenant.read_bytes()
    bad = copy.deepcopy(PUBLIC)
    bad["integracoes"]["mercadolivre"] = dict(bad["integracoes"]["bling"])
    with pytest.raises(ValueError):
        scoped([bad])
    with pytest.raises(ValueError):
        index.materialize_store_index("tenant-a", [bad], providers=["bling"])
    assert tenant.read_bytes() == original


def test_expired_provider_session_preserves_local_jwt_workflows(tenant):
    value = scoped()
    index.materialize_store_index("tenant-a", [PUBLIC], providers=["bling"])
    value.expires_at = 1
    client._sessions[client.session_key("local-fixture-jwt")] = value
    try:
        client.bind_request("local-fixture-jwt", {"jk_central": 1, "client_id": "tenant-a",
                                                "sub": "owner", "machine_id": "machine-a"})
        assert client.current("tenant-a") is None
        cfg = integracoes.buscar_loja_snapshot("tenant-a", LOCAL["nome"], STORE_ID)["integracoes"]
        assert cfg["mercadolivre"]["access_token"] == "ml-access"
        assert not cfg["bling"]["connected"]
        with pytest.raises(HTTPException):
            value.call("GET", "/bootstrap")
    finally:
        client._sessions.pop(client.session_key("local-fixture-jwt"), None)


def test_scoped_transport_requires_the_initiating_session_context(monkeypatch):
    value = scoped()
    marker = value.stores()[0]["integracoes"]["bling"]["access_token"]
    calls = []
    monkeypatch.setattr(value, "call", lambda *args, **kwargs: calls.append(args))
    token = client._current.set(None)
    try:
        with pytest.raises(HTTPException) as exc:
            client.provider_request(marker, "GET", "https://api.bling.com.br/Api/v3/produtos")
        assert exc.value.status_code == 403 and not calls
        with pytest.raises(HTTPException):
            client.bling_account_for_marker(marker)
    finally:
        client._current.reset(token)


def test_central_reconnection_and_disconnect_use_exact_bling_store(tenant, monkeypatch):
    from backend.schemas import AuthRequest
    value = scoped()
    client._current.set(value)
    calls = []
    monkeypatch.setattr(value, "call", lambda *args: calls.append(args) or {"success": True})
    monkeypatch.setattr(value, "refresh_stores", lambda: [PUBLIC])
    req = AuthRequest(loja=LOCAL["nome"], store_id=STORE_ID, client_id="app", client_secret="secret")
    asyncio.run(integracoes_api.start_bling_auth(req, None, "tenant-a"))
    asyncio.run(integracoes_api.disconnect_integracao(LOCAL["nome"], "bling", STORE_ID, "tenant-a"))
    assert calls[0][1] == f"/stores/{STORE_ID}/connect"
    assert calls[1][1] == f"/stores/{STORE_ID}/disconnect"
    assert all(call[2]["provider"] == "bling" for call in calls)


def test_timestamp_only_change_still_refreshes_instead_of_invalidating(tenant, monkeypatch):
    before = copy.deepcopy(LOCAL["integracoes"]["bling"])
    before["updated_at"] = "100"
    integracoes.atualizar_api_loja("tenant-a", LOCAL["nome"], "bling", {"updated_at": "200"}, store_id=STORE_ID)
    calls = []
    monkeypatch.setattr(integracoes, "exchange_bling_refresh_token", lambda *args: calls.append(args) or {
        "access_token": "renewed", "refresh_token": "rotated"})
    result, disposition = integracoes.renovar_token_bling_loja("tenant-a", LOCAL["nome"], before,
                                                              store_id=STORE_ID, return_disposition=True)
    assert len(calls) == 1 and result["access_token"] == "renewed"
    assert disposition == "committed_by_caller" and not result["oauth_invalid"]
