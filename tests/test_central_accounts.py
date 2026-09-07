from __future__ import annotations

import base64
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import bcrypt
import pytest
from fastapi.testclient import TestClient

from cloud.auth_gateway.app.central_accounts import CentralAccounts, CentralError, Principal, key_for
from cloud.auth_gateway.app.central_contracts import (ConnectRequest, LegacyAdoptionRequest,
                                                       ProviderRequest, StoreCreate, StoreGrant)
from cloud.auth_gateway.app.central_provider import ProviderTransport
from cloud.auth_gateway.app.central_store import MemoryDocuments, Vault
from cloud.auth_gateway.app.domain import AuthenticationService
from cloud.auth_gateway.app.contracts import GatewayLoginRequest
from cloud.auth_gateway.app.policy import normalize_permissions
from cloud.auth_gateway.app.main import create_app


NOW = 1800000000


class Users:
    def __init__(self):
        self.rows = {name: {"username": name, "client_id": tenant, "active": True,
                           "valid_until": "31/12/2030", "machine_ids": ["machine-a", "machine-b"],
                           "max_machines": 2, "password_hash": bcrypt.hashpw(b"password", bcrypt.gensalt(4)).decode(),
                           "permissions": {"integracao": True, "vendas": True, "cadastro": True}}
                     for name, tenant in (("owner", "tenant-a"), ("reader", "tenant-a"), ("other", "tenant-b"))}

    def get_user(self, username):
        return copy.deepcopy(self.rows.get(username))

    def claim_machine(self, username, password, machine):
        return self.get_user(username)

    def enable_central_accounts(self, username, expected_client_id, machine, expected_password_epoch):
        row = self.rows[username]
        assert row["client_id"] == expected_client_id
        assert machine in row["machine_ids"]
        assert expected_password_epoch == key_for(row["password_hash"])
        row["central_accounts_enabled"] = True
        return self.get_user(username)


class Provider:
    validate = staticmethod(ProviderTransport.validate)

    def __init__(self):
        self.requests, self.refreshes, self.exchanges = [], 0, 0
        self.refresh_error = None
        self.entered = self.release = None
        self.status = 200

    def exchange(self, draft, code):
        self.exchanges += 1
        return {"provider": draft["provider"], "app_id": draft["app_id"], "app_secret": "secret-provider-app",
                "access_token": "secret-provider-access", "refresh_token": "secret-provider-refresh",
                "expires_at": NOW + 3600, "seller_id": "123", "site_id": "MLB"}

    def refresh(self, credentials):
        self.refreshes += 1
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        if self.refresh_error:
            raise self.refresh_error
        return {**credentials, "access_token": "secret-provider-new", "refresh_token": "secret-refresh-new",
                "expires_at": NOW + 7200}

    def request(self, credentials, payload):
        self.requests.append((credentials["access_token"], payload.path))
        return {"status": self.status, "headers": {"Content-Type": "application/json"},
                "body_base64": base64.b64encode(b'{"data":[]}').decode()}

    def identify(self, credentials):
        if credentials.get("access_token") == "invalid":
            raise CentralError("central_reconnect_required", 409)
        suffix = credentials["app_id"].split("-")[-1]
        return {"seller_id": "account-" + suffix,
                "site_id": "MLB" if credentials["provider"] == "mercadolivre" else ""}


@pytest.fixture
def central():
    users, provider = Users(), Provider()
    return CentralAccounts(users, MemoryDocuments(), Vault(base64.b64encode(b"v" * 32).decode()),
                           provider, "https://central.example.test", clock=lambda: NOW)


def principal(central, username="owner", machine="machine-a"):
    user = central.users.get_user(username)
    return Principal(username, user["client_id"], machine, normalize_permissions(user["permissions"]))


def connected(central):
    owner = principal(central)
    store = central.create_store(owner, StoreCreate(name="Shop", request_id="a" * 32))
    flow = central.connect(owner, store["store_id"], ConnectRequest(provider="mercadolivre", app_id="app",
                                                                  app_secret="provider-secret"))
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(flow["url"]).query)["state"][0]
    central.complete_oauth(state, "one-use-code")
    return owner, store["store_id"], state


def request(method="GET", path="/orders/search", request_id="b" * 32):
    return ProviderRequest(provider="mercadolivre", method=method, path=path, request_id=request_id)


def test_bootstrap_is_minimal_and_never_calls_provider(central):
    owner, identity, _ = connected(central)
    result = central.bootstrap_session(central.users.get_user("owner"), "owner", "machine-b")
    assert result["sync_mode"] == "manual"
    assert result["stores"][0]["store_id"] == identity
    assert not central.provider.requests and central.provider.refreshes == 0
    assert "secret-provider" not in json.dumps(result)
    assert "access_token" not in json.dumps(result)
    assert central.authenticate(result["session"], "machine-b").key == owner.key


