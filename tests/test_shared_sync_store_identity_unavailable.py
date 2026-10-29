"""Unavailable providers cannot erase account proof or block unrelated proof."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pandas as pd
import pytest
import requests
from fastapi import HTTPException

from test_shared_sync_store_reconciliation import existing_account, new_machine, store  # noqa: F401
from test_shared_sync_store_inactive_provider import (
    CADASTRO,
    _apply_sales,
    _entry,
    _publish,
    _read_sales,
    _receive,
    _sales_bytes,
    _snapshot,
)
from backend.services import central_accounts_migration as migration
from backend.services import shared_sync_store_bindings as bindings


_REAL_IDENTITY = migration._identity


def _verified(provider):
    return ("123", "MLB") if provider == "mercadolivre" else ("company-123", "")


def _fail_provider(monkeypatch, *, provider="bling", sides=("local", "remote"), status=401):
    calls = []

    def identity(current_provider, token):
        calls.append((current_provider, token))
        if current_provider == provider and token.partition("-")[0] in sides:
            raise HTTPException(status, "Synthetic unavailable account")
        return _verified(current_provider)

    monkeypatch.setattr(migration, "_identity", identity)
    return calls


def _reject_unchanged(env, scopes=None, *, code=None):
    before, bundles = _snapshot(env), deepcopy(env.bundles)
    with pytest.raises(HTTPException) as caught:
        if scopes is None:
            _receive(env)
        else:
            _receive(env, scopes)
    assert caught.value.status_code == 409
    if code is not None:
        assert caught.value.detail["code"] == code
    assert _snapshot(env) == before
    assert env.bundles == bundles
    return caught.value


def _assert_preserved_import(env, local, remote, provider):
    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert [row["store_id"] for row in rows] == [local["store_id"]]
    assert rows[0]["nome"] == local["nome"]
    for current_provider, original in local["integracoes"].items():
        assert {key: rows[0]["integracoes"][current_provider][key] for key in original} == original
    products = pd.read_csv(env.tenant / CADASTRO, dtype=str)
    assert products[["store_id", "sku"]].to_dict("records") == [
        {"store_id": local["store_id"], "sku": "001"},
    ]
    record = _entry(env)[1]["stores"][remote["store_id"]]
    assert record["unverified_providers"] == [provider]


@pytest.mark.parametrize("status", [401, 429, 503])
@pytest.mark.parametrize("side", ["local", "remote", "both"])
def test_available_account_imports_cadastro_without_replacing_connections(existing_account, monkeypatch, status, side):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])
    bundles = deepcopy(env.bundles)
    sides = ("local", "remote") if side == "both" else (side,)
    calls = _fail_provider(monkeypatch, sides=sides, status=status)

    assert _receive(env)["success"]

    _assert_preserved_import(env, local, remote, "bling")
    assert {token for provider, token in calls if provider == "mercadolivre"} == {"local-ml", "remote-ml"}
    assert env.bundles == bundles


def test_bling_can_prove_store_when_mercadolivre_is_unavailable(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])
    _fail_provider(monkeypatch, provider="mercadolivre", status=429)

    assert _receive(env)["success"]

    _assert_preserved_import(env, local, remote, "mercadolivre")


@pytest.mark.parametrize("error_type", [requests.Timeout, requests.ConnectionError])
def test_network_errors_normalized_by_identity_allow_other_account(existing_account, monkeypatch, error_type):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])

    def request(method, url, **kwargs):
        assert method == "GET"
        if "api.bling.com.br" in url:
            raise error_type("Synthetic transport failure")
        return SimpleNamespace(status_code=200, json=lambda: {"id": "123", "site_id": "MLB"})

    monkeypatch.setattr(migration, "_request", request)
    monkeypatch.setattr(migration, "_identity", _REAL_IDENTITY)

    assert _receive(env)["success"]

    _assert_preserved_import(env, local, remote, "bling")


@pytest.mark.parametrize("kind", ["invalid_identity", "unexpected"])
def test_other_account_does_not_hide_fatal_identity_errors(existing_account, monkeypatch, kind):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])

    def identity(provider, token):
        if provider == "bling":
            if kind == "invalid_identity":
                raise HTTPException(502, "Synthetic invalid identity")
            raise ValueError("Synthetic unexpected failure")
        return _verified(provider)

    monkeypatch.setattr(migration, "_identity", identity)
    _reject_unchanged(env, code="shared_sync_store_account_unverifiable")


@pytest.mark.parametrize("invalid_json", [False, True])
def test_invalid_provider_payload_remains_fatal(existing_account, monkeypatch, invalid_json):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])

    def payload():
        if invalid_json:
            raise ValueError("Synthetic malformed JSON")
        return {"data": {}}

    def request(method, url, **kwargs):
        if "api.bling.com.br" in url:
            return SimpleNamespace(status_code=200, json=payload)
        return SimpleNamespace(status_code=200, json=lambda: {"id": "123", "site_id": "MLB"})

    monkeypatch.setattr(migration, "_request", request)
    monkeypatch.setattr(migration, "_identity", _REAL_IDENTITY)
    _reject_unchanged(env, code="shared_sync_store_account_unverifiable")


@pytest.mark.parametrize("side", ["local", "remote", "both"])
def test_every_selected_store_still_requires_an_authenticated_account(existing_account, monkeypatch, side):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])
    sides = ("local", "remote") if side == "both" else (side,)

    def identity(provider, token):
        if token.partition("-")[0] in sides:
            raise HTTPException(503, "Synthetic unavailable accounts")
        return _verified(provider)

    monkeypatch.setattr(migration, "_identity", identity)
    _reject_unchanged(env, code="shared_sync_store_account_unverifiable")


def test_different_available_platforms_do_not_prove_a_common_account(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])

    def identity(provider, token):
        if (provider, token.partition("-")[0]) in {("bling", "local"), ("mercadolivre", "remote")}:
            raise HTTPException(401, "Synthetic unavailable account")
        return _verified(provider)

    monkeypatch.setattr(migration, "_identity", identity)
    _reject_unchanged(env, code="shared_sync_store_account_unverifiable")


@pytest.mark.parametrize("conflict", ["both_declared", "local_failed", "remote_failed"])
def test_failed_provider_cannot_hide_conflicting_account_claim(existing_account, monkeypatch, conflict):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    if conflict == "both_declared":
        local["integracoes"]["bling"]["empresa_id"] = "company-local"
        remote["integracoes"]["bling"]["empresa_id"] = "company-remote"
        sides = ("local", "remote")
    else:
        side = conflict.removesuffix("_failed")
        row = local if side == "local" else remote
        row["integracoes"]["bling"]["empresa_id"] = "company-other"
        sides = (side,)
    _publish(env, [local], [remote])
    _fail_provider(monkeypatch, sides=sides)

    _reject_unchanged(env)


@pytest.mark.parametrize("claim", [False, "invalid/company"])
def test_failed_provider_cannot_hide_malformed_account_claim(existing_account, monkeypatch, claim):
    env, local, remote, _ = existing_account
    remote = deepcopy(remote)
    remote["integracoes"]["bling"]["empresa_id"] = claim
    _publish(env, [local], [remote])
    _fail_provider(monkeypatch, sides=("remote",))

    _reject_unchanged(env, code="shared_sync_store_account_unverifiable")


def test_available_identity_mismatch_remains_fatal_during_partial_outage(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])

    def identity(provider, token):
        if provider == "bling":
            raise HTTPException(401, "Synthetic expired connection")
        return "different-account", "MLB"

    monkeypatch.setattr(migration, "_identity", identity)
    _reject_unchanged(env, code="shared_sync_store_account_mismatch")


@pytest.mark.parametrize("legacy", [False, True])
def test_unavailable_bling_blocks_sales_before_any_writes(existing_account, monkeypatch, legacy):
    env, local, remote, _ = existing_account
    proof = _publish(env, [local], [remote])
    _fail_provider(monkeypatch)
    assert _receive(env)["success"]
    _, entry = _entry(env)
    current = _sales_bytes(local["store_id"], local["nome"], legacy=legacy, sale_id="local-sale")
    (env.tenant / "vendas_historico.db").write_bytes(current)
    before = {path.relative_to(env.tenant): path.read_bytes() for path in env.tenant.rglob("*") if path.is_file()}

    with pytest.raises(HTTPException) as caught:
        _apply_sales(
            env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"], legacy=legacy),
            [("produtos_compilado.csv", b"store_id,sku\nstore-a,001\n")],
        )

    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert {path.relative_to(env.tenant): path.read_bytes() for path in env.tenant.rglob("*") if path.is_file()} == before


def test_matching_recovery_on_both_sides_restores_sales(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    proof = _publish(env, [local], [remote])
    _fail_provider(monkeypatch)
    assert _receive(env)["success"]
    assert _entry(env)[1]["stores"][remote["store_id"]]["unverified_providers"] == ["bling"]
    _fail_provider(monkeypatch, sides=())

    assert _receive(env, ["lojas_integracoes"])["success"]

    _, entry = _entry(env)
    assert "unverified_providers" not in entry["stores"][remote["store_id"]]
    result = _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert result["file_count"] == 1
    persisted = (env.tenant / "vendas_historico.db").read_bytes()
    assert _read_sales(persisted, "SELECT store_id FROM vendas") == [(local["store_id"],)]


def test_removing_failed_connection_does_not_clear_sales_restriction(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])
    _fail_provider(monkeypatch)
    assert _receive(env)["success"]
    remote = deepcopy(remote)
    remote["integracoes"].pop("bling")
    proof = _publish(env, [local], [remote])
    _fail_provider(monkeypatch, sides=())

    assert _receive(env, ["lojas_integracoes"])["success"]

    _, entry = _entry(env)
    assert entry["stores"][remote["store_id"]]["unverified_providers"] == ["bling"]
    with pytest.raises(HTTPException) as caught:
        _apply_sales(env, entry, proof, _sales_bytes(remote["store_id"], remote["nome"]))
    assert caught.value.detail["code"] == "shared_sync_store_sales_account_unverified"
    assert not (env.tenant / "vendas_historico.db").exists()


def test_cadastro_revalidation_cannot_weaken_an_existing_complete_proof(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    _publish(env, [local], [remote])
    assert _receive(env, ["lojas_integracoes"])["success"]
    path = env.tenant / "lojas_config.json"
    rows = json.loads(path.read_bytes())
    rows[0]["integracoes"]["bling"]["access_token"] = "local-rotated-bling"
    path.write_text(json.dumps(rows), encoding="utf-8")
    _fail_provider(monkeypatch, sides=("local",))

    _reject_unchanged(env, ["cadastro"])


def test_empty_destination_receives_distinct_remote_stores_with_other_account_proof(existing_account, monkeypatch):
    env, _, remote, _ = existing_account
    second = store("store-b", "second", seller="456")
    second["nome"] = "Second synthetic store"
    _publish(env, [], [remote, second])
    bundles = deepcopy(env.bundles)

    def identity(provider, token):
        if provider == "bling":
            raise HTTPException(401, "Synthetic unavailable connection")
        return ("456" if token.startswith("second-") else "123"), "MLB"

    monkeypatch.setattr(migration, "_identity", identity)

    assert _receive(env)["success"]

    rows = json.loads((env.tenant / "lojas_config.json").read_bytes())
    assert {row["store_id"] for row in rows} == {remote["store_id"], second["store_id"]}
    for original in (remote, second):
        actual = next(row for row in rows if row["store_id"] == original["store_id"])
        for provider, config in original["integracoes"].items():
            assert {key: actual["integracoes"][provider][key] for key in config} == config
    assert pd.read_csv(env.tenant / CADASTRO, dtype=str)["store_id"].tolist() == [remote["store_id"]]
    assert not (env.tenant / bindings.BINDINGS_FILE).exists()
    assert env.bundles == bundles


def test_distinct_remote_sellers_cannot_hide_duplicate_unavailable_company(existing_account, monkeypatch):
    env, _, remote, _ = existing_account
    remote = deepcopy(remote)
    second = store("store-b", "second", seller="456")
    second["nome"] = "Second synthetic store"
    for row in (remote, second):
        row["integracoes"]["bling"]["empresa_id"] = "shared-company"
    _publish(env, [], [remote, second])

    def identity(provider, token):
        if provider == "bling":
            raise HTTPException(401, "Synthetic unavailable connection")
        return ("456" if token.startswith("second-") else "123"), "MLB"

    monkeypatch.setattr(migration, "_identity", identity)

    _reject_unchanged(env)


def test_different_local_and_remote_sellers_cannot_hide_same_unavailable_company(existing_account, monkeypatch):
    env, local, remote, _ = existing_account
    local, remote = deepcopy(local), deepcopy(remote)
    remote["integracoes"]["mercadolivre"]["user_id"] = "456"
    for row in (local, remote):
        row["integracoes"]["bling"]["empresa_id"] = "shared-company"
    _publish(env, [local], [remote])

    def identity(provider, token):
        if provider == "bling":
            raise HTTPException(401, "Synthetic unavailable connection")
        return ("456" if token.startswith("remote-") else "123"), "MLB"

    monkeypatch.setattr(migration, "_identity", identity)

    _reject_unchanged(env)
