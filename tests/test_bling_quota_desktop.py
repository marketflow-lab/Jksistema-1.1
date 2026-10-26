from __future__ import annotations

import threading
import time
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.services import bling, bling_accounts, central_accounts_client as central


def headers(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture(autouse=True)
def isolated_quota(monkeypatch):
    monkeypatch.setattr(bling, "BLING_ACCOUNT_MIN_INTERVAL_SECONDS", 0.025)
    with bling._BLING_QUOTA_CONDITION:
        bling._BLING_ACCOUNT_NEXT_CALL.clear()
        bling._BLING_QUOTA_ACTIVE = 0
        bling._BLING_QUOTA_ACTIVE_KEYS.clear()
        bling._BLING_QUOTA_UNKNOWN_ACTIVE = False
        bling._BLING_QUOTA_UNKNOWN_WAITERS = 0
        bling._BLING_QUOTA_LAST_START = 0.0
        bling._BLING_QUOTA_UNKNOWN_UNTIL = 0.0
    with bling_accounts._LOCK:
        bling_accounts._TOKENS.clear()
    old_references = dict(central._references)
    context = central._current.set(None)
    yield
    central._current.reset(context)
    central._references.clear()
    central._references.update(old_references)


def test_four_verified_companies_can_start_concurrently():
    barrier = threading.Barrier(4)
    def run(index):
        token = "token-" + str(index)
        bling_accounts.register_bling_account(token, str(index + 1))
        barrier.wait(timeout=2)
        with bling.bling_account_request(headers(token), deadline=time.monotonic() + 2):
            barrier.wait(timeout=2)
            return time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        times = list(pool.map(run, range(4)))
    assert max(times) - min(times) < 0.1


def test_rotating_tokens_and_different_store_aliases_share_company_quota():
    tokens = ["old", "new", "other-store"]
    for token in tokens:
        bling_accounts.register_bling_account(token, "123")
    starts = []
    for token in tokens:
        with bling.bling_account_request(headers(token)):
            starts.append(time.monotonic())
    assert all(b - a >= 0.023 for a, b in zip(starts, starts[1:]))
    assert set(bling._BLING_ACCOUNT_NEXT_CALL) == {"bling:123"}


def test_hexadecimal_company_aliases_share_case_normalized_quota():
    identity = "436c56a5679921f5f13a3d6433561773"
    bling_accounts.register_bling_account("lowercase", identity)
    bling_accounts.register_bling_account("uppercase", identity.upper())
    assert bling._bling_quota_key(headers("lowercase")) == bling._bling_quota_key(headers("uppercase")) == "bling:" + identity


def test_unknown_discovery_excludes_inflight_and_new_known_requests():
    bling_accounts.register_bling_account("known", "123")
    known_started, finish_known = threading.Event(), threading.Event()
    unknown_started, finish_unknown = threading.Event(), threading.Event()
    second_known_started = threading.Event()
    def first_known():
        with bling.bling_account_request(headers("known")):
            known_started.set()
            assert finish_known.wait(2)
    def unknown():
        with bling.bling_account_request(headers("unverified")):
            unknown_started.set()
            assert finish_unknown.wait(2)
    def second_known():
        with bling.bling_account_request(headers("known")):
            second_known_started.set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(first_known)
        assert known_started.wait(1)
        discovery = pool.submit(unknown)
        limit = time.monotonic() + 1
        while not bling._BLING_QUOTA_UNKNOWN_WAITERS and time.monotonic() < limit:
            time.sleep(0.001)
        second = pool.submit(second_known)
        assert not unknown_started.wait(0.02)
        finish_known.set()
        assert unknown_started.wait(1)
        assert not second_known_started.is_set()
        finish_unknown.set()
        first.result(2), discovery.result(2), second.result(2)
    assert second_known_started.is_set()


def test_deadline_does_not_leave_an_unknown_barrier_or_start_a_call():
    with bling.bling_account_request(headers("unknown")):
        pass
    with pytest.raises(requests.Timeout):
        with bling.bling_account_request(headers("unknown"), deadline=time.monotonic() + 0.003):
            pytest.fail("expired quota was admitted")
    assert bling._BLING_QUOTA_UNKNOWN_WAITERS == 0
    assert bling._BLING_QUOTA_UNKNOWN_ACTIVE is False


def test_retry_after_blocks_same_company_without_blocking_another():
    bling_accounts.register_bling_account("a", "1")
    bling_accounts.register_bling_account("b", "2")
    bling.remember_bling_throttle(headers("a"), SimpleNamespace(status_code=429, headers={"Retry-After": "2"}))
    with bling.bling_account_request(headers("b"), deadline=time.monotonic() + 0.05):
        pass
    with pytest.raises(requests.Timeout):
        with bling.bling_account_request(headers("a"), deadline=time.monotonic() + 0.003):
            pytest.fail("company cooldown was bypassed")


def test_central_company_quota_requires_server_capability_and_authorized_marker():
    identity = "436c56a5679921f5f13a3d6433561773"
    client = SimpleNamespace(expires_at=time.time() + 60, bling_account_quota=False,
        public_stores=lambda: [{"store_id": "store-a", "integracoes": {"bling": {"user_id": identity}}}])
    marker = central.PREFIX + "fixture"
    central._references[marker] = (client, "store-a", "bling")
    assert central.bling_account_for_marker(marker) == identity
    assert bling._bling_quota_key(headers(marker)) == "bling-process-wide"
    client.bling_account_quota = True
    assert bling._bling_quota_key(headers(marker)) == "bling:" + identity
    central._current.set(object())
    with pytest.raises(HTTPException) as error:
        central.bling_account_for_marker(marker)
    assert error.value.status_code == 403


def test_central_generated_429_preserves_read_retry_after():
    def call(*args, **kwargs):
        raise HTTPException(429, "quota", headers={"Retry-After": "3"})
    client = SimpleNamespace(call=call)
    marker = central.PREFIX + "fixture"
    central._references[marker] = (client, "store-a", "bling")
    response = central.provider_request(marker, "GET", "https://api.bling.com.br/Api/v3/pedidos/vendas")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"
    with pytest.raises(HTTPException):
        central.provider_request(marker, "POST", "https://api.bling.com.br/Api/v3/pedidos/vendas", json={})


def test_desktop_learns_central_capability_without_changing_bootstrap_schema():
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"ok": True}).encode()
    response.headers["X-JK-Bling-Quota"] = "company-v1"
    transport = SimpleNamespace(request=lambda *args, **kwargs: response)
    client = central.CentralClient(
        {"session": "fixture", "expires_at": time.time() + 60, "stores": []},
        {"client_id": "tenant", "username": "fixture", "machine_id": "fixture"}, {},
        configuration=SimpleNamespace(url="https://central.invalid/login"), transport=transport)
    assert client.bling_account_quota is False
    assert client.call("GET", "/bootstrap") == {"ok": True}
    assert client.bling_account_quota is True
    response.headers.clear()
    client.call("GET", "/bootstrap")
    assert client.bling_account_quota is False
    response.headers["X-JK-Bling-Quota"] = "company-v1"
    client.call("GET", "/bootstrap")
    assert client.bling_account_quota is True
    def unavailable(*args, **kwargs):
        raise requests.Timeout("fixture")
    transport.request = unavailable
    with pytest.raises(HTTPException):
        client.call("GET", "/bootstrap")
    assert client.bling_account_quota is False