@pytest.mark.parametrize("change", ["expired", "machine", "password", "tenant", "disabled", "revoked"])
def test_session_revalidates_current_authority(central, change):
    result = central.bootstrap_session(central.users.get_user("owner"), "owner", "machine-a")
    machine = "machine-a"
    if change == "expired":
        central.clock = lambda: NOW + 28801
    elif change == "machine":
        machine = "machine-b"
    elif change == "password":
        central.users.rows["owner"]["password_hash"] = "changed"
    elif change == "tenant":
        central.users.rows["owner"]["client_id"] = "tenant-b"
    elif change == "disabled":
        central.users.rows["owner"]["active"] = False
    else:
        central.users.rows["owner"]["machine_ids"] = ["machine-b"]
    with pytest.raises(CentralError):
        central.authenticate(result["session"], machine)


def test_grant_is_explicit_scoped_and_revoked_without_relogin(central):
    owner, identity, _ = connected(central)
    other = principal(central, "other")
    assert central.list_stores(other) == []
    with pytest.raises(CentralError):
        central.request(other, identity, request())
    central.grant(owner, identity, StoreGrant(username="other", access="read"))
    assert central.list_stores(other)[0]["owner_client_id"] == "tenant-a"
    assert central.request(other, identity, request())["status"] == 200
    with pytest.raises(CentralError) as error:
        central.request(other, identity, request("POST", "/items"))
    assert error.value.code == "central_store_read_only"
    with pytest.raises(CentralError):
        central.grant(other, identity, StoreGrant(username="reader", access="write"))
    central.grant(owner, identity, StoreGrant(username="other", access="revoke"))
    with pytest.raises(CentralError):
        central.request(other, identity, request())


def test_oauth_is_one_use_and_checks_revocation(central):
    owner, identity, state = connected(central)
    with pytest.raises(CentralError):
        central.complete_oauth(state, "same-code")
    assert central.provider.exchanges == 1
    central.delete_store(owner, identity)
    assert central.list_stores(owner) == []
    with pytest.raises(CentralError):
        central.request(owner, identity, request())


def expire_connection(central, owner, identity):
    connection_id = central.store(owner, identity)["connections"]["mercadolivre"]["id"]
    row = central.documents.get("connections", connection_id)
    credentials = central.vault.open("connection:" + connection_id, row["sealed"])
    credentials["expires_at"] = NOW - 1
    central.documents.change("connections", connection_id, lambda old: {
        **old, "sealed": central.vault.seal("connection:" + connection_id, credentials)})
    return connection_id


def test_refresh_is_single_authority_across_machines(central):
    owner, identity, _ = connected(central)
    expire_connection(central, owner, identity)
    central.provider.entered, central.provider.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(central.request, owner, identity, request())
        assert central.provider.entered.wait(5)
        try:
            with pytest.raises(CentralError) as error:
                central.request(principal(central, machine="machine-b"), identity, request())
            assert error.value.code == "central_refresh_busy"
        finally:
            central.provider.release.set()
        assert first.result()["status"] == 200
    assert central.request(owner, identity, request())["status"] == 200
    assert central.provider.refreshes == 1
    assert all(token == "secret-provider-new" for token, _ in central.provider.requests)


def test_uncertain_refresh_is_never_replayed(central):
    owner, identity, _ = connected(central)
    expire_connection(central, owner, identity)
    central.provider.refresh_error = TimeoutError("private request must not be echoed")
    for _ in range(2):
        with pytest.raises(CentralError) as error:
            central.request(owner, identity, request())
        assert error.value.code == "central_reconnect_required"
    assert central.provider.refreshes == 1
    assert not central.provider.requests


def test_mutation_not_replayed_on_retry_or_401(central):
    owner, identity, _ = connected(central)
    central.provider.status = 401
    central.request(owner, identity, request("POST", "/items"))
    assert len(central.provider.requests) == 1
    with pytest.raises(CentralError):
        central.request(owner, identity, request("POST", "/items"))
    assert len(central.provider.requests) == 1


@pytest.mark.parametrize("path", ["https://evil.test/items", "//evil.test/items", "/oauth/token",
                                   "/items/../oauth/token", "/items/%2e%2e/oauth/token",
                                   "/items/%252e%252e/token", "/items?access_token=x", "/users/999/items",
                                   "/users/123/applications", "/users/123/credentials"])
def test_provider_destination_and_seller_are_not_caller_controlled(central, path):
    owner, identity, _ = connected(central)
    with pytest.raises(CentralError):
        central.request(owner, identity, request(path=path))
    assert not central.provider.requests


