from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import bling, bling_accounts as accounts, integracoes


URL = "https://api.bling.com.br/Api/v3/empresas/me/dados-basicos"


@pytest.fixture(autouse=True)
def clean_registry():
    with accounts._LOCK:
        accounts._TOKENS.clear()
        accounts._DISCOVERY_LOCKS.clear()
    yield


def setup_discovery(monkeypatch, *, saved=True, response_id=987, url=URL):
    current = {"access_token": "credential", "refresh_token": "refresh", "id": "application",
               "oauth_connection_id": "authorization", "updated_at": "version"}
    calls, changes = [], []
    monkeypatch.setattr(integracoes, "_bling_config_atual", lambda *args: dict(current))
    def get(destination, **kwargs):
        calls.append((destination, kwargs))
        return SimpleNamespace(status_code=200, url=url, headers={}, json=lambda: {"data": {"id": response_id}})
    def persist(*args, **kwargs):
        changes.append(kwargs)
        if saved:
            current.update(kwargs["dados_api"])
        return saved, dict(current)
    monkeypatch.setattr(bling, "_bling_get_with_adaptive_limit", get)
    monkeypatch.setattr(integracoes, "_atualizar_bling_cas", persist)
    return current, calls, changes


def test_discovers_real_company_once_and_binds_evidence_to_authorization(monkeypatch):
    cfg, calls, changes = setup_discovery(monkeypatch)
    assert accounts.resolve_bling_account("tenant", "Shop", store_id="store") == "987"
    assert accounts.resolve_bling_account("tenant", "Renamed Shop", store_id="store") == "987"
    assert len(calls) == len(changes) == 1
    assert calls[0][0] == URL
    assert changes[0]["expected_access_token"] == "credential"
    assert changes[0]["expected_updated_at"] == "version"
    assert changes[0]["dados_api"] == {"empresa_id": "987", "empresa_oauth_connection_id": "authorization",
                                        "oauth_connection_id": "authorization"}
    assert accounts.account_for_token("credential") == "987"


def test_reconnection_does_not_reuse_old_company_evidence():
    cfg = {"access_token": "new-token", "oauth_connection_id": "new-authorization",
           "empresa_id": "987", "empresa_oauth_connection_id": "old-authorization"}
    assert accounts.remember_bling_configuration(cfg) == ""
    assert accounts.account_for_token("new-token") == ""


def test_refresh_registers_new_token_in_same_company_group():
    cfg = {"access_token": "old-token", "oauth_connection_id": "authorization",
           "empresa_id": "987", "empresa_oauth_connection_id": "authorization"}
    accounts.remember_bling_configuration(cfg)
    accounts.remember_bling_configuration({**cfg, "access_token": "new-token"})
    assert accounts.account_for_token("old-token") == accounts.account_for_token("new-token") == "987"


def test_cas_lost_does_not_assign_old_response_to_a_reconnection(monkeypatch):
    cfg, calls, changes = setup_discovery(monkeypatch, saved=False)
    with pytest.raises(HTTPException) as error:
        accounts.resolve_bling_account("tenant", "Shop", store_id="store")
    assert error.value.status_code == 409
    assert accounts.account_for_token("credential") == ""


@pytest.mark.parametrize("identity", [True, 0, "", "invalid id with spaces", {}, []])
def test_rejects_invalid_company_identity(monkeypatch, identity):
    _, _, changes = setup_discovery(monkeypatch, response_id=identity)
    with pytest.raises(HTTPException) as error:
        accounts.resolve_bling_account("tenant", "Shop", store_id="store")
    assert error.value.status_code == 502
    assert changes == []


def test_identity_discovery_rejects_redirected_destination(monkeypatch):
    _, _, changes = setup_discovery(monkeypatch, url="https://untrusted.invalid/company")
    with pytest.raises(HTTPException) as error:
        accounts.resolve_bling_account("tenant", "Shop", store_id="store")
    assert error.value.status_code == 502
    assert changes == []


@pytest.mark.parametrize("status", [403, 503])
def test_unavailable_company_metadata_keeps_existing_sales_compatible(monkeypatch, status):
    _, _, changes = setup_discovery(monkeypatch)
    monkeypatch.setattr(bling, "_bling_get_with_adaptive_limit", lambda *a, **kw:
        SimpleNamespace(status_code=status, url=URL, headers={}))
    assert accounts.resolve_bling_account("tenant", "Shop", store_id="store") == ""
    assert changes == []


def test_discovery_uses_existing_refresh_flow_before_binding_company(monkeypatch):
    cfg, _, changes = setup_discovery(monkeypatch)
    seen = []
    def get(*args, **kwargs):
        seen.append(kwargs["headers"]["Authorization"])
        if len(seen) == 1:
            return SimpleNamespace(status_code=401, url=URL, headers={})
        return SimpleNamespace(status_code=200, url=URL, headers={}, json=lambda: {"data": {"id": 987}})
    monkeypatch.setattr(bling, "_bling_get_with_adaptive_limit", get)
    monkeypatch.setattr(integracoes, "renovar_token_bling_loja", lambda *a, **kw: {**cfg, "access_token": "renewed"})
    assert accounts.resolve_bling_account("tenant", "Shop", store_id="store") == "987"
    assert seen == ["Bearer credential", "Bearer renewed"]
    assert changes[0]["expected_access_token"] == "renewed"


def test_invalid_oauth_never_queries_company_metadata(monkeypatch):
    from backend.services.bling_oauth import BlingAuthenticationError
    _, calls, changes = setup_discovery(monkeypatch)
    with pytest.raises(BlingAuthenticationError):
        accounts.resolve_bling_account("tenant", "Shop", store_id="store",
            config={"access_token": "invalid", "oauth_invalid": True})
    assert calls == changes == []


def test_discovers_and_reuses_real_hexadecimal_company_id(monkeypatch):
    identity = "436c56a5679921f5f13a3d6433561773"
    _, calls, _ = setup_discovery(monkeypatch, response_id=identity)
    assert accounts.resolve_bling_account("tenant", "Shop", store_id="store") == identity
    assert accounts.account_for_token("credential") == identity
    assert accounts.resolve_bling_account("tenant", "Shop", store_id="store") == identity
    assert len(calls) == 1