def test_retry_after_http_date_is_shared_with_same_company():
    from email.utils import formatdate
    bling_accounts.register_bling_account("known", "123")
    response = SimpleNamespace(status_code=429, headers={"Retry-After": formatdate(time.time() + 10, usegmt=True)})
    bling.remember_bling_throttle(headers("known"), response)
    assert bling._BLING_ACCOUNT_NEXT_CALL["bling:123"] > time.monotonic() + 8


def test_get_returns_original_429_when_shared_cooldown_exceeds_deadline(monkeypatch):
    bling_accounts.register_bling_account("known", "123")
    calls = []
    response = SimpleNamespace(status_code=429, headers={"Retry-After": "2"})
    monkeypatch.setattr(bling, "BLING_SESSION", SimpleNamespace(get=lambda *a, **kw: calls.append(kw) or response))
    monkeypatch.setattr(bling, "_bling_cancelable_sleep", lambda *a, **kw: None)
    result = bling._bling_get_with_adaptive_limit("https://api.bling.com.br/Api/v3/pedidos/vendas",
        headers=headers("known"), total_timeout=0.1)
    assert result is response
    assert len(calls) == 1
    assert result.headers["Retry-After"] == "2"


def test_suspended_grant_cannot_burst_same_company_and_other_company_remains_free(monkeypatch):
    monkeypatch.setattr(bling, "BLING_ACCOUNT_MIN_INTERVAL_SECONDS", 0.35)
    bling_accounts.register_bling_account("old", "123")
    bling_accounts.register_bling_account("new", "123")
    bling_accounts.register_bling_account("independent", "456")
    admitted, resume = threading.Event(), threading.Event()
    original_gate = bling.bling_account_request
    starts = {}
    @contextmanager
    def suspended_gate(request_headers, **kwargs):
        with original_gate(request_headers, **kwargs) as mark_start:
            if request_headers == headers("old"):
                admitted.set()
                assert resume.wait(2)
            yield mark_start
    def get(*args, **kwargs):
        token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        starts[token] = time.monotonic()
        time.sleep(0.125)
        return SimpleNamespace(status_code=200, headers={})
    monkeypatch.setattr(bling, "bling_account_request", suspended_gate)
    monkeypatch.setattr(bling, "BLING_SESSION", SimpleNamespace(get=get))
    def run(token):
        return bling._bling_get_with_adaptive_limit("https://api.bling.com.br/Api/v3/pedidos/vendas",
            headers=headers(token), total_timeout=3)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(run, "old")
        assert admitted.wait(1)
        second = pool.submit(run, "new")
        independent = pool.submit(run, "independent")
        assert independent.result(1).status_code == 200
        assert not second.done()
        time.sleep(0.25)  # total admission delay >350ms, including the independent RTT
        assert "new" not in starts
        resume.set()
        assert first.result(2).status_code == second.result(2).status_code == 200
    assert starts["new"] - starts["old"] >= 0.348