def test_vault_context_prevents_copying_credentials_to_another_connection(central):
    value = central.vault.seal("connection:a", {"refresh_token": "private"})
    from cloud.auth_gateway.app.errors import GatewayUnavailable
    with pytest.raises(GatewayUnavailable):
        central.vault.open("connection:b", value)
    assert "private" not in value


def test_http_requires_session_and_rejects_tenant_injection(central):
    client = TestClient(create_app(central=central))
    assert client.get("/api/central/v1/bootstrap").status_code == 401
    session = central.bootstrap_session(central.users.get_user("owner"), "owner", "machine-a")
    headers = {"Authorization": "Bearer " + session["session"], "X-JK-Machine": "machine-a"}
    response = client.post("/api/central/v1/stores", headers=headers,
                           json={"name": "Store", "request_id": "f" * 32, "tenant": "injected"})
    assert response.status_code == 422 and "injected" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/central/v1/bootstrap", headers=headers).json()["stores"] == []


def test_store_write_grant_does_not_override_module_permission(central):
    owner, identity, _ = connected(central)
    central.grant(owner, identity, StoreGrant(username="other", access="write"))
    central.users.rows["other"]["permissions"] = {"vendas": True}
    with pytest.raises(CentralError) as error:
        central.request(principal(central, "other"), identity, request("POST", "/items"))
    assert error.value.code == "central_permission_denied"
    assert not central.provider.requests


@pytest.mark.parametrize("override", [{"headers": {"authorization": "stolen"}},
                                      {"headers": {"x-version": "a\r\nb"}},
                                      {"params": {"seller": "999"}},
                                      {"body": {"nested": [{"refresh_token": "private"}]}}])
def test_closed_provider_contract_rejects_credential_and_header_injection(central, override):
    owner, identity, _ = connected(central)
    payload = request().model_copy(update=override)
    with pytest.raises(CentralError):
        central.request(owner, identity, payload)
    assert not central.provider.requests


def test_login_bootstrap_requires_protocol_negotiation(central):
    class Issuer:
        def issue(self, uid, claims):
            self.claims = claims
            return "signed-identity", 3600
    issuer = Issuer()
    auth = AuthenticationService(central.users, issuer, "1.0.133", central)
    login = GatewayLoginRequest(username="owner", password="password", machine_id="machine-a",
                                app_version="1.0.133", request_nonce="n" * 43)
    assert "central" not in auth.authenticate(login)
    result = auth.authenticate(login, central_protocol=True)
    assert result["central"]["sync_mode"] == "manual"
    from cloud.auth_gateway.app.policy import canonical_hash
    assert issuer.claims["jk_response_hash"] == canonical_hash({
        key: result[key] for key in ("user_data", "permissions", "policy", "request_nonce", "central")})


def test_production_rollout_requires_enrollment_and_revalidates_it(central):
    central.enrollment_required = True
    user = central.users.rows['owner']
    assert not central.enabled_for(user)
    with pytest.raises(CentralError):
        central.bootstrap_session(user, 'owner', 'machine-a')
    user['central_accounts_enabled'] = True
    bootstrap = central.bootstrap_session(user, 'owner', 'machine-a')
    assert central.authenticate(bootstrap['session'], 'machine-a').username == 'owner'
    user['central_accounts_enabled'] = False
    with pytest.raises(CentralError):
        central.authenticate(bootstrap['session'], 'machine-a')


def test_unenrolled_login_keeps_legacy_signed_contract(central):
    from tests.test_auth_gateway_domain import FakeIssuer
    central.enrollment_required = True
    auth = AuthenticationService(central.users, FakeIssuer(), '1.0.124', central=central)
    old_payload = GatewayLoginRequest(username='owner', password='password', machine_id='machine-a',
                                      app_version='1.0.134', request_nonce='n' * 43)
    assert 'central_migration' not in auth.authenticate(old_payload, central_protocol=True)
    payload = GatewayLoginRequest(username='owner', password='password', machine_id='machine-a',
                                  app_version='1.0.135', request_nonce='n' * 43)
    result = auth.authenticate(payload, central_protocol=True)
    assert 'central' not in result
    assert result['central_migration']['mode'] == 'legacy_adoption'
    central.users.rows['owner']['central_accounts_enabled'] = True
    assert auth.authenticate(payload, central_protocol=True)['central']['sync_mode'] == 'manual'


def legacy_request(count=11, *, invalid_at=None, expected_mismatch_at=None):
    stores = []
    for index in range(count):
        store_id = f"{index + 1:024x}" if index < count - 1 else f"{index + 1:032x}"
        connections = []
        for provider in ("mercadolivre", "bling"):
            app_id = f"app-{provider}-{index}"
            account = "wrong-account" if expected_mismatch_at == (index, provider) else f"account-{index}"
            connections.append({
                "provider": provider, "app_id": app_id, "app_secret": f"secret-{provider}-{index}",
                "access_token": "invalid" if invalid_at == (index, provider) else f"access-{provider}-{index}",
                "refresh_token": f"refresh-{provider}-{index}", "expires_at": NOW + 3600,
                "expected_account_id": account,
                "expected_site_id": "MLB" if provider == "mercadolivre" else "",
            })
        stores.append({"store_id": store_id, "name": f"Store {index + 1}", "connections": connections})
    return LegacyAdoptionRequest(operation_id="c" * 32, stores=stores)


def test_legacy_adoption_migrates_11_stores_and_22_connections_atomically(central):
    central.enrollment_required = True
    user = central.users.get_user("owner")
    capability = central.migration_session(user, "owner", "machine-a")
    actor = central.authenticate_migration(capability["session"], "machine-a")
    result = central.adopt_legacy(actor, legacy_request())
    assert result == {"success": True, "operation_id": "c" * 32, "status": "completed",
                      "stores_total": 11, "connections_total": 22,
                      "completed_at": NOW, "failure": None}
    assert central.users.rows["owner"]["central_accounts_enabled"] is True
    bootstrap = central.bootstrap_session(central.users.get_user("owner"), "owner", "machine-b")
    assert len(bootstrap["stores"]) == 11
    assert sum(len(store["integracoes"]) for store in bootstrap["stores"]) == 22
    assert all(len(store["store_id"]) in (24, 32) for store in bootstrap["stores"])
    public = json.dumps({"result": result, "bootstrap": bootstrap})
    assert "access-" not in public and "refresh-" not in public and "secret-" not in public
    assert central.list_stores(principal(central, "reader")) == []


def test_legacy_adoption_is_idempotent_and_rejects_changed_content(central):
    central.enrollment_required = True
    capability = central.migration_session(central.users.get_user("owner"), "owner", "machine-a")
    actor = central.authenticate_migration(capability["session"], "machine-a")
    request_value = legacy_request(1)
    first = central.adopt_legacy(actor, request_value)
    assert central.adopt_legacy(actor, request_value) == first
    changed = request_value.model_copy(deep=True)
    changed.stores[0].name = "Changed"
    with pytest.raises(CentralError) as error:
        central.adopt_legacy(actor, changed)
    assert error.value.code == "central_migration_conflict"


@pytest.mark.parametrize("failure", ["token", "identity", "tenant"])
def test_legacy_adoption_failure_never_enables_user_or_publishes_store(central, failure):
    central.enrollment_required = True
    request_value = legacy_request(
        2,
        invalid_at=(1, "bling") if failure == "token" else None,
        expected_mismatch_at=(1, "bling") if failure == "identity" else None,
    )
    if failure == "tenant":
        connection = request_value.stores[1].connections[1]
        identity = central.provider.identify({"app_id": connection.app_id,
                                              "provider": connection.provider,
                                              "access_token": connection.access_token.get_secret_value()})
        connection_id = key_for(connection.provider, connection.app_id, identity["seller_id"])
        central.documents.change("connections", connection_id, lambda _: {"tenant": "tenant-b"})
    capability = central.migration_session(central.users.get_user("owner"), "owner", "machine-a")
    actor = central.authenticate_migration(capability["session"], "machine-a")
    with pytest.raises(CentralError) as error:
        central.adopt_legacy(actor, request_value)
    assert error.value.details["store_id"] == request_value.stores[1].store_id
    assert error.value.details["provider"] == "bling"
    assert central.users.rows["owner"].get("central_accounts_enabled") is not True
    assert central.list_stores(actor) == []


@pytest.mark.parametrize("change", ["expired", "machine", "revoked", "tenant"])
def test_migration_capability_is_short_lived_and_bound_to_user_tenant_machine(central, change):
    central.enrollment_required = True
    capability = central.migration_session(central.users.get_user("owner"), "owner", "machine-a")
    machine = "machine-a"
    if change == "expired":
        central.clock = lambda: NOW + 1801
    elif change == "machine":
        machine = "machine-b"
    elif change == "revoked":
        central.users.rows["owner"]["machine_ids"] = ["machine-b"]
    else:
        central.users.rows["owner"]["client_id"] = "tenant-b"
    with pytest.raises(CentralError):
        central.authenticate_migration(capability["session"], machine)


def test_hosting_routes_central_and_auth_to_same_pinned_service():
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parents[1] / 'firebase.json').read_text())
    routes = {row['source']: row for row in config['hosting']['rewrites']}
    assert routes['/api/central/**']['run'] == routes['/api/auth/**']['run']
    assert routes['/api/central/**']['run']['pinTag'] is True
